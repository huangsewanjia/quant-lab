#!/usr/bin/env python3
"""WORK-572 回填校验脚本（只读主库 + 直读 raw 文件）。

    python scripts/work572_verify.py snapshot   # 装载前拍 A 股 20 表基线（写 meta/industry/work572-astock-baseline.json）
    python scripts/work572_verify.py astock     # 装载后比对 A 股 20 表是否零 diff
    python scripts/work572_verify.py coverage   # 回填前（快照 parquet）vs 回填后（主库）覆盖表
    python scripts/work572_verify.py spot        # 2 个新增期间点与 raw 全量双向 EXCEPT
    python scripts/work572_verify.py shape       # 表数 / data_catalog / industry_* 行数

设计：
  * 只读打开主库（read_only=True），不写库；
  * raw 比对用 DuckDB 直读 parquet/CSV（不经过 db.etl.industry 的 transform，避免自证）；
  * 回填前快照在 raw/_backup-work572/db-pre-work572/*.parquet。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

DB = "/srv/share/quantdb/db/quantdb.duckdb"
PRE = Path("/srv/share/quantdb/raw/_backup-work572/db-pre-work572")
RAW = Path("/srv/share/quantdb/raw")
META = Path("/srv/share/quantdb/meta/industry")
BASELINE = META / "work572-astock-baseline.json"

ASTOCK = ["daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d", "index_daily",
          "index_weight", "index_classify", "stock_basic", "trade_cal", "namechange",
          "income", "balancesheet", "cashflow", "fina_indicator", "dividend",
          "report_meta", "consensus_eps", "rating_change", "data_catalog"]

# 源 × 日期列（回填前后覆盖表用）
COVER = {
    "industry_gfex_lithium_carbonate": "trade_date",
    "industry_chinamoney_lpr": "quote_date",
    "industry_cnesa_storage": "period",
    "industry_cninfo_announcements": "published_date",
    "industry_caam_auto_output": "period",
    "industry_cpcaauto_nev": "period",
    "industry_mof_fiscal": "period",
    "industry_nea_power": "period",
    "industry_pbc_shrzgm": "period",
    "industry_stats_gov_release": "period",
    "industry_benchmark_public_index": "snapshot_date",
    "industry_comtrade_trade_values": "period_year",
    "industry_exchange_daily_quotes": "trade_date",
    "industry_ow_iea_usgs_timeseries": "year",
    "industry_ppi_spot_prices": "as_of",
    "industry_sne_public_battery": "publish_date",
    "industry_trendforce_spot_price": "snapshot_date",
}


def _now():
    import datetime as _dt
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def con(read_only=True):
    return duckdb.connect(DB, read_only=read_only)


def snapshot():
    META.mkdir(parents=True, exist_ok=True)
    c = con()
    out = {}
    for t in ASTOCK:
        rows = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        # 便宜的指纹：行数 + 所有 VARCHAR 列 md5 行数不现实，取每表 3 个聚合
        cols = [r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=? AND data_type IN ('DOUBLE','BIGINT','INTEGER','HUGEINT')",
            [t]).fetchall()]
        agg = {}
        for col in cols[:2]:
            agg[col] = c.execute(f'SELECT round(sum("{col}"), 6) FROM {t}').fetchone()[0]
        out[t] = {"rows": rows, "agg": {k: (float(v) if v is not None else None) for k, v in agg.items()}}
    BASELINE.write_text(json.dumps({"tables": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(v["rows"] for v in out.values())
    print(f"[snapshot] 20 张 A 股表基线已写入 {BASELINE}；行数合计 {total:,}")
    for t, v in out.items():
        print(f"  {t:16} {v['rows']:>12,}  {v['agg']}")


def astock():
    if not BASELINE.exists():
        print("[astock] 缺基线，先跑 snapshot"); return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["tables"]
    c = con()
    bad, diffs = 0, []
    for t in ASTOCK:
        rows = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        agg = {}
        for col in base[t]["agg"]:
            v = c.execute(f'SELECT round(sum("{col}"), 6) FROM {t}').fetchone()[0]
            agg[col] = float(v) if v is not None else None
        # 行数必须完全一致；浮点聚合和受 DuckDB 并行求和顺序影响，只允许
        # 1e-9 相对误差（实测差异出现在第 13–15 位有效数字）。
        def close(a, b):
            if a is None or b is None:
                return a == b
            return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))
        ok = rows == base[t]["rows"] and all(close(agg[k], base[t]["agg"][k]) for k in agg)
        if not ok:
            bad += 1
            diffs.append(t)
        print(f"  {'OK ' if ok else 'DIFF'} {t:16} rows {base[t]['rows']:,} -> {rows:,}  agg {base[t]['agg']} -> {agg}")
    result = {"tables": len(ASTOCK), "unchanged": len(ASTOCK) - bad,
              "diffs": diffs, "pass": bad == 0,
              "checked_at": _now(),
              "baseline": str(BASELINE)}
    (META / "work572-astock-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[astock] {'PASS' if bad == 0 else 'FAIL'}：{len(ASTOCK) - bad}/{len(ASTOCK)} 张零 diff")
    return 0 if bad == 0 else 1


def coverage():
    c = con()
    print(f"{'table':40} {'rows_before':>12} {'range_before':>24} {'rows_after':>10} {'range_after':>24}")
    total_before = total_after = 0
    for t, col in COVER.items():
        b = c.execute(f"SELECT count(*), min({col}), max({col}) FROM read_parquet('{PRE}/{t}.parquet')").fetchone()
        a = c.execute(f"SELECT count(*), min({col}), max({col}) FROM {t}").fetchone()
        total_before += b[0]
        total_after += a[0]
        print(f"{t:40} {b[0]:>12,} {f'{b[1]}..{b[2]}':>24} {a[0]:>10,} {f'{a[1]}..{a[2]}':>24}")
    print(f"{'合计':40} {total_before:>12,} {'':>24} {total_after:>10,}")
    return 0


def _diff_sql(c, name, left, right, cols):
    """全量双向 EXCEPT：返回 (left-right, right-left) 行数。"""
    sel = ", ".join(cols)
    l = c.execute(f"SELECT {sel} FROM {left} EXCEPT SELECT {sel} FROM {right}").fetchall()
    r = c.execute(f"SELECT {sel} FROM {right} EXCEPT SELECT {sel} FROM {left}").fetchall()
    print(f"[spot] {name}: 左-右={len(l)} 右-左={len(r)}")
    for x in (l[:3] + r[:3]):
        print(f"     样例差异 {x}")
    return len(l), len(r)


def spot():
    c = con()
    ok = True
    # 1) OWID：全部行回对（raw CSV vs 表），按 slug/entity/year
    left = f"read_csv_auto('{RAW}/industry_alt/ow_iea_usgs_timeseries/data.csv', all_varchar=true)"
    l, r = _diff_sql(c, "owid slug/entity/year/value（排除 status 行）",
                     f"(SELECT slug, entity, CAST(year AS INTEGER) AS year, CAST(value AS DOUBLE) AS value FROM {left} WHERE status='OK')",
                     "industry_ow_iea_usgs_timeseries", ["slug", "entity", "year", "value"])
    ok &= (l == 0 and r == 0)
    # 2) GFEX：2023-08（回填新增的第一批月份）按 交易日×合约×字段
    left = f"read_parquet('{RAW}/industry/gfex_lithium_carbonate/part.parquet')"
    # raw 里 trade_date 两种写法并存（'20230721' 与 '2023-07-21'，历史抓取不同版本），
    # 统一去横线成 YYYYMMDD 再比；DB 侧用 strftime 求同。
    l, r = _diff_sql(
        c, "gfex 2023-08 新增期间（排除 unit，成交额单位属已知规范化）",
        f"(SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS trade_date, contract, field, value "
        f"FROM {left} WHERE replace(CAST(trade_date AS VARCHAR), '-', '') LIKE '202308%')",
        "(SELECT strftime(trade_date, '%Y%m%d') AS trade_date, contract, field, value "
        "FROM industry_gfex_lithium_carbonate WHERE trade_date BETWEEN DATE '2023-08-01' AND DATE '2023-08-31')",
        ["trade_date", "contract", "field", "value"])
    ok &= (l == 0 and r == 0)
    print(f"[spot] {'PASS' if ok else 'FAIL'}（差集必须为 0）")
    return 0 if ok else 1


def idem():
    """同参数连跑两次装载：比较两份「17 源完整」装载报表的 loaded_rows / partitions。"""
    reports = []
    for p in sorted(META.glob("load-*.json"), key=lambda x: x.stat().st_mtime):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        srcs = data.get("sources") if isinstance(data, dict) else None
        if isinstance(srcs, list) and len(srcs) == 17:
            reports.append((p.name, data))
    if len(reports) < 2:
        print(f"[idem] 只找到 {len(reports)} 份 17 源完整装载报表，无法比较"); return 1
    (n1, r1), (n2, r2) = reports[-2], reports[-1]
    m1 = {x["table"]: (x["loaded_rows"], x["partitions"]) for x in r1["sources"]}
    m2 = {x["table"]: (x["loaded_rows"], x["partitions"]) for x in r2["sources"]}
    diff = {t: [m1.get(t), m2.get(t)] for t in sorted(set(m1) | set(m2)) if m1.get(t) != m2.get(t)}
    res = {"run_1": n1, "run_2": n2, "tables": len(m1), "diffs": diff,
           "pass": not diff, "compared_at": _now()}
    (META / "work572-idempotency.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[idem] {n1} vs {n2}: {len(m1)} 表，差异 {len(diff)} 处 -> {'PASS' if not diff else 'FAIL'}")
    for t, v in diff.items():
        print(f"   DIFF {t}: {v[0]} -> {v[1]}")
    return 0 if not diff else 1


def shape(baseline: int | None = None):
    c = con()
    tabs = [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE' ORDER BY 1").fetchall()]
    # industry_forecast 是 WORK-584 的预测表，不是观测源（目标期在未来，不进 17 表口径）；
    # shape 的本职是核对 17 张观测表 + 265,023 行这类回填基线，预测表单独打印。
    skip = ("industry_catalog_map", "industry_load_audit", "industry_forecast")
    ind = [t for t in tabs if t.startswith("industry_") and t not in skip]
    print(f"[shape] 库内 BASE TABLE 共 {len(tabs)} 张；industry_* 观测表 {len(ind)} 张")
    if "industry_forecast" in tabs:
        print(f"[shape] 预测表 industry_forecast "
              f"{c.execute('SELECT count(*) FROM industry_forecast').fetchone()[0]} 行（不计入观测表合计）")
    print(f"[shape] data_catalog 行数 {c.execute('SELECT count(*) FROM data_catalog').fetchone()[0]}")
    tot = 0
    for t in sorted(ind):
        n = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        tot += n
        print(f"  {t:40} {n:>10,}")
    # 交叉核对：同一条 SQL 里再求一次总和，与逐表相加比较（两条独立代码路径）。
    union_sql = " UNION ALL ".join(f"SELECT count(*) AS n FROM {t}" for t in sorted(ind))
    sql_total = c.execute(f"SELECT sum(n) FROM ({union_sql})").fetchone()[0] or 0
    if sql_total != tot:
        print(f"[shape] 合计交叉核对 FAIL：逐表相加 {tot:,} != SQL 汇总 {sql_total:,}")
        return 1
    print(f"[shape] industry_* 合计 {tot:,} 行（交叉核对 PASS：逐表相加 = SQL 汇总；{len(ind)} 张表）")
    if baseline is not None:
        if tot < baseline:
            print(f"[shape] 行数回退 FAIL：合计 {tot:,} < 基线 {baseline:,}（增量不许丢历史）")
            return 1
        print(f"[shape] 行数回退检查 PASS：{tot:,} >= 基线 {baseline:,}")
    return 0 if len(ind) == 17 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "shape"
    fn = {"snapshot": snapshot, "astock": astock, "coverage": coverage,
          "spot": spot, "shape": shape, "idem": idem}.get(cmd)
    if not fn:
        print(__doc__)
        sys.exit(2)
    kwargs = {}
    if cmd == "shape" and "--baseline" in sys.argv:
        kwargs["baseline"] = int(sys.argv[sys.argv.index("--baseline") + 1])
    try:
        sys.exit(fn(**kwargs) or 0)
    except BrokenPipeError:      # `| head` 提前关闭管道时不算失败
        sys.exit(0)
