"""``check_scores.py`` 命令行入口的单元测试（不联网）。"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

from check_scores import DEFAULT_OUT_DIR, build_parser, main, resolve_output
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
        "--out-dir",
        "--csv",
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
    assert args.out is None and args.csv is None and args.list_file is None
    assert args.out_dir == DEFAULT_OUT_DIR == "out"
    assert not args.strict and not args.no_field_check and not args.login_only


def test_output_options_accept_bare_flags():
    """`--csv` / `--out` / `--list-file` 不带值时使用缺省文件名。"""
    args = build_parser().parse_args(["--csv", "--out", "--list-file"])
    assert (args.out, args.csv, args.list_file) == ("report.json", "report.csv", "suspicious.txt")


def test_resolve_output_puts_bare_names_into_out_dir(tmp_path):
    assert resolve_output("suspicous.txt", "out") == Path("out") / "suspicous.txt"
    assert resolve_output("out/report.csv", "out") == Path("out/report.csv")
    absolute = tmp_path / "report.csv"
    assert resolve_output(str(absolute), "out") == absolute


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
            "--csv",
            "out/report.csv",
            "--quiet",
        ]
    )
    assert (args.source, args.window, args.tolerance, args.score_tolerance) == ("test", "round", 0, 0)
    assert args.list_file == "out/suspicious.txt"
    assert args.csv == "out/report.csv"
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


def test_csv_output_is_written(tmp_path, capsys):
    """``--csv`` 应把全部结果（含 BOM 表头）写成 Excel 可直接打开的 CSV。"""
    code = main(
        [
            "--source",
            "file",
            "--records-file",
            str(write_source_files(tmp_path)),
            "--music-data-file",
            str(tmp_path / "charts.json"),
            "--csv",
            str(tmp_path / "nested" / "report.csv"),
            "--quiet",
        ]
    )
    assert code in (0, 2)
    target = tmp_path / "nested" / "report.csv"
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0][0] == "状态" and rows[0][7] == "成绩(%)"
    assert len(rows) == 2 and rows[1][1] == "Test" and rows[1][7] == "99.1234"
    assert "CSV 表格（1 行" in capsys.readouterr().out


def write_source_files(tmp_path) -> Path:
    """写一份「1 条成绩 + 1 个谱面」的离线数据，返回成绩文件路径。"""
    charts = [
        {"id": "1", "title": "Test", "type": "SD", "ds": [5.0], "level": ["5"], "charts": [{"notes": [10, 2, 3, 1]}]}
    ]
    records = [{"song_id": "1", "type": "SD", "level_index": 0, "achievements": 99.1234}]
    (tmp_path / "charts.json").write_text(json.dumps(charts), encoding="utf-8")
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(records), encoding="utf-8")
    return records_path


def test_bare_output_name_lands_in_out_dir(tmp_path, monkeypatch, capsys):
    """只写文件名（不带目录）时输出应落在 ``--out-dir`` 里，而不是散到项目根目录。"""
    records_path = write_source_files(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--source",
            "file",
            "--records-file",
            str(records_path),
            "--music-data-file",
            str(tmp_path / "charts.json"),
            "--csv",
            "report.csv",
            "--out-dir",
            "reports",
            "--quiet",
        ]
    )
    assert code in (0, 2)
    target = tmp_path / "reports" / "report.csv"
    assert target.exists()
    assert not (tmp_path / "report.csv").exists()
    # 提示里给出绝对路径，避免「文件到底写哪了」的歧义
    assert str(target) in capsys.readouterr().out

