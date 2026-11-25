#!/usr/bin/env python3
"""WORK-578 交易所日行情回补校验（只读主库 + 直读 raw CSV + 可选对冷备库）。

    python scripts/work578_verify.py snapshot    # 装载前拍基线（20 张 A 股表全列哈希 + 17 张 industry 表 + 交易所分布）
    python scripts/work578_verify.py astock      # 与基线比对 A 股 20 表（行数 + 全列哈希）
    python scripts/work578_verify.py astock --against /srv/backup/quantdb/db/quantdb.duckdb
                                                 # 直接与另一份库（如每晚冷备）逐表全列哈希比对
    python scripts/work578_verify.py rows        # 17 张 industry 表行数快照（幂等比对用）
    python scripts/work578_verify.py shape       # 表数 / data_catalog 行数 / industry_* 合计
    python scripts/work578_verify.py exchange    # 交易所表形态 + raw CSV 双向 EXCEPT + 装载审计

设计：
  * 只读打开主库（read_only=True），本脚本不写库；
  * A 股指纹是「行数 + 全列行哈希之和」（`hash(concat_ws(\\x1f, COLUMNS(*)))`），不是只比前两列；
    哈希对 NULL 与空串不区分（concat_ws 跳 NULL），用于「有没有变」的判定足够；
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
AUX_TABLES = ("industry_catalog_map", "industry_load_audit")

ASTOCK = ["daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d", "index_daily",
          "index_weight", "index_classify", "stock_basic", "trade_cal", "namechange",
          "income", "balancesheet", "cashflow", "fina_indicator", "dividend",
          "report_meta", "consensus_eps", "rating_change", "data_catalog"]

PREFIX = "\\x1f"


def con(read_only: bool = True):
    return duckdb.connect(DB, read_only=read_only)


def _fingerprint(c, table: str, schema: str | None = None) -> dict:
    """行数 + 全列行哈希之和（UBIGINT 求和后转字符串，避免 JSON 大整数丢精度）。"""
    name = f'{schema}."{table}"' if schema else f'"{table}"'
    rows, digest = c.execute(
        "SELECT count(*), sum(h) FROM "
        f"(SELECT hash(concat_ws('{PREFIX}', COLUMNS(*))) AS h FROM {name})"
    ).fetchone()
    return {"rows": int(rows), "hash": None if digest is None else str(digest)}


def _now() -> str:
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _industry_tables(c) -> list[str]:
    return [r[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_type='BASE TABLE' "
        "AND table_name LIKE 'industry\\_%' ESCAPE '\\' ORDER BY 1").fetchall()]


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
        "fingerprint": "rows + sum(hash(concat_ws('\\x1f', COLUMNS(*))))",
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


def astock(against: str | None = None) -> int:
    c = con()
    if against:
        c.execute(f"ATTACH '{against}' AS bak (READ_ONLY)")
        left, right, label = "bak", "main", against
    else:
        if not BASELINE.exists():
            print("[astock] 缺基线，先跑 snapshot")
            return 1
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        if base.get("fingerprint") is None:
            print("[astock] 基线是旧指纹格式（只有两列求和），请重跑 snapshot")
            return 1
        bad = []
        detail = {}
        for t in ASTOCK:
            now = _fingerprint(c, t)
            old = base["astock"][t]
            if now["rows"] != old["rows"] or now["hash"] != old["hash"]:
                bad.append(t)
                detail[t] = {"before": old, "after": now}
        out = {"checked_at": _now(), "baseline_generated_at": base["generated_at"],
               "method": base["fingerprint"], "tables": len(ASTOCK),
               "zero_diff": len(ASTOCK) - len(bad), "diff_tables": detail}
        (META / "work578-astock-result.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[astock] {'PASS' if not bad else 'FAIL'}：{len(ASTOCK) - len(bad)}/{len(ASTOCK)} "
              f"张行数+全列哈希一致（基线 {base['generated_at']}）")
        if bad:
            print(json.dumps(detail, ensure_ascii=False, indent=1))
        return 0 if not bad else 1

    bad, detail = [], {}
    for t in ASTOCK:
        a = _fingerprint(c, t, left)
        b = _fingerprint(c, t, right)
        if a != b:
            bad.append(t)
            detail[t] = {"left": a, "right": b}
    out = {"checked_at": _now(), "against": label, "tables": len(ASTOCK),
           "zero_diff": len(ASTOCK) - len(bad), "diff_tables": detail}
    (META / "work578-astock-result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[astock] {'PASS' if not bad else 'FAIL'}：{len(ASTOCK) - len(bad)}/{len(ASTOCK)} "
          f"张行数+全列哈希一致（对照 {label}）")
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
    base_tables = int(c.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_type='BASE TABLE'").fetchone()[0])
    user_views = int(c.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal").fetchone()[0])
    aux = [t for t in _industry_tables(c) if t in AUX_TABLES]
    industry = [t for t in _industry_tables(c) if t not in AUX_TABLES]
    ind_rows = {t: int(c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in industry}
    catalog = int(c.execute("SELECT count(*) FROM data_catalog").fetchone()[0])
    total = sum(ind_rows.values())
    print(json.dumps({
        "checked_at": _now(),
        "base_tables_total": base_tables,
        "industry_data_tables": len(industry),
        "industry_aux_tables": aux,
        "astock_tables": len(ASTOCK),
        "user_views": user_views,
        "data_catalog_rows": catalog,
        "industry_rows": ind_rows,
        "industry_total": total,
        "note": "industry_total 为逐表行数相加（信息性；并发装载期间会变）",
    }, ensure_ascii=False, indent=1))
    if len(industry) != 17:
        print(f"[shape] FAIL：industry 数据表应为 17 张，实际 {len(industry)} 张")
        return 1
    return 0


def exchange() -> int:
    c = con()
    head = {
        "rows": c.execute(f"SELECT count(*) FROM {EXCHANGE}").fetchone()[0],
        "dates": c.execute(f"SELECT count(DISTINCT trade_date) FROM {EXCHANGE}").fetchone()[0],
        "first": c.execute(f"SELECT min(trade_date) FROM {EXCHANGE}").fetchone()[0],
        "last": c.execute(f"SELECT max(trade_date) FROM {EXCHANGE}").fetchone()[0],
    }
    by_exchange = c.execute(
        f"SELECT exchange, count(*) AS rows, count(DISTINCT trade_date) AS dates, "
        f"min(trade_date) AS first, max(trade_date) AS last FROM {EXCHANGE} "
        f"GROUP BY 1 ORDER BY 1").fetchall()
    cal = c.execute(
        "SELECT count(*) FROM trade_cal WHERE exchange='SSE' AND is_open=1 "
        "AND cal_date BETWEEN ? AND ?", [head["first"], head["last"]]).fetchone()[0]
    shfe_dates = c.execute(
        f"SELECT count(DISTINCT trade_date) FROM {EXCHANGE} WHERE exchange='SHFE'").fetchone()[0]
    extra = c.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT trade_date FROM {EXCHANGE}) s "
        f"WHERE trade_date NOT IN (SELECT cal_date FROM trade_cal WHERE exchange='SSE' AND is_open=1)"
    ).fetchone()[0]
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
        "db": {k: str(v) for k, v in head.items()},
        "by_exchange": [{"exchange": r[0], "rows": int(r[1]), "dates": int(r[2]),
                         "first": str(r[3]), "last": str(r[4])} for r in by_exchange],
        "trade_cal_open_days_in_window": int(cal),
        "shfe_distinct_dates": int(shfe_dates),
        "coverage_gap_shfe": max(0, int(cal) - int(shfe_dates)),
        "dates_outside_trade_cal": int(extra),
        "raw_csv_rows": int(csv_rows),
        "csv_vs_db": {"only_in_db": int(only_db), "only_in_csv": int(only_csv)},
        "load_audit_recent": [
            {"run_id": r[0], "raw": int(r[1]), "staged": int(r[2]), "loaded": int(r[3]),
             "partitions": int(r[4]), "mode": r[5], "finished_at": str(r[6])} for r in audits],
    }, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    against = None
    if "--against" in argv:
        i = argv.index("--against")
        against = argv[i + 1] if i + 1 < len(argv) else None
        if not against:
            print(__doc__)
            return 2
        del argv[i:i + 2]
    fn = {"snapshot": snapshot, "astock": astock, "rows": rows, "shape": shape,
          "exchange": exchange}
    if not argv or argv[0] not in fn:
        print(__doc__)
        return 2
    if argv[0] == "astock":
        return fn["astock"](against)
    return fn[argv[0]]()


if __name__ == "__main__":
    sys.exit(main())
