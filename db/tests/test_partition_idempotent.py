from __future__ import annotations

import datetime as dt
import shutil

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
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 1000.0,
                "amount": 10500.0,
            }
            for code, day in rows
        ]
    )


def test_same_partition_is_idempotent(qdb):
    df = _daily([("600000.SH", dt.date(2020, 1, 2)), ("000001.SZ", dt.date(2020, 1, 2))])
    first = common.write_partition(df, "daily", "20200102", "test")
    second = common.write_partition(df, "daily", "20200102", "test")
    assert first["rows"] == second["rows"] == 2
    assert first["sha256"] == second["sha256"]
    stored = pd.read_parquet(common.partition_dir("daily", "20200102") / "data.parquet")
    assert len(stored) == 2


def test_duplicate_rows_are_collapsed_by_pk(qdb):
    day = dt.date(2020, 1, 2)
    df = pd.concat(
        [
            _daily([("600000.SH", day)]),
            _daily([("600000.SH", day)]),
        ],
        ignore_index=True,
    )
    meta = common.write_partition(df, "daily", "20200102", "test")
    assert meta["rows"] == 1
    stored = pd.read_parquet(common.partition_dir("daily", "20200102") / "data.parquet")
    assert len(stored) == 1


def test_duckdb_load_is_idempotent(qdb):
    day = dt.date(2020, 1, 2)
    df = _daily([("600000.SH", day), ("000001.SZ", day)])
    meta = common.write_partition(df, "daily", "20200102", "test")
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        rows1 = common.load_partitions(con, "daily", ["20200102"])
        rows2 = common.load_partitions(con, "daily", ["20200102"])
        count = con.execute("SELECT count(*) FROM daily").fetchone()[0]
    assert rows1 == rows2 == 2
    assert count == 2
    assert meta["rows"] == 2


def test_load_dedupes_data_and_part_parquet(qdb):
    day = dt.date(2020, 1, 2)
    df = _daily([("600000.SH", day), ("000001.SZ", day)])
    common.write_partition(df, "daily", "20200102", "test")
    folder = common.partition_dir("daily", "20200102")
    # Another writer may use part.parquet in the same partition.
    shutil.copy(folder / "data.parquet", folder / "part.parquet")
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        rows = common.load_partitions(con, "daily", ["20200102"])
        count = con.execute("SELECT count(*) FROM daily").fetchone()[0]
    assert rows == 2
    assert count == 2
