"""Regression tests for the quality gates.

Two classes of defect are covered here:

* G5 bound the Sina day key (``YYYYMMDD``) straight into a DATE column and
  raised a ConversionException, so the cross-check could never pass;
* G1..G6 reported PASS on data that was simply absent - an empty table, a
  dataset with no partitions - which is a false green, not a clean run.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from db.checks import checks


# --------------------------------------------------------------------------- #
# date normalisation
# --------------------------------------------------------------------------- #


def test_iso_date_converts_compact_and_iso_forms():
    assert checks._iso_date("20200102") == "2020-01-02"
    assert checks._iso_date("2020-01-02") == "2020-01-02"
    assert checks._iso_date("20060104") == "2006-01-04"


def test_iso_date_rejects_garbage():
    assert checks._iso_date("") is None
    assert checks._iso_date("not-a-date") is None
    assert checks._iso_date("202001") is None


# --------------------------------------------------------------------------- #
# G5 - Sina cross-check
# --------------------------------------------------------------------------- #


def test_sina_crosscheck_binds_date_against_a_date_column(qdb, monkeypatch):
    """The Sina day key must be converted before it is bound as a parameter."""
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "trade_date": ["2020-01-02", "2020-01-03"],
            "close": [12.47, 12.60],
        }
    )
    qdb.write_partition(frame, "daily", "20200102", "test")
    qdb.write_partition(frame, "daily", "20200103", "test")

    captured: dict = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        captured["input"] = json.loads(input)
        payload = {
            "closes": {"sh600000": {"20200102": 12.47, "20200103": 12.80}},
            "errors": {},
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "daily", ["20200102", "20200103"])
        con.execute(
            "INSERT INTO trade_cal (exchange, cal_date, is_open) "
            "VALUES ('SSE', DATE '2020-01-02', 1), ('SSE', DATE '2020-01-03', 1)"
        )
        ok, detail = checks.check_sina_crosscheck(con, sample_days=2)

    assert "error" not in detail, detail
    assert detail["compared_pairs"] == 2
    assert detail["matched_pairs"] == 1
    assert detail["violations"], "a 0.01 close difference must be reported"
    assert ok is False


def test_sina_crosscheck_passes_within_tolerance(qdb, monkeypatch):
    frame = pd.DataFrame(
        {"ts_code": ["600000.SH"], "trade_date": ["2020-01-02"], "close": [12.47]}
    )
    qdb.write_partition(frame, "daily", "20200102", "test")

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        payload = {"closes": {"sh600000": {"20200102": 12.47}}, "errors": {}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.load_partitions(con, "daily", ["20200102"])
        con.execute(
            "INSERT INTO trade_cal (exchange, cal_date, is_open) "
            "VALUES ('SSE', DATE '2020-01-02', 1)"
        )
        ok, detail = checks.check_sina_crosscheck(con, sample_days=1)

    assert ok is True, detail
    assert detail["max_abs_diff"] == 0.0


def test_sina_crosscheck_fails_when_nothing_is_compared(qdb, monkeypatch):
    """`compared_pairs == 0` must not be reported as a pass."""

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
        payload = {"closes": {"sh600000": {}}, "errors": {}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)

    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        con.execute(
            "INSERT INTO trade_cal (exchange, cal_date, is_open) "
            "VALUES ('SSE', DATE '2020-01-02', 1)"
        )
        ok, detail = checks.check_sina_crosscheck(con, sample_days=1)

    assert ok is False
    assert detail["compared_pairs"] == 0


# --------------------------------------------------------------------------- #
# vacuous passes
# --------------------------------------------------------------------------- #


def test_g3_fails_on_an_empty_daily_table(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        ok, detail = checks.check_ohlc(con)
    assert ok is False
    assert detail["rows_checked"] == 0
    assert detail["empty_table"] is True


def test_g4_fails_on_an_empty_adj_factor_table(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        ok, detail = checks.check_adj_factor(con)
    assert ok is False
    assert detail["rows_checked"] == 0
    assert detail["empty_table"] is True


def test_g1_fails_on_an_empty_calendar(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        ok, detail = checks.check_trading_days(con)
    assert ok is False
    assert detail["empty_calendar"] is True


def test_g2_fails_when_a_dataset_has_no_partitions(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        ok, detail = checks.check_primary_keys(con)
    assert ok is False
    assert detail["missing_datasets"]


def test_g6_fails_when_a_dataset_has_no_partitions(qdb):
    ok, detail = checks.check_manifest_integrity(["daily", "income"])
    assert ok is False
    assert detail["missing_datasets"] == ["daily", "income"]
    assert detail["bad_total"] == 2
