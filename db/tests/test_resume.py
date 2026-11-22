"""Resume contract: a partition with a verified manifest is never re-fetched."""

from __future__ import annotations

import pandas as pd

from db.etl import common, fetch


class FakeClient:
    """Records every upstream call so the test can assert what was skipped."""

    def __init__(self, days: list[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.days = sorted(days or [])

    def frame(self, api, params, **kwargs):
        self.calls.append((api, dict(params)))
        return pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": [params.get("trade_date", "20200102")],
                "close": [10.5],
            }
        )

    def call(self, api, params, **kwargs):
        self.calls.append((api, dict(params)))
        lo = str(params.get("start_date", "20200101"))
        hi = str(params.get("end_date", "20200131"))
        days = [day for day in self.days if lo <= day <= hi]
        fields = ["ts_code", "trade_date", "close"]
        items = [["600000.SH", day, 10.5] for day in days]
        return fields, items


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


def _seed_daily(qdb, day: str) -> None:
    qdb.write_partition(
        pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": [day], "close": [10.5]}),
        "daily",
        day,
        "test",
    )


def test_completed_partition_is_not_re_fetched(qdb):
    days = ["20200102", "20200103", "20200106"]
    _seed_calendar(qdb, days)
    _seed_daily(qdb, "20200102")
    client = FakeClient(days)
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert failures == []
    assert done == 2, "the partition with a verified manifest must be skipped"
    for day in days:
        assert qdb.manifest_ok("daily", day)


def test_resume_off_refetches_everything(qdb):
    days = ["20200102", "20200103", "20200106"]
    _seed_calendar(qdb, days)
    _seed_daily(qdb, "20200102")
    client = FakeClient(days)
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=False)
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert failures == []
    assert done == 3


def test_a_corrupted_partition_is_refetched(qdb):
    days = ["20200102"]
    _seed_calendar(qdb, days)
    _seed_daily(qdb, "20200102")
    target = qdb.partition_file("daily", "20200102")
    target.write_bytes(target.read_bytes() + b"x")
    client = FakeClient(days)
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert qdb.manifest_ok("daily", "20200102")
    assert client.calls, "a corrupted partition must be re-fetched"


def test_a_trading_day_without_rows_gets_an_empty_partition(qdb):
    """'checked and empty' must be distinguishable from 'never fetched'."""
    days = ["20200102", "20200103"]
    _seed_calendar(qdb, days)
    client = FakeClient(["20200102"])  # 20200103 returns nothing
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert failures == []
    assert done == 2
    assert qdb.manifest_ok("daily", "20200103")
    import pandas as pd

    empty = pd.read_parquet(qdb.partition_file("daily", "20200103"))
    assert len(empty) == 0
