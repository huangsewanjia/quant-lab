"""Policy HTML -> plain text + dual-timeline metadata.

Only the official whitelisted sources are supported. The extractor never
guesses an effective date: when a document does not state one, the caller
stores the literal string ``unknown``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

from bs4 import BeautifulSoup

CN_DATE = r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?"
DATE_CN_RE = re.compile(CN_DATE)

EFFECTIVE_PATTERNS = (
    # 自2026年1月1日起施行 / 自 2026-01-01 起实施
    re.compile(r"自\s*" + CN_DATE + r"\s*起\s*(?:施行|实施|执行|生效)"),
    re.compile(r"于\s*" + CN_DATE + r"\s*起\s*(?:施行|实施|执行|生效)"),
    # 本办法自2026年1月1日起执行（中间可能夹字）
    re.compile(r"自\s*" + CN_DATE + r"\s*起[^。；\n]{0,12}(?:施行|实施|执行|生效)"),
    # 施行日期：2026年1月1日
    re.compile(r"(?:施行日期|生效日期|实施日期)[：:\s]*" + CN_DATE),
)
EFFECTIVE_FROM_PUB_RE = re.compile(
    r"自\s*(?:公布|发布|印发|颁布|下发)\s*之日起\s*(?:施行|实施|执行|生效)"
)
EXPIRY_PATTERNS = (
    re.compile(r"有效期(?:限)?至\s*" + CN_DATE),
    re.compile(r"失效日期[：:\s]*" + CN_DATE),
    re.compile(r"自\s*" + CN_DATE + r"\s*起\s*废止"),
)
DOC_NO_RE = re.compile(
    r"(?m)^\s*([\u4e00-\u9fa5]{1,12}[〔\[﹝（(]\s*\d{4}\s*[〕\]﹞）)]\s*第?\s*\d+\s*号)\s*$"
)
DOC_NO_ALT_RE = re.compile(r"((?:国务院|中华人民共和国)\s*第\s*\d+\s*号)")
DOC_NO_GENERIC_RE = re.compile(r"第\s*\d+\s*号")
LABELED_DOCNO_RE = re.compile(
    r"(?:发文字号|文号)[：:\s]*"
    r"([\u4e00-\u9fa5]{0,14}[〔\[﹝（(]\s*\d{4}\s*[〕\]﹞）)]\s*第?\s*\d+\s*号)"
)
AGENCY_RE = re.compile(r"发文机关[：:\s]*([^\n\r]{2,40})")
PUB_DATE_LABELED_RE = re.compile(
    r"(?:发布时间|发布日期|发文日期|成文日期|公开日期|印发日期)[：:\s]*(" + CN_DATE + r")"
)

TITLE_SUFFIX_RE = re.compile(
    r"\s*(?:[|｜]\s*(?:中国政府网|国家发展和改革委员会|财政部|国家能源局|自然资源部|"
    r"中国人民银行|中国证监会)[^\n]*)$"
)
TITLE_DOCNO_RE = re.compile(r"(\d{4}\s*年第\s*\d+\s*号令?)\s*$")


def now_utc() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_text(text: str) -> str:
    """Keep paragraph breaks, drop indentation and blank-line runs."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u3000\xa0]+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    title = TITLE_SUFFIX_RE.sub("", title)
    return title.strip(" 　")


def split_title_docno(title: str) -> tuple[str, str | None]:
    """NDRC titles carry the order number: '《办法》 2026年第49号令'."""
    match = TITLE_DOCNO_RE.search(title or "")
    if not match:
        return title, None
    return clean_title(title[: match.start()]), re.sub(r"\s+", "", match.group(1))


def extract_text(html: str, selectors: list[str]) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(
        ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe"]
    ):
        tag.decompose()
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:  # noqa: BLE001 - a bad selector must not kill the run
            node = None
        if node is None:
            continue
        text = normalize_text(node.get_text("\n", strip=True))
        if len(text) >= 80:
            return text
    # Fallback: largest text block that is not the whole page.
    best, best_len = "", 0
    for node in soup.find_all(["article", "div", "section", "td"]):
        text = normalize_text(node.get_text("\n", strip=True))
        if len(text) > best_len:
            best, best_len = text, len(text)
    if best_len >= 80:
        return best
    return normalize_text(soup.get_text("\n", strip=True))


def _iso(year: str, month: str, day: str) -> str | None:
    try:
        return dt.date(int(year), int(month), int(day)).isoformat()
    except (TypeError, ValueError):
        return None


def _first_date(match: re.Match[str], offset: int = 1) -> str | None:
    return _iso(match.group(offset), match.group(offset + 1), match.group(offset + 2))


def extract_publish_date(
    html: str, text: str, url: str, hint: str | None = None
) -> str | None:
    if hint:
        match = DATE_CN_RE.search(hint)
        if match:
            value = _first_date(match)
            if value:
                return value
    soup = BeautifulSoup(html, "lxml")
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        if name not in {
            "firstpublishedtime",
            "pubdate",
            "publishdate",
            "publish_date",
            "article:published_time",
            "dc.date",
            "date",
            "weibo:article:create_at",
        }:
            continue
        match = DATE_CN_RE.search(meta.get("content") or "")
        if match and (value := _first_date(match)):
            return value
    match = PUB_DATE_LABELED_RE.search(text)
    if match and (value := _first_date(match)):
        return value
    # NDRC/MOF/MNR style: .../202611/t20261110_123.html
    match = re.search(r"(?:/|t)(20\d{2})(\d{2})(\d{2})(?:_|\.|/)", url)
    if match and (value := _iso(*match.groups())):
        return value
    return None


def extract_effective_date(
    text: str, publish_date: str | None, labeled_only: bool = False
) -> str | None:
    head = text[:6000]
    if labeled_only:
        match = EFFECTIVE_PATTERNS[3].search(head)
        return _first_date(match) if match else None
    if EFFECTIVE_FROM_PUB_RE.search(head) and publish_date:
        return publish_date
    for pattern in EFFECTIVE_PATTERNS:
        match = pattern.search(head)
        if match and (value := _first_date(match)):
            return value
    return None


def extract_expiry_date(text: str) -> str | None:
    head = text[:6000]
    for pattern in EXPIRY_PATTERNS:
        match = pattern.search(head)
        if match and (value := _first_date(match)):
            return value
    return None


def extract_document_number(text: str) -> str | None:
    labeled = LABELED_DOCNO_RE.search(text[:8000])
    if labeled:
        return re.sub(r"\s+", "", labeled.group(1))
    # Own document numbers normally sit on their own line near the top; a
    # mid-sentence number is usually a reference to another document.
    head = text[:1200]
    for pattern in (DOC_NO_RE, DOC_NO_ALT_RE, DOC_NO_GENERIC_RE):
        match = pattern.search(head)
        if match:
            return re.sub(r"\s+", "", match.group(1) if match.groups() else match.group(0))
    return None


def docno_from_title(title: str) -> str | None:
    match = re.match(
        r"\s*([\u4e00-\u9fa5\s、]{2,60}?令)\s*([〔\[（(]\s*\d{4}\s*[〕\]）)]\s*第?\s*\d+\s*号)",
        title,
    )
    if match:
        return re.sub(r"\s+", "", match.group(1) + match.group(2))
    # NDRC notices append the number in parentheses: （发改价格〔2026〕630号）
    match = re.search(
        r"[（(]\s*([\u4e00-\u9fa5]{1,12}[〔\[]\s*\d{4}\s*[〕\]]\s*第?\s*\d+\s*号)\s*[）)]\s*$",
        title,
    )
    if match:
        return re.sub(r"\s+", "", match.group(1))
    match = re.search(r"(\d{4})\s*年第\s*(\d+)\s*号(?:公告)?\s*$", title)
    if match:
        return f"{match.group(1)}年第{match.group(2)}号"
    return None


def extract_agency(text: str, title: str, default: str) -> str:
    match = AGENCY_RE.search(text[:2000])
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
        "国家能源局",
        "国家药监局",
        "国家卫生健康委",
        "国家医保局",
        "自然资源部",
    ):
        if title.startswith(prefix):
            return prefix
    return default


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_doc_id(source_id: str, url: str) -> str:
    return hashlib.sha256(f"{source_id}\n{url}".encode("utf-8")).hexdigest()[:16]


def build_document(
    *,
    html: str,
    url: str,
    title: str,
    source: dict[str, Any],
    publish_hint: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = meta or {}
    text = extract_text(html, list(source.get("content_selectors") or []))
    raw_title = meta.get("title") or title
    title, title_docno = split_title_docno(clean_title(raw_title))
    publish_date = meta.get("publish_date") or extract_publish_date(
        html, text, url, publish_hint or meta.get("publish_hint")
    )
    full_text = ""
    try:
        full_text = normalize_text(BeautifulSoup(html, "lxml").get_text("\n", strip=True))[:8000]
    except Exception:  # noqa: BLE001 - fallback is optional
        pass
    effective_date = extract_effective_date(text, publish_date)
    if not effective_date:
        # Only trust an explicit 实施日期 label outside the article body; a
        # quoted older regulation must not supply this document's date.
        effective_date = extract_effective_date(full_text, publish_date, labeled_only=True)
    agency = meta.get("agency") or extract_agency(
        text, title, str(source.get("agency") or source.get("name") or "")
    )
    if meta.get("document_number"):
        doc_no, docno_source = meta["document_number"], "meta"
    elif title_docno:
        doc_no, docno_source = title_docno, "title"
    elif docno_from_title(title):
        doc_no, docno_source = docno_from_title(title), "title"
    elif extract_document_number(text):
        doc_no, docno_source = extract_document_number(text), "text"
    elif extract_document_number(full_text):
        doc_no, docno_source = extract_document_number(full_text), "text"
    else:
        doc_no, docno_source = None, None
    expiry = meta.get("expiry_date") or extract_expiry_date(text)
    return {
        "source_id": source["id"],
        "source_name": source.get("name") or source["id"],
        "source_column": meta.get("source_column") or "",
        "agency": agency,
        "title": title,
        "url": url,
        "publish_date": publish_date,
        "effective_date": effective_date,
        "expiry_date": expiry,
        "document_number": doc_no,
        "docno_source": docno_source,
        "sha256": text_sha256(text),
        "text": text,
        "text_bytes": len(text.encode("utf-8")),
        "text_chars": len(text),
        "fetched_at": now_utc(),
    }
