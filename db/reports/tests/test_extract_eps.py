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
    extract_one,
    slice_body_html,
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
    assert all(v.confidence == "rule" for v in values)


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
    padded = '<html><body><div id="ctx-content">' + padded + "</div></body></html>"
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


REALISTIC_HTML = """<html><head><title>贵州茅台(600519)：Q1开门红</title></head><body>
<div class="report-header"><h1>贵州茅台(600519)</h1><p>投资要点：</p></div>
<div class="report-content">
<p>事件：公司发布2024年一季报，实现营业总收入464.7亿元，同比+18.1%。</p>
<p>我们微调盈利预测，预计2024-2026年EPS分别为68.50/79.10/90.20元，对应PE 25/22/19倍，维持“买入”评级。</p>
<table class="data-table">
<thead><tr><th>指标</th><th>2023A</th><th>2024E</th><th>2025E</th><th>2026E</th></tr></thead>
<tbody>
<tr><td>营业总收入(百万元)</td><td>147,694</td><td>172,000</td><td>198,000</td><td>225,000</td></tr>
<tr><td>每股收益(元)</td><td>59.49</td><td>68.50</td><td>79.10</td><td>90.20</td></tr>
<tr><td>市盈率(倍)</td><td>28.6</td><td>25.0</td><td>22.0</td><td>19.0</td></tr>
</tbody></table>
<p>风险提示：宏观需求不及预期。</p>
</div>
<script>window.__DATA__ = {"每股收益": 999};</script>
</body></html>"""


def test_realistic_page_prefers_table_and_ignores_other_rows():
    text = html_to_text(REALISTIC_HTML)
    values = {v.fiscal_year: v for v in extract_eps(text, {2024, 2025, 2026})}
    assert {y: v.eps for y, v in values.items()} == {2024: 68.50, 2025: 79.10, 2026: 90.20}
    assert all(v.extractor == "table_eps_row" for v in values.values())
    assert "147,694" not in " ".join(v.raw_text for v in values.values())


def test_realistic_page_without_eps_table_falls_back_to_text():
    html = REALISTIC_HTML.replace("每股收益(元)</td><td>59.49</td><td>68.50</td><td>79.10</td><td>90.20",
                                   "每股收益(元)</td><td>—</td><td>—</td><td>—</td><td>—")
    values = {v.fiscal_year: v for v in extract_eps(html_to_text(html), {2024, 2025, 2026})}
    assert {y: v.eps for y, v in values.items()} == {2024: 68.50, 2025: 79.10, 2026: 90.20}
    assert all(v.extractor == "text_range" for v in values.values())


def test_text_range_with_yuan_between_values():
    # eastmoney prose style: "EPS分别为49.83元、58.60元、68.27元"
    text = "盈利预测与投资建议。预计2022-2024年EPS分别为49.83元、58.60元、68.27元，对应动态PE分别为35倍、30倍、26倍。"
    values = {v.fiscal_year: v for v in extract_eps(text, {2023, 2024, 2025})}
    assert {y: v.eps for y, v in values.items()} == {2023: 58.60, 2024: 68.27}
    assert all(v.extractor == "text_range" for v in values.values())


SHELL_WITH_DECOY = """<html><body>
<div class="main"><table><tr><th>名称</th><th>2023E</th><th>2024E</th></tr>
<tr><td>每股收益(元)</td><td>9.99</td><td>9.99</td></tr></table></div>
<div id="ctx-content" class="ctx-content"><p>正文</p>
<p>预计2023-2025年EPS分别为1.56、1.89、2.20元。</p></div>
<div class="footer"><table><tr><th>指标</th><th>2023E</th><th>2024E</th></tr>
<tr><td>每股收益(元)</td><td>8.88</td><td>8.88</td></tr></table></div>
</body></html>"""


def test_slice_body_html_excludes_shell_decoys():
    fragment, scope = slice_body_html(SHELL_WITH_DECOY)
    assert scope == "ctx-content"
    assert "9.99" not in fragment and "8.88" not in fragment
    values = {v.fiscal_year: v.eps for v in extract_eps(html_to_text(fragment), {2023, 2024, 2025})}
    assert values == {2023: 1.56, 2024: 1.89, 2025: 2.20}


def test_slice_body_html_falls_back_when_anchor_missing():
    fragment, scope = slice_body_html("<html><body><p>无容器</p></body></html>")
    assert scope == "whole-document"
    assert "无容器" in fragment


def test_sample_includes_source_context(tmp_path):
    from db.reports.extract_eps import cmd_sample

    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    day = dt.date(2024, 4, 1)
    body = paths.body_file(day, "AP7")
    body.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(body, "wb") as fh:
        fh.write(("<html><body><div id=\"ctx-content\">" + "<p>前言段落。</p>" * 30
                  + "<p>预计2024-2026年EPS分别为1.11元、1.22元、1.33元。</p></div></body></html>").encode())
    record = extract_one(
        paths,
        {
            "info_code": "AP7",
            "publish_date": "2024-04-01",
            "stock_code": "600519",
            "fiscal_year_this": 2024,
            "fiscal_year_next": 2025,
            "fiscal_year_two": 2026,
        },
    )
    out = paths.meta_dir / "extracted" / "20240401" / "AP7.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    payload = cmd_sample(argparse.Namespace(docs=3, per_status=True), paths)
    assert payload["docs"] == 1
    item = payload["sample"][0]
    assert item["body_scope"] == "ctx-content"
    assert item["body_head"]
    value = item["values"][0]
    assert value["raw_text"] in value["context"]
    assert "1.11" in value["context"]


def test_offset_points_at_the_stripped_sentence_not_the_raw_chunk():
    # a sentence that starts with spaces: offset must land on the first
    # non-space character so text[offset:offset+len(raw_text)] == raw_text
    text = "前言。\n   预计2024-2026年EPS分别为1.11元、1.22元、1.33元。"
    for value in extract_eps(text, {2024, 2025, 2026}):
        assert text[value.char_offset : value.char_offset + len(value.raw_text)] == value.raw_text


# ---------------------------------------------------------------------------
# Review round 1 regressions (B1 / B2 / S1 / S7)
# ---------------------------------------------------------------------------


def test_text_list_fallback_never_uses_a_leading_history_year():
    # B1: the first year token ("2023") must not shift the whole column.
    text = "我们回顾2023年的经营情况，2024年EPS分别为1.20、1.50、1.80元。"
    assert extract_eps(text, {2024, 2025, 2026}) == []


def test_text_list_fallback_requires_trailing_consecutive_years():
    text = "预计2024、2025、2026年EPS分别为1.20、1.50、1.80元。"
    values = {v.fiscal_year: v.eps for v in extract_eps(text, {2024, 2025, 2026})}
    assert values == {2024: 1.20, 2025: 1.50, 2026: 1.80}


def test_explicit_range_with_wrong_value_count_is_not_guessed():
    text = "预计2024-2026年EPS分别为1.20元/1.50元。"
    assert extract_eps(text, {2024, 2025, 2026}) == []


def test_missing_container_is_not_mined(tmp_path):
    from db.reports.extract_eps import cmd_extract

    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    day = dt.date(2024, 4, 1)
    body = paths.body_file(day, "AP5")
    body.parent.mkdir(parents=True, exist_ok=True)
    shell = ("<html><body><table><tr><th>指标</th><th>2024E</th><th>2025E</th></tr>"
             "<tr><td>每股收益(元)</td><td>9.99</td><td>9.99</td></tr></table>"
             + "<p>" + "页面外壳文字。" * 40 + "</p></body></html>")
    with gzip.open(body, "wb") as fh:
        fh.write(shell.encode())
    worklist = tmp_path / "wl.jsonl"
    worklist.write_text(json.dumps({
        "info_code": "AP5", "publish_date": "2024-04-01", "stock_code": "600519",
        "fiscal_year_this": 2024, "fiscal_year_next": 2025, "fiscal_year_two": 2026,
    }) + "\n", encoding="utf-8")
    record = extract_one(paths, {
        "info_code": "AP5", "publish_date": "2024-04-01", "stock_code": "600519",
        "fiscal_year_this": 2024, "fiscal_year_next": 2025, "fiscal_year_two": 2026,
    })
    assert record["status"] == "no-container"
    assert record["values"] == []
    assert cmd_extract(argparse.Namespace(worklist=str(worklist)), paths)["stats"]["no-container"] == 1


def test_colspan_header_keeps_columns_aligned():
    html = ('<div id="ctx-content"><table>'
            '<tr><th>项目</th><th colspan="2">2024E</th><th>2025E</th></tr>'
            '<tr><td>每股收益(元)</td><td>1.11</td><td>1.22</td><td>2.33</td></tr>'
            "</table></div>")
    text = html_to_text(html)
    # without colspan padding the 2nd data value would be read as 2025
    values = {v.fiscal_year: v.eps for v in extract_from_table(text, {2024, 2025})}
    assert values == {2024: 1.11, 2025: 2.33}


def test_build_drops_values_without_provenance(tmp_path):
    paths = DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))
    _seed_db(paths.db_path)
    out = paths.meta_dir / "extracted" / "20240401" / "APX.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "info_code": "APX", "publish_date": "2024-04-01", "stock_code": "600519",
        "extractor_version": EXTRACT_VERSION, "extracted_at": "2026-11-25T01:00:00+00:00",
        "status": "ok", "text_chars": 10,
        "values": [
            {"fiscal_year": 2024, "eps": 1.23, "source": "body_text", "raw_text": None,
             "char_offset": None, "confidence": "rule", "extractor": "text_range"},
            {"fiscal_year": 2025, "eps": 2.34, "source": "body_text", "raw_text": "abc",
             "char_offset": 99, "confidence": "rule", "extractor": "text_range"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    payload = cmd_build(argparse.Namespace(), paths)
    assert payload["rows_inserted"] == 0


def test_text_list_rejects_history_years_even_for_two_values():
    # review round 2 B-1: n=2 must not borrow a year from an earlier clause
    assert extract_eps("2021、2022、2023年公司营收持续增长。预计2024年EPS分别为1.20、1.50元。", {2024, 2025, 2026}) == []
    assert extract_eps("2021-2023年营收增长。预计2024年EPS分别为1.20、1.50元。", {2024, 2025, 2026}) == []
    assert extract_eps("根据2023年的经营情况，2024年EPS分别为1.20、1.50元。", {2024, 2025, 2026}) == []


def test_text_list_accepts_a_real_two_year_enumeration():
    text = "预计2024、2025年EPS分别为1.20、1.50元。"
    values = {v.fiscal_year: v.eps for v in extract_eps(text, {2024, 2025, 2026})}
    assert values == {2024: 1.20, 2025: 1.50}


def test_year_range_accepts_full_width_and_mathematical_dashes():
    # review round 3 B-1: the range rule and the list rule must accept the
    # same dash variants, otherwise "2024－2025年" silently extracts nothing.
    for dash in ("－", "−", "〜"):
        text = f"预计2024{dash}2025年EPS分别为1.20、1.50元。"
        values = {v.fiscal_year: v.eps for v in extract_eps(text, {2024, 2025, 2026})}
        assert values == {2024: 1.20, 2025: 1.50}, dash


def test_year_binding_never_tail_binds_a_longer_enumeration():
    # review round 3 suggestion (S-1): three years followed by two values used
    # to emit 2025=1.20 / 2026=1.50 (whole column shifted).  It is ambiguous,
    # so nothing may be emitted.
    assert extract_eps("预计2024、2025、2026年EPS分别为1.20、1.50元。", {2024, 2025, 2026}) == []
    # the opposite direction: a history year must not supply the missing year
    assert extract_eps("2021、2022、2023年公司营收持续增长。预计2024年EPS分别为1.20、1.50元。", {2024, 2025, 2026}) == []
