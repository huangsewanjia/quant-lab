from __future__ import annotations

import pandas as pd

from db.etl import common, fetch


class FinFetcher:
    def __init__(self):
        self.calls = []
        self.end_date = "20200331"

    def call(self, method, dataset, *, trade_date=None, ts_code=None, **kwargs):
        self.calls.append((method, dataset, ts_code, dict(kwargs)))
        return pd.DataFrame(
            [
                {
                    "ts_code": ts_code,
                    "ann_date": "20200430",
                    "f_ann_date": "20200430",
                    "end_date": self.end_date,
                    "report_type": "1",
                    "total_revenue": 100.0,
                }
            ]
        )


def test_financial_call_has_single_ts_code_and_skips(qdb):
    fetcher = FinFetcher()
    done, failures = fetch.fetch_financials(
        fetcher, ["600000.SH"], ["income"], None, None, 1
    )
    assert failures == []
    assert done == 1
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][2] == "600000.SH"

    done, failures = fetch.fetch_financials(
        fetcher, ["600000.SH"], ["income"], None, None, 1
    )
    assert failures == []
    assert done == 1
    assert len(fetcher.calls) == 1


def test_financial_new_range_merges_old_partition(qdb):
    fetcher = FinFetcher()
    fetch.fetch_financials(fetcher, ["600000.SH"], ["income"], None, None, 1)
    fetcher.end_date = "20190331"
    fetch.fetch_financials(
        fetcher, ["600000.SH"], ["income"], "20190101", "20191231", 1
    )
    frame = common.read_partition_frame("income", "600000.SH")
    assert frame is not None
    assert len(frame) == 2
