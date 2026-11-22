"""Regression tests for the four blocking findings of the independent review.

1. a failed upstream window must not become a permanently-skipped empty partition;
2. a financial partition must not be written when one of its windows failed;
3. the month-end helper must produce a real calendar end (index_weight);
4. loading one dataset must not drop the other DuckDB tables.
"""

from __future__ import annotations

import pandas as pd

from db.etl import common, fetch


class _FailingClient:
    """Every upstream call fails."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, api, params, **kwargs):
        self.calls.append(dict(params))
        raise RuntimeError("HTTP 502 upstream")


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


def test_a_failed_window_does_not_produce_empty_partitions(qdb):
    """A failure must stay a failure: no manifest, so the next slice retries."""
    days = ["20200102", "20200103", "20200106"]
    _seed_calendar(qdb, days)
    client = _FailingClient()
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert done == 0
    assert failures, "the failure must be recorded"
    for day in days:
        assert not qdb.manifest_ok("daily", day), (
            f"{day} must not be marked complete after a failed window"
        )
        assert not qdb.partition_file("daily", day).exists()
    # the ledger is keyed by partition so a later success can clear it
    recorded = qdb.read_failures("daily")
    assert {record["partition"] for record in recorded} == set(days)


def test_a_failed_financial_window_does_not_write_a_partition(qdb):
    qdb.write_partition(
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "symbol": ["600000"],
                "name": ["浦发银行"],
                "list_date": ["20060101"],
            }
        ),
        "stock_basic",
        "L",
        "test",
    )
    client = _FailingClient()
    fetcher = fetch.Fetcher(client, "20060101", "20261121", resume=True)
    done, failures = fetcher.fetch_financial(fetch.DATASETS["income"], concurrency=1)
    assert done == 0
    assert failures
    assert not qdb.manifest_ok("income", "600000.SH")
    assert not qdb.partition_file("income", "600000.SH").exists()


def test_month_end_is_a_real_calendar_end():
    assert fetch._month_end(2026, 1) == "20260131"
    assert fetch._month_end(2026, 2) == "20260228"
    assert fetch._month_end(2024, 2) == "20240229"  # leap year
    assert fetch._month_end(2026, 4) == "20260430"
    assert fetch._month_end(2026, 12) == "20261231"
    for month in range(1, 13):
        value = fetch._month_end(2026, month)
        assert len(value) == 8 and value.isdigit(), value


def test_loading_one_dataset_keeps_the_other_tables(qdb):
    """`--load-only --dataset daily` must not empty the other L1 tables."""
    daily = pd.DataFrame(
        {"ts_code": ["600000.SH"], "trade_date": ["20200102"], "close": [10.5]}
    )
    other = pd.DataFrame(
        {"ts_code": ["600000.SH"], "list_date": ["19991110"], "name": ["浦发银行"]}
    )
    qdb.write_partition(daily, "daily", "20200102", "test")
    qdb.write_partition(other, "stock_basic", "L", "test")
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "daily", ["20200102"])
        qdb.load_partitions(con, "stock_basic", ["L"])
        con.execute("CHECKPOINT")
    with qdb.duckdb_connect() as con:
        before = con.execute("SELECT count(*) FROM stock_basic").fetchone()[0]
        assert before == 1
        qdb.load_partitions(con, "daily", ["20200102"])
        after = con.execute("SELECT count(*) FROM stock_basic").fetchone()[0]
    assert after == 1, "loading daily must not drop stock_basic"


def test_schema_has_no_drop_statements():
    """Re-running the schema must never empty a table."""
    sql = common.SCHEMA_SQL.read_text(encoding="utf-8")
    assert "DROP TABLE" not in sql
    assert sql.count("CREATE TABLE IF NOT EXISTS") == len(common.TABLE_PK)


def test_an_exhausted_slice_budget_does_not_write_empty_partitions(qdb):
    """A slice that runs out of budget has NOT checked the remaining days.

    Writing them as empty partitions would mark them complete and every later
    run would skip them for good - the days that the upstream actually has
    data for would be lost silently.
    """
    import time

    days = ["20200102", "20200103"]
    _seed_calendar(qdb, days)

    class _OkClient:
        def call(self, api, params, **kwargs):
            return (
                ["ts_code", "trade_date", "close"],
                [["600000.SH", day, 10.5] for day in days],
            )

    fetcher = fetch.Fetcher(
        _OkClient(), "20200101", "20200131", resume=True, deadline=time.monotonic() - 1
    )
    done, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert done == 0
    for day in days:
        assert not qdb.manifest_ok("daily", day), f"{day} must not be marked complete"
        assert not qdb.partition_file("daily", day).exists()
