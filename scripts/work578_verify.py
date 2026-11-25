#!/usr/bin/env python3
"""WORK-578 交易所日行情回补校验（只读主库 + 直读 raw CSV）。

    python scripts/work578_verify.py snapshot    # 装载前拍基线（20 张 A 股表 + 17 张 industry 表 + 交易所分布）
    python scripts/work578_verify.py astock      # 装载后比对 A 股 20 表零 diff（对当日基线）
    python scripts/work578_verify.py rows        # 17 张 industry 表行数快照（幂等比对用）
    python scripts/work578_verify.py shape       # 表数 / data_catalog 行数 / industry_* 合计（列相加自检）
    python scripts/work578_verify.py exchange    # 交易所表形态 + raw CSV 双向 EXCEPT + 装载审计

设计：
  * 只读打开主库（read_only=True），本脚本不写库；
  * A 股基线写 meta/industry/work578-astock-baseline.json（不覆盖 WORK-572 的基线文件）；
  * raw 比对直接 read_csv 原文件，不经过 db.etl.industry 的 transform（避免自证）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

DB = "/srv/share/quantdb/db/quantdb.duckdb"
RAW_CSV = "/srv/share/quantdb/raw/industry_alt/exchange_daily_quotes/data.csv"
META = Path("/srv/share/quantdb/meta/industry")
BASELINE = META / "work578-astock-baseline.json"
EXCHANGE = "industry_exchange_daily_quotes"

ASTOCK = ["daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d", "index_daily",
          "index_weight", "index_classify", "stock_basic", "trade_cal", "namechange",
          "income", "balancesheet", "cashflow", "fina_indicator", "dividend",
          "report_meta", "consensus_eps", "rating_change", "data_catalog"]

EXCHANGE_COLS = ["exchange", "contract", "month", "trade_date", "close", "settle",
                 "volume", "oi", "status"]


def con(read_only: bool = True):
    return duckdb.connect(DB, read_only=read_only)


def _industry_tables(c) -> list[str]:
    return [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'industry\\_%' ESCAPE '\\' ORDER BY 1").fetchall()]


def _fingerprint(c, table: str) -> dict:
    rows = c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    cols = [r[0] for r in c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=? "
        "AND data_type IN ('DOUBLE','BIGINT','INTEGER','HUGEINT')", [table]).fetchall()]
    agg = {}
    for col in cols[:2]:
        val = c.execute(f'SELECT round(sum("{col}"), 6) FROM "{table}"').fetchone()[0]
        agg[col] = float(val) if val is not None else None
    return {"rows": int(rows), "agg": agg}


def snapshot() -> int:
    META.mkdir(parents=True, exist_ok=True)
    c = con()
    stocks = {t: _fingerprint(c, t) for t in ASTOCK}
    industry = {t: _fingerprint(c, t)["rows"] for t in _industry_tables(c)}
    exchange = c.execute(
        f"SELECT count(*) AS rows, count(DISTINCT trade_date) AS dates, "
        f"min(trade_date) AS first_date, max(trade_date) AS last_date FROM {EXCHANGE}").fetchone()
    payload = {
        "generated_at": _now(),
        "db": DB,
        "astock": stocks,
        "industry_rows": industry,
        "exchange_before": {"rows": int(exchange[0]), "dates": int(exchange[1]),
                            "first": str(exchange[2]), "last": str(exchange[3])},
    }
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[snapshot] 基线 -> {BASELINE}")
    print(f"[snapshot] A 股 20 表行数合计 {sum(v['rows'] for v in stocks.values()):,}；"
          f"industry 表 {len(industry)} 张行数合计 {sum(industry.values()):,}；"
          f"exchange 表 {exchange[0]} 行 / {exchange[1]} 个交易日")
    return 0


def _now() -> str:
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def astock() -> int:
    if not BASELINE.exists():
        print("[astock] 缺基线，先跑 snapshot")
        return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    c = con()
    bad = []
    detail = {}
    for t in ASTOCK:
        now = _fingerprint(c, t)
        old = base["astock"][t]
        diff = {"rows_before": old["rows"], "rows_after": now["rows"]}
        same = now["rows"] == old["rows"]
        for col, val in old["agg"].items():
            cur = now["agg"].get(col)
            if val is None and cur is None:
                continue
            if val is None or cur is None:
                same = False
                diff[col] = {"before": val, "after": cur}
                continue
            # DOUBLE 求和的浮点重结合噪声：相对 1e-9 / 绝对 1e-6 以内视为相同
            tol = max(1e-6, 1e-9 * max(abs(float(val)), abs(float(cur))))
            if abs(float(val) - float(cur)) > tol:
                same = False
                diff[col] = {"before": val, "after": cur}
        if not same:
            bad.append(t)
            detail[t] = diff
    out = {"checked_at": _now(), "baseline_generated_at": base["generated_at"],
           "tables": len(ASTOCK), "zero_diff": len(ASTOCK) - len(bad), "diff_tables": detail}
    (META / "work578-astock-result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[astock] {'PASS' if not bad else 'FAIL'}：{len(ASTOCK) - len(bad)}/{len(ASTOCK)} 张零 diff"
          f"（基线 {base['generated_at']}）")
    if bad:
        print(json.dumps(detail, ensure_ascii=False, indent=1))
    return 0 if not bad else 1


def rows() -> int:
    c = con()
    out = {t: int(c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0])
           for t in _industry_tables(c)}
    print(json.dumps({"captured_at": _now(), "tables": len(out), "rows": out,
                      "total": sum(out.values())}, ensure_ascii=False))
    return 0


def shape() -> int:
    c = con()
    tables = [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main' "
        "ORDER BY table_name").fetchall()]
    views = [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.views WHERE table_schema='main' "
        "ORDER BY table_name").fetchall()]
    aux = [t for t in _industry_tables(c) if t in ("industry_catalog_map", "industry_load_audit")]
    industry = [t for t in _industry_tables(c) if t not in aux]
    ind_rows = {t: int(c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in industry}
    catalog = int(c.execute("SELECT count(*) FROM data_catalog").fetchone()[0])
    total = sum(ind_rows.values())
    print(json.dumps({
        "checked_at": _now(),
        "base_tables": len(tables) - len(industry) - len(aux),
        "industry_data_tables": len(industry),
        "industry_aux_tables": aux,
        "views": len(views),
        "data_catalog_rows": catalog,
        "industry_rows": ind_rows,
        "industry_total": total,
        "sum_check": "列相加 = 合计：%d = %d -> %s"
                     % (total, sum(ind_rows.values()), total == sum(ind_rows.values())),
        "table_names": tables,
    }, ensure_ascii=False, indent=1))
    return 0


def exchange() -> int:
    c = con()
    q = {
        "rows": f"SELECT count(*) FROM {EXCHANGE}",
        "dates": f"SELECT count(DISTINCT trade_date) FROM {EXCHANGE}",
        "first": f"SELECT min(trade_date) FROM {EXCHANGE}",
        "last": f"SELECT max(trade_date) FROM {EXCHANGE}",
    }
    head = {k: c.execute(sql).fetchone()[0] for k, sql in q.items()}
    by_exchange = c.execute(
        f"SELECT exchange, count(*) AS rows, count(DISTINCT trade_date) AS dates, "
        f"min(trade_date) AS first, max(trade_date) AS last FROM {EXCHANGE} "
        f"GROUP BY 1 ORDER BY 1").fetchall()
    # 交易日缺口：主库 trade_cal（SSE）窗口内开市日 vs 表内已有的 SHFE 日
    cal_start, cal_end = head["first"], head["last"]
    cal = c.execute(
        "SELECT count(*) FROM trade_cal WHERE exchange='SSE' AND is_open=1 "
        "AND cal_date BETWEEN ? AND ?", [cal_start, cal_end]).fetchone()[0]
    shfe_dates = c.execute(
        f"SELECT count(DISTINCT trade_date) FROM {EXCHANGE} WHERE exchange='SHFE'").fetchone()[0]
    # raw CSV 双向比对（键 + 数值），日期统一成字符串
    raw_sql = (
        "SELECT exchange, contract, month, strftime(trade_date, '%Y%m%d') AS trade_date, "
        "close, settle, volume, oi, status FROM " + EXCHANGE)
    csv_sql = (
        "SELECT exchange, CAST(contract AS VARCHAR) AS contract, CAST(month AS VARCHAR) AS month, "
        "CAST(trade_date AS VARCHAR) AS trade_date, "
        "CAST(close AS DOUBLE) AS close, CAST(settle AS DOUBLE) AS settle, "
        "CAST(volume AS DOUBLE) AS volume, CAST(oi AS DOUBLE) AS oi, "
        f"status FROM read_csv('{RAW_CSV}', header=true, all_varchar=true)")
    only_db = c.execute(f"SELECT count(*) FROM ({raw_sql} EXCEPT {csv_sql})").fetchone()[0]
    only_csv = c.execute(f"SELECT count(*) FROM ({csv_sql} EXCEPT {raw_sql})").fetchone()[0]
    csv_rows = c.execute(f"SELECT count(*) FROM read_csv('{RAW_CSV}', header=true, "
                         f"all_varchar=true)").fetchone()[0]
    audits = c.execute(
        "SELECT run_id, raw_rows, staged_rows, loaded_rows, partitions, mode, finished_at "
        "FROM industry_load_audit WHERE table_name=? ORDER BY finished_at DESC LIMIT 4",
        [EXCHANGE]).fetchall()
    print(json.dumps({
        "checked_at": _now(),
        "db": {**{k: str(v) for k, v in head.items()}},
        "by_exchange": [{"exchange": r[0], "rows": int(r[1]), "dates": int(r[2]),
                         "first": str(r[3]), "last": str(r[4])} for r in by_exchange],
        "trade_cal_open_days_in_window": int(cal),
        "shfe_distinct_dates": int(shfe_dates),
        "coverage_gap_shfe": int(cal) - int(shfe_dates),
        "raw_csv_rows": int(csv_rows),
        "csv_vs_db": {"only_in_db": int(only_db), "only_in_csv": int(only_csv)},
        "load_audit_recent": [
            {"run_id": r[0], "raw": int(r[1]), "staged": int(r[2]), "loaded": int(r[3]),
             "partitions": int(r[4]), "mode": r[5], "finished_at": str(r[6])} for r in audits],
    }, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    fn = {"snapshot": snapshot, "astock": astock, "rows": rows, "shape": shape,
          "exchange": exchange}
    if not argv or argv[0] not in fn:
        print(__doc__)
        return 2
    return fn[argv[0]]()


if __name__ == "__main__":
    sys.exit(main())
