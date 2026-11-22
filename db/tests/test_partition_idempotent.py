"""Idempotency: re-running the same parameters must not duplicate rows."""

from __future__ import annotations

import pandas as pd

from db.etl import common


def _daily(rows):
    return pd.DataFrame(
        [
            {
                "ts_code": code,
                "trade_date": day,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "vol": 1000.0,
                "amount": 10500.0,
            }
            for code, day in rows
        ]
    )


def test_same_partition_is_idempotent(qdb):
    df = _daily([("600000.SH", "20200102"), ("000001.SZ", "20200102")])
    first = qdb.write_partition(df, "daily", "20200102", "test")
    second = qdb.write_partition(df, "daily", "20200102", "test")
    assert first["rows"] == second["rows"] == 2
    assert first["sha256"] == second["sha256"]
    stored = pd.read_parquet(qdb.partition_file("daily", "20200102"))
    assert len(stored) == 2


def test_duplicate_rows_are_collapsed_by_pk(qdb):
    df = pd.concat(
        [_daily([("600000.SH", "20200102")]), _daily([("600000.SH", "20200102")])],
        ignore_index=True,
    )
    meta = qdb.write_partition(df, "daily", "20200102", "test")
    assert meta["rows"] == 1
    stored = pd.read_parquet(qdb.partition_file("daily", "20200102"))
    assert len(stored) == 1


def test_duckdb_load_is_idempotent(qdb):
    df = _daily([("600000.SH", "20200102"), ("000001.SZ", "20200102")])
    meta = qdb.write_partition(df, "daily", "20200102", "test")
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        rows1 = qdb.load_partitions(con, "daily", ["20200102"])
        rows2 = qdb.load_partitions(con, "daily", ["20200102"])
        count = con.execute("SELECT count(*) FROM daily").fetchone()[0]
    assert rows1 == rows2 == 2
    assert count == 2
    assert meta["rows"] == 2


def test_no_temp_files_are_left_behind(qdb):
    qdb.write_partition(_daily([("600000.SH", "20200102")]), "daily", "20200102", "test")
    leftovers = list(qdb.RAW_DIR.glob("daily/*/.*tmp*"))
    assert leftovers == []
