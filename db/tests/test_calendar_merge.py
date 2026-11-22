from __future__ import annotations

import pandas as pd

from db.etl import common, fetch


class CalendarFetcher:
    def call(self, method, dataset, *, trade_date=None, ts_code=None, **kwargs):
        dates = pd.date_range(kwargs["start_date"], kwargs["end_date"], freq="D")
        return pd.DataFrame(
            {
                "exchange": "SSE",
                "cal_date": dates.strftime("%Y%m%d"),
                "is_open": 1,
                "pretrade_date": None,
            }
        )


def test_calendar_ranges_are_merged(qdb):
    fetcher = CalendarFetcher()
    fetch.ensure_calendar(fetcher, "20200101", "20200131")
    fetch.ensure_calendar(fetcher, "20210101", "20210131")
    frame = common.read_partition_frame("trade_cal", "calendar")
    assert frame is not None
    assert len(frame) == 62
    years = sorted(pd.to_datetime(frame["cal_date"]).dt.year.unique().tolist())
    assert years == [2020, 2021]
