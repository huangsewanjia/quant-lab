"""Regression tests for the four blockers of WORK-513 / WORK-518.

B1  an empty partition used to land in Parquet with the ``null`` type, so a
    batch that mixed an empty partition with a populated one made DuckDB cast
    ``DATE`` to ``NULL`` and the whole load died. Two sides have to hold:
    the writer gives every column an explicit type, and the loader tolerates
    the legacy files that are already on disk (they cannot be rewritten).

B2  ``fetch_index_weight`` concatenated the year and the month onto a date
    that already carried both, so every monthly window asked the upstream for
    an invalid end_date and came back empty.

B4  G1 compared the daily row count with a rolling median and called every
    deviation above 5% a defect. A market-wide suspension is not a defect, so
    the gate now looks at the suspension count before it decides.
"""

from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq
import pytest

from db.checks import checks
from db.etl import common, fetch


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _legacy_empty_partition(qdb, dataset: str, partition: str) -> None:
    """Write an empty partition the way the old writer did: Parquet null types.

    The files already on disk are exactly like this and must not be rewritten,
    so the loader has to survive them.
    """
    target = qdb.partition_file(dataset, partition)
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=qdb.canonical_columns(dataset)).to_parquet(
        target, index=False, compression="zstd"
    )


def _parquet_types(path) -> dict[str, str]:
    schema = pq.read_schema(path)
    return {field.name: str(field.type) for field in schema}


# --------------------------------------------------------------------------- #
# B1 - the writer gives empty partitions a real type
# --------------------------------------------------------------------------- #


def test_write_partition_types_an_empty_partition_from_the_schema(qdb):
    """An empty partition must not carry Parquet's null type."""
    columns = common.canonical_columns("stk_limit")
    qdb.write_partition(pd.DataFrame(columns=columns), "stk_limit", "20060104", "test")

    types = _parquet_types(qdb.partition_file("stk_limit", "20060104"))
    assert "null" not in types.values(), types
    assert types["trade_date"] == "date32[day]"
    assert types["ts_code"] == "string"
    assert types["up_limit"] == "double"

    # The manifest records the types, so a partition can be audited later.
    manifest = qdb.read_manifest("stk_limit", "20060104")
    assert manifest["rows"] == 0
    assert manifest["column_types"]["trade_date"] == "date32[day]"


def test_write_partition_types_a_column_that_is_entirely_none(qdb):
    """A populated partition with an all-None column must type that column too."""
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "ann_date": ["20230131"],
            "f_ann_date": [None],
            "end_date": ["20231231"],
            "report_type": ["1"],
            "total_revenue": [None],
        }
    )
    qdb.write_partition(frame, "income", "600000.SH", "test")

    types = _parquet_types(qdb.partition_file("income", "600000.SH"))
    assert "null" not in types.values(), types
    assert types["f_ann_date"] == "date32[day]"
    assert types["total_revenue"] == "double"


def test_write_partition_keeps_the_upstream_types_of_populated_columns(qdb):
    """The fix must not re-type columns that carry real upstream values."""
    frame = pd.DataFrame(
        {
            "trade_date": ["20200102"],
            "ts_code": ["600000.SH"],
            "up_limit": [11.0],
            "down_limit": [9.0],
        }
    )
    qdb.write_partition(frame, "stk_limit", "20200102", "test")
    types = _parquet_types(qdb.partition_file("stk_limit", "20200102"))
    assert types["trade_date"] == "date32[day]"
    assert types["up_limit"] == "double"


# --------------------------------------------------------------------------- #
# B1 - the loader survives a batch that mixes empty and populated partitions
# --------------------------------------------------------------------------- #


def test_loader_accepts_an_empty_and_a_populated_partition_in_one_batch(qdb):
    """The exact crash of WORK-513: one empty partition poisons the whole batch."""
    _legacy_empty_partition(qdb, "stk_limit", "20060104")
    qdb.write_partition(
        pd.DataFrame(
            {
                "trade_date": ["20070104", "20070104"],
                "ts_code": ["600000.SH", "000001.SZ"],
                "up_limit": [11.0, 12.0],
                "down_limit": [9.0, 10.0],
            }
        ),
        "stk_limit",
        "20070104",
        "test",
    )

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        loaded = qdb.load_partitions(
            con, "stk_limit", ["20060104", "20070104"], batch_size=400
        )
        assert loaded == 2
        rows = con.execute("SELECT count(*) FROM stk_limit").fetchone()[0]
        assert rows == 2
        stored = con.execute(
            "SELECT ts_code, trade_date, typeof(trade_date) FROM stk_limit ORDER BY ts_code"
        ).fetchall()
        assert stored[0][2] == "DATE"


def test_loader_accepts_a_sparse_dataset_where_early_years_are_empty(qdb):
    """stk_limit has no upstream data in 2006; the whole set must still load."""
    for day in ("20060104", "20060105", "20060106"):
        _legacy_empty_partition(qdb, "stk_limit", day)
    for day in ("20070104", "20070105"):
        qdb.write_partition(
            pd.DataFrame(
                {
                    "trade_date": [day] * 3,
                    "ts_code": ["600000.SH", "000001.SZ", "600519.SH"],
                    "up_limit": [11.0, 12.0, 13.0],
                    "down_limit": [9.0, 10.0, 11.0],
                }
            ),
            "stk_limit",
            day,
            "test",
        )

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        parts = ["20060104", "20060105", "20060106", "20070104", "20070105"]
        loaded = qdb.load_partitions(con, "stk_limit", parts, batch_size=400)
        assert loaded == 6
        rows = con.execute("SELECT count(*) FROM stk_limit").fetchone()[0]
        assert rows == 6
        days = con.execute(
            "SELECT count(DISTINCT trade_date) FROM stk_limit"
        ).fetchone()[0]
        assert days == 2


def test_loader_accepts_a_batch_of_only_empty_partitions(qdb):
    """A batch whose files are all empty must load without an error."""
    for day in ("20060104", "20060105"):
        _legacy_empty_partition(qdb, "suspend_d", day)

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        loaded = qdb.load_partitions(
            con, "suspend_d", ["20060104", "20060105"], batch_size=400
        )
        assert loaded == 0
        assert con.execute("SELECT count(*) FROM suspend_d").fetchone()[0] == 0


def test_loader_splits_a_mixed_set_into_batches_that_all_load(qdb):
    """The batch size must not decide whether a mixed set loads."""
    partitions = []
    for index, day in enumerate(("20060104", "20060105", "20070104", "20070105")):
        partitions.append(day)
        if index % 2 == 0:
            _legacy_empty_partition(qdb, "stk_limit", day)
        else:
            qdb.write_partition(
                pd.DataFrame(
                    {
                        "trade_date": [day],
                        "ts_code": ["600000.SH"],
                        "up_limit": [11.0],
                        "down_limit": [9.0],
                    }
                ),
                "stk_limit",
                day,
                "test",
            )

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        # batch_size=2 puts an empty partition and a populated one into the
        # SAME batch - that is the case that used to crash the load.
        loaded = qdb.load_partitions(con, "stk_limit", partitions, batch_size=2)
        assert loaded == 2
        assert con.execute("SELECT count(*) FROM stk_limit").fetchone()[0] == 2


def test_loader_keeps_the_dates_of_a_timestamp_typed_partition(qdb):
    """A date column written as TIMESTAMP must not be nulled out on load.

    The partitions that are already on disk hold a date column as DATE in one
    partition and as TIMESTAMP in the next one (a partition whose date column
    has no values at all). Merging the schemas promotes the column to
    TIMESTAMP, and a projection that only understands strings would silently
    drop those dates.
    """
    qdb.write_partition(
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600000.SH"],
                "name": ["浦发银行", "浦发银行"],
                "start_date": ["20060104", "20060105"],
                "end_date": ["20100101", "20100102"],
                "ann_date": [None, None],
                "change_reason": ["更名", "更名"],
            }
        ),
        "namechange",
        "600000.SH",
        "test",
    )
    types = _parquet_types(qdb.partition_file("namechange", "600000.SH"))
    assert types["end_date"] == "date32[day]", types

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        loaded = qdb.load_partitions(con, "namechange", ["600000.SH"])
        assert loaded == 2
        rows = con.execute(
            "SELECT end_date, typeof(end_date) FROM namechange ORDER BY start_date"
        ).fetchall()
        assert rows[0][0].isoformat() == "2010-01-01"
        assert rows[1][0].isoformat() == "2010-01-02"
        assert rows[0][1] == "DATE"


def test_loader_keeps_the_dates_of_a_legacy_timestamp_partition(qdb):
    """The same for a partition that was written before the writer was fixed."""
    legacy = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "name": ["浦发银行"],
            "start_date": pd.to_datetime(["2006-01-04"]),
            "end_date": pd.to_datetime(["2010-01-01"]),
            "ann_date": pd.to_datetime([None]),
            "change_reason": ["更名"],
        }
    )
    target = qdb.partition_file("namechange", "600000.SH")
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.to_parquet(target, index=False, compression="zstd")
    assert _parquet_types(target)["end_date"].startswith("timestamp")

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        loaded = qdb.load_partitions(con, "namechange", ["600000.SH"])
        assert loaded == 1
        row = con.execute(
            "SELECT end_date, typeof(end_date) FROM namechange"
        ).fetchone()
        assert row[0].isoformat() == "2010-01-01"
        assert row[1] == "DATE"


def test_loader_keeps_the_dates_when_a_batch_merges_date_and_timestamp(qdb):
    """A batch that mixes a DATE partition with a TIMESTAMP partition.

    This is the production shape: dividend.div_listdate is date32[day] in
    4,161 partitions and timestamp[ms] in 1,709 of them. Merging the schemas
    of such a batch promotes the column to TIMESTAMP, and a projection that
    only understands strings would silently drop those dates - the row count
    would still look right, only the dates would be gone.
    """
    # one partition written by the current writer: DATE
    qdb.write_partition(
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "name": ["浦发银行"],
                "start_date": ["20060104"],
                "end_date": ["20100101"],
                "ann_date": [None],
                "change_reason": ["更名"],
            }
        ),
        "namechange",
        "600000.SH",
        "test",
    )
    assert _parquet_types(qdb.partition_file("namechange", "600000.SH"))["end_date"] == (
        "date32[day]"
    )

    # one partition written by the old writer: TIMESTAMP, with values
    legacy = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "start_date": pd.to_datetime(["2006-01-05"]),
            "end_date": pd.to_datetime(["2008-10-31"]),
            "ann_date": pd.to_datetime([None]),
            "change_reason": ["更名"],
        }
    )
    target = qdb.partition_file("namechange", "000001.SZ")
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.to_parquet(target, index=False, compression="zstd")
    assert _parquet_types(target)["end_date"].startswith("timestamp")

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        loaded = qdb.load_partitions(
            con, "namechange", ["000001.SZ", "600000.SH"], batch_size=400
        )
        assert loaded == 2
        rows = con.execute(
            "SELECT ts_code, end_date, typeof(end_date) FROM namechange ORDER BY ts_code"
        ).fetchall()
        assert rows[0][1].isoformat() == "2008-10-31"
        assert rows[1][1].isoformat() == "2010-01-01"
        assert {row[2] for row in rows} == {"DATE"}


# --------------------------------------------------------------------------- #
# B2 - the index_weight window is a valid date
# --------------------------------------------------------------------------- #


class _RecordingClient:
    """Records every window the fetcher asks the upstream for."""

    def __init__(self) -> None:
        self.params: list[dict] = []

    def call(self, api, params, **kwargs):
        self.params.append(dict(params))
        return ["index_code", "con_code", "trade_date", "weight"], []

    def fetch_window(self, api, params, **kwargs):
        self.params.append(dict(params))
        return pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"])


def test_index_weight_asks_for_a_valid_end_date(qdb):
    """The end_date must be YYYYMMDD, not the month glued onto the month end."""
    client = _RecordingClient()
    fetcher = fetch.Fetcher(client, "20260101", "20260331", resume=True)
    fetcher.fetch_index_weight(fetch.DATASETS["index_weight"], concurrency=1)

    assert client.params, "no upstream call was made"
    for params in client.params:
        end_date = params["end_date"]
        assert len(end_date) == 8, params
        assert end_date.isdigit(), params
        assert end_date >= params["start_date"], params
        assert end_date <= "20260331", params


def test_index_weight_end_date_is_the_last_day_of_the_month(qdb):
    """A full month must end on its last calendar day."""
    client = _RecordingClient()
    fetcher = fetch.Fetcher(client, "20260101", "20260331", resume=True)
    fetcher.fetch_index_weight(fetch.DATASETS["index_weight"], concurrency=1)

    ends = sorted({params["end_date"] for params in client.params})
    assert ends == ["20260131", "20260228", "20260331"]


def test_index_weight_end_date_never_leaves_the_run_window(qdb):
    """The last month is cut at the end of the run window."""
    client = _RecordingClient()
    fetcher = fetch.Fetcher(client, "20260101", "20260315", resume=True)
    fetcher.fetch_index_weight(fetch.DATASETS["index_weight"], concurrency=1)

    ends = sorted({params["end_date"] for params in client.params})
    assert ends == ["20260131", "20260228", "20260315"]


# --------------------------------------------------------------------------- #
# B4 - G1 tells a market-wide suspension from a data defect
# --------------------------------------------------------------------------- #


def _seed_g1_tables(
    con,
    days,
    rows_per_day,
    drop_day=None,
    drop_rows=None,
    surplus_day=None,
    routine_suspended=0,
    extra_suspended=0,
):
    """Fill trade_cal / daily / suspend_d with a reproducible row-count series.

    ``routine_suspended`` is the number of stocks suspended on EVERY trading
    day (the routine level); ``extra_suspended`` is the extra suspensions on
    the day under test (the abnormal part that can explain a deviation).
    """
    con.execute("DELETE FROM suspend_d")
    con.execute("DELETE FROM daily")
    con.execute("DELETE FROM trade_cal")
    for day in days:
        con.execute(
            "INSERT INTO trade_cal VALUES (?, ?, ?, ?)",
            ["SSE", day, 1, day],
        )
    for day in days:
        if day == drop_day:
            count = drop_rows if drop_rows is not None else max(1, rows_per_day // 10)
        elif day == surplus_day:
            count = int(rows_per_day * 1.2)
        else:
            count = rows_per_day
        for index in range(count):
            con.execute(
                "INSERT INTO daily (ts_code, trade_date, open, high, low, close) "
                "VALUES (?, ?, 1.0, 1.0, 1.0, 1.0)",
                [f"{index:06d}.SZ", day],
            )
    for day in days:
        suspended = routine_suspended
        if day in (drop_day, surplus_day):
            suspended += extra_suspended
        for index in range(suspended):
            con.execute(
                "INSERT INTO suspend_d (ts_code, trade_date, suspend_timing, suspend_type) "
                "VALUES (?, ?, NULL, 'S')",
                [f"S{day}-{index:05d}.SZ", day],
            )


def _g1_days() -> list[str]:
    return [f"2020-01-{day:02d}" for day in range(2, 13)]


def test_g1_reports_a_suspension_explained_deviation_as_an_exception(qdb):
    """A shortfall that is covered by the excess suspensions is not a defect."""
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        # 20 routine suspensions every day, 40 more on the day in question:
        # the excess is 40, so 60 rows are expected and 60 rows are there.
        _seed_g1_tables(
            con,
            days,
            100,
            drop_day="2020-01-07",
            drop_rows=60,
            routine_suspended=20,
            extra_suspended=40,
        )
        ok, detail = checks.check_trading_days(con)

    assert ok is True
    assert detail["anomaly_count"] == 1
    assert detail["unexplained_count"] == 0
    assert detail["exception_count"] == 1
    assert detail["conclusion"] == "PASS_WITH_EXCEPTIONS"

    exception = detail["exceptions"][0]
    assert exception["trade_date"] == "2020-01-07"
    assert exception["rows"] == 60
    assert exception["rolling_median"] == 100.0
    assert exception["shortfall_rows"] == 40
    assert exception["suspend_rows"] == 60
    assert exception["suspend_rows_s"] == 60
    assert exception["excess_suspensions"] == 40.0
    assert exception["expected_rows"] == 60.0
    assert exception["adjusted_deviation"] == 0.0
    assert exception["judgement"] == "explained_by_suspensions"


def test_g1_fails_a_deviation_that_the_suspension_count_does_not_cover(qdb):
    """A shortfall bigger than the excess suspensions is still a defect."""
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        _seed_g1_tables(
            con,
            days,
            100,
            drop_day="2020-01-07",
            drop_rows=10,
            routine_suspended=20,
            extra_suspended=40,
        )
        ok, detail = checks.check_trading_days(con)

    assert ok is False
    assert detail["unexplained_count"] == 1
    assert detail["exception_count"] == 0
    anomaly = detail["anomalies"][0]
    assert anomaly["judgement"] == "unexplained"
    assert anomaly["suspend_rows"] == 60
    assert anomaly["excess_suspensions"] == 40.0
    assert anomaly["shortfall_rows"] == 90


def test_g1_fails_a_deviation_without_any_suspension_record(qdb):
    """No suspension record means no excuse."""
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        _seed_g1_tables(con, days, 100, drop_day="2020-01-07", drop_rows=10)
        ok, detail = checks.check_trading_days(con)

    assert ok is False
    assert detail["unexplained_count"] == 1
    assert detail["anomalies"][0]["suspend_rows"] == 0
    assert detail["anomalies"][0]["excess_suspensions"] == 0.0


def test_g1_fails_an_upward_deviation_even_with_suspensions(qdb):
    """More rows than usual is a defect, not a suspension - it is never excused.

    The shortfall of a surplus day is 0, so a rule that compares the shortfall
    with the suspension count would call every surplus day "explained".
    """
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        _seed_g1_tables(
            con,
            days,
            100,
            surplus_day="2020-01-07",
            routine_suspended=20,
            extra_suspended=40,
        )
        ok, detail = checks.check_trading_days(con)

    assert ok is False
    assert detail["unexplained_count"] == 1
    assert detail["exception_count"] == 0
    anomaly = detail["anomalies"][0]
    assert anomaly["trade_date"] == "2020-01-07"
    assert anomaly["surplus_rows"] == 20
    assert anomaly["shortfall_rows"] == 0
    assert anomaly["judgement"] == "unexplained"


def test_g1_fails_an_upward_deviation_without_suspensions(qdb):
    """The plain case: a day with too many rows must fail the gate."""
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        _seed_g1_tables(con, days, 100, surplus_day="2020-01-07")
        ok, detail = checks.check_trading_days(con)

    assert ok is False
    assert detail["unexplained_count"] == 1
    assert detail["anomalies"][0]["judgement"] == "unexplained"


def test_g1_does_not_excuse_a_gap_that_the_routine_suspensions_do_not_cover(qdb):
    """The routine suspensions of a normal day must not excuse a real gap.

    Every trading day has suspension records, so comparing the shortfall with
    the absolute count would excuse a real gap of that size on any day. The
    excuse has to come from the EXCESS suspensions over the neighbouring days.
    """
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        _seed_g1_tables(
            con,
            days,
            100,
            drop_day="2020-01-07",
            # 20 routine suspensions, 20 rows missing: the OLD rule compared
            # the shortfall with the absolute suspension count and excused it,
            # the new rule looks at the EXCESS (0) and does not.
            drop_rows=80,
            routine_suspended=20,
            extra_suspended=0,
        )
        ok, detail = checks.check_trading_days(con)

    assert ok is False
    assert detail["unexplained_count"] == 1
    anomaly = detail["anomalies"][0]
    assert anomaly["suspend_rows"] == 20
    assert anomaly["excess_suspensions"] == 0.0
    assert anomaly["shortfall_rows"] == 20


def test_g1_keeps_every_anomaly_record(qdb):
    """The exceptions are added next to the anomalies, they do not replace them."""
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        _seed_g1_tables(
            con,
            days,
            100,
            drop_day="2020-01-07",
            drop_rows=60,
            routine_suspended=20,
            extra_suspended=40,
        )
        _, detail = checks.check_trading_days(con)

    assert detail["anomaly_count"] == 1
    assert len(detail["anomalies"]) == min(detail["anomaly_count"], 50)
    assert detail["anomalies"][0]["trade_date"] == "2020-01-07"


def test_g1_does_not_loosen_the_five_percent_threshold(qdb):
    """A deviation of 4% stays clean; the threshold did not move."""
    days = _g1_days()
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        for day in days:
            con.execute("INSERT INTO trade_cal VALUES (?, ?, ?, ?)", ["SSE", day, 1, day])
        for day in days:
            count = 96 if day == "2020-01-07" else 100
            for index in range(count):
                con.execute(
                    "INSERT INTO daily (ts_code, trade_date, open, high, low, close) "
                    "VALUES (?, ?, 1.0, 1.0, 1.0, 1.0)",
                    [f"{index:06d}.SZ", day],
                )
        ok, detail = checks.check_trading_days(con)

    assert ok is True
    assert detail["anomaly_count"] == 0
    assert detail["deviation_tolerance"] == 0.05


# --------------------------------------------------------------------------- #
# B5 (found by the WORK-518 load): a stale primary key drops rows silently
# --------------------------------------------------------------------------- #


def test_a_table_with_a_stale_primary_key_is_rebuilt(qdb):
    """A table created with an older primary key must not keep it.

    DuckDB cannot change a primary key and CREATE TABLE IF NOT EXISTS keeps
    the old table, so the financial tables kept the primary key without
    dup_seq and INSERT OR REPLACE silently dropped one of the two report
    versions (measured: 20%-44% of the financial rows).
    """
    with qdb.duckdb_connect() as con:
        con.execute(
            "CREATE TABLE income (ts_code VARCHAR, end_date DATE, report_type VARCHAR, "
            "ann_date DATE, PRIMARY KEY (ts_code, end_date, report_type, ann_date))"
        )
        assert qdb.existing_primary_key(con, "income") == (
            "ts_code",
            "end_date",
            "report_type",
            "ann_date",
        )

        # The rebuild happens in the load of THAT dataset, not in apply_schema.
        qdb.apply_schema(con)
        qdb.write_partition(
            pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "end_date": ["20231231"],
                    "report_type": ["1"],
                    "ann_date": ["20240101"],
                    "total_revenue": [100.0],
                }
            ),
            "income",
            "600000.SH",
            "test",
        )
        qdb.load_partitions(con, "income", ["600000.SH"])

        assert qdb.existing_primary_key(con, "income") == (
            "ts_code",
            "end_date",
            "report_type",
            "ann_date",
            "dup_seq",
        )


def test_a_table_with_the_current_primary_key_is_kept(qdb):
    """A table that already matches the schema must not be dropped."""
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        con.execute(
            "INSERT INTO income (ts_code, end_date, report_type, ann_date, dup_seq) "
            "VALUES ('600000.SH', DATE '2023-12-31', '1', DATE '2024-01-01', 0)"
        )
        qdb.apply_schema(con)
        rows = con.execute("SELECT count(*) FROM income").fetchone()[0]
        assert rows == 1


def test_a_stale_primary_key_does_not_swallow_rows(qdb):
    """The rebuilt table keeps both versions of the same report.

    This is the silent data loss the WORK-518 load measured: the old primary
    key had no dup_seq, so INSERT OR REPLACE replaced the first version of the
    report with the second one.
    """
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20231231", "20231231"],
            "report_type": ["1", "1"],
            "ann_date": ["20240101", "20240101"],
            "total_revenue": [100.0, 110.0],
        }
    )
    with qdb.duckdb_connect() as con:
        # a table with the old primary key
        con.execute(
            "CREATE TABLE income (ts_code VARCHAR, end_date DATE, report_type VARCHAR, "
            "ann_date DATE, total_revenue DOUBLE, "
            "PRIMARY KEY (ts_code, end_date, report_type, ann_date))"
        )
        qdb.apply_schema(con)
        qdb.write_partition(frame, "income", "600000.SH", "test")
        # the load of that dataset rebuilds the table, then loads the partition twice
        qdb.load_partitions(con, "income", ["600000.SH"])
        qdb.load_partitions(con, "income", ["600000.SH"])
        rows = con.execute(
            "SELECT count(*), count(DISTINCT dup_seq) FROM income"
        ).fetchone()
        assert rows[0] == 2
        assert rows[1] == 2


class _EmptyClient:
    """Every upstream window comes back empty."""

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


# --------------------------------------------------------------------------- #
# F6: an empty partition written by an older fetch implementation is re-probed
# --------------------------------------------------------------------------- #


def test_an_empty_partition_of_an_older_fetch_version_is_re_fetched(qdb):
    """A fetch bug must not freeze empty partitions for ever.

    The WORK-518 index_weight bug asked the upstream for an invalid date and
    published eight empty partitions; every later resume skipped them because
    the manifest said "complete, 0 rows".
    """
    days = ["20200102", "20200103"]
    _seed_calendar(qdb, days)
    client = _EmptyClient()
    fetcher = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    fetcher.fetch_range(fetch.DATASETS["stk_limit"], concurrency=1)

    # The manifest now carries the fetch implementation version.
    manifest = qdb.read_manifest("stk_limit", "20200102")
    assert manifest["fetch_impl"] == qdb.FETCH_IMPL_VERSION

    # An older implementation (or a missing marker) is not trusted.
    manifest["fetch_impl"] = "old-window-bug"
    qdb.write_json_atomic(qdb.manifest_path("stk_limit", "20200102"), manifest)
    again = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    done, failures = again.fetch_range(fetch.DATASETS["stk_limit"], concurrency=1)
    assert failures == []
    assert done == 1, "the empty partition of an older fetch version was skipped"

    # A partition with rows is not re-fetched just because the marker is old.
    qdb.write_partition(
        pd.DataFrame(
            {
                "trade_date": ["20200103"],
                "ts_code": ["600000.SH"],
                "up_limit": [11.0],
                "down_limit": [9.0],
            }
        ),
        "stk_limit",
        "20200103",
        "test",
    )
    manifest = qdb.read_manifest("stk_limit", "20200103")
    manifest["fetch_impl"] = "old-window-bug"
    qdb.write_json_atomic(qdb.manifest_path("stk_limit", "20200103"), manifest)
    calls_before = len(client.calls)
    third = fetch.Fetcher(client, "20200101", "20200131", resume=True)
    third.fetch_range(fetch.DATASETS["stk_limit"], concurrency=1)
    assert len(client.calls) == calls_before, "a populated partition was re-fetched"


def test_a_subset_load_does_not_empty_a_table_it_does_not_refill(qdb):
    """A stale table outside the load set must keep its rows.

    The rebuild is scoped to the dataset that is loaded: a subset load (for
    example `--dataset daily --load-only`) must never empty the financial
    tables it does not refill.
    """
    stale = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20231231", "20231231"],
            "report_type": ["1", "1"],
            "ann_date": ["20240101", "20240101"],
            "total_revenue": [100.0, 110.0],
        }
    )
    with qdb.duckdb_connect() as con:
        con.execute(
            "CREATE TABLE income (ts_code VARCHAR, end_date DATE, report_type VARCHAR, "
            "ann_date DATE, total_revenue DOUBLE, "
            "PRIMARY KEY (ts_code, end_date, report_type, ann_date))"
        )
        con.execute(
            "INSERT INTO income VALUES ('600000.SH', DATE '2023-12-31', '1', "
            "DATE '2024-01-01', 100.0)"
        )
        qdb.apply_schema(con)
        qdb.write_partition(stale, "income", "600000.SH", "test")

        # a load of a DIFFERENT dataset must not touch the income table
        qdb.write_partition(
            pd.DataFrame(
                {
                    "trade_date": ["20200102"],
                    "ts_code": ["600000.SH"],
                    "up_limit": [11.0],
                    "down_limit": [9.0],
                }
            ),
            "stk_limit",
            "20200102",
            "test",
        )
        qdb.load_partitions(con, "stk_limit", ["20200102"])

        rows = con.execute("SELECT count(*) FROM income").fetchone()[0]
        assert rows == 1, "the stale table was emptied by a load of another dataset"
