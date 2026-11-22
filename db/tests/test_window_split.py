"""Window logic: financial ann_date windows and the cap-aware split."""

from __future__ import annotations

import pandas as pd

from db.etl import fetch
from db.etl.tushare_client import TushareClient


def test_code_windows_split_at_2015_2016():
    windows = fetch.code_windows("20060101", None, "20060101", "20261121")
    assert windows == [("20060101", "20151231"), ("20160101", "20261121")]


def test_code_windows_for_a_recent_listing():
    windows = fetch.code_windows("20200101", None, "20060101", "20261121")
    assert windows == [("20200101", "20261121")]


def test_code_windows_for_a_delisted_stock():
    windows = fetch.code_windows("20060101", "20100501", "20060101", "20261121")
    assert windows == [("20060101", "20100501")]


def test_code_windows_empty_when_out_of_range():
    assert fetch.code_windows("20220101", None, "20060101", "20261121") == [
        ("20220101", "20261121")
    ]


class _CappedClient:
    """Fake upstream: a wide window is truncated at the cap, a narrow one is not."""

    def __init__(self, cap: int = 100) -> None:
        self.cap = cap
        self.calls: list[dict] = []

    def call(self, api, params, **kwargs):
        self.calls.append(dict(params))
        lo = int(str(params.get("start_date", "20200101")))
        hi = int(str(params.get("end_date", "20201231")))
        # one row per day in the window
        days = pd.date_range(str(lo), str(hi), freq="D")
        rows = []
        for day in days:
            for _ in range(3):
                rows.append(
                    {
                        "ts_code": "600000.SH",
                        "ann_date": day.strftime("%Y%m%d"),
                        "end_date": day.strftime("%Y%m%d"),
                        "report_type": "1",
                    }
                )
        if len(rows) > self.cap:
            rows = rows[: self.cap]
        fields = list(rows[0].keys()) if rows else ["ts_code", "ann_date", "end_date", "report_type"]
        items = [[row[f] for f in fields] for row in rows]
        return fields, items


def test_fetch_window_splits_until_below_the_cap():
    client = _CappedClient(cap=100)
    tushare = TushareClient.__new__(TushareClient)  # no token / no session needed
    tushare.call = client.call  # type: ignore[method-assign]
    frame = tushare.fetch_window(
        "balancesheet",
        {"ts_code": "600000.SH", "start_date": "20200101", "end_date": "20201231"},
        dataset="balancesheet",
        cap=100,
    )
    assert len(client.calls) > 1, "a truncated response must be split"
    # every announced day of the window is present exactly once
    assert set(frame["ann_date"]) == {
        d.strftime("%Y%m%d") for d in pd.date_range("20200101", "20201231", freq="D")
    }


def test_fetch_window_raises_when_it_cannot_split():
    class _NoWindowClient(_CappedClient):
        def call(self, api, params, **kwargs):
            self.calls.append(dict(params))
            return ["ts_code"], [["600000.SH"]] * 100

    client = _NoWindowClient()
    tushare = TushareClient.__new__(TushareClient)
    tushare.call = client.call  # type: ignore[method-assign]
    try:
        tushare.fetch_window(
            "daily", {"trade_date": "20200102"}, dataset="daily", cap=100
        )
    except Exception as exc:  # noqa: BLE001
        assert "触顶" in str(exc) or "截断" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected a truncation error")


def test_listing_density_tracks_the_listing_dates():
    density = fetch.ListingDensity(["20060101", "20070101", "20100101"])
    assert density.listed_by("20051231") == 0
    assert density.listed_by("20060101") == 1
    assert density.listed_by("20091231") == 2
    assert density.listed_by("20100101") == 3
    # a window in 2008 has two listed stocks a day
    assert density.estimate("20080101", "20080131") == 62
    # a window after 2010 has three listed stocks a day
    assert density.estimate("20110101", "20110110") == 30


def test_split_window_fanout_is_driven_by_the_estimate():
    # 3 days with an estimate of 9000 rows against a 6000 row cap needs two
    # windows, not one.
    density = fetch.ListingDensity(["20060101"] * 3000)
    estimate = density.estimate("20060101", "20060103")
    parts = int(-(-estimate // int(6000 * 0.9)))
    assert parts >= 2
    children = fetch.split_window({"start_date": "20060101", "end_date": "20060103"}, parts=parts)
    assert len(children) == min(parts, 3)
    assert children[0]["start_date"] == "20060101"
    assert children[-1]["end_date"] == "20060103"


def test_split_window_refuses_a_single_day():
    assert fetch.split_window({"start_date": "20200102", "end_date": "20200102"}) == []
