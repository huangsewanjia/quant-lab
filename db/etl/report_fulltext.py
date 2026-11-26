"""研报正文入库（WORK-623）：raw/reports_fulltext/** -> report_fulltext。

    python -m db.etl.report_fulltext schema          # 只建表/视图（含旧表补列）
    python -m db.etl.report_fulltext ingest          # 扫描落盘 PDF + meta.json 入库
    python -m db.etl.report_fulltext ingest --dry-run

设计要点：

* 唯一键 = 发现报告 reportId（``report_id``）。同 id 重抓覆盖不新增；
  PDF 的 sha256 变化时先写一行 ``report_fulltext_changes``，再覆盖正文行。
* 入库前逐条核对 meta.json 与磁盘：sha256、字节数、页数（pypdf 实读）、report_id 与文件名一致，
  四项都对上才写库；对不上写 ``failed.jsonl``，不进库，不静默跳过。
* 每篇保留官网回查链接 ``site_url``（https://www.fxbaogao.com/view?id=<id>），meta 缺失时按 id 补齐。
* 只读 ``raw/reports_fulltext``；本模块不抓网络（采集见 ``scripts/fxbaogao_fulltext.py``，官方 API）。
* 归一化口径只在 ``db/schema_report_fulltext.sql`` 的 SQL 宏里维护。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from . import common

SCHEMA_SQL = common.PROJECT_ROOT / "db" / "schema_report_fulltext.sql"
SOURCE = "fxbaogao"
SITE_VIEW = "https://www.fxbaogao.com/view?id={report_id}"
REQUIRED_META = (
    "report_id", "title", "org", "page_date", "source_url",
    "bytes", "pages", "sha256", "fetched_at", "fetch_mode",
)
DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def root_dir() -> Path:
    """按 common.RAW_DIR 现算，便于测试替换 QUANTDB_HOME。"""
    return common.RAW_DIR / "reports_fulltext" / SOURCE


def apply_schema(con) -> None:
    con.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_pages(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def failure_record(report_id: Any, url: str, http_status: Any, reason: str, path: str,
                   code: Any = None, msg: str = "") -> dict:
    """failed.jsonl 固定字段：report_id / url / http_status / code / msg / reason。"""
    return {
        "report_id": report_id,
        "url": url or "",
        "http_status": http_status if http_status is not None else 0,
        "code": code,
        "msg": msg or "",
        "reason": reason,
        "path": path,
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def iter_artifacts(root: Path) -> Iterable[tuple[Path, Path]]:
    """按 <YYYY-MM-DD>/<report_id>-<slug>.pdf 的落盘约定枚举 PDF 与同名 meta.json。"""
    for pdf in sorted(root.glob("*/*.pdf")):
        if not DATE_DIR.match(pdf.parent.name):
            continue
        yield pdf, pdf.with_suffix(".meta.json")


def _int_field(meta: dict, key: str, problems: list[str]) -> int | None:
    """meta 数值字段容错解析：坏值记为问题并返回 None，不让整批崩掉。"""
    value = meta.get(key)
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        problems.append(f"{key} 非整数: {value!r}")
        return None


def _meta_report_id(meta: dict, pdf: Path) -> str:
    stem = pdf.stem
    match = re.match(r"^(\d+)-", stem)
    if match:
        return match.group(1)
    return str(meta.get("report_id", ""))


def validate_artifact(pdf: Path, meta_path: Path) -> tuple[dict, list[str]]:
    """返回 (规整后的库行, 问题列表)。问题非空则不写库，交给 failed.jsonl。"""
    problems: list[str] = []
    if not meta_path.exists():
        return {}, [f"meta.json 缺失: {meta_path}"]
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 文件坏了要如实报，不要跳过
        return {}, [f"meta.json 无法解析: {type(exc).__name__}: {exc}"]
    if not isinstance(meta, dict):
        return {}, [f"meta.json 顶层不是对象: {type(meta).__name__}"]
    missing = [k for k in REQUIRED_META if k not in meta or meta.get(k) in (None, "")]
    if missing:
        problems.append("meta.json 缺字段: " + ", ".join(missing))

    bytes_on_disk = pdf.stat().st_size
    digest = sha256_file(pdf)
    meta_bytes = _int_field(meta, "bytes", problems)
    if meta_bytes is not None and meta_bytes != bytes_on_disk:
        problems.append(f"字节数不符: meta={meta_bytes} 磁盘={bytes_on_disk}")
    if meta.get("sha256") and str(meta["sha256"]).lower() != digest:
        problems.append(f"sha256 不符: meta={str(meta['sha256'])[:12]} 磁盘={digest[:12]}")
    try:
        pages = count_pages(pdf)
    except Exception as exc:  # noqa: BLE001 - 坏 PDF 也要留证据
        problems.append(f"PDF 无法解析: {type(exc).__name__}: {exc}")
        pages = 0
    meta_pages = _int_field(meta, "pages", problems)
    if pages <= 0:
        problems.append("页数为 0（空壳/错误页）")
    elif meta_pages is not None and meta_pages != pages:
        problems.append(f"页数不符: meta={meta_pages} 实读={pages}")

    report_id = _meta_report_id(meta, pdf)
    if not str(report_id).isdigit():
        problems.append(f"report_id 非数字: {report_id!r}")
    elif meta.get("report_id") not in (None, "") and str(meta["report_id"]).strip() != str(report_id):
        problems.append(f"report_id 与文件名不一致: meta={meta['report_id']} 文件名={report_id}")

    site_url = meta.get("site_url") or (
        SITE_VIEW.format(report_id=report_id) if str(report_id).isdigit() else "")
    row = {
        "report_id": int(report_id) if str(report_id).isdigit() else -1,
        "source": SOURCE,
        "title": meta.get("title") or "",
        "org_name": meta.get("org") or meta.get("org_name") or "",
        "publish_date": meta.get("page_date") or None,
        "page_url": meta.get("source_url") or meta.get("url") or "",
        "site_url": site_url,
        "pdf_url": meta.get("pdf_url") or "",
        "pdf_path": str(pdf),
        "pdf_bytes": bytes_on_disk,
        "pdf_pages": pages,
        "sha256": digest,
        "fetched_at": meta.get("fetched_at"),
        "fetch_mode": meta.get("fetch_mode") or "unknown",
        "meta_path": str(meta_path),
        "http_status": meta.get("http_status", (meta.get("api") or {}).get("http_status", 0)),
        "code": meta.get("code", (meta.get("api") or {}).get("code")),
        "msg": meta.get("msg", (meta.get("api") or {}).get("msg", "")),
    }
    return row, problems


def _ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
    return parsed


def _existing(con, report_id: int) -> tuple[str, str] | None:
    row = con.execute(
        "select sha256, pdf_path from report_fulltext where report_id = ?", [report_id]
    ).fetchone()
    return (row[0], row[1]) if row else None


def ingest(root: Path | None = None, dry_run: bool = False, con=None) -> dict:
    """扫描落盘 PDF 入库；返回统计。任何一条校验不过只记录，不中断整批。"""
    root = Path(root) if root else root_dir()
    stats = {
        "root": str(root), "scanned": 0, "inserted": 0, "updated": 0,
        "unchanged": 0, "failed": 0, "changes": 0, "failures": [],
    }
    own = con is None
    if own:
        con = common.duckdb_connect()
    try:
        apply_schema(con)
        for pdf, meta_path in iter_artifacts(root):
            stats["scanned"] += 1
            try:
                row, problems = validate_artifact(pdf, meta_path)
            except Exception as exc:  # noqa: BLE001 - 校验自身异常也要落账，不能中断整批
                row, problems = {}, [f"校验异常: {type(exc).__name__}: {exc}"]
            if problems:
                stats["failed"] += 1
                rec = failure_record(
                    row.get("report_id") if row else None,
                    row.get("page_url", "") if row else "",
                    row.get("http_status", 0) if row else 0,
                    "; ".join(problems), str(pdf),
                    row.get("code") if row else None, row.get("msg", "") if row else "",
                )
                stats["failures"].append(rec)
                if not dry_run:
                    append_jsonl(root / "failed.jsonl", rec)
                continue
            if dry_run:
                continue
            try:
                con.execute("BEGIN TRANSACTION")
                prev = _existing(con, row["report_id"])
                now = dt.datetime.now()
                if prev is None:
                    stats["inserted"] += 1
                elif prev[0] == row["sha256"]:
                    stats["unchanged"] += 1
                else:
                    stats["updated"] += 1
                    stats["changes"] += 1
                    con.execute(
                        "insert or replace into report_fulltext_changes values (?,?,?,?,?,?,?)",
                        [row["report_id"], now, prev[0], row["sha256"], prev[1],
                         row["pdf_path"], "sha256_changed"],
                    )
                con.execute(
                    """
                    insert or replace into report_fulltext
                    (report_id, source, title, org_name, publish_date, page_url, site_url, pdf_url,
                     pdf_path, pdf_bytes, pdf_pages, sha256, fetched_at, fetch_mode, meta_path, ingested_at)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [row["report_id"], SOURCE, row["title"], row["org_name"],
                     row["publish_date"], row["page_url"], row["site_url"], row["pdf_url"],
                     row["pdf_path"], row["pdf_bytes"], row["pdf_pages"], row["sha256"],
                     _ts(row["fetched_at"]), row["fetch_mode"], row["meta_path"], now],
                )
                con.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001 - 单条失败回滚整条，记录后继续下一条
                con.execute("ROLLBACK")
                stats["failed"] += 1
                rec = failure_record(row["report_id"], row["page_url"], row.get("http_status", 0),
                                     f"写库失败: {type(exc).__name__}: {exc}", str(pdf),
                                     row.get("code"), row.get("msg", ""))
                stats["failures"].append(rec)
                append_jsonl(root / "failed.jsonl", rec)
        if own:
            con.execute("CHECKPOINT")
    finally:
        if own:
            con.close()
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m db.etl.report_fulltext",
                                     description="研报正文入库（WORK-623）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="建表与视图（含旧表补列）")
    ing = sub.add_parser("ingest", help="扫描 raw/reports_fulltext 入库")
    ing.add_argument("--root", default=None)
    ing.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "schema":
        with common.duckdb_connect() as con:
            apply_schema(con)
            con.execute("CHECKPOINT")
        print("[report_fulltext] schema 已应用")
        return 0
    stats = ingest(Path(args.root) if args.root else None, dry_run=args.dry_run)
    print(json.dumps({k: v for k, v in stats.items() if k != "failures"},
                     ensure_ascii=False, indent=1))
    for rec in stats["failures"]:
        print("  [fail]", rec["report_id"], rec["reason"], rec["path"], file=sys.stderr)
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
