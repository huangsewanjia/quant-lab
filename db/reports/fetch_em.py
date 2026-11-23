#!/usr/bin/env python3
"""Fetch Eastmoney sell-side report metadata and build derived DuckDB tables.

Scope is deliberately metadata-only.  The upstream record contains an
``encodeUrl`` and ``attachPages`` but this module never requests the PDF.

Commands::

    python db/reports/fetch_em.py fetch  --start 2023-01-01 --end 2026-11-23
    python db/reports/fetch_em.py build  --start 2023-01-01 --end 2026-11-23
    python db/reports/fetch_em.py verify --start 2023-01-01 --end 2026-11-23
    python db/reports/fetch_em.py stats

The raw layer is ``<QUANTDB_HOME>/raw/reports/meta/trade_date=YYYYMMDD/part.parquet``.
The DuckDB file is ``<QUANTDB_HOME>/db/quantdb.duckdb``.  Both can be changed
with ``--data-root`` / ``--db-path``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import requests

API_URL = "https://reportapi.eastmoney.com/report/list"
FORECAST_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax"
SOURCE_NAME = "eastmoney.reportapi"
IMPL_VERSION = "2026-11-23-p1"
Q_TYPE_STOCK = 0
PAGE_SIZE = 100  # upstream silently caps pageSize at 100
MAX_PAGES_PER_MONTH = 200
DEFAULT_WINDOW_DAYS = 90
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Paths and schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paths:
    home: Path
    db_file: Path | None = None

    @classmethod
    def from_args(cls, data_root: str | None, db_path: str | None) -> "Paths":
        home = Path(
            data_root
            or os.environ.get("QUANTDB_HOME")
            or "/srv/share/quantdb"
        ).expanduser().resolve()
        db = Path(db_path).expanduser().resolve() if db_path else None
        return cls(home=home, db_file=db)

    @property
    def raw_dir(self) -> Path:
        return self.home / "raw" / "reports" / "meta"

    @property
    def meta_dir(self) -> Path:
        return self.home / "meta" / "reports"

    @property
    def validation_dir(self) -> Path:
        return self.meta_dir / "validation"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def staging_dir(self) -> Path:
        return self.home / "staging" / "tmp"

    @property
    def db_path(self) -> Path:
        return self.db_file or (self.home / "db" / "quantdb.duckdb")


REPORT_FIELDS: dict[str, pa.DataType] = {
    "info_code": pa.string(),
    "title": pa.string(),
    "stock_code": pa.string(),
    "stock_name": pa.string(),
    "ts_code": pa.string(),
    "exchange": pa.string(),
    "org_code": pa.string(),
    "org_name": pa.string(),
    "org_short_name": pa.string(),
    "researcher": pa.string(),
    "author_ids": pa.string(),
    "publish_date": pa.date32(),
    "publish_year": pa.int32(),
    "fiscal_year_this": pa.int32(),
    "fiscal_year_next": pa.int32(),
    "fiscal_year_two": pa.int32(),
    "rating_raw": pa.string(),
    "rating_norm": pa.string(),
    "rating_rank": pa.int8(),
    "rating_value": pa.int8(),
    "last_rating_raw": pa.string(),
    "last_rating_value": pa.int8(),
    "rating_change_code": pa.string(),
    "rating_change_type": pa.string(),
    "aim_price_high": pa.float64(),
    "aim_price_low": pa.float64(),
    "predict_this_year_eps": pa.float64(),
    "predict_next_year_eps": pa.float64(),
    "predict_next_two_year_eps": pa.float64(),
    "predict_this_year_pe": pa.float64(),
    "predict_next_year_pe": pa.float64(),
    "predict_next_two_year_pe": pa.float64(),
    "predict_last_year_eps": pa.float64(),
    "predict_last_year_pe": pa.float64(),
    "actual_last_year_eps": pa.float64(),
    "actual_last_two_year_eps": pa.float64(),
    "indv_indu_code": pa.string(),
    "indv_indu_name": pa.string(),
    "industry_code": pa.string(),
    "industry_name": pa.string(),
    "em_industry_code": pa.string(),
    "report_type": pa.int32(),
    "attach_type": pa.string(),
    "attach_size": pa.int64(),
    "attach_pages": pa.int32(),
    "encode_url": pa.string(),
    "pdf_available": pa.bool_(),
    "market": pa.string(),
    "org_type": pa.string(),
    "s_rating_name": pa.string(),
    "s_rating_code": pa.string(),
    "column_code": pa.string(),
    "new_listing_date": pa.date32(),
    "new_purchase_date": pa.date32(),
    "new_issue_price": pa.float64(),
    "new_pe_issue_a": pa.float64(),
    "indv_is_new": pa.string(),
    "fetched_at": pa.timestamp("us"),
    "source_url": pa.string(),
    "raw_json": pa.string(),
}
REPORT_SCHEMA = pa.schema(REPORT_FIELDS)


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _i(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    text = str(value).strip()[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _joined(value: Any) -> str | None:
    if isinstance(value, list):
        return ",".join(str(x) for x in value if x not in (None, ""))
    if value in (None, ""):
        return None
    return str(value)


def _exchange(market: str | None, stock_code: str | None) -> str:
    m = (market or "").upper()
    if "SHANGHAI" in m or m == "SH":
        return "SH"
    if "BEIJING" in m or m == "BJ":
        return "BJ"
    if "SHENZHEN" in m or m == "SZ":
        return "SZ"
    code = stock_code or ""
    if code.startswith(("6", "51", "58", "60", "68", "90")):
        return "SH"
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return "SZ"


def _ts_code(stock_code: str | None, exchange: str) -> str | None:
    if not stock_code:
        return None
    return f"{stock_code}.{exchange}"


_STRONG_BUY = {"强烈推荐", "强推", "买入", "买进", "BUY", "BUY(BUY)"}
_BUY = {
    "增持",
    "推荐",
    "谨慎推荐",
    "优于大市",
    "跑赢行业",
    "outperform",
    "增持(outperform)",
    "买进(buy)",
}
_NEUTRAL = {"中性", "持有", "观望", "同步大市", "hold", "neutral"}
_REDUCE = {"减持", "回避", "弱于大市", "underperform"}
_SELL = {"卖出", "sell"}


def normalize_rating(raw: str | None, value: Any = None) -> tuple[str, int | None]:
    """Map a broker rating name to the five-level normalised scale.

    The scale is ordinal only.  Broker definitions differ, so this column must
    not be read as a cross-broker recommendation.
    """
    text = (raw or "").strip()
    low = text.lower()
    rank: int | None = None
    if text in _STRONG_BUY or low in {x.lower() for x in _STRONG_BUY}:
        name, rank = "强买", 5
    elif text in _BUY or low in {x.lower() for x in _BUY}:
        name, rank = "买入", 4
    elif text in _NEUTRAL or low in {x.lower() for x in _NEUTRAL}:
        name, rank = "中性", 3
    elif text in _REDUCE or low in {x.lower() for x in _REDUCE}:
        name, rank = "减持", 2
    elif text in _SELL or low in {x.lower() for x in _SELL}:
        name, rank = "卖出", 1
    elif "强烈" in text or "强推" in text:
        name, rank = "强买", 5
    elif text.startswith("买入") or text.startswith("买进"):
        name, rank = "强买", 5
    elif (
        text.startswith("增持")
        or text.startswith("推荐")
        or "优于大市" in text
        or "跑赢" in text
        or "outperform" in low
    ):
        name, rank = "买入", 4
    elif "中性" in text or "持有" in text or "观望" in text or "同步" in text:
        name, rank = "中性", 3
    elif "减持" in text or "回避" in text or "弱于大市" in text or "underperform" in low:
        name, rank = "减持", 2
    elif "卖出" in text or "sell" in low:
        name, rank = "卖出", 1
    else:
        name = "未知"
    numeric = _i(value)
    if rank is None and numeric is not None and text:
        # Eastmoney's numeric scale is 3=buy, 2=add, 1=neutral, 0=other.
        rank = {3: 5, 2: 4, 1: 3, 0: None}.get(numeric)
        if rank:
            name = {5: "强买", 4: "买入", 3: "中性"}[rank]
    return name, rank


def classify_change(
    code: Any,
    old_raw: str | None,
    new_raw: str | None,
    old_rank: int | None,
    new_rank: int | None,
) -> tuple[str, int | None]:
    """Return (direction, rank_delta).  ``ratingChange`` codes are inferred."""
    code_s = "" if code in (None, "") else str(_i(code) if _i(code) is not None else code)
    delta = None
    if old_rank is not None and new_rank is not None:
        delta = new_rank - old_rank
    if code_s == "2":
        return "initiate", delta
    if code_s == "1":
        return "downgrade", delta
    if code_s == "0":
        return "upgrade", delta
    if code_s == "3":
        return "maintain", delta
    if delta is not None:
        if delta > 0:
            return "upgrade", delta
        if delta < 0:
            return "downgrade", delta
        return "maintain", delta
    if new_rank is not None and old_rank is None and new_raw and not old_raw:
        return "initiate", delta
    return "unknown", delta


def normalize_record(raw: dict[str, Any], fetched_at: dt.datetime) -> dict[str, Any]:
    stock_code = str(raw.get("stockCode") or "").strip() or None
    market = str(raw.get("market") or "").strip() or None
    exchange = _exchange(market, stock_code)
    publish_date = _date(raw.get("publishDate"))
    rating_raw = str(raw.get("emRatingName") or "").strip() or None
    rating_norm, rating_rank = normalize_rating(rating_raw, raw.get("emRatingValue"))
    last_raw = str(raw.get("lastEmRatingName") or "").strip() or None
    last_norm, last_rank = normalize_rating(last_raw, raw.get("lastEmRatingValue"))
    direction, delta = classify_change(
        raw.get("ratingChange"), last_raw, rating_raw, last_rank, rating_rank
    )
    year = publish_date.year if publish_date else None
    return {
        "info_code": str(raw.get("infoCode") or "").strip(),
        "title": raw.get("title"),
        "stock_code": stock_code,
        "stock_name": raw.get("stockName"),
        "ts_code": _ts_code(stock_code, exchange),
        "exchange": exchange,
        "org_code": str(raw.get("orgCode") or "").strip() or None,
        "org_name": raw.get("orgName"),
        "org_short_name": raw.get("orgSName"),
        "researcher": raw.get("researcher"),
        "author_ids": _joined(raw.get("authorID")),
        "publish_date": publish_date,
        "publish_year": year,
        "fiscal_year_this": year,
        "fiscal_year_next": year + 1 if year else None,
        "fiscal_year_two": year + 2 if year else None,
        "rating_raw": rating_raw,
        "rating_norm": rating_norm,
        "rating_rank": rating_rank,
        "rating_value": _i(raw.get("emRatingValue")),
        "last_rating_raw": last_raw,
        "last_rating_value": _i(raw.get("lastEmRatingValue")),
        "rating_change_code": (
            ""
            if raw.get("ratingChange") in (None, "")
            else (
                str(_i(raw.get("ratingChange")))
                if _i(raw.get("ratingChange")) is not None
                else str(raw.get("ratingChange"))
            )
        ),
        "rating_change_type": direction,
        "aim_price_high": _f(raw.get("indvAimPriceT")),
        "aim_price_low": _f(raw.get("indvAimPriceL")),
        "predict_this_year_eps": _f(raw.get("predictThisYearEps")),
        "predict_next_year_eps": _f(raw.get("predictNextYearEps")),
        "predict_next_two_year_eps": _f(raw.get("predictNextTwoYearEps")),
        "predict_this_year_pe": _f(raw.get("predictThisYearPe")),
        "predict_next_year_pe": _f(raw.get("predictNextYearPe")),
        "predict_next_two_year_pe": _f(raw.get("predictNextTwoYearPe")),
        "predict_last_year_eps": _f(raw.get("predictLastYearEps")),
        "predict_last_year_pe": _f(raw.get("predictLastYearPe")),
        "actual_last_year_eps": _f(raw.get("actualLastYearEps")),
        "actual_last_two_year_eps": _f(raw.get("actualLastTwoYearEps")),
        "indv_indu_code": str(raw.get("indvInduCode") or "").strip() or None,
        "indv_indu_name": raw.get("indvInduName"),
        "industry_code": raw.get("industryCode"),
        "industry_name": raw.get("industryName"),
        "em_industry_code": raw.get("emIndustryCode"),
        "report_type": _i(raw.get("reportType")),
        "attach_type": str(raw.get("attachType") or "").strip() or None,
        "attach_size": _i(raw.get("attachSize")),
        "attach_pages": _i(raw.get("attachPages")),
        "encode_url": raw.get("encodeUrl"),
        "pdf_available": bool(raw.get("encodeUrl")),
        "market": market,
        "org_type": raw.get("orgType"),
        "s_rating_name": raw.get("sRatingName"),
        "s_rating_code": raw.get("sRatingCode"),
        "column_code": raw.get("column"),
        "new_listing_date": _date(raw.get("newListingDate")),
        "new_purchase_date": _date(raw.get("newPurchaseDate")),
        "new_issue_price": _f(raw.get("newIssuePrice")),
        "new_pe_issue_a": _f(raw.get("newPeIssueA")),
        "indv_is_new": raw.get("indvIsNew"),
        "fetched_at": fetched_at,
        "source_url": API_URL,
        "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
    }


# ---------------------------------------------------------------------------
# Files and manifests
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def partition_dir(paths: Paths, day: dt.date) -> Path:
    return paths.raw_dir / f"trade_date={day:%Y%m%d}"


def manifest_path(paths: Paths, day: dt.date) -> Path:
    return paths.meta_dir / f"trade_date={day:%Y%m%d}.json"


def partition_file(paths: Paths, day: dt.date) -> Path:
    return partition_dir(paths, day) / "part.parquet"


def partition_ok(paths: Paths, day: dt.date) -> bool:
    man = manifest_path(paths, day)
    part = partition_file(paths, day)
    if not man.exists() or not part.exists():
        return False
    try:
        payload = read_json(man)
    except Exception:
        return False
    return payload.get("sha256") == sha256_file(part)


def write_partition(
    paths: Paths,
    day: dt.date,
    records: list[dict[str, Any]],
    query: dict[str, Any],
    pages: int,
    upstream_hits: int,
    rows_raw: int,
) -> dict[str, Any]:
    records = sorted(records, key=lambda r: r["info_code"])
    table = pa.Table.from_pylist(records, schema=REPORT_SCHEMA)
    out_dir = partition_dir(paths, day)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = partition_file(paths, day)
    tmp = out.with_name(f"part.parquet.tmp.{os.getpid()}.{time.time_ns()}")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, out)
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = {
        "dataset": "reports/meta",
        "partition": f"trade_date={day:%Y%m%d}",
        "rows": len(records),
        "bytes": out.stat().st_size,
        "sha256": sha256_file(out),
        "file": str(out),
        "source": API_URL,
        "fetched_at": fetched_at,
        "columns": [field.name for field in REPORT_SCHEMA],
        "column_types": {field.name: str(field.type) for field in REPORT_SCHEMA},
        "primary_key": "info_code",
        "query": query,
        "pages": pages,
        "upstream_hits": upstream_hits,
        "rows_raw": rows_raw,
        "duplicates_dropped": max(rows_raw - len(records), 0),
        "impl_version": IMPL_VERSION,
        "empty_upstream": len(records) == 0,
    }
    write_json_atomic(manifest_path(paths, day), payload)
    return payload


# ---------------------------------------------------------------------------
# HTTP and fetch
# ---------------------------------------------------------------------------


class FetchError(RuntimeError):
    pass


class AuthBlocked(FetchError):
    pass


def _log_call(paths: Paths, payload: dict[str, Any]) -> None:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    path = paths.log_dir / f"reports-fetch-{dt.date.today():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def http_get_json(
    session: requests.Session,
    paths: Paths,
    url: str,
    params: dict[str, Any],
    *,
    retries: int = 2,
    timeout: tuple[int, int] = (10, 60),
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        started = time.time()
        try:
            response = session.get(url, params=params, timeout=timeout)
            elapsed = round(time.time() - started, 3)
            body = response.text
            if response.status_code in (401, 403):
                _log_call(
                    paths,
                    {
                        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "url": url,
                        "params": params,
                        "status": response.status_code,
                        "elapsed_s": elapsed,
                        "error": body[:500],
                    },
                )
                raise AuthBlocked(
                    f"upstream returned HTTP {response.status_code}; refusing to continue. "
                    f"Body starts: {body[:300]!r}"
                )
            if response.status_code == 200:
                payload = response.json()
                _log_call(
                    paths,
                    {
                        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "url": url,
                        "params": params,
                        "status": response.status_code,
                        "elapsed_s": elapsed,
                        "rows": len(payload.get("data") or []),
                        "hits": payload.get("hits"),
                        "total_page": payload.get("TotalPage"),
                    },
                )
                return payload
            last_error = FetchError(
                f"HTTP {response.status_code} for {url} params={params}: {body[:300]!r}"
            )
        except AuthBlocked:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(1.5**attempt)
    raise FetchError(str(last_error))


def month_windows(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    out: list[tuple[dt.date, dt.date]] = []
    cur = start.replace(day=1)
    while cur <= end:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            nxt = cur.replace(month=cur.month + 1, day=1)
        lo = max(cur, start)
        hi = min(nxt - dt.timedelta(days=1), end)
        if lo <= hi:
            out.append((lo, hi))
        cur = nxt
    return out


def _month_fully_cached(paths: Paths, lo: dt.date, hi: dt.date) -> bool:
    day = lo
    while day <= hi:
        if not partition_ok(paths, day):
            return False
        day += dt.timedelta(days=1)
    return True


def fetch_month(
    session: requests.Session,
    paths: Paths,
    lo: dt.date,
    hi: dt.date,
    *,
    resume: bool,
    sleep_s: float,
) -> dict[str, Any]:
    if resume and _month_fully_cached(paths, lo, hi):
        return {"month": f"{lo:%Y-%m}", "skipped": True, "rows": 0, "dates": 0}

    base = {
        "pageSize": PAGE_SIZE,
        "beginTime": lo.isoformat(),
        "endTime": hi.isoformat(),
        "qType": Q_TYPE_STOCK,
    }
    first = http_get_json(session, paths, API_URL, {**base, "pageNo": 1})
    if "data" not in first or "hits" not in first:
        raise FetchError(
            f"{lo}..{hi}: unexpected report-list payload keys={sorted(first)[:10]}; "
            "refusing to treat it as an empty month"
        )
    hits = int(first.get("hits") or 0)
    total_pages = int(first.get("TotalPage") or 0)
    if total_pages > MAX_PAGES_PER_MONTH:
        raise FetchError(
            f"{lo}..{hi} reports {total_pages} pages, above safety limit {MAX_PAGES_PER_MONTH}"
        )
    raw_rows: list[dict[str, Any]] = list(first.get("data") or [])
    pages = 1
    for page in range(2, total_pages + 1):
        payload = http_get_json(session, paths, API_URL, {**base, "pageNo": page})
        if "data" not in payload:
            raise FetchError(
                f"{lo}..{hi} page {page}: unexpected payload keys={sorted(payload)[:10]}"
            )
        raw_rows.extend(payload.get("data") or [])
        pages += 1
        if sleep_s:
            time.sleep(sleep_s)

    dedup: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        info_code = str(row.get("infoCode") or "").strip()
        if info_code:
            dedup[info_code] = row
    rows = list(dedup.values())
    if abs(len(rows) - hits) > max(2, int(hits * 0.005)):
        raise FetchError(
            f"{lo}..{hi}: fetched {len(rows)} unique rows but upstream says hits={hits}; "
            "refusing to publish a partial partition"
        )

    fetched_at = dt.datetime.now(dt.timezone.utc)
    by_day: dict[dt.date, list[dict[str, Any]]] = {}
    raw_by_day: dict[dt.date, int] = {}
    for row in raw_rows:
        day = _date(row.get("publishDate"))
        if day is not None:
            raw_by_day[day] = raw_by_day.get(day, 0) + 1
    for row in rows:
        day = _date(row.get("publishDate"))
        if day is None or day < lo or day > hi:
            continue
        by_day.setdefault(day, []).append(normalize_record(row, fetched_at))

    written = 0
    day = lo
    while day <= hi:
        if resume and partition_ok(paths, day):
            day += dt.timedelta(days=1)
            continue
        recs = by_day.get(day, [])
        write_partition(
            paths,
            day,
            recs,
            {
                "beginTime": lo.isoformat(),
                "endTime": hi.isoformat(),
                "qType": Q_TYPE_STOCK,
                "pageSize": PAGE_SIZE,
            },
            pages=pages,
            upstream_hits=hits,
            rows_raw=raw_by_day.get(day, 0),
        )
        written += 1
        day += dt.timedelta(days=1)
    return {
        "month": f"{lo:%Y-%m}",
        "skipped": False,
        "hits": hits,
        "rows": len(rows),
        "pages": pages,
        "dates_written": written,
    }


def cmd_fetch(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    start, end = parse_range(args.start, args.end)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://data.eastmoney.com/"})
    summaries: list[dict[str, Any]] = []
    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}_{start:%Y%m%d}_{end:%Y%m%d}"
    windows = month_windows(start, end)
    failures: list[dict[str, Any]] = []
    for idx, (lo, hi) in enumerate(windows, 1):
        try:
            item = fetch_month(session, paths, lo, hi, resume=args.resume, sleep_s=args.sleep)
            summaries.append(item)
            print(
                f"[fetch] {idx}/{len(windows)} {item['month']} "
                f"rows={item.get('rows', 0)} dates={item.get('dates_written', 0)}"
                + (" (cached)" if item.get("skipped") else ""),
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            failures.append({"month": f"{lo:%Y-%m}", "error": str(exc)})
            print(f"[fetch] FAILED {lo}..{hi}: {exc}", file=sys.stderr, flush=True)
            if isinstance(exc, AuthBlocked):
                break
    total_rows = sum(int(x.get("rows") or 0) for x in summaries)
    run = {
        "run_id": run_id,
        "command": "fetch",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "months": len(windows),
        "months_ok": len(summaries),
        "months_failed": len(failures),
        "rows": total_rows,
        "failures": failures,
        "summaries": summaries,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "impl_version": IMPL_VERSION,
    }
    write_json_atomic(paths.meta_dir / "runs" / f"{run_id}.json", run)
    if failures:
        raise FetchError(f"{len(failures)} month(s) failed; see {paths.meta_dir / 'runs'}")
    return run


# ---------------------------------------------------------------------------
# DuckDB build
# ---------------------------------------------------------------------------


REPORT_META_DDL = """
CREATE TABLE report_meta_new (
    info_code VARCHAR PRIMARY KEY,
    title VARCHAR,
    stock_code VARCHAR,
    stock_name VARCHAR,
    ts_code VARCHAR,
    exchange VARCHAR,
    org_code VARCHAR,
    org_name VARCHAR,
    org_short_name VARCHAR,
    researcher VARCHAR,
    author_ids VARCHAR,
    publish_date DATE,
    publish_year INTEGER,
    fiscal_year_this INTEGER,
    fiscal_year_next INTEGER,
    fiscal_year_two INTEGER,
    rating_raw VARCHAR,
    rating_norm VARCHAR,
    rating_rank TINYINT,
    rating_value TINYINT,
    last_rating_raw VARCHAR,
    last_rating_value TINYINT,
    rating_change_code VARCHAR,
    rating_change_type VARCHAR,
    aim_price_high DOUBLE,
    aim_price_low DOUBLE,
    predict_this_year_eps DOUBLE,
    predict_next_year_eps DOUBLE,
    predict_next_two_year_eps DOUBLE,
    predict_this_year_pe DOUBLE,
    predict_next_year_pe DOUBLE,
    predict_next_two_year_pe DOUBLE,
    predict_last_year_eps DOUBLE,
    predict_last_year_pe DOUBLE,
    actual_last_year_eps DOUBLE,
    actual_last_two_year_eps DOUBLE,
    indv_indu_code VARCHAR,
    indv_indu_name VARCHAR,
    industry_code VARCHAR,
    industry_name VARCHAR,
    em_industry_code VARCHAR,
    report_type INTEGER,
    attach_type VARCHAR,
    attach_size BIGINT,
    attach_pages INTEGER,
    encode_url VARCHAR,
    pdf_available BOOLEAN,
    market VARCHAR,
    org_type VARCHAR,
    s_rating_name VARCHAR,
    s_rating_code VARCHAR,
    column_code VARCHAR,
    new_listing_date DATE,
    new_purchase_date DATE,
    new_issue_price DOUBLE,
    new_pe_issue_a DOUBLE,
    indv_is_new VARCHAR,
    fetched_at TIMESTAMP,
    source_url VARCHAR
)
"""

CONSENSUS_DDL = """
CREATE TABLE consensus_eps_new (
    ts_code VARCHAR,
    stock_code VARCHAR,
    fiscal_year INTEGER,
    as_of_date DATE,
    eps_median DOUBLE,
    eps_mean DOUBLE,
    eps_std DOUBLE,
    eps_min DOUBLE,
    eps_max DOUBLE,
    eps_q25 DOUBLE,
    eps_q75 DOUBLE,
    n_orgs INTEGER,
    latest_publish_date DATE,
    window_days INTEGER,
    PRIMARY KEY (ts_code, fiscal_year, as_of_date)
)
"""

RATING_CHANGE_DDL = """
CREATE TABLE rating_change_new (
    info_code VARCHAR PRIMARY KEY,
    stock_code VARCHAR,
    ts_code VARCHAR,
    stock_name VARCHAR,
    stock_basic_name VARCHAR,
    stock_industry VARCHAR,
    org_code VARCHAR,
    org_name VARCHAR,
    publish_date DATE,
    old_rating_raw VARCHAR,
    new_rating_raw VARCHAR,
    old_rating_norm VARCHAR,
    new_rating_norm VARCHAR,
    direction VARCHAR,
    rank_delta INTEGER,
    rating_change_code VARCHAR
)
"""

REPORT_META_COLUMNS = [
    "info_code", "title", "stock_code", "stock_name", "ts_code", "exchange",
    "org_code", "org_name", "org_short_name", "researcher", "author_ids",
    "publish_date", "publish_year", "fiscal_year_this", "fiscal_year_next",
    "fiscal_year_two", "rating_raw", "rating_norm", "rating_rank", "rating_value",
    "last_rating_raw", "last_rating_value", "rating_change_code",
    "rating_change_type", "aim_price_high", "aim_price_low",
    "predict_this_year_eps", "predict_next_year_eps", "predict_next_two_year_eps",
    "predict_this_year_pe", "predict_next_year_pe", "predict_next_two_year_pe",
    "predict_last_year_eps", "predict_last_year_pe", "actual_last_year_eps",
    "actual_last_two_year_eps", "indv_indu_code", "indv_indu_name",
    "industry_code", "industry_name", "em_industry_code", "report_type",
    "attach_type", "attach_size", "attach_pages", "encode_url", "pdf_available",
    "market", "org_type", "s_rating_name", "s_rating_code", "column_code",
    "new_listing_date", "new_purchase_date", "new_issue_price", "new_pe_issue_a",
    "indv_is_new", "fetched_at", "source_url",
]


def _connect(paths: Paths, read_only: bool = False):
    import duckdb

    paths.staging_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(paths.db_path), read_only=read_only)
    if not read_only:
        con.execute(f"SET temp_directory='{paths.staging_dir.as_posix()}'")
        con.execute("SET threads=4")
    return con


def _parquet_glob(paths: Paths) -> str:
    return (paths.raw_dir / "trade_date=*" / "part.parquet").as_posix()


def build_tables(
    paths: Paths,
    start: dt.date,
    end: dt.date,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    if not list(paths.raw_dir.glob("trade_date=*/part.parquet")):
        raise FileNotFoundError(f"no report partitions under {paths.raw_dir}")
    con = _connect(paths)
    try:
        _install_rating_sql(con)
        con.execute("BEGIN")
        con.execute("DROP TABLE IF EXISTS report_meta_new")
        con.execute(REPORT_META_DDL)
        select_list = ", ".join(REPORT_META_COLUMNS)
        con.execute(
            f"INSERT INTO report_meta_new SELECT {select_list} "
            "FROM read_parquet(?, union_by_name=true)",
            [_parquet_glob(paths)],
        )
        # Keep the authoritative mapping from the P0 stock table.  The
        # exchange-suffix fallback in the raw layer stays as a cross-check.
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE stock_map AS
            SELECT ts_code, split_part(ts_code, '.', 1) AS stock_code, name, industry
            FROM stock_basic
            """
        )
        con.execute(
            """
            UPDATE report_meta_new AS r
            SET ts_code = s.ts_code
            FROM stock_map AS s
            WHERE r.stock_code = s.stock_code
            """
        )
        con.execute("DROP TABLE IF EXISTS report_meta")
        con.execute("ALTER TABLE report_meta_new RENAME TO report_meta")
        con.execute("COMMIT")

        con.execute("BEGIN")
        con.execute("DROP TABLE IF EXISTS rating_change_new")
        con.execute(RATING_CHANGE_DDL)
        con.execute(
            """
            INSERT INTO rating_change_new
            WITH mapped AS (
                SELECT r.*, s.name AS stock_basic_name, s.industry AS stock_industry
                FROM report_meta r
                LEFT JOIN stock_map s ON r.stock_code = s.stock_code
            )
            SELECT
                info_code, stock_code, ts_code, stock_name,
                stock_basic_name, stock_industry,
                org_code, org_name, publish_date,
                last_rating_raw, rating_raw,
                CASE last_rating_raw
                    WHEN '' THEN NULL ELSE normalize_rating_text(last_rating_raw)
                END,
                rating_norm,
                rating_change_type,
                COALESCE(rating_rank - normalize_rating_rank(last_rating_raw), NULL),
                rating_change_code
            FROM mapped
            WHERE rating_change_type IN ('upgrade', 'downgrade', 'initiate')
            """
        )
        con.execute("DROP TABLE IF EXISTS rating_change")
        con.execute("ALTER TABLE rating_change_new RENAME TO rating_change")
        con.execute("COMMIT")

        con.execute("BEGIN")
        con.execute("DROP TABLE IF EXISTS consensus_eps_new")
        con.execute(CONSENSUS_DDL)
        con.execute(
            f"""
            INSERT INTO consensus_eps_new
            WITH dates AS (
                SELECT cal_date AS as_of_date
                FROM trade_cal
                WHERE exchange = 'SSE'
                  AND CAST(is_open AS INTEGER) = 1
                  AND cal_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
            ),
            forecasts AS (
                SELECT info_code, ts_code, stock_code, org_code, org_name,
                       publish_date, fiscal_year_this AS fiscal_year,
                       predict_this_year_eps AS eps
                FROM report_meta WHERE predict_this_year_eps IS NOT NULL
                UNION ALL
                SELECT info_code, ts_code, stock_code, org_code, org_name,
                       publish_date, fiscal_year_next AS fiscal_year,
                       predict_next_year_eps AS eps
                FROM report_meta WHERE predict_next_year_eps IS NOT NULL
                UNION ALL
                SELECT info_code, ts_code, stock_code, org_code, org_name,
                       publish_date, fiscal_year_two AS fiscal_year,
                       predict_next_two_year_eps AS eps
                FROM report_meta WHERE predict_next_two_year_eps IS NOT NULL
            ),
            candidates AS (
                SELECT f.ts_code, f.stock_code, f.fiscal_year, d.as_of_date,
                       f.org_code, f.eps, f.publish_date, f.info_code,
                       row_number() OVER (
                           PARTITION BY f.ts_code, f.fiscal_year, d.as_of_date, f.org_code
                           ORDER BY f.publish_date DESC, f.info_code DESC
                       ) AS rn
                FROM forecasts f
                JOIN dates d
                  ON f.publish_date BETWEEN d.as_of_date - INTERVAL '{int(window_days) - 1} days'
                                        AND d.as_of_date
                WHERE f.ts_code IS NOT NULL AND f.eps IS NOT NULL
            )
            SELECT
                ts_code, stock_code, fiscal_year, as_of_date,
                median(eps) AS eps_median,
                avg(eps) AS eps_mean,
                stddev_samp(eps) AS eps_std,
                min(eps) AS eps_min,
                max(eps) AS eps_max,
                quantile_cont(eps, 0.25) AS eps_q25,
                quantile_cont(eps, 0.75) AS eps_q75,
                count(*) AS n_orgs,
                max(publish_date) AS latest_publish_date,
                {int(window_days)} AS window_days
            FROM candidates
            WHERE rn = 1
            GROUP BY ts_code, stock_code, fiscal_year, as_of_date
            """
        )
        con.execute("DROP TABLE IF EXISTS consensus_eps")
        con.execute("ALTER TABLE consensus_eps_new RENAME TO consensus_eps")
        con.execute("COMMIT")

        counts = {}
        for table in ("report_meta", "consensus_eps", "rating_change"):
            counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return {"tables": counts, "window_days": window_days}
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def _install_rating_sql(con) -> None:
    """Register small Python normalisers as DuckDB functions for derived SQL."""
    con.create_function(
        "normalize_rating_text",
        lambda raw: normalize_rating(raw)[0],
        ["VARCHAR"],
        "VARCHAR",
    )
    con.create_function(
        "normalize_rating_rank",
        lambda raw: normalize_rating(raw)[1],
        ["VARCHAR"],
        "TINYINT",
    )


def cmd_build(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    start, end = parse_range(args.start, args.end)
    result = build_tables(paths, start, end, window_days=args.window_days)
    result["start"] = start.isoformat()
    result["end"] = end.isoformat()
    return result


# ---------------------------------------------------------------------------
# Validation and statistics
# ---------------------------------------------------------------------------


def _stock_validation_sample(con, n: int) -> list[tuple[str, str, str, int]]:
    return con.execute(
        """
        SELECT stock_code, any_value(stock_name) AS stock_name,
               any_value(indv_indu_name) AS industry, count(*) AS n
        FROM report_meta
        WHERE indv_indu_name IS NOT NULL AND indv_indu_name <> ''
          AND stock_code IS NOT NULL
        GROUP BY stock_code, indv_indu_name
        QUALIFY row_number() OVER (
            PARTITION BY indv_indu_name ORDER BY count(*) DESC, stock_code
        ) = 1
        ORDER BY n DESC, stock_code
        LIMIT ?
        """,
        [n],
    ).fetchall()


def _api_count(session: requests.Session, paths: Paths, start: dt.date, end: dt.date, code: str) -> int:
    payload = http_get_json(
        session,
        paths,
        API_URL,
        {
            "pageSize": 1,
            "beginTime": start.isoformat(),
            "endTime": end.isoformat(),
            "pageNo": 1,
            "qType": Q_TYPE_STOCK,
            "code": code,
        },
    )
    return int(payload.get("hits") or 0)


def verify_counts(
    con,
    session: requests.Session,
    paths: Paths,
    start: dt.date,
    end: dt.date,
    n: int,
) -> list[dict[str, Any]]:
    out = []
    for code, name, industry, _ in _stock_validation_sample(con, n):
        db_n = con.execute(
            "SELECT count(*) FROM report_meta WHERE stock_code = ? AND publish_date BETWEEN ? AND ?",
            [code, start, end],
        ).fetchone()[0]
        api_n = _api_count(session, paths, start, end, code)
        diff = db_n - api_n
        pct = abs(diff) / api_n if api_n else (0.0 if db_n == 0 else 1.0)
        out.append(
            {
                "stock_code": code,
                "stock_name": name,
                "industry": industry,
                "db_rows": db_n,
                "api_hits": api_n,
                "diff": diff,
                "abs_pct": round(pct * 100, 3),
                "pass": pct <= 0.05,
            }
        )
    return out


def _secu_code(exchange: str | None, code: str) -> str:
    return f"{(exchange or 'SZ').upper()}{code}"


def verify_consensus(
    con,
    session: requests.Session,
    paths: Paths,
    start: dt.date,
    end: dt.date,
    codes: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for code, name, exchange in codes:
        payload = http_get_json(
            session,
            paths,
            FORECAST_URL,
            {"code": _secu_code(exchange, code)},
        )
        jgyc = payload.get("jgyc") or []
        anchor = next((x for x in jgyc if x.get("ORG_NAME_ABBR") == "近六月平均"), None)
        if anchor is None:
            out.append({"stock_code": code, "stock_name": name, "error": "no 近六月平均 row"})
            continue
        latest = con.execute(
            "SELECT max(as_of_date) FROM consensus_eps WHERE ts_code LIKE ?",
            [f"{code}.%"],
        ).fetchone()[0]
        years = []
        for idx in (2, 3, 4):
            fy = anchor.get(f"YEAR{idx}")
            em_eps = anchor.get(f"EPS{idx}")
            ours = con.execute(
                """
                SELECT eps_median, n_orgs, latest_publish_date
                FROM consensus_eps
                WHERE ts_code LIKE ? AND fiscal_year = ? AND as_of_date = ?
                """,
                [f"{code}.%", fy, latest],
            ).fetchone()
            if ours is None or em_eps is None:
                years.append({"fiscal_year": fy, "em_eps": em_eps, "ours": None, "pass": False})
                continue
            median, n_orgs, latest_pub = ours
            rel = abs(median - float(em_eps)) / abs(float(em_eps)) if float(em_eps) else 0.0
            years.append(
                {
                    "fiscal_year": fy,
                    "em_eps": float(em_eps),
                    "our_median": median,
                    "n_orgs": n_orgs,
                    "latest_publish_date": latest_pub,
                    "abs_pct": round(rel * 100, 3),
                    "pass": rel <= 0.05,
                }
            )
        out.append(
            {
                "stock_code": code,
                "stock_name": name,
                "as_of_date": latest,
                "comparison": "东财近六月平均 vs consensus_eps 中位数",
                "years": years,
            }
        )
    return out


def stats(con) -> dict[str, Any]:
    total = con.execute("SELECT count(*) FROM report_meta").fetchone()[0]
    min_date, max_date = con.execute(
        "SELECT min(publish_date), max(publish_date) FROM report_meta"
    ).fetchone()
    per_day = con.execute(
        """
        SELECT publish_date, count(*) n
        FROM report_meta
        GROUP BY publish_date ORDER BY publish_date
        """
    ).fetchall()
    zero_days = con.execute(
        """
        WITH days AS (
            SELECT DISTINCT cal_date AS d
            FROM trade_cal
            WHERE exchange = 'SSE'
              AND cal_date BETWEEN (SELECT min(publish_date) FROM report_meta)
                                AND (SELECT max(publish_date) FROM report_meta)
        ),
        have AS (SELECT DISTINCT publish_date FROM report_meta)
        SELECT d, COALESCE(CAST(is_open AS INTEGER), 0)
        FROM days LEFT JOIN have ON have.publish_date = days.d
        LEFT JOIN trade_cal t ON t.exchange = 'SSE' AND t.cal_date = days.d
        WHERE have.publish_date IS NULL
        """
    ).fetchall()
    eh = con.execute(
        """
        SELECT
          count(*) AS n,
          avg(CASE WHEN predict_this_year_eps IS NULL THEN 1 ELSE 0 END) AS eps_this_null,
          avg(CASE WHEN predict_next_year_eps IS NULL THEN 1 ELSE 0 END) AS eps_next_null,
          avg(CASE WHEN predict_next_two_year_eps IS NULL THEN 1 ELSE 0 END) AS eps_two_null,
          count(DISTINCT stock_code) AS stocks,
          count(DISTINCT org_code) AS orgs,
          count(DISTINCT indv_indu_name) AS industries,
          sum(CASE WHEN pdf_available THEN 1 ELSE 0 END) AS pdf_flags
        FROM report_meta
        """
    ).fetchone()
    rating_counts = dict(
        con.execute("SELECT rating_norm, count(*) FROM report_meta GROUP BY 1 ORDER BY 2 DESC").fetchall()
    )
    eps_by_year = [
        {
            "year": int(row[0]),
            "reports": int(row[1]),
            "this_year_null": round(float(row[2] or 0), 4),
            "next_year_null": round(float(row[3] or 0), 4),
            "two_year_null": round(float(row[4] or 0), 4),
        }
        for row in con.execute(
            """
            SELECT publish_year, count(*),
                   avg(CASE WHEN predict_this_year_eps IS NULL THEN 1 ELSE 0 END),
                   avg(CASE WHEN predict_next_year_eps IS NULL THEN 1 ELSE 0 END),
                   avg(CASE WHEN predict_next_two_year_eps IS NULL THEN 1 ELSE 0 END)
            FROM report_meta
            GROUP BY publish_year ORDER BY publish_year
            """
        ).fetchall()
    ]
    consensus_by_year = [
        {"fiscal_year": int(row[0]), "rows": int(row[1]), "stocks": int(row[2])}
        for row in con.execute(
            """
            SELECT fiscal_year, count(*), count(DISTINCT ts_code)
            FROM consensus_eps GROUP BY fiscal_year ORDER BY fiscal_year
            """
        ).fetchall()
    ]
    change_counts = dict(
        con.execute(
            "SELECT direction, count(*) FROM rating_change GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    )
    consensus_rows = con.execute("SELECT count(*) FROM consensus_eps").fetchone()[0]
    return {
        "report_meta_rows": total,
        "date_min": str(min_date),
        "date_max": str(max_date),
        "days_with_rows": len(per_day),
        "zero_trade_days": [
            {"date": str(d), "is_open": bool(is_open)} for d, is_open in zero_days
        ],
        "stocks": eh[4],
        "orgs": eh[5],
        "industries": eh[6],
        "eps_null_rate": {
            "this_year": round(float(eh[1] or 0), 4),
            "next_year": round(float(eh[2] or 0), 4),
            "two_year": round(float(eh[3] or 0), 4),
        },
        "pdf_flag_rows": eh[7],
        "rating_norm_counts": {str(k): v for k, v in rating_counts.items()},
        "rating_change_counts": {str(k): v for k, v in change_counts.items()},
        "eps_null_rate_by_publish_year": eps_by_year,
        "consensus_coverage_by_fiscal_year": consensus_by_year,
        "consensus_eps_rows": consensus_rows,
    }


def cmd_verify(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    start, end = parse_range(args.start, args.end)
    con = _connect(paths, read_only=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://data.eastmoney.com/"})
    try:
        counts = verify_counts(con, session, paths, start, end, args.sample_size)
        codes = [
            (
                r[0],
                r[1],
                con.execute(
                    "SELECT any_value(exchange) FROM report_meta WHERE stock_code=?", [r[0]]
                ).fetchone()[0],
            )
            for r in _stock_validation_sample(con, 3)
        ]
        consensus = verify_consensus(con, session, paths, start, end, codes)
        summary = stats(con)
    finally:
        con.close()
    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "count_validation": counts,
        "consensus_validation": consensus,
        "summary": summary,
        "pass": all(x["pass"] for x in counts)
        and bool(consensus)
        and all(
            "years" in x and all(y.get("pass") for y in x["years"])
            for x in consensus
        ),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    paths.validation_dir.mkdir(parents=True, exist_ok=True)
    out = paths.validation_dir / f"verify-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    write_json_atomic(out, payload)
    write_json_atomic(paths.meta_dir / "summary.json", summary)
    payload["evidence_file"] = str(out)
    return payload


def cmd_stats(args: argparse.Namespace, paths: Paths) -> dict[str, Any]:
    con = _connect(paths, read_only=True)
    try:
        payload = stats(con)
    finally:
        con.close()
    write_json_atomic(paths.meta_dir / "summary.json", payload)
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def parse_range(start: str | None, end: str | None) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    lo = parse_date(start) if start else dt.date(2023, 1, 1)
    hi = parse_date(end) if end else today
    if lo > hi:
        raise ValueError(f"start {lo} is after end {hi}")
    return lo, hi


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", help="QUANTDB_HOME, default /srv/share/quantdb")
    p.add_argument("--db-path", help="DuckDB file; default <data-root>/db/quantdb.duckdb")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("fetch", "build", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--start", default="2023-01-01")
        sp.add_argument("--end", default=dt.date.today().isoformat())
        if name == "fetch":
            sp.add_argument("--resume", action="store_true", help="skip partitions whose manifest and sha match")
            sp.add_argument("--sleep", type=float, default=0.15, help="seconds between pages")
        if name == "build":
            sp.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
        if name == "verify":
            sp.add_argument("--sample-size", type=int, default=10)
    sub.add_parser("stats")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    paths = Paths.from_args(args.data_root, args.db_path)
    try:
        if args.command == "fetch":
            payload = cmd_fetch(args, paths)
        elif args.command == "build":
            payload = cmd_build(args, paths)
        elif args.command == "verify":
            payload = cmd_verify(args, paths)
        elif args.command == "stats":
            payload = cmd_stats(args, paths)
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
