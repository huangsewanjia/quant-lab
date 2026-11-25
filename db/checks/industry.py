"""行业层质量闸（WORK-568）。

    python -m db.checks.industry all
    python -m db.checks.industry C1_rows_nonzero
    python -m db.checks.industry all --out /srv/share/quantdb/meta/checks/industry.json

六道闸，任一 FAIL 退出 1：

  C1_rows_nonzero        17 张 industry_* 表都存在且行数 > 0
  C2_primary_key         主键列无 NULL、主键唯一；哈希主键的源另报业务键重复数
  C3_date_domain         所有日期列落在 2000-01-01 .. 今天+1；月份列落在 2000-01 .. 当月
  C4_unit_reconciliation 单位勾稽：
                           gfex 成交额(万元) 反推 VWAP 落在当日 low..high
                           nea  全社会用电量 ≈ 分产业用电量之和（同文章同期）
                           ppi  同货同供方同日 元/公斤 与 元/吨 比值 ≈ 1000
  C5_audit_consistency   最新装载审计的 raw/staged/loaded 与现状一致（防手改、防漏装）
  C6_catalog_coverage    v_industry_catalog_coverage 已覆盖数 >= 30，并给出完整/部分拆分
  C7_freshness           日频源最新日期在 7 天内，月频源在 45 天内

只读打开 DuckDB；本模块不写主库，只写 meta/checks 下的 JSON 报告。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..etl import common
from ..etl.industry import SOURCES

def report_dir() -> Path:
    return common.META_DIR / "checks"

TABLES = [s.table for s in SOURCES]

DATE_COLUMNS: dict[str, list[str]] = {
    "industry_gfex_lithium_carbonate": ["trade_date", "published_date"],
    "industry_chinamoney_lpr": ["quote_date"],
    "industry_cnesa_storage": ["period", "published_date"],
    "industry_cninfo_announcements": ["published_date"],
    "industry_caam_auto_output": ["period", "published_date"],
    "industry_cpcaauto_nev": ["period", "published_date"],
    "industry_mof_fiscal": ["period", "published_date"],
    "industry_nea_power": ["period", "published_date"],
    "industry_pbc_shrzgm": ["period"],
    "industry_stats_gov_release": ["period", "published_date"],
    "industry_benchmark_public_index": ["snapshot_date"],
    "industry_comtrade_trade_values": [],
    "industry_exchange_daily_quotes": ["trade_date"],
    "industry_ow_iea_usgs_timeseries": [],
    "industry_ppi_spot_prices": ["as_of"],
    "industry_sne_public_battery": ["publish_date"],
    "industry_trendforce_spot_price": ["snapshot_date"],
}

BUSINESS_KEYS: dict[str, list[str]] = {
    "industry_caam_auto_output": ["period", "indicator", "scope", "subject"],
    "industry_cpcaauto_nev": ["period", "kind", "indicator", "source_url"],
    "industry_mof_fiscal": ["period", "indicator"],
    "industry_nea_power": ["period", "indicator", "source_url"],
    "industry_ppi_spot_prices": ["goods_id", "spec", "market", "supplier", "as_of"],
    "industry_trendforce_spot_price": ["snapshot_date", "series", "spec"],
    "industry_benchmark_public_index": ["snapshot_date", "kind", "name"],
}


def _today() -> dt.date:
    return dt.date.today()


def c1_rows_nonzero(con) -> tuple[bool, dict[str, Any]]:
    existing = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE'"
        ).fetchall()
    }
    rows: dict[str, int] = {}
    missing: list[str] = []
    empty: list[str] = []
    for table in TABLES:
        if table not in existing:
            missing.append(table)
            rows[table] = 0
            continue
        count = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        rows[table] = count
        if count == 0:
            empty.append(table)
    ok = not missing and not empty
    return ok, {
        "pass": ok,
        "tables_checked": len(TABLES),
        "tables_present": len(TABLES) - len(missing),
        "missing": missing,
        "empty": empty,
        "rows": rows,
        "rows_total": sum(rows.values()),
    }


def _primary_key(con, table: str) -> list[str]:
    rows = con.execute(
        """
        SELECT k.column_name
        FROM duckdb_constraints() c,
             LATERAL unnest(c.constraint_column_names) AS k(column_name)
        WHERE c.table_name = ? AND c.constraint_type = 'PRIMARY KEY'
        ORDER BY k.column_name
        """,
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def c2_primary_key(con) -> tuple[bool, dict[str, Any]]:
    detail: dict[str, Any] = {}
    ok = True
    for table in TABLES:
        keys = _primary_key(con, table)
        if not keys:
            detail[table] = {"pass": False, "error": "无主键"}
            ok = False
            continue
        cols = ", ".join(keys)
        total, distinct, nulls = con.execute(
            f"SELECT count(*), count(DISTINCT ({cols})), "
            f"count(*) FILTER (WHERE {' OR '.join(f'{k} IS NULL' for k in keys)}) "
            f"FROM {table}"
        ).fetchone()
        dup_on_keys = int(total) - int(distinct)
        entry: dict[str, Any] = {
            "pass": dup_on_keys == 0 and int(nulls) == 0,
            "pk": keys,
            "rows": int(total),
            "null_in_pk": int(nulls),
            "duplicate_pk_rows": dup_on_keys,
        }
        if table in BUSINESS_KEYS:
            bkeys = BUSINESS_KEYS[table]
            cond = " || '|' || ".join(
                f"coalesce(cast({k} AS VARCHAR), '∅')" for k in bkeys
            )
            btotal, bdistinct = con.execute(
                f"SELECT count(*), count(DISTINCT ({cond})) FROM {table}"
            ).fetchone()
            entry["business_key"] = bkeys
            entry["business_key_duplicate_rows"] = int(btotal) - int(bdistinct)
        detail[table] = entry
        ok = ok and entry["pass"]
    return ok, {"pass": ok, "tables": detail}


def c3_date_domain(con) -> tuple[bool, dict[str, Any]]:
    low = dt.date(2000, 1, 1)
    high = _today() + dt.timedelta(days=1)
    detail: dict[str, Any] = {}
    ok = True
    for table, columns in DATE_COLUMNS.items():
        for column in columns:
            out_of_range, nulls, total = con.execute(
                f"SELECT count(*) FILTER (WHERE {column} < ? OR {column} > ?), "
                f"count(*) FILTER (WHERE {column} IS NULL), count(*) FROM {table}",
                [low, high],
            ).fetchone()
            key = f"{table}.{column}"
            detail[key] = {
                "rows": int(total),
                "null": int(nulls),
                "out_of_range": int(out_of_range),
            }
            if int(out_of_range):
                ok = False
    return ok, {"pass": ok, "domain": [str(low), str(high)], "columns": detail}


def c4_unit_reconciliation(con) -> tuple[bool, dict[str, Any]]:
    detail: dict[str, Any] = {}
    ok = True

    # gfex: 成交额（万元）反推 VWAP，必须落在当日 low..high（容差 0.5%）
    gfex = con.execute(
        """
        WITH d AS (
            SELECT trade_date, contract,
                   max(value) FILTER (WHERE field = 'turnover') AS turnover_wan,
                   max(value) FILTER (WHERE field = 'volumn')   AS volume,
                   max(value) FILTER (WHERE field = 'low')      AS low,
                   max(value) FILTER (WHERE field = 'high')     AS high
            FROM industry_gfex_lithium_carbonate
            GROUP BY 1, 2
        )
        SELECT count(*) FILTER (WHERE volume > 0),
               count(*) FILTER (
                   WHERE volume > 0
                     AND turnover_wan * 10000 / volume BETWEEN low * 0.995 AND high * 1.005
               )
        FROM d
        """
    ).fetchone()
    gfex_n, gfex_ok = int(gfex[0]), int(gfex[1])
    gfex_rate = gfex_ok / gfex_n if gfex_n else 0.0
    detail["gfex_vwap_in_range"] = {
        "pairs": gfex_n, "ok": gfex_ok,
        "rate": round(gfex_rate, 6), "threshold": 0.99,
        "pass": gfex_n > 0 and gfex_rate >= 0.99,
    }
    ok = ok and detail["gfex_vwap_in_range"]["pass"]

    # nea: 同文章同期 全社会用电量 ≈ 分产业之和（容差 1%）
    nea_rows = con.execute(
        """
        WITH a AS (
            SELECT source_url, period,
                   max(value) FILTER (WHERE indicator = '全社会用电量') AS total,
                   sum(value) FILTER (
                       WHERE indicator IN ('第一产业用电量','第二产业用电量',
                                           '第三产业用电量','城乡居民生活用电量')
                   ) AS parts,
                   count(*) FILTER (
                       WHERE indicator IN ('第一产业用电量','第二产业用电量',
                                           '第三产业用电量','城乡居民生活用电量')
                   ) AS n_parts
            FROM industry_nea_power
            GROUP BY 1, 2
        )
        SELECT count(*) FILTER (WHERE total IS NOT NULL AND n_parts = 4),
               count(*) FILTER (
                   WHERE total IS NOT NULL AND n_parts = 4
                     AND abs(parts - total) <= abs(total) * 0.01
               )
        FROM a
        """
    ).fetchone()
    nea_pairs, nea_ok = int(nea_rows[0]), int(nea_rows[1])
    detail["nea_electricity_sum"] = {
        "pairs": nea_pairs,
        "ok": nea_ok,
        "note": "无「全社会用电量 + 四分项」同文章组合时不判定（pairs=0 视为 skipped）",
        "pass": nea_pairs == 0 or nea_ok == nea_pairs,
    }
    ok = ok and detail["nea_electricity_sum"]["pass"]

    # ppi: 同货同供方同日 元/公斤 与 元/吨 的比值 ≈ 1000（容差 2%）
    ppi_rows = con.execute(
        """
        WITH p AS (
            SELECT goods_id, supplier, as_of,
                   max(price) FILTER (WHERE price_unit = '元/吨') AS per_t,
                   max(price) FILTER (WHERE price_unit = '元/公斤') AS per_kg
            FROM industry_ppi_spot_prices
            GROUP BY 1, 2, 3
        )
        SELECT count(*) FILTER (WHERE per_t IS NOT NULL AND per_kg IS NOT NULL),
               count(*) FILTER (
                   WHERE per_t IS NOT NULL AND per_kg IS NOT NULL
                     AND per_t / per_kg BETWEEN 980 AND 1020
               )
        FROM p
        """
    ).fetchone()
    ppi_pairs, ppi_ok = int(ppi_rows[0]), int(ppi_rows[1])
    detail["ppi_kg_vs_tonne"] = {
        "pairs": ppi_pairs,
        "ok": ppi_ok,
        "note": "源里同货同供方同日同时出现两种单位才可勾稽；pairs=0 视为 skipped",
        "pass": ppi_pairs == 0 or ppi_ok == ppi_pairs,
    }
    ok = ok and detail["ppi_kg_vs_tonne"]["pass"]

    return ok, {"pass": ok, "gates": detail}


def c5_audit_consistency(con) -> tuple[bool, dict[str, Any]]:
    # 取「覆盖全部 17 张表」的最新一次运行，而不是 run_id 的字典序最大值：
    # 单源排查（--sources）与测试运行也会写审计，字典序可能排在正式运行之后。
    row = con.execute(
        "SELECT run_id, max(finished_at) FROM industry_load_audit "
        "GROUP BY 1 HAVING count(*) = ? ORDER BY 2 DESC LIMIT 1",
        [len(TABLES)],
    ).fetchone()
    if row is None:
        return False, {"pass": False, "error": f"industry_load_audit 中没有覆盖 {len(TABLES)} 张表的完整运行"}
    latest = row[0]
    rows = con.execute(
        "SELECT table_name, raw_rows, staged_rows, loaded_rows, partitions, finished_at "
        "FROM industry_load_audit WHERE run_id = ?",
        [latest],
    ).fetchall()
    current = {
        r[0]: int(r[1])
        for r in con.execute(
            "SELECT table_name, sum(n) FROM ("
            + " UNION ALL ".join(f"SELECT '{t}' AS table_name, count(*) AS n FROM {t}" for t in TABLES)
            + ") GROUP BY 1"
        ).fetchall()
    }
    audit: dict[str, Any] = {}
    ok = True
    for table, raw_rows, staged_rows, loaded_rows, partitions, finished_at in rows:
        now_count = current.get(table, 0)
        consistent = now_count == int(loaded_rows) and int(staged_rows) <= int(raw_rows)
        audit[table] = {
            "run_id": latest,
            "raw_rows": int(raw_rows),
            "staged_rows": int(staged_rows),
            "loaded_rows": int(loaded_rows),
            "table_rows_now": now_count,
            "partitions": int(partitions),
            "finished_at": str(finished_at),
            "pass": consistent,
        }
        ok = ok and consistent
    missing = sorted(set(TABLES) - {r[0] for r in rows})
    if missing:
        ok = False
    return ok, {"pass": ok, "latest_run": latest, "missing_audit": missing, "tables": audit}


def c6_catalog_coverage(con, minimum: int = 30) -> tuple[bool, dict[str, Any]]:
    total, covered, complete, partial = con.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE coverage = '已覆盖'),
               count(*) FILTER (WHERE completeness = '完整'),
               count(*) FILTER (WHERE completeness = '部分')
        FROM v_industry_catalog_coverage
        """
    ).fetchone()
    by_industry = con.execute(
        "SELECT * FROM v_industry_catalog_coverage_summary"
    ).df().to_dict("records")
    orphans = int(
        con.execute(
            """
            SELECT count(*) FROM industry_catalog_map m
            WHERE NOT EXISTS (
                SELECT 1 FROM data_catalog c
                WHERE c."行业" = m.catalog_industry
                  AND c."指标名" = m.catalog_indicator
            )
            """
        ).fetchone()[0]
    )
    ok = int(covered) >= minimum and orphans == 0
    return ok, {
        "pass": ok,
        "catalog_rows": int(total),
        "covered": int(covered),
        "complete": int(complete),
        "partial": int(partial),
        "threshold": minimum,
        "mapping_orphans": orphans,
        "by_industry": by_industry,
    }


def c7_freshness(con, daily_days: int = 7, monthly_days: int = 45) -> tuple[bool, dict[str, Any]]:
    today = _today()
    # (table, date column, tier, limit_days override)
    checks = [
        ("industry_gfex_lithium_carbonate", "trade_date", "daily", None),
        ("industry_exchange_daily_quotes", "trade_date", "daily", None),
        ("industry_ppi_spot_prices", "as_of", "daily", None),
        ("industry_trendforce_spot_price", "snapshot_date", "daily", None),
        ("industry_benchmark_public_index", "snapshot_date", "daily", None),
        ("industry_chinamoney_lpr", "quote_date", "monthly", None),
        # CNESA 是年度发布（次年 1 月），用月度阈值会永久误报
        ("industry_cnesa_storage", "published_date", "monthly", 400),
        ("industry_cninfo_announcements", "published_date", "monthly", None),
        ("industry_caam_auto_output", "published_date", "monthly", None),
        ("industry_cpcaauto_nev", "published_date", "monthly", None),
        ("industry_mof_fiscal", "published_date", "monthly", None),
        ("industry_nea_power", "published_date", "monthly", None),
        # 社融按统计期发布，当月数据次月中旬才有，75 天覆盖发布延迟
        ("industry_pbc_shrzgm", "period", "monthly", 75),
        ("industry_stats_gov_release", "published_date", "monthly", None),
        ("industry_sne_public_battery", "publish_date", "monthly", None),
    ]
    detail: dict[str, Any] = {}
    ok = True
    for table, column, tier, override in checks:
        latest = con.execute(f"SELECT max({column}) FROM {table}").fetchone()[0]
        limit = override if override is not None else (daily_days if tier == "daily" else monthly_days)
        age = (today - latest).days if latest is not None else None
        passed = age is not None and age <= limit
        detail[f"{table}.{column}"] = {
            "latest": str(latest), "age_days": age, "limit_days": limit, "pass": passed,
        }
        ok = ok and passed
    return ok, {"pass": ok, "today": str(today), "checks": detail}


GATES = {
    "C1_rows_nonzero": c1_rows_nonzero,
    "C2_primary_key": c2_primary_key,
    "C3_date_domain": c3_date_domain,
    "C4_unit_reconciliation": c4_unit_reconciliation,
    "C5_audit_consistency": c5_audit_consistency,
    "C6_catalog_coverage": c6_catalog_coverage,
    "C7_freshness": c7_freshness,
}


def run_all(out: Path | None = None) -> int:
    results: dict[str, Any] = {}
    with common.duckdb_connect(read_only=True) as con:
        for name, fn in GATES.items():
            try:
                ok, detail = fn(con)
            except Exception as exc:  # noqa: BLE001 - 闸失败必须落报告
                ok, detail = False, {"pass": False, "error": f"{type(exc).__name__}: {exc}"}
            results[name] = detail
            flag = "PASS" if ok else "FAIL"
            print(f"[{flag}] {name}")
    ok_all = all(bool(d.get("pass")) for d in results.values())
    payload = {
        "generated_at": common.utc_now(),
        "ok": ok_all,
        "gates": results,
    }
    path = out or report_dir() / f"industry-{dt.date.today():%Y%m%d}.json"
    common.write_json_atomic(path, payload)
    print(f"report: {path}")
    print(json.dumps(_summary(results), ensure_ascii=False, indent=2))
    return 0 if ok_all else 1


def _summary(results: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name, detail in results.items():
        row: dict[str, Any] = {"pass": detail.get("pass")}
        if name == "C1_rows_nonzero":
            row.update({
                "tables_present": detail.get("tables_present"),
                "rows_total": detail.get("rows_total"),
                "empty": detail.get("empty"),
                "missing": detail.get("missing"),
            })
        elif name == "C2_primary_key":
            bad = [t for t, v in detail.get("tables", {}).items() if not v.get("pass")]
            row["failing_tables"] = bad
        elif name == "C3_date_domain":
            bad = [c for c, v in detail.get("columns", {}).items() if v.get("out_of_range")]
            row["out_of_range"] = bad
        elif name == "C4_unit_reconciliation":
            row["gates"] = {
                k: {"pass": v["pass"], "pairs": v["pairs"], "ok": v["ok"]}
                for k, v in detail.get("gates", {}).items()
            }
        elif name == "C5_audit_consistency":
            row["latest_run"] = detail.get("latest_run")
            row["mismatched"] = [
                t for t, v in detail.get("tables", {}).items() if not v.get("pass")
            ]
        elif name == "C6_catalog_coverage":
            row.update({
                "covered": detail.get("covered"),
                "complete": detail.get("complete"),
                "partial": detail.get("partial"),
                "catalog_rows": detail.get("catalog_rows"),
            })
        elif name == "C7_freshness":
            row["stale"] = [
                k for k, v in detail.get("checks", {}).items() if not v.get("pass")
            ]
        summary[name] = row
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.checks.industry", description="行业层质量闸"
    )
    parser.add_argument("check", choices=list(GATES) + ["all"])
    parser.add_argument("--out", help="报告输出路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check == "all":
        return run_all(Path(args.out) if args.out else None)
    fn = GATES[args.check]
    with common.duckdb_connect(read_only=True) as con:
        try:
            ok, detail = fn(con)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, {"pass": False, "error": f"{type(exc).__name__}: {exc}"}
    path = Path(args.out) if args.out else report_dir() / f"industry-{dt.date.today():%Y%m%d}.json"
    common.write_json_atomic(path, {"generated_at": common.utc_now(), "ok": ok,
                                    "gates": {args.check: detail}})
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
    print(f"report: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
