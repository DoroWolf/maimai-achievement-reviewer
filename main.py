#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from maimai_check import sources as df
from maimai_check.checks import (
    DEFAULT_SCORE_TOLERANCE,
    ChartInfo,
    CheckResult,
    Record,
    Status,
    check_record,
    summarize,
)
from maimai_check.report import render_results, render_summary, write_csv, write_json, write_problem_list
from maimai_check.scoreline import BREAK_TABLES, WINDOWS, Notes

DEFAULT_CONFIG = df.DEFAULT_CONFIG_PATH

DEFAULT_OUT_DIR = "out"
DEFAULT_RAW_NAME = "report.json"
DEFAULT_CSV_NAME = "report.csv"
DEFAULT_OUTPUT_NAME = "report.txt"


def use_robust_std_streams() -> None:
    """让 stdout/stderr 在无法编码某个字符时退化为替换字符而不是抛异常。

    Windows 控制台的默认编码是 GBK（cp936），而水鱼曲名里会出现半角片假名（如 ``ﾟ``）、
    特殊符号等 GBK 无法表示的字形，一旦打印就会 ``UnicodeEncodeError`` 中断整个报告。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - 被替换成非标准流时跳过
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 流已关闭时忽略
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="基于水鱼 API 的 maimai 成绩合法性校验器（判定成绩是否「打得出来」）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=("oauth", "b50", "local"),
        default="oauth",
        help="成绩来源：oauth=全量(需授权) / b50=公开 B50 / local=本地成绩文件",
    )
    parser.add_argument("--username", help="B50 查询用的水鱼用户名")
    parser.add_argument("--qq", help="B50 查询用的 QQ 号")
    parser.add_argument("--records-file", help="本地成绩 JSON（--source local）")
    parser.add_argument("--music-data-file", help="本地谱面 JSON（离线时使用）")
    parser.add_argument("--cache-dir", default="cache", help="缓存目录（默认 cache）")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"凭据文件（默认 {DEFAULT_CONFIG}）")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="只走水鱼 OAuth 授权（设备码），把凭据写入配置文件后退出，不拉取成绩",
    )
    parser.add_argument(
        "--break-table",
        choices=tuple(BREAK_TABLES),
        default="T1",
        help="BREAK 判定系数表（默认 T1，为真实数据回归出的最佳拟合）",
    )
    parser.add_argument(
        "--window",
        choices=tuple(WINDOWS),
        default="floor",
        help="取整判定窗口（默认 floor=显示值等于向下取整，回归最佳）",
    )
    parser.add_argument("--tolerance", type=int, default=1, help="物量容差（默认 1，超出的记为「边缘」）")
    parser.add_argument(
        "--score-tolerance",
        type=int,
        default=DEFAULT_SCORE_TOLERANCE,
        help=f"分数容差（S 单位，默认 {DEFAULT_SCORE_TOLERANCE} = 0.0001%%，差值更大即记为「可疑」）",
    )
    parser.add_argument("--strict", action="store_true", help="等价于 --tolerance 0 --score-tolerance 0")
    parser.add_argument("--include-utage", action="store_true", help="同时校验宴谱（默认跳过）")
    parser.add_argument("--limit", type=int, default=0, help="只校验前 N 条（调试用）")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存，重新请求")
    parser.add_argument(
        "--raw-dir",
        default=DEFAULT_OUT_DIR,
        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）：--raw/--csv/--output 只写文件名时落在这里",
    )
    parser.add_argument(
        "--raw",
        nargs="?",
        const=DEFAULT_RAW_NAME,
        help=f"把完整结果写入 JSON 文件（缺省文件名 {DEFAULT_RAW_NAME}）",
    )
    parser.add_argument(
        "--csv",
        nargs="?",
        const=DEFAULT_CSV_NAME,
        help=f"把全部结果写入 CSV 表格（缺省文件名 {DEFAULT_CSV_NAME}，UTF-8 BOM + CRLF，可直接用 Excel 打开）",
    )
    parser.add_argument(
        "--output",
        nargs="?",
        const=DEFAULT_OUTPUT_NAME,
        help=(
            "把「可疑 + 边缘」成绩清单写入文本文件"
            f"（缺省文件名 {DEFAULT_OUTPUT_NAME}，含曲名/ID/类型/难度/等级/定数/成绩/全连/连锁/物量/说明）"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="只输出汇总，不打印明细表")
    return parser


def resolve_output(path: str, out_dir: str) -> Path:
    """解析输出路径。

    只给文件名（如 ``suspicous.txt``）时放进 ``--raw-dir``（默认 ``out``），
    带目录的写法（``out/report.csv``、``D:\\tmp\\x.csv``）与绝对路径原样使用，
    这样「只写文件名」的结果不会再散落到项目根目录。
    """
    target = Path(path)
    if str(target.parent) in ("", "."):
        return Path(out_dir) / target.name
    return target


def describe_output(path: Path) -> str:
    """输出用于提示的绝对路径（避免相对路径看不出去哪了）。"""
    try:
        return str(path.resolve())
    except OSError:  # pragma: no cover - 路径无法解析时退回原值
        return str(path)


def make_client(args: argparse.Namespace, log=print) -> df.DivingFishClient:
    """构造数据客户端。

    凭据只来自 ``--config`` 指向的配置文件：``client_id`` 缺省用 ``sources.OFFICIAL_CLIENT_ID``
    （源码里写死的官方值），机密客户端的 ``client_secret`` 也只能写在配置文件里。
    """
    credentials = df.load_credentials(args.config)
    return df.DivingFishClient(credentials, cache_dir=args.cache_dir, config_path=args.config, log=log)


def load_chart_index(args: argparse.Namespace, log=print) -> dict[tuple[str, str, int], ChartInfo]:
    """取得 ``(song_id, type, level_index) -> ChartInfo`` 索引。"""
    if args.music_data_file:
        data = json.loads(Path(args.music_data_file).read_text(encoding="utf-8"))
        index: dict[tuple[str, str, int], ChartInfo] = {}
        for entry in data:
            song_id = str(entry.get("id"))
            ds_list = entry.get("ds") or []
            level_list = entry.get("level") or []
            for level_index, chart in enumerate(entry.get("charts") or []):
                if not chart.get("notes"):
                    continue
                try:
                    notes = Notes.from_api(chart["notes"])
                except ValueError:
                    continue
                info = ChartInfo(
                    song_id=song_id,
                    title=str(entry.get("title", song_id)),
                    type=str(entry.get("type", "SD")),
                    level_index=level_index,
                    level=str(level_list[level_index]) if level_index < len(level_list) else "",
                    ds=float(ds_list[level_index]) if level_index < len(ds_list) else 0.0,
                    notes=notes,
                )
                index[info.key] = info
        log(f"已从 {args.music_data_file} 读取 {len(index)} 张谱面")
        return index

    client = make_client(args, log)
    index = client.chart_index(refresh=args.refresh)
    log(f"已取得 {len(index)} 张谱面的物量数据")
    return index


def load_records(args: argparse.Namespace, log=print) -> tuple[list[dict], str]:
    """按来源取得原始成绩列表，返回 ``(records, 描述)``。"""
    if args.source == "local":
        if not args.records_file:
            raise SystemExit("--source local 需要 --records-file")
        return df.records_from_file(args.records_file), f"本地文件 {args.records_file}"
    if args.source == "b50":
        if not (args.username or args.qq):
            raise SystemExit("--source b50 需要 --username 或 --qq")
        client = make_client(args, log)
        return client.b50(
            username=args.username, qq=args.qq, refresh=args.refresh, include_utage=args.include_utage
        ), "水鱼 /query/player (B50)"

    client = make_client(args, log)
    return client.records(refresh=args.refresh, include_utage=args.include_utage), "水鱼 /player/records (OAuth)"


def main(argv: list[str] | None = None) -> int:
    use_robust_std_streams()
    args = build_parser().parse_args(argv)
    if args.strict:
        args.tolerance = 0
        args.score_tolerance = 0
    log = (lambda message: None) if args.quiet else print
    started = time.perf_counter()

    if args.login_only:
        try:
            client = make_client(args, log)
            client.access_token()
        except df.DataSourceError as exc:
            print(f"授权失败：{exc}", file=sys.stderr)
            return 1
        print(f"授权完成，凭据已写入 {args.config}")
        return 0

    try:
        charts = load_chart_index(args, log)
        raw_records, source_name = load_records(args, log)
    except (df.DataSourceError, ValueError) as exc:
        print(f"取数失败：{exc}", file=sys.stderr)
        return 1

    if args.limit:
        raw_records = raw_records[: args.limit]

    results: list[CheckResult] = []
    skipped_parse = 0
    for raw in raw_records:
        try:
            record = Record.from_api(raw)
        except (KeyError, TypeError, ValueError) as exc:
            skipped_parse += 1
            log(f"[warn] 跳过无法解析的成绩：{exc}")
            continue
        chart = charts.get(record.key)
        results.append(
            check_record(
                record,
                chart,
                table=args.break_table,
                window=args.window,
                tolerance=max(0, args.tolerance),
                score_tolerance=max(0, args.score_tolerance),
            )
        )

    elapsed = time.perf_counter() - started
    counts = summarize(results)
    print(render_summary(results, elapsed=elapsed))
    print(
        f"数据来源：{source_name} | 判定：{args.break_table} 表 / {args.window} 窗口 / "
        f"物量容差 {args.tolerance} / 分数容差 {args.score_tolerance / 10000:.4f}%"
    )
    if skipped_parse:
        print(f"（另有 {skipped_parse} 条成绩无法解析，已跳过）")
    if not args.quiet:
        print()
        print(render_results(results))

    meta = {
        "source": args.source,
        "source_detail": source_name,
        "break_table": args.break_table,
        "window": args.window,
        "tolerance": args.tolerance,
        "score_tolerance": args.score_tolerance,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if args.raw or args.csv or args.output:
        print("\n")
        if args.raw:
            path = write_json(resolve_output(args.raw, args.raw_dir), results, meta)
            print(f"原始 JSON 已写入 {describe_output(path)}")
        if args.csv:
            path = write_csv(resolve_output(args.csv, args.raw_dir), results)
            print(f"CSV 表格（{len(results)} 行）已写入 {describe_output(path)}")
        if args.output:
            path = write_problem_list(resolve_output(args.output, args.raw_dir), results, meta=meta)
            problems = counts[Status.IMPOSSIBLE] + counts[Status.MARGINAL]
            print(f"异常清单（{problems} 条）已写入 {describe_output(path)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

