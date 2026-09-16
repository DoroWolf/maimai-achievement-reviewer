#!/usr/bin/env python
"""命令行入口：拉取谱面与成绩，逐条校验并输出报告。

用法示例::

    uv run python check_scores.py --source oauth --client-id <ID> --client-secret <SECRET>
    uv run python check_scores.py --source test                 # 水鱼公开测试数据（离线回归）
    uv run python check_scores.py --source b50 --username <水鱼用户名>
    uv run python check_scores.py --source file --records-file records.json

判定说明见 ``maimai_check/scoreline.py`` 的模块文档；可疑条目退出码为 2。
"""

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
from maimai_check.report import render_results, render_summary, write_json, write_problem_list
from maimai_check.scoreline import BREAK_TABLES, WINDOWS, Notes

DEFAULT_CONFIG = "config.local.json"


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
        prog="check_scores.py",
        description="基于水鱼 API 的 maimai 成绩合法性校验器（判定成绩是否「打得出来」）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=("oauth", "b50", "test", "file"),
        default="oauth",
        help="成绩来源：oauth=全量(需授权) / b50=公开 B50 / test=水鱼测试数据 / file=本地文件",
    )
    parser.add_argument("--client-id", help="水鱼 OAuth client_id（或用 DF_CLIENT_ID 环境变量）")
    parser.add_argument("--client-secret", help="水鱼 OAuth client_secret（机密客户端才需要）")
    parser.add_argument("--username", help="B50 查询用的水鱼用户名")
    parser.add_argument("--qq", help="B50 查询用的 QQ 号")
    parser.add_argument("--records-file", help="本地成绩 JSON（--source file）")
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
    parser.add_argument("--no-field-check", action="store_true", help="关闭 ra/rate/ds 字段自洽校验")
    parser.add_argument("--check-dx", action="store_true", help="额外校验 dxScore 上限（水鱼数据可能不可靠）")
    parser.add_argument("--check-combo", action="store_true", help="额外校验 100.5%% 以上必须 AP")
    parser.add_argument("--include-utage", action="store_true", help="同时校验宴谱（默认跳过）")
    parser.add_argument("--limit", type=int, default=0, help="只校验前 N 条（调试用）")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存，重新请求")
    parser.add_argument("--out", help="把完整结果写入 JSON 文件")
    parser.add_argument(
        "--list-file",
        help="把「可疑 + 边缘」成绩清单写入文本文件（含曲名/ID/类型/难度/等级/定数/成绩/全连/连锁/物量/说明）",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出汇总，不打印明细表")
    return parser


def make_client(args: argparse.Namespace, log=print) -> df.DivingFishClient:
    """构造数据客户端；不需要授权的来源允许缺少 client_id。"""
    try:
        credentials = df.load_credentials(args.client_id, args.client_secret, args.config)
    except df.DataSourceError as exc:
        if args.source in ("test", "b50", "file"):
            log(f"[warn] {exc}；当前来源无需授权，继续执行")
            credentials = df.Credentials(client_id="")
        else:
            raise
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
        log(f"已从 {args.music_data_file} 读取 {len(index)} 个谱面")
        return index

    client = make_client(args, log)
    index = client.chart_index(refresh=args.refresh)
    log(f"已取得 {len(index)} 个谱面的物量数据")
    return index


def load_records(args: argparse.Namespace, log=print) -> tuple[list[dict], str]:
    """按来源取得原始成绩列表，返回 ``(records, 描述)``。"""
    if args.source == "file":
        if not args.records_file:
            raise SystemExit("--source file 需要 --records-file")
        return df.records_from_file(args.records_file), f"本地文件 {args.records_file}"
    if args.source == "test":
        return df.DivingFishClient(df.Credentials(client_id=""), cache_dir=args.cache_dir, log=log).test_data(
            refresh=args.refresh, include_utage=args.include_utage
        ), "水鱼 /player/test_data"
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
                check_fields=not args.no_field_check,
                check_dx=args.check_dx,
                check_combo=args.check_combo,
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
    if args.out:
        path = write_json(args.out, results, meta)
        print(f"\n完整结果已写入 {path}")
    if args.list_file:
        path = write_problem_list(args.list_file, results, meta=meta)
        problems = counts[Status.IMPOSSIBLE] + counts[Status.MARGINAL] + counts[Status.FIELD_ERROR]
        print(f"可疑/边缘清单（{problems} 条）已写入 {path}")

    if counts[Status.IMPOSSIBLE] or counts[Status.FIELD_ERROR]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

