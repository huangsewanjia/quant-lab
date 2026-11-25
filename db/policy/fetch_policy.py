"""Whitelisted policy collector.

Stores any document whose text was extracted. A missing effective_date is
recorded as the string ``unknown`` -- never back-filled with publish_date --
and the URL is also appended to policy/missing/YYYYMMDD.jsonl so a later run
can supply the effective date. Documents without publish_date or with fewer
than 80 text bytes are rejected, not stored.

Examples:
  python -m db.policy.fetch_policy --source all --max-items 2
  python -m db.policy.fetch_policy --source gov --list-only
  python -m db.policy.fetch_policy --backfill-missing 20261124 20261125
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..etl import common
from .extract import build_document

SOURCES_TOML = Path(__file__).with_name("sources.toml")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 quantdb-policy/0.1"
)


class PolicyError(RuntimeError):
    pass


def load_sources() -> list[dict[str, Any]]:
    with SOURCES_TOML.open("rb") as fh:
        payload = tomllib.load(fh)
    return list(payload.get("source") or [])


class Fetcher:
    def __init__(self, rpm: float = 60.0, retries: int = 2) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        self.limiter = common.RateLimiter(rpm)
        self.retries = retries

    def get(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(1, self.retries + 2):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=25)
                resp.raise_for_status()
                if not resp.encoding or resp.encoding.lower() in {"iso-8859-1", "ascii"}:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt <= self.retries:
                    time.sleep(2 ** (attempt - 1))
        raise PolicyError(f"GET {url} 失败: {type(last).__name__}: {last}")


def allowed(source: dict[str, Any], url: str) -> bool:
    host = urlparse(url).netloc.lower()
    base_host = urlparse(source.get("base_url", "")).netloc.lower()
    if not host:
        return False
    if host == base_host:
        return True
    # Government sites sometimes use a document subdomain (e.g. gks.mof.gov.cn).
    base_parts = base_host.split(".")
    if len(base_parts) >= 2:
        root = ".".join(base_parts[-2:])
        return host.endswith("." + root) or host == root
    return False


def _hint_from_row(anchor) -> str | None:
    """Prefer an unambiguous date; avoid partial title dates like 2026年1-10."""
    patterns = (
        re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
        re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
    )
    node = anchor
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        for pattern in patterns:
            for match in pattern.finditer(text):
                try:
                    dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                except ValueError:
                    continue
                return match.group(0)
    return None


def list_html(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    html = fetcher.get(source["list_url"])
    soup = BeautifulSoup(html, "lxml")
    pattern = re.compile(source["link_regex"])
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not pattern.search(href):
            continue
        url = urljoin(source["base_url"], href)
        if url in seen or not allowed(source, url):
            continue
        title = (anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        if len(title) < 6:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "publish_hint": _hint_from_row(anchor)})
    return items


def list_json(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    text = fetcher.get(source["list_url"])
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("list") or []
    items: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return items
    for row in payload:
        if not isinstance(row, dict):
            continue
        title = str(row.get("TITLE") or row.get("title") or "").strip()
        url = str(row.get("URL") or row.get("url") or "").strip()
        if not title or not url:
            continue
        url = urljoin(source["base_url"], url)
        if not allowed(source, url):
            continue
        hint = str(row.get("DOCRELPUBTIME") or row.get("publish_time") or "")
        items.append({"title": title, "url": url, "publish_hint": hint or None})
    return items


def _walk_miit(node: Any, source: dict[str, Any], out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        url = node.get("url")
        title = node.get("title") or node.get("title_text")
        if isinstance(url, str) and isinstance(title, str) and url and title:
            if "/visit/link" not in url and not url.startswith("javascript"):
                full = urljoin(source["base_url"], url)
                if allowed(source, full):
                    deploy = node.get("deploytime")
                    hint = None
                    if deploy:
                        try:
                            hint = dt.datetime.fromtimestamp(int(deploy) / 1000).strftime("%Y-%m-%d")
                        except (ValueError, OSError, OverflowError):
                            hint = None
                    out.append({"title": title.strip(), "url": full, "publish_hint": hint})
        for value in node.values():
            _walk_miit(value, source, out)
    elif isinstance(node, list):
        for value in node:
            _walk_miit(value, source, out)


def list_miit(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    text = fetcher.get(source["list_url"])
    payload = json.loads(text)
    out: list[dict[str, Any]] = []
    _walk_miit(payload, source, out)
    # Deduplicate by URL, keep first.
    seen: set[str] = set()
    unique = []
    for item in out:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    return unique


def list_items(source: dict[str, Any], fetcher: Fetcher) -> list[dict[str, Any]]:
    kind = source.get("list_type")
    if kind == "json":
        return list_json(source, fetcher)
    if kind == "html":
        return list_html(source, fetcher)
    if kind == "miit_api":
        return list_miit(source, fetcher)
    raise PolicyError(f"未知 list_type: {kind}")


def load_index() -> dict[str, Any]:
    path = common.POLICY_DIR / "index.json"
    if path.exists():
        try:
            payload = common.read_json(path)
            if isinstance(payload, dict) and isinstance(payload.get("documents"), list):
                payload.setdefault("alias_urls", [])
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    # Rebuild from per-document meta.json when the index is absent/corrupt.
    documents = []
    for meta_path in common.POLICY_DIR.glob("*/*/meta.json"):
        try:
            documents.append(common.read_json(meta_path))
        except (OSError, json.JSONDecodeError):
            continue
    return {"generated_at": common.utc_now(), "documents": documents, "alias_urls": []}


def _known_urls(index: dict[str, Any]) -> set[str]:
    urls = {str(meta.get("url")) for meta in index.get("documents", [])}
    urls.update(str(url) for url in index.get("alias_urls", []) if url)
    return urls


def append_missing(record: dict[str, Any]) -> None:
    path = common.POLICY_DIR / "missing" / f"{common.today_tag()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def store_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Persist text and meta. effective_date unknown is materialised as ``unknown``."""
    doc = dict(doc)
    if not doc.get("effective_date"):
        doc["effective_date"] = "unknown"
    key = doc["sha256"][:16]
    folder = common.POLICY_DIR / doc["source_id"] / key
    folder.mkdir(parents=True, exist_ok=True)
    text_path = folder / "content.txt"
    meta_path = folder / "meta.json"
    text_path.write_text(doc["text"] + "\n", encoding="utf-8")
    meta = {key_: value for key_, value in doc.items() if key_ != "text"}
    meta["content_path"] = str(text_path.relative_to(common.QDB_HOME))
    meta["meta_path"] = str(meta_path.relative_to(common.QDB_HOME))
    common.write_json_atomic(meta_path, meta)
    return meta


def run_source(
    source: dict[str, Any],
    fetcher: Fetcher,
    *,
    max_items: int,
    list_only: bool,
    index: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "source": source["id"],
        "listed": 0,
        "stored": 0,
        "pending_effective": 0,
        "missing": 0,
        "duplicates": 0,
        "known": 0,
        "remaining": 0,
        "errors": [],
    }
    try:
        items = list_items(source, fetcher)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"列表失败: {type(exc).__name__}: {exc}")
        print(f"[fail] {source['id']} 列表: {exc}", file=sys.stderr)
        return result
    result["listed"] = len(items)
    print(f"[list] {source['id']}: {len(items)} items")
    if list_only:
        for item in items[:max_items]:
            print(f"  {item.get('publish_hint') or '':12s} {item['title'][:70]} {item['url']}")
        return result

    by_sha = {str(meta.get("sha256")) for meta in index["documents"]}
    by_url = _known_urls(index)
    processed = 0
    for item in items:
        url = item["url"]
        if url in by_url:
            # Known URLs are skipped before the max-items budget is spent, so
            # a daily run reaches genuinely new items deeper in the list.
            result["known"] += 1
            continue
        if processed >= max_items:
            break
        processed += 1
        _store_item(item, source, fetcher, index, by_sha, by_url, result)
    result["remaining"] = max(0, len(items) - result["known"] - processed)
    return result


def _store_item(
    item: dict[str, Any],
    source: dict[str, Any],
    fetcher: Fetcher,
    index: dict[str, Any],
    by_sha: set[str],
    by_url: set[str],
    result: dict[str, Any],
) -> str:
    """Fetch, validate and store one list item. Returns a status string."""
    url = item["url"]
    title = item["title"]
    try:
        html = fetcher.get(url)
        doc = build_document(
            html=html,
            url=url,
            title=title,
            source=source,
            publish_hint=item.get("publish_hint"),
        )
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"{url}: {type(exc).__name__}: {exc}")
        append_missing(
            {
                "source": source["id"],
                "title": title,
                "url": url,
                "reason": f"抓取/抽取失败: {type(exc).__name__}: {exc}",
                "fetched_at": common.utc_now(),
            }
        )
        result["missing"] += 1
        print(f"[fail] {title[:50]}: {exc}", file=sys.stderr)
        return "error"

    if doc["sha256"] in by_sha:
        result["duplicates"] += 1
        # Same text under another URL: remember the URL so a bounded
        # reconciliation does not spend its budget on it every week.
        aliases = index.setdefault("alias_urls", [])
        if url not in aliases:
            aliases.append(url)
        by_url.add(url)
        print(f"[dup-text] {title[:50]}")
        return "duplicate"
    reasons = []
    if not doc["publish_date"]:
        reasons.append("缺 publish_date")
    if doc["text_bytes"] < 80:
        reasons.append("正文过短")
    if reasons:
        result["missing"] += 1
        append_missing(
            {
                "source": source["id"],
                "title": title,
                "url": url,
                "publish_date": doc["publish_date"],
                "effective_date": doc["effective_date"],
                "reason": "；".join(reasons),
                "fetched_at": common.utc_now(),
            }
        )
        print(f"[missing] {title[:50]}: {'；'.join(reasons)}")
        return "missing"

    meta = store_document(doc)
    index["documents"].append(meta)
    by_sha.add(doc["sha256"])
    by_url.add(url)
    result["stored"] += 1
    if meta["effective_date"] == "unknown":
        # Stored now, effective date still to be supplied: keep the URL on the
        # missing list with an explicit reason so the follow-up queue stays complete.
        result["pending_effective"] += 1
        append_missing(
            {
                "source": source["id"],
                "title": title,
                "url": url,
                "publish_date": doc["publish_date"],
                "effective_date": None,
                "reason": "缺 effective_date（已入库，待补生效日）",
                "doc_id": meta["sha256"][:16],
                "content_path": meta["content_path"],
                "fetched_at": common.utc_now(),
            }
        )
        print(
            f"[store-unknown] {source['id']} {doc['publish_date']}..unknown "
            f"{title[:50]}"
        )
    else:
        print(
            f"[store] {source['id']} {doc['publish_date']}..{meta['effective_date']} "
            f"{title[:50]}"
        )
    return "stored"


def reconcile(
    sources: list[dict[str, Any]],
    fetcher: Fetcher,
    index: dict[str, Any],
    *,
    max_fetch: int,
) -> dict[str, Any]:
    """Compare list-page counts with the index and backfill missing items.

    The run is intentionally bounded: a first reconciliation can have more
    than a thousand historical items, and fetching them all in one weekly run
    would exceed the job window. ``remaining`` records what is left.
    """
    by_sha = {str(meta.get("sha256")) for meta in index["documents"]}
    by_url = _known_urls(index)
    budget = max(0, max_fetch)
    results: list[dict[str, Any]] = []
    for source in sources:
        entry = {
            "source": source["id"],
            "listed": 0,
            "in_index": 0,
            "missing": 0,
            "fetched": 0,
            "stored": 0,
            "pending_effective": 0,
            "duplicates": 0,
            "errors": [],
            "remaining": 0,
        }
        try:
            items = list_items(source, fetcher)
        except Exception as exc:  # noqa: BLE001
            entry["errors"].append(f"列表失败: {type(exc).__name__}: {exc}")
            print(f"[fail] {source['id']} 列表: {exc}", file=sys.stderr)
            results.append(entry)
            continue
        entry["listed"] = len(items)
        missing = [item for item in items if item["url"] not in by_url]
        entry["in_index"] = len(items) - len(missing)
        entry["missing"] = len(missing)
        for item in missing:
            if budget <= 0:
                break
            budget -= 1
            status = _store_item(item, source, fetcher, index, by_sha, by_url, entry)
            if status in ("stored", "missing", "duplicate"):
                entry["fetched"] += 1
        entry["remaining"] = max(0, entry["missing"] - entry["fetched"])
        results.append(entry)
    summary = {
        "ts": common.utc_now(),
        "max_fetch": max_fetch,
        "sources": results,
        "total_listed": sum(r["listed"] for r in results),
        "total_in_index": sum(r["in_index"] for r in results),
        "total_missing": sum(r["missing"] for r in results),
        "total_fetched": sum(r["fetched"] for r in results),
        "total_remaining": sum(r["remaining"] for r in results),
    }
    path = common.POLICY_DIR / "meta" / f"reconcile-{dt.date.today():%Y%m%d}.json"
    common.write_json_atomic(path, summary)
    print(
        "[reconcile] listed={total_listed} in_index={total_in_index} "
        "missing={total_missing} fetched={total_fetched} "
        "remaining={total_remaining} report={path}".format(path=path, **summary)
    )
    return summary


def backfill_missing(
    sources: list[dict[str, Any]],
    fetcher: Fetcher,
    index: dict[str, Any],
    dates: list[str],
    *,
    max_fetch: int | None = None,
) -> dict[str, Any]:
    """Re-fetch URLs recorded in policy/missing/<date>.jsonl and store them.

    Records are deduplicated by URL across the requested dates; URLs already in
    the index are skipped. Fetch failures are reported, never retried here.
    """
    by_sha = {str(meta.get("sha256")) for meta in index["documents"]}
    by_url = _known_urls(index)
    source_map = {str(src["id"]): src for src in sources}
    summary: dict[str, Any] = {
        "ts": common.utc_now(),
        "dates": dates,
        "total_records": 0,
        "total_unique_urls": 0,
        "total_known": 0,
        "total_stored": 0,
        "total_pending_effective": 0,
        "total_duplicates": 0,
        "total_failed": 0,
        "total_remaining": 0,
        "failures": [],
    }
    seen: set[str] = set()
    queue: list[dict[str, Any]] = []
    for date in dates:
        path = common.POLICY_DIR / "missing" / f"{date}.jsonl"
        if not path.exists():
            summary["failures"].append(f"{date}: 清单不存在 {path}")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            summary["total_records"] += 1
            url = str(record.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            queue.append(record)
    summary["total_unique_urls"] = len(queue)

    processed = 0
    for record in queue:
        if max_fetch is not None and processed >= max_fetch:
            break
        url = str(record["url"])
        if url in by_url:
            summary["total_known"] += 1
            continue
        source = source_map.get(str(record.get("source")))
        if source is None:
            summary["total_failed"] += 1
            summary["failures"].append(f"{url}: 未知来源 {record.get('source')!r}")
            continue
        processed += 1
        counter: dict[str, Any] = {
            "stored": 0,
            "pending_effective": 0,
            "missing": 0,
            "duplicates": 0,
            "errors": [],
        }
        status = _store_item(
            {
                "title": str(record.get("title") or url),
                "url": url,
                "publish_hint": record.get("publish_date"),
            },
            source,
            fetcher,
            index,
            by_sha,
            by_url,
            counter,
        )
        summary["total_stored"] += counter["stored"]
        summary["total_pending_effective"] += counter["pending_effective"]
        summary["total_duplicates"] += counter["duplicates"]
        if status in ("missing", "error"):
            summary["total_failed"] += 1
            detail = counter["errors"][0] if counter["errors"] else status
            summary["failures"].append(f"{url}: {detail}")

    accounted = (
        summary["total_known"]
        + summary["total_stored"]
        + summary["total_duplicates"]
        + summary["total_failed"]
    )
    summary["total_remaining"] = max(0, summary["total_unique_urls"] - accounted)
    path = common.POLICY_DIR / "meta" / f"backfill-{'-'.join(dates)}.json"
    common.write_json_atomic(path, summary)
    print(
        "[backfill] records={total_records} unique={total_unique_urls} "
        "known={total_known} stored={total_stored} "
        "pending_effective={total_pending_effective} "
        "duplicates={total_duplicates} failed={total_failed} "
        "remaining={total_remaining} report={path}".format(path=path, **summary)
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.policy.fetch_policy",
        description="六源白名单政策采集；缺生效日记 unknown 并登记待补",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", default="all", help="源 id 或 all")
    parser.add_argument("--max-items", type=int, default=2, help="每源最多处理条目")
    parser.add_argument("--rpm", type=float, default=60.0, help="请求速率")
    parser.add_argument("--list-only", action="store_true", help="只列标题，不抓正文")
    parser.add_argument(
        "--backfill-missing",
        nargs="+",
        metavar="YYYYMMDD",
        default=None,
        help="回填 policy/missing/<date>.jsonl 中正文仍可取的条目",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="对账：源站列表条数 vs 库内，补抓缺失项",
    )
    parser.add_argument(
        "--max-fetch",
        type=int,
        default=200,
        help="对账模式单轮最多抓取的缺失条目（跨源总预算）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common.ensure_layout()
    sources = load_sources()
    if args.source != "all":
        sources = [src for src in sources if src["id"] == args.source]
        if not sources:
            raise SystemExit(f"未知政策源: {args.source}")
    fetcher = Fetcher(rpm=args.rpm)
    index = load_index()
    if args.backfill_missing:
        if args.reconcile or args.list_only:
            print("--backfill-missing 不能与 --reconcile/--list-only 同用", file=sys.stderr)
            return 2
        summary = backfill_missing(
            sources,
            fetcher,
            index,
            args.backfill_missing,
            max_fetch=args.max_fetch or None,
        )
        index["generated_at"] = common.utc_now()
        common.write_json_atomic(common.POLICY_DIR / "index.json", index)
        common.write_json_atomic(
            common.POLICY_DIR / "last-backfill.json",
            summary,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["total_failed"] else 0
    if args.reconcile:
        if args.list_only:
            print("--reconcile 与 --list-only 不能同用", file=sys.stderr)
            return 2
        summary = reconcile(sources, fetcher, index, max_fetch=args.max_fetch)
        index["generated_at"] = common.utc_now()
        common.write_json_atomic(common.POLICY_DIR / "index.json", index)
        common.write_json_atomic(
            common.POLICY_DIR / "last-reconcile.json",
            summary,
        )
        if summary["total_listed"] == 0:
            print("[fail] 全部政策源列表不可达", file=sys.stderr)
            return 2
        if any(entry.get("errors") for entry in summary["sources"]):
            print("[fail] 政策对账存在源级或条目级错误", file=sys.stderr)
            return 1
        return 0
    results = []
    for source in sources:
        results.append(
            run_source(
                source,
                fetcher,
                max_items=args.max_items,
                list_only=args.list_only,
                index=index,
            )
        )
    index["generated_at"] = common.utc_now()
    common.write_json_atomic(common.POLICY_DIR / "index.json", index)
    summary = {
        "sources": results,
        "total_listed": sum(r["listed"] for r in results),
        "total_stored": sum(r["stored"] for r in results),
        "total_pending_effective": sum(r["pending_effective"] for r in results),
        "total_missing": sum(r["missing"] for r in results),
        "total_duplicates": sum(r["duplicates"] for r in results),
    }
    common.write_json_atomic(
        common.POLICY_DIR / "last-run.json",
        {"ts": common.utc_now(), **summary},
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["total_listed"] == 0 and not args.list_only:
        print("[fail] 全部政策源列表不可达", file=sys.stderr)
        return 2
    if any(result.get("errors") for result in results):
        print("[fail] 政策抓取存在源级或条目级错误", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
