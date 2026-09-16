from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from itertools import product
from typing import Iterable, Sequence

__all__ = [
    "BREAK_TABLES",
    "EXACT_BRK_LIMIT",
    "WINDOWS",
    "MAX_SCORE",
    "Notes",
    "break_pairs",
    "break_share_bases",
    "is_exact",
    "to_score",
    "to_achievement",
    "is_reachable",
    "nearest_reachable",
    "non_break_bitmask",
    "non_break_range",
    "reachable_within_tolerance",
    "max_reachable_score",
]

#: BREAK 判定表：(基础分, BREAK 池份额)，单位分别为 1/10 基础单位 与 1/20 池。
#: ``T1`` 为本次数据回归出的最佳拟合表（默认），其余为社区/机器人中的备选表。
BREAK_TABLES: dict[str, tuple[tuple[int, int], ...]] = {
    "T1": ((50, 20), (50, 15), (40, 8), (20, 6), (0, 0)),
    "break2600": ((50, 20), (40, 8), (20, 6), (0, 0)),
    "break2550": ((50, 15), (30, 8), (20, 6), (0, 0)),
    "break2500": ((50, 10), (25, 8), (20, 6), (0, 0)),
}

#: 判定窗口，见模块文档。
WINDOWS: dict[str, tuple[int, int]] = {
    "floor": (0, 2),
    "round": (-1, 1),
    "union": (-1, 2),
}

#: 单 note 三档判定的贡献（单位：1/10 基础单位）
_TAP_OPTIONS = (5, 8, 10)  # TAP / TOUCH：0.5u / 0.8u / 1.0u
_HOLD_OPTIONS = (10, 16, 20)  # HOLD：1.0u / 1.6u / 2.0u
_SLIDE_OPTIONS = (15, 24, 30)  # SLIDE：1.5u / 2.4u / 3.0u

#: 分数上限（101.0000%），超过即为不可能成绩
MAX_SCORE = 1_010_000

#: 精确判定的 BREAK 数上限；超过则改用保守判定。
EXACT_BRK_LIMIT = 64


@dataclass(frozen=True, slots=True)
class Notes:
    """谱面物量。``touch`` 仅 DX 谱面存在，SD 谱面为 0。"""

    tap: int
    hold: int
    slide: int
    touch: int = 0
    brk: int = 0

    @classmethod
    def from_api(cls, raw: Sequence[int]) -> "Notes":
        """按 API 的 ``charts[*].notes`` 构造。

        SD 谱面为 ``[tap, hold, slide, brk]``，DX 谱面为 ``[tap, hold, slide, touch, brk]``。
        """
        values = [int(v) for v in raw]
        if len(values) == 4:
            return cls(values[0], values[1], values[2], 0, values[3])
        if len(values) == 5:
            return cls(values[0], values[1], values[2], values[3], values[4])
        raise ValueError(f"无法识别的物量数组（长度 {len(values)}）：{values}")

    def to_api(self) -> list[int]:
        """还原为 API 的物量数组（touch 为 0 的 DX 谱面仍需输出 5 项）。"""
        return [self.tap, self.hold, self.slide, self.touch, self.brk]

    @property
    def total(self) -> int:
        """总权重 T。"""
        return self.tap + 2 * self.hold + 3 * self.slide + self.touch + 5 * self.brk

    @property
    def note_count(self) -> int:
        """音符个数。"""
        return self.tap + self.hold + self.slide + self.touch + self.brk

    def perturb(self, dt: int, dh: int, ds: int, db: int) -> "Notes":
        return Notes(self.tap + dt, self.hold + dh, self.slide + ds, self.touch, self.brk + db)

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return (
            f"tap={self.tap} hold={self.hold} slide={self.slide} "
            f"touch={self.touch} brk={self.brk} (T={self.total})"
        )


def to_score(achievements: float) -> int:
    """把 ``achievements``（如 99.5983）转为本模块的分数单位 S（995983）。"""
    value = Decimal(str(achievements)).scaleb(4)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def to_achievement(score: int) -> float:
    """把分数单位 S 还原为 ``achievements``。"""
    return score / 10000


def _options_bitmask(count: int, options: Iterable[int]) -> int:
    """位集合：第 i 位为 1 表示「count 个音符各取三档」可以凑出 i（单位 1/10 基础单位）。"""
    mask = 1
    options = tuple(options)
    for _ in range(count):
        mask |= (mask << options[0]) | (mask << options[1]) | (mask << options[2])
    return mask


def _expand(mask: int, step: int) -> int:
    """把位集合 ``step`` 的每个元素作为平移量并入 ``mask``（等价于两集合的 Minkowski 和）。"""
    result = mask
    while step:
        low = step & -step
        result |= mask << (low.bit_length() - 1)
        step ^= low
    return result


@lru_cache(maxsize=None)
def non_break_bitmask(notes: Notes) -> int:
    """非 BREAK 部分所有可能贡献的位集合（单位 1/10 基础单位）。"""
    tap_mask = _options_bitmask(notes.tap + notes.touch, _TAP_OPTIONS)
    mask = _expand(tap_mask, _options_bitmask(notes.hold, _HOLD_OPTIONS))
    return _expand(mask, _options_bitmask(notes.slide, _SLIDE_OPTIONS))


@lru_cache(maxsize=None)
def non_break_range(notes: Notes) -> tuple[int, int]:
    """非 BREAK 部分贡献的可达区间 ``(最小, 最大)``（单位 1/10 基础单位）。"""
    low = 5 * notes.tap + 10 * notes.hold + 15 * notes.slide + 5 * notes.touch
    high = 10 * notes.tap + 20 * notes.hold + 30 * notes.slide + 10 * notes.touch
    return low, high


@lru_cache(maxsize=None)
def break_pairs(brk: int, table: str = "T1") -> tuple[tuple[int, int], ...]:
    """``brk`` 个 BREAK 的 (基础分合计, 池份额合计) 所有可能组合（已去重）。

    仅作为参考实现保留（测试用它交叉验证 :func:`break_share_bases`）；
    组合数随 ``brk`` 立方级增长，判定路径不再直接使用它。
    """
    options = BREAK_TABLES[table]
    states: set[tuple[int, int]] = {(0, 0)}
    for _ in range(brk):
        states |= {(base + db, share + dshare) for base, share in states for db, dshare in options}
    return tuple(states)


#: ``(brk, table) -> {池份额合计: 基础分位集合}``，用于增量计算与反转编码复用。
_BREAK_STATES: dict[tuple[int, str], dict[int, int]] = {}
#: ``(brk, table) -> (升序份额元组, 与之一一对应的反转编码位集合)``
_BREAK_LOOKUP: dict[tuple[int, str], tuple[tuple[int, ...], tuple[int, ...]]] = {}
#: 单个 BREAK 的最大基础分（单位 1/10 基础单位）
_MAX_BREAK_BASE = 50


def break_share_bases(brk: int, table: str = "T1") -> dict[int, int]:
    """``brk`` 个 BREAK 时，每个「池份额合计」下可达的「基础分合计」位集合。

    返回 ``{share: bitmask}``，``bitmask`` 第 ``i`` 位为 1 表示基础分合计可达 ``i``
    （单位 1/10 基础单位）。结果按 ``(brk, table)`` 缓存，并从已缓存的较小 ``brk``
    增量推进，避免重复计算。
    """
    if brk <= 0:
        return {0: 1}
    key = (brk, table)
    cached = _BREAK_STATES.get(key)
    if cached is not None:
        return cached
    options = BREAK_TABLES[table]
    keep_intermediate = brk <= EXACT_BRK_LIMIT
    start = 0
    states: dict[int, int] = {0: 1}
    for candidate in range(brk - 1, 0, -1):
        if (candidate, table) in _BREAK_STATES:
            start = candidate
            states = _BREAK_STATES[(candidate, table)]
            break
    for current in range(start + 1, brk + 1):
        merged = dict(states)
        for share, bits in states.items():
            for base, gain in options:
                index = share + gain
                merged[index] = merged.get(index, 0) | (bits << base)
        states = merged
        if keep_intermediate:
            _BREAK_STATES[(current, table)] = states
    _BREAK_STATES[key] = states
    return states


#: 单字节位序反转查表
_BYTE_REVERSE = bytes(int(f"{value:08b}"[::-1], 2) for value in range(256))


def _bit_reverse(value: int, width: int) -> int:
    """把 ``value`` 的低 ``width`` 位按位序反转。"""
    size = (width + 7) // 8
    data = value.to_bytes(size, "little").translate(_BYTE_REVERSE)
    return int.from_bytes(data, "big") >> (size * 8 - width)


def break_lookup(brk: int, table: str = "T1") -> tuple[tuple[int, ...], tuple[int, ...]]:
    """返回 ``(份额序列, 反转编码的基础分位集合)``，供份额级 O(1) 查询。

    反转编码以 ``_MAX_BREAK_BASE * brk`` 为基准：第 ``j`` 位为 1 表示
    ``基础分合计 = _MAX_BREAK_BASE * brk - j`` 可达。
    """
    key = (brk, table)
    cached = _BREAK_LOOKUP.get(key)
    if cached is not None:
        return cached
    states = break_share_bases(brk, table)
    width = _MAX_BREAK_BASE * brk + 1
    shares = tuple(sorted(states))
    reversed_masks = tuple(_bit_reverse(states[share], width) for share in shares)
    lookup = (shares, reversed_masks)
    _BREAK_LOOKUP[key] = lookup
    return lookup


def _candidate_units(low: int, high: int, divisor: int) -> range:
    """满足 ``low <= u * divisor < high`` 的所有整数 ``u``。"""
    return range(-((-low) // divisor), (high - 1) // divisor + 1)


def _reachable_exact(notes: Notes, score: int, table: str, window: str) -> bool:
    """精确判定：枚举「池份额合计」，用一次移位 + 与运算查询全部基础分合计。"""
    total = notes.total
    brk = notes.brk
    divisor = 200000 * brk
    low_offset, high_offset = WINDOWS[window]
    score_low = (2 * score + low_offset) * total * brk
    score_high = (2 * score + high_offset) * total * brk
    mask = non_break_bitmask(notes)
    shares, reversed_masks = break_lookup(brk, table)
    max_base = _MAX_BREAK_BASE * brk
    for share, reversed_bases in zip(shares, reversed_masks):
        low = score_low - 1000 * share * total
        high = score_high - 1000 * share * total
        for units in _candidate_units(low, high, divisor):
            if units < 0:
                continue
            shift = max_base - units
            shifted = mask << shift if shift >= 0 else mask >> -shift
            if reversed_bases & shifted:
                return True
    return False


def _reachable_relaxed(notes: Notes, score: int, table: str, window: str) -> bool:
    """保守判定：只用「基础分 / 池份额」的区间界与非 BREAK 部分的可达区间。"""
    total = notes.total
    brk = notes.brk
    divisor = 200000 * brk
    low_offset, high_offset = WINDOWS[window]
    score_low = (2 * score + low_offset) * total * brk
    score_high = (2 * score + high_offset) * total * brk
    non_break_low, non_break_high = non_break_range(notes)
    max_base = _MAX_BREAK_BASE * brk
    for share in range(0, 20 * brk + 1):
        # 单个 BREAK 的基础分 ∈ [2.5, 5] × 池份额，故合计落在下面的区间内
        base_low = -((-5 * share) // 2)
        base_high = min(5 * share, max_base)
        if base_low > base_high:
            continue
        low = score_low - 1000 * share * total
        high = score_high - 1000 * share * total
        for units in _candidate_units(low, high, divisor):
            if units < 0:
                continue
            if max(base_low, units - non_break_high) <= min(base_high, units - non_break_low):
                return True
    return False


def _reachable_without_breaks(notes: Notes, score: int, window: str) -> bool:
    """``brk == 0`` 的谱面：只有基础分，理论上限为 100.0000%。"""
    total = notes.total
    mask = non_break_bitmask(notes)
    low_offset, high_offset = WINDOWS[window]
    low = (2 * score + low_offset) * total
    high = (2 * score + high_offset) * total
    for units in _candidate_units(low, high, 200000):
        if units >= 0 and (mask >> units) & 1:
            return True
    return False


def is_exact(notes: Notes) -> bool:
    """该物量是否会走精确判定（而非保守判定）。"""
    return notes.brk <= EXACT_BRK_LIMIT


def is_reachable(
    notes: Notes,
    score: int,
    table: str = "T1",
    window: str = "floor",
) -> bool:
    """``score``（S 单位）在该物量下是否存在合法的判定分配。

    推导：令 U 为「所有音符贡献之和 × 10」（1/10 基础单位），B 为 BREAK 池份额合计
    （以 1/20 池为单位），则

        R = 1000000 * (U/10) / T + 10000 * (B/20) / brk

    两边乘 ``2*T*brk`` 得 ``2R*T*brk = 200000*U*brk + 1000*B*T``；代入窗口
    ``2R ∈ [2S+a, 2S+b)`` 即得下式（全部为大整数运算，无浮点误差）。

    ``brk > EXACT_BRK_LIMIT`` 时改用保守判定，此时结果可能偏宽松（见模块文档）。
    """
    if notes.brk < 0:
        return False
    if notes.brk == 0:
        return _reachable_without_breaks(notes, score, window)
    if is_exact(notes):
        return _reachable_exact(notes, score, table, window)
    return _reachable_relaxed(notes, score, table, window)


def nearest_reachable(
    notes: Notes,
    score: int,
    radius: int = 200,
    table: str = "T1",
    window: str = "floor",
) -> int | None:
    """在 ``score`` 附近寻找最近的可行分数，返回其与目标的差值（带符号），无则 None。"""
    for delta in range(radius + 1):
        if is_reachable(notes, score + delta, table, window):
            return delta
        if delta and score - delta >= 0 and is_reachable(notes, score - delta, table, window):
            return -delta
    return None


def reachable_within_tolerance(
    notes: Notes,
    score: int,
    tolerance: int = 1,
    table: str = "T1",
    window: str = "floor",
) -> tuple[int, int, int, int] | None:
    """允许物量有 ``tolerance`` 个以内的偏差时是否可行（返回使其可行的偏差）。

    水鱼 ``music_data`` 对少数旧谱的物量与打歌版本可能存在 1 个音符级差异，
    因此默认把这类成绩归为「边缘」而非「不可能」。
    """
    if tolerance <= 0:
        return None
    span = range(-tolerance, tolerance + 1)
    candidates = [
        (dt, dh, ds, db)
        for dt, dh, ds, db in product(span, span, span, span)
        if 0 < abs(dt) + abs(dh) + abs(ds) + abs(db) <= tolerance
    ]
    candidates.sort(key=lambda p: sum(abs(v) for v in p))
    for dt, dh, ds, db in candidates:
        candidate = notes.perturb(dt, dh, ds, db)
        if min(candidate.tap, candidate.hold, candidate.slide) < 0 or candidate.brk < 0:
            continue
        if is_reachable(candidate, score, table, window):
            return (dt, dh, ds, db)
    return None


def max_reachable_score(notes: Notes, table: str = "T1", window: str = "floor") -> int:
    """该谱面在给定表/窗口下可达到的最高分（通常为 1010000）。"""
    for score in range(MAX_SCORE, -1, -1):
        if is_reachable(notes, score, table, window):
            return score
    return 0
