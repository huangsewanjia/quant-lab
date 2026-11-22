"""Quality gates for the WORK-513 backfill.

Six machine-checkable gates; the runner exits non-zero when any of them fails:

  G1  every open trading day in trade_cal has a daily partition, and the row
      count of each trading day is reconciled against the rolling median of
      its neighbours (deviations above +-5% are listed individually; a shortfall
      that is not bigger than the number of stocks suspended that day is
      reported as an evidence-backed exception instead of a data defect);
  G2  primary keys are unique in the Parquet layer, and every DuckDB table has
      exactly as many rows as its Parquet partitions (a mismatch means the
      primary key silently dropped rows on load);
  G3  high >= max(open, close) and low <= min(open, close) on every daily row;
  G4  adj_factor has no zero, negative or missing value;
  G5  20 sampled trading days are compared with the Sina source (akshare),
      tolerance 0.01 CNY on close;
  G6  partition/manifest integrity: one manifest per partition, every sha256
      verified, no partition without a manifest.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from ..etl import common

AKSHARE_PYTHON = os.environ.get(
    "QUANTDB_AKSHARE_PYTHON", "/home/moyongkai/mcp/akshare-one-mcp/bin/python"
)

# Long-listed, always-traded stocks used for the Sina cross-check.
CROSSCHECK_SYMBOLS = ("sh600000", "sz000001", "sh600519", "sh601318", "sz000002")

PK_DATASETS = (
    "daily",
    "daily_basic",
    "adj_factor",
    "stk_limit",
    "suspend_d",
    "index_daily",
    "index_weight",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "dividend",
    "namechange",
)


def _code_to_symbol(code: str) -> str:
    return common.ts_code_to_sina(code)


def _iso_date(day: str) -> str | None:
    """Normalise a YYYYMMDD / YYYY-MM-DD day to the ISO form DuckDB expects.

    The Sina helper reports days as ``YYYYMMDD`` strings while the DuckDB
    ``trade_date`` column is a real DATE, so the value has to be converted
    before it can be bound as a query parameter.
    """
    text = str(day).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return None


# --------------------------------------------------------------------------- #
# G1 - trading-day reconciliation
# --------------------------------------------------------------------------- #


def _suspension_counts(con) -> dict[str, dict[str, int]]:
    """Suspended stocks per trading day, split by suspend_type.

    ``suspend_d`` holds one row per (ts_code, trade_date). ``S`` marks a stock
    that was suspended, ``R`` one that resumed. A suspended stock has no daily
    row, so the count is the upper bound of how many daily rows a day may be
    missing. A day without any suspension record is not excused.
    """
    counts: dict[str, dict[str, int]] = {}
    try:
        rows = con.execute(
            "SELECT trade_date, suspend_type, count(*) FROM suspend_d GROUP BY 1, 2"
        ).fetchall()
    except Exception:  # noqa: BLE001 - the table may not be loaded yet
        return counts
    for day, suspend_type, count in rows:
        bucket = counts.setdefault(str(day), {"total": 0, "suspended": 0, "resumed": 0})
        bucket["total"] += int(count)
        if str(suspend_type).upper() == "S":
            bucket["suspended"] += int(count)
        elif str(suspend_type).upper() == "R":
            bucket["resumed"] += int(count)
    return counts


def check_trading_days(con, tolerance: float = 0.05) -> tuple[bool, dict[str, Any]]:
    # SSE and SZSE carry the same calendar; DISTINCT keeps one row per day.
    calendar = con.execute(
        "SELECT DISTINCT cal_date FROM trade_cal WHERE is_open = 1 ORDER BY cal_date"
    ).fetchall()
    open_days = [str(row[0]) for row in calendar]
    counts = con.execute(
        "SELECT trade_date, count(*) FROM daily GROUP BY 1 ORDER BY 1"
    ).fetchall()
    by_day = {str(row[0]): int(row[1]) for row in counts}
    suspensions = _suspension_counts(con)

    missing = [day for day in open_days if day not in by_day]
    empty = [day for day in open_days if by_day.get(day) == 0]
    extra = [day for day in by_day if day not in set(open_days)]

    # Rolling median over +-5 trading days; a genuine data gap or a duplicated
    # day moves the count far away from its neighbours.
    series = pd.Series([by_day.get(day, 0) for day in open_days], index=open_days)
    rolling = series.rolling(11, center=True, min_periods=3).median()
    anomalies: list[dict[str, Any]] = []
    for day in open_days:
        count = by_day.get(day)
        median = rolling.get(day)
        if count is None or median is None or median == 0:
            continue
        deviation = abs(count - median) / median
        if deviation <= tolerance:
            continue
        # The deviation is kept as an anomaly either way. Whether it is a data
        # defect or a market-wide suspension is decided by the evidence: a
        # suspended stock has no daily row, so a shortfall that is not bigger
        # than the number of stocks suspended that day cannot be missing data.
        shortfall = max(0.0, float(median) - float(count))
        suspension = suspensions.get(day, {"total": 0, "suspended": 0, "resumed": 0})
        explained = shortfall <= suspension["suspended"]
        anomalies.append(
            {
                "trade_date": day,
                "rows": count,
                "rolling_median": float(median),
                "deviation": round(float(deviation), 4),
                "shortfall_rows": int(round(shortfall)),
                "suspend_rows": suspension["total"],
                "suspend_rows_s": suspension["suspended"],
                "suspend_rows_r": suspension["resumed"],
                "judgement": "explained_by_suspensions" if explained else "unexplained",
            }
        )
    anomalies.sort(key=lambda item: -item["deviation"])
    exceptions = [item for item in anomalies if item["judgement"] == "explained_by_suspensions"]
    unexplained = [item for item in anomalies if item["judgement"] == "unexplained"]
    ok = (
        not missing
        and not empty
        and not extra
        and not unexplained
        and bool(open_days)
        and bool(by_day)
    )
    return ok, {
        "open_trading_days": len(open_days),
        "days_with_rows": len(by_day),
        "empty_calendar": not open_days,
        "empty_daily": not by_day,
        "missing_days": missing[:50],
        "missing_day_count": len(missing),
        "empty_days": empty[:50],
        "empty_day_count": len(empty),
        "days_not_in_calendar": extra[:50],
        "deviation_tolerance": tolerance,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies[:50],
        "exception_count": len(exceptions),
        "exceptions": exceptions[:50],
        "unexplained_count": len(unexplained),
        "unexplained_anomalies": unexplained[:50],
        "judgement_rule": (
            "偏差日先按 11 日居中滚动中位数记为 anomaly；缺口行数 <= 当日 suspend_d 中 "
            "suspend_type='S' 的停牌家数，判为停牌可解释例外（PASS-with-exceptions），"
            "否则判为未解释异常，闸门 FAIL。阈值 5% 不放宽，anomaly 记录全部保留。"
        ),
        "suspension_source": "suspend_d 表按 trade_date 统计（S=停牌，R=复牌）",
        "days_with_suspension_records": len(suspensions),
        "conclusion": "PASS_WITH_EXCEPTIONS" if exceptions and not unexplained else (
            "PASS" if ok else "FAIL"
        ),
    }


# --------------------------------------------------------------------------- #
# G2 - primary key uniqueness and Parquet/DuckDB parity
# --------------------------------------------------------------------------- #


def check_primary_keys(con) -> tuple[bool, dict[str, Any]]:
    parity: dict[str, Any] = {}
    duplicates: dict[str, int] = {}
    missing: list[str] = []

    for dataset in PK_DATASETS:
        files = common.partition_globs(dataset)
        if not files:
            # A dataset with no partitions at all would otherwise "pass" this
            # gate with zero rows - that is a missing dataset, not a clean one.
            missing.append(dataset)
            parity[dataset] = {"partitions": 0, "note": "无分区", "difference": 0}
            continue
        glob = common.parquet_glob(dataset)
        pk = [c for c in common.TABLE_PK[dataset] if c in common.canonical_columns(dataset)]
        parquet_rows = int(
            con.execute(
                f"SELECT count(*) FROM {common.PARQUET_SCAN}", [glob]
            ).fetchone()[0]
        )
        if pk:
            group = ",".join(str(index + 1) for index in range(len(pk)))
            dup_count = int(
                con.execute(
                    "SELECT count(*) FROM ("
                    f"SELECT {', '.join(chr(34) + c + chr(34) for c in pk)}, count(*) AS c "
                    f"FROM {common.PARQUET_SCAN} GROUP BY {group} HAVING c > 1)",
                    [glob],
                ).fetchone()[0]
            )
        else:  # pragma: no cover - every dataset declares a primary key
            dup_count = 0
        table_rows = int(con.execute(f"SELECT count(*) FROM {dataset}").fetchone()[0])
        duplicates[dataset] = dup_count
        parity[dataset] = {
            "parquet_rows": parquet_rows,
            "duckdb_rows": table_rows,
            "difference": parquet_rows - table_rows,
            "partitions": len(files),
            "duplicate_key_groups": dup_count,
        }

    total_dup = sum(duplicates.values())
    mismatched = {k: v for k, v in parity.items() if v.get("difference", 0) != 0}
    ok = total_dup == 0 and not mismatched and not missing
    return ok, {
        "duplicate_keys": duplicates,
        "total_duplicate_key_groups": total_dup,
        "row_parity": parity,
        "mismatched_datasets": mismatched,
        "missing_datasets": missing,
    }


# --------------------------------------------------------------------------- #
# G3 - OHLC consistency
# --------------------------------------------------------------------------- #

_OHLC_VIOLATION = (
    "(high IS NOT NULL AND open IS NOT NULL AND close IS NOT NULL "
    " AND high < greatest(open, close)) "
    "OR (low IS NOT NULL AND open IS NOT NULL AND close IS NOT NULL "
    " AND low > least(open, close))"
)


def check_ohlc(con, limit: int = 20) -> tuple[bool, dict[str, Any]]:
    total = int(con.execute("SELECT count(*) FROM daily").fetchone()[0])
    violations = int(
        con.execute(f"SELECT count(*) FROM daily WHERE {_OHLC_VIOLATION}").fetchone()[0]
    )
    samples = con.execute(
        "SELECT ts_code, trade_date, open, high, low, close FROM daily WHERE "
        f"{_OHLC_VIOLATION} LIMIT ?",
        [limit],
    ).fetchall()
    null_rows = int(
        con.execute(
            "SELECT count(*) FROM daily WHERE open IS NULL OR high IS NULL "
            "OR low IS NULL OR close IS NULL"
        ).fetchone()[0]
    )
    ok = violations == 0 and total > 0
    return ok, {
        "rows_checked": total,
        "violations": violations,
        "violation_samples": [list(row) for row in samples],
        "rows_with_null_ohlc": null_rows,
        "empty_table": total == 0,
    }


# --------------------------------------------------------------------------- #
# G4 - adjustment factors
# --------------------------------------------------------------------------- #


def check_adj_factor(con, limit: int = 20) -> tuple[bool, dict[str, Any]]:
    total = int(con.execute("SELECT count(*) FROM adj_factor").fetchone()[0])
    non_positive = int(
        con.execute(
            "SELECT count(*) FROM adj_factor WHERE adj_factor IS NOT NULL AND adj_factor <= 0"
        ).fetchone()[0]
    )
    nulls = int(
        con.execute("SELECT count(*) FROM adj_factor WHERE adj_factor IS NULL").fetchone()[0]
    )
    samples = con.execute(
        "SELECT ts_code, trade_date, adj_factor FROM adj_factor "
        "WHERE adj_factor IS NULL OR adj_factor <= 0 LIMIT ?",
        [limit],
    ).fetchall()
    ok = non_positive == 0 and nulls == 0 and total > 0
    return ok, {
        "rows_checked": total,
        "non_positive": non_positive,
        "nulls": nulls,
        "samples": [list(row) for row in samples],
        "empty_table": total == 0,
    }


# --------------------------------------------------------------------------- #
# G5 - cross-check against the Sina source
# --------------------------------------------------------------------------- #


def check_sina_crosscheck(
    con, sample_days: int = 20, tolerance: float = 0.01
) -> tuple[bool, dict[str, Any]]:
    open_days = [
        str(row[0])
        for row in con.execute(
            "SELECT DISTINCT cal_date FROM trade_cal WHERE is_open = 1 ORDER BY cal_date"
        ).fetchall()
    ]
    if not open_days:
        return False, {"error": "trade_cal 为空"}
    step = max(1, len(open_days) // sample_days)
    sampled = open_days[::step][:sample_days]

    symbols = list(CROSSCHECK_SYMBOLS)
    pairs = [[symbol, day.replace("-", "")] for symbol in symbols for day in sampled]

    script = Path(__file__).with_name("sina_crosscheck.py")
    if not Path(AKSHARE_PYTHON).exists():
        return False, {"error": f"akshare python 不存在: {AKSHARE_PYTHON}"}
    try:
        completed = subprocess.run(
            [AKSHARE_PYTHON, str(script)],
            input=json.dumps({"pairs": pairs}),
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return False, {"error": "akshare 交叉校验超时"}
    if completed.returncode != 0:
        return False, {"error": f"akshare 交叉校验失败: {completed.stderr[:400]}"}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return False, {"error": f"akshare 输出无法解析: {exc}"}
    closes = payload.get("closes") or {}
    errors = payload.get("errors") or {}

    compared = 0
    matched = 0
    violations: list[dict[str, Any]] = []
    max_diff = 0.0
    for symbol, per_day in closes.items():
        code = common.normalize_ts_code(symbol)
        for day, sina_close in per_day.items():
            iso = _iso_date(day)
            if iso is None:
                continue
            tushare = con.execute(
                "SELECT close FROM daily WHERE ts_code = ? AND trade_date = ?::DATE",
                [code, iso],
            ).fetchone()
            if tushare is None:
                continue
            compared += 1
            diff = abs(float(tushare[0]) - float(sina_close))
            max_diff = max(max_diff, diff)
            if diff <= tolerance:
                matched += 1
            else:
                violations.append(
                    {
                        "ts_code": code,
                        "trade_date": day,
                        "tushare_close": float(tushare[0]),
                        "sina_close": float(sina_close),
                        "diff": round(diff, 4),
                    }
                )
    ok = compared > 0 and not violations and not errors
    return ok, {
        "sampled_trading_days": len(sampled),
        "sampled_dates": sampled,
        "symbols": symbols,
        "requested_pairs": len(pairs),
        "compared_pairs": compared,
        "matched_pairs": matched,
        "pairs_missing_in_sina": len(pairs) - compared,
        "tolerance": tolerance,
        "max_abs_diff": round(max_diff, 6),
        "violations": violations[:20],
        "source_errors": errors,
    }


# --------------------------------------------------------------------------- #
# G6 - partition / manifest integrity
# --------------------------------------------------------------------------- #


def check_manifest_integrity(datasets: list[str]) -> tuple[bool, dict[str, Any]]:
    details: dict[str, Any] = {}
    bad = 0
    missing_datasets: list[str] = []
    for dataset in datasets:
        partitions = common.list_partitions(dataset)
        files = common.partition_globs(dataset)
        if not files:
            # No partition at all means the dataset was never fetched; that is
            # a failure, not an integrity pass.
            missing_datasets.append(dataset)
        missing_file = 0
        hash_mismatch = 0
        unreadable_manifest = 0
        for partition in partitions:
            meta = common.read_manifest(dataset, partition)
            if not meta:
                unreadable_manifest += 1
                continue
            path = common.QDB_HOME / str(meta.get("file", ""))
            if not path.exists():
                missing_file += 1
                continue
            if path.stat().st_size != int(meta.get("bytes", -1)):
                hash_mismatch += 1
                continue
            if common.sha256_file(path) != meta.get("sha256"):
                hash_mismatch += 1
        on_disk = {
            path.parent.name.split("=", 1)[1]
            for path in common.RAW_DIR.glob(f"{dataset}/*/part.parquet")
        }
        without_manifest = sorted(on_disk - set(partitions))
        bad += missing_file + hash_mismatch + unreadable_manifest + len(without_manifest)
        details[dataset] = {
            "partitions": len(files),
            "manifests": len(partitions),
            "missing_file": missing_file,
            "hash_mismatch": hash_mismatch,
            "unreadable_manifest": unreadable_manifest,
            "partitions_without_manifest": len(without_manifest),
            "examples_without_manifest": without_manifest[:10],
            "missing_dataset": not files,
        }
    bad += len(missing_datasets)
    return bad == 0, {
        "bad_total": bad,
        "missing_datasets": missing_datasets,
        "datasets": details,
    }


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #


def run_all(
    datasets: list[str] | None = None,
    sample_days: int = 20,
    tolerance: float = 0.01,
) -> tuple[bool, dict[str, Any]]:
    datasets = datasets or list(common.TABLE_PK)
    report: dict[str, Any] = {"generated_at": common.utc_now()}
    gates = [
        ("G1_trading_day_reconciliation", None),
        ("G2_primary_keys_and_parity", None),
        ("G3_ohlc_consistency", None),
        ("G4_adj_factor_positive", None),
        ("G5_sina_crosscheck", None),
        ("G6_manifest_integrity", None),
    ]
    with common.duckdb_connect(read_only=True) as con:
        bound = {
            "G1_trading_day_reconciliation": lambda: check_trading_days(con),
            "G2_primary_keys_and_parity": lambda: check_primary_keys(con),
            "G3_ohlc_consistency": lambda: check_ohlc(con),
            "G4_adj_factor_positive": lambda: check_adj_factor(con),
            "G5_sina_crosscheck": lambda: check_sina_crosscheck(
                con, sample_days=sample_days, tolerance=tolerance
            ),
            "G6_manifest_integrity": lambda: check_manifest_integrity(datasets),
        }
        for name, _ in gates:
            try:
                ok, detail = bound[name]()
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
            report[name] = {"pass": bool(ok), **detail}
    report["all_pass"] = all(report[name]["pass"] for name, _ in gates)
    return report["all_pass"], report
