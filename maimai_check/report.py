"""结果输出：终端表格（按中日韩字符宽度对齐）、JSON 报告与可疑成绩文本清单。"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .checks import CheckResult, Status, summarize
from .scoreline import Notes

__all__ = [
    "CHAIN_LABELS",
    "COMBO_LABELS",
    "DIFFICULTY_NAMES",
    "PROBLEM_LIST_TITLE",
    "as_dict",
    "combo_label",
    "display_width",
    "render_problem_list",
    "render_results",
    "render_summary",
    "render_table",
    "write_json",
    "write_problem_list",
]

DIFFICULTY_NAMES = ("BSC", "ADV", "EXP", "MAS", "ReM")

#: 全连标记（水鱼 ``fc`` 字段）的可读名。
COMBO_LABELS: dict[str, str] = {
    "fc": "FC",
    "fcp": "FC+",
    "ap": "AP",
    "app": "AP+",
}

#: 连锁/同步标记（水鱼 ``fs`` 字段）的可读名。
CHAIN_LABELS: dict[str, str] = {
    "fs": "FS",
    "fsp": "FS+",
    "fsd": "FSD",
    "fsdp": "FSD+",
    "sync": "SYNC",
}

#: 文本清单的标题与表头。
PROBLEM_LIST_TITLE = "maimai 成绩合法性校验 —— 可疑/边缘成绩清单"
_PROBLEM_HEADERS = ("曲名", "ID", "类型", "难度", "等级", "定数", "成绩", "全连", "连锁", "物量", "说明")
_PROBLEM_ALIGNS = ("left", "left", "left", "left", "left", "right", "right", "left", "left", "left", "left")

#: 清单中出现的状态分组（按此顺序输出）。
_PROBLEM_GROUPS = (Status.IMPOSSIBLE, Status.MARGINAL, Status.FIELD_ERROR)


def display_width(text: str) -> int:
    """终端显示宽度（全角字符算 2）。"""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    padding = " " * max(0, width - display_width(text))
    if align == "right":
        return padding + text
    return text + padding


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str] | None = None) -> str:
    """渲染等宽表格（``aligns`` 为每列的对齐方式）。"""
    columns = len(headers)
    widths = [display_width(h) for h in headers]
    for row in rows:
        for index in range(columns):
            widths[index] = max(widths[index], display_width(str(row[index])))
    aligns = list(aligns or ["left"] * columns)
    lines = ["  ".join(_pad(h, widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * widths[i] for i in range(columns)))
    for row in rows:
        lines.append("  ".join(_pad(str(row[i]), widths[i], aligns[i]) for i in range(columns)))
    return "\n".join(lines)


def format_notes(notes: Notes | None) -> str:
    if notes is None:
        return "-"
    return f"{notes.tap}+{notes.hold}+{notes.slide}+{notes.touch}+{notes.brk}"


def difficulty_name(level_index: int) -> str:
    if 0 <= level_index < len(DIFFICULTY_NAMES):
        return DIFFICULTY_NAMES[level_index]
    return f"L{level_index}"


def _detail(result: CheckResult) -> str:
    if result.status is Status.MARGINAL and result.note_delta is not None:
        dt, dh, ds, db = result.note_delta
        parts = []
        for label, value in (("tap", dt), ("hold", dh), ("slide", ds), ("brk", db)):
            if value:
                parts.append(f"{label}{value:+d}")
        return "物量 " + "/".join(parts) + " 后即可解"
    if result.issues:
        return "；".join(result.issues)
    if result.status is Status.OK:
        return "可解"
    if result.status is Status.SKIPPED:
        return result.describe_issues() or "已跳过"
    return ""


def as_dict(result: CheckResult) -> dict:
    """把校验结果转成可序列化的字典。"""
    notes = result.notes
    return {
        "song_id": result.record.song_id,
        "title": result.title,
        "type": result.record.type,
        "level_index": result.record.level_index,
        "difficulty": difficulty_name(result.record.level_index),
        "level": None if result.chart is None else result.chart.level,
        "ds": result.ds,
        "achievements": result.record.achievements,
        "score": result.score,
        "ra": result.record.ra,
        "rate": result.record.rate,
        "fc": result.record.fc,
        "fs": result.record.fs,
        "notes": None if notes is None else notes.to_api(),
        "note_count": None if notes is None else notes.note_count,
        "status": result.status.value,
        "status_label": result.status.label,
        "issues": list(result.issues),
        "note_delta": None if result.note_delta is None else list(result.note_delta),
        "nearest_delta": result.nearest_delta,
    }


def render_results(results: Sequence[CheckResult]) -> str:
    """渲染明细表（跳过状态为「通过」的条目，只看问题与边缘）。"""
    rows = []
    for result in results:
        if result.status is Status.OK:
            continue
        rows.append(
            [
                result.status.label,
                result.title,
                result.record.type,
                difficulty_name(result.record.level_index),
                format_notes(result.notes),
                f"{result.record.achievements:.4f}%",
                f"{result.ds:g}",
                _detail(result),
            ]
        )
    if not rows:
        return "未发现可疑成绩。"
    headers = ["状态", "曲名", "类型", "难度", "物量(t+h+s+to+b)", "成绩", "定数", "说明"]
    return render_table(headers, rows, ["left", "left", "left", "left", "left", "right", "right", "left"])


def render_summary(results: Sequence[CheckResult], *, elapsed: float | None = None) -> str:
    """渲染统计信息。"""
    counts = summarize(results)
    total = sum(counts.values())
    parts = [f"共校验 {total} 条成绩"]
    for status in (Status.IMPOSSIBLE, Status.MARGINAL, Status.FIELD_ERROR, Status.SKIPPED, Status.OK):
        parts.append(f"{status.label} {counts[status]}")
    line = " | ".join(parts)
    if elapsed is not None:
        line += f" | 耗时 {elapsed:.1f}s"
    return line


def write_json(path: str | Path, results: Iterable[CheckResult], meta: dict | None = None) -> Path:
    """写出 JSON 报告。"""
    results = list(results)
    payload = {
        "meta": meta or {},
        "summary": {status.value: count for status, count in summarize(results).items()},
        "results": [as_dict(result) for result in results],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def combo_label(value: str | None, labels: Mapping[str, str] = COMBO_LABELS) -> str:
    """把 ``fc``/``fs`` 字段翻译成可读标记（未知值原样保留，空值记 ``-``）。"""
    if value is None:
        return "-"
    text = str(value).strip()
    if not text or text.lower() in ("dummy", "none", "null"):
        return "-"
    return labels.get(text.lower(), text)


def _problem_rows(results: Iterable[CheckResult]) -> list[list[str]]:
    rows: list[list[str]] = []
    for result in results:
        chart = result.chart
        rows.append(
            [
                result.title,
                result.record.song_id,
                result.record.type,
                difficulty_name(result.record.level_index),
                "-" if chart is None else chart.level,
                f"{result.ds:g}",
                f"{result.record.achievements:.4f}%",
                combo_label(result.record.fc),
                combo_label(result.record.fs, CHAIN_LABELS),
                format_notes(result.notes),
                _detail(result),
            ]
        )
    return rows


def _sort_key(result: CheckResult) -> tuple:
    record = result.record
    return (record.song_id, record.type, record.level_index)


def render_problem_list(
    results: Iterable[CheckResult],
    *,
    meta: dict | None = None,
    title: str = PROBLEM_LIST_TITLE,
) -> str:
    """渲染「可疑 + 边缘（+ 字段不符）」文本清单，用于存档与人工复查。

    分组顺序为 ``_PROBLEM_GROUPS``（可疑 → 边缘 → 字段不符）；可疑组按「与最近可行成绩的
    差值」降序（越离谱越靠前），其余组按 ``(曲目 ID, 类型, 难度)`` 升序，保证同样输入
    得到可 diff 的稳定输出。``meta`` 与 JSON 报告使用同一份字典。
    """
    results = list(results)
    meta = meta or {}
    counts = summarize(results)

    lines = [title, "-" * 72]
    lines.append(f"生成时间：{meta.get('generated_at', '-')}")
    source = meta.get("source_detail") or meta.get("source") or "-"
    if meta.get("source_detail") and meta.get("source"):
        source = f"{meta['source_detail']}（来源 {meta['source']}）"
    lines.append(f"数据来源：{source}")
    if meta.get("break_table"):
        try:
            score_tolerance = float(meta.get("score_tolerance", 0)) / 10000
        except (TypeError, ValueError):
            score_tolerance = 0.0
        lines.append(
            f"判定配置：{meta['break_table']} 表 / {meta.get('window', 'floor')} 窗口 / "
            f"物量容差 {meta.get('tolerance', 0)} / 分数容差 {score_tolerance:.4f}%"
        )
    parts = [f"共校验 {sum(counts.values())} 条成绩"]
    for status in (Status.IMPOSSIBLE, Status.MARGINAL, Status.FIELD_ERROR, Status.SKIPPED, Status.OK):
        parts.append(f"{status.label} {counts[status]}")
    lines.append("统计：" + " | ".join(parts))
    lines.append(
        "说明：可疑 = 该物量下无法达成；边缘 = 与最近可行成绩的差距落在容差内"
        "（可能是数据/模型舍入）；字段不符 = 成绩自带 ra/rate/ds 与谱面不一致。"
    )

    for status in _PROBLEM_GROUPS:
        if status is Status.IMPOSSIBLE:
            group = sorted(
                (r for r in results if r.status is status),
                key=lambda r: (-abs(r.nearest_delta or 0), _sort_key(r)),
            )
        else:
            group = sorted((r for r in results if r.status is status), key=_sort_key)
        lines.append("")
        lines.append(f"【{status.label}】{len(group)} 条")
        if not group:
            lines.append("（无）")
            continue
        lines.append(render_table(_PROBLEM_HEADERS, _problem_rows(group), _PROBLEM_ALIGNS))
    return "\n".join(lines)


def write_problem_list(
    path: str | Path,
    results: Iterable[CheckResult],
    *,
    meta: dict | None = None,
    title: str = PROBLEM_LIST_TITLE,
) -> Path:
    """把可疑/边缘清单写成 UTF-8 文本文件（等宽表格），返回实际写入的路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_problem_list(results, meta=meta, title=title)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return target
