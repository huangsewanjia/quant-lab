"""WORK-623 正文入库 / 关联视图 / 质量闸的离线测试（不联网）。"""
from __future__ import annotations

import json

import duckdb
import pytest
from pypdf import PdfWriter

from db.checks import report_fulltext as checks
from db.etl import common
from db.etl import report_fulltext as rf


def make_pdf(path, pages: int = 3):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def write_meta(pdf, **overrides):
    meta = {
        "channel_id": "fxbaogao_vip",
        "report_id": int(pdf.name.split("-", 1)[0]),
        "title": "开源证券:《宁德时代(300750)：Q3业绩超预期》-发现报告",
        "org": "开源证券",
        "page_date": pdf.parent.name,
        "source_url": "https://www.fxbaogao.com/detail/" + pdf.name.split("-", 1)[0],
        "bytes": pdf.stat().st_size,
        "pages": 3,
        "sha256": rf.sha256_file(pdf),
        "fetched_at": "2026-11-27T01:00:00+08:00",
        "fetch_mode": "cdp",
    }
    meta.update(overrides)
    path = pdf.with_suffix(".meta.json")
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def stub_meta_table(con):
    con.execute("create table if not exists report_meta(info_code varchar, title varchar, "
                "org_name varchar, publish_date date)")


def ensure_meta_stub():
    """真库里有 report_meta；临时 home 的测试库先建同结构空表，让视图能建起来。"""
    con = common.duckdb_connect()
    try:
        stub_meta_table(con)
    finally:
        con.close()


@pytest.fixture()
def con(qdb):
    connection = common.duckdb_connect()
    stub_meta_table(connection)
    rf.apply_schema(connection)
    yield connection
    connection.close()


def test_schema_objects_created(con):
    tables = {r[0] for r in con.execute(
        "select table_name from information_schema.tables where table_schema='main'").fetchall()}
    assert {"report_fulltext", "report_fulltext_changes", "report_meta"} <= tables
    views = {r[0] for r in con.execute(
        "select table_name from information_schema.views where table_schema='main'").fetchall()}
    assert {"v_report_fulltext", "v_report_fulltext_link_candidates",
            "v_report_fulltext_linked", "v_report_fulltext_unlinked"} <= views


def test_link_view_window_org_suffix_and_ambiguity(con):
    con.execute(
        "insert into report_fulltext values (1,'fxbaogao','开源证券:《宁德时代(300750)：Q3业绩超预期》-发现报告',"
        "'开源证券',DATE '2026-11-16','https://www.fxbaogao.com/detail/1','/x/1-a.pdf',900,4,'sha1',"
        "TIMESTAMP '2026-11-27 01:00:00','cdp','/x/1.meta.json',TIMESTAMP '2026-11-27 01:01:00')")
    # 同一报告的两份 info_code（相差 1 天和 3 天），机构带「股份有限公司」后缀
    con.execute("insert into report_meta values ('AP2','宁德时代(300750)：Q3业绩超预期','开源证券股份有限公司',DATE '2026-11-19')")
    con.execute("insert into report_meta values ('AP1','宁德时代(300750)：Q3业绩超预期','开源证券股份有限公司',DATE '2026-11-15')")
    # 超出 ±3 天窗口
    con.execute("insert into report_meta values ('AP0','宁德时代(300750)：Q3业绩超预期','开源证券股份有限公司',DATE '2026-11-10')")
    assert con.execute("select count(*) from v_report_fulltext_link_candidates").fetchone()[0] == 2
    linked = con.execute(
        "select info_code, day_gap, candidate_count from v_report_fulltext_linked").fetchall()
    assert linked == [("AP1", 1, 2)]  # 日差最小；并列时 info_code 最小，确定性
    assert con.execute("select count(*) from v_report_fulltext_unlinked").fetchone()[0] == 0


def test_org_mismatch_and_empty_title_not_linked(con):
    con.execute(
        "insert into report_fulltext values (2,'fxbaogao','媒体报告：某行业观察-发现报告','某财经媒体',"
        "DATE '2026-11-16','https://www.fxbaogao.com/detail/2','/x/2-b.pdf',900,2,'sha2',"
        "TIMESTAMP '2026-11-27 01:00:00','manual','/x/2.meta.json',TIMESTAMP '2026-11-27 01:01:00')")
    con.execute("insert into report_meta values ('AP3','某行业观察','开源证券股份有限公司',DATE '2026-11-16')")
    assert con.execute("select count(*) from v_report_fulltext_linked").fetchone()[0] == 0
    assert con.execute("select count(*) from v_report_fulltext_unlinked").fetchone()[0] == 1


def test_ingest_idempotent_and_change_log(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "1001-宁德时代-Q3.pdf", pages=3)
    write_meta(pdf)
    first = rf.ingest(root)
    assert (first["inserted"], first["unchanged"], first["failed"]) == (1, 0, 0)
    again = rf.ingest(root)
    assert (again["inserted"], again["unchanged"]) == (0, 1)
    con = common.duckdb_connect(read_only=True)
    try:
        assert con.execute("select count(*) from report_fulltext").fetchone()[0] == 1
        assert con.execute("select pdf_pages from report_fulltext").fetchone()[0] == 3
    finally:
        con.close()
    # 内容变化（页数变 4）：sha 变，留痕一行，行数不变
    make_pdf(pdf, pages=4)
    write_meta(pdf, pages=4)
    changed = rf.ingest(root)
    assert (changed["updated"], changed["changes"]) == (1, 1)
    con = common.duckdb_connect(read_only=True)
    try:
        assert con.execute("select count(*) from report_fulltext").fetchone()[0] == 1
        assert con.execute("select pdf_pages from report_fulltext").fetchone()[0] == 4
        change = con.execute(
            "select old_sha256, new_sha256, reason from report_fulltext_changes").fetchall()
        assert len(change) == 1 and change[0][0] != change[0][1]
        assert change[0][2] == "sha256_changed"
    finally:
        con.close()


def test_invalid_artifact_logs_complete_failure(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "2002-坏文件.pdf", pages=2)
    write_meta(pdf, sha256="0" * 64)
    stats = rf.ingest(root)
    assert stats["failed"] == 1 and stats["inserted"] == 0
    lines = (root / "failed.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert set(record) >= {"report_id", "url", "http_status", "reason", "path", "ts"}
    assert record["report_id"] == 2002 and "sha256 不符" in record["reason"]


def test_missing_meta_logged(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    make_pdf(root / "2026-11-16" / "3003-无meta.pdf", pages=1)
    stats = rf.ingest(root)
    assert stats["failed"] == 1
    assert "meta.json 缺失" in json.loads(
        (root / "failed.jsonl").read_text(encoding="utf-8").splitlines()[0])["reason"]


def test_zero_page_pdf_logged(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    pdf = root / "2026-11-16" / "3103-空壳.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    with open(pdf, "wb") as fh:
        PdfWriter().write(fh)
    write_meta(pdf, pages=1)
    stats = rf.ingest(root)
    assert stats["failed"] == 1 and stats["inserted"] == 0
    assert "页数为 0" in json.loads(
        (root / "failed.jsonl").read_text(encoding="utf-8").splitlines()[0])["reason"]


def test_malformed_meta_values_do_not_abort_batch(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    bad = make_pdf(root / "2026-11-16" / "3203-坏字节.pdf", pages=1)
    write_meta(bad, bytes="900.5", pages=1)
    good = make_pdf(root / "2026-11-17" / "3204-好文件.pdf", pages=1)
    write_meta(good, report_id=3204, page_date="2026-11-17", pages=1)
    stats = rf.ingest(root)
    assert stats["failed"] == 1 and stats["inserted"] == 1
    reasons = [json.loads(l)["reason"] for l in
               (root / "failed.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any("bytes 非整数" in reason for reason in reasons)


def test_non_object_meta_rejected_and_reported_by_checks(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "3303-数组meta.pdf", pages=1)
    pdf.with_suffix(".meta.json").write_text("[1, 2]", encoding="utf-8")
    stats = rf.ingest(root)
    assert stats["failed"] == 1
    assert "顶层不是对象" in json.loads(
        (root / "failed.jsonl").read_text(encoding="utf-8").splitlines()[0])["reason"]
    payload = checks.run(["C5_meta_completeness"], root)
    assert payload["blocking_fail"] == ["C5_meta_completeness"]


def test_meta_report_id_mismatch_rejected(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "7007-x.pdf", pages=1)
    write_meta(pdf, report_id=9999, pages=1)
    stats = rf.ingest(root)
    assert stats["failed"] == 1 and stats["inserted"] == 0
    reason = json.loads((root / "failed.jsonl").read_text(encoding="utf-8"))["reason"]
    assert "report_id 与文件名不一致" in reason


def test_iter_artifacts_ignores_inbox_and_other_dirs(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    make_pdf(root / "_inbox" / "raw.pdf", pages=1)
    make_pdf(root / "2026-11-16" / "4004-ok.pdf", pages=1)
    found = [p.name for p, _ in rf.iter_artifacts(root)]
    assert found == ["4004-ok.pdf"]


def test_checks_all_pass_on_good_run(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "5005-宁德时代.pdf", pages=2)
    write_meta(pdf, pages=2)
    rf.ingest(root)
    con = common.duckdb_connect()
    try:
        con.execute("insert into report_meta values ('AP9','宁德时代(300750)：Q3业绩超预期','开源证券股份有限公司',DATE '2026-11-16')")
    finally:
        con.close()
    payload = checks.run(list(checks.GATES), root)
    assert payload["blocking_fail"] == []
    stats = [g for g in payload["gates"] if g["gate"] == "C6_link_rate"][0]["detail"]
    assert stats["numerator_linked"] == 1 and stats["denominator_fulltext"] == 1
    assert "criteria" in stats and "query" in stats


def test_checks_c4_flags_incomplete_failure_line(qdb):
    ensure_meta_stub()
    root = rf.root_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "failed.jsonl").write_text(
        json.dumps({"report_id": 1, "reason": "x"}, ensure_ascii=False) + "\n", encoding="utf-8")
    payload = checks.run(["C4_failed_log_schema"], root)
    assert payload["blocking_fail"] == ["C4_failed_log_schema"]


def test_checks_c1_flags_duplicate_report_id_files(qdb):
    con = common.duckdb_connect()
    try:
        stub_meta_table(con)
        rf.apply_schema(con)
    finally:
        con.close()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "8101-a.pdf", pages=1)
    write_meta(pdf, report_id=8101, pages=1)
    rf.ingest(root)
    make_pdf(root / "2026-11-17" / "8101-标题改版.pdf", pages=1)
    payload = checks.run(["C1_rows_match_files"], root)
    assert payload["blocking_fail"] == ["C1_rows_match_files"]
    detail = payload["gates"][0]["detail"]
    assert detail["duplicate_report_ids"] == [8101]
    assert detail["pdf_files"] == 2 and detail["rows"] == 1


def test_checks_c1_detects_pdf_without_row(qdb):
    con = common.duckdb_connect()
    try:
        stub_meta_table(con)
        rf.apply_schema(con)
    finally:
        con.close()
    root = rf.root_dir()
    pdf = make_pdf(root / "2026-11-16" / "6006-未入库.pdf", pages=1)
    write_meta(pdf, report_id=6006)
    payload = checks.run(["C1_rows_match_files"], root)
    assert payload["blocking_fail"] == ["C1_rows_match_files"]
    assert payload["gates"][0]["detail"]["pdf_without_row"] == [6006]


def test_adopt_matching_scores_titles(qdb):
    item = {"report_id": "7007", "title": "开源证券:《宁德时代(300750)：Q3业绩超预期》-发现报告",
            "org": "开源证券", "url": "https://www.fxbaogao.com/detail/7007"}
    text = "宁德时代(300750) Q3业绩超预期\n开源证券研究所 2026年11月"
    assert rf.match_score(text, item) == 1.0
    other = {"report_id": "7008", "title": "完全不同的报告标题：光伏行业月度跟踪", "org": "开源证券"}
    assert rf.match_score(text, other) < 0.9
