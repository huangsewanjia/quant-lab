"""Quality gate implementations. Each returns (ok, detail)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..etl import common
from ..etl.sina import AKSHARE_PYTHON, fetch_sina_history, to_canonical_daily


class CheckError(RuntimeError):
    pass


def check_manifest(verify_hash: bool = True) -> tuple[bool, dict[str, Any]]:
    missing: list[str] = []
    bad: list[str] = []
    raw_keys: set[tuple[str, str]] = set()
    for dataset in common.TABLE_PK:
        base = common.RAW_DIR / dataset
        if not base.exists():
            continue
        for folder in base.iterdir():
            if folder.is_dir() and any(folder.glob("*.parquet")):
                key = folder.name.split("=", 1)[1] if "=" in folder.name else folder.name
                raw_keys.add((dataset, key))
    manifest_keys = {
        (dataset, partition)
        for dataset in common.TABLE_PK
        for partition in common.list_partitions(dataset)
    }
    for dataset, partition in sorted(raw_keys - manifest_keys):
        missing.append(f"{dataset}/{partition}: 有 parquet 但缺 manifest")
    total = 0
    for dataset, partition in sorted(manifest_keys):
        total += 1
        meta = common.read_manifest(dataset, partition)
        if not meta:
            missing.append(f"{dataset}/{partition}: manifest 不可读")
            continue
        if meta.get("dataset") != dataset or meta.get("partition") != common.safe_key(partition):
            bad.append(
                f"{dataset}/{partition}: identity={meta.get('dataset')}/{meta.get('partition')}"
            )
            continue
        file_path = common.manifest_file_path(dataset, partition, meta)
        if file_path is None:
            bad.append(f"{dataset}/{partition}: file 路径不在分区目录内")
            continue
        if not file_path.exists():
            missing.append(f"{dataset}/{partition}: {file_path} 不存在")
            continue
        expected_bytes = common.strict_nonneg_int(meta.get("bytes"))
        expected_rows = common.strict_nonneg_int(meta.get("rows"))
        if expected_bytes is None or expected_rows is None:
            bad.append(f"{dataset}/{partition}: bytes/rows 不是非负整数")
            continue
        if file_path.stat().st_size != expected_bytes:
            bad.append(f"{dataset}/{partition}: 字节数不匹配")
            continue
        if verify_hash and common.sha256_file(file_path) != meta.get("sha256"):
            bad.append(f"{dataset}/{partition}: sha256 不匹配")
            continue
        try:
            import pyarrow.parquet as pq

            actual_rows = int(pq.ParquetFile(file_path).metadata.num_rows)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{dataset}/{partition}: 行数不可读 ({exc})")
            continue
        if actual_rows != expected_rows:
            bad.append(
                f"{dataset}/{partition}: 行数 {actual_rows} != manifest {meta.get('rows')}"
            )
        covered = {str(file_path.relative_to(common.QDB_HOME))}
        extra = {
            str(path.relative_to(common.QDB_HOME))
            for path in common.partition_dir(dataset, partition).glob("*.parquet")
            if str(path.relative_to(common.QDB_HOME)) not in covered
        }
        if extra:
            bad.append(
                f"{dataset}/{partition}: 有 manifest 未覆盖的 parquet: {sorted(extra)[:3]}"
            )
    ok = not missing and not bad
    return ok, {
        "partitions": total,
        "raw_partitions": len(raw_keys),
        "missing": missing[:50],
        "bad": bad[:50],
        "missing_count": len(missing),
        "bad_count": len(bad),
    }


def check_calendar(con) -> tuple[bool, dict[str, Any]]:
    try:
        daily_dates = {
            str(row[0])
            for row in con.execute("SELECT DISTINCT trade_date FROM daily").fetchall()
        }
        open_dates = {
            str(row[0])
            for row in con.execute(
                "SELECT cal_date FROM trade_cal WHERE is_open = 1"
            ).fetchall()
        }
    except Exception as exc:  # noqa: BLE001
        raise CheckError(f"读取 daily/trade_cal 失败: {exc}") from exc
    if not daily_dates:
        raise CheckError("daily 无数据")
    if not open_dates:
        raise CheckError("trade_cal 无交易日记录")
    closed_with_data = sorted(daily_dates - open_dates)
    lo, hi = min(daily_dates), max(daily_dates)
    expected = {d for d in open_dates if lo <= d <= hi}
    missing = sorted(expected - daily_dates)
    ok = not closed_with_data and not missing
    return ok, {
        "daily_dates": len(daily_dates),
        "open_dates_in_range": len(expected),
        "closed_with_data": closed_with_data[:30],
        "missing_open_dates": missing[:30],
        "closed_with_data_count": len(closed_with_data),
        "missing_open_count": len(missing),
    }


def check_pk(con, datasets: list[str] | None = None) -> tuple[bool, dict[str, Any]]:
    targets = datasets or list(common.TABLE_PK)
    details: dict[str, Any] = {}
    ok = True
    for dataset in targets:
        pk = common.TABLE_PK[dataset]
        try:
            total = int(con.execute(f"SELECT count(*) FROM {dataset}").fetchone()[0])
        except Exception as exc:  # noqa: BLE001
            details[dataset] = {"error": f"表不可读: {exc}"}
            ok = False
            continue
        if total == 0:
            details[dataset] = {"rows": 0}
            continue
        cols = ", ".join(pk)
        dup = int(
            con.execute(
                f"SELECT count(*) FROM (SELECT {cols} FROM {dataset} "
                f"GROUP BY {cols} HAVING count(*) > 1)"
            ).fetchone()[0]
        )
        details[dataset] = {"rows": total, "duplicate_keys": dup}
        if dup:
            ok = False
    return ok, details


def check_ohlc(con) -> tuple[bool, dict[str, Any]]:
    total = int(con.execute("SELECT count(*) FROM daily").fetchone()[0])
    bad = int(
        con.execute(
            """
            SELECT count(*) FROM daily
            WHERE high < greatest(open, close)
               OR low > least(open, close)
               OR high < low
               OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
            """
        ).fetchone()[0]
    )
    return bad == 0, {"rows": total, "bad_rows": bad}


def check_cross_source(
    con, sample: int = 5, tolerance: float = 0.01
) -> tuple[bool, dict[str, Any]]:
    if sample <= 0:
        raise CheckError("sample 必须 > 0")
    codes = [
        row[0]
        for row in con.execute(
            f"SELECT ts_code FROM (SELECT DISTINCT ts_code FROM daily) "
            f"ORDER BY hash(ts_code) LIMIT {int(sample)}"
        ).fetchall()
    ]
    if not codes:
        raise CheckError("daily 无股票可抽样")
    checked = 0
    mismatches: list[dict[str, Any]] = []
    max_diff = 0.0
    errors: list[str] = []
    for code in codes:
        symbol = common.ts_code_to_sina(code)
        try:
            sina = to_canonical_daily(fetch_sina_history(symbol), code)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{code}: {exc}")
            continue
        local = con.execute(
            "SELECT trade_date, close FROM daily WHERE ts_code = ? ORDER BY trade_date",
            [code],
        ).fetchdf()
        if local.empty or sina.empty:
            errors.append(f"{code}: 本地或新浪为空")
            continue
        local["trade_date"] = pd.to_datetime(local["trade_date"]).dt.date
        merged = local.merge(
            sina[["trade_date", "close"]].rename(columns={"close": "sina_close"}),
            on="trade_date",
            how="inner",
        )
        if merged.empty:
            errors.append(f"{code}: 无重叠交易日")
            continue
        diff = (merged["close"] - merged["sina_close"]).abs()
        checked += len(merged)
        max_diff = max(max_diff, float(diff.max()))
        bad = merged[diff > tolerance]
        if not bad.empty:
            mismatches.append(
                {
                    "code": code,
                    "rows": int(len(bad)),
                    "max_diff": float(diff.max()),
                    "examples": [
                        {
                            "trade_date": str(row.trade_date),
                            "local": float(row.close),
                            "sina": float(row.sina_close),
                        }
                        for row in bad.head(3).itertuples()
                    ],
                }
            )
    ok = not mismatches and not errors and checked > 0
    return ok, {
        "akshare_python": AKSHARE_PYTHON,
        "codes": codes,
        "checked_rows": checked,
        "max_abs_diff": max_diff,
        "tolerance": tolerance,
        "mismatches": mismatches,
        "errors": errors[:20],
    }


def check_rowcount(con) -> tuple[bool, dict[str, Any]]:
    """Every open day in range must have at least one daily row."""
    rows = con.execute(
        """
        SELECT trade_date, count(*) AS rows, count(DISTINCT ts_code) AS codes
        FROM daily GROUP BY trade_date ORDER BY trade_date
        """
    ).fetchall()
    empty = [str(row[0]) for row in rows if int(row[1]) <= 0]
    zero_code = [str(row[0]) for row in rows if int(row[2]) <= 0]
    actual = {str(row[0]) for row in rows}
    if actual:
        lo, hi = min(actual), max(actual)
        expected = {
            str(row[0])
            for row in con.execute(
                "SELECT cal_date FROM trade_cal WHERE is_open = 1"
            ).fetchall()
            if lo <= str(row[0]) <= hi
        }
    else:
        expected = set()
    missing = sorted(expected - actual)
    total_rows = sum(int(row[1]) for row in rows)
    ok = bool(rows) and not empty and not zero_code and not missing
    return ok, {
        "days": len(rows),
        "total_rows": total_rows,
        "empty_days": empty[:20],
        "zero_code_days": zero_code[:20],
        "missing_open_days": missing[:20],
        "missing_open_count": len(missing),
    }
