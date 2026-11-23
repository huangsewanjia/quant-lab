"""Weekly data-health report for quantdb.

    python -m db.etl.weekly
    python -m db.etl.weekly --days 7 --json

The report answers four questions the user asked for:

* successful upstream call rate;
* missing partitions (market calendar gaps and the failure ledger);
* retry count;
* row-count change against the previous weekly snapshot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from . import common, incremental
from .fetch import DATASETS, FETCH_ORDER


def report_dir() -> Path:
    return common.META_DIR / "weekly"


def _parse_ts(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _db_counts() -> dict[str, int]:
    if not common.DB_PATH.exists():
        return {}
    con = common.duckdb_connect(read_only=True)
    try:
        counts: dict[str, int] = {}
        for table in common.TABLE_PK:
            try:
                counts[table] = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except Exception:
                counts[table] = 0
        return counts
    finally:
        con.close()


def _db_latest() -> dict[str, str]:
    if not common.DB_PATH.exists():
        return {}
    con = common.duckdb_connect(read_only=True)
    try:
        latest: dict[str, str] = {}
        for table in ("daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d", "index_daily"):
            try:
                value = con.execute(f"SELECT max(trade_date) FROM {table}").fetchone()[0]
            except Exception:
                continue
            if value:
                latest[table] = str(value).replace("-", "")
        return latest
    finally:
        con.close()


def _raw_stats() -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for dataset in FETCH_ORDER:
        partitions = common.list_partitions(dataset)
        rows = 0
        empty = 0
        for partition in partitions:
            meta = common.read_manifest(dataset, partition) or {}
            value = int(meta.get("rows") or 0)
            rows += value
            if value == 0:
                empty += 1
        stats[dataset] = {
            "partitions": len(partitions),
            "rows": rows,
            "empty_partitions": empty,
            "latest": max(
                (p for p in partitions if len(p) == 8 and p.isdigit()),
                default=None,
            ),
        }
    return stats


def _call_stats(days: int) -> dict[str, Any]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    calls = 0
    ok = 0
    errors = 0
    retries = 0
    by_dataset: dict[str, dict[str, int]] = {}
    for path in sorted(common.LOGS_DIR.glob("fetch-*.jsonl")):
        if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc) < cutoff:
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                calls += 1
                dataset = str(record.get("dataset") or "?")
                entry = by_dataset.setdefault(dataset, {"calls": 0, "ok": 0, "errors": 0, "retries": 0})
                entry["calls"] += 1
                if record.get("status") == "ok":
                    ok += 1
                    entry["ok"] += 1
                else:
                    errors += 1
                    entry["errors"] += 1
                if int(record.get("attempt") or 1) > 1:
                    retries += 1
                    entry["retries"] += 1
    return {
        "calls": calls,
        "ok": ok,
        "errors": errors,
        "retries": retries,
        "success_rate": round(ok / calls, 4) if calls else None,
        "datasets": by_dataset,
    }


def _failure_count() -> int:
    total = 0
    for path in common.FAILED_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            total += 1
            continue
        if isinstance(payload, list):
            total += len(payload)
        elif isinstance(payload, dict):
            total += len(payload.get("failures") or payload.get("items") or [payload])
        else:
            total += 1
    return total


def _task_retries(days: int) -> int:
    """Wrapper retry attempts recorded as `attempt=N` in task logs."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    total = 0
    for task in ("daily", "finance", "policy", "weekly"):
        for path in common.LOGS_DIR.glob(f"{task}-*.log"):
            if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc) < cutoff:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    match = re.search(r"attempt=(\d+)", line)
                    if match and int(match.group(1)) > 1:
                        total += 1
    return total


def _calendar_gaps(
    raw: dict[str, dict[str, int]], now: dt.datetime | None = None
) -> dict[str, int]:
    try:
        frame = incremental.calendar_frame()
        days = [str(d).replace("-", "") for d in frame.query("is_open == 1")["cal_date"]]
        end = incremental.effective_end(now or incremental.parse_now(), frame)
    except Exception:
        return {}
    gaps: dict[str, int] = {}
    for dataset in ("daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d", "index_daily"):
        entry = raw.get(dataset)
        if not entry:
            continue
        have = set()
        for partition in common.list_partitions(dataset):
            meta = common.read_manifest(dataset, partition) or {}
            if not meta or meta.get("provisional"):
                continue
            if common.manifest_file_ok(meta, verify_hash=False):
                have.add(partition)
        if not have:
            continue
        # Right edge is the latest completed close, not the latest observed
        # partition: a dataset that stopped days ago must not look gap-free.
        expected = [d for d in days if min(have) <= d <= end]
        gaps[dataset] = max(0, len(expected) - len([d for d in expected if d in have]))
    return gaps


def _previous() -> dict[str, Any]:
    path = report_dir() / "last.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build(days: int = 7) -> dict[str, Any]:
    raw = _raw_stats()
    db = _db_counts()
    db_latest = _db_latest()
    calls = _call_stats(days)
    gaps = _calendar_gaps(raw)
    previous = _previous()
    previous_rows = previous.get("db_rows") or {}
    datasets: dict[str, Any] = {}
    for dataset in FETCH_ORDER:
        raw_rows = raw.get(dataset, {}).get("rows", 0)
        db_rows = db.get(dataset, 0)
        datasets[dataset] = {
            "api": DATASETS[dataset].api,
            "db_rows": db_rows,
            "raw_rows": raw_rows,
            "partitions": raw.get(dataset, {}).get("partitions", 0),
            "empty_partitions": raw.get(dataset, {}).get("empty_partitions", 0),
            "calendar_gap": gaps.get(dataset, 0),
            "latest_raw": raw.get(dataset, {}).get("latest"),
            "latest_db": db_latest.get(dataset),
            "row_delta": (db_rows - int(previous_rows.get(dataset, db_rows))) if dataset in previous_rows else None,
        }
    report = {
        "generated_at": common.utc_now(),
        "period_days": days,
        "calls": calls,
        "task_retries": _task_retries(days),
        "failure_ledger_entries": _failure_count(),
        "calendar_gaps": gaps,
        "db_rows": db,
        "datasets": datasets,
        "total_db_rows": sum(db.values()),
        "previous_generated_at": previous.get("generated_at"),
    }
    return report


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# quantdb 周报",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 统计窗口：最近 {report['period_days']} 天",
        f"- 上游调用：{report['calls']['calls']:,}（成功 {report['calls']['ok']:,}，"
        f"失败 {report['calls']['errors']:,}，成功率 "
        f"{report['calls']['success_rate'] if report['calls']['success_rate'] is not None else '无数据'}）",
        f"- 重试次数：{report['calls']['retries']:,}",
        f"- wrapper 重试次数：{report['task_retries']:,}",
        f"- 失败清单条目：{report['failure_ledger_entries']:,}",
        f"- DuckDB 总行数：{report['total_db_rows']:,}",
        "",
        "| 数据集 | 表内行数 | Parquet 行数 | 最新 raw | 最新 DB | 分区 | 空分区 | 日历缺口 | 周变化 |",
        "|---|---:|---:|---|---|---:|---:|---:|---:|",
    ]
    for name, entry in report["datasets"].items():
        delta = entry["row_delta"]
        delta_text = "首次" if delta is None else f"{delta:+,}"
        lines.append(
            f"| `{name}` | {entry['db_rows']:,} | {entry['raw_rows']:,} | "
            f"{entry['latest_raw'] or '-'} | {entry['latest_db'] or '-'} | "
            f"{entry['partitions']:,} | {entry['empty_partitions']:,} | "
            f"{entry['calendar_gap']:,} | {delta_text} |"
        )
    lines.append("")
    lines.append("> 空分区表示上游确实没有该日数据；日历缺口表示该交易日连空分区都没有。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m db.etl.weekly")
    parser.add_argument("--days", type=int, default=7, help="调用统计窗口")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--out", help="额外写入路径")
    args = parser.parse_args(argv)
    common.ensure_layout()
    report = build(args.days)
    out_dir = report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"weekly-{stamp}.json"
    md_path = out_dir / f"weekly-{stamp}.md"
    common.write_json_atomic(json_path, report)
    md_path.write_text(markdown(report), encoding="utf-8")
    common.write_json_atomic(out_dir / "last.json", report)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(markdown(report))
    rate = report["calls"]["success_rate"]
    print(
        "[weekly] success={rate} calls={calls} retries={retries} wrapper_retries={wrapper_retries} "
        "failure_ledger={failures} rows={rows}".format(
            rate="n/a" if rate is None else f"{rate:.2%}",
            calls=report["calls"]["calls"],
            retries=report["calls"]["retries"],
            wrapper_retries=report["task_retries"],
            failures=report["failure_ledger_entries"],
            rows=report["total_db_rows"],
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
