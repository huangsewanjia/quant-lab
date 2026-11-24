#!/usr/bin/env python3
"""WORK-557 段1：member-day 缺失率**逐年**（在本地 parquet 副本上算，不再扫网络盘）。

派单卡停手条件之一：「库里 2009–2013 数据缺口 > 5% → 停手回报缺多少、哪年缺」。
本脚本用本地快照副本 + PIT 名单，逐 member-day 比对，输出每年缺失率，
并写回 experiments/WORK-557/data_probe.json。

口径：member-day = (PIT 成分, 交易日) 的笛卡尔积；缺 = 该表里没有这一行。
**daily 无行 = 停牌**（与 WORK-503 的新浪口径一致：停牌日价格前向填充、成交量置 0），
不等于数据缺口 —— 报告里两个口径分开说。
"""
from __future__ import annotations

import json
import os

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EXP = "WORK-557"
RAW = os.path.join(REPO, "data", EXP, "cache", "raw_local").replace("\\", "/")
EXP_DIR = os.path.join(REPO, "experiments", EXP)

WINDOW = (20090101, 20241231)
TABLES = {
    "daily": ("daily", "%s/daily/trade_date=*/part.parquet" % RAW),
    "adj_factor": ("adj_factor", "%s/adj_factor/trade_date=*/part.parquet" % RAW),
    "stk_limit": ("stk_limit", "%s/stk_limit/trade_date=*/part.parquet" % RAW),
    "daily_basic": ("daily_basic", "%s/daily_basic/trade_date=*/part.parquet" % RAW),
}


def main():
    probe_path = os.path.join(EXP_DIR, "data_probe.json")
    probe = json.load(open(probe_path, encoding="utf-8")) if os.path.exists(probe_path) else {}

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute("SET memory_limit='8GB'")

    mem_csv = os.path.join(EXP_DIR, "pool_membership.csv").replace("\\", "/")
    con.execute("""
        create table mem as
        select cast(replace(cast(effective_date as varchar),'-','') as bigint) as eff,
               -- pool_membership.csv 用 baostock 风格的 "sh.600519"；
               -- quantdb 的 ts_code 是 "600519.SH" —— 这里做格式对齐
               substring(code from 4) || '.' || upper(substring(code from 1 for 2)) as ts_code
        from read_csv('%s', header=true)
    """ % mem_csv)
    con.execute("create table cal as select distinct trade_date from "
                "read_parquet('%s/daily/trade_date=*/part.parquet') "
                "where trade_date between %d and %d" % (RAW, WINDOW[0], WINDOW[1]))
    print("calendar days:", con.execute("select count(*) from cal").fetchone()[0],
          flush=True)
    con.execute("""
        create table pit as
        select c.trade_date, m.ts_code
        from cal c
        join mem m on m.eff = (
            select max(m2.eff) from mem m2 where m2.eff <= c.trade_date
        )
    """)
    n_md = con.execute("select count(*) from pit").fetchone()[0]
    print("member-days:", n_md, " codes:",
          con.execute("select count(distinct ts_code) from pit").fetchone()[0],
          flush=True)

    # ---- PIT 名单逐年 active 数（段1 交付项） ---------------------------
    pit_year = con.execute("""
        select cast(trade_date/10000 as int) as year,
               count(distinct trade_date) as trading_days,
               avg(active) as active_mean,
               min(active) as active_min,
               max(active) as active_max
        from (
            select trade_date, count(*) as active from pit group by trade_date
        ) group by 1 order by 1
    """).fetchall()
    distinct_members = dict(con.execute(
        "select cast(trade_date/10000 as int), count(distinct ts_code) "
        "from pit group by 1").fetchall())
    probe["pit_by_year"] = [
        {"year": int(y), "trading_days": int(td), "active_mean": float(am),
         "active_min": int(amin), "active_max": int(amax),
         "distinct_members": int(distinct_members.get(y, 0))}
        for y, td, am, amin, amax in pit_year]
    probe["pit"] = {"stock_day_pairs": int(n_md),
                    "unique_constituents": int(con.execute(
                        "select count(distinct ts_code) from pit").fetchone()[0])}
    probe["trade_calendar"] = {"open_days_in_window": int(con.execute(
        "select count(*) from cal").fetchone()[0]),
        "min": WINDOW[0], "max": WINDOW[1]}
    snap_stat = con.execute("""
        select count(distinct eff) as snapshots, min(eff) as dmin, max(eff) as dmax
        from mem
    """).fetchone()
    probe["index_weight"] = {
        "snapshots": int(snap_stat[0]), "snapshot_date_min": int(snap_stat[1]),
        "snapshot_date_max": int(snap_stat[2])}
    print("\n=== PIT 名单逐年 active 数 ===", flush=True)
    print("%-6s %-9s %-10s %-10s %-10s %s"
          % ("年", "交易日", "active均值", "active最小", "active最大", "不同成分"), flush=True)
    for r in probe["pit_by_year"]:
        print("%-6d %-9d %-10.1f %-10d %-10d %d"
              % (r["year"], r["trading_days"], r["active_mean"], r["active_min"],
                 r["active_max"], r["distinct_members"]), flush=True)

    out = {}
    for label, (base, path) in TABLES.items():
        if not os.path.isdir(os.path.join(RAW, base)):
            print("skip (not copied):", label, flush=True)
            continue
        df = con.execute("""
            select cast(p.trade_date/10000 as int) as year,
                   count(*) as member_days,
                   sum(case when d.ts_code is null then 1 else 0 end) as missing
            from pit p
            left join (
                select trade_date, ts_code from read_parquet('%s')
                where trade_date between %d and %d
            ) d on d.trade_date = p.trade_date and d.ts_code = p.ts_code
            group by 1 order by 1
        """ % (path, WINDOW[0], WINDOW[1])).fetchall()
        by_year = {str(int(y)): int(mi) for y, _md, mi in df}
        rates = {}
        tot_md = 0
        tot_mi = 0
        for y, md, mi in df:
            rates[str(int(y))] = float(mi) / float(md)
            tot_md += int(md)
            tot_mi += int(mi)
        out[label] = {
            "member_days": int(tot_md),
            "missing_pairs": int(tot_mi),
            "missing_rate": (tot_mi / tot_md) if tot_md else None,
            "missing_by_year": by_year,
            "missing_rate_by_year": rates,
        }
        print("=== %s ===" % label, flush=True)
        for y, md, mi in df:
            print("  %d  member_days=%-7d missing=%-6d rate=%.4f%%"
                  % (y, md, mi, 100 * mi / md), flush=True)

    probe["missing_rate"] = out
    probe["missing_rate_note"] = (
        "member-day 口径：PIT 成分 × 交易日；daily 无行 = 停牌（WORK-503 同口径："
        "停牌日价格前向填充、成交量置 0），不等于数据缺口。逐表逐行来自 "
        "data/WORK-557/cache/raw_local（适配层从 Z:\\quantdb\\raw 并行拷下的只读副本）。")
    with open(probe_path, "w", encoding="utf-8") as fh:
        json.dump(probe, fh, ensure_ascii=False, indent=1)
    print("\nwrote -> %s" % probe_path)

    print("\n=== 停手条件①：2009–2013 逐年缺口 > 5% ? ===")
    stop = False
    for label, v in out.items():
        rs = [v["missing_rate_by_year"][y] for y in
              ("2009", "2010", "2011", "2012", "2013")
              if y in v["missing_rate_by_year"]]
        if not rs:
            continue
        worst = max(rs)
        hit = worst > 0.05
        stop = stop or hit
        print("  %-12s 2009-2013 最大逐年缺失率 = %.4f%%  -> %s"
              % (label, 100 * worst, "触发" if hit else "未触发"))
    print("  停手条件① 总体：%s" % ("触发 → 停手回报" if stop else "未触发，继续"))


if __name__ == "__main__":
    main()
