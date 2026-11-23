"""Regression tests for the defects found after the first acceptance pass.

Three defects are covered here:

* **G2 had a blind spot.** ``PK_DATASETS`` was a hand-written list of 13
  datasets; the three reference tables (``trade_cal``, ``stock_basic``,
  ``index_classify``) were not in it. On 2026-11-23 the calendar was
  re-fetched with ``end_date=20261123`` ten minutes after the DuckDB load,
  so the Parquet layer held 15,264 rows and DuckDB 15,260 - a 4-row drift
  that every gate reported as clean. The list is now derived from
  ``common.TABLE_PK``; these tests pin that down.
* **The reference tables are not exempt from the primary key check.**
  ``trade_cal`` is keyed by (exchange, cal_date); a calendar that is
  fetched twice for the same exchange must be caught.
* **The qfq/hfq views are inner joins.** A ``daily`` row without an
  ``adj_factor`` row has no adjusted price and must not receive a
  fabricated one. Upstream leaves 1,919 such rows in this backfill (the
  NEEQ/BJ codes whose history starts before upstream's ``adj_factor``
  coverage); the views must exclude exactly those rows.
"""

from __future__ import annotations

import pandas as pd

from db.checks import checks
from db.etl import common


# --------------------------------------------------------------------------- #
# the parity gate must cover every table that lands in DuckDB
# --------------------------------------------------------------------------- #


def test_the_parity_gate_covers_every_declared_dataset():
    """A table that exists in the schema has to be checked by G2.

    Before the fix this failed with three missing datasets: the reference
    tables were absent from the hand-written list.
    """
    assert set(checks.PK_DATASETS) == set(common.TABLE_PK)
    assert {"trade_cal", "stock_basic", "index_classify"} <= set(checks.PK_DATASETS)


def test_g2_reports_a_reference_dataset_without_partitions(qdb):
    """A reference table with no partitions is a missing dataset, not a clean one."""
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        ok, detail = checks.check_primary_keys(con)

    assert ok is False
    assert {"trade_cal", "stock_basic", "index_classify"} <= set(detail["missing_datasets"])


def test_g2_reports_the_reference_tables_in_the_parity_table(qdb):
    frame = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["2020-01-02", "2020-01-03"],
            "is_open": [1, 1],
            "pretrade_date": ["2019-12-31", "2020-01-02"],
        }
    )
    qdb.write_partition(frame, "trade_cal", "SSE", "test")
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "trade_cal", ["SSE"])
        ok, detail = checks.check_primary_keys(con)

    assert "trade_cal" in detail["row_parity"]
    assert detail["row_parity"]["trade_cal"]["parquet_rows"] == 2
    assert detail["row_parity"]["trade_cal"]["duckdb_rows"] == 2
    assert detail["row_parity"]["trade_cal"]["difference"] == 0
    assert detail["row_parity"]["trade_cal"]["duplicate_key_groups"] == 0
    assert ok is False, "the other datasets are still missing in this fixture"


def test_g2_fails_when_the_calendar_partitions_drift_from_duckdb(qdb):
    """The 2026-11-23 defect, reproduced: Parquet has 3 calendar rows, DuckDB 2.

    The gate must report the difference instead of staying silent about a
    table it did not look at.
    """
    two_rows = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["2020-01-02", "2020-01-03"],
            "is_open": [1, 1],
            "pretrade_date": ["2019-12-31", "2020-01-02"],
        }
    )
    qdb.write_partition(two_rows, "trade_cal", "SSE", "test")
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "trade_cal", ["SSE"])

        # The Parquet layer grows behind the back of DuckDB - exactly what
        # happened when the calendar was re-fetched with a later end_date.
        three_rows = pd.concat(
            [
                two_rows,
                pd.DataFrame(
                    {
                        "exchange": ["SSE"],
                        "cal_date": ["2020-01-06"],
                        "is_open": [1],
                        "pretrade_date": ["2020-01-03"],
                    }
                ),
            ],
            ignore_index=True,
        )
        qdb.write_partition(three_rows, "trade_cal", "SSE", "test")

        ok, detail = checks.check_primary_keys(con)

    parity = detail["row_parity"]["trade_cal"]
    assert parity["parquet_rows"] == 3
    assert parity["duckdb_rows"] == 2
    assert parity["difference"] == 1
    assert "trade_cal" in detail["mismatched_datasets"]
    assert ok is False


def test_the_writer_never_writes_a_duplicate_primary_key(qdb):
    """The L0 writer drops repeats of the primary key before the file lands."""
    duplicated = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["2020-01-02", "2020-01-02"],
            "is_open": [1, 1],
            "pretrade_date": ["2019-12-31", "2019-12-31"],
        }
    )
    qdb.write_partition(duplicated, "trade_cal", "SSE", "test")

    stored = pd.read_parquet(common.partition_file("trade_cal", "SSE"))
    assert len(stored) == 1


def test_g2_fails_when_a_partition_carries_a_duplicate_key(qdb):
    """The calendar is keyed by (exchange, cal_date); a repeat must be caught.

    The file is written outside ``write_partition`` on purpose: the writer
    deduplicates, so the gate's duplicate check only ever fires on a file
    that reached the raw layer some other way.
    """
    duplicated = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["2020-01-02", "2020-01-02"],
            "is_open": [1, 1],
            "pretrade_date": ["2019-12-31", "2019-12-31"],
        }
    )
    target = common.partition_file("trade_cal", "SSE")
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(duplicated).to_parquet(target, index=False)

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "trade_cal", ["SSE"])
        ok, detail = checks.check_primary_keys(con)

    assert detail["row_parity"]["trade_cal"]["duplicate_key_groups"] == 1
    assert detail["total_duplicate_key_groups"] >= 1
    assert ok is False


# --------------------------------------------------------------------------- #
# the qfq/hfq views are inner joins
# --------------------------------------------------------------------------- #


def test_the_qfq_view_excludes_the_rows_without_an_adj_factor(qdb):
    """No adj_factor row means no adjusted price - the view must not invent one."""
    daily = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "trade_date": ["2020-01-02", "2020-01-03"],
            "open": [12.0, 12.5],
            "high": [12.6, 13.0],
            "low": [11.9, 12.4],
            "close": [12.47, 12.80],
            "pre_close": [12.0, 12.5],
            "vol": [1.0, 2.0],
            "amount": [1.0, 2.0],
            "change": [0.47, 0.30],
            "pct_chg": [3.9, 2.5],
        }
    )
    factors = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "trade_date": ["2020-01-02"],
            "adj_factor": [1.0],
        }
    )
    qdb.write_partition(daily, "daily", "20200102", "test")
    qdb.write_partition(daily, "daily", "20200103", "test")
    qdb.write_partition(factors, "adj_factor", "20200102", "test")

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "daily", ["20200102", "20200103"])
        qdb.load_partitions(con, "adj_factor", ["20200102"])
        rows = con.execute(
            "SELECT trade_date, close_qfq FROM v_daily_qfq ORDER BY trade_date"
        ).fetchall()
        hfq = con.execute("SELECT count(*) FROM v_daily_hfq").fetchone()[0]

    assert len(rows) == 1
    assert str(rows[0][0]) == "2020-01-02"
    assert rows[0][1] == 12.47
    assert hfq == 1
