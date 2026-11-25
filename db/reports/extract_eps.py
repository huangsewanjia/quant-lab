#!/usr/bin/env python3
"""Extract three-year EPS forecasts from stored Eastmoney report bodies.

Rule first, deterministic fallback second, never an LLM guess.  Every value
keeps the source line (``raw_text``) and its character ``char_offset`` inside
the normalised body text, so any number in ``report_eps_extracted`` can be
re-checked against the archived HTML without re-fetching.

Pipeline::

    body.html.gz  ->  html_to_lines()  ->  text (offset preserving)
                  ->  extract_eps()    ->  eps.json per report
                  ->  build            ->  DuckDB report_eps_extracted

Rules, in priority order:

1. ``table_eps_row`` (confidence ``rule``): a ``<tr>`` whose first cell is
   ``EPS`` / ``每股收益`` and whose header cells carry fiscal years.
2. ``text_range`` (confidence ``rule``): an explicit
   ``2023-2025年EPS分别为1.2/1.5/1.8元`` template with a year range and the
   same number of values.
3. ``text_single_year`` (confidence ``nlp``): ``2023年EPS为1.2元`` single-year
   mentions, used only when rules 1-2 produced nothing for that year.

Commands::

    python db/reports/extract_eps.py extract --worklist <path>
    python db/reports/extract_eps.py build
    python db/reports/extract_eps.py stats
    python db/reports/extract_eps.py sample --docs 30
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html as html_mod
import json
import os
import re
import sys
import traceback
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct ``python db/reports/extract_eps.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.reports.fetch_detail import DetailPaths, load_worklist  # noqa: E402
from db.reports.fetch_em import Paths, read_json, write_json_atomic  # noqa: E402

EXTRACT_VERSION = "2026-11-25-p1-extract"
FORBIDDEN_TABLES = ("report_meta", "consensus_eps", "rating_change")
BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre", "figcaption",
    "caption", "dd", "dt", "dl", "hr",
}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
EPS_LABEL_RE = re.compile(r"(每股收益|\beps\b|摊薄每股收益|基本每股收益)", re.IGNORECASE)
YEAR_CELL_RE = re.compile(r"^\s*(20\d{2})\s*([EAea])?\s*年?\s*$")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
EPS_PLAUSIBLE_MAX = 1000.0


# ---------------------------------------------------------------------------
# HTML -> offset preserving text lines
# ---------------------------------------------------------------------------


class _TextBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._skip_depth = 0

    # -- helpers ---------------------------------------------------------
    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.lines.append(text)

    def _end_cell(self) -> None:
        if self._row is None or self._cell is None:
            return
        cell = re.sub(r"\s+", " ", "".join(self._cell)).strip()
        self._row.append(cell)
        self._cell = None

    def _end_row(self) -> None:
        if self._row is None:
            return
        self._end_cell()
        cells = [c for c in self._row]
        self._row = None
        if any(cells):
            self.lines.append("| " + " | ".join(cells) + " |")

    # -- parser hooks ----------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "tr":
            self._end_row()
            self._flush()
            self._row = []
        elif tag in ("td", "th"):
            self._end_cell()
            if self._row is None:
                self._row = []
            self._cell = []
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in ("td", "th"):
            self._end_cell()
        elif tag == "tr":
            self._end_row()
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._end_row()
        self._flush()


BODY_ANCHORS = ('id="ctx-content"', "id='ctx-content'", 'class="ctx-content"')


def slice_body_html(html: str) -> tuple[str, str]:
    """Cut the report body out of the eastmoney page shell.

    The detail page wraps the report text in ``<div id="ctx-content">`` and
    surrounds it with navigation, "latest reports" tables and a consensus
    widget.  Extracting from the whole document would let those unrelated
    tables leak EPS-looking numbers, so the body is sliced first and the
    scope is recorded next to every extracted value.
    """
    start = -1
    for anchor in BODY_ANCHORS:
        idx = html.find(anchor)
        if idx != -1:
            start = html.rfind("<div", 0, idx)
            if start != -1:
                break
    if start == -1:
        return html, "whole-document"
    depth = 0
    pos = start
    while True:
        nxt_open = html.find("<div", pos + 1)
        nxt_close = html.find("</div", pos + 1)
        if nxt_close == -1:
            return html, "whole-document"
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open
        else:
            if depth == 0:
                end = html.find(">", nxt_close)
                return html[start : end + 1], "ctx-content"
            depth -= 1
            pos = nxt_close


def html_to_text(html: str) -> str:
    builder = _TextBuilder()
    builder.feed(html)
    builder.close()
    return "\n".join(builder.lines)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpsValue:
    fiscal_year: int
    eps: float
    source: str
    raw_text: str
    char_offset: int
    confidence: str
    extractor: str


def _plausible(value: float) -> bool:
    return abs(value) <= EPS_PLAUSIBLE_MAX


def _parse_numbers(text: str) -> list[float]:
    out: list[float] = []
    for match in NUM_RE.finditer(text):
        try:
            out.append(float(match.group(0)))
        except ValueError:
            continue
    return out


def expected_years(item: dict[str, Any]) -> set[int]:
    years = {item.get("fiscal_year_this"), item.get("fiscal_year_next"), item.get("fiscal_year_two")}
    return {int(y) for y in years if y}


def extract_from_table(text: str, years: set[int]) -> list[EpsValue]:
    lines = text.split("\n")
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    found: list[EpsValue] = []
    block: list[tuple[int, list[str]]] = []

    def flush_block() -> None:
        if len(block) < 2:
            block.clear()
            return
        header_at = -1
        year_by_col: dict[int, int] = {}
        for position, (_row_idx, header_cells) in enumerate(block):
            candidate: dict[int, int] = {}
            for col, cell in enumerate(header_cells):
                match = YEAR_CELL_RE.match(cell)
                if match:
                    candidate[col] = int(match.group(1))
            if len(candidate) >= 2:
                header_at = position
                year_by_col = candidate
                break
        if year_by_col:
            for row_idx, cells in block[header_at + 1:]:
                if not cells:
                    continue
                if not EPS_LABEL_RE.search(cells[0]):
                    continue
                for col, year in year_by_col.items():
                    if col >= len(cells):
                        continue
                    numbers = NUM_RE.findall(cells[col])
                    if len(numbers) != 1:
                        continue
                    try:
                        value = float(numbers[0])
                    except ValueError:
                        continue
                    if year not in years or not _plausible(value):
                        continue
                    found.append(
                        EpsValue(
                            fiscal_year=year,
                            eps=value,
                            source="body_table",
                            raw_text=lines[row_idx],
                            char_offset=offsets[row_idx],
                            confidence="rule",
                            extractor="table_eps_row",
                        )
                    )
        block.clear()

    for idx, line in enumerate(lines):
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            block.append((idx, cells))
        else:
            flush_block()
    flush_block()
    return found


_NUM = r"-?\d+(?:\.\d+)?"
_TAIL = r"(?:\s*[（(][^）)]{0,20}[）)])?"  # "11.2(-4.2)" / "11.2（-4.2）" — revision note
_SEP = r"(?:\s*元)?\s*[、,，/\\／和及与]\s*"
_VALUES = rf"{_NUM}{_TAIL}(?:{_SEP}{_NUM}{_TAIL}){{1,5}}"

# "EPS分别为69.57、81.81和96.47元" — the year anchor is looked up separately
# in the same sentence, because sell-side prose often puts it several clauses
# before the numbers ("预计2024-2026年营收…，EPS分别为…").
EPS_VALUES_RE = re.compile(
    r"(?:EPS|每股收益)[^。；;\n]{0,16}?(?:分别为|分别|为|达到|预计为|约|达)?\s*(" + _VALUES + r")"
    r"\s*(?:元(?:/股)?|(?=[。，,；;]))",
    re.IGNORECASE,
)
YEAR_RANGE_RE = re.compile(r"(20\d{2})\s*年?\s*[-~～至—–]\s*(20\d{2})")
SHORT_YEAR_RANGE_RE = re.compile(r"(?<!\d)(\d{2})\s*[-~～至—–]\s*(\d{2})\s*年?")
FULL_YEAR_LIST_RE = re.compile(r"(20\d{2})\s*/\s*(20\d{2})\s*/\s*(20\d{2})\s*年")
SHORT_YEAR_LIST_RE = re.compile(r"(?<!\d)(\d{2})\s*/\s*(\d{2})\s*/\s*(\d{2})\s*年")
SINGLE_RE = re.compile(
    r"(20\d{2})\s*年[^。；;\n]{0,20}?(?:EPS|每股收益)[^。；;\n]{0,16}?(?:为|预计为|约为|达|达到)\s*"
    r"(-?\d+(?:\.\d+)?)\s*元",
    re.IGNORECASE,
)
YEAR_TOKEN_RE = re.compile(r"20\d{2}")


def _sentences(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    start = 0
    for idx, char in enumerate(text):
        # split on full stops and line breaks only: a year anchor and the EPS
        # numbers are often separated by "；" inside one paragraph.
        if char in "。\n":
            end = idx if char == "\n" else idx + 1
            chunk = text[start:end]
            if chunk.strip():
                out.append((start, chunk))
            start = idx + 1
    if start < len(text) and text[start:].strip():
        out.append((start, text[start:]))
    return out


def _year_anchors(sentence: str) -> list[tuple[int, list[int]]]:
    """Every explicit year anchor in the sentence: ("2024-2026", "23-25年", "23/24/25年")."""
    anchors: list[tuple[int, list[int]]] = []
    for match in YEAR_RANGE_RE.finditer(sentence):
        y0, y1 = int(match.group(1)), int(match.group(2))
        if y1 >= y0 and y1 - y0 <= 5:
            anchors.append((match.start(), list(range(y0, y1 + 1))))
    for match in SHORT_YEAR_RANGE_RE.finditer(sentence):
        y0, y1 = 2000 + int(match.group(1)), 2000 + int(match.group(2))
        if 2000 <= y0 <= 2099 and 0 <= y1 - y0 <= 5:
            anchors.append((match.start(), list(range(y0, y1 + 1))))
    for match in FULL_YEAR_LIST_RE.finditer(sentence):
        years = [int(match.group(i)) for i in (1, 2, 3)]
        if years == list(range(years[0], years[0] + 3)):
            anchors.append((match.start(), years))
    for match in SHORT_YEAR_LIST_RE.finditer(sentence):
        years = [2000 + int(match.group(i)) for i in (1, 2, 3)]
        if years == list(range(years[0], years[0] + 3)):
            anchors.append((match.start(), years))
    return sorted(anchors)


def _bind_years(sentence: str, value_start: int, n_values: int) -> tuple[list[int], str] | None:
    """Resolve the fiscal years a value list belongs to, or None if ambiguous.

    The nearest explicit anchor before the numbers wins; only anchors whose
    span length equals the number of values are accepted, so "2024-2025年"
    followed by three values is treated as ambiguous instead of guessed.
    """
    before = [anchor for anchor in _year_anchors(sentence) if anchor[0] < value_start]
    for _pos, years in reversed(before):
        if len(years) == n_values:
            return years, "text_range"
    hits = [int(y) for y in YEAR_TOKEN_RE.findall(sentence[:value_start])]
    if len(hits) >= 2:
        return list(range(hits[0], hits[0] + n_values)), "text_list"
    return None


def _parse_value_list(group: str) -> list[float]:
    """First number of each token: keeps 11.2 from "11.2(-4.2)"."""
    values: list[float] = []
    for piece in re.split(r"[、,，/\\／和及与]", group):
        match = NUM_RE.search(piece)
        if match:
            try:
                values.append(float(match.group(0)))
            except ValueError:
                continue
    return values


ANCHOR_LOOKBACK_CHARS = 300


def extract_from_text(text: str, years: set[int]) -> list[EpsValue]:
    found: list[EpsValue] = []
    for offset, sentence in _sentences(text):
        # The year anchor often sits in the previous sentence of the same
        # paragraph ("预计2024/2025/2026年营收…。对应EPS10.53/…元。"), so the
        # search space is the sentence plus a short look-behind.
        prefix = text[max(0, offset - ANCHOR_LOOKBACK_CHARS) : offset]
        search_space = prefix + sentence
        for match in EPS_VALUES_RE.finditer(sentence):
            values = _parse_value_list(match.group(1))
            if len(values) < 2:
                continue
            bound = _bind_years(search_space, len(prefix) + match.start(), len(values))
            if bound is None:
                continue
            fiscal_years, extractor = bound
            lead = len(sentence) - len(sentence.lstrip())
            for year, value in zip(fiscal_years, values):
                if year in years and _plausible(value):
                    found.append(
                        EpsValue(
                            year, value, "body_text", sentence.strip(), offset + lead, "rule", extractor
                        )
                    )
    return found


def extract_from_single_year(text: str, years: set[int]) -> list[EpsValue]:
    found: list[EpsValue] = []
    for offset, sentence in _sentences(text):
        for match in SINGLE_RE.finditer(sentence):
            year = int(match.group(1))
            value = float(match.group(2))
            lead = len(sentence) - len(sentence.lstrip())
            if year in years and _plausible(value):
                found.append(
                    EpsValue(
                        year, value, "body_text", sentence.strip(), offset + lead, "nlp", "text_single_year"
                    )
                )
    return found


PRIORITY = {
    "table_eps_row": 0,
    "text_range": 1,
    "text_list": 2,
    "text_single_year": 3,
}


def extract_eps(text: str, years: set[int]) -> list[EpsValue]:
    """Merge candidates per fiscal year, best extractor wins."""
    candidates: list[EpsValue] = []
    candidates.extend(extract_from_table(text, years))
    candidates.extend(extract_from_text(text, years))
    candidates.extend(extract_from_single_year(text, years))
    best: dict[int, EpsValue] = {}
    for value in candidates:
        current = best.get(value.fiscal_year)
        if current is None or PRIORITY[value.extractor] < PRIORITY[current.extractor]:
            best[value.fiscal_year] = value
    return [best[year] for year in sorted(best)]


# ---------------------------------------------------------------------------
# Per-report extraction
# ---------------------------------------------------------------------------


def extracted_dir(paths: DetailPaths) -> Path:
    return paths.meta_dir / "extracted"


def extract_one(paths: DetailPaths, item: dict[str, Any]) -> dict[str, Any]:
    publish_date = dt.date.fromisoformat(item["publish_date"])
    body = paths.body_file(publish_date, item["info_code"])
    record: dict[str, Any] = {
        "info_code": item["info_code"],
        "publish_date": item["publish_date"],
        "stock_code": item.get("stock_code"),
        "ts_code": item.get("ts_code"),
        "fiscal_years": sorted(expected_years(item)),
        "extractor_version": EXTRACT_VERSION,
        "extracted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if not body.exists():
        record.update({"status": "no-body", "values": [], "text_chars": 0})
        return record
    with gzip.open(body, "rb") as fh:
        html_text = fh.read().decode("utf-8", errors="replace")
    fragment, scope = slice_body_html(html_text)
    record["body_scope"] = scope
    text = html_to_text(fragment)
    record["text_chars"] = len(text)
    if len(text) < 200:
        record.update({"status": "empty-body", "values": []})
        return record
    values = extract_eps(text, expected_years(item))
    record["status"] = "ok" if values else "no-eps"
    record["values"] = [asdict(v) for v in values]
    return record


def cmd_extract(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    worklist = load_worklist(Path(args.worklist))
    out_dir = extracted_dir(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"ok": 0, "no-eps": 0, "no-body": 0, "empty-body": 0, "rows": 0}
    for item in worklist:
        record = extract_one(paths, item)
        out = out_dir / f"{item['publish_date'].replace('-', '')}" / f"{item['info_code']}.json"
        write_json_atomic(out, record)
        stats[record["status"]] = stats.get(record["status"], 0) + 1
        stats["rows"] += len(record["values"])
    return {
        "worklist": args.worklist,
        "docs": len(worklist),
        "stats": stats,
        "by_extractor": by_extractor(out_dir),
        "written_to": str(out_dir),
    }


def iter_extracted(paths: DetailPaths):
    for path in sorted(extracted_dir(paths).glob("*/*.json")):
        try:
            yield read_json(path)
        except Exception:
            continue


def by_extractor(out_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in out_dir.glob("*/*.json"):
        try:
            record = read_json(path)
        except Exception:
            continue
        for value in record.get("values") or []:
            key = f"{value.get('confidence')}:{value.get('extractor')}"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------

EPS_DDL = """
CREATE TABLE report_eps_extracted_new (
    info_code VARCHAR,
    fiscal_year INTEGER,
    eps DOUBLE,
    stock_code VARCHAR,
    ts_code VARCHAR,
    publish_date DATE,
    source VARCHAR,
    raw_text VARCHAR,
    char_offset INTEGER,
    confidence VARCHAR,
    extractor VARCHAR,
    extract_version VARCHAR,
    extracted_at TIMESTAMP,
    PRIMARY KEY (info_code, fiscal_year)
)
"""


def connect_rw(paths: DetailPaths):
    import duckdb

    paths.home.staging_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(paths.db_path))
    con.execute(f"SET temp_directory='{paths.home.staging_dir.as_posix()}'")
    con.execute("SET threads=4")
    return con


def cmd_build(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    import duckdb

    rows: list[tuple] = []
    docs = 0
    for record in iter_extracted(paths):
        docs += 1
        for value in record.get("values") or []:
            rows.append(
                (
                    record["info_code"],
                    int(value["fiscal_year"]),
                    float(value["eps"]),
                    record.get("stock_code"),
                    record.get("ts_code"),
                    record["publish_date"],
                    value.get("source"),
                    value.get("raw_text"),
                    int(value.get("char_offset") or 0),
                    value.get("confidence"),
                    value.get("extractor"),
                    record.get("extractor_version"),
                    record.get("extracted_at"),
                )
            )
    con = connect_rw(paths)
    try:
        existing = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        for name in FORBIDDEN_TABLES:
            if name not in existing:
                raise RuntimeError(f"expected table {name} is missing; refusing to write")
        con.execute("BEGIN")
        con.execute("DROP TABLE IF EXISTS report_eps_extracted_new")
        con.execute(EPS_DDL)
        if rows:
            con.executemany(
                "INSERT INTO report_eps_extracted_new VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
            )
        con.execute("DROP TABLE IF EXISTS report_eps_extracted")
        con.execute("ALTER TABLE report_eps_extracted_new RENAME TO report_eps_extracted")
        con.execute("COMMIT")
        table_rows = con.execute("SELECT count(*) FROM report_eps_extracted").fetchone()[0]
        by_conf = con.execute(
            "SELECT confidence, count(*) FROM report_eps_extracted GROUP BY 1 ORDER BY 1"
        ).fetchall()
        by_year = con.execute(
            "SELECT fiscal_year, count(*) FROM report_eps_extracted GROUP BY 1 ORDER BY 1"
        ).fetchall()
        docs_with = con.execute(
            "SELECT count(DISTINCT info_code) FROM report_eps_extracted"
        ).fetchone()[0]
        other = {
            name: con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in FORBIDDEN_TABLES
        }
    finally:
        con.close()
    return {
        "extracted_docs_scanned": docs,
        "rows_inserted": table_rows,
        "rows_offered": len(rows),
        "docs_with_values": docs_with,
        "by_confidence": {k: v for k, v in by_conf},
        "by_fiscal_year": {str(k): v for k, v in by_year},
        "untouched_tables": other,
        "table": "report_eps_extracted",
    }


def cmd_stats(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    import duckdb

    out_dir = extracted_dir(paths)
    records = list(iter_extracted(paths))
    status: dict[str, int] = {}
    for record in records:
        status[record.get("status", "unknown")] = status.get(record.get("status", "unknown"), 0) + 1
    con = duckdb.connect(str(paths.db_path), read_only=True)
    try:
        should = con.execute(
            "SELECT count(*) FROM report_meta WHERE publish_year IN (2023, 2024)"
        ).fetchone()[0]
        table_exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='report_eps_extracted'"
        ).fetchone()[0]
        table_rows = (
            con.execute("SELECT count(*) FROM report_eps_extracted").fetchone()[0] if table_exists else 0
        )
        by_conf = (
            con.execute("SELECT confidence, count(*) FROM report_eps_extracted GROUP BY 1 ORDER BY 1").fetchall()
            if table_exists
            else []
        )
    finally:
        con.close()
    return {
        "should_extract_2023_2024": should,
        "extracted_docs": len(records),
        "doc_status": status,
        "coverage_of_should": round(len(records) / should, 4) if should else None,
        "table_rows": table_rows,
        "by_confidence": {k: v for k, v in by_conf},
        "by_extractor": by_extractor(out_dir),
        "empty_rate": round(1 - (sum(1 for r in records if r.get("values")) / len(records)), 4) if records else None,
        "stats_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _body_text(paths: DetailPaths, record: dict[str, Any]) -> str:
    publish_date = dt.date.fromisoformat(record["publish_date"])
    body = paths.body_file(publish_date, record["info_code"])
    if not body.exists():
        return ""
    with gzip.open(body, "rb") as fh:
        html_text = fh.read().decode("utf-8", errors="replace")
    fragment, _scope = slice_body_html(html_text)
    return html_to_text(fragment)


def cmd_sample(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    """Pick N reports for manual verification, with the source context."""
    records = [r for r in iter_extracted(paths) if r.get("status") in ("ok", "no-eps", "empty-body")]
    records.sort(key=lambda r: (r.get("status"), r.get("publish_date") or "", r["info_code"]))
    if args.per_status:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            buckets.setdefault(record.get("status"), []).append(record)
        chosen: list[dict[str, Any]] = []
        per = max(1, args.docs // max(1, len(buckets)))
        for status in sorted(buckets):
            chosen.extend(buckets[status][:per])
        chosen = chosen[: args.docs]
    else:
        chosen = records[: args.docs]
    sample = []
    for record in chosen:
        text = _body_text(paths, record)
        values = record.get("values") or []
        enriched = []
        for value in values:
            offset = int(value.get("char_offset") or 0)
            enriched.append(
                {
                    **value,
                    "context": text[max(0, offset - 120) : offset + len(value.get("raw_text") or "") + 120],
                }
            )
        sample.append(
            {
                "info_code": record["info_code"],
                "publish_date": record.get("publish_date"),
                "stock_code": record.get("stock_code"),
                "status": record.get("status"),
                "text_chars": record.get("text_chars"),
                "body_scope": record.get("body_scope"),
                "values": enriched,
                "body_head": text[:400],
            }
        )
    return {"docs": len(sample), "sample": sample}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-root", help="QUANTDB_HOME, default /srv/share/quantdb")
    p.add_argument("--db-path", help="DuckDB file; default <data-root>/db/quantdb.duckdb")
    sub = p.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--worklist", required=True)
    sub.add_parser("build")
    sub.add_parser("stats")
    sample = sub.add_parser("sample")
    sample.add_argument("--docs", type=int, default=30)
    sample.add_argument("--per-status", action="store_true", help="spread the sample across ok/no-eps/empty-body")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    paths = DetailPaths(Paths.from_args(args.data_root, args.db_path))
    try:
        if args.command == "extract":
            payload = cmd_extract(args, paths)
        elif args.command == "build":
            payload = cmd_build(args, paths)
        elif args.command == "stats":
            payload = cmd_stats(args, paths)
        elif args.command == "sample":
            payload = cmd_sample(args, paths)
        else:
            raise SystemExit(f"unknown command {args.command}")
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if os.environ.get("REPORTS_DEBUG"):
            traceback.print_exc()
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
