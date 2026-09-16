"""记录级校验：把一条成绩拆成「可解性」与「字段自洽」两类判定。

判定分级（``Status``）：

* ``IMPOSSIBLE`` 可疑：分数在物量 ±tolerance、分数 ±score_tolerance 内都解不出来
* ``MARGINAL`` 边缘：原地无解，但物量 ±tolerance 或分数 ±score_tolerance 内可解
  （水鱼物量数据/模型舍入的边缘）
* ``FIELD_ERROR`` 字段不符：``ra`` / ``rate`` / ``ds`` 与标准公式或谱面数据不符
* ``SKIPPED`` 跳过：宴谱（id >= 100000）或物量数据缺失
* ``OK`` 通过
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from .scoreline import (
    MAX_SCORE,
    Notes,
    is_reachable,
    nearest_reachable,
    reachable_within_tolerance,
    to_score,
)

__all__ = [
    "ChartInfo",
    "CheckResult",
    "DEFAULT_SCORE_TOLERANCE",
    "Record",
    "Status",
    "check_record",
    "compute_rating",
    "rate_of",
    "summarize",
]

#: 与水鱼 / 机器人一致的评级区间（``maimaidx_mapping.score_to_rate``）
SCORE_TO_RATE: tuple[tuple[float, float, str], ...] = (
    (0.0, 50.0, "d"),
    (50.0, 60.0, "c"),
    (60.0, 70.0, "b"),
    (70.0, 75.0, "bb"),
    (75.0, 80.0, "bbb"),
    (80.0, 90.0, "a"),
    (90.0, 94.0, "aa"),
    (94.0, 97.0, "aaa"),
    (97.0, 98.0, "s"),
    (98.0, 99.0, "sp"),
    (99.0, 99.5, "ss"),
    (99.5, 100.0, "ssp"),
    (100.0, 100.5, "sss"),
    (100.5, float("inf"), "sssp"),
)

#: 机器人 ``maimaidx_mapping.rate_mapping``：短码 -> 展示名
RATE_MAPPING: dict[str, str] = {
    "d": "D",
    "c": "C",
    "b": "B",
    "bb": "BB",
    "bbb": "BBB",
    "a": "A",
    "aa": "AA",
    "aaa": "AAA",
    "s": "S",
    "sp": "S+",
    "ss": "SS",
    "ssp": "SS+",
    "sss": "SSS",
    "sssp": "SSS+",
}

#: AP+ 及以上成绩必然是全连（用于可选检查）
COMBO_REQUIRED_AP = ("ap", "app")

#: 分数容差（S 单位，1 即 0.0001%）：与最近可行成绩的差值在此范围内时记为「边缘」。
#: 默认取 1，即只放行成绩百分数最后一位的 ±1 误差；超过它就一律记为「可疑」交由人工判断。
DEFAULT_SCORE_TOLERANCE = 1


class Status(str, Enum):
    OK = "ok"
    MARGINAL = "marginal"
    IMPOSSIBLE = "impossible"
    FIELD_ERROR = "field_error"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        return STATUS_LABEL[self]

    @property
    def is_problem(self) -> bool:
        return self in (Status.IMPOSSIBLE, Status.FIELD_ERROR)


STATUS_LABEL: dict[Status, str] = {
    Status.OK: "通过",
    Status.MARGINAL: "边缘",
    Status.IMPOSSIBLE: "可疑",
    Status.FIELD_ERROR: "字段不符",
    Status.SKIPPED: "跳过",
}


def compute_rating(ds: float, achievement: float) -> int:
    """与水鱼 / 机器人 ``maimaidx_utils.compute_rating`` 完全一致的 单曲 RA 计算。"""
    achievement = round(achievement, 4)
    if achievement >= 100.5:
        base_ra = 22.4
    elif achievement == 100.4999:
        base_ra = 22.2
    elif achievement >= 100:
        base_ra = 21.6
    elif achievement == 99.9999:
        base_ra = 21.4
    elif achievement >= 99.5:
        base_ra = 21.1
    elif achievement >= 99:
        base_ra = 20.8
    elif achievement == 98.9999:
        base_ra = 20.6
    elif achievement >= 98:
        base_ra = 20.3
    elif achievement >= 97:
        base_ra = 20.0
    elif achievement == 96.9999:
        base_ra = 17.6
    elif achievement >= 94:
        base_ra = 16.8
    elif achievement >= 90:
        base_ra = 15.2
    elif achievement >= 80:
        base_ra = 13.6
    elif achievement == 79.9999:
        base_ra = 12.8
    elif achievement >= 75:
        base_ra = 12.0
    elif achievement >= 70:
        base_ra = 11.2
    elif achievement >= 60:
        base_ra = 9.6
    elif achievement >= 50:
        base_ra = 8.0
    elif achievement >= 40:
        base_ra = 6.4
    elif achievement >= 30:
        base_ra = 4.8
    elif achievement >= 20:
        base_ra = 3.2
    else:
        base_ra = 1.6
    return max(0, math.floor(ds * (min(100.5, achievement) / 100) * base_ra))


def rate_of(achievement: float) -> str:
    """按成绩区间返回评级短码（如 ``sssp``）。"""
    for low, high, code in SCORE_TO_RATE:
        if low <= achievement < high:
            return code
    return "d"


@dataclass(slots=True)
class ChartInfo:
    """谱面信息（来自 ``/music_data``）。"""

    song_id: str
    title: str
    type: str
    level_index: int
    level: str
    ds: float
    notes: Notes

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.song_id, self.type, self.level_index)


@dataclass(slots=True)
class Record:
    """一条玩家成绩（来自 ``/player/records``、``/query/player`` 或 ``/player/test_data``）。"""

    song_id: str
    type: str
    level_index: int
    achievements: float
    ra: int | None = None
    rate: str | None = None
    ds: float | None = None
    fc: str | None = None
    fs: str | None = None
    dx_score: int | None = None
    raw: dict | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.song_id, self.type, self.level_index)

    @classmethod
    def from_api(cls, raw: dict) -> "Record":
        """兼容不同接口的字段命名（``song_id`` / ``id``，``dxScore`` / ``dx_score``）。"""
        song_id = raw.get("song_id", raw.get("id"))
        if song_id is None:
            raise ValueError(f"成绩缺少曲目 ID：{raw}")
        return cls(
            song_id=str(song_id),
            type=str(raw.get("type", "SD")),
            level_index=int(raw.get("level_index", 0)),
            achievements=float(raw["achievements"]),
            ra=int(raw["ra"]) if raw.get("ra") is not None else None,
            rate=raw.get("rate"),
            ds=float(raw["ds"]) if raw.get("ds") is not None else None,
            fc=raw.get("fc"),
            fs=raw.get("fs"),
            dx_score=raw.get("dxScore", raw.get("dx_score")),
            raw=raw,
        )


@dataclass(slots=True)
class CheckResult:
    """一条成绩的校验结论。"""

    record: Record
    chart: ChartInfo | None
    status: Status
    issues: list[str] = field(default_factory=list)
    score: int = 0
    note_delta: tuple[int, int, int, int] | None = None
    nearest_delta: int | None = None

    @property
    def title(self) -> str:
        if self.chart is not None:
            return self.chart.title
        return f"未知曲目 {self.record.song_id}"

    @property
    def ds(self) -> float:
        if self.record.ds is not None:
            return self.record.ds
        if self.chart is not None:
            return self.chart.ds
        return 0.0

    @property
    def notes(self) -> Notes | None:
        return None if self.chart is None else self.chart.notes

    def describe_issues(self, sep: str = "；") -> str:
        return sep.join(self.issues)


def check_record(
    record: Record,
    chart: ChartInfo | None,
    *,
    table: str = "T1",
    window: str = "floor",
    tolerance: int = 1,
    score_tolerance: int = DEFAULT_SCORE_TOLERANCE,
    check_fields: bool = True,
    check_dx: bool = False,
    check_combo: bool = False,
) -> CheckResult:
    """校验一条成绩，返回 ``CheckResult``。

    ``tolerance`` 为物量容差（个），``score_tolerance`` 为分数容差（S 单位，1 = 0.0001%）；
    两者都用于把「可疑」与「边缘」区分开，默认只放行最小可分辨误差，更大的偏差一律记为可疑。
    """
    score = to_score(record.achievements)
    if chart is None:
        return CheckResult(record, None, Status.SKIPPED, ["谱面数据缺失，无法校验"], score)

    notes = chart.notes
    issues: list[str] = []
    note_delta: tuple[int, int, int, int] | None = None
    nearest: int | None = None

    # 1) 理论上限
    if score > MAX_SCORE:
        issues.append(f"成绩 {record.achievements:.4f}% 超过理论上限 101.0000%")
        reachable = False
    # 2) 可解性
    elif is_reachable(notes, score, table, window):
        reachable = True
    else:
        reachable = False
        note_delta = reachable_within_tolerance(notes, score, tolerance, table, window)
        if note_delta is None:
            radius = 2000 if notes.brk <= 16 else 50
            nearest = nearest_reachable(notes, score, radius, table, window)
            if nearest is None:
                detail = "未在 ±%d 内找到" % radius
            else:
                detail = f"{nearest / 10000:+.4f}%"
            if nearest is not None and abs(nearest) <= score_tolerance:
                issues.append(
                    f"该成绩在物量 {notes} 下无解，但最接近的可行成绩仅差 {detail}"
                    f"（分数容差 ±{score_tolerance / 10000:.4f}%，判定表 {table}/{window}）"
                )
            else:
                issues.append(
                    f"该成绩在物量 {notes} 下无解（最近的可行成绩差值：{detail}，判定表 {table}/{window}）"
                )

    # 3) 字段自洽（水鱼数据中 dx/fc 存在被随机化的可能，故默认不查）
    if check_fields:
        if record.ds is not None and abs(record.ds - chart.ds) > 1e-6:
            issues.append(f"ds 不符：成绩 {record.ds} / 谱面 {chart.ds}")
        if record.ra is not None:
            expect_ra = compute_rating(chart.ds, record.achievements)
            if expect_ra != record.ra:
                issues.append(f"ra 不符：成绩 {record.ra} / 应为 {expect_ra}")
        if record.rate:
            expect_rate = rate_of(record.achievements)
            if expect_rate != record.rate:
                issues.append(
                    f"rate 不符：成绩 {record.rate} / 应为 {expect_rate}"
                    f"（{RATE_MAPPING.get(expect_rate, expect_rate)}）"
                )
    if check_dx:
        if record.dx_score is None:
            issues.append("缺少 dxScore，无法校验")
        elif record.dx_score > notes.note_count * 3:
            issues.append(
                f"dxScore {record.dx_score} 超过上限 {notes.note_count * 3}（物量 {notes.note_count} × 3）"
            )
    if check_combo:
        if record.achievements > 100.5 and (record.fc or "").lower() not in COMBO_REQUIRED_AP:
            issues.append(f"成绩 {record.achievements:.4f}% > 100.5% 但 fc={record.fc or '空'}")

    if not reachable and score <= MAX_SCORE:
        if note_delta is not None:
            status = Status.MARGINAL
        elif nearest is not None and abs(nearest) <= score_tolerance:
            status = Status.MARGINAL
        else:
            status = Status.IMPOSSIBLE
    elif issues:
        status = Status.FIELD_ERROR
    else:
        status = Status.OK

    return CheckResult(record, chart, status, issues, score, note_delta, nearest)


def summarize(results: Iterable[CheckResult]) -> dict[Status, int]:
    """统计各状态的条数。"""
    counts = {status: 0 for status in Status}
    for result in results:
        counts[result.status] += 1
    return counts
