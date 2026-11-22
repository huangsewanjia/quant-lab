"""Per-dataset statistics for the delivery ledger.

    python -m db.etl.stats            # markdown table
    python -m db.etl.stats --json     # machine readable

Reads the manifests (authoritative for rows/bytes) and the call log (for the
upstream call count and the measured wall clock).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import common
from .fetch import DATASETS, FETCH_ORDER

LOG_DIR = common.LOGS_DIR


def call_stats() -> dict[str, dict[str, Any]]:
    """Upstream calls and duration per dataset, from logs/fetch-*.jsonl."""
    stats: dict[str, dict[str, Any]] = {}
    if not LOG_DIR.exists():
        return stats
    for path in sorted(LOG_DIR.glob("fetch-*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                dataset = record.get("dataset") or "?"
                entry = stats.setdefault(dataset, {"calls": 0, "errors": 0, "ms": 0, "rows": 0})
                entry["calls"] += 1
                entry["ms"] += int(record.get("duration_ms") or 0)
                entry["rows"] += int(record.get("rows") or 0)
                if record.get("status") != "ok":
                    entry["errors"] += 1
    return stats


def dataset_stats() -> list[dict[str, Any]]:
    calls = call_stats()
    rows: list[dict[str, Any]] = []
    for name in FETCH_ORDER:
        spec = DATASETS[name]
        manifests = common.list_partitions(name)
        total_rows = 0
        total_bytes = 0
        empty = 0
        for partition in manifests:
            meta = common.read_manifest(name, partition)
            if not meta:
                continue
            total_rows += int(meta.get("rows") or 0)
            total_bytes += int(meta.get("bytes") or 0)
            if not int(meta.get("rows") or 0):
                empty += 1
        files = common.partition_globs(name)
        entry = {
            "dataset": name,
            "api": spec.api,
            "strategy": spec.strategy,
            "partition_kind": spec.partition_kind,
            "partitions": len(files),
            "manifests": len(manifests),
            "rows": total_rows,
            "bytes": total_bytes,
            "empty_partitions": empty,
            "calls": calls.get(name, {}).get("calls", 0),
            "call_errors": calls.get(name, {}).get("errors", 0),
            "upstream_ms": calls.get(name, {}).get("ms", 0),
        }
        rows.append(entry)
    return rows


def markdown(rows: list[dict[str, Any]]) -> str:
    header = (
        "| 数据集 | 接口 | 拉法 | 分区键 | 分区数 | manifest | 行数 | 字节 | 空分区 | 上游调用 | "
        "调用报错 | 上游耗时 |"
    )
    divider = "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, divider]
    for row in rows:
        lines.append(
            "| {dataset} | `{api}` | {strategy} | {partition_kind}= | {partitions} | {manifests} | "
            "{rows:,} | {bytes:,} | {empty_partitions} | {calls:,} | {call_errors} | {upstream_s:.0f} s |".format(
                upstream_s=row["upstream_ms"] / 1000.0, **row
            )
        )
    total_rows = sum(r["rows"] for r in rows)
    total_bytes = sum(r["bytes"] for r in rows)
    total_parts = sum(r["partitions"] for r in rows)
    total_calls = sum(r["calls"] for r in rows)
    total_ms = sum(r["upstream_ms"] for r in rows)
    lines.append(
        f"| **合计** | | | | **{total_parts:,}** | | **{total_rows:,}** | **{total_bytes:,}** | | "
        f"**{total_calls:,}** | **{sum(r['call_errors'] for r in rows)}** | **{total_ms / 1000.0:.0f} s** |"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m db.etl.stats")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--out", help="写入文件")
    args = parser.parse_args(argv)
    rows = dataset_stats()
    if args.json:
        text = json.dumps(rows, ensure_ascii=False, indent=2)
    else:
        text = markdown(rows)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
