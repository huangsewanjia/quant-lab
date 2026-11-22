from __future__ import annotations

import pandas as pd

from db.etl import fetch


class FakePro:
    def __init__(self):
        self.kwargs = {}

    def income(self, **kwargs):
        self.kwargs = kwargs
        return pd.DataFrame()

    def daily(self, **kwargs):
        self.kwargs = kwargs
        return pd.DataFrame()


def test_tushare_call_forwards_named_filters(qdb, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    fetcher = fetch.TushareFetcher(rpm=1_000_000, retries=0)
    pro = FakePro()
    fetcher._local.pro = pro
    fetcher.call("income", "income", ts_code="600000.SH", start_date="20200101")
    assert pro.kwargs["ts_code"] == "600000.SH"
    assert pro.kwargs["start_date"] == "20200101"
    fetcher.call("daily", "daily", trade_date="20200102")
    assert pro.kwargs["trade_date"] == "20200102"
