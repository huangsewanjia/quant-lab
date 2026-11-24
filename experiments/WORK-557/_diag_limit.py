#!/usr/bin/env python3
"""WORK-557 段1 收尾诊断：定位 stk_limit implied band 异常的成因（口径问题定位，非交付数字）。

只打印诊断观察，不写产物；结论回填到 crosscheck.json 的 note。
"""
from __future__ import annotations

import os

import duckdb

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
RAW = os.path.join(REPO, "data", "WORK-557", "cache", "raw_local").replace("\\", "/")

con = duckdb.connect()
con.execute("PRAGMA threads=8")
con.execute("SET memory_limit='8GB'")
con.execute("""
    create table d as
    select trade_date, ts_code, close, pre_close,
           lag(close) over (partition by ts_code order by trade_date) as prev_close,
           row_number() over (partition by ts_code order by trade_date) as rn
    from read_parquet('%s/daily/trade_date=*/part.parquet')
    where trade_date between 20090101 and 20241231
""" % RAW)
con.execute("""
    create table sl as
    select trade_date, ts_code, up_limit, down_limit
    from read_parquet('%s/stk_limit/trade_date=*/part.parquet', union_by_name=true)
    where trade_date between 20090101 and 20241231
""" % RAW)
con.execute("""
    create table j as
    select d.trade_date, d.ts_code, d.close, d.prev_close, d.pre_close, d.rn,
           sl.up_limit, sl.down_limit, split_part(d.ts_code,'.',1) as num,
           case when split_part(d.ts_code,'.',1) like '688%'
                  or split_part(d.ts_code,'.',1) like '689%' then 'STAR'
                when split_part(d.ts_code,'.',1) like '30%'  then 'ChiNext'
                when split_part(d.ts_code,'.',1) like '8%'
                  or split_part(d.ts_code,'.',1) like '4%'  then 'BSE'
                else 'MainBoard' end as board
    from d join sl on sl.trade_date=d.trade_date and sl.ts_code=d.ts_code
    where d.prev_close>0 and sl.up_limit is not null
""")

print("=== 主板 implied band>=25% 样本 ===", flush=True)
for r in con.execute("""
    select trade_date, ts_code, rn, close, prev_close, pre_close, up_limit,
           round(up_limit/prev_close-1,4) as impl_vs_lag,
           round(up_limit/pre_close-1,4) as impl_vs_preclose
    from j where board='MainBoard' and up_limit/prev_close-1>=0.25
    order by trade_date limit 8""").fetchall():
    print("  ", r)
print("  主板该类总行数:",
      con.execute("""select count(*) from j where board='MainBoard'
                     and up_limit/prev_close-1>=0.25""").fetchone()[0])
print("  其中 rn=1(该股首根 bar):",
      con.execute("""select count(*) from j where board='MainBoard'
                     and up_limit/prev_close-1>=0.25 and rn=1""").fetchone()[0])
print("  其中 pre_close 与 lag(prev_close) 不等:",
      con.execute("""select count(*) from j where board='MainBoard'
                     and up_limit/prev_close-1>=0.25
                     and abs(pre_close-prev_close)>0.005""").fetchone()[0])

print("\n=== 主板 implied band>=25% 的 up_limit/pre_close-1 分布 ===", flush=True)
for r in con.execute("""
    select round(up_limit/pre_close-1,3) as impl, count(*) n from j
    where board='MainBoard' and up_limit/prev_close-1>=0.25
    group by 1 order by 2 desc limit 8""").fetchall():
    print("  ", r)

for board, lo, hi, title in (("ChiNext", 0.075, 0.15, "创业板 implied band=10%"),
                              ("ChiNext", 0.15, 0.25, "创业板 implied band=20%")):
    print("\n=== %s 的年份分布 ===" % title, flush=True)
    for r in con.execute("""
        select cast(trade_date/10000 as int) yr, count(*) n,
               min(trade_date) mn, max(trade_date) mx
        from j where board=? and up_limit/prev_close-1>=? and up_limit/prev_close-1<?
        group by 1 order by 1""", [board, lo, hi]).fetchall():
        print("  ", r)

print("\n=== 主板 implied band=5%（ST/风险警示）年份分布 ===", flush=True)
for r in con.execute("""
    select cast(trade_date/10000 as int) yr, count(*) n from j
    where board='MainBoard' and up_limit/prev_close-1<0.075 group by 1 order by 1""").fetchall():
    print("  ", r)

print("\n=== 全表：若改用库内 pre_close（而非 lag 前收盘）算 implied band 的板块一致性 ===", flush=True)
for r in con.execute("""
    select board, count(*) n,
           sum(case when up_limit/pre_close-1 < 0.075 then 1 else 0 end) as b5,
           sum(case when up_limit/pre_close-1>=0.075 and up_limit/pre_close-1<0.15 then 1 else 0 end) as b10,
           sum(case when up_limit/pre_close-1>=0.15 and up_limit/pre_close-1<0.25 then 1 else 0 end) as b20,
           sum(case when up_limit/pre_close-1>=0.25 then 1 else 0 end) as b30
    from j where pre_close is not null and pre_close>0 group by 1 order by 1""").fetchall():
    print("  ", r)
