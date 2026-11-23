"""Regression tests for the two defects found after the acceptance run.

A1  An empty partition was written with every column typed ``null`` (an empty
    pandas frame carries no values, so pyarrow cannot infer a type). A file
    like that breaks any reader that unifies the schemas of a dataset's
    files: DuckDB takes the schema of one file and then fails to cast the
    others ("failed to cast column X from type DATE to NULL"). The parity
    check crashed on ``stk_limit`` because of it.

B1  ``trade_cal`` was re-fetched with a different ``--end`` window without
    reloading DuckDB, so the Parquet layer and the DuckDB layer disagreed
    (15,264 vs 15,260 rows) and G1 flagged a trading day that is outside the
    study window. The parity gate now also covers the reference tables, so
    that kind of drift cannot pass unnoticed again.
"""

from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq

from db.etl import common


def test_an_empty_partition_carries_the_declared_schema(qdb):
    """The empty partition must have the same types as a non-empty one."""
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "trade_date": ["20200102"],
            "open": [12.4],
            "high": [12.6],
            "low": [12.3],
            "close": [12.47],
            "pre_close": [12.37],
            "change": [0.1],
            "pct_chg": [0.8],
            "vol": [100.0],
            "amount": [200.0],
        }
    )
    qdb.write_partition(frame, "daily", "20200102", "test")
    qdb.write_partition(common.empty_frame("daily"), "daily", "20200103", "test")

    filled = pq.ParquetFile(qdb.partition_file("daily", "20200102")).schema_arrow
    empty = pq.ParquetFile(qdb.partition_file("daily", "20200103")).schema_arrow
    assert [(f.name, str(f.type)) for f in empty] == [(f.name, str(f.type)) for f in filled]
    assert all(f.type.id != 0 for f in empty), "a column is still typed null"
    assert pq.ParquetFile(qdb.partition_file("daily", "20200103")).metadata.num_rows == 0


def test_the_parity_gate_covers_the_reference_tables():
    """A drift between Parquet and DuckDB must be caught for every dataset."""
    from db.checks import checks

    for dataset in ("trade_cal", "stock_basic"):
        assert dataset in checks.PK_DATASETS, dataset
    for dataset in common.TABLE_PK:
        assert dataset in checks.PK_DATASETS, dataset


def test_the_loader_unifies_the_schema_of_the_files(qdb):
    """read_parquet must not depend on which file the scan picks first."""
    import duckdb

    qdb.write_partition(
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": ["20200102"],
                "open": [12.4],
                "high": [12.6],
                "low": [12.3],
                "close": [12.47],
                "pre_close": [12.37],
                "change": [0.1],
                "pct_chg": [0.8],
                "vol": [100.0],
                "amount": [200.0],
            }
        ),
        "daily",
        "20200102",
        "test",
    )
    qdb.write_partition(common.empty_frame("daily"), "daily", "20200103", "test")
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        loaded = qdb.load_partitions(con, "daily", ["20200102", "20200103"])
    assert loaded == 1
    with duckdb.connect(str(qdb.DB_PATH), read_only=True) as con:
        rows = con.execute(
            "SELECT count(*) FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
            [common.parquet_glob("daily")],
        ).fetchone()[0]
    assert rows == 1
