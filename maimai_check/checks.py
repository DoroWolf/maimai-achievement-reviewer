"""记录级校验：判断一条成绩在该物量下「解得出来吗」。

判定分级（``Status``）：内部标识一律用英文（``ok`` / ``marginal`` / ``impossible`` /
``skipped``），只有最终输出（终端 / JSON / CSV / 文本清单）才用中文标签（``Status.label``）。

* ``IMPOSSIBLE`` 可疑：分数在物量 ±tolerance、分数 ±score_tolerance 内都解不出来
* ``MARGINAL`` 边缘：原地无解，但物量 ±tolerance 或分数 ±score_tolerance 内可解
  （水鱼物量数据/模型舍入的边缘）
* ``SKIPPED`` 跳过：宴谱（id >= 100000）或物量数据缺失
* ``OK`` 通过

本模块只判「可解性」，不校验成绩自带的 ``ra`` / ``rate`` / ``ds`` / ``dxScore`` / ``fc``
字段（这些字段原样引用水鱼返回的数据，本程序不做一致性检查）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

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
    "summarize",
]

#: 机器人 ``maimaidx_mapping.rate_mapping``：短码 -> 展示名（只用于展示水鱼返回的 ``rate``）
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

#: 分数容差（S 单位，1 即 0.0001%）：与最近可行成绩的差值在此范围内时记为「边缘」。
#: 默认取 1，即只放行成绩百分数最后一位的 ±1 误差；超过它就一律记为「可疑」交由人工判断。
DEFAULT_SCORE_TOLERANCE = 1


class Status(str, Enum):
    """判定状态：成员值（内部标识）用英文，最终输出一律用 :attr:`label`（中文）。"""

    OK = "ok"
    MARGINAL = "marginal"
    IMPOSSIBLE = "impossible"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        """最终输出用的中文名。"""
        return STATUS_LABEL[self]


#: 状态的中文展示名（内部标识 -> 输出用文本）。
STATUS_LABEL: dict[Status, str] = {
    Status.OK: "通过",
    Status.MARGINAL: "边缘",
    Status.IMPOSSIBLE: "可疑",
    Status.SKIPPED: "跳过",
}


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
    """一条玩家成绩（来自 ``/player/records`` 或 ``/query/player``）。"""

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
) -> CheckResult:
    """校验一条成绩，返回 ``CheckResult``。

    ``tolerance`` 为物量容差（个），``score_tolerance`` 为分数容差（S 单位，1 = 0.0001%）；
    两者都用于把「可疑」与「边缘」区分开，默认只放行最小可分辨误差，更大的偏差一律记为可疑。
    只判可解性，不校验成绩自带的字段。
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

    # 3) 结论：只看分数解不解得出来，不校验成绩自带的字段
    if reachable:
        status = Status.OK
    elif note_delta is not None:
        status = Status.MARGINAL
    elif nearest is not None and abs(nearest) <= score_tolerance:
        status = Status.MARGINAL
    else:
        status = Status.IMPOSSIBLE

    return CheckResult(record, chart, status, issues, score, note_delta, nearest)


def summarize(results: Iterable[CheckResult]) -> dict[Status, int]:
    """统计各状态的条数。"""
    counts = {status: 0 for status in Status}
    for result in results:
        counts[result.status] += 1
    return counts
