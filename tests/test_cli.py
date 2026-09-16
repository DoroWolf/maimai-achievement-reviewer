"""``check_scores.py`` 命令行入口的单元测试（不联网）。"""

from __future__ import annotations

import io
import json
import sys

import pytest

from check_scores import build_parser, main
from maimai_check.checks import DEFAULT_SCORE_TOLERANCE


def test_help_renders_all_options():
    """回归：argparse 的 help 里出现裸 ``%`` 会让 ``--help`` 直接抛 ValueError。"""
    text = build_parser().format_help()
    for option in (
        "--source",
        "--break-table",
        "--window",
        "--tolerance",
        "--score-tolerance",
        "--out",
        "--list-file",
        "--login-only",
    ):
        assert option in text
    assert "0.0001%" in text
    assert "100.5%" in text


def test_defaults():
    args = build_parser().parse_args([])
    assert args.source == "oauth"
    assert args.break_table == "T1"
    assert args.window == "floor"
    assert args.tolerance == 1
    assert args.score_tolerance == DEFAULT_SCORE_TOLERANCE
    assert args.out is None and args.list_file is None
    assert not args.strict and not args.no_field_check and not args.login_only


def test_options_are_parsed():
    args = build_parser().parse_args(
        [
            "--source",
            "test",
            "--window",
            "round",
            "--tolerance",
            "0",
            "--score-tolerance",
            "0",
            "--list-file",
            "out/suspicious.txt",
            "--quiet",
        ]
    )
    assert (args.source, args.window, args.tolerance, args.score_tolerance) == ("test", "round", 0, 0)
    assert args.list_file == "out/suspicious.txt"
    assert args.quiet


def test_invalid_choice_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--window", "nope"])


def test_table_survives_gbk_console(tmp_path, monkeypatch):
    """回归：GBK 控制台遇到无法编码的字符（半角片假名 ``ﾟ``）时应退化为 ``?`` 而不是崩溃。

    真实数据里就有曲名（如水鱼 id 11558）带半角片假名，一旦抛 ``UnicodeEncodeError``
    整个报告（含 ``--out`` / ``--list-file`` 的落盘）都会中断。
    """
    charts = [
        {
            "id": "1",
            "title": "テストﾟ",
            "type": "SD",
            "ds": [5.0],
            "level": ["5"],
            "charts": [{"notes": [10, 2, 3, 1]}],
        }
    ]
    records = [{"song_id": "1", "type": "SD", "level_index": 0, "achievements": 99.1234}]
    (tmp_path / "charts.json").write_text(json.dumps(charts), encoding="utf-8")
    (tmp_path / "records.json").write_text(json.dumps(records), encoding="utf-8")

    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(sys, "stderr", console)

    code = main(
        [
            "--source",
            "file",
            "--records-file",
            str(tmp_path / "records.json"),
            "--music-data-file",
            str(tmp_path / "charts.json"),
            "--list-file",
            str(tmp_path / "suspicious.txt"),
        ]
    )
    console.flush()
    text = buffer.getvalue().decode("gbk")
    # 返回码 2 表示「有可疑成绩」，只要没因编码崩溃、清单正常落盘即达标
    assert code in (0, 2)
    assert "テスト?" in text
    assert (tmp_path / "suspicious.txt").exists()

