"""Incremental watermarks, plans and load helpers for the quantdb refresh jobs.

The full backfill (WORK-513/518) is driven by explicit ``--start/--end``
windows.  The scheduled jobs must not re-walk 20 years every night, so this
module answers one question before each run: *which* dates or partitions are
new?

Three rules matter:

* Market datasets advance by ``max(raw partition) + 1`` and stop at the last
  completed close.  A manual run before 18:30 therefore cannot write an empty
  partition for a day whose data is not published yet.
* Financial statements are selected by the upstream ``disclosure_date``
  endpoint (``actual_date=YYYYMMDD``), then fetched per ``ts_code`` because the
  upstream rejects cross-sectional statement queries.  A seven-day overlap
  catches late filings.  ``dividend`` is cross-sectional by ``ann_date`` and is
  merged back into the per-code partitions.
* A partition that already exists is merged, never replaced.  DuckDB loading
  uses ``INSERT OR REPLACE`` on the declared primary keys, so loading the same
  partition twice is idempotent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from . import common

TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_START = "20060101"
CAL_START = "20060101"
CAL_END = "20261121"
CLOSE_TIME = dt.time(18, 30)
INDEX_WEIGHT_TIME = dt.time(19, 30)

MARKET_DATASETS = (
    "daily",
    "daily_basic",
    "adj_factor",
    "stk_limit",
    "suspend_d",
    "index_daily",
)
FINANCE_DATASETS = ("income", "balancesheet", "cashflow", "fina_indicator", "dividend")
SUPPORTED_DATASETS = frozenset(MARKET_DATASETS + FINANCE_DATASETS + ("stock_basic", "trade_cal", "index_weight"))
GROUPS = ("market", "finance", "index_weight", "all")

# These two APIs legitimately return no rows on many trading days (no limit
# event / no suspension). For every other market dataset an empty recent
# trading day means "not published yet" and must be retried, not published as
# an empty partition.
ALLOW_EMPTY_DATASETS = frozenset({"stk_limit", "suspend_d"})


class IncrementalError(RuntimeError):
    """A plan or load precondition that the wrapper must surface in its log."""


@dataclass(frozen=True)
class Plan:
    group: str
    start: str | None
    end: str | None
    reason: str
    days: tuple[str, ...] = ()

    @property
    def is_noop(self) -> bool:
        return self.start is None or self.end is None or not self.days

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "start": self.start,
            "end": self.end,
            "reason": self.reason,
            "days": list(self.days),
            "noop": self.is_noop,
        }


def _norm_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith("T00:00:00"):
        text = text[:10]
    text = text.replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def parse_now(value: str | None = None) -> dt.datetime:
    """Parse the optional ``--now`` test hook in Asia/Shanghai time."""
    if value:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
    else:
        parsed = dt.datetime.now(TZ)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def calendar_frame() -> pd.DataFrame:
    path = common.partition_file("trade_cal", "SSE")
    if not path.exists():
        raise IncrementalError(
            f"缺少交易日历 {path}；先跑 `python -m db.etl.fetch --dataset trade_cal`"
        )
    frame = pd.read_parquet(path)
    frame["cal_date"] = frame["cal_date"].map(_norm_date)
    return frame


def open_days(calendar: pd.DataFrame | None = None) -> list[str]:
    frame = calendar if calendar is not None else calendar_frame()
    opened = frame[frame["is_open"] == 1]
    return sorted(d for d in opened["cal_date"].dropna().tolist() if d)


def is_trading_day(day: str, calendar: pd.DataFrame | None = None) -> bool:
    return day in set(open_days(calendar))


def last_trading_day(day: str, calendar: pd.DataFrame | None = None) -> str | None:
    days = open_days(calendar)
    eligible = [d for d in days if d <= day]
    return eligible[-1] if eligible else None


def first_trading_day_of_month(day: str, calendar: pd.DataFrame | None = None) -> str | None:
    prefix = day[:6]
    for value in open_days(calendar):
        if value.startswith(prefix):
            return value
    return None


def effective_end(
    now: dt.datetime,
    calendar: pd.DataFrame | None = None,
    close: dt.time = CLOSE_TIME,
) -> str:
    """The last day whose market data can be expected to exist.

    Before the close the current trading day's data is not published.  Using
    the previous trading day prevents a manual daytime run from writing a
    permanent empty partition; the 18:30 timer is the first run that may
    request today.
    """
    today = now.strftime("%Y%m%d")
    days = open_days(calendar)
    if today in days and now.time() >= close:
        return today
    previous = (now.date() - dt.timedelta(days=1)).strftime("%Y%m%d")
    value = last_trading_day(previous, calendar)
    if value is None:
        raise IncrementalError("交易日历没有可用的历史交易日")
    return value


def _date_after(day: str) -> str:
    return (dt.date(int(day[:4]), int(day[4:6]), int(day[6:])) + dt.timedelta(days=1)).strftime("%Y%m%d")


def _date_range(start: str, end: str) -> list[str]:
    lo = dt.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    hi = dt.date(int(end[:4]), int(end[4:6]), int(end[6:]))
    out: list[str] = []
    while lo <= hi:
        out.append(lo.strftime("%Y%m%d"))
        lo += dt.timedelta(days=1)
    return out


def max_raw_date(dataset: str) -> str | None:
    values = []
    for partition in common.list_partitions(dataset):
        if not _norm_date(partition):
            continue
        meta = common.read_manifest(dataset, partition) or {}
        if not common.manifest_file_ok(meta, verify_hash=False):
            continue
        values.append(partition)
    return max(values) if values else None


def db_max_date(dataset: str, con=None) -> str | None:
    """Max trade_date of a table, or None when the table/file is absent."""
    owns = con is None
    if owns:
        if not common.DB_PATH.exists():
            return None
        con = common.duckdb_connect(read_only=True)
    try:
        try:
            value = con.execute(f"SELECT max(trade_date) FROM {dataset}").fetchone()[0]
        except Exception:
            return None
        return _norm_date(value)
    finally:
        if owns:
            con.close()


def _market_start(dataset: str) -> str:
    raw = max_raw_date(dataset)
    db = db_max_date(dataset) if raw is None else None
    latest = max([d for d in (raw, db) if d], default=None)
    return _date_after(latest) if latest else DEFAULT_START


def _partition_present(dataset: str, day: str) -> bool:
    meta = common.read_manifest(dataset, day) or {}
    if not meta or meta.get("provisional"):
        return False
    return common.manifest_file_ok(meta, verify_hash=False)


def market_plans(
    now: dt.datetime,
    calendar: pd.DataFrame | None = None,
) -> dict[str, Plan]:
    frame = calendar if calendar is not None else calendar_frame()
    days = open_days(frame)
    end = effective_end(now, frame)
    all_days = [d for d in days if CAL_START <= d <= end]
    plans: dict[str, Plan] = {}
    for dataset in MARKET_DATASETS:
        # Find the first raw partition that is absent or corrupt, not just the
        # max date: a missing middle day must be re-fetched, not skipped.
        missing = [d for d in all_days if not _partition_present(dataset, d)]
        selected = [d for d in all_days if missing and d >= missing[0]]
        if not selected:
            plans[dataset] = Plan(dataset, None, None, f"无新交易日或缺失分区（到 {end}）")
            continue
        plans[dataset] = Plan(
            dataset,
            selected[0],
            selected[-1],
                f"水位 {selected[0]} 之后到 {end}，{len(selected)} 个交易日",
            tuple(selected),
        )
    return plans


def strict_empty_dates_for(
    dataset: str, plan: Plan, now: dt.datetime
) -> set[str]:
    """Dates whose empty response must be retried instead of published."""
    if plan.is_noop:
        return set()
    if dataset in ALLOW_EMPTY_DATASETS:
        # No limit event / no suspension is a legitimate empty result on many
        # trading days. Do not turn it into a source-delay alert.
        return set()
    return set(plan.days)


def finance_plan(now: dt.datetime, overlap_days: int = 7) -> Plan:
    if overlap_days < 1:
        raise IncrementalError("--overlap-days 必须 >= 1")
    end_date = now.date()
    start_date = end_date - dt.timedelta(days=overlap_days - 1)
    start = start_date.strftime("%Y%m%d")
    end = end_date.strftime("%Y%m%d")
    return Plan(
        "finance",
        start,
        end,
        f"公告日重叠 {overlap_days} 天：{start}..{end}",
        tuple(_date_range(start, end)),
    )


def index_weight_plan(now: dt.datetime, calendar: pd.DataFrame | None = None) -> Plan:
    frame = calendar if calendar is not None else calendar_frame()
    today = now.strftime("%Y%m%d")
    first = first_trading_day_of_month(today, frame)
    if first != today:
        return Plan("index_weight", None, None, f"{today} 不是当月第一个交易日（{first}）")
    if now.time() < INDEX_WEIGHT_TIME:
        return Plan("index_weight", None, None, f"未到 {INDEX_WEIGHT_TIME.strftime('%H:%M')}")
    first_of_month = dt.date(int(today[:4]), int(today[4:6]), 1)
    previous_end = first_of_month - dt.timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    start = previous_start.strftime("%Y%m%d")
    return Plan(
        "index_weight",
        start,
        today,
        f"每月首个交易日 {today}，取上月至今的月度窗口",
        tuple(_date_range(start, today)),
    )


def _validate_calendar_frame(
    frame: pd.DataFrame, exchange: str, start: str, end: str
) -> None:
    """Reject an empty or truncated calendar before it replaces the old one."""
    if frame.empty:
        raise IncrementalError(f"trade_cal {exchange} 返回空响应，保留旧日历")
    for column in ("cal_date", "is_open"):
        if column not in frame.columns:
            raise IncrementalError(f"trade_cal {exchange} 缺列 {column}")
    dates = [d for d in frame["cal_date"].map(_norm_date).tolist() if d]
    if not dates:
        raise IncrementalError(f"trade_cal {exchange} 没有有效日期，保留旧日历")
    if min(dates) > start or max(dates) < end:
        raise IncrementalError(
            f"trade_cal {exchange} 覆盖 {min(dates)}..{max(dates)}，"
            f"不足 {start}..{end}，保留旧日历"
        )
    expected = set(_date_range(start, end))
    missing = expected - set(dates)
    if missing:
        raise IncrementalError(
            f"trade_cal {exchange} 缺 {len(missing)} 天（如 {sorted(missing)[:3]}），"
            "保留旧日历"
        )
    try:
        flags = {int(value) for value in frame["is_open"].dropna().tolist()}
    except (TypeError, ValueError) as exc:
        raise IncrementalError(f"trade_cal {exchange} is_open 非法") from exc
    if not flags or 1 not in flags or not flags <= {0, 1}:
        raise IncrementalError(f"trade_cal {exchange} is_open 越界: {sorted(flags)}")


def refresh_calendar(client, end: str | None = None) -> dict[str, Any]:
    """Refresh the two trade_cal partitions through ``end`` (or today)."""
    calendar_end = max(CAL_END, end or dt.datetime.now(TZ).strftime("%Y%m%d"))
    frames: list[tuple[str, pd.DataFrame]] = []
    for exchange in ("SSE", "SZSE"):
        frame = client.frame(
            "trade_cal",
            {"exchange": exchange, "start_date": CAL_START, "end_date": calendar_end},
            dataset="trade_cal",
            partition=exchange,
        )
        _validate_calendar_frame(frame, exchange, CAL_START, calendar_end)
        frames.append((exchange, frame))
    written = []
    for exchange, frame in frames:
        meta = common.write_partition(
            frame,
            "trade_cal",
            exchange,
            "tushare",
            extra_meta={
                "query": {
                    "exchange": exchange,
                    "start_date": CAL_START,
                    "end_date": calendar_end,
                },
                "calendar_refresh": True,
            },
        )
        written.append({"exchange": exchange, "rows": meta["rows"], "end": calendar_end})
    return {"calendar_end": calendar_end, "partitions": written}


def disclosure_plan(
    client, days: Iterable[str]
) -> tuple[list[str], str | None, str | None]:
    """Codes whose statements were actually disclosed on ``days``.

    ``disclosure_date`` is the only affordable way to avoid 5,924 per-code
    statement calls on every run.  It is a separate upstream endpoint, not a
    new data source.

    The four statement APIs do not share one date filter: measured 2026-11-23,
    ``income``/``balancesheet``/``cashflow`` select on ``ann_date`` while
    ``fina_indicator`` selects on ``end_date`` (the report period).  The window
    therefore spans both the report period and the actual disclosure date.
    """
    codes: set[str] = set()
    window_dates: list[str] = []
    for day in days:
        fields, items = client.call(
            "disclosure_date",
            {"actual_date": day},
            dataset="disclosure_date",
            partition=day,
        )
        if len(items) >= 6000:
            raise IncrementalError(
                f"disclosure_date {day} 返回 {len(items)} 行，可能触到上游上限；"
                "请缩小窗口后重跑"
            )
        if "ts_code" not in fields:
            continue
        index = fields.index("ts_code")
        end_index = fields.index("end_date") if "end_date" in fields else None
        actual_index = fields.index("actual_date") if "actual_date" in fields else None
        for item in items:
            code = str(item[index] or "").strip()
            if code:
                codes.add(code)
            end = _norm_date(item[end_index]) if end_index is not None else None
            actual = _norm_date(item[actual_index]) if actual_index is not None else None
            for value in (end, actual or day):
                if value:
                    window_dates.append(value)
    return (
        sorted(codes),
        min(window_dates) if window_dates else None,
        max(window_dates) if window_dates else None,
    )


def disclosure_codes(client, days: Iterable[str]) -> list[str]:
    """Backwards-compatible code-only view of :func:`disclosure_plan`."""
    return disclosure_plan(client, days)[0]


def extended_window(dataset: str, partition: str, start: str, end: str) -> list[str]:
    meta = common.read_manifest(dataset, partition) or {}
    old = meta.get("study_window")
    if isinstance(old, (list, tuple)) and len(old) == 2 and all(old):
        return [min(str(old[0]), start), max(str(old[1]), end)]
    return [start, end]


def load_selected(partitions_by_dataset: dict[str, Iterable[str]]) -> dict[str, int]:
    """Load chosen partitions with INSERT OR REPLACE (idempotent)."""
    loaded: dict[str, int] = {}
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        for dataset, partitions in partitions_by_dataset.items():
            if dataset not in common.TABLE_PK:
                continue
            keys = sorted(set(partitions))
            if not keys:
                continue
            rows = common.load_partitions(con, dataset, keys)
            loaded[dataset] = int(rows)
            print(f"[load] {dataset}: {len(keys)} 分区 / {rows} 行", flush=True)
        con.execute("CHECKPOINT")
    return loaded


def _watermark_path() -> Path:
    return common.META_DIR / "incremental" / "watermark.json"


def read_watermark() -> dict[str, Any]:
    path = _watermark_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_watermark(payload: dict[str, Any]) -> None:
    path = _watermark_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    common.write_json_atomic(path, payload)


def update_watermark(
    *,
    market: dict[str, str] | None = None,
    finance_loaded: bool = False,
    index_weight_loaded: bool = False,
) -> dict[str, Any]:
    current = read_watermark()
    current["updated_at"] = common.utc_now()
    if market:
        current.setdefault("market", {}).update(market)
    if finance_loaded:
        current.setdefault("finance", {})["last_load"] = common.utc_now()
    if index_weight_loaded:
        current.setdefault("index_weight", {})["last_load"] = common.utc_now()
    write_watermark(current)
    return current


def _table_max(con, dataset: str) -> str | None:
    try:
        value = con.execute(f"SELECT max(trade_date) FROM {dataset}").fetchone()[0]
        return _norm_date(value)
    except Exception:
        return None


def _finance_parts_since(last_load: str | None) -> dict[str, list[str]]:
    """Financial partitions touched since the last successful load."""
    if not last_load:
        return {}
    last = dt.datetime.fromisoformat(last_load.replace("Z", "+00:00"))
    cutoff = last - dt.timedelta(minutes=5)
    parts: dict[str, list[str]] = {}
    for dataset in FINANCE_DATASETS:
        for partition in common.list_partitions(dataset):
            meta = common.read_manifest(dataset, partition) or {}
            stamp = meta.get("fetched_at")
            if not stamp:
                continue
            try:
                fetched = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if fetched >= cutoff:
                parts.setdefault(dataset, []).append(partition)
    return parts


def load_only_report(datasets: Iterable[str] | None = None) -> dict[str, int]:
    """``fetch --load-only --incremental``: load only post-watermark partitions."""
    requested = {d.strip() for d in (datasets or []) if d and d.strip()}
    if not requested or "all" in requested:
        requested = set(SUPPORTED_DATASETS)
    watermark = read_watermark()
    selected: dict[str, list[str]] = {}
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        for dataset in sorted(requested):
            if dataset not in common.TABLE_PK:
                continue
            if dataset in ("trade_cal", "stock_basic", "index_weight"):
                selected[dataset] = common.list_partitions(dataset)
                continue
            if dataset in MARKET_DATASETS:
                try:
                    db_dates = {
                        _norm_date(row[0])
                        for row in con.execute(
                            f"SELECT DISTINCT trade_date FROM {dataset}"
                        ).fetchall()
                    }
                except Exception:
                    db_dates = set()
                selected[dataset] = [
                    p
                    for p in common.list_partitions(dataset)
                    if p not in db_dates and _partition_present(dataset, p)
                ]
                continue
            if dataset in FINANCE_DATASETS:
                last = (watermark.get("finance") or {}).get("last_load")
                parts = _finance_parts_since(last)
                # No watermark means the first incremental load after a full
                # backfill. Loading every financial partition is slow but exact;
                # the caller sees the partition count in the log.
                selected[dataset] = (
                    list(parts.get(dataset, []))
                    if last
                    else common.list_partitions(dataset)
                )
                continue
            if dataset not in ("namechange",):
                selected[dataset] = common.list_partitions(dataset)
        loaded: dict[str, int] = {}
        for dataset, partitions in selected.items():
            if not partitions:
                continue
            rows = common.load_partitions(con, dataset, partitions)
            loaded[dataset] = int(rows)
            print(f"[load] {dataset}: {len(partitions)} 分区 / {rows} 行", flush=True)
        con.execute("CHECKPOINT")
        market: dict[str, str] = {}
        for dataset in MARKET_DATASETS:
            value = _table_max(con, dataset)
            if value:
                market[dataset] = value
    update_watermark(
        market={k: v for k, v in market.items() if v},
        finance_loaded=bool(set(FINANCE_DATASETS) & requested),
        index_weight_loaded="index_weight" in requested,
    )
    print(json.dumps({"loaded": loaded}, ensure_ascii=False, indent=2), flush=True)
    return loaded


def load_only(datasets: Iterable[str] | None = None) -> int:
    load_only_report(datasets)
    return 0


def _manifest_new_since(dataset: str, day: str, since: str) -> bool:
    meta = common.read_manifest(dataset, day) or {}
    stamp = meta.get("fetched_at")
    return bool(stamp and str(stamp) >= since)


def _run_market(client, now: dt.datetime, args: argparse.Namespace, summary: dict[str, Any]) -> int:
    from . import fetch as fetch_mod

    refresh = refresh_calendar(client, now.strftime("%Y%m%d"))
    summary["calendar"] = refresh
    calendar = calendar_frame()
    plans = market_plans(now, calendar)
    run_start = common.utc_now()
    source_delay = False
    errors: list[dict[str, Any]] = []

    for dataset, plan in plans.items():
        summary["datasets"][dataset] = plan.as_dict()
        if plan.is_noop:
            continue
        strict = strict_empty_dates_for(dataset, plan, now)
        fetcher = fetch_mod.Fetcher(
            client,
            plan.start,
            plan.end,
            resume=True,
            concurrency=args.concurrency,
            strict_empty_dates=strict,
            provisional_empty=True,
        )
        spec = fetch_mod.DATASETS[dataset]
        if spec.strategy == "range":
            done, failures = fetcher.fetch_range(spec, args.concurrency)
        else:
            done, failures = fetcher.fetch_trade_date(spec, args.concurrency)
        summary["datasets"][dataset]["done"] = done
        summary["datasets"][dataset]["failures"] = len(failures)
        for failure in failures:
            if failure.get("source_delay"):
                source_delay = True
            else:
                errors.append(failure)
        plan_parts = [
            p for p in common.list_partitions(dataset) if plan.start <= p <= plan.end
        ]
        summary["new_partitions"] += sum(
            1 for p in plan_parts if _manifest_new_since(dataset, p, run_start)
        )

    # stock_basic is small (3 calls) and changes rarely; refresh it every day.
    basic = fetch_mod.Fetcher(
        client,
        CAL_START,
        now.strftime("%Y%m%d"),
        resume=False,
        concurrency=args.concurrency,
    )
    done, basic_failures = basic.fetch_stock_basic(fetch_mod.DATASETS["stock_basic"])
    summary["datasets"]["stock_basic"] = {"done": done, "failures": len(basic_failures)}
    errors.extend(basic_failures)

    # Load by the DuckDB watermark, not by this run's fetch window: a previous
    # run may have written raw partitions and then crashed before loading them.
    # `load_only` picks up every raw partition newer than the table.
    summary["loaded"] = load_only_report(
        list(MARKET_DATASETS) + ["stock_basic", "trade_cal"]
    )

    if errors:
        summary["errors"] = errors[:20]
        return 1
    if source_delay:
        summary["source_delay"] = True
        return 75
    return 0


def _run_finance(client, now: dt.datetime, args: argparse.Namespace, summary: dict[str, Any]) -> int:
    from . import fetch as fetch_mod

    plan = finance_plan(now, overlap_days=args.overlap_days)
    summary["datasets"]["finance"] = plan.as_dict()
    if plan.is_noop:
        return 0
    codes, window_start, window_end = disclosure_plan(client, plan.days)
    # Span both the report period and the actual disclosure date: the four
    # statement APIs use different date filters (see disclosure_plan).
    fetch_start = window_start or plan.start
    fetch_end = window_end or plan.end
    summary["disclosure_codes"] = len(codes)
    summary["finance_fetch_window"] = [fetch_start, fetch_end]
    run_start = common.utc_now()
    errors: list[dict[str, Any]] = []

    if codes:
        for dataset in ("income", "balancesheet", "cashflow", "fina_indicator"):
            fetcher = fetch_mod.Fetcher(
                client,
                fetch_start,
                fetch_end,
                resume=False,
                concurrency=args.concurrency,
                merge_existing=True,
            )
            fetcher.codes = codes
            done, failures = fetcher.fetch_financial(
                fetch_mod.DATASETS[dataset], args.concurrency
            )
            summary["datasets"][dataset] = {"done": done, "failures": len(failures)}
            summary["new_partitions"] += sum(
                1
                for code in codes
                if _manifest_new_since(dataset, code, run_start)
            )
            errors.extend(failures)

    dividend = _fetch_dividend_days(client, plan, run_start)
    summary["datasets"]["dividend"] = {
        "done": dividend["partitions"],
        "rows": dividend["rows"],
        "errors": dividend["errors"],
    }
    errors.extend(dividend["errors"])
    summary["new_partitions"] += dividend["partitions"]

    summary["loaded"] = load_only_report(list(FINANCE_DATASETS))
    if errors:
        summary["errors"] = errors[:20]
        return 1
    return 0


def _fetch_dividend_days(client, plan: Plan, run_start: str) -> dict[str, Any]:
    """Cross-sectional dividend fetch by ann_date, then merge per ts_code."""
    from . import fetch as fetch_mod

    rows_by_code: dict[str, list[pd.DataFrame]] = {}
    errors: list[dict[str, Any]] = []
    for day in plan.days:
        try:
            fields, items = client.call(
                "dividend",
                {"ann_date": day},
                dataset="dividend",
                partition=day,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"dataset": "dividend", "partition": day, "error": str(exc)})
            continue
        if len(items) >= 2000:
            errors.append(
                {
                    "dataset": "dividend",
                    "partition": day,
                    "error": f"ann_date={day} 返回 {len(items)} 行，可能触到 2000 行上限",
                }
            )
            continue
        if not items:
            continue
        frame = pd.DataFrame(items, columns=fields)
        if "ts_code" not in frame.columns:
            continue
        for code, group in frame.groupby("ts_code", dropna=True):
            rows_by_code.setdefault(str(code), []).append(group)

    partitions = 0
    rows = 0
    for code, frames in rows_by_code.items():
        new = pd.concat(frames, ignore_index=True, sort=False)
        merged = fetch_mod.merge_existing_frame("dividend", code, new)
        merged, dropped = common.drop_null_pk_rows(merged, "dividend")
        if merged.empty:
            continue
        meta = common.write_partition(
            merged,
            "dividend",
            code,
            "tushare",
            extra_meta={
                "query": {"ann_date": list(plan.days), "mode": "incremental_merge"},
                "study_window": extended_window("dividend", code, plan.start, plan.end),
                "incremental_merge": True,
                "rows_dropped_null_pk": dropped,
            },
        )
        partitions += 1
        rows += int(meta["rows"])
    return {
        "partitions": partitions,
        "rows": rows,
        "codes": sorted(rows_by_code),
        "errors": errors,
    }


def _run_index_weight(client, now: dt.datetime, args: argparse.Namespace, summary: dict[str, Any]) -> int:
    from . import fetch as fetch_mod

    plan = index_weight_plan(now)
    summary["datasets"]["index_weight"] = plan.as_dict()
    if plan.is_noop:
        return 0
    fetcher = fetch_mod.Fetcher(
        client,
        plan.start,
        plan.end,
        resume=False,
        concurrency=args.concurrency,
        merge_existing=True,
    )
    done, failures = fetcher.fetch_index_weight(
        fetch_mod.DATASETS["index_weight"], args.concurrency
    )
    summary["datasets"]["index_weight"]["done"] = done
    summary["datasets"]["index_weight"]["failures"] = len(failures)
    summary["loaded"] = load_only_report(["index_weight"])
    if any(failure.get("source_delay") for failure in failures):
        summary["source_delay"] = True
        return 75
    if failures:
        summary["errors"] = failures[:20]
        return 1
    if done == 0:
        # No index produced data: treat it as an upstream delay rather than a
        # silent success. The next 20:30 trigger can retry.
        summary["source_delay"] = True
        return 75
    return 0


def infer_group(datasets: Iterable[str] | None, group: str | None) -> str:
    if group:
        return group
    requested = {d.strip() for d in (datasets or []) if d and d.strip()}
    if not requested or "all" in requested:
        return "all"
    if requested <= set(MARKET_DATASETS) | {"stock_basic", "trade_cal"}:
        return "market"
    if requested <= set(FINANCE_DATASETS):
        return "finance"
    if requested <= {"index_weight"}:
        return "index_weight"
    raise IncrementalError(f"无法从数据集推断增量分组: {sorted(requested)}")


def run(args: argparse.Namespace) -> int:
    """Entry point for ``fetch --incremental``."""
    from .tushare_client import TushareClient

    common.ensure_layout()
    now = parse_now(getattr(args, "now", None))
    group = infer_group(getattr(args, "dataset", None), getattr(args, "group", None))
    summary: dict[str, Any] = {
        "group": group,
        "started_at": common.utc_now(),
        "now": now.isoformat(),
        "datasets": {},
        "new_partitions": 0,
        "errors": [],
    }
    if group in ("market", "index_weight"):
        try:
            frame = calendar_frame()
            days = open_days(frame)
            today = now.strftime("%Y%m%d")
            calendar_dates = frame["cal_date"].dropna()
            calendar_max = (
                str(calendar_dates.max()).replace("-", "") if not calendar_dates.empty else ""
            )
            if calendar_max and today <= calendar_max and today not in set(days):
                summary.update(
                    {
                        "finished_at": common.utc_now(),
                        "exit_code": 0,
                        "reason": f"{today} 非交易日，直接退出 0",
                    }
                )
                print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
                return 0
        except IncrementalError:
            # No usable calendar yet: refresh it below with the client.
            pass
    client = TushareClient(
        rpm=getattr(args, "rpm", 195.0),
        retries=getattr(args, "retries", 2),
        concurrency=getattr(args, "concurrency", 8),
    )

    codes: list[int] = []
    if group in ("market", "all"):
        codes.append(_run_market(client, now, args, summary))
    if group in ("finance", "all"):
        codes.append(_run_finance(client, now, args, summary))
    if group in ("index_weight", "all"):
        codes.append(_run_index_weight(client, now, args, summary))
    summary["finished_at"] = common.utc_now()
    non_delay = [code for code in codes if code not in (0, 75)]
    summary["exit_code"] = 1 if non_delay else (75 if 75 in codes else 0)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary["exit_code"]


def check_day(day: str) -> int:
    common.ensure_layout()
    try:
        frame = calendar_frame()
        days = open_days(frame)
        calendar_dates = frame["cal_date"].dropna()
        if calendar_dates.empty:
            print(json.dumps({"date": day, "error": "交易日历为空"}, ensure_ascii=False))
            return 2
        calendar_max = str(calendar_dates.max()).replace("-", "")
        if day > calendar_max:
            print(
                json.dumps(
                    {
                        "date": day,
                        "trading_day": None,
                        "error": f"交易日历只覆盖到 {calendar_max}，请先 refresh-calendar",
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        opened = day in set(days)
    except IncrementalError as exc:
        print(json.dumps({"date": day, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"date": day, "trading_day": opened}, ensure_ascii=False))
    return 0 if opened else 1


def plan_payload(now: dt.datetime, group: str = "all") -> dict[str, Any]:
    frame = calendar_frame()
    payload: dict[str, Any] = {"now": now.isoformat(), "calendar_max": max(open_days(frame))}
    if group in ("market", "all"):
        payload["market"] = {k: v.as_dict() for k, v in market_plans(now, frame).items()}
    if group in ("finance", "all"):
        payload["finance"] = finance_plan(now).as_dict()
    if group in ("index_weight", "all"):
        payload["index_weight"] = index_weight_plan(now, frame).as_dict()
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.etl.incremental",
        description="quantdb 增量计划与交易日检查",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-day", help="检查某日是否交易日；交易日退出 0，否则 1")
    check.add_argument("--date", required=True, help="YYYYMMDD")

    plan = sub.add_parser("plan", help="打印增量区间（不发起上游调用）")
    plan.add_argument("--group", choices=GROUPS, default="all")
    plan.add_argument("--now", help="调试用 ISO 时间")

    refresh = sub.add_parser("refresh-calendar", help="刷新 trade_cal 到指定日期或今天")
    refresh.add_argument("--end", help="YYYYMMDD，默认今天")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check-day":
        return check_day(args.date)
    if args.command == "plan":
        print(
            json.dumps(plan_payload(parse_now(args.now), args.group), ensure_ascii=False, indent=2)
        )
        return 0
    if args.command == "refresh-calendar":
        from .tushare_client import TushareClient

        client = TushareClient(rpm=30, retries=2, concurrency=1)
        print(json.dumps(refresh_calendar(client, args.end), ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"未知命令: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
