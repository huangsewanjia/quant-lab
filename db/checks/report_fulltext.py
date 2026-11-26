"""研报正文质量闸（WORK-623）。

    python -m db.checks.report_fulltext all
    python -m db.checks.report_fulltext C1_rows_match_files
    python -m db.checks.report_fulltext all --out /srv/share/quantdb/meta/checks/report_fulltext.json

判据（对应 WORK-623 验收标准）：

  C1_rows_match_files     表行数 == 落盘 PDF 篇数（逐篇列差集，不只看总数）
  C2_pdf_pages_positive   每行 pdf_pages > 0（pypdf 实读时也是）
  C3_sha_matches_disk     每行 sha256 与磁盘 PDF 现算一致；文件缺失直接 FAIL
  C4_failed_log_schema    failed.jsonl 每行都带 report_id / url / http_status / reason
  C5_meta_completeness    每篇 PDF 的同名 meta.json 字段齐、report_id 与文件名一致
  C6_link_rate            与 report_meta 的关联率（分子/分母/口径三项都打印；信息项，不判 FAIL）
  C7_change_log_integrity 内容变更留痕：old/new sha 不同，最新一条 new_sha 等于表内 sha

只读打开 DuckDB；不写主库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ..etl import common
from ..etl import report_fulltext as rf


def report_dir() -> Path:
    return common.META_DIR / "checks"


def _table_rows(con) -> list[dict]:
    cols = ["report_id", "title", "org_name", "publish_date", "pdf_path", "pdf_bytes",
            "pdf_pages", "sha256", "fetch_mode"]
    rows = con.execute(
        "select report_id, title, org_name, publish_date, pdf_path, pdf_bytes, pdf_pages, sha256, fetch_mode "
        "from report_fulltext order by report_id"
    ).fetchall()
    return [dict(zip(cols, row)) for row in rows]


def _disk_index(root: Path) -> tuple[dict[int, list[Path]], list[int]]:
    """report_id -> 磁盘上的所有 PDF（同 id 多份要能被发现，不能互相覆盖）。"""
    index: dict[int, list[Path]] = {}
    for pdf, _meta in rf.iter_artifacts(root):
        try:
            rid = int(pdf.name.split("-", 1)[0])
        except ValueError:
            continue
        index.setdefault(rid, []).append(pdf)
    duplicates = sorted(rid for rid, paths in index.items() if len(paths) > 1)
    return index, duplicates


def _disk_pdfs(root: Path) -> dict[int, Path]:
    index, _dup = _disk_index(root)
    return {rid: paths[0] for rid, paths in index.items()}


def _result(name: str, ok: bool, detail: dict) -> dict:
    return {"gate": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def c1_rows_match_files(con, root: Path, **_) -> dict:
    rows = _table_rows(con)
    index, duplicates = _disk_index(root)
    db_ids = {r["report_id"] for r in rows}
    missing = sorted(set(index) - db_ids)
    orphan_rows = sorted(db_ids - set(index))
    pdf_files = sum(len(paths) for paths in index.values())
    ok = len(rows) == pdf_files and not missing and not orphan_rows and not duplicates
    return _result("C1_rows_match_files", ok, {
        "rows": len(rows), "pdf_files": pdf_files, "unique_report_ids_on_disk": len(index),
        "duplicate_report_ids": duplicates[:20],
        "pdf_without_row": missing[:20], "row_without_pdf": orphan_rows[:20],
        "hint": ("row_without_pdf 表示库里有行但文件被删/移；pdf_without_row 表示未入库（先跑 ingest，再查 failed.jsonl）；"
                 "duplicate_report_ids 表示同一 report_id 有多份文件（会话重抓换名后未清理）"),
    })


def c2_pdf_pages_positive(con, root: Path, **_) -> dict:
    rows = _table_rows(con)
    bad = [r["report_id"] for r in rows if not r["pdf_pages"] or r["pdf_pages"] <= 0]
    return _result("C2_pdf_pages_positive", not bad,
                   {"rows": len(rows), "bad_report_ids": bad[:20]})


def c3_sha_matches_disk(con, root: Path, **_) -> dict:
    mismatch, missing = [], []
    for row in _table_rows(con):
        path = Path(row["pdf_path"])
        if not path.exists():
            missing.append(row["report_id"])
            continue
        if rf.sha256_file(path) != row["sha256"]:
            mismatch.append(row["report_id"])
    return _result("C3_sha_matches_disk", not mismatch and not missing, {
        "mismatch": mismatch[:20], "missing": missing[:20],
        "hint": "sha 不一致说明文件被替换/修复过，先重跑 ingest 让表内 sha 与内容变更留痕对齐",
    })


def c4_failed_log_schema(con, root: Path, **_) -> dict:
    path = root / "failed.jsonl"
    if not path.exists():
        return _result("C4_failed_log_schema", True, {"file": str(path), "lines": 0, "bad_lines": []})
    bad, total = [], 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            bad.append({"line": lineno, "problem": "空行"})
            continue
        total += 1
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            bad.append({"line": lineno, "problem": f"JSON 解析失败: {exc}"})
            continue
        miss = [k for k in ("report_id", "url", "http_status", "reason") if k not in rec]
        if miss:
            bad.append({"line": lineno, "problem": "缺字段: " + ",".join(miss)})
    return _result("C4_failed_log_schema", not bad,
                   {"file": str(path), "lines": total, "bad_lines": bad[:20]})


def c5_meta_completeness(con, root: Path, **_) -> dict:
    problems = []
    for pdf, meta_path in rf.iter_artifacts(root):
        row, issues = rf.validate_artifact(pdf, meta_path)
        for issue in issues:
            problems.append({"pdf": str(pdf), "issue": issue})
    return _result("C5_meta_completeness", not problems,
                   {"pdf_files": len(_disk_pdfs(root)), "problems": problems[:20]})


def link_rate(con) -> dict:
    total = con.execute("select count(*) from report_fulltext").fetchone()[0]
    linked = con.execute("select count(*) from v_report_fulltext_linked").fetchone()[0]
    multi = con.execute(
        "select count(*) from v_report_fulltext_linked where candidate_count > 1"
    ).fetchone()[0]
    return {
        "numerator_linked": linked,
        "denominator_fulltext": total,
        "rate": round(linked / total, 4) if total else None,
        "ambiguous_multi_candidate": multi,
        "criteria": ("标题归一相等（去站点后缀、去标点、去开头机构名）+ 机构归一相等（去公司后缀）"
                     "+ 发布日相差 ≤3 天；一篇正文多条候选时取日差最小、info_code 最小"),
        "query": "select count(*) from v_report_fulltext_linked  /  select count(*) from report_fulltext",
    }


def c6_link_rate(con, root: Path, **_) -> dict:
    stats = link_rate(con)
    return {"gate": "C6_link_rate", "status": "INFO", "detail": stats}


def c7_change_log_integrity(con, root: Path, **_) -> dict:
    rows = con.execute(
        "select report_id, changed_at, old_sha256, new_sha256 from report_fulltext_changes "
        "order by report_id, changed_at"
    ).fetchall()
    bad = []
    for report_id, _ts, old, new in rows:
        if old == new:
            bad.append({"report_id": report_id, "problem": "old_sha256 == new_sha256"})
    latest = con.execute(
        "select c.report_id, c.new_sha256, f.sha256 from report_fulltext_changes c "
        "join report_fulltext f using (report_id) qualify row_number() over "
        "(partition by c.report_id order by c.changed_at desc) = 1"
    ).fetchall()
    for report_id, new, sha in latest:
        if new != sha:
            bad.append({"report_id": report_id, "problem": "最新留痕与表内 sha 不一致"})
    return _result("C7_change_log_integrity", not bad,
                   {"change_rows": len(rows), "problems": bad[:20]})


GATES: dict[str, Callable[..., dict]] = {
    "C1_rows_match_files": c1_rows_match_files,
    "C2_pdf_pages_positive": c2_pdf_pages_positive,
    "C3_sha_matches_disk": c3_sha_matches_disk,
    "C4_failed_log_schema": c4_failed_log_schema,
    "C5_meta_completeness": c5_meta_completeness,
    "C6_link_rate": c6_link_rate,
    "C7_change_log_integrity": c7_change_log_integrity,
}


def run(names: list[str], root: Path | None = None) -> dict:
    root = Path(root) if root else rf.root_dir()
    con = common.duckdb_connect(read_only=True)
    try:
        results = [GATES[name](con, root) for name in names]
    finally:
        con.close()
    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "db": str(common.DB_PATH),
        "root": str(root),
        "gates": results,
        "blocking_fail": [r["gate"] for r in results if r["status"] == "FAIL"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m db.checks.report_fulltext")
    parser.add_argument("gate", nargs="?", default="all",
                        help="all 或 C1..C7 中的一个")
    parser.add_argument("--root", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    names = list(GATES) if args.gate == "all" else [args.gate]
    for name in names:
        if name not in GATES:
            parser.error(f"未知闸门 {name}；可选 {', '.join(GATES)}")
    payload = run(names, Path(args.root) if args.root else None)
    for item in payload["gates"]:
        detail = json.dumps(item["detail"], ensure_ascii=False)[:400]
        print(f"{item['status']:4s} {item['gate']:26s} {detail}", flush=True)
    out = Path(args.out) if args.out else report_dir() / "report_fulltext.json"
    common.write_json_atomic(out, payload)
    print(f"[checks] 报告: {out}")
    return 1 if payload["blocking_fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
