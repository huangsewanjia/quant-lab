"""Regression tests for trading days that upstream returns no rows for.

Some upstream datasets have no history for the early years (``stk_limit``
before ~2020, ``suspend_d`` early on). Those trading days must still end up
with a manifest, otherwise the resume logic treats them as "never fetched",
re-probes the same empty range on every run, and the backfill can never reach
completion - the slice spends its whole budget on days that will never
produce rows.
"""

from __future__ import annotations

import pandas as pd

from db.etl import fetch


class _PartialClient:
    """Returns rows for some trading days only; the rest come back empty."""

    def __init__(self, days_with_rows: list[str]) -> None:
        self.days_with_rows = days_with_rows
        self.calls: list[dict] = []

    def call(self, api, params, **kwargs):
        self.calls.append(dict(params))
        lo = str(params.get("start_date", ""))
        hi = str(params.get("end_date", ""))
        rows = [
            ["600000.SH", day, 10.5]
            for day in self.days_with_rows
            if lo <= day <= hi
        ]
        if not rows:
            return ["ts_code", "trade_date", "close"], []
        return ["ts_code", "trade_date", "close"], rows


class _EmptyClient:
    """Every window comes back empty."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, api, params, **kwargs):
        self.calls.append(dict(params))
        return ["ts_code", "trade_date", "close"], []


def _seed_calendar(qdb, days: list[str]) -> None:
    qdb.write_partition(
        pd.DataFrame(
            {
                "exchange": ["SSE"] * len(days),
                "cal_date": days,
                "is_open": [1] * len(days),
                "pretrade_date": days,
            }
        ),
        "trade_cal",
        "SSE",
        "test",
    )


def test_a_day_without_rows_gets_an_empty_partition(qdb):
    """A fetched day with no upstream rows is published as an empty partition."""
    days = ["20200102", "20200103", "20200106"]
    _seed_calendar(qdb, days)
    client = _PartialClient(["20200103"])
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)

    assert failures == []
    assert done == len(days)
    for day in days:
        assert qdb.manifest_ok("daily", day), f"{day} has no manifest"

    # The day that does carry rows holds them.
    frame = pd.read_parquet(qdb.partition_file("daily", "20200103"))
    assert len(frame) == 1
    assert frame["close"].iloc[0] == 10.5

    # The days without rows are published empty, with a manifest that says so.
    for day in ("20200102", "20200106"):
        empty = pd.read_parquet(qdb.partition_file("daily", day))
        assert len(empty) == 0
        manifest = qdb.read_manifest("daily", day)
        assert manifest["rows"] == 0
        assert manifest.get("empty_upstream") is True


def test_an_entirely_empty_window_still_publishes_every_day(qdb):
    """The upstream has no stk_limit history before ~2020."""
    days = ["20060104", "20060105", "20060106"]
    _seed_calendar(qdb, days)
    client = _EmptyClient()
    fetcher = fetch.Fetcher(client, "20060101", "20060131", resume=True)
    done, failures = fetcher.fetch_range(fetch.DATASETS["stk_limit"], concurrency=1)

    assert failures == []
    assert done == len(days)
    for day in days:
        assert qdb.manifest_ok("stk_limit", day)
        assert len(pd.read_parquet(qdb.partition_file("stk_limit", day))) == 0


def test_empty_partitions_are_not_refetched(qdb):
    """The second run must not call the upstream again for an empty day."""
    days = ["20060104", "20060105"]
    _seed_calendar(qdb, days)
    client = _EmptyClient()
    fetcher = fetch.Fetcher(client, "20060101", "20060131", resume=True)
    fetcher.fetch_range(fetch.DATASETS["stk_limit"], concurrency=1)
    first_calls = len(client.calls)

    again = fetch.Fetcher(client, "20060101", "20060131", resume=True)
    done, failures = again.fetch_range(fetch.DATASETS["stk_limit"], concurrency=1)

    assert failures == []
    assert done == 0
    assert len(client.calls) == first_calls, "empty days were re-fetched"


def test_a_failed_window_still_produces_no_partition(qdb):
    """The empty-partition rule must not resurrect the review's finding #1."""

    class _FailingClient:
        def call(self, api, params, **kwargs):
            raise RuntimeError("HTTP 502 upstream")

    days = ["20200102", "20200103"]
    _seed_calendar(qdb, days)
    fetcher = fetch.Fetcher(_FailingClient(), "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)

    assert done == 0
    assert failures
    for day in days:
        assert not qdb.manifest_ok("daily", day)
        assert not qdb.partition_file("daily", day).exists()


def test_an_empty_trade_date_response_is_not_a_failure(qdb):
    """fetch_trade_date must publish an empty partition, not a failure record."""
    days = ["20200102"]
    _seed_calendar(qdb, days)

    class _EmptyClient:
        def call(self, api, params, **kwargs):
            return ["ts_code", "trade_date", "close"], []

        def frame(self, api, params, **kwargs):
            return pd.DataFrame(columns=["ts_code", "trade_date", "close"])

    fetcher = fetch.Fetcher(_EmptyClient(), "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_trade_date(fetch.DATASETS["index_daily"], concurrency=1)

    assert failures == []
    assert done == 1
    assert qdb.manifest_ok("index_daily", "20200102")
    assert len(pd.read_parquet(qdb.partition_file("index_daily", "20200102"))) == 0


def test_trading_days_in_uses_the_calendar(qdb):
    days = ["20200102", "20200103", "20200106", "20200107"]
    _seed_calendar(qdb, days)
    fetcher = fetch.Fetcher(_EmptyClient(), "20200101", "20200131", resume=True)
    assert fetcher.trading_days_in("20200103", "20200106") == ["20200103", "20200106"]
    assert fetcher.trading_days_in("20200101", "20200101") == []
    assert fetcher.trading_days_in("20200101", "20201231") == days


def test_an_index_without_constituent_data_is_not_a_permanent_failure(qdb):
    """An all-empty index_weight sweep must not block the dataset forever."""
    _seed_calendar(qdb, ["20200102"])

    class _EmptyClient:
        def call(self, api, params, **kwargs):
            return ["index_code", "con_code", "trade_date"], []

        def fetch_window(self, api, params, **kwargs):
            return pd.DataFrame(columns=["index_code", "con_code", "trade_date"])

    fetcher = fetch.Fetcher(_EmptyClient(), "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_index_weight(fetch.DATASETS["index_weight"], concurrency=1)

    assert failures == [], "an empty upstream sweep must not be a failure"
    assert done == len(fetch.INDEX_UNIVERSE)
    for index_code in fetch.INDEX_UNIVERSE:
        assert qdb.manifest_ok("index_weight", index_code)
        manifest = qdb.read_manifest("index_weight", index_code)
        assert manifest["rows"] == 0
        assert manifest.get("months_with_rows") == 0
        assert manifest.get("empty_upstream") is True
