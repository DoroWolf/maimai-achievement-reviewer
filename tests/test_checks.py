"""``maimai_check.checks`` 的单元测试：RA / 评级公式与成绩状态判定。"""

from __future__ import annotations

import pytest

from maimai_check.checks import (
    DEFAULT_SCORE_TOLERANCE,
    ChartInfo,
    Record,
    Status,
    check_record,
    compute_rating,
    rate_of,
    summarize,
)
from maimai_check.scoreline import MAX_SCORE, Notes


@pytest.mark.parametrize(
    ("ds", "achievement", "expected"),
    [
        (13.0, 100.5, 292),
        (14.0, 101.0, 315),
        (13.0, 100.4999, 290),
        (13.0, 100.0, 280),
        (13.0, 99.9999, 278),
        (13.0, 99.0, 267),
        (13.0, 98.9999, 265),
        (12.5, 97.0, 242),
        (10.0, 40.0, 25),
        (7.0, 5.0, 0),
    ],
)
def test_compute_rating(ds: float, achievement: float, expected: int):
    assert compute_rating(ds, achievement) == expected


def test_compute_rating_caps_achievement_at_100_5():
    assert compute_rating(13.0, 101.0) == compute_rating(13.0, 100.5)


@pytest.mark.parametrize(
    ("achievement", "code"),
    [
        (0.0, "d"),
        (49.9999, "d"),
        (50.0, "c"),
        (96.9999, "aaa"),
        (97.0, "s"),
        (97.9999, "s"),
        (98.0, "sp"),
        (99.0, "ss"),
        (99.4999, "ss"),
        (99.5, "ssp"),
        (99.9999, "ssp"),
        (100.0, "sss"),
        (100.4999, "sss"),
        (100.5, "sssp"),
        (101.0, "sssp"),
    ],
)
def test_rate_of(achievement: float, code: str):
    assert rate_of(achievement) == code


def make_chart(notes: Notes = Notes(10, 0, 0), ds: float = 13.0) -> ChartInfo:
    return ChartInfo("8", "テスト曲", "SD", 3, "13", ds, notes)


def make_record(chart: ChartInfo, achievement: float, **kwargs) -> Record:
    """构造字段自洽的成绩（``ra`` / ``rate`` 默认按公式填写）。"""
    ra = kwargs.pop("ra", "auto")
    rate = kwargs.pop("rate", "auto")
    record = Record(
        song_id=chart.song_id,
        type=chart.type,
        level_index=chart.level_index,
        achievements=achievement,
        ds=chart.ds,
        **kwargs,
    )
    record.ra = compute_rating(chart.ds, achievement) if ra == "auto" else ra
    record.rate = rate_of(achievement) if rate == "auto" else rate
    return record


def test_ok_record():
    chart = make_chart()
    result = check_record(make_record(chart, 100.0), chart)
    assert result.status is Status.OK
    assert result.issues == []
    assert result.score == 1000000
    assert result.nearest_delta is None and result.note_delta is None


def test_impossible_when_outside_every_tolerance():
    chart = make_chart()
    result = check_record(make_record(chart, 50.0001), chart, tolerance=0, score_tolerance=0)
    assert result.status is Status.IMPOSSIBLE
    assert "无解" in result.issues[0]


def test_small_gap_becomes_marginal_with_score_tolerance():
    chart = make_chart()
    result = check_record(make_record(chart, 50.0001), chart, tolerance=0)
    assert result.status is Status.MARGINAL
    assert "仅差" in result.issues[0]
    assert result.nearest_delta == -1


def test_note_tolerance_marks_marginal():
    chart = make_chart(Notes(295, 28, 66, 0, 2))
    result = check_record(make_record(chart, 98.4464), chart, tolerance=1)
    assert result.status is Status.MARGINAL
    assert result.note_delta == (0, 0, 0, 1)
    assert result.issues == []

    strict = check_record(make_record(chart, 98.4464), chart, tolerance=0, score_tolerance=0)
    assert strict.status is Status.IMPOSSIBLE
    assert strict.note_delta is None


def test_field_error_on_ra_mismatch():
    chart = make_chart()
    result = check_record(make_record(chart, 100.0, ra=1), chart)
    assert result.status is Status.FIELD_ERROR
    assert "ra 不符" in result.issues[0]


def test_field_error_on_rate_mismatch():
    chart = make_chart()
    result = check_record(make_record(chart, 100.0, rate="sssp"), chart)
    assert result.status is Status.FIELD_ERROR
    assert "rate 不符" in result.issues[0]


def test_field_error_on_score_over_the_cap():
    chart = make_chart()
    result = check_record(make_record(chart, 101.5), chart)
    assert result.status is Status.FIELD_ERROR
    assert "超过理论上限" in result.issues[0]


def test_skipped_without_chart_data():
    record = Record(song_id="9999", type="SD", level_index=3, achievements=100.0)
    result = check_record(record, None)
    assert result.status is Status.SKIPPED
    assert result.chart is None
    assert "谱面数据缺失" in result.issues[0]


def test_optional_dx_score_check():
    chart = make_chart()
    record = make_record(chart, 100.0, dx_score=31)
    assert check_record(record, chart).status is Status.OK
    result = check_record(record, chart, check_dx=True)
    assert result.status is Status.FIELD_ERROR
    assert "dxScore" in result.issues[0]


def test_optional_combo_check():
    chart = make_chart(Notes(0, 0, 0, 0, 10))
    perfect = make_record(chart, MAX_SCORE / 10000, fc="ap")
    assert check_record(perfect, chart, check_combo=True).status is Status.OK

    result = check_record(make_record(chart, MAX_SCORE / 10000, fc=None), chart, check_combo=True)
    assert result.status is Status.FIELD_ERROR
    assert "> 100.5%" in result.issues[0]


def test_summarize_counts():
    chart = make_chart()
    results = [
        check_record(make_record(chart, 100.0), chart),
        check_record(make_record(chart, 50.0001), chart, tolerance=0, score_tolerance=0),
        check_record(make_record(chart, 100.0, ra=1), chart),
        check_record(Record("9999", "SD", 3, 100.0), None),
    ]
    counts = summarize(results)
    assert counts[Status.OK] == 1
    assert counts[Status.IMPOSSIBLE] == 1
    assert counts[Status.FIELD_ERROR] == 1
    assert counts[Status.SKIPPED] == 1
    assert counts[Status.MARGINAL] == 0
    assert set(counts) == set(Status)


def test_status_labels_and_problem_flag():
    assert Status.OK.label == "通过"
    assert Status.MARGINAL.label == "边缘"
    assert Status.IMPOSSIBLE.is_problem and Status.FIELD_ERROR.is_problem
    assert not Status.OK.is_problem and not Status.MARGINAL.is_problem
    assert DEFAULT_SCORE_TOLERANCE == 1
