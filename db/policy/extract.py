"""Policy HTML -> plain text + dual-timeline metadata."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

from bs4 import BeautifulSoup
import trafilatura

TAG_RE = re.compile(r"<[^>]+>")
DATE_CN_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
DATE_DASH_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
EFFECTIVE_CN_RE = re.compile(
    r"自\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*起\s*(?:施行|实行|执行|生效|实施)?"
)
EFFECTIVE_DASH_RE = re.compile(
    r"自\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*起\s*(?:施行|实行|执行|生效|实施)?"
)
EFFECTIVE_FROM_PUB_RE = re.compile(
    r"自\s*(?:公布|发布|印发|施行)\s*之日起\s*(?:施行|实行|执行|生效|实施)"
)
DOC_NO_RE = re.compile(
    r"(?:发文字号|文号)[：:\s]*([^\s，。；;]{2,40})"
)
DOC_NO_FALLBACK_RE = re.compile(
    r"[\u4e00-\u9fa5]{1,10}[〔\[﹝（(]\s*\d{4}\s*[〕\]﹞）)]\s*第?\s*\d+\s*号"
)
DOC_NO_SIMPLE_RE = re.compile(r"第\s*\d+\s*号")
AGENCY_RE = re.compile(r"发文机关[：:\s]*([^\n\r]{2,30})")


def normalize_text(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u3000]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_text(html: str, selectors: list[str]) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:  # noqa: BLE001 - bad selector should not kill the run
            node = None
        if node:
            text = normalize_text(node.get_text("\n", strip=True))
            if len(text) >= 80:
                return text
    try:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception:  # noqa: BLE001 - optional fallback
        extracted = None
    if extracted:
        cleaned = normalize_text(extracted)
        if len(cleaned) >= 200:
            return cleaned
    # Last structured fallback: largest text block.
    best = None
    best_len = 0
    for node in soup.find_all(["article", "div", "section", "td"]):
        text = normalize_text(node.get_text("\n", strip=True))
        if len(text) > best_len:
            best, best_len = text, len(text)
    if best and best_len >= 80:
        return best
    return normalize_text(soup.get_text("\n", strip=True))


def _parse_ymd(year: str, month: str, day: str) -> str | None:
    try:
        return dt.date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def extract_publish_date(html: str, text: str, url: str, hint: str | None = None) -> str | None:
    if hint:
        match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", hint)
        if match:
            value = _parse_ymd(*match.groups())
            if value:
                return value
    soup = BeautifulSoup(html, "lxml")
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if name in {
            "firstpublishedtime",
            "pubdate",
            "publishdate",
            "publish_date",
            "article:published_time",
            "dc.date",
            "date",
        }:
            content = meta.get("content") or ""
            match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", content)
            if match:
                value = _parse_ymd(*match.groups())
                if value:
                    return value
    for pattern in (
        r"(?:发布时间|发布日期|发文日期|成文日期|公开日期)[：:\s]*"
        r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})",
    ):
        match = re.search(pattern, text)
        if match:
            value = _parse_ymd(*match.groups())
            if value:
                return value
    match = re.search(r"t(20\d{6})(?:_|\.)", url)
    if match:
        raw = match.group(1)
        value = _parse_ymd(raw[:4], raw[4:6], raw[6:8])
        if value:
            return value
    return None


def extract_effective_date(text: str, publish_date: str | None) -> str | None:
    if EFFECTIVE_FROM_PUB_RE.search(text) and publish_date:
        return publish_date
    for pattern in (EFFECTIVE_CN_RE, EFFECTIVE_DASH_RE):
        match = pattern.search(text)
        if match:
            value = _parse_ymd(*match.groups())
            if value:
                return value
    return None


def extract_document_number(text: str) -> str | None:
    match = DOC_NO_RE.search(text)
    if match:
        return match.group(1).strip()
    match = DOC_NO_FALLBACK_RE.search(text)
    if match:
        return match.group(0).strip()
    match = DOC_NO_SIMPLE_RE.search(text)
    if match:
        return match.group(0).strip()
    return None


def extract_agency(text: str, title: str, default: str) -> str:
    match = AGENCY_RE.search(text)
    if match:
        return match.group(1).strip()
    for prefix in (
        "国务院办公厅",
        "国务院",
        "中共中央",
        "财政部",
        "中国人民银行",
        "中国证监会",
        "国家发展改革委",
        "工业和信息化部",
    ):
        if title.startswith(prefix):
            return prefix
    return default


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_document(
    *,
    html: str,
    url: str,
    title: str,
    source: dict[str, Any],
    publish_hint: str | None = None,
) -> dict[str, Any]:
    selectors = list(source.get("content_selectors") or [])
    text = extract_text(html, selectors)
    publish_date = extract_publish_date(html, text, url, publish_hint)
    effective_date = extract_effective_date(text, publish_date)
    agency = extract_agency(text, title, str(source.get("agency") or source["name"]))
    doc_no = extract_document_number(text)
    digest = text_sha256(text)
    return {
        "source_id": source["id"],
        "source_name": source["name"],
        "agency": agency,
        "title": title.strip(),
        "url": url,
        "publish_date": publish_date,
        "effective_date": effective_date,
        "document_number": doc_no,
        "sha256": digest,
        "text": text,
        "text_bytes": len(text.encode("utf-8")),
        "fetched_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
