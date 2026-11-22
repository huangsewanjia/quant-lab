"""Regression tests for the second independent review (4 blocking findings).

B1  a slice-budget expiry inside a financial code's window tree must NOT
    produce a partition (it would claim completeness while holding only
    part of the history, and the resume would skip it for good);
B2  a failed namechange year / index_weight month must NOT produce a
    partition either;
B3  --time-budget must actually stop the work: submitting every task up
    front and breaking out of the result loop does not, because the
    executor's shutdown(wait=True) drains the queue;
B4  a partition fetched for a narrower --start/--end window must not be
    resumed for a wider window.
"""

from __future__ import annotations

import pandas as pd

from db.etl import common, fetch


class _CountingClient:
    """Counts calls and returns a fixed frame."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.calls: list[dict] = []
        self.rows = rows or []

    def call(self, api, params, **kwargs):
        self.calls.append(dict(params))
        return ["ts_code", "end_date", "report_type", "ann_date", "ebit"], [
            [r["ts_code"], r["end_date"], r["report_type"], r["ann_date"], r["ebit"]]
            for r in self.rows
        ]

    def frame(self, api, params, **kwargs):
        self.calls.append(dict(params))
        return pd.DataFrame(
            self.rows,
            columns=["ts_code", "end_date", "report_type", "ann_date", "ebit"],
        )

    def fetch_window(self, api, params, **kwargs):
        self.calls.append(dict(params))
        return pd.DataFrame(
            self.rows,
            columns=["ts_code", "end_date", "report_type", "ann_date", "ebit"],
        )


def _seed_universe(qdb) -> None:
    qdb.write_partition(
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "000002.SZ"],
                "list_date": ["19991110", "19910129"],
                "name": ["浦发银行", "万科A"],
                "list_status": ["L", "L"],
            }
        ),
        "stock_basic",
        "L",
        "test",
    )


# --------------------------------------------------------------------------- #
# B1 - budget expiry inside a financial window tree
# --------------------------------------------------------------------------- #


def test_a_cut_short_financial_window_writes_no_partition(qdb):
    """The partition must not be published when the tree was not exhausted."""
    _seed_universe(qdb)
    client = _CountingClient(
        [{"ts_code": "600000.SH", "end_date": "20151231", "report_type": "1", "ann_date": "20160420", "ebit": 1.0}]
    )
    # A deadline in the past makes every window tree stop before it is walked.
    fetcher = fetch.Fetcher(
        client, "20060101", "20261121", resume=True, deadline=0.0
    )
    done, failures = fetcher.fetch_financial(fetch.DATASETS["income"], concurrency=1)

    assert done == 0
    assert failures == [], "a budget expiry is an interruption, not an upstream failure"
    for code in ("600000.SH", "000002.SZ"):
        assert not qdb.manifest_ok("income", code)
        assert not qdb.partition_file("income", code).exists()


def test_a_complete_financial_window_is_published(qdb):
    _seed_universe(qdb)
    client = _CountingClient(
        [{"ts_code": "600000.SH", "end_date": "20151231", "report_type": "1", "ann_date": "20160420", "ebit": 1.0}]
    )
    fetcher = fetch.Fetcher(client, "20060101", "20261121", resume=True)
    done, failures = fetcher.fetch_financial(fetch.DATASETS["income"], concurrency=1)

    assert failures == []
    assert done == 2
    assert qdb.manifest_ok("income", "600000.SH")


# --------------------------------------------------------------------------- #
# B3 - the time budget actually stops the work
# --------------------------------------------------------------------------- #


def test_an_expired_budget_stops_the_concurrent_path(qdb):
    """Submitting everything up front kept the whole dataset running."""
    _seed_universe(qdb)
    client = _CountingClient(
        [{"ts_code": "600000.SH", "end_date": "20151231", "report_type": "1", "ann_date": "20160420", "ebit": 1.0}]
    )
    fetcher = fetch.Fetcher(
        client, "20060101", "20261121", resume=True, deadline=0.0, concurrency=8
    )
    fetcher.fetch_financial(fetch.DATASETS["income"], concurrency=8)
    assert client.calls == [], f"{len(client.calls)} calls were made after the budget expired"


def test_run_bounded_stops_between_batches(qdb):
    handled: list[str] = []

    def handle(item: str) -> None:
        handled.append(item)

    fetcher = fetch.Fetcher(_CountingClient(), "20060101", "20261121", deadline=0.0, concurrency=4)
    fetcher.run_bounded([str(i) for i in range(100)], handle, "test")
    assert handled == []


def test_run_bounded_processes_everything_without_a_deadline(qdb):
    handled: list[str] = []
    fetcher = fetch.Fetcher(_CountingClient(), "20060101", "20261121", concurrency=4)
    fetcher.run_bounded([str(i) for i in range(50)], handled.append, "test")
    assert sorted(handled) == sorted(str(i) for i in range(50))


# --------------------------------------------------------------------------- #
# B4 - a narrow window must not be resumed for a wider one
# --------------------------------------------------------------------------- #


def test_a_narrow_window_partition_is_not_resumed_for_a_wider_run(qdb):
    _seed_universe(qdb)
    client = _CountingClient(
        [{"ts_code": "600000.SH", "end_date": "20201231", "report_type": "1", "ann_date": "20210420", "ebit": 1.0}]
    )
    narrow = fetch.Fetcher(client, "20200101", "20201231", resume=True)
    narrow.fetch_financial(fetch.DATASETS["income"], concurrency=1)
    assert qdb.manifest_ok("income", "600000.SH")

    wide = fetch.Fetcher(client, "20060101", "20261121", resume=True)
    assert wide.resumable("income", "600000.SH") is False
    assert wide.resumable("income", "000002.SZ") is False


def test_the_same_window_is_resumable(qdb):
    _seed_universe(qdb)
    client = _CountingClient(
        [{"ts_code": "600000.SH", "end_date": "20201231", "report_type": "1", "ann_date": "20210420", "ebit": 1.0}]
    )
    fetcher = fetch.Fetcher(client, "20200101", "20201231", resume=True)
    fetcher.fetch_financial(fetch.DATASETS["income"], concurrency=1)
    again = fetch.Fetcher(client, "20200101", "20201231", resume=True)
    assert again.resumable("income", "600000.SH") is True


def test_window_scoped_datasets_are_declared():
    for dataset in ("income", "balancesheet", "cashflow", "fina_indicator", "dividend"):
        assert dataset in common.WINDOW_SCOPED_DATASETS
    assert "daily" not in common.WINDOW_SCOPED_DATASETS


# --------------------------------------------------------------------------- #
# B2 - a failed window in namechange / index_weight
# --------------------------------------------------------------------------- #


def test_a_failed_namechange_year_writes_no_partition(qdb):
    _seed_universe(qdb)

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def fetch_window(self, api, params, **kwargs):
            self.calls.append(dict(params))
            if str(params.get("start_date", "")).startswith("2015"):
                raise RuntimeError("HTTP 502 upstream")
            return pd.DataFrame(
                [{"ts_code": "600000.SH", "name": "浦发银行", "start_date": "20100101", "end_date": "20101231"}]
            )

    client = _FlakyClient()
    fetcher = fetch.Fetcher(client, "20140101", "20151231", resume=True)
    done, failures = fetcher.fetch_namechange(fetch.DATASETS["namechange"], concurrency=1)

    assert done == 0
    assert failures, "the failed year must be recorded"
    assert not qdb.manifest_ok("namechange", "600000.SH")
    assert not qdb.partition_file("namechange", "600000.SH").exists()


def test_a_failed_index_weight_month_writes_no_partition(qdb):
    class _FlakyClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def fetch_window(self, api, params, **kwargs):
            self.calls.append(dict(params))
            if str(params.get("start_date", "")).startswith("201501"):
                raise RuntimeError("HTTP 502 upstream")
            return pd.DataFrame(
                [{"index_code": "000300.SH", "con_code": "600000.SH", "trade_date": "20140228", "weight": 1.0}]
            )

    client = _FlakyClient()
    fetcher = fetch.Fetcher(client, "20140101", "20151231", resume=True)
    done, failures = fetcher.fetch_index_weight(fetch.DATASETS["index_weight"], concurrency=1)

    assert done == 0
    assert failures, "the failed month must be recorded"
    for index_code in fetch.INDEX_UNIVERSE:
        assert not qdb.manifest_ok("index_weight", index_code)
