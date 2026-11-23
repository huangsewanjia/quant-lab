"""Weekly report must detect a stale dataset tail, not only interior holes."""

from __future__ import annotations

import pandas as pd

from db.etl import incremental, weekly


def test_calendar_gap_uses_last_completed_close(qdb):
    qdb.write_partition(
        pd.DataFrame(
            {
                "exchange": ["SSE"] * 3,
                "cal_date": ["20200102", "20200103", "20200106"],
                "is_open": [1, 1, 1],
                "pretrade_date": ["20200102", "20200102", "20200103"],
            }
        ),
        "trade_cal",
        "SSE",
        "test",
    )
    qdb.write_partition(
        pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20200102"], "close": [10.5]}),
        "daily",
        "20200102",
        "test",
    )
    gaps = weekly._calendar_gaps(
        weekly._raw_stats(),
        now=incremental.parse_now("2020-01-06T19:00:00+08:00"),
    )
    assert gaps["daily"] == 2
