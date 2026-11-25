from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db.reports.extract_eps import (  # noqa: E402
    EXTRACT_VERSION,
    cmd_build,
    expected_years,
    extract_eps,
    extract_from_table,
    extract_from_text,
    html_to_text,
)
from db.reports.fetch_detail import DetailPaths  # noqa: E402
from db.reports.fetch_em import Paths  # noqa: E402

YEARS = {2023, 2024, 2025}

HTML_TABLE = """<html><body>
<h2>盈利预测</h2>
<table>
<tr><td>项目</td><td>2022A</td><td>2023E</td><td>2024E</td><td>2025E</td></tr>
<tr><td>每股收益(元)</td><td>49.93</td><td>58.20</td><td>68.50</td><td>79.10</td></tr>
<tr><td>市盈率(倍)</td><td>35.0</td><td>30.0</td><td>25.5</td><td>22.1</td></tr>
</table>
<script>var x = "每股收益 999";</script>
</body></html>"""


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------


def test_html_to_text_keeps_table_rows_and_drops_scripts():
    text = html_to_text(HTML_TABLE)
    assert "| 项目 | 2022A | 2023E | 2024E | 2025E |" in text
    assert "| 每股收益(元) | 49.93 | 58.20 | 68.50 | 79.10 |" in text
    assert "999" not in text  # script content is not part of the body
    assert "盈利预测" in text


def test_html_to_text_unescapes_entities():
    text = html_to_text("<p>A&amp;B&nbsp;公司</p>")
    assert "A&B" in text


# ---------------------------------------------------------------------------
# Rule 1: table
# ---------------------------------------------------------------------------


def test_table_extraction_maps_year_columns():
    text = html_to_text(HTML_TABLE)
    values = extract_from_table(text, YEARS)
    got = {v.fiscal_year: v.eps for v in values}
    assert got == {2023: 58.20, 2024: 68.50, 2025: 79.10}
    assert all(v.confidence == "rule" and v.extractor == "table_eps_row" for v in values)


def test_table_drops_years_outside_the_forecast_window():
    text = html_to_text(HTML_TABLE)
    values = extract_from_table(text, {2024, 2025, 2026})
    assert {v.fiscal_year for v in values} == {2024, 2025}


def test_table_accepts_eps_label_variants():
    html = """<table>
    <tr><th>指标</th><th>2024E</th><th>2025E</th></tr>
    <tr><td>EPS（元）</td><td>1.11</td><td>1.33</td></tr>
    <tr><td>摊薄每股收益</td><td>1.10</td><td>1.30</td></tr>
    </table>"""
    text = html_to_text(html)
    values = extract_from_table(text, {2024, 2025})
    # first matching row wins at the merge stage; the raw scan sees both
    assert {(v.fiscal_year, v.eps) for v in values} == {(2024, 1.11), (2025, 1.33), (2024, 1.10), (2025, 1.30)}


def test_table_skips_rows_with_non_numeric_cells():
    html = """<table>
    <tr><th>指标</th><th>2024E</th></tr>
    <tr><td>每股收益</td><td>—</td></tr>
    </table>"""
    assert extract_from_table(html_to_text(html), {2024}) == []


# ---------------------------------------------------------------------------
# Rule 2/3: text
# ---------------------------------------------------------------------------


def test_text_range_pattern():
    text = "盈利预测：预计公司2023-2025年EPS分别为1.56、1.89、2.20元，维持买入评级。"
    values = extract_from_text(text, YEARS)
    assert {v.fiscal_year: v.eps for v in values} == {2023: 1.56, 2024: 1.89, 2025: 2.20}
    assert all(v.extractor == "text_range" and v.confidence == "rule" for v in values)


def test_text_range_slash_separated():
    text = "我们预计2024-2026年EPS为2.10/2.55/3.01元。"
    values = extract_from_text(text, {2024, 2025, 2026})
    assert {v.fiscal_year: v.eps for v in values} == {2024: 2.10, 2025: 2.55, 2026: 3.01}


def test_text_list_pattern_with_slash_years():
    text = "公司2023/2024/2025年EPS分别为3.10、3.80、4.50元。"
    values = extract_from_text(text, YEARS)
    assert {v.fiscal_year: v.eps for v in values} == {2023: 3.10, 2024: 3.80, 2025: 4.50}
    assert all(v.extractor == "text_list" for v in values)


def test_text_list_without_year_anchor_is_not_guessed():
    # Only one year mentioned: the three values cannot be bound to years
    # without guessing, so nothing is emitted.
    assert extract_from_text("参考2023年报表，公司EPS分别为3.10、3.80、4.50元。", YEARS) == []


def test_single_year_fallback_is_nlp():
    text = "2024年EPS为2.35元，同比增长20%。"
    values = extract_eps(text, YEARS)
    assert [(v.fiscal_year, v.eps, v.confidence) for v in values] == [(2024, 2.35, "nlp")]


def test_table_beats_single_year_fallback():
    text = html_to_text(HTML_TABLE) + "\n2024年EPS为1.00元。"
    values = {v.fiscal_year: v for v in extract_eps(text, YEARS)}
    assert values[2024].eps == 68.50
    assert values[2024].extractor == "table_eps_row"


def test_implausible_values_are_rejected():
    assert extract_eps("2024年EPS为99999元。", {2024}) == []


def test_no_eps_returns_empty():
    assert extract_eps("公司公告回购计划，金额不超过10亿元。", YEARS) == []


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_every_value_carries_raw_text_and_offset():
    text = html_to_text(HTML_TABLE)
    for value in extract_eps(text, YEARS):
        assert value.raw_text
        assert text[value.char_offset : value.char_offset + len(value.raw_text)] == value.raw_text


def test_text_offset_points_at_the_sentence():
    sentence = "预计公司2023-2025年EPS分别为1.56、1.89、2.20元。"
    text = "前言。\n" + sentence
    values = extract_eps(text, YEARS)
    assert values
    for value in values:
        assert text[value.char_offset :].startswith(sentence)


def test_expected_years_reads_report_meta_columns():
    assert expected_years({"fiscal_year_this": 2023, "fiscal_year_next": 2024, "fiscal_year_two": None}) == {2023, 2024}


# ---------------------------------------------------------------------------
# DuckDB build
# ---------------------------------------------------------------------------


def _seed_db(db_path: Path, with_forbidden: bool = True) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        if with_forbidden:
            con.execute("CREATE TABLE report_meta (info_code VARCHAR)")
            con.execute("CREATE TABLE consensus_eps (ts_code VARCHAR)")
            con.execute("CREATE TABLE rating_change (info_code VARCHAR)")
    finally:
        con.close()


def _write_extracted(paths: DetailPaths, info_code: str, day: str, values: list[dict]) -> None:
    out = paths.meta_dir / "extracted" / day / f"{info_code}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "info_code": info_code,
                "publish_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                "stock_code": "600519",
                "ts_code": "600519.SH",
                "extractor_version": EXTRACT_VERSION,
                "extracted_at": "2026-11-25T01:00:00+00:00",
                "status": "ok",
                "values": values,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_creates_table_and_leaves_other_tables_alone(tmp_path):
    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    _seed_db(paths.db_path)
    _write_extracted(
        paths,
        "AP1",
        "20240401",
        [
            {
                "fiscal_year": 2024,
                "eps": 68.5,
                "source": "body_table",
                "raw_text": "| 每股收益(元) | 58.20 | 68.50 |",
                "char_offset": 12,
                "confidence": "rule",
                "extractor": "table_eps_row",
            }
        ],
    )
    payload = cmd_build(argparse.Namespace(), paths)
    assert payload["rows_inserted"] == 1
    assert payload["by_confidence"] == {"rule": 1}
    assert payload["untouched_tables"] == {"report_meta": 0, "consensus_eps": 0, "rating_change": 0}
    con = duckdb.connect(str(paths.db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT info_code, fiscal_year, eps, confidence, raw_text, char_offset FROM report_eps_extracted"
        ).fetchone()
        assert row == ("AP1", 2024, 68.5, "rule", "| 每股收益(元) | 58.20 | 68.50 |", 12)
        pk = con.execute(
            "SELECT constraint_type FROM duckdb_constraints() "
            "WHERE table_name='report_eps_extracted' AND constraint_type='PRIMARY KEY'"
        ).fetchall()
        assert pk
    finally:
        con.close()


def test_build_is_idempotent(tmp_path):
    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    _seed_db(paths.db_path)
    _write_extracted(
        paths,
        "AP1",
        "20240401",
        [{"fiscal_year": 2024, "eps": 1.0, "source": "body_table", "raw_text": "r", "char_offset": 0,
          "confidence": "rule", "extractor": "table_eps_row"}],
    )
    first = cmd_build(argparse.Namespace(), paths)
    second = cmd_build(argparse.Namespace(), paths)
    assert first["rows_inserted"] == second["rows_inserted"] == 1


def test_build_refuses_when_other_tables_are_missing(tmp_path):
    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    _seed_db(paths.db_path, with_forbidden=False)
    _write_extracted(
        paths,
        "AP1",
        "20240401",
        [{"fiscal_year": 2024, "eps": 1.0, "source": "body_table", "raw_text": "r", "char_offset": 0,
          "confidence": "rule", "extractor": "table_eps_row"}],
    )
    with pytest.raises(RuntimeError):
        cmd_build(argparse.Namespace(), paths)


def test_extract_one_reads_gzip_body(tmp_path):
    from db.reports.extract_eps import extract_one

    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    day = dt.date(2024, 4, 1)
    body = paths.body_file(day, "AP9")
    body.parent.mkdir(parents=True, exist_ok=True)
    padded = HTML_TABLE.replace("</body>", "<p>" + "公司经营稳健，渠道扩张顺利。" * 20 + "</p></body>")
    with gzip.open(body, "wb") as fh:
        fh.write(padded.encode("utf-8"))
    record = extract_one(
        paths,
        {
            "info_code": "AP9",
            "publish_date": "2024-04-01",
            "stock_code": "600519",
            "fiscal_year_this": 2024,
            "fiscal_year_next": 2025,
            "fiscal_year_two": 2026,
        },
    )
    assert record["status"] == "ok"
    assert {v["fiscal_year"] for v in record["values"]} == {2024, 2025}
