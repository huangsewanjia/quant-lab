"""研报正文入库（WORK-623）：raw/reports_fulltext/** -> report_fulltext。

    python -m db.etl.report_fulltext schema          # 只建表/视图
    python -m db.etl.report_fulltext ingest          # 扫描落盘 PDF + meta.json 入库
    python -m db.etl.report_fulltext ingest --dry-run
    python -m db.etl.report_fulltext adopt --queue candidates.jsonl --inbox <dir>

设计要点：

* 唯一键 = 发现报告 detail 数字 id（``report_id``）。同 id 重抓覆盖不新增；
  PDF 的 sha256 变化时先写一行 ``report_fulltext_changes``，再覆盖正文行。
* 入库前逐条核对 meta.json 与磁盘：sha256、字节数、页数（pypdf 实读）三项都对上才写库；
  对不上写 ``failed.jsonl``，不进库，不静默跳过。
* 页数必须 > 0：0 页说明下载到的是错误页/空壳，按失败处理。
* 只读 ``raw/reports_fulltext``；本模块不抓网络。采集在 DESKTOP-TS1FCDR 上做
  （``scripts/fxbaogao_fulltext.py``），本机只跑写库这一步。
* 归一化口径只在 ``db/schema_report_fulltext.sql`` 的 SQL 宏里维护；本模块只做
  「PDF 字节 -> 队列条目」的手工下载配对（adopt），不复刻库内匹配口径。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from . import common

SCHEMA_SQL = common.PROJECT_ROOT / "db" / "schema_report_fulltext.sql"
SOURCE = "fxbaogao"
DEFAULT_ROOT = common.RAW_DIR / "reports_fulltext" / SOURCE

REQUIRED_META = (
    "report_id", "title", "org", "page_date", "source_url",
    "bytes", "pages", "sha256", "fetched_at", "fetch_mode",
)

_PUNCT = re.compile(r"""[（）()《》〈〉【】\[\]{}「」『』:：;；,，.。!！?？、\s\-—_·"“”‘’'/\\|+*]""")
_SITE_SUFFIX = re.compile(r"(发现报告网?|fxbaogao|报告网)$")
_CORP_SUFFIX = re.compile(r"(集团)?(股份有限公司|有限责任公司|有限公司)$")


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


def failure_record(report_id: Any, url: str, http_status: Any, reason: str, path: str) -> dict:
    """failed.jsonl 的固定字段：report_id / url / http_status / reason，缺一条都不算失败记录。"""
    return {
        "report_id": report_id,
        "url": url or "",
        "http_status": http_status if http_status is not None else 0,
        "reason": reason,
        "path": path,
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalize_text(value: str) -> str:
    """标题比较用（只服务于手工下载配对，不参与库内 report_meta 关联口径）。"""
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = _PUNCT.sub("", text)
    return _SITE_SUFFIX.sub("", text)


def normalize_title(title: str, org: str) -> str:
    text = normalize_text(title)
    for prefix in (normalize_text(org or ""), _CORP_SUFFIX.sub("", normalize_text(org or ""))):
        if prefix and text.startswith(prefix):
            return text[len(prefix):]
    return text


def iter_artifacts(root: Path) -> Iterable[tuple[Path, Path]]:
    """按 <YYYY-MM-DD>/<report_id>-<slug>.pdf 的落盘约定枚举 PDF 与同名 meta.json。"""
    date_dir = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for pdf in sorted(root.glob("*/*.pdf")):
        # 只认 <YYYY-MM-DD>/ 这一层；_inbox/ 等手工下载暂存目录不进入库流程
        if not date_dir.match(pdf.parent.name):
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

    row = {
        "report_id": int(report_id) if str(report_id).isdigit() else -1,
        "source": SOURCE,
        "title": meta.get("title") or "",
        "org_name": meta.get("org") or meta.get("org_name") or "",
        "publish_date": meta.get("page_date") or None,
        "page_url": meta.get("source_url") or meta.get("url") or "",
        "pdf_path": str(pdf),
        "pdf_bytes": bytes_on_disk,
        "pdf_pages": pages,
        "sha256": digest,
        "fetched_at": meta.get("fetched_at"),
        "fetch_mode": meta.get("fetch_mode") or "unknown",
        "meta_path": str(meta_path),
        "http_status": meta.get("http_status", 0),
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
                    (report_id, source, title, org_name, publish_date, page_url, pdf_path,
                     pdf_bytes, pdf_pages, sha256, fetched_at, fetch_mode, meta_path, ingested_at)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [row["report_id"], SOURCE, row["title"], row["org_name"],
                     row["publish_date"], row["page_url"], row["pdf_path"], row["pdf_bytes"],
                     row["pdf_pages"], row["sha256"], _ts(row["fetched_at"]), row["fetch_mode"],
                     row["meta_path"], now],
                )
                con.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001 - 单条失败回滚整条，记录后继续下一条
                con.execute("ROLLBACK")
                stats["failed"] += 1
                rec = failure_record(row["report_id"], row["page_url"], row.get("http_status", 0),
                                     f"写库失败: {type(exc).__name__}: {exc}", str(pdf))
                stats["failures"].append(rec)
                append_jsonl(root / "failed.jsonl", rec)
        if own:
            con.execute("CHECKPOINT")
    finally:
        if own:
            con.close()
    return stats


# ---------------------------------------------------------------- 手工下载配对
def load_queue(path: Path) -> list[dict]:
    """读候选清单：JSONL（candidates/fetch 的输出）或 CSV（report_id,url,title,org,page_date）。"""
    items: list[dict] = []
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        for row in csv.DictReader(text.splitlines()):
            items.append({k: (v or "").strip() for k, v in row.items()})
    else:
        for line in text.splitlines():
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _pdf_first_page_text(pdf: Path, max_chars: int = 6000) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    if not reader.pages:
        return ""
    return (reader.pages[0].extract_text() or "")[:max_chars]


def match_score(pdf_text: str, item: dict) -> float:
    text = normalize_text(pdf_text)
    if not text:
        return 0.0
    title_norm = normalize_title(item.get("title", ""), item.get("org", ""))
    if not title_norm:
        return 0.0
    forms = {title_norm, normalize_text(item.get("org", "")) + title_norm}
    if any(len(f) >= 6 and f in text for f in forms):
        return 1.0
    return max(SequenceMatcher(None, f, text[: max(len(f) * 3, 600)]).ratio() for f in forms)


def match_pdf(pdf: Path, items: list[dict], threshold: float = 0.90,
              margin: float = 0.03) -> tuple[dict | None, list[tuple[float, dict]]]:
    """把一份手工下载的 PDF 配到队列条目；分数接近（差距 < margin）判为歧义，不猜。"""
    text = _pdf_first_page_text(pdf)
    scored = sorted(((match_score(text, it), it) for it in items), key=lambda x: -x[0])
    if not scored or scored[0][0] < threshold:
        return None, scored[:3]
    if len(scored) > 1 and scored[0][0] - scored[1][0] < margin:
        return None, scored[:3]
    return scored[0][1], scored[:3]


def _slug(title: str, limit: int = 60) -> str:
    """与采集器 slugify 保持同一算法（含剥离「-发现报告」后缀），否则两条路径文件名不一致。"""
    text = re.sub(r"[-_—|]?\s*发现报告网?$", "", title or "")
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return (text[:limit].strip("-") or "report")


def adopt(queue_path: Path, inbox: Path, root: Path | None = None,
          dry_run: bool = False, move: bool = True) -> dict:
    """手工下载收编：inbox 里的 PDF -> <date>/<report_id>-<slug>.pdf + meta.json。"""
    root = Path(root) if root else root_dir()
    items = load_queue(Path(queue_path))
    stats = {"inbox": str(inbox), "seen": 0, "adopted": 0, "unmatched": 0, "details": []}
    for pdf in sorted(Path(inbox).glob("*.pdf")):
        stats["seen"] += 1
        item, top = match_pdf(pdf, items)
        if item is None:
            stats["unmatched"] += 1
            rec = {
                "file": pdf.name,
                "reason": "未能唯一匹配队列条目（阈值 0.90，领先 <0.03 视为歧义）",
                "top3": [{"score": round(s, 3), "title": it.get("title", "")} for s, it in top],
            }
            stats["details"].append(rec)
            if not dry_run:
                append_jsonl(root / "unmatched.jsonl", rec)
            continue
        report_id = str(item.get("report_id") or "").strip()
        if not report_id.isdigit():
            stats["unmatched"] += 1
            stats["details"].append({"file": pdf.name, "reason": f"队列条目 report_id 非数字: {report_id!r}"})
            continue
        page_date = (item.get("page_date") or "").strip() or dt.date.today().isoformat()
        dest = root / page_date / f"{report_id}-{_slug(item.get('title', ''))}.pdf"
        try:
            meta = {
                "channel_id": "fxbaogao_vip",
                "report_id": int(report_id),
                "title": item.get("title", ""),
                "org": item.get("org", ""),
                "page_date": page_date,
                "source_url": item.get("url", "") or item.get("page_url", ""),
                "bytes": pdf.stat().st_size,
                "pages": count_pages(pdf) if not dry_run else 0,
                "sha256": sha256_file(pdf) if not dry_run else "",
                "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "fetch_mode": "manual",
                "http_status": 200,
            }
            if dry_run:
                stats["adopted"] += 1
                stats["details"].append({"file": pdf.name, "dest": str(dest), "dry_run": True})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(pdf), dest)
            else:
                shutil.copy2(str(pdf), dest)
            dest.with_suffix(".meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响其余收编
            stats["unmatched"] += 1
            rec = {"file": pdf.name, "reason": f"收编失败: {type(exc).__name__}: {exc}"}
            stats["details"].append(rec)
            append_jsonl(root / "unmatched.jsonl", rec)
            continue
        stats["adopted"] += 1
        details = {"file": pdf.name, "dest": str(dest)}
        try:
            details["score"] = round(match_score(_pdf_first_page_text(dest), item), 3)
        except Exception:  # noqa: BLE001 - 匹配分只是记录，读不出不影响已完成的收编
            details["score"] = None
        stats["details"].append(details)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m db.etl.report_fulltext",
                                     description="研报正文入库（WORK-623）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="建表与视图")
    ing = sub.add_parser("ingest", help="扫描 raw/reports_fulltext 入库")
    ing.add_argument("--root", default=None)
    ing.add_argument("--dry-run", action="store_true")
    ad = sub.add_parser("adopt", help="把手工下载的 PDF 收编进目录并登记")
    ad.add_argument("--queue", required=True)
    ad.add_argument("--inbox", required=True)
    ad.add_argument("--root", default=None)
    ad.add_argument("--dry-run", action="store_true")
    ad.add_argument("--copy", action="store_true", help="复制而非移动 inbox 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "schema":
        with common.duckdb_connect() as con:
            apply_schema(con)
            con.execute("CHECKPOINT")
        print("[report_fulltext] schema 已应用")
        return 0
    if args.command == "ingest":
        stats = ingest(Path(args.root) if args.root else None, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in stats.items() if k != "failures"},
                         ensure_ascii=False, indent=1))
        for rec in stats["failures"]:
            print("  [fail]", rec["report_id"], rec["reason"], rec["path"], file=sys.stderr)
        return 0 if stats["failed"] == 0 else 1
    stats = adopt(Path(args.queue), Path(args.inbox),
                  Path(args.root) if args.root else None,
                  dry_run=args.dry_run, move=not args.copy)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0 if stats["unmatched"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
