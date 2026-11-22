from __future__ import annotations

import datetime as dt

import pandas as pd

from db.checks import checks
from db.etl import common


def _write_one(qdb):
    df = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "trade_date": dt.date(2020, 1, 2),
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
        ]
    )
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        work = common.conform_frame(df, "daily", con, "test")
    return common.write_partition(work, "daily", "20200102", "test")


def test_manifest_verifies_hash(qdb):
    _write_one(qdb)
    assert common.manifest_ok("daily", "20200102")
    path = common.partition_dir("daily", "20200102") / "data.parquet"
    path.write_bytes(path.read_bytes() + b"x")
    assert not common.manifest_ok("daily", "20200102")
    ok, detail = checks.check_manifest()
    assert not ok
    assert detail["bad_count"] == 1


def test_manifest_missing_file_fails(qdb):
    _write_one(qdb)
    path = common.partition_dir("daily", "20200102") / "data.parquet"
    path.unlink()
    assert not common.manifest_ok("daily", "20200102")
    ok, detail = checks.check_manifest()
    assert not ok
    assert detail["missing_count"] == 1


def test_manifest_rows_tamper_fails(qdb):
    _write_one(qdb)
    path = common.manifest_path("daily", "20200102")
    meta = common.read_json(path)
    meta["rows"] = 999999
    common.write_json_atomic(path, meta)
    assert not common.manifest_ok("daily", "20200102")
    ok, detail = checks.check_manifest()
    assert not ok
    assert detail["bad_count"] == 1


def test_raw_partition_without_manifest_fails(qdb):
    _write_one(qdb)
    common.manifest_path("daily", "20200102").unlink()
    ok, detail = checks.check_manifest()
    assert not ok
    assert detail["missing_count"] == 1
