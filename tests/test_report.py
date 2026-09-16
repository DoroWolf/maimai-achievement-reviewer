"""``maimai_check.report`` 的单元测试：宽度对齐、表格渲染与 JSON 输出。"""

from __future__ import annotations

import json

import pytest

from maimai_check.checks import ChartInfo, CheckResult, Record, Status, check_record
from maimai_check.report import (
    CHAIN_LABELS,
    as_dict,
    combo_label,
    difficulty_name,
    display_width,
    format_notes,
    render_problem_list,
    render_results,
    render_summary,
    render_table,
    write_json,
    write_problem_list,
)
from maimai_check.scoreline import Notes


def make_chart(notes: Notes = Notes(10, 0, 0), ds: float = 13.0) -> ChartInfo:
    """默认用无 BREAK 的谱面：全 PERFECT 即恰好 100.0000%，便于构造「通过」的成绩。"""
    return ChartInfo("84", "Catch The Future", "SD", 3, "13.0", ds, notes)


def make_marginal_result() -> CheckResult:
    chart = ChartInfo("84", "Catch The Future", "SD", 3, "13.1", 13.1, Notes(295, 28, 66, 0, 2))
    return check_record(make_record(chart, 98.4464), chart, tolerance=1)


def make_record(chart: ChartInfo, achievement: float) -> Record:
    return Record(chart.song_id, "SD", 3, achievement, ds=chart.ds)


META = {
    "source": "oauth",
    "source_detail": "水鱼 /player/records (OAuth)",
    "break_table": "T1",
    "window": "floor",
    "tolerance": 1,
    "score_tolerance": 1,
    "generated_at": "2026-09-16 16:40:00",
}


def test_display_width_counts_wide_characters():
    assert display_width("") == 0
    assert display_width("abc") == 3
    assert display_width("曲名") == 4
    assert display_width("a曲") == 3


def test_render_table_pads_to_equal_display_width():
    rows = [["可疑", "无解"], ["边缘", "可解"]]
    lines = render_table(["状态", "说明"], rows, ["left", "left"]).splitlines()
    assert lines[0] == "状态  说明"
    assert lines[1] == "----  ----"
    assert lines[2] == "可疑  无解"
    assert lines[3] == "边缘  可解"
    assert {display_width(line) for line in lines} == {display_width(lines[0])}


def test_format_notes_and_difficulty_name():
    assert format_notes(None) == "-"
    assert format_notes(Notes(1, 2, 3, 4, 5)) == "1+2+3+4+5"
    assert [difficulty_name(index) for index in (0, 3, 4)] == ["BSC", "MAS", "ReM"]
    assert difficulty_name(9) == "L9"


def test_render_results_skips_ok_records():
    chart = make_chart()
    ok = check_record(make_record(chart, 100.0), chart)
    assert render_results([ok]) == "未发现可疑成绩。"

    table = render_results([ok, make_marginal_result()])
    assert "边缘" in table and "Catch The Future" in table
    assert "通过" not in table
    assert "物量 brk+1 后即可解" in table


def test_render_summary_line():
    chart = make_chart()
    results = [
        check_record(make_record(chart, 100.0), chart),
        make_marginal_result(),
    ]
    summary = render_summary(results, elapsed=1.25)
    assert summary.startswith("共校验 2 条成绩")
    assert "边缘 1" in summary and "通过 1" in summary
    assert summary.endswith("耗时 1.2s")


def test_as_dict_is_serializable():
    chart = make_chart()
    payload = as_dict(check_record(make_record(chart, 100.0), chart))
    assert payload["status"] == "ok" and payload["status_label"] == "通过"
    assert payload["notes"] == [10, 0, 0, 0, 0]
    assert payload["note_count"] == 10
    assert payload["difficulty"] == "MAS"
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_as_dict_without_chart():
    result = check_record(Record("9999", "SD", 3, 100.0), None)
    payload = as_dict(result)
    assert payload["status"] == "skipped"
    assert payload["notes"] is None and payload["note_count"] is None
    assert payload["level"] is None


def make_impossible_result(delta: int | None, *, song_id: str = "1234", achievement: float = 101.0001) -> CheckResult:
    """直接构造「可疑」结果：便于精确控制 nearest_delta 与排序。"""
    chart = ChartInfo(song_id, f"Fake {song_id}", "DX", 3, "14.5", 14.5, Notes(1000, 100, 200, 50, 10))
    record = Record(song_id, "DX", 3, achievement, fc="ap", fs="fdx")
    return CheckResult(record, chart, Status.IMPOSSIBLE, ["无解"], int(achievement * 10000), None, delta)


def test_combo_label_translates_known_values():
    assert combo_label("ap") == "AP"
    assert combo_label("APP") == "AP+"
    assert combo_label("fc") == "FC"
    assert combo_label("fsd", CHAIN_LABELS) == "FSD"
    assert combo_label("sync", CHAIN_LABELS) == "SYNC"
    assert combo_label("weird") == "weird"  # 未知取值原样保留，便于发现新字段
    assert combo_label("") == "-"
    assert combo_label("dummy") == "-"
    assert combo_label(None) == "-"


def test_render_problem_list_contains_required_columns_and_groups():
    chart = make_chart()
    ok = check_record(make_record(chart, 100.0), chart)
    text = render_problem_list([ok, make_impossible_result(4321), make_marginal_result()], meta=META)

    assert text.startswith("maimai 成绩合法性校验")
    assert "数据来源：水鱼 /player/records (OAuth)（来源 oauth）" in text
    assert "判定配置：T1 表 / floor 窗口 / 物量容差 1 / 分数容差 0.0001%" in text
    assert "共校验 3 条成绩 | 可疑 1 | 边缘 1 | 字段不符 0 | 跳过 0 | 通过 1" in text
    assert "【可疑】1 条" in text and "【边缘】1 条" in text
    for header in ("曲名", "ID", "类型", "难度", "等级", "定数", "成绩", "全连", "连锁", "物量"):
        assert header in text
    assert "Fake 1234" in text and "1234" in text and "DX" in text and "14.5" in text
    assert "101.0001%" in text and "AP" in text and "101.0001" not in text.replace("101.0001%", "")
    assert "通过" not in text.split("【可疑】")[1].split("【边缘】")[0]
    assert "Catch The Future" in text and "边缘" in text and "-" in text


def test_render_problem_list_puts_worst_impossible_first():
    rows = [make_impossible_result(10, song_id="100"), make_impossible_result(9000, song_id="200")]
    text = render_problem_list(rows)
    assert text.index("Fake 200") < text.index("Fake 100")


def test_render_problem_list_marks_empty_groups():
    chart = make_chart()
    text = render_problem_list([check_record(make_record(chart, 100.0), chart)])
    assert text.count("（无）") == 3
    assert "【接近" not in text
    assert "曲名" not in text  # 没有条目时不该渲染表头


def test_write_problem_list_writes_utf8_document(tmp_path):
    target = tmp_path / "nested" / "suspicious.txt"
    assert write_problem_list(target, [make_impossible_result(5000)], meta=META) == target
    content = target.read_text(encoding="utf-8")
    assert content.endswith("\n") and content.count("\n") >= 8
    assert "Fake 1234" in content and "可疑" in content


def test_write_json_creates_parent_and_round_trips(tmp_path):
    chart = make_chart()
    results = [check_record(make_record(chart, 100.0), chart), make_marginal_result()]
    target = tmp_path / "nested" / "report.json"
    assert write_json(target, results, {"tolerance": 1}) == target

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["meta"] == {"tolerance": 1}
    assert payload["summary"][Status.OK.value] == 1
    assert payload["summary"][Status.MARGINAL.value] == 1
    assert len(payload["results"]) == 2
