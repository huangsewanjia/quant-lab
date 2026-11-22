from __future__ import annotations

import datetime as dt

import pandas as pd

from db.etl import common, fetch


class FakeFetcher:
    def __init__(self):
        self.calls = []

    def call(self, method, dataset, *, trade_date=None, ts_code=None, **kwargs):
        self.calls.append(trade_date)
        return pd.DataFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "pre_close": 10.0,
                    "change": 0.5,
                    "pct_chg": 5.0,
                    "vol": 1000.0,
                    "amount": 10500.0,
                }
            ]
        )


def test_completed_partition_is_skipped(qdb):
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        frame = pd.DataFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": dt.date(2020, 1, 2),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "pre_close": 10.0,
                    "change": 0.5,
                    "pct_chg": 5.0,
                    "vol": 1000.0,
                    "amount": 10500.0,
                }
            ]
        )
        work = common.conform_frame(frame, "daily", con, "test")
    common.write_partition(work, "daily", "20200102", "test")

    fetcher = FakeFetcher()
    done, failures = fetch.fetch_by_trade_date(
        fetcher, "daily", "daily", ["20200102", "20200103"], concurrency=1
    )
    assert fetcher.calls == ["20200103"]
    assert done == 1
    assert failures == []
