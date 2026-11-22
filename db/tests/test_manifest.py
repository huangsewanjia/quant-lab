"""Manifest contract: rows + sha256 + bytes + fetch time + query parameters."""

from __future__ import annotations

import json

import pandas as pd

from db.etl import common


def _frame(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [f"{600000 + i:06d}.SH" for i in range(rows)],
            "trade_date": ["20261120"] * rows,
            "close": [10.0 + i for i in range(rows)],
        }
    )


def test_manifest_is_written_next_to_the_partition(qdb):
    meta = qdb.write_partition(_frame(), "daily", "20261120", "tushare")
    manifest = qdb.manifest_path("daily", "20261120")
    assert manifest.exists()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["rows"] == 3
    assert payload["bytes"] == qdb.partition_file("daily", "20261120").stat().st_size
    assert payload["sha256"] == meta["sha256"]
    assert payload["source"] == "tushare"
    assert payload["fetched_at"].endswith("Z")
    assert payload["primary_key"] == ["ts_code", "trade_date"]
    assert payload["file"] == "raw/daily/trade_date=20261120/part.parquet"


def test_partition_layout_follows_the_issue_contract(qdb):
    qdb.write_partition(_frame(), "daily", "20261120", "tushare")
    qdb.write_partition(_frame(), "income", "600000.SH", "tushare")
    assert (qdb.RAW_DIR / "daily" / "trade_date=20261120" / "part.parquet").exists()
    assert (qdb.RAW_DIR / "income" / "ts_code=600000.SH" / "part.parquet").exists()


def test_manifest_ok_detects_a_modified_file(qdb):
    qdb.write_partition(_frame(), "daily", "20261120", "tushare")
    assert qdb.manifest_ok("daily", "20261120") is True
    target = qdb.partition_file("daily", "20261120")
    target.write_bytes(target.read_bytes() + b"tamper")
    assert qdb.manifest_ok("daily", "20261120") is False


def test_manifest_ok_detects_a_missing_file(qdb):
    qdb.write_partition(_frame(), "daily", "20261120", "tushare")
    qdb.partition_file("daily", "20261120").unlink()
    assert qdb.manifest_ok("daily", "20261120") is False


def test_manifest_ok_is_false_without_a_manifest(qdb):
    assert qdb.manifest_ok("daily", "19900101") is False


def test_manifest_count_matches_partition_count(qdb):
    for day in ("20261119", "20261120", "20261123"):
        qdb.write_partition(_frame(), "daily", day, "tushare")
    partitions = sorted(p.parent.name for p in qdb.RAW_DIR.glob("daily/*/part.parquet"))
    manifests = sorted(p.stem for p in (qdb.META_DIR / "daily").glob("*.json"))
    assert len(partitions) == len(manifests) == 3
    assert [p.split("=")[1] for p in partitions] == manifests


def test_failed_partitions_are_recorded_and_deduplicated(qdb):
    qdb.append_failures([{"dataset": "daily", "partition": "20261120", "error": "boom"}], "daily")
    qdb.append_failures([{"dataset": "daily", "partition": "20261120", "error": "boom"}], "daily")
    records = qdb.read_failures("daily")
    assert len(records) == 1
    assert records[0]["partition"] == "20261120"
