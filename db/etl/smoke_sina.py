"""No-token end-to-end smoke: Sina 600000.SH -> Parquet -> DuckDB -> ql.

This writes ~20 years of daily rows, one Parquet partition per trade_date,
then loads them into DuckDB and runs ql. It is intentionally a single-stock
smoke, not a full-market backfill.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

from . import common
from .sina import AKSHARE_PYTHON, fetch_sina_history, to_canonical_daily


def _merge_partition(df: pd.DataFrame, trade_date: str, code: str) -> pd.DataFrame:
    target = common.partition_dir("daily", trade_date) / "data.parquet"
    if not target.exists():
        return df
    old = pd.read_parquet(target)
    if "ts_code" in old.columns:
        old = old[old["ts_code"] != code]
    if old.empty:
        return df
    return pd.concat([old, df], ignore_index=True)


def _build_trade_cal(dates: list[dt.date]) -> pd.DataFrame:
    ordered = sorted(set(dates))
    rows = []
    previous = None
    for day in ordered:
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": day,
                "is_open": 1,
                "pretrade_date": previous,
            }
        )
        previous = day
    return pd.DataFrame(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.etl.smoke_sina",
        description="无 token 单股冒烟：新浪 -> raw -> manifest -> DuckDB -> ql",
    )
    parser.add_argument("--code", default="600000.SH")
    parser.add_argument("--years", type=int, default=20)
    parser.add_argument("--skip-load", action="store_true", help="只落 raw/manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common.ensure_layout()
    common.ensure_db()
    code = common.normalize_ts_code(args.code)
    symbol = common.ts_code_to_sina(code)
    today = dt.date.today()
    start = today - relativedelta(years=args.years)
    report: dict = {
        "ts": common.utc_now(),
        "code": code,
        "sina_symbol": symbol,
        "akshare_python": AKSHARE_PYTHON,
        "window_start": start.isoformat(),
        "window_end": today.isoformat(),
    }

    started = time.monotonic()
    raw = fetch_sina_history(symbol)
    report["sina_20y_call_s"] = round(time.monotonic() - started, 3)
    if raw.empty:
        raise SystemExit("新浪返回空")
    raw = raw[(raw["trade_date"] >= start) & (raw["trade_date"] <= today)].copy()
    if raw.empty:
        raise SystemExit("窗口内无数据")
    canonical = to_canonical_daily(raw, code)
    report["rows_20y"] = int(len(canonical))
    report["actual_start"] = str(canonical["trade_date"].min())
    report["actual_end"] = str(canonical["trade_date"].max())

    last_date = canonical["trade_date"].max()
    started = time.monotonic()
    one = fetch_sina_history(symbol)
    report["sina_1d_call_s"] = round(time.monotonic() - started, 3)
    one = one[one["trade_date"] == last_date].copy()
    one_day = to_canonical_daily(one, code)
    report["one_day_rows"] = int(len(one_day))
    if one_day.empty:
        raise SystemExit("单交易日返回空")
    left = canonical[canonical["trade_date"] == last_date].iloc[0]
    right = one_day.iloc[0]
    report["one_day_match"] = {
        col: bool(abs(float(left[col]) - float(right[col])) < 1e-9)
        for col in ("open", "high", "low", "close", "vol", "amount")
    }

    started = time.monotonic()
    written = 0
    total_bytes = 0
    for trade_date, group in canonical.groupby("trade_date", sort=True):
        key = pd.Timestamp(trade_date).strftime("%Y%m%d")
        merged = _merge_partition(group.reset_index(drop=True), key, code)
        meta = common.write_partition(merged, "daily", key, "sina", pk=("ts_code", "trade_date"))
        written += 1
        total_bytes += int(meta["bytes"])
    report["raw_write_s"] = round(time.monotonic() - started, 3)
    report["raw_partitions"] = written
    report["raw_parquet_bytes"] = total_bytes

    cal = _build_trade_cal(list(canonical["trade_date"]))
    with common.duckdb_connect(read_only=True) as con:
        cal = common.conform_frame(cal, "trade_cal", con, "sina-derived")
    common.write_partition(cal, "trade_cal", "calendar", "sina-derived")

    if not args.skip_load:
        started = time.monotonic()
        with common.duckdb_connect() as con:
            common.apply_schema(con)
            daily_rows = common.load_partitions(con, "daily", common.list_partitions("daily"))
            cal_rows = common.load_partitions(con, "trade_cal", common.list_partitions("trade_cal"))
            con.execute("CHECKPOINT")
        report["duckdb_load_s"] = round(time.monotonic() - started, 3)
        report["duckdb_daily_rows"] = daily_rows
        report["duckdb_cal_rows"] = cal_rows

        py = str(Path(sys.executable))
        queries = [
            [
                py,
                "-m",
                "db.ql",
                "daily",
                "--code",
                code,
                "--start",
                "20200101",
                "--end",
                "20201231",
            ],
            [py, "-m", "db.ql", "sql", f"SELECT count(*) AS rows FROM daily WHERE ts_code = '{code}'"],
        ]
        ql_results = []
        for cmd in queries:
            env = dict(**__import__("os").environ)
            env["PYTHONPATH"] = str(common.PROJECT_ROOT)
            started = time.monotonic()
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                cwd=str(common.PROJECT_ROOT),
                env=env,
            )
            ql_results.append(
                {
                    "cmd": cmd,
                    "exit": proc.returncode,
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
        report["ql"] = ql_results
        (common.META_DIR / "smoke-ql.txt").write_text(
            "\n\n".join(
                f"$ {' '.join(item['cmd'])}\n{item['stdout']}\n{item['stderr']}"
                for item in ql_results
            ),
            encoding="utf-8",
        )

    out = common.META_DIR / "smoke-report.json"
    common.write_json_atomic(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
