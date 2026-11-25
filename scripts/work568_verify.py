#!/usr/bin/env python3
"""WORK-568 抽样回对：库表 vs 原始 parquet/csv，逐行逐列比对。

对三个源做**全量**比对（不是抽几行）：
  * gfex_lithium_carbonate（parquet，12,960 行）
  * sne_public_battery（csv，8 行）
  * comtrade_trade_values（csv，5 行）

两侧都投影成 VARCHAR 再 EXCEPT：日期统一 ISO，数值保留同一文本表示。
任一方向有差集即 FAIL。
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

HOME = Path("/srv/share/quantdb")
DB = HOME / "db" / "quantdb.duckdb"


def _ymd(expr: str) -> str:
    return (
        "strftime(coalesce(try_cast({e} AS DATE), try_strptime({e}, '%Y%m%d'), "
        "try_strptime({e}, '%Y.%m.%d')), '%Y-%m-%d')"
    ).format(e=expr)


def compare(con, table: str, source_sql: str, pairs: list[tuple[str, str]]) -> int:
    raw_cols = ", ".join(f'{expr} AS "{name}"' for expr, name in pairs)
    db_cols = ", ".join(f'cast("{name}" AS VARCHAR) AS "{name}"' for _, name in pairs)
    sql = f"""
    WITH raw AS (SELECT {raw_cols} FROM {source_sql}),
         db  AS (SELECT {db_cols} FROM {table})
    SELECT (SELECT count(*) FROM raw),
           (SELECT count(*) FROM db),
           (SELECT count(*) FROM (SELECT * FROM raw EXCEPT SELECT * FROM db)),
           (SELECT count(*) FROM (SELECT * FROM db EXCEPT SELECT * FROM raw))
    """
    raw_rows, db_rows, only_raw, only_db = con.execute(sql).fetchone()
    print(f"{table}: raw={raw_rows} db={db_rows} raw-db={only_raw} db-raw={only_db}")
    return only_raw + only_db


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    problems = 0

    gfex_parquet = str(HOME / "raw/industry/gfex_lithium_carbonate/part.parquet")
    problems += compare(
        con, "industry_gfex_lithium_carbonate",
        f"read_parquet('{gfex_parquet}')",
        [
            (_ymd("trade_date"), "trade_date"),
            ("cast(contract AS VARCHAR)", "contract"),
            ("cast(variety AS VARCHAR)", "variety"),
            ("cast(field AS VARCHAR)", "field"),
            ("cast(indicator AS VARCHAR)", "indicator"),
            ("cast(value AS VARCHAR)", "value"),
            (_ymd("published_date"), "published_date"),
            ("cast(source_url AS VARCHAR)", "source_url"),
        ],
    )

    sne_csv = str(HOME / "raw/industry_alt/sne_public_battery/data.csv")
    problems += compare(
        con, "industry_sne_public_battery",
        f"read_csv('{sne_csv}', header=true, all_varchar=true)",
        [
            ("cast(release_id AS VARCHAR)", "release_id"),
            ("cast(title AS VARCHAR)", "title"),
            (_ymd("publish_date"), "publish_date"),
            ("cast(key_figure AS VARCHAR)", "key_figure"),
            ("cast(url AS VARCHAR)", "url"),
            ("cast(status AS VARCHAR)", "status"),
        ],
    )

    comtrade_csv = str(HOME / "raw/industry_alt/comtrade_trade_values/data.csv")
    problems += compare(
        con, "industry_comtrade_trade_values",
        f"read_csv('{comtrade_csv}', header=true, all_varchar=true)",
        [
            ("cast(hs_code AS VARCHAR)", "hs_code"),
            ("cast(label AS VARCHAR)", "label"),
            ("cast(reporter AS VARCHAR)", "reporter"),
            ("cast(period AS VARCHAR)", "period_year"),
            ("cast(flow AS VARCHAR)", "flow"),
            ("cast(value_usd AS VARCHAR)", "value_usd"),
            ("cast(netweight_kg AS VARCHAR)", "netweight_kg"),
            ("cast(status AS VARCHAR)", "status"),
        ],
    )

    row = con.execute(
        "SELECT value, unit FROM industry_gfex_lithium_carbonate "
        "WHERE trade_date = DATE '2026-05-28' AND contract='2606' AND field='turnover'"
    ).fetchone()
    print(f"gfex turnover(20260528,2606): db={row[0]} unit={row[1]}")
    con.close()
    print("RESULT", "PASS" if problems == 0 else f"FAIL({problems})")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
