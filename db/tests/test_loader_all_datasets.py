"""Loader coverage for every dataset in the registry.

Only ``daily`` and ``stock_basic`` had a loader test before this file. The
backfill writes 16 datasets into DuckDB; a cast/BY-NAME problem in any of the
other 14 would otherwise only surface after hours of fetching. This test
builds one synthetic partition per dataset straight from the generated
``schema.sql`` and asserts that the row lands in the right table with the
declared types.
"""

from __future__ import annotations

import pandas as pd
import pytest

from db.etl import common
from db.etl.fetch import DATASETS

# Partition key used for the synthetic partition of each dataset. It must be
# stable across the test and must not collide with the PRIMARY KEY columns,
# which the fixture fills with unique values below.
PARTITION_KEY = {
    "trade_cal": "partition=SSE",
    "stock_basic": "status=L",
    "daily": "20200102",
    "daily_basic": "20200102",
    "adj_factor": "20200102",
    "stk_limit": "20200102",
    "suspend_d": "20200102",
    "index_daily": "20200102",
    "index_weight": "000300.SH 20200101",
    "index_classify": "src=SW2021",
    "namechange": "600000.SH",
    "dividend": "600000.SH",
    "income": "600000.SH",
    "balancesheet": "600000.SH",
    "cashflow": "600000.SH",
    "fina_indicator": "600000.SH",
}

ALL_DATASETS = sorted(DATASETS)


def _dummy_value(column: str, dtype: str, index: int) -> object:
    """A type-appropriate, non-null dummy value for one schema column."""
    if dtype == "DATE":
        return "20200102"
    if dtype in {"DOUBLE", "FLOAT", "REAL", "DECIMAL"}:
        return float(index + 1)
    if dtype in {"BIGINT", "INTEGER", "INT", "SMALLINT", "TINYINT"}:
        return index + 1
    if dtype == "BOOLEAN":
        return True
    if dtype == "TIMESTAMP":
        return "2020-01-02 00:00:00"
    # Text columns: keep the primary-key columns identifiable and unique.
    if column == "ts_code":
        return "600000.SH"
    if column == "con_code":
        return "000001.SZ"
    if column == "index_code":
        return "000300.SH"
    if column == "exchange":
        return "SSE"
    if column == "level":
        return "L1"
    return f"x{column}"[:32]


def _frame_for(con, dataset: str) -> pd.DataFrame:
    """Build a one-row frame matching the declared schema of `dataset`."""
    types = common.schema_types(con, dataset)
    data: dict[str, object] = {}
    for index, (column, dtype) in enumerate(types.items()):
        data[column] = [_dummy_value(column, dtype, index)]
    return pd.DataFrame(data)


@pytest.mark.parametrize("dataset", ALL_DATASETS)
def test_every_dataset_loads_into_duckdb(qdb, dataset):
    """One partition of every dataset must reach DuckDB with one row."""
    key = PARTITION_KEY[dataset]
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        types = common.schema_types(con, dataset)
        frame = _frame_for(con, dataset)
        qdb.write_partition(frame, dataset, key, "test")
        loaded = qdb.load_partitions(con, dataset, [key])

        assert loaded == 1
        rows = con.execute(f"SELECT count(*) FROM {dataset}").fetchone()[0]
        assert rows == 1

        # The primary key columns must have survived the cast intact.
        pk = common.TABLE_PK[dataset]
        for column in pk:
            value = con.execute(
                f'SELECT "{column}" FROM {dataset} LIMIT 1'
            ).fetchone()[0]
            assert value is not None, f"{dataset}.{column} loaded as NULL"

        # A DATE column must stay a DATE, not degrade to a string.
        date_columns = common.DATE_COLUMNS.get(dataset, set())
        for column in date_columns:
            if column not in types:
                continue
            stored_type = con.execute(
                f"SELECT typeof(\"{column}\") FROM {dataset} LIMIT 1"
            ).fetchone()[0]
            assert stored_type == "DATE", f"{dataset}.{column} is {stored_type}"


@pytest.mark.parametrize("dataset", ALL_DATASETS)
def test_reloading_a_partition_is_idempotent(qdb, dataset):
    """Loading the same partition twice must not duplicate rows."""
    key = PARTITION_KEY[dataset]
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        frame = _frame_for(con, dataset)
        qdb.write_partition(frame, dataset, key, "test")
        qdb.load_partitions(con, dataset, [key])
        qdb.load_partitions(con, dataset, [key])
        rows = con.execute(f"SELECT count(*) FROM {dataset}").fetchone()[0]
        assert rows == 1
