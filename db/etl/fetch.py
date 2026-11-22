"""Tushare -> Parquet(L0) -> DuckDB(L1) ETL for WORK-513.

    python -m db.etl.fetch --dataset trade_cal --start 20060101 --end 20261121
    python -m db.etl.fetch --dataset all --concurrency 8 --resume
    python -m db.etl.fetch --dataset daily --start 20060101 --end 20261121 --concurrency 8
    python -m db.etl.fetch --dataset all --load-only

Design notes
------------
* Every partition is written to a temp file and atomically renamed, then its
  manifest is written. A crash can never leave a manifest pointing at a
  missing file, and a finished partition is never rewritten.
* ``--resume`` (default on) skips a partition whose manifest exists, whose
  parquet exists with the recorded byte size, and whose sha256 matches.
* Market datasets are fetched with date windows and split automatically when
  the server truncates a response at its row cap; the rows are then exploded
  into one partition per trade_date (the layout WORK-513 mandates).
* Financial statements are fetched per ts_code because Tushare rejects
  cross-sectional queries for them (code 50101, "必填参数 ts_code").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from bisect import bisect_left, bisect_right
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from . import common
from .tushare_client import ROW_CAPS, TushareClient, TushareError

DEFAULT_START = "20060101"
DEFAULT_END = "20261121"

# The issue this ETL reports progress to.
ISSUE_ID = "01a202e2-3475-7438-b91a-29add6099aa5"

# The calendar always covers the whole study window so that a narrow smoke run
# can never poison the partition that the full run later resumes from.
CAL_START = "20060101"
CAL_END = "20261121"

# Broad-based indices whose monthly constituents are pulled (index_weight).
INDEX_UNIVERSE = (
    "000001.SH",  # 上证综指
    "000016.SH",  # 上证 50
    "000300.SH",  # 沪深 300
    "000905.SH",  # 中证 500
    "000852.SH",  # 中证 1000
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "000688.SH",  # 科创 50
)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    api: str
    strategy: str
    partition_kind: str


DATASETS: dict[str, DatasetSpec] = {
    "trade_cal": DatasetSpec("trade_cal", "trade_cal", "calendar", "exchange"),
    "stock_basic": DatasetSpec("stock_basic", "stock_basic", "stock_basic", "status"),
    "daily": DatasetSpec("daily", "daily", "range", "trade_date"),
    "daily_basic": DatasetSpec("daily_basic", "daily_basic", "range", "trade_date"),
    "adj_factor": DatasetSpec("adj_factor", "adj_factor", "range", "trade_date"),
    "stk_limit": DatasetSpec("stk_limit", "stk_limit", "range", "trade_date"),
    "suspend_d": DatasetSpec("suspend_d", "suspend_d", "range", "trade_date"),
    "index_daily": DatasetSpec("index_daily", "index_daily", "trade_date", "trade_date"),
    "index_weight": DatasetSpec("index_weight", "index_weight", "index_weight", "index_code"),
    "index_classify": DatasetSpec("index_classify", "index_classify", "classify", "src"),
    "namechange": DatasetSpec("namechange", "namechange", "namechange", "ts_code"),
    "dividend": DatasetSpec("dividend", "dividend", "per_code", "ts_code"),
    "income": DatasetSpec("income", "income", "financial", "ts_code"),
    "balancesheet": DatasetSpec("balancesheet", "balancesheet", "financial", "ts_code"),
    "cashflow": DatasetSpec("cashflow", "cashflow", "financial", "ts_code"),
    "fina_indicator": DatasetSpec("fina_indicator", "fina_indicator", "financial", "ts_code"),
}

FETCH_ORDER = [
    "trade_cal",
    "stock_basic",
    "daily",
    "daily_basic",
    "adj_factor",
    "index_daily",
    "stk_limit",
    "suspend_d",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "dividend",
    "namechange",
    "index_weight",
    "index_classify",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def norm_date(value: Any) -> str | None:
    """Normalise a tushare date cell to YYYYMMDD, or None when unusable."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    text = text.replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def frame_dates(df: pd.DataFrame, column: str) -> pd.Series:
    return df[column].map(norm_date)


def concat_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def contiguous_runs(dates: list[str], max_gap: int = 7) -> list[list[str]]:
    """Group an ascending date list into runs of near-consecutive calendar days.

    Trading weeks are separated by weekends, so a strict adjacency rule would
    produce ~1,020 windows for 20 years instead of one; the upstream accepts a
    date range and simply omits the non-trading days inside it, so runs are
    merged across gaps of up to ``max_gap`` calendar days.
    """
    if not dates:
        return []
    runs: list[list[str]] = []
    current = [dates[0]]
    for previous, following in zip(dates, dates[1:]):
        gap = (
            pd.Timestamp(str(following)) - pd.Timestamp(str(previous))
        ).days
        if gap <= max_gap:
            current.append(following)
            continue
        runs.append(current)
        current = [following]
    runs.append(current)
    return runs


def code_windows(list_date: str | None, delist_date: str | None, start: str, end: str) -> list[tuple[str, str]]:
    """Announcement-date windows for one stock's financial statements.

    The statements are selected by ``ann_date`` (verified 2026-11-22), so the
    windows are cut on announcement dates and bounded by the listing date.
    A 2015/2016 cut keeps each window below Tushare's hard 100-row cap.
    """
    def _usable(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text if len(text) == 8 and text.isdigit() else None

    listing = _usable(list_date)
    delisting = _usable(delist_date)
    alive_from = max([d for d in (listing, start) if d], default=start)
    alive_to = min([d for d in (delisting, end) if d], default=end)
    if alive_from > alive_to:
        return []
    windows: list[tuple[str, str]] = []
    cut = "20151231"
    if alive_from <= cut:
        windows.append((alive_from, min(cut, alive_to)))
    if alive_to >= "20160101":
        windows.append((max(alive_from, "20160101"), alive_to))
    return windows


def split_window(params: dict[str, Any], parts: int = 4) -> list[dict[str, Any]]:
    """Split a date window into ``parts`` contiguous children.

    The server truncates silently at a fixed row cap, so a window that comes
    back at the cap is re-requested over narrower windows until every response
    is strictly below the cap. A 4-way fan-out keeps the number of wasted
    "parent" calls (the truncated ones) at a quarter of the leaf count.
    """
    lo = pd.Timestamp(str(params["start_date"]))
    hi = pd.Timestamp(str(params["end_date"]))
    total_days = (hi - lo).days + 1
    if total_days <= 1:
        return []
    count = max(2, min(parts, total_days))
    children: list[dict[str, Any]] = []
    for index in range(count):
        start = lo + pd.Timedelta(days=int(round(index * total_days / count)))
        end = lo + pd.Timedelta(days=int(round((index + 1) * total_days / count)) - 1)
        if end > hi:
            end = hi
        if start > end:
            continue
        children.append(
            {
                **params,
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
            }
        )
    return children


class ListingDensity:
    """Estimates how many rows a market dataset has on a given day.

    The number of listed stocks is known from ``stock_basic`` (with its
    ``list_date``), so the expected row count of any window can be estimated
    without calling the API. The estimate drives the split fan-out: a window
    in 2026 holds ~5,600 rows a day while a window in 2006 holds ~1,400, so a
    uniform split would waste most of the upstream calls.
    """

    def __init__(self, list_dates: list[str]) -> None:
        self.sorted_dates = sorted(int(d) for d in list_dates if d)
        self._prefix: list[int] = [0]
        for value in self.sorted_dates:
            self._prefix.append(self._prefix[-1] + value)

    def listed_by(self, day: str) -> int:
        import bisect

        return bisect.bisect_right(self.sorted_dates, int(day))

    def estimate(self, lo: str, hi: str) -> int:
        """Exact number of (stock, day) pairs in [lo, hi], given the listing dates.

        Each stock contributes one row per calendar day from its listing date
        onwards, so the total is the sum over the listed stocks of the days
        they were alive inside the window. Computed with a prefix sum over the
        sorted listing dates (O(log n)), not a trapezoid approximation: an
        over-estimate would inflate the split fan-out and waste upstream calls.
        """
        import bisect

        lo_d, hi_d = int(lo), int(hi)
        if hi_d < lo_d:
            return 0
        days = (pd.Timestamp(str(hi)) - pd.Timestamp(str(lo))).days + 1
        before = bisect.bisect_right(self.sorted_dates, lo_d)
        inside = bisect.bisect_right(self.sorted_dates, hi_d) - before
        if inside <= 0:
            return int(before * days)
        total = before * days
        total += inside * (hi_d + 1) - (self._prefix[before + inside] - self._prefix[before])
        return int(total)


class Progress:
    """Writes meta/progress.json and posts a milestone comment at most every N minutes."""

    def __init__(self, dataset: str, total: int, interval_minutes: float = 10.0, milestone: int = 500):
        self.dataset = dataset
        self.total = total
        self.milestone = milestone
        self.interval = interval_minutes * 60.0
        self.done = 0
        self.failed = 0
        self.started = time.time()
        self._last_post = 0.0
        self._last_write = 0.0
        self._last_milestone = 0
        self._lock = threading.Lock()
        self._progress_path = common.META_DIR / "progress" / f"{dataset}.json"
        self._cli = os.environ.get("MULTICA_CLI_PATH", "")

    def bump(self, failed: bool = False) -> None:
        post = False
        write = False
        with self._lock:
            if failed:
                self.failed += 1
            else:
                self.done += 1
            crossed = self.done - self._last_milestone >= self.milestone
            now = time.time()
            if crossed:
                self._last_milestone = self.done
                if now - self._last_post >= self.interval:
                    self._last_post = now
                    post = True
            # The progress file is a convenience, not evidence: writing it on
            # every partition would cost more than the parquet write itself.
            if now - self._last_write >= 3.0:
                self._last_write = now
                write = True
        # The comment post shells out to the issue tracker; it must not run
        # while the lock is held or it would stall every worker.
        if post:
            self._post()
        if write:
            self._write()

    def _write(self) -> None:
        payload = {
            "dataset": self.dataset,
            "done": self.done,
            "total": self.total,
            "failed": self.failed,
            "elapsed_s": int(time.time() - self.started),
            "updated_at": common.utc_now(),
        }
        try:
            common.write_json_atomic(self._progress_path, payload)
        except OSError:
            pass


    def _post(self) -> None:
        if not self._cli:
            return
        elapsed = int(time.time() - self.started)
        text = (
            f"进度 | {self.dataset} | {self.done}/{self.total} 分区 | "
            f"耗时 {elapsed // 60} 分 {elapsed % 60} 秒 | 失败 {self.failed}"
        )
        try:
            import subprocess

            subprocess.run(
                [
                    self._cli,
                    "issue",
                    "comment",
                    "add",
                    ISSUE_ID,
                    "--content",
                    text,
                ],
                check=False,
                capture_output=True,
                timeout=60,
            )
        except Exception:  # noqa: BLE001 - progress posting must never break the fetch
            pass

    def summary(self) -> str:
        elapsed = int(time.time() - self.started)
        return (
            f"[done] {self.dataset}: 完成 {self.done}/{self.total} 分区, "
            f"失败 {self.failed}, 耗时 {elapsed // 60} 分 {elapsed % 60} 秒"
        )


# --------------------------------------------------------------------------- #
# strategies
# --------------------------------------------------------------------------- #


class Fetcher:
    def __init__(
        self,
        client: TushareClient,
        start: str,
        end: str,
        resume: bool = True,
        concurrency: int = 8,
        deadline: float | None = None,
    ) -> None:
        self.client = client
        self.start = start
        self.end = end
        self.resume = resume
        self.concurrency = max(1, concurrency)
        self.deadline = deadline
        self._calendar: list[str] | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._io_pool: ThreadPoolExecutor | None = None
        self._universe: pd.DataFrame | None = None
        self._listing_density: ListingDensity | None = None

    def expired(self) -> bool:
        """True once the slice budget is used up (the run resumes from disk)."""
        return self.deadline is not None and time.monotonic() >= self.deadline

    def run_bounded(self, items: list[str], handle: Callable[[str], None], label: str) -> None:
        """Run ``handle`` over ``items`` in bounded batches, honouring the budget.

        Submitting every task up front and then breaking out of the result loop
        does NOT stop the work: the executor's ``shutdown(wait=True)`` drains
        the queue, so a slice that was supposed to end after 8 minutes kept
        running - and kept spending upstream calls - until the whole dataset
        was done.
        """
        if not items:
            return
        if self.concurrency <= 1:
            for item in items:
                if self.expired():
                    print(f"[slice] 时间预算用尽，剩余{label}留给下一次续跑", flush=True)
                    return
                handle(item)
            return
        batch_size = max(self.concurrency * 4, self.concurrency)
        pending = list(items)
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            while pending:
                if self.expired():
                    print(f"[slice] 时间预算用尽，剩余{label}留给下一次续跑", flush=True)
                    return
                batch = pending[:batch_size]
                pending = pending[len(batch):]
                futures = {pool.submit(handle, item): item for item in batch}
                for future in as_completed(futures):
                    future.result()

    def resumable(self, dataset: str, partition: str) -> bool:
        """True when the partition is complete AND belongs to this run window.

        A narrow run (--start/--end) writes partitions whose content only
        covers that window. Resuming them for a wider study window would
        silently keep the narrow data for good, so the window is recorded in
        the manifest and a mismatch forces a re-fetch.
        """
        if not self.resume:
            return False
        if not common.manifest_ok(dataset, partition):
            return False
        if dataset not in common.WINDOW_SCOPED_DATASETS:
            return True
        meta = common.read_manifest(dataset, partition) or {}
        window = meta.get("study_window")
        if window is None:
            return True
        return list(window) == [self.start, self.end]

    def _density(self) -> ListingDensity | None:
        """Listing-density estimator used to size the window splits."""
        if self._listing_density is None:
            try:
                universe = self._stock_universe()
                self._listing_density = ListingDensity(
                    [str(d) for d in universe["list_date"].dropna().tolist()]
                )
            except Exception:  # noqa: BLE001 - the estimate is an optimisation
                self._listing_density = ListingDensity([])
        return self._listing_density if self._listing_density.sorted_dates else None

    @property
    def pool(self) -> ThreadPoolExecutor:
        """Pool for the per-code level of work."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self.concurrency)
        return self._pool

    @property
    def io_pool(self) -> ThreadPoolExecutor:
        """Pool for the window-level upstream calls."""
        if self._io_pool is None:
            self._io_pool = ThreadPoolExecutor(max_workers=self.concurrency)
        return self._io_pool

    def _windows(
        self,
        spec: DatasetSpec,
        params: dict[str, Any],
        cap: int,
        on_complete: Any,
        on_failure: Any,
        density_aware: bool = True,
    ) -> None:
        """Fetch a date window, splitting it until every response is below the cap.

        The split is breadth-first with one batch of ``concurrency`` windows in
        flight, so the upstream rate limiter (not the recursion) is the
        bottleneck: measured 2026-11-22, the sequential variant only reached
        83 calls/minute against a 195 calls/minute budget.

        ``cap`` is the upstream row cap; a response that reaches it may be
        truncated, so the window is split until every response is strictly
        below the cap. The fan-out is driven by the estimated row count (see
        ``ListingDensity``) so that the number of upstream calls stays close to
        the theoretical minimum of total_rows / cap.
        """
        density = self._density()
        pending = deque([params])
        stopped = False
        while pending:
            if self.expired():
                stopped = True
                break
            batch = []
            while pending and len(batch) < self.concurrency:
                batch.append(pending.popleft())
            futures = {}
            for window in batch:
                label = f"{window.get('start_date')}..{window.get('end_date')}"
                futures[
                    self.io_pool.submit(
                        self.client.call,
                        spec.api,
                        window,
                        dataset=spec.name,
                        partition=label,
                    )
                ] = window
            for future in as_completed(futures):
                window = futures[future]
                lo = str(window.get("start_date"))
                hi = str(window.get("end_date"))
                try:
                    fields, items = future.result()
                except Exception as exc:  # noqa: BLE001
                    on_failure(lo, hi, exc)
                    continue
                if len(items) < cap:
                    frame = (
                        pd.DataFrame(items, columns=fields)
                        if items
                        else pd.DataFrame(columns=fields)
                    )
                    on_complete(lo, hi, frame)
                    continue
                parts = 4
                if density_aware and density is not None:
                    # Market datasets: size the fan-out from the number of rows
                    # the window is expected to hold.
                    estimate = density.estimate(lo, hi)
                    parts = int(-(-estimate // max(1, int(cap * 0.9))))  # ceil
                    parts = max(2, parts)
                else:
                    # Per-ts_code windows: the market-wide listing density is
                    # meaningless here, a plain halving is what the 100-row cap
                    # needs (a density-sized fan-out would explode into one
                    # window per calendar day).
                    parts = 2
                children = split_window(window, parts=parts)
                if not children:
                    on_failure(lo, hi, f"行数触顶({len(items)})且窗口无法继续拆分")
                    continue
                pending.extend(children)
        if stopped:
            print(
                "[slice] 时间预算用尽，未取完的窗口留给下一次续跑",
                flush=True,
            )
        # False means the window tree was NOT exhausted: the caller must not
        # treat the untouched trading days as "checked and empty".
        return not stopped

    # -- calendar -------------------------------------------------------- #

    def calendar(self) -> list[str]:
        if self._calendar is not None:
            return self._calendar
        path = common.partition_file("trade_cal", "SSE")
        if path.exists():
            df = pd.read_parquet(path)
            opened = df[df["is_open"] == 1]
            dates = sorted(
                d for d in (norm_date(v) for v in opened["cal_date"]) if d and self.start <= d <= self.end
            )
            if dates:
                self._calendar = dates
                return dates
        raise RuntimeError("缺少 trade_cal 分区，先跑 --dataset trade_cal")

    def trading_days_in(self, lo: str, hi: str) -> list[str]:
        """Trading days of the calendar inside the [lo, hi] window.

        The calendar is cached and sorted, so the lookup is a bisect instead
        of a scan over 5,000+ dates for every leaf window.
        """
        calendar = self.calendar()
        return calendar[bisect_left(calendar, lo) : bisect_right(calendar, hi)]

    def write_empty_partition(self, spec: DatasetSpec, key: str, query: dict[str, Any]) -> None:
        """Publish an explicit empty partition for a trading day.

        Some upstream datasets have no history for the early years
        (``stk_limit`` before ~2020, ``suspend_d`` early on). A trading day
        that was fetched and genuinely carries no rows still needs a
        manifest, otherwise the resume logic treats it as "not fetched yet"
        and re-probes the same empty range on every single run - the slice
        then spends its whole budget on days that will never produce rows
        and the backfill can never reach completion.
        """
        common.write_partition(
            pd.DataFrame(columns=common.canonical_columns(spec.name)),
            spec.name,
            key,
            "tushare",
            extra_meta={"rows_raw": 0, "query": query, "empty_upstream": True},
        )

    # -- per-dataset strategies ------------------------------------------ #

    def fetch_calendar(self, spec: DatasetSpec) -> tuple[int, list[dict[str, Any]]]:
        failures: list[dict[str, Any]] = []
        done = 0
        for exchange in ("SSE", "SZSE"):
            if self.resume and common.manifest_ok("trade_cal", exchange):
                done += 1
                continue
            try:
                frame = self.client.frame(
                    "trade_cal",
                    {"exchange": exchange, "start_date": CAL_START, "end_date": CAL_END},
                    dataset="trade_cal",
                    partition=exchange,
                )
                meta = common.write_partition(
                    frame,
                    "trade_cal",
                    exchange,
                    "tushare",
                    extra_meta={
                        "query": {"exchange": exchange, "start_date": CAL_START, "end_date": CAL_END}
                    },
                )
                done += 1
                print(f"[ok] trade_cal {exchange} rows={meta['rows']}", flush=True)
            except Exception as exc:  # noqa: BLE001
                failures.append({"dataset": "trade_cal", "partition": exchange, "error": str(exc)})
                print(f"[fail] trade_cal {exchange}: {exc}", file=sys.stderr, flush=True)
        common.append_failures(failures, "trade_cal")
        return done, failures

    def fetch_stock_basic(self, spec: DatasetSpec) -> tuple[int, list[dict[str, Any]]]:
        failures: list[dict[str, Any]] = []
        done = 0
        for status in ("L", "D", "P"):
            if self.resume and common.manifest_ok("stock_basic", status):
                done += 1
                continue
            try:
                frame = self.client.frame(
                    "stock_basic", {"list_status": status}, dataset="stock_basic", partition=status
                )
                meta = common.write_partition(
                    frame,
                    "stock_basic",
                    status,
                    "tushare",
                    extra_meta={"query": {"list_status": status}},
                )
                done += 1
                print(f"[ok] stock_basic {status} rows={meta['rows']}", flush=True)
            except Exception as exc:  # noqa: BLE001
                failures.append({"dataset": "stock_basic", "partition": status, "error": str(exc)})
                print(f"[fail] stock_basic {status}: {exc}", file=sys.stderr, flush=True)
        common.append_failures(failures, "stock_basic")
        return done, failures

    def fetch_range(self, spec: DatasetSpec, concurrency: int) -> tuple[int, list[dict[str, Any]]]:
        """Date-window fetch for 行情类; one partition per trade_date."""
        dates = self.calendar()
        missing = [d for d in dates if not (self.resume and common.manifest_ok(spec.name, d))]
        runs = contiguous_runs(missing)
        failures: list[dict[str, Any]] = []
        progress = Progress(spec.name, len(missing))
        lock = threading.Lock()
        seen: set[str] = set()

        failed_dates: set[str] = set()
        missing_set = set(missing)

        def on_failure(lo: str, hi: str, exc: Any) -> None:
            # Every trading day inside a failed window is suspect: it must NOT
            # be turned into an "empty" partition, otherwise the manifest would
            # mark it complete and every later run would skip it forever.
            span = [day.strftime("%Y%m%d") for day in pd.date_range(lo, hi, freq="D")]
            # Only the trading days that were actually expected are ledger
            # entries; the window may also cover weekends.
            span = [day for day in span if day in missing_set]
            with lock:
                for day in span:
                    failures.append(
                        {"dataset": spec.name, "partition": day, "error": str(exc)}
                    )
                failed_dates.update(span)
            progress.bump(failed=True)
            print(f"[fail] {spec.name} {lo}..{hi}: {exc}", file=sys.stderr, flush=True)

        def on_complete(lo: str, hi: str, frame: pd.DataFrame) -> None:
            # A window that came back complete covers every trading day in
            # [lo, hi]. Days that carry no rows are a real observation, not a
            # missing partition, so they get an explicit empty partition; days
            # outside the calendar are still published from the response so
            # no upstream row is ever dropped.
            groups: dict[str, pd.DataFrame] = {}
            if not frame.empty:
                work = frame.copy()
                work["trade_date"] = frame_dates(work, "trade_date")
                work = work[work["trade_date"].notna()]
                groups = {
                    str(trade_date): group.reset_index(drop=True)
                    for trade_date, group in work.groupby("trade_date", sort=True)
                }
            query = {"start_date": lo, "end_date": hi}
            for key, group in groups.items():
                seen.add(key)
                if self.resume and common.manifest_ok(spec.name, key):
                    continue
                try:
                    common.write_partition(
                        group,
                        spec.name,
                        key,
                        "tushare",
                        extra_meta={"query": query},
                    )
                    written.append(key)
                    progress.bump()
                except Exception as exc:  # noqa: BLE001
                    # A write failure affects this partition only, not the whole
                    # window: record it under its own key.
                    with lock:
                        failures.append(
                            {"dataset": spec.name, "partition": key, "error": str(exc)}
                        )
                        failed_dates.add(key)
                    progress.bump(failed=True)
                    print(f"[fail] {spec.name} {key}: {exc}", file=sys.stderr, flush=True)
            for key in self.trading_days_in(lo, hi):
                if key in groups:
                    continue
                if self.resume and common.manifest_ok(spec.name, key):
                    continue
                try:
                    self.write_empty_partition(spec, key, query)
                    written.append(key)
                    progress.bump()
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        failures.append(
                            {"dataset": spec.name, "partition": key, "error": str(exc)}
                        )
                        failed_dates.add(key)
                    progress.bump(failed=True)
                    print(f"[fail] {spec.name} {key}: {exc}", file=sys.stderr, flush=True)

        written: list[str] = []
        for run in runs:
            if self.expired():
                print("[slice] 时间预算用尽，剩余分区留给下一次续跑", flush=True)
                break
            complete = self._windows(
                spec,
                {"start_date": run[0], "end_date": run[-1]},
                ROW_CAPS.get(spec.api),
                on_complete,
                on_failure,
            )
            if not complete:
                # The slice budget ran out mid-tree. The days that were never
                # requested must NOT be written as empty partitions: they were
                # not checked, they were skipped, and an empty partition would
                # make every later run skip them for good.
                print(
                    f"[slice] {spec.name} {run[0]}..{run[-1]} 未取完，留给下一次续跑",
                    flush=True,
                )
                continue
            # A trading day that returns no rows still gets an explicit (empty)
            # partition, so "checked and empty" is distinguishable from
            # "never fetched".
            for trade_date in run:
                if trade_date in seen:
                    continue
                if trade_date in failed_dates:
                    continue
                if self.resume and common.manifest_ok(spec.name, trade_date):
                    continue
                columns = common.canonical_columns(spec.name)
                if not columns:
                    with lock:
                        failures.append(
                            {
                                "dataset": spec.name,
                                "partition": trade_date,
                                "error": "无法确定该数据集的列清单，未写空分区",
                            }
                        )
                    progress.bump(failed=True)
                    continue
                try:
                    common.write_partition(
                        pd.DataFrame(columns=columns),
                        spec.name,
                        trade_date,
                        "tushare",
                        extra_meta={
                            "query": {"start_date": run[0], "end_date": run[-1]},
                            "empty_upstream": True,
                        },
                    )
                    progress.bump()
                except Exception as exc:  # noqa: BLE001
                    on_failure(trade_date, trade_date, exc)
        common.append_failures(failures, spec.name)
        common.clear_failures(spec.name, written)
        print(progress.summary(), flush=True)
        return progress.done, failures

    def fetch_trade_date(self, spec: DatasetSpec, concurrency: int) -> tuple[int, list[dict[str, Any]]]:
        """One call per trading day (index_daily does not accept a date window)."""
        dates = self.calendar()
        missing = [d for d in dates if not (self.resume and common.manifest_ok(spec.name, d))]
        failures: list[dict[str, Any]] = []
        progress = Progress(spec.name, len(missing))
        lock = threading.Lock()

        def handle(trade_date: str) -> None:
            try:
                frame = self.client.frame(
                    spec.api, {"trade_date": trade_date}, dataset=spec.name, partition=trade_date
                )
                if frame.empty:
                    # The day was fetched and upstream has no rows for it.
                    # Recording it as a failure would put it into the failure
                    # ledger for good; an explicit empty partition is the
                    # honest record and lets the resume logic move on.
                    self.write_empty_partition(
                        spec, trade_date, {"trade_date": trade_date}
                    )
                    progress.bump()
                    return
                cap = ROW_CAPS.get(spec.api)
                truncated = bool(cap and len(frame) >= cap)
                if truncated:
                    # The response reached the upstream row cap, so rows may be
                    # missing. The partition must not be published as complete.
                    with lock:
                        failures.append(
                            {
                                "dataset": spec.name,
                                "partition": trade_date,
                                "error": f"响应触到上游行数上限({len(frame)}>= {cap})，可能截断",
                            }
                        )
                    progress.bump(failed=True)
                    print(
                        f"[fail] {spec.name} {trade_date}: 响应触到行数上限，可能截断",
                        file=sys.stderr,
                        flush=True,
                    )
                    return
                common.write_partition(
                    frame,
                    spec.name,
                    trade_date,
                    "tushare",
                    extra_meta={"possible_truncation": truncated, "query": {"trade_date": trade_date}},
                )
                progress.bump()
            except Exception as exc:  # noqa: BLE001
                with lock:
                    failures.append({"dataset": spec.name, "partition": trade_date, "error": str(exc)})
                progress.bump(failed=True)
                print(f"[fail] {spec.name} {trade_date}: {exc}", file=sys.stderr, flush=True)

        todo = missing
        self.run_bounded(todo, handle, spec.name)
        common.append_failures(failures, spec.name)
        print(progress.summary(), flush=True)
        return progress.done, failures

    def _stock_universe(self) -> pd.DataFrame:
        if self._universe is not None:
            return self._universe
        frames = []
        for status in ("L", "D", "P"):
            path = common.partition_file("stock_basic", status)
            if path.exists():
                frames.append(pd.read_parquet(path))
        if not frames:
            raise RuntimeError("缺少 stock_basic 分区，先跑 --dataset stock_basic")
        universe = pd.concat(frames, ignore_index=True)
        for column in ("list_date", "delist_date"):
            if column in universe.columns:
                universe[column] = universe[column].map(norm_date)
            else:
                # Upstream `stock_basic` does not return a delist_date; keep the
                # column present but empty (None), not NaN floats.
                universe[column] = None
        self._universe = universe
        return universe

    def fetch_financial(self, spec: DatasetSpec, concurrency: int) -> tuple[int, list[dict[str, Any]]]:
        """Per ts_code fetch (Tushare rejects cross-sectional queries: code 50101)."""
        universe = self._stock_universe()
        codes = list(universe["ts_code"].dropna().unique())
        dates = {row["ts_code"]: row for row in universe.to_dict(orient="records")}
        todo = [c for c in codes if not self.resumable(spec.name, c)]
        failures: list[dict[str, Any]] = []
        progress = Progress(spec.name, len(todo))
        lock = threading.Lock()

        def handle(code: str) -> None:
            row = dates.get(code, {})
            windows = code_windows(row.get("list_date"), row.get("delist_date"), self.start, self.end)
            columns = common.canonical_columns(spec.name)
            if not windows or not columns:
                common.write_partition(
                    pd.DataFrame(columns=columns),
                    spec.name,
                    code,
                    "tushare",
                    extra_meta={"rows_raw": 0, "study_window": [self.start, self.end]},
                )
                progress.bump()
                return
            frames: list[pd.DataFrame] = []

            def on_complete(lo: str, hi: str, frame: pd.DataFrame) -> None:
                if not frame.empty:
                    frames.append(frame)

            window_failures: list[str] = []

            def on_failure(lo: str, hi: str, exc: Any) -> None:
                with lock:
                    window_failures.append(f"{lo}..{hi}")
                    failures.append(
                        {"dataset": spec.name, "partition": f"{code}/{lo}..{hi}", "error": str(exc)}
                    )
                progress.bump(failed=True)
                print(f"[fail] {spec.name} {code} {lo}..{hi}: {exc}", file=sys.stderr, flush=True)

            try:
                complete = True
                for lo, hi in windows:
                    ok = self._windows(
                        spec,
                        {"ts_code": code, "start_date": lo, "end_date": hi},
                        ROW_CAPS.get(spec.api),
                        on_complete,
                        on_failure,
                        density_aware=False,
                    )
                    if not ok:
                        # The slice budget ran out inside this code's window
                        # tree: the statement history would be short but the
                        # manifest would claim completeness, so the partition
                        # is NOT written and the next slice retries the code.
                        complete = False
                        break
                if not complete:
                    print(
                        f"[slice] {spec.name} {code} 未取完，留给下一次续跑", flush=True
                    )
                    return
                if window_failures:
                    # One window failed: the statement history would be short
                    # but the manifest would claim completeness, so the
                    # partition is NOT written and the next slice retries it.
                    with lock:
                        failures.append(
                            {
                                "dataset": spec.name,
                                "partition": code,
                                "error": f"窗口失败，未写分区: {window_failures}",
                            }
                        )
                    progress.bump(failed=True)
                    return
                frame = concat_frames(frames)
                if frame.empty:
                    common.write_partition(
                        pd.DataFrame(columns=columns),
                        spec.name,
                        code,
                        "tushare",
                        extra_meta={"rows_raw": 0, "study_window": [self.start, self.end]},
                    )
                    progress.bump()
                    return
                frame = frame.copy()
                frame["ann_date"] = frame_dates(frame, "ann_date")
                frame, dropped = common.drop_null_pk_rows(frame, spec.name)
                common.write_partition(
                    frame,
                    spec.name,
                    code,
                    "tushare",
                    extra_meta={
                        "query": {"ts_code": code, "windows": windows},
                        "rows_dropped_null_pk": dropped,
                        "study_window": [self.start, self.end],
                    },
                )
                common.clear_failures(spec.name, [code])
                progress.bump()
            except Exception as exc:  # noqa: BLE001
                with lock:
                    failures.append({"dataset": spec.name, "partition": code, "error": str(exc)})
                progress.bump(failed=True)
                print(f"[fail] {spec.name} {code}: {exc}", file=sys.stderr, flush=True)

        self.run_bounded(todo, handle, spec.name)
        common.append_failures(failures, spec.name)
        print(progress.summary(), flush=True)
        return progress.done, failures

    def fetch_dividend(self, spec: DatasetSpec, concurrency: int) -> tuple[int, list[dict[str, Any]]]:
        """Dividends per ts_code; Tushare's date-window semantics for this API are
        unreliable (2026-11-22 probe: a 3-month window returned 20 rows while a
        6-month window hit the 2000-row cap), so the per-code query is used."""
        universe = self._stock_universe()
        codes = list(universe["ts_code"].dropna().unique())
        todo = [c for c in codes if not self.resumable(spec.name, c)]
        failures: list[dict[str, Any]] = []
        progress = Progress(spec.name, len(todo))
        lock = threading.Lock()

        def handle(code: str) -> None:
            try:
                frame = self.client.frame(spec.api, {"ts_code": code}, dataset=spec.name, partition=code)
                rows_raw = len(frame)
                cap = ROW_CAPS.get(spec.api)
                truncated = bool(cap and len(frame) >= cap)
                if truncated:
                    # The response reached the upstream row cap, so rows may be
                    # missing. The partition must not be published as complete.
                    with lock:
                        failures.append(
                            {
                                "dataset": spec.name,
                                "partition": code,
                                "error": f"响应触到上游行数上限({len(frame)}>= {cap})，可能截断",
                            }
                        )
                    progress.bump(failed=True)
                    print(
                        f"[fail] {spec.name} {code}: 响应触到行数上限，可能截断",
                        file=sys.stderr,
                        flush=True,
                    )
                    return
                if not frame.empty:
                    frame = frame.copy()
                    frame["ann_date"] = frame_dates(frame, "ann_date")
                    keep = frame["ann_date"].isna() | (
                        (frame["ann_date"] >= self.start) & (frame["ann_date"] <= self.end)
                    )
                    frame = frame[keep].reset_index(drop=True)
                    frame, dropped = common.drop_null_pk_rows(frame, spec.name)
                else:
                    dropped = 0
                common.write_partition(
                    frame,
                    spec.name,
                    code,
                    "tushare",
                    extra_meta={
                        "rows_raw": rows_raw,
                        "query": {"ts_code": code},
                        "rows_dropped_null_pk": dropped,
                        "possible_truncation": truncated,
                        "study_window": [self.start, self.end],
                    },
                )
                progress.bump()
            except Exception as exc:  # noqa: BLE001
                with lock:
                    failures.append({"dataset": spec.name, "partition": code, "error": str(exc)})
                progress.bump(failed=True)
                print(f"[fail] {spec.name} {code}: {exc}", file=sys.stderr, flush=True)

        self.run_bounded(todo, handle, spec.name)
        common.append_failures(failures, spec.name)
        print(progress.summary(), flush=True)
        return progress.done, failures

    def fetch_namechange(self, spec: DatasetSpec, concurrency: int) -> tuple[int, list[dict[str, Any]]]:
        """Name changes by year window, exploded into one partition per ts_code.

        A single 2006-2026 window is truncated at the 10000-row cap and loses
        rows (verified 2026-11-22: per-code rows missing from the full-window
        response), so one call per calendar year is used and the yearly
        responses are unioned before exploding.
        """
        universe = self._stock_universe()
        codes = set(universe["ts_code"].dropna().unique())
        failures: list[dict[str, Any]] = []
        failed_years: list[str] = []
        frames = []
        for year in range(int(self.start[:4]), int(self.end[:4]) + 1):
            lo = max(f"{year}0101", self.start)
            hi = min(f"{year}1231", self.end)
            if lo > hi:
                continue
            try:
                frame = self.client.fetch_window(
                    spec.api,
                    {"start_date": lo, "end_date": hi},
                    dataset=spec.name,
                    partition=str(year),
                    cap=ROW_CAPS.get(spec.api),
                )
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:  # noqa: BLE001
                failed_years.append(str(year))
                failures.append({"dataset": spec.name, "partition": str(year), "error": str(exc)})
                print(f"[fail] {spec.name} {year}: {exc}", file=sys.stderr, flush=True)
        common.append_failures(failures, spec.name)
        if failed_years:
            # A year failed: the union would be missing that year's rows while
            # the manifest claimed completeness, and the rows would never be
            # repaired. Nothing is written; the next run retries the whole
            # sweep.
            print(
                f"[fail] {spec.name}: 年份窗口失败 {failed_years}，未写分区",
                file=sys.stderr,
                flush=True,
            )
            return 0, failures
        combined = concat_frames(frames)
        if combined.empty:
            return 0, failures
        combined = combined.copy()
        combined["ts_code"] = combined["ts_code"].astype(str)
        combined = combined[combined["ts_code"].isin(codes)]
        written = 0
        for code, group in combined.groupby("ts_code", sort=True):
            if self.resumable(spec.name, str(code)):
                continue
            common.write_partition(
                group.reset_index(drop=True),
                spec.name,
                str(code),
                "tushare",
                extra_meta={
                    "query": {"years": "yearly windows"},
                    "study_window": [self.start, self.end],
                },
            )
            written += 1
        print(f"[done] {spec.name}: {written} partitions, {len(failures)} failures", flush=True)
        return written, failures

    def fetch_index_weight(self, spec: DatasetSpec, concurrency: int) -> tuple[int, list[dict[str, Any]]]:
        """Monthly constituent weights for the broad index universe."""
        failures: list[dict[str, Any]] = []
        done = 0
        for index_code in INDEX_UNIVERSE:
            if self.resumable(spec.name, index_code):
                done += 1
                continue
            frames = []
            months_requested = 0
            months_with_rows = 0
            failed_months: list[str] = []
            year, month = int(self.start[:4]), int(self.start[4:6])
            end_year, end_month = int(self.end[:4]), int(self.end[4:6])
            while (year, month) <= (end_year, end_month):
                lo = max(f"{year:04d}{month:02d}01", self.start)
                # _month_end() already returns the full YYYYMMDD of the last
                # day of the month. Prefixing it with the year and month again
                # produced "20260920260930", an invalid date that made the
                # upstream answer with zero rows for every window.
                hi = min(_month_end(year, month), self.end)
                if lo > hi:
                    year, month = _next_month(year, month)
                    continue
                try:
                    months_requested += 1
                    frame = self.client.fetch_window(
                        spec.api,
                        {"index_code": index_code, "start_date": lo, "end_date": hi},
                        dataset=spec.name,
                        partition=f"{index_code} {lo}",
                        cap=ROW_CAPS.get(spec.api),
                    )
                    if not frame.empty:
                        months_with_rows += 1
                        frames.append(frame)
                except Exception as exc:  # noqa: BLE001
                    failed_months.append(f"{index_code} {lo}")
                    failures.append(
                        {"dataset": spec.name, "partition": f"{index_code} {lo}", "error": str(exc)}
                    )
                year, month = _next_month(year, month)
            if failed_months:
                # A monthly window failed: the constituent set would be missing
                # that month while the manifest claimed completeness. Nothing is
                # written; the next run retries the whole sweep.
                print(
                    f"[fail] {spec.name} {index_code}: 月份窗口失败 {failed_months}，未写分区",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            combined = concat_frames(frames)
            if combined.empty:
                # Every monthly window came back empty: the upstream has no
                # constituent history for this index. That is an observation,
                # not a failure - a permanent failure entry would keep the
                # dataset from ever reaching completion. The manifest records
                # months_requested = 0 rows so the gap stays visible.
                common.write_partition(
                    pd.DataFrame(columns=common.canonical_columns(spec.name)),
                    spec.name,
                    index_code,
                    "tushare",
                    extra_meta={
                        "rows_raw": 0,
                        "query": {
                            "index_code": index_code,
                            "freq": "monthly",
                            "start_date": self.start,
                            "end_date": self.end,
                        },
                        "months_requested": months_requested,
                        "months_with_rows": months_with_rows,
                        "empty_upstream": True,
                        "study_window": [self.start, self.end],
                    },
                )
                done += 1
                print(
                    f"[warn] {spec.name} {index_code}: 全部月度窗口均为空，已写空分区",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            common.write_partition(
                combined,
                spec.name,
                index_code,
                "tushare",
                extra_meta={
                    "query": {
                        "index_code": index_code,
                        "freq": "monthly",
                        "start_date": self.start,
                        "end_date": self.end,
                    },
                    "months_requested": months_requested,
                    "months_with_rows": months_with_rows,
                    "study_window": [self.start, self.end],
                },
            )
            done += 1
            print(f"[ok] index_weight {index_code} rows={len(combined)}", flush=True)
        common.append_failures(failures, spec.name)
        return done, failures

    def fetch_classify(self, spec: DatasetSpec) -> tuple[int, list[dict[str, Any]]]:
        failures: list[dict[str, Any]] = []
        if self.resume and common.manifest_ok(spec.name, "SW2021"):
            return 1, failures
        try:
            frame = self.client.frame(
                spec.api, {"src": "SW2021"}, dataset=spec.name, partition="SW2021"
            )
            common.write_partition(
                frame, spec.name, "SW2021", "tushare", extra_meta={"query": {"src": "SW2021"}}
            )
            print(f"[ok] index_classify SW2021 rows={len(frame)}", flush=True)
            return 1, failures
        except Exception as exc:  # noqa: BLE001
            failures.append({"dataset": spec.name, "partition": "SW2021", "error": str(exc)})
            common.append_failures(failures, spec.name)
            return 0, failures


def _month_end(year: int, month: int) -> str:
    """Last calendar day of a month as YYYYMMDD."""
    return (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def run_datasets(
    names: list[str],
    start: str,
    end: str,
    concurrency: int,
    resume: bool,
    rpm: float,
    retries: int,
    deadline: float | None = None,
) -> int:
    client = TushareClient(rpm=rpm, retries=retries, concurrency=concurrency)
    fetcher = Fetcher(
        client, start, end, resume=resume, concurrency=concurrency, deadline=deadline
    )
    total_failures = 0
    for name in names:
        if fetcher.expired():
            print(f"[slice] 时间预算用尽，剩余数据集留给下一次续跑", flush=True)
            break
        spec = DATASETS[name]
        print(f"=== {name} ({spec.api}, {spec.strategy}) ===", flush=True)
        started = time.time()
        if spec.strategy == "calendar":
            done, failures = fetcher.fetch_calendar(spec)
        elif spec.strategy == "stock_basic":
            done, failures = fetcher.fetch_stock_basic(spec)
        elif spec.strategy == "range":
            done, failures = fetcher.fetch_range(spec, concurrency)
        elif spec.strategy == "trade_date":
            done, failures = fetcher.fetch_trade_date(spec, concurrency)
        elif spec.strategy == "financial":
            done, failures = fetcher.fetch_financial(spec, concurrency)
        elif spec.strategy == "per_code":
            done, failures = fetcher.fetch_dividend(spec, concurrency)
        elif spec.strategy == "namechange":
            done, failures = fetcher.fetch_namechange(spec, concurrency)
        elif spec.strategy == "index_weight":
            done, failures = fetcher.fetch_index_weight(spec, concurrency)
        elif spec.strategy == "classify":
            done, failures = fetcher.fetch_classify(spec)
        else:  # pragma: no cover - registry is closed
            raise ValueError(f"未知策略: {spec.strategy}")
        total_failures += len(failures)
        print(
            f"[done] {name}: {done} partitions, {len(failures)} failures, "
            f"{int(time.time() - started)} s",
            flush=True,
        )
    print(f"[totals] upstream calls={client.calls} failures={total_failures}", flush=True)
    return 0 if total_failures == 0 else 1


def load_duckdb(datasets: list[str], full_refresh: bool) -> int:
    if "all" in datasets:
        datasets = [name for name in DATASETS if name not in {"index_classify"}]
    loaded = 0
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        for dataset in datasets:
            if dataset not in common.TABLE_PK:
                print(f"[skip] 未知数据集 {dataset}", file=sys.stderr)
                continue
            parts = common.list_partitions(dataset)
            if not parts:
                print(f"[skip] {dataset}: 无分区")
                continue
            rows = common.load_partitions(con, dataset, parts, full_refresh=full_refresh)
            loaded += rows
            table_rows = int(con.execute(f"SELECT count(*) FROM {dataset}").fetchone()[0])
            print(
                f"[ok] {dataset}: {len(parts)} 分区 / 读入 {rows} 行 / 表内 {table_rows} 行",
                flush=True,
            )
        con.execute("CHECKPOINT")
    return loaded


def cmd_status(_: argparse.Namespace) -> int:
    print(f"QUANTDB_HOME={common.QDB_HOME}")
    for name in FETCH_ORDER:
        parts = common.list_partitions(name)
        if parts:
            print(f"{name:16s} partitions={len(parts):6d}")
    if common.DB_PATH.exists():
        print(f"duckdb={common.DB_PATH} ({common.DB_PATH.stat().st_size} bytes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.etl.fetch",
        description="quantdb ETL：Tushare -> Parquet(L0) -> DuckDB(L1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dataset", default="all", help="all 或逗号分隔的数据集名")
    parser.add_argument("--start", default=DEFAULT_START, help="YYYYMMDD")
    parser.add_argument("--end", default=DEFAULT_END, help="YYYYMMDD")
    parser.add_argument("--concurrency", type=int, default=8, help="并发数，上限 8")
    parser.add_argument("--rpm", type=float, default=195.0, help="全局请求速率（次/分钟）")
    parser.add_argument("--retries", type=int, default=2, help="单次调用失败重试次数")
    parser.add_argument("--resume", action="store_true", help="断点续跑（默认开启）")
    parser.add_argument("--no-resume", action="store_true", help="忽略已有 manifest，重新抓取")
    parser.add_argument("--load-only", action="store_true", help="只把已有 Parquet 装入 DuckDB")
    parser.add_argument("--full-refresh", action="store_true", help="装载前清空目标表")
    parser.add_argument(
        "--time-budget",
        type=int,
        default=0,
        help="单次运行的秒数预算（0 = 不限）；用尽后干净退出，靠 manifest 续跑",
    )
    parser.add_argument("--status", action="store_true", help="打印分区状态")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.concurrency < 1 or args.concurrency > 8:
        print("--concurrency 必须在 1..8", file=sys.stderr)
        return 2
    common.ensure_layout()
    if args.status:
        return cmd_status(args)
    if args.load_only:
        datasets = [d.strip() for d in args.dataset.split(",") if d.strip()]
        load_duckdb(datasets, args.full_refresh)
        return 0
    names = [d.strip() for d in args.dataset.split(",") if d.strip()]
    if "all" in names:
        names = list(FETCH_ORDER)
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        print(f"未知数据集: {unknown}", file=sys.stderr)
        return 2
    deadline = time.monotonic() + args.time_budget if args.time_budget > 0 else None
    return run_datasets(
        names,
        start=args.start,
        end=args.end,
        concurrency=args.concurrency,
        resume=not args.no_resume,
        rpm=args.rpm,
        retries=args.retries,
        deadline=deadline,
    )


if __name__ == "__main__":
    raise SystemExit(main())
