"""Whitelisted policy collector: list -> fetch -> extract -> store.

Design constraints (WORK-515):
  * whitelist is YAML configuration (`policy/sources.yaml`), no site-wide crawl
  * incremental by publication day, resumable via `--resume`
  * one failing source never stops the others; each request retries twice
  * every stored document keeps publish_date and effective_date separately;
    a missing effective date is stored as the literal `unknown`
  * document number / text sha256 dedupe, reprints recorded with reprint_of

Example:
  python policy/collect.py --backfill --source all
  python policy/collect.py --since 2026-11-01 --source gov_gw --resume
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from pypdf import PdfReader

from extract import build_document, clean_title, make_doc_id, now_utc
from tagging import tag_document

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "sources.yaml"
DEFAULT_ROOT = Path(os.environ.get("QUANTDB_POLICY_DIR", "/srv/share/quantdb/policy"))
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 quantlab-policy/1.0"
)
DATE_HINT_RE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")
GENERIC_DOCNO_RE = re.compile(r"^第\d+号$")


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


class RateLimiter:
    """Global token bucket; kept simple because total volume is ~20k pages."""

    def __init__(self, rpm: float) -> None:
        self.interval = 60.0 / max(rpm, 1.0)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        delay = at - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class Fetcher:
    def __init__(self, rpm: float, retries: int, timeout: int) -> None:
        self.retries = retries
        self.timeout = timeout
        self.limiter = RateLimiter(rpm)
        self.local = threading.local()

    @property
    def session(self) -> requests.Session:
        if not hasattr(self.local, "session"):
            session = requests.Session()
            session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            self.local.session = session
        return self.local.session

    def get(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                resp.raise_for_status()
                if (resp.encoding or "").lower() in {"iso-8859-1", "ascii"}:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            except Exception as exc:  # noqa: BLE001 - network error is expected
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed: {type(last).__name__}: {last}")

    def get_bytes(self, url: str, max_bytes: int = 30 * 1024 * 1024) -> bytes:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=max(self.timeout, 60), stream=True)
                resp.raise_for_status()
                chunks = []
                size = 0
                for chunk in resp.iter_content(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError(f"PDF exceeds {max_bytes} bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET bytes {url} failed: {type(last).__name__}: {last}")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    defaults = payload.get("defaults") or {}
    sources = payload.get("sources") or []
    merged = []
    for source in sources:
        item = {**defaults, **source}
        merged.append(item)
    return {"defaults": defaults, "sources": merged}


def canonical_url(host_url: str, href: str, base: str) -> str:
    url = urljoin(host_url or base, href.strip())
    url = url.split("#")[0]
    return url


def parse_hint(text: str) -> str | None:
    match = DATE_HINT_RE.search(text or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def _row_hint(anchor) -> str | None:
    node = anchor
    for _ in range(3):
        node = node.parent
        if node is None:
            break
        hint = parse_hint(node.get_text(" ", strip=True))
        if hint:
            return hint
    return None


def _title_from_anchor(anchor) -> str:
    title = anchor.get("title") or anchor.get_text(" ", strip=True)
    title = re.sub(r"\s+", " ", title).strip()
    # drop a trailing date cell that HTML lists often include in the same <a>
    title = re.sub(r"\s*\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?$", "", title)
    return clean_title(title)


def list_gov_api(source: dict[str, Any], fetcher: Fetcher, since: str, max_pages: int) -> list[dict]:
    conf = source["list"]
    aliases = source.get("agency_aliases") or []
    items: list[dict] = []
    seen: set[str] = set()
    page = 1
    total_pages = max_pages
    while page <= min(total_pages, max_pages):
        url = (
            "https://sousuo.www.gov.cn/search-gov/data"
            f"?t={conf['collection']}&q=&timetype=timeqb"
            f"&mintime={since}&maxtime={date.today().isoformat()}"
            "&sort=pubtime&sortType=1&searchfield=title"
            f"&p={page}&n={conf.get('page_size', 50)}"
        )
        try:
            payload = json.loads(fetcher.get(url))
        except Exception as exc:  # noqa: BLE001
            if page == 1:
                raise
            log(f"[warn] {source['id']} page {page} unreadable: {exc}")
            break
        search = payload.get("searchVO") or {}
        if page == 1:
            total_pages = int(search.get("totalpage") or 0)
            log(f"[list] {source['id']} total={search.get('totalCount')} pages={total_pages}")
        rows = search.get("listVO") or []
        if not rows:
            break
        for row in rows:
            url_ = str(row.get("url") or "").strip()
            title = clean_title(str(row.get("title") or ""))
            if not url_ or len(title) < 6 or url_ in seen:
                continue
            seen.add(url_)
            published = parse_hint(str(row.get("pubtimeStr") or ""))
            agency = (row.get("puborg") or "").strip()
            if aliases and not any(alias in agency for alias in aliases):
                continue
            items.append(
                {
                    "url": url_,
                    "title": title,
                    "publish_hint": published,
                    "agency": agency or None,
                    "document_number": (row.get("pcode") or "").strip() or None,
                    "expiry_date": parse_hint(str(row.get("shixiao") or "")),
                    "source_column": (row.get("childtype") or "").replace("\\", "/"),
                }
            )
        page += 1
    return items


def list_html(source: dict[str, Any], fetcher: Fetcher, since: str, max_pages: int) -> list[dict]:
    conf = source["list"]
    link_re = re.compile(conf["link_regex"])
    exclude_re = re.compile(conf["exclude_regex"]) if conf.get("exclude_regex") else None
    include_title = re.compile(conf["include_title_regex"]) if conf.get("include_title_regex") else None
    exclude_title = re.compile(conf["exclude_title_regex"]) if conf.get("exclude_title_regex") else None
    items: list[dict] = []
    seen: set[str] = set()
    for section in conf.get("sections") or []:
        pages = min(int(section.get("max_pages") or conf.get("max_pages") or 10), max_pages)
        for page in range(1, pages + 1):
            # TRS-style sites: index.html is page 1, index_1.html is page 2.
            url = section["url"] if page == 1 else section["page_url"].format(page=page - 1)
            try:
                html = fetcher.get(url)
            except Exception as exc:  # noqa: BLE001
                if page == 1:
                    log(f"[warn] {source['id']}/{section['name']} list unreachable: {exc}")
                    break
                log(f"[warn] {source['id']}/{section['name']} page {page} unreachable: {exc}")
                break
            soup = BeautifulSoup(html, "lxml")
            found = 0
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"].strip()
                if not link_re.search(href):
                    continue
                if exclude_re and exclude_re.search(href):
                    continue
                url_ = canonical_url(url, href, section["url"])
                if url_ in seen:
                    continue
                title = _title_from_anchor(anchor)
                if len(title) < 6:
                    continue
                if include_title and not include_title.search(title):
                    continue
                if exclude_title and exclude_title.search(title):
                    continue
                seen.add(url_)
                found += 1
                items.append(
                    {
                        "url": url_,
                        "title": title,
                        "publish_hint": _row_hint(anchor),
                        "source_column": section["name"],
                    }
                )
            log(f"[list] {source['id']}/{section['name']} p{page}: +{found}")
            if found == 0 and page > 1:
                break
    return items


def list_source(source: dict[str, Any], fetcher: Fetcher, since: str, max_pages: int) -> list[dict]:
    kind = source["list"]["type"]
    if kind == "gov_api":
        return list_gov_api(source, fetcher, since, max_pages)
    if kind == "html":
        return list_html(source, fetcher, since, max_pages)
    raise ValueError(f"unknown list type: {kind}")


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class Store:
    root: Path
    source: dict[str, Any]
    seen_sha: dict[str, str] = field(default_factory=dict)
    seen_docno: dict[str, str] = field(default_factory=dict)
    seen_url: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "listed": 0,
            "stored": 0,
            "dup_url": 0,
            "dup_sha": 0,
            "dup_docno": 0,
            "rejected": 0,
            "errors": 0,
        }
    )
    reject_log: Path = Path()
    dup_log: Path = Path()
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.reject_log = self.root / "meta" / "rejects.jsonl"
        self.dup_log = self.root / "meta" / "duplicates.jsonl"
        for path in (self.reject_log, self.dup_log):
            path.parent.mkdir(parents=True, exist_ok=True)
        # Index existing docs so a resumed run does not duplicate text.
        for meta_path in self.root.glob("raw/*/*/*.meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            self.seen_sha[meta["sha256"]] = meta["doc_id"]
            self.seen_url.add(meta["url"])
            if is_strong_docno(meta.get("document_number")):
                self.seen_docno[normalize_docno(meta["document_number"])] = meta["doc_id"]

    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def store(self, doc: dict[str, Any]) -> str:
        title = doc["title"]
        url = doc["url"]
        if not doc["publish_date"]:
            self._reject(doc, "缺 publish_date")
            return "rejected"
        if len(doc["text"]) < 400:
            self._reject(doc, "正文过短(可能仅有PDF/OFD附件)")
            return "rejected"
        doc_id = make_doc_id(self.source["id"], url)
        with self.lock:
            if url in self.seen_url or doc_id in self.seen_url:
                self.counts["dup_url"] += 1
                return "dup_url"
            if doc["sha256"] in self.seen_sha:
                self._duplicate(doc, self.seen_sha[doc["sha256"]], "sha256")
                return "dup_sha"
            docno = doc.get("document_number")
            if is_strong_docno(docno) and normalize_docno(docno) in self.seen_docno:
                self._duplicate(doc, self.seen_docno[normalize_docno(docno)], "document_number")
                return "dup_docno"
            self.seen_url.add(url)
            self.seen_sha[doc["sha256"]] = doc_id
            if is_strong_docno(docno):
                self.seen_docno[normalize_docno(docno)] = doc_id
                self.counts["stored"] += 1
            else:
                self.counts["stored"] += 1

        year = doc["publish_date"][:4] if doc["publish_date"] else "unknown"
        out_dir = self.root / "raw" / self.source["id"] / year
        out_dir.mkdir(parents=True, exist_ok=True)
        text_path = out_dir / f"{doc_id}.txt"
        html_path = out_dir / f"{doc_id}.html.gz"
        meta_path = out_dir / f"{doc_id}.meta.json"

        tags = tag_document(doc["title"], doc["text"], doc.get("agency") or "")
        effective = doc.get("effective_date") or "unknown"
        meta = {
            "doc_id": doc_id,
            "title": doc["title"],
            "agency": doc.get("agency") or "unknown",
            "document_number": doc.get("document_number") or "unknown",
            "docno_source": doc.get("docno_source") or "",
            "publish_date": doc["publish_date"],
            "effective_date": effective,
            "expiry_date": doc.get("expiry_date") or "unknown",
            "industry_tags": tags["industry_tags"],
            "topic_tags": tags["topic_tags"],
            "tag_basis": tags["tag_basis"],
            "url": doc["url"],
            "fetched_at": doc["fetched_at"],
            "sha256": doc["sha256"],
            "text_bytes": doc["text_bytes"],
            "text_chars": doc["text_chars"],
            "source_column": doc.get("source_column") or "",
            "text_source": doc.get("text_source") or "html",
            "source_id": self.source["id"],
            "source_name": self.source.get("name") or self.source["id"],
            "reprint_count": 0,
            "text_path": rel(text_path, self.root),
            "html_gz_path": rel(html_path, self.root),
        }
        text_path.write_text(doc["text"], encoding="utf-8")
        with gzip.open(html_path, "wt", encoding="utf-8") as fh:
            fh.write(doc["html"])
        atomic_write_json(meta_path, meta)
        return "stored"

    def reject(self, item: dict[str, Any], reason: str, detail: str = "") -> None:
        with self.lock:
            self.counts["rejected"] += 1
            self._append(
                self.reject_log,
                {
                    "source": self.source["id"],
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "reason": reason,
                    "detail": detail,
                    "ts": now_utc(),
                },
            )

    def _reject(self, doc: dict[str, Any], reason: str) -> None:
        self.reject(doc, reason)

    def _duplicate(self, doc: dict[str, Any], canonical: str, how: str) -> None:
        self.counts["dup_sha" if how == "sha256" else "dup_docno"] += 1
        self._append(
            self.dup_log,
            {
                "source": self.source["id"],
                "title": doc["title"],
                "url": doc["url"],
                "duplicate_of": canonical,
                "matched": how,
                "ts": now_utc(),
            },
        )
        self._bump_reprint(canonical)

    def _bump_reprint(self, canonical: str) -> None:
        for path in self.root.glob(f"raw/*/*/{canonical}.meta.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            meta["reprint_count"] = int(meta.get("reprint_count") or 0) + 1
            atomic_write_json(path, meta)
            return


def normalize_docno(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def is_strong_docno(value: str | None) -> bool:
    value = normalize_docno(value)
    if len(value) < 6 or value == "unknown":
        return False
    return not GENERIC_DOCNO_RE.match(value)


def state_path(root: Path, source_id: str) -> Path:
    return root / "meta" / "state" / f"{source_id}.json"


def _pdf_links(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.lower().split("?")[0].endswith(".pdf"):
            continue
        url = urljoin(page_url, href).split("#")[0]
        if url not in links:
            links.append(url)
    return links


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 60) -> str:
    from io import BytesIO

    reader = PdfReader(BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages[:max_pages]:
        raw = page.extract_text() or ""
        # PDF lines are visual line breaks; Chinese text should be re-joined.
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        pages.append("".join(lines))
    text = "\n".join(pages)
    text = re.sub(r"第\s*\d+\s*页\s*(?:共\s*\d+\s*页)?", "", text)
    return text.strip()


def load_state(root: Path, source_id: str) -> dict[str, Any]:
    path = state_path(root, source_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_state(root: Path, source_id: str, payload: dict[str, Any]) -> None:
    path = state_path(root, source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)


def process_item(item: dict[str, Any], source: dict[str, Any], fetcher: Fetcher, store: Store) -> str:
    # A resumed/backfill rerun must not re-download documents already stored.
    if item["url"] in store.seen_url:
        store.counts["dup_url"] += 1
        return "dup_url"
    try:
        html = fetcher.get(item["url"])
    except Exception as exc:  # noqa: BLE001
        store.counts["errors"] += 1
        store.reject(item, "fetch_failed", str(exc)[:300])
        return "error"
    try:
        doc = build_document(
            html=html,
            url=item["url"],
            title=item["title"],
            source=source,
            publish_hint=item.get("publish_hint"),
            meta=item,
        )
        doc["html"] = html
        doc["text_source"] = "html"
        if len(doc["text"]) < 400:
            # Many NDRC/MOF/gov.cn department notices keep only the cover page
            # in HTML and put the full text in a PDF attachment. Extract the
            # text but never store the PDF itself.
            for pdf_url in _pdf_links(html, item["url"])[:2]:
                try:
                    pdf_text = extract_pdf_text(fetcher.get_bytes(pdf_url))
                except Exception as exc:  # noqa: BLE001
                    log(f"[warn] PDF extract failed {pdf_url}: {exc}")
                    continue
                if len(pdf_text) >= 200:
                    doc["text"] = doc["text"] + "\n\n" + pdf_text
                    doc["sha256"] = __import__("hashlib").sha256(
                        doc["text"].encode("utf-8")
                    ).hexdigest()
                    doc["text_bytes"] = len(doc["text"].encode("utf-8"))
                    doc["text_chars"] = len(doc["text"])
                    doc["text_source"] = "html+pdf"
                    break
    except Exception as exc:  # noqa: BLE001
        store.counts["errors"] += 1
        store.reject(item, "extract_failed", f"{type(exc).__name__}: {exc}"[:300])
        return "error"
    return store.store(doc)


def run_source(
    source: dict[str, Any],
    root: Path,
    fetcher: Fetcher,
    *,
    since: str,
    backfill: bool,
    resume: bool,
    max_pages: int,
    limit: int | None,
    list_only: bool,
) -> dict[str, Any]:
    state = load_state(root, source["id"]) if resume else {}
    pending = state.get("pending") or []
    if pending:
        log(f"[resume] {source['id']} pending={len(pending)}")
        items = pending
    else:
        try:
            items = list_source(source, fetcher, since, max_pages)
        except Exception as exc:  # noqa: BLE001 - isolate per source
            log(f"[fail] {source['id']} listing: {type(exc).__name__}: {exc}")
            return {
                "source": source["id"],
                "status": "list_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "counts": {},
            }
    # The gov.cn API ignores mintime/maxtime in practice, so the date window is
    # enforced here for backfill runs as well.
    items = [
        item
        for item in items
        if not since or not item.get("publish_hint") or item["publish_hint"] >= since
    ]
    if limit:
        items = items[:limit]
    log(f"[plan] {source['id']} items={len(items)} since={since} backfill={backfill}")
    if list_only:
        for item in items[:10]:
            log(f"  {item.get('publish_hint') or '??????????'} {item['title'][:60]}")
        return {"source": source["id"], "status": "listed", "items": len(items), "counts": {}}

    store = Store(root=root, source=source)
    store.counts["listed"] = len(items)
    workers = int(source.get("workers") or 3)
    done = 0
    last_flush = time.monotonic()
    pending_queue = list(items)
    done_ids: set[int] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_item, item, source, fetcher, store): item for item in pending_queue}
        for future in as_completed(futures):
            item = futures[future]
            try:
                outcome = future.result()
            except Exception as exc:  # noqa: BLE001
                outcome = "error"
                store.counts["errors"] += 1
                store.reject(item, "worker_crashed", f"{type(exc).__name__}: {exc}"[:300])
            done_ids.add(id(item))
            done += 1
            if outcome in {"stored", "dup_sha", "dup_docno"}:
                log(f"[{outcome}] {item['title'][:46]}")
            if done % 50 == 0 or time.monotonic() - last_flush > 20:
                pending_queue = [row for row in items if id(row) not in done_ids]
                latest = max(
                    (it.get("publish_hint") or "" for it in pending_queue), default=""
                )
                save_state(
                    root,
                    source["id"],
                    {
                        "source": source["id"],
                        "updated_at": now_utc(),
                        "pending": pending_queue,
                        "last_batch_publish_date": latest,
                        "counts": store.counts,
                        "since": since,
                    },
                )
                last_flush = time.monotonic()
    dates = [item.get("publish_hint") for item in items if item.get("publish_hint")]
    save_state(
        root,
        source["id"],
        {
            "source": source["id"],
            "updated_at": now_utc(),
            "pending": [],
            "last_publish_date": max(dates) if dates else None,
            "counts": store.counts,
            "since": since,
        },
    )
    log(f"[done] {source['id']} {store.counts}")
    return {"source": source["id"], "status": "ok", "items": len(items), "counts": store.counts}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python policy/collect.py",
        description="白名单政策原文采集（近 10 年，双时间轴）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", default="all", help="源 id 或 all")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="数据根目录")
    parser.add_argument("--since", default=None, help="增量起始日 YYYY-MM-DD")
    parser.add_argument("--backfill", action="store_true", help="从 2016-01-01 全量回填")
    parser.add_argument("--resume", action="store_true", help="从断点队列续跑")
    parser.add_argument("--max-pages", type=int, default=1000, help="每栏目最多列表页")
    parser.add_argument("--limit", type=int, default=None, help="每源最多处理条数（调试）")
    parser.add_argument("--workers", type=int, default=None, help="覆盖并发数")
    parser.add_argument("--rpm", type=float, default=None, help="覆盖全局限速")
    parser.add_argument("--list-only", action="store_true", help="只列举，不抓正文")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    defaults = config["defaults"]
    sources = config["sources"]
    if args.source != "all":
        sources = [src for src in sources if src["id"] == args.source]
        if not sources:
            raise SystemExit(f"未知政策源: {args.source}")
    if args.workers:
        for src in sources:
            src["workers"] = args.workers
    since = args.since or defaults.get("since") or "2016-01-01"
    if args.backfill:
        since = "2016-01-01"
    fetcher = Fetcher(
        rpm=args.rpm or float(defaults.get("rpm") or 180),
        retries=int(defaults.get("retries") or 2),
        timeout=int(defaults.get("timeout") or 25),
    )
    args.root.mkdir(parents=True, exist_ok=True)
    results = []
    for source in sources:
        source_since = since
        if not args.backfill and not args.since:
            state = load_state(args.root, source["id"]) if args.resume else {}
            last = state.get("last_publish_date")
            if last:
                source_since = (date.fromisoformat(last) - timedelta(days=3)).isoformat()
        try:
            results.append(
                run_source(
                    source,
                    args.root,
                    fetcher,
                    since=source_since,
                    backfill=args.backfill,
                    resume=args.resume,
                    max_pages=args.max_pages,
                    limit=args.limit,
                    list_only=args.list_only,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one source must not kill the rest
            log(f"[fail] {source['id']}: {type(exc).__name__}: {exc}")
            results.append({"source": source["id"], "status": "crashed", "error": str(exc)})
    run_file = args.root / "meta" / "runs" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    run_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        run_file,
        {
            "ts": now_utc(),
            "since": since,
            "backfill": args.backfill,
            "results": results,
            "total_stored": sum(r.get("counts", {}).get("stored", 0) for r in results),
            "total_listed": sum(r.get("items", 0) for r in results),
        },
    )
    print(json.dumps({"results": results, "run_file": str(run_file)}, ensure_ascii=False, indent=2))
    failed = [r for r in results if r["status"] != "ok" and not args.list_only]
    if failed and len(failed) == len(results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
