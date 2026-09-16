"""``maimai_check.scoreline`` 的单元测试：判定窗口、BREAK 表、物量容差。"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from maimai_check.scoreline import (
    BREAK_TABLES,
    EXACT_BRK_LIMIT,
    MAX_SCORE,
    Notes,
    break_pairs,
    break_share_bases,
    is_exact,
    is_reachable,
    max_reachable_score,
    nearest_reachable,
    non_break_bitmask,
    reachable_within_tolerance,
    to_achievement,
    to_score,
)

#: 单个 note 的判定系数（TAP/TOUCH 权重 1、HOLD 2、SLIDE 3），按模块文档独立写出。
NOTE_OPTIONS = {
    1: (Fraction(5, 10), Fraction(8, 10), Fraction(10, 10)),
    2: (Fraction(10, 10), Fraction(16, 10), Fraction(20, 10)),
    3: (Fraction(15, 10), Fraction(24, 10), Fraction(30, 10)),
}
#: 单个 BREAK 的 (基础分, 池份额)，取自 T1 表（含 MISS 的 (0, 0)）。
BREAK_OPTIONS = tuple((Fraction(base, 10), Fraction(share, 20)) for base, share in BREAK_TABLES["T1"])


def random_rating(notes: Notes, rng: random.Random) -> Fraction:
    """随机分配每个音符的判定，返回该次「实际打歌」的成绩（S 单位）。"""
    units = Fraction(0)  # 基础分合计（单位为「基础单位」，即 U/10）
    for weight, count in ((1, notes.tap), (2, notes.hold), (3, notes.slide), (1, notes.touch)):
        options = NOTE_OPTIONS[weight]
        for _ in range(count):
            units += options[rng.randrange(3)]
    share = Fraction(0)
    for _ in range(notes.brk):
        base, gain = BREAK_OPTIONS[rng.randrange(len(BREAK_OPTIONS))]
        units += base
        share += gain
    return Fraction(1000000) * units / notes.total + Fraction(10000) * share / notes.brk


def reference_reachable(notes: Notes, score: int, table: str, window: str) -> bool:
    """改造前的参考实现（直接遍历 ``break_pairs``），仅用于交叉验证。"""
    if notes.brk <= 0:
        return False
    total = notes.total
    low_offset, high_offset = {"floor": (0, 2), "round": (-1, 1), "union": (-1, 2)}[window]
    divisor = 200000 * notes.brk
    mask = non_break_bitmask(notes)
    for base, share in break_pairs(notes.brk, table):
        low = (2 * score + low_offset) * total * notes.brk - 1000 * share * total
        high = (2 * score + high_offset) * total * notes.brk - 1000 * share * total
        units = -((-low) // divisor)
        if units * divisor >= high:
            continue
        rest = units - base
        if rest >= 0 and (mask >> rest) & 1:
            return True
    return False


def test_to_score_roundtrip():
    assert to_score(99.5983) == 995983
    assert to_score(101.0) == 1010000
    assert to_achievement(995983) == pytest.approx(99.5983)


@pytest.mark.parametrize("table", sorted(BREAK_TABLES))
@pytest.mark.parametrize("brk", range(1, 9))
def test_break_share_bases_matches_break_pairs(table: str, brk: int):
    """位集合形式与逐组合枚举形式必须完全一致。"""
    states = break_share_bases(brk, table)
    rebuilt = {
        (base, share)
        for share, bits in states.items()
        for base in range(bits.bit_length())
        if (bits >> base) & 1
    }
    assert rebuilt == set(break_pairs(brk, table))


def test_exact_path_matches_reference():
    """``brk`` 未超限时，新的位运算实现必须与参考实现逐条一致。"""
    rng = random.Random(2024)
    scores = [0, 1, 500000, 995000, 1000000, MAX_SCORE, MAX_SCORE + 1]
    for _ in range(60):
        notes = Notes(
            tap=rng.randint(1, 40),
            hold=rng.randint(0, 20),
            slide=rng.randint(0, 20),
            touch=rng.randint(0, 10),
            brk=rng.randint(1, 10),
        )
        for table in ("T1", "break2600"):
            for window in ("floor", "round", "union"):
                for score in scores + [rng.randint(0, MAX_SCORE)]:
                    assert is_reachable(notes, score, table, window) == reference_reachable(
                        notes, score, table, window
                    ), (notes, score, table, window)


def test_is_exact_boundary():
    assert is_exact(Notes(10, 0, 0, 0, 0))
    assert is_exact(Notes(10, 0, 0, 0, EXACT_BRK_LIMIT))
    assert not is_exact(Notes(10, 0, 0, 0, EXACT_BRK_LIMIT + 1))


def test_full_perfect_is_the_upper_bound():
    notes = Notes(tap=600, hold=80, slide=40, touch=30, brk=12)
    assert is_reachable(notes, MAX_SCORE, "T1", "floor")
    assert not is_reachable(notes, MAX_SCORE + 1, "T1", "floor")
    assert max_reachable_score(notes) == MAX_SCORE


def test_brk_zero_chart_has_no_break_pool():
    notes = Notes(10, 0, 0)
    assert notes.total == 10
    assert is_reachable(notes, 1000000, "T1", "floor")  # 全 PERFECT
    assert is_reachable(notes, 500000, "T1", "floor")  # 全 GOOD
    assert not is_reachable(notes, 1000001, "T1", "floor")
    assert not is_reachable(notes, 1010000, "T1", "floor")  # 没有 BREAK 池
    assert max_reachable_score(notes) == 1000000


def test_non_break_bitmask_basics():
    mask = non_break_bitmask(Notes(1, 0, 0))
    assert all((mask >> shift) & 1 for shift in (5, 8, 10))
    assert not (mask >> 9) & 1 and not (mask >> 7) & 1


@pytest.mark.parametrize("brk", [2, 7, 16, 33, EXACT_BRK_LIMIT, 150, 300])
def test_every_real_play_is_reachable(brk: int):
    """任何一次真实判定分配得到的成绩都必须被判为可达（含保守判定的大 BREAK 谱面）。"""
    rng = random.Random(9000 + brk)
    for _ in range(6):
        notes = Notes(
            tap=rng.randint(30, 800),
            hold=rng.randint(0, 120),
            slide=rng.randint(0, 90),
            touch=rng.randint(0, 60),
            brk=brk,
        )
        rating = random_rating(notes, rng)
        score = int(rating)  # 游戏显示值 = 向下取整
        assert is_reachable(notes, score, "T1", "floor"), (notes, rating)
        assert is_reachable(notes, score, "T1", "union"), (notes, rating)
        # round 窗口下，显示值只会是 floor 或 floor+1
        assert is_reachable(notes, score, "T1", "round") or is_reachable(
            notes, score + 1, "T1", "round"
        ), (notes, rating)
        assert nearest_reachable(notes, score, 200, "T1", "floor") == 0


def test_relaxed_is_superset_of_exact():
    """保守判定只会更宽松：精确判定为真时它必须也为真（不会误伤正常成绩）。"""
    from maimai_check.scoreline import _reachable_exact, _reachable_relaxed

    rng = random.Random(4242)
    for brk in (EXACT_BRK_LIMIT + 1, EXACT_BRK_LIMIT + 3):
        for _ in range(20):
            notes = Notes(
                tap=rng.randint(20, 300),
                hold=rng.randint(0, 60),
                slide=rng.randint(0, 40),
                touch=rng.randint(0, 30),
                brk=brk,
            )
            for score in (int(random_rating(notes, rng)), MAX_SCORE, MAX_SCORE - 1):
                exact = _reachable_exact(notes, score, "T1", "floor")
                relaxed = _reachable_relaxed(notes, score, "T1", "floor")
                assert not exact or relaxed, (notes, score, exact, relaxed)
                assert is_reachable(notes, score, "T1", "floor") == relaxed


def test_relaxed_rejects_scores_over_the_cap():
    notes = Notes(tap=500, hold=50, slide=50, touch=0, brk=200)
    assert is_reachable(notes, MAX_SCORE, "T1", "floor")
    assert not is_reachable(notes, MAX_SCORE + 1, "T1", "floor")


def test_tolerance_zero_means_strict():
    notes = Notes(295, 28, 66, 0, 2)
    assert reachable_within_tolerance(notes, 984464, 0, "T1", "floor") is None
    assert reachable_within_tolerance(notes, 984464, 1, "T1", "floor") == (0, 0, 0, 1)


@pytest.mark.parametrize(
    ("notes", "score", "note_delta", "nearest"),
    [
        (Notes(295, 28, 66, 0, 2), 984464, (0, 0, 0, 1), -3),
        (Notes(154, 17, 12, 0, 8), 992793, None, 4),
        (Notes(185, 17, 8, 0, 3), 985290, None, 58),
    ],
)
def test_real_data_regressions(notes: Notes, score: int, note_delta, nearest: int):
    """水鱼 ``test_data`` 中三条「差一点」的成绩（判定边界回归）。"""
    assert not is_reachable(notes, score, "T1", "floor")
    assert reachable_within_tolerance(notes, score, 1, "T1", "floor") == note_delta
    assert nearest_reachable(notes, score, 2000, "T1", "floor") == nearest
    assert nearest_reachable(notes, score + nearest, 2000, "T1", "floor") == 0
