"""行业层质量闸（WORK-568）。

    python -m db.checks.industry all
    python -m db.checks.industry C1_rows_nonzero
    python -m db.checks.industry all --out /srv/share/quantdb/meta/checks/industry.json

九道闸，任一 FAIL 退出 1：

  C1_rows_nonzero        17 张 industry_* 表都存在且行数 > 0
  C2_primary_key         主键列无 NULL、主键唯一；哈希主键的源另报业务键重复数
  C3_date_domain         所有日期列落在 2000-01-01 .. 今天+1；月份列落在 2000-01 .. 当月
  C4_unit_reconciliation 单位勾稽：
                           gfex 成交额(万元) 反推 VWAP 落在当日 low..high
                           nea  当月全社会用电量 ≈ 四个分产业当月值之和（同文章同期）
                           unit 词表：每张带 unit 列的表只允许 schema 注释里写明的单位
                          （100ppi 每家公司只报一种单位，跨单位比值在当前数据上不成立，
                            故不做该比值闸；见 docs/industry-ingest-2026.md §4.7）
  C5_audit_consistency   最新完整装载审计的 raw/staged/loaded 与现状一致（防手改、防漏装）
  C6_catalog_coverage    v_industry_catalog_coverage 已覆盖数 >= 30，并给出完整/部分/文本拆分
  C7_freshness           日频源最新日期在 7 天内，月频源在 45 天内
  C8_forecast            预测表 industry_forecast（WORK-584）：
                           主键唯一、必填列非空、period_target 晚于今天、
                           period_target_type 与日期编码/原文表述一致、单位在词表内、
                           context 含「预计/有望」类语气词且能在正文快照里逐字定位
  C9_load_diff           装载留痕完整性（WORK-585）：最新一次覆盖 17 张表的运行，每张表都要
                          留过痕（差异行或 no_change 汇总行）；diff 行须能对上 audit、
                          change_type 合法、reason 非空、各类型的字段组合正确（不判断变化是否符合预期）

C1..C7 是观测表的既有 7 道闸，判据未改；C8 只查预测表，C9 只查留痕完整性。
只读打开 DuckDB；本模块不写主库，只写 meta/checks 下的 JSON 报告。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from ..etl import common
from ..etl.industry import BUSINESS_KEYS, FORECAST_SOURCES, SOURCES


def report_dir() -> Path:
    return common.META_DIR / "checks"


TABLES = [s.table for s in SOURCES]

# 每张带单位列的表允许的单位词表（与 schema 列注释一致）。上游悄悄改单位时
# C4 会失败。有 unit 列的表共 11 张，全部在此；没有单位列的 6 张
# （cninfo/comtrade/exchange/owid/sne/benchmark）不在此列。
# 值 = (单位列, 数值列, 允许单位集合)：单位为空且数值也为空的行（100ppi 的
# 分类标题行）不算错，单位为空但有数值的行算错。
UNIT_VOCABULARY: dict[str, tuple[str, str, set[str]]] = {
    "industry_gfex_lithium_carbonate": ("unit", "value", {"元/吨", "手", "万元"}),
    "industry_chinamoney_lpr": ("unit", "value", {"%"}),
    # CNESA 词表扩到 WORK-577 实测单位（该改动此前只在 live 树、未入 git，本单随
    # 本单分支同步，保证分支上的闸可独立复跑）
    "industry_cnesa_storage": ("unit", "value",
                               {"GW", "%", "GWh", "个", "项", "元/kWh", "小时"}),
    "industry_caam_auto_output": ("unit", "value", {"万辆"}),
    "industry_cpcaauto_nev": ("unit", "value", {"万辆", "%", "条"}),
    "industry_mof_fiscal": ("unit", "value", {"亿元"}),
    # nea 的单位来自两种正文形态：散文摘要用 亿千瓦/亿千瓦时/小时；2021–2023 的
    # 电力统计稿正文是整张 HTML 表（WORK-581），表内按上游原样出现 万千瓦（装机）、
    # 亿元（电源/电网投资）、%（厂用电率）、克/千瓦时（供电煤耗）、万百万千焦（供热量）、
    # 万吨（供热耗煤）、万千伏安/千米（新增变电设备与线路）。词表按实测全集固定，
    # 上游再出现新单位仍判 FAIL。
    "industry_nea_power": ("unit", "value",
                           {"亿千瓦", "亿千瓦时", "小时", "万千瓦", "亿元", "%",
                            "克/千瓦时", "万百万千焦", "万吨", "万千伏安", "千米"}),
    "industry_pbc_shrzgm": ("unit", "value", {"亿元"}),
    "industry_ppi_spot_prices": ("price_unit", "price", {"吨", "公斤", "千克"}),
    "industry_stats_gov_release": ("unit", "value", {"%", "亿元/%"}),
    "industry_trendforce_spot_price": ("unit", "value", {"USD"}),
}


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

# BUSINESS_KEYS 定义在 etl/industry.py（差异日志与 C2 的重复检查共用一份）。

# 预测表（WORK-584）：单位词表按首批数据实测固定；上游出现新单位时 C8 直接判 FAIL，
# 迫使实现方在 schema 注释与文档里明确新单位，而不是悄悄混进来。
FORECAST_TABLE = FORECAST_SOURCES[0].table
FORECAST_UNITS = frozenset({"GW", "亿千瓦", "小时"})
FORECAST_TARGET_TYPES = frozenset({"calendar_year_end", "five_year_plan_end"})
# 预测行必须带着原文语气词，防止把「预计/有望」洗成确定值。
FORECAST_MODALITY = re.compile(r"预计|有望|展望|预测")


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

    # nea: 同文章同期「当月」全社会用电量 ≈ 四个分产业当月值之和（容差 1%）。
    # 抓取层对每个指标同时落了当月与累计，同一 (文章, 期, 指标) 有两行；
    # 分产业与全社会用电量都按 min() 取当月（流量累计值 ≥ 当月值），两侧同口径。
    # 全社会用电量先按 (文章, 期) 收敛成一行再参与连接：2021–2023 的用电量稿
    # 同时给当月和累计，若直接 JOIN 会让分产业行数翻倍、n_parts≠4 而被静默排除。
    nea_rows = con.execute(
        """
        WITH monthly AS (
            SELECT source_url, period, indicator, min(value) AS monthly
            FROM industry_nea_power
            WHERE indicator IN ('第一产业用电量','第二产业用电量',
                                '第三产业用电量','城乡居民生活用电量')
            GROUP BY 1, 2, 3
            HAVING count(*) = 2
        ), total AS (
            SELECT source_url, period, min(value) AS monthly_total
            FROM industry_nea_power
            WHERE indicator = '全社会用电量'
            GROUP BY 1, 2
        ), a AS (
            SELECT m.source_url, m.period,
                   max(t.monthly_total) AS total,
                   sum(m.monthly) AS parts,
                   count(*) AS n_parts
            FROM monthly m
            JOIN total t
              ON t.source_url = m.source_url
             AND t.period IS NOT DISTINCT FROM m.period
            GROUP BY 1, 2
        )
        SELECT count(*) FILTER (WHERE n_parts = 4),
               count(*) FILTER (
                   WHERE n_parts = 4 AND abs(parts - total) <= abs(total) * 0.01
               )
        FROM a
        """
    ).fetchone()
    nea_pairs, nea_ok = int(nea_rows[0]), int(nea_rows[1])
    detail["nea_electricity_sum"] = {
        "pairs": nea_pairs,
        "ok": nea_ok,
        "note": "同文章同期需要「全社会用电量 + 四个分产业当月值」才判定；pairs=0 时按 FAIL 处理，"
                "不允许空转算 PASS",
        "pass": nea_pairs > 0 and nea_ok == nea_pairs,
    }
    ok = ok and detail["nea_electricity_sum"]["pass"]

    # 单位词表：每张带单位列的表只允许文档里写明的单位，防止上游悄悄改单位。
    # （100ppi 每家公司只报一种单位，跨单位勾稽在数据上不可能成立，故不做比值闸。）
    vocab_failures = 0
    vocab_detail: dict[str, Any] = {}
    for table, (unit_col, value_col, allowed) in UNIT_VOCABULARY.items():
        unexpected, missing, null_units = con.execute(
            f"""
            SELECT count(*) FILTER (
                       WHERE {unit_col} IS NOT NULL
                         AND {unit_col} NOT IN ({', '.join('?' for _ in allowed)})
                   ),
                   count(*) FILTER (WHERE {unit_col} IS NULL AND {value_col} IS NOT NULL),
                   count(*) FILTER (WHERE {unit_col} IS NULL)
            FROM {table}
            """,
            sorted(allowed),
        ).fetchone()
        entry = {
            "unit_column": unit_col,
            "value_column": value_col,
            "allowed": sorted(allowed),
            "unexpected": int(unexpected),
            "missing_unit_with_value": int(missing),
            "null_units": int(null_units),
            "pass": int(unexpected) == 0 and int(missing) == 0,
        }
        vocab_detail[table] = entry
        if not entry["pass"]:
            vocab_failures += 1
    detail["unit_vocabulary"] = {
        "pairs": len(UNIT_VOCABULARY),
        "ok": len(UNIT_VOCABULARY) - vocab_failures,
        "tables": vocab_detail,
        "pass": vocab_failures == 0,
    }
    ok = ok and detail["unit_vocabulary"]["pass"]

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
    # 映射到文本字段（公告标题/新闻稿标题）的条目，口径是「有可查记录」而非「可查到数值」，
    # 单列出来，避免把文本索引算成数值覆盖。
    text_only = int(
        con.execute(
            """
            SELECT count(DISTINCT m.catalog_industry || '|' || m.catalog_indicator)
            FROM industry_catalog_map m
            WHERE m.field_name IN ('title', 'key_figure')
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
        "text_only": text_only,
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


def c8_forecast(con) -> tuple[bool, dict[str, Any]]:
    """预测表独立闸：结构、日期编码、语气词与原文可回指。

    设计取舍：
    * 不复用 C1..C7：那 7 道闸的判据（尤其 C3 日期域 ≤ 今天+1）是为观测表定的，
      预测的目标期在未来，混进去必然误报；新增一条独立闸，既有判据一字不改。
    * context 要能在正文快照里逐字定位：快照由抓取脚本写
      ``raw/industry/<dataset>/article.txt``（与提取时用的正文同一份文本），
      这样「可回指」不依赖再联网，也能在离线复跑时判定。
    """
    existing = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE'"
        ).fetchall()
    }
    if FORECAST_TABLE not in existing:
        return False, {
            "pass": False,
            "error": f"{FORECAST_TABLE} 不存在：先跑 "
                     f"python -m db.etl.industry load --sources cnesa_forecast",
        }

    today = _today()
    units = sorted(FORECAST_UNITS)
    unit_ph = ", ".join("?" for _ in units)
    total, bad_units, blank_required, not_future = con.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE unit NOT IN ({unit_ph})),
               count(*) FILTER (
                   WHERE coalesce(trim(source_org), '') = ''
                      OR coalesce(trim(source_doc), '') = ''
                      OR coalesce(trim(context), '') = ''
                      OR coalesce(trim(rule_version), '') = ''
                      OR coalesce(trim(period_target_raw), '') = ''
               ),
               count(*) FILTER (WHERE period_target <= ?)
        FROM {FORECAST_TABLE}
        """,
        [*units, today],
    ).fetchone()

    key_dups = int(
        con.execute(
            f"""SELECT count(*) FROM (
                    SELECT indicator, period_target, source_org
                    FROM {FORECAST_TABLE}
                    GROUP BY 1, 2, 3 HAVING count(*) > 1
                )"""
        ).fetchone()[0]
    )
    types = sorted(FORECAST_TARGET_TYPES)
    type_ph = ", ".join("?" for _ in types)
    bad_type, bad_encoding = con.execute(
        f"""
        SELECT count(*) FILTER (
                   WHERE period_target_type IS NULL
                      OR period_target_type NOT IN ({type_ph})
               ),
               count(*) FILTER (
                   WHERE period_target_type IN ({type_ph})
                     AND (month(period_target) <> 12 OR day(period_target) <> 31)
               )
        FROM {FORECAST_TABLE}
        """,
        [*types, *types],
    ).fetchone()

    rows = con.execute(
        f"SELECT dataset, indicator, period_target, period_target_type, "
        f"period_target_raw, context FROM {FORECAST_TABLE} "
        f"ORDER BY dataset, period_target, indicator"
    ).fetchall()
    no_modality = [
        f"{i}@{p}" for _ds, i, p, _t, _raw, context in rows
        if not FORECAST_MODALITY.search(context or "")
    ]
    # 两种类型都落在 12-31，单看日期区分不了；再用原文表述交叉一次：
    # 五年规划写作「十二五」「十五五」等（十…五），日历年写作「2030年」。
    type_raw_mismatch: list[str] = []
    for _ds, i, p, t, raw, _c in rows:
        label = (raw or "").strip()
        if t == "calendar_year_end" and not re.fullmatch(r"(?:19|20)\d{2}年", label):
            type_raw_mismatch.append(f"{i}@{p}: {t} 与原文「{label}」不符")
        elif t == "five_year_plan_end" and not re.fullmatch(r"十[一二三四五六七八九]?五", label):
            type_raw_mismatch.append(f"{i}@{p}: {t} 与原文「{label}」不符")

    snapshots: dict[str, Any] = {}
    snapshot_failures = 0
    for dataset in sorted({r[0] for r in rows}):
        path = common.RAW_DIR / "industry" / dataset / "article.txt"
        entry: dict[str, Any] = {"path": str(path)}
        if not path.exists():
            entry.update({"pass": False, "error": "正文快照不存在，无法离线验证 context 可回指"})
            snapshot_failures += 1
            snapshots[dataset] = entry
            continue
        text = path.read_text(encoding="utf-8")
        in_dataset = [(i, p, c) for ds, i, p, _t, _raw, c in rows if ds == dataset]
        missing = [f"{i}@{p}" for i, p, c in in_dataset if c not in text]
        entry.update({
            "rows": len(in_dataset),
            "located": len(in_dataset) - len(missing),
            "missing": missing,
            "pass": not missing,
        })
        if missing:
            snapshot_failures += 1
        snapshots[dataset] = entry

    ok = (
        int(total) > 0
        and int(bad_units) == 0
        and int(blank_required) == 0
        and int(not_future) == 0
        and key_dups == 0
        and int(bad_type) == 0
        and int(bad_encoding) == 0
        and not no_modality
        and not type_raw_mismatch
        and snapshot_failures == 0
    )
    return ok, {
        "pass": ok,
        "table": FORECAST_TABLE,
        "rows": int(total),
        "today": str(today),
        "unit_vocabulary": {
            "allowed": units,
            "unexpected": int(bad_units),
            "pass": int(bad_units) == 0,
        },
        "required_blank": int(blank_required),
        "duplicate_business_key": key_dups,
        "target_not_future": int(not_future),
        "target_type": {
            "allowed": types,
            "unexpected": int(bad_type),
            "bad_date_encoding": int(bad_encoding),
            "rule": "日历年末 / 五年规划期末统一编码为 12-31；原文另存 period_target_raw",
            "pass": int(bad_type) == 0 and int(bad_encoding) == 0,
        },
        "missing_modality": no_modality,
        "target_type_raw_mismatch": type_raw_mismatch,
        "snapshots": snapshots,
    }


DIFF_CHANGE_TYPES = ("value_changed", "row_added", "row_dropped", "no_change")


def c9_load_diff(con) -> tuple[bool, dict[str, Any]]:
    """留痕完整性（WORK-585）：最近一次覆盖 17 表的装载，每张表都要留过痕。

    判据与 C5 同一取数口径（覆盖全部表的最新一次运行）。这条闸只回答「替换有没有
    记账」，不判断变化是否符合业务预期——后者是人看 v_industry_load_changes_recent
    的事，闸不替人做价值判断。
    """
    row = con.execute(
        "SELECT run_id, max(finished_at) FROM industry_load_audit "
        "GROUP BY 1 HAVING count(*) = ? ORDER BY 2 DESC LIMIT 1",
        [len(TABLES)],
    ).fetchone()
    if row is None:
        return False, {
            "pass": False,
            "error": f"industry_load_audit 中没有覆盖 {len(TABLES)} 张表的完整运行",
        }
    latest = row[0]

    present = {
        r[0]: int(r[1])
        for r in con.execute(
            "SELECT table_name, count(*) FROM industry_load_diff "
            "WHERE run_id = ? GROUP BY 1",
            [latest],
        ).fetchall()
    }
    missing = sorted(set(TABLES) - set(present))

    placeholders = ", ".join("?" for _ in DIFF_CHANGE_TYPES)
    bad_type, orphan, shape = con.execute(
        f"""
        SELECT
          (SELECT count(*) FROM industry_load_diff
            WHERE change_type NOT IN ({placeholders})),
          (SELECT count(*) FROM industry_load_diff d
            WHERE NOT EXISTS (SELECT 1 FROM industry_load_audit a
                              WHERE a.run_id = d.run_id
                                AND a.table_name = d.table_name)),
          (SELECT count(*) FROM industry_load_diff
            WHERE reason IS NULL
               OR (change_type = 'value_changed'
                   AND (column_name IS NULL OR old_value IS NULL OR new_value IS NULL))
               OR (change_type = 'row_added' AND new_value IS NULL)
               OR (change_type = 'row_dropped' AND old_value IS NULL)
               OR (change_type = 'no_change'
                   AND (column_name IS NOT NULL OR old_value IS NOT NULL)))
        """,
        list(DIFF_CHANGE_TYPES),
    ).fetchone()

    ok = (
        not missing
        and int(bad_type) == 0
        and int(orphan) == 0
        and int(shape) == 0
    )
    return ok, {
        "pass": ok,
        "latest_run": latest,
        "tables_with_diff": len(present),
        "missing_diff_tables": missing,
        "diff_rows": present,
        "illegal_change_type": int(bad_type),
        "orphan_diff_rows": int(orphan),
        "malformed_diff_rows": int(shape),
    }


GATES = {
    "C1_rows_nonzero": c1_rows_nonzero,
    "C2_primary_key": c2_primary_key,
    "C3_date_domain": c3_date_domain,
    "C4_unit_reconciliation": c4_unit_reconciliation,
    "C5_audit_consistency": c5_audit_consistency,
    "C6_catalog_coverage": c6_catalog_coverage,
    "C7_freshness": c7_freshness,
    "C8_forecast": c8_forecast,
    "C9_load_diff": c9_load_diff,
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
                "text_only": detail.get("text_only"),
                "catalog_rows": detail.get("catalog_rows"),
            })
        elif name == "C7_freshness":
            row["stale"] = [
                k for k, v in detail.get("checks", {}).items() if not v.get("pass")
            ]
        elif name == "C8_forecast":
            row.update({
                "rows": detail.get("rows"),
                "duplicate_business_key": detail.get("duplicate_business_key"),
                "target_not_future": detail.get("target_not_future"),
                "missing_modality": detail.get("missing_modality"),
                "snapshot_failures": [
                    ds for ds, v in (detail.get("snapshots") or {}).items()
                    if not v.get("pass")
                ],
            })
        elif name == "C9_load_diff":
            row.update({
                "latest_run": detail.get("latest_run"),
                "tables_with_diff": detail.get("tables_with_diff"),
                "missing_diff_tables": detail.get("missing_diff_tables"),
                "illegal_change_type": detail.get("illegal_change_type"),
                "orphan_diff_rows": detail.get("orphan_diff_rows"),
                "malformed_diff_rows": detail.get("malformed_diff_rows"),
            })
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
    # 单闸模式写独立文件名，不能覆盖当日 all 的 7 闸报告（复审判定 suggestion 3）。
    path = (
        Path(args.out)
        if args.out
        else report_dir() / f"industry-{dt.date.today():%Y%m%d}-{args.check}.json"
    )
    common.write_json_atomic(path, {"generated_at": common.utc_now(), "ok": ok,
                                    "gates": {args.check: detail}})
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
    print(f"report: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
