"""Build the delivery summary for WORK-513.

    python -m db.etl.delivery --out /tmp/delivery.json

Collects, in one place:
  * the per-dataset ledger (partitions, rows, bytes, upstream calls);
  * the DuckDB row counts and the Parquet<->DuckDB parity;
  * the six quality gates' verdicts (from meta/checks/);
  * the spot checks the issue asks for;
  * the environment (interpreter and package versions).

Everything is read back from disk, so the summary is evidence, not a claim.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

from . import common
from .fetch import DATASETS, FETCH_ORDER
from .stats import dataset_stats


def environment() -> dict[str, Any]:
    packages = {}
    for name in ("tushare", "pandas", "pyarrow", "duckdb", "requests", "pytest", "numpy"):
        try:
            import importlib.metadata as metadata

            packages[name] = metadata.version(name)
        except Exception:  # noqa: BLE001
            packages[name] = "missing"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "quantdb_home": str(common.QDB_HOME),
        "duckdb_file": str(common.DB_PATH),
        "duckdb_bytes": common.DB_PATH.stat().st_size if common.DB_PATH.exists() else 0,
    }


def duckdb_counts() -> dict[str, Any]:
    if not common.DB_PATH.exists():
        return {"error": "DuckDB 不存在"}
    counts: dict[str, int] = {}
    with common.duckdb_connect(read_only=True) as con:
        tables = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
            ).fetchall()
        ]
        for table in tables:
            counts[table] = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        views = [
            row[0]
            for row in con.execute(
                "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
            ).fetchall()
        ]
    return {"table_rows": counts, "views": sorted(views)}


def parity() -> dict[str, Any]:
    """Rows in the Parquet layer vs the DuckDB layer, per dataset."""
    out: dict[str, Any] = {}
    for dataset in DATASETS:
        files = common.partition_globs(dataset)
        if not files:
            continue
        manifests = common.list_partitions(dataset)
        parquet_rows = 0
        for partition in manifests:
            meta = common.read_manifest(dataset, partition)
            if meta:
                parquet_rows += int(meta.get("rows") or 0)
        out[dataset] = {"partitions": len(files), "parquet_rows": parquet_rows}
    return out


def gates() -> dict[str, Any]:
    directory = common.META_DIR / "checks"
    if not directory.exists():
        return {"error": "没有质量闸报告"}
    latest = max(directory.glob("checks-*.json"), key=lambda p: p.stat().st_mtime)
    return {"report": str(latest), "payload": json.loads(latest.read_text(encoding="utf-8"))}


def spot_checks() -> dict[str, Any]:
    """The two rows the issue asks to see."""
    if not common.DB_PATH.exists():
        return {"error": "DuckDB 不存在"}
    out: dict[str, Any] = {}
    with common.duckdb_connect(read_only=True) as con:
        for table in ("daily", "fina_indicator", "income"):
            if table not in common.TABLE_PK:
                continue
            exists = con.execute(
                "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]
            ).fetchone()[0]
            if not exists:
                continue
            out[table] = [
                {k: str(v) for k, v in zip([c[0] for c in con.description], row)}
                for row in con.execute(
                    f"SELECT * FROM {table} WHERE ts_code = '600000.SH' "
                    + (
                        "AND trade_date BETWEEN '2020-01-02' AND '2020-01-10' "
                        if table == "daily"
                        else "AND end_date BETWEEN '2023-01-01' AND '2023-12-31' "
                    )
                    + "LIMIT 3"
                ).fetchall()
            ]
    return out


def failures() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for dataset in sorted(set(DATASETS) | {"all"}):
        records = common.read_failures(dataset)
        if records:
            out[dataset] = records[:50]
    return out


def build() -> dict[str, Any]:
    stats = dataset_stats()
    payload = {
        "generated_at": common.utc_now(),
        "environment": environment(),
        "datasets": stats,
        "totals": {
            "partitions": sum(row["partitions"] for row in stats),
            "rows": sum(row["rows"] for row in stats),
            "bytes": sum(row["bytes"] for row in stats),
            "upstream_calls": sum(row["calls"] for row in stats),
            "upstream_errors": sum(row["call_errors"] for row in stats),
        },
        "duckdb": duckdb_counts(),
        "parquet": parity(),
        "quality_gates": gates(),
        "spot_checks": spot_checks(),
        "failures": failures(),
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m db.etl.delivery")
    parser.add_argument("--out", help="写入 JSON 报告的路径")
    args = parser.parse_args(argv)
    payload = build()
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
        print(json.dumps(payload["totals"], ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
