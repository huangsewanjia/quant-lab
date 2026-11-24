#!/usr/bin/env python3
"""WORK-557 段1 收尾：stk_limit / suspend_d 到位后的**一致性交叉核对**。

背景
----
段1 回帖里承诺「stk_limit / suspend_d 两表到位后补一次一致性交叉核对」。
本脚本兑现这条承诺，做两件事：

**核对 A｜停牌判读是否成立**
段1 的关键判读是「`daily` 无行 = 停牌，不是数据缺口」。
quantdb 的 `suspend_d` 是**独立来源**的停牌事件表。
对每个 member-day (PIT 成分 × 交易日) 交叉计数：

    no_bar      = daily 无行
    in_susp     = suspend_d 有行
    no_bar ∧ ¬in_susp  ->  真·数据缺口（若非 0，段1 判读需修正）

同时核 WORK-503 的第二条停牌触发条件（`volume == 0` 但有行）有多少。

**核对 B｜涨跌停口径偏差**
`work503_build_panel.py` 的口径：
    bands(code) = 688/689 → {20%,5%}; 30x → {20%,5%}; 8x/4x → {30%,5%};
                  其余 → {10%,5%}
    limit_up   : close >= prev_traded_close * (1+b) - 0.005  对 b ∈ bands 取并集
    limit_down : close <= prev_traded_close * (1-b) + 0.005  对 b ∈ bands 取并集
    prev_traded_close = 上一个有行情日的收盘（未交易日 ffill）
取并集 = 取**最小 b** = 门槛最松 = **过度掩码**（符合 mask_contract「宁可多掩码」）。

本脚本量化它与 quantdb `stk_limit` 真值的偏差：
    implied_band = up_limit / pre_close - 1  （库里真实涨跌停幅度）
    503 判的 limit_up 标志 vs 库里 close >= up_limit 的真标志
    -> 过掩（503 判了、库里没到）/ 漏掩（库里到了、503 没判）

输出：experiments/WORK-557/crosscheck.json（数字只从这里取，不手抄）
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


def board_of(ts_code: str) -> str:
    """WORK-503 `band_for` 的板块分类（照抄，不重新定义）。"""
    num = ts_code.split(".")[-1]
    if num.startswith("688") or num.startswith("689"):
        return "STAR(688/689)"
    if num.startswith("30"):
        return "ChiNext(30)"
    if num.startswith("8") or num.startswith("4"):
        return "BSE(8/4)"
    return "MainBoard"


def main():
    out_path = os.path.join(EXP_DIR, "crosscheck.json")
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("SET memory_limit='4GB'")

    mem_csv = os.path.join(EXP_DIR, "pool_membership.csv").replace("\\", "/")
    con.execute("""
        create table mem as
        select cast(replace(cast(effective_date as varchar),'-','') as bigint) as eff,
               substring(code from 4) || '.' || upper(substring(code from 1 for 2)) as ts_code
        from read_csv('%s', header=true)
    """ % mem_csv)
    con.execute("""
        create table cal as select distinct trade_date from
        read_parquet('%s/daily/trade_date=*/part.parquet')
        where trade_date between %d and %d
    """ % (RAW, WINDOW[0], WINDOW[1]))
    con.execute("""
        create table pit as
        select c.trade_date, m.ts_code
        from cal c join mem m on m.eff = (
            select max(m2.eff) from mem m2 where m2.eff <= c.trade_date)
    """)

    # daily 侧：只保留有行情的行 + 上一有行情日收盘
    con.execute("""
        create table d as
        select trade_date, ts_code, close, pre_close, vol,
               lag(close) over (partition by ts_code order by trade_date) as prev_close
        from read_parquet('%s/daily/trade_date=*/part.parquet')
        where trade_date between %d and %d
    """ % (RAW, WINDOW[0], WINDOW[1]))
    con.execute("""
        create table s as
        select trade_date, ts_code from read_parquet('%s/suspend_d/trade_date=*/part.parquet')
        where trade_date between %d and %d
    """ % (RAW, WINDOW[0], WINDOW[1]))
    con.execute("""
        create table sl as
        select trade_date, ts_code, up_limit, down_limit
        from read_parquet('%s/stk_limit/trade_date=*/part.parquet', union_by_name=true)
        where trade_date between %d and %d
    """ % (RAW, WINDOW[0], WINDOW[1]))

    n_md = con.execute("select count(*) from pit").fetchone()[0]
    print("member-days:", n_md, flush=True)

    # ---------------- 核对 A：停牌 ----------------
    a = con.execute("""
        select
          count(*)                                                          as member_days,
          sum(case when d.ts_code is null then 1 else 0 end)               as no_bar,
          sum(case when s.ts_code is not null then 1 else 0 end)           as in_suspend_d,
          sum(case when d.ts_code is null and s.ts_code is not null then 1 else 0 end) as no_bar_and_susp,
          sum(case when d.ts_code is null and s.ts_code is null then 1 else 0 end)     as no_bar_not_susp,
          sum(case when d.ts_code is not null and s.ts_code is not null then 1 else 0 end) as bar_and_susp,
          sum(case when d.ts_code is not null and d.vol = 0 then 1 else 0 end)          as bar_vol_zero
        from pit p
        left join d on d.trade_date = p.trade_date and d.ts_code = p.ts_code
        left join s on s.trade_date = p.trade_date and s.ts_code = p.ts_code
    """).fetchone()
    keys = ("member_days", "no_bar", "in_suspend_d", "no_bar_and_susp",
            "no_bar_not_susp", "bar_and_susp", "bar_vol_zero")
    A = dict(zip(keys, a))

    # 逐年：真缺口（no_bar 且不在 suspend_d）
    yearly = con.execute("""
        select cast(p.trade_date/10000 as int) as year,
               count(*) as member_days,
               sum(case when d.ts_code is null then 1 else 0 end) as no_bar,
               sum(case when d.ts_code is null and s.ts_code is null then 1 else 0 end) as real_gap,
               sum(case when d.ts_code is null and s.ts_code is not null then 1 else 0 end) as suspended
        from pit p
        left join d on d.trade_date = p.trade_date and d.ts_code = p.ts_code
        left join s on s.trade_date = p.trade_date and s.ts_code = p.ts_code
        group by 1 order by 1
    """).fetchall()

    # suspend_d 全表（含非成分股）规模，供口径对照
    s_all = con.execute("select count(*) from s").fetchone()[0]
    s_codes = con.execute("select count(distinct ts_code) from s").fetchone()[0]

    # ---------------- 核对 B：涨跌停 ----------------
    # ts_code 形如 "600519.SH"：数字前缀在 '.' 之前，交易所后缀在 '.' 之后。
    # WORK-503 `band_for` 作用在 baostock 风格 "sh.600519" 上（数字部分在 '.' 之后），
    # 这里等价地取 split_part(ts_code,'.',1) 作为数字码。
    con.execute("""
        create table dband as
        select d.trade_date, d.ts_code, d.close, d.prev_close, d.pre_close,
               sl.up_limit, sl.down_limit,
               split_part(d.ts_code,'.',1) as num,
               case
                 when split_part(d.ts_code,'.',1) like '688%'
                   or split_part(d.ts_code,'.',1) like '689%' then 'STAR'
                 when split_part(d.ts_code,'.',1) like '30%'  then 'ChiNext'
                 when split_part(d.ts_code,'.',1) like '8%'
                   or split_part(d.ts_code,'.',1) like '4%'   then 'BSE'
                 else 'MainBoard'
               end as board
        from d join sl on sl.trade_date = d.trade_date and sl.ts_code = d.ts_code
        where d.prev_close is not null and d.prev_close > 0
          and sl.up_limit is not null and sl.down_limit is not null
    """)
    # WORK-503 的 bands 集合（照抄 band_for）：
    #   STAR/ChiNext -> {0.20, 0.05};  BSE -> {0.30, 0.05};  MainBoard -> {0.10, 0.05}
    # 对 limit_up 取并集 = 取**最小 b**（门槛最松）-> 四板块的 min_band 恒为 0.05。
    # 这就是「过度掩码」的来源，下面量化它与库里真值的偏差。
    con.execute("""
        create table dband2 as
        select *,
               0.05 as min_band,
               case when board in ('STAR','ChiNext') then 0.20
                    when board = 'BSE' then 0.30
                    else 0.10 end as board_band,
               case when up_limit/prev_close - 1 < 0.075 then 0.05
                    when up_limit/prev_close - 1 < 0.15  then 0.10
                    when up_limit/prev_close - 1 < 0.25  then 0.20
                    else 0.30 end as implied_band
        from dband
    """)
    b = con.execute("""
        select
          count(*) as joined_rows,
          sum(case when close >= up_limit then 1 else 0 end) as db_hit_up,
          sum(case when close <= down_limit then 1 else 0 end) as db_hit_down,
          sum(case when close >= prev_close*(1+min_band) - 0.005 then 1 else 0 end) as w503_hit_up,
          sum(case when close <= prev_close*(1-min_band) + 0.005 then 1 else 0 end) as w503_hit_down,
          sum(case when close >= prev_close*(1+min_band) - 0.005
                    and close < up_limit then 1 else 0 end) as over_mask_up,
          sum(case when close <= prev_close*(1-min_band) + 0.005
                    and close > down_limit then 1 else 0 end) as over_mask_down,
          sum(case when close >= up_limit
                    and close < prev_close*(1+min_band) - 0.005 then 1 else 0 end) as miss_mask_up,
          sum(case when close <= down_limit
                    and close > prev_close*(1-min_band) + 0.005 then 1 else 0 end) as miss_mask_down,
          sum(case when implied_band = board_band
                    or implied_band = 0.05 then 1 else 0 end) as band_set_consistent
        from dband2
    """).fetchone()
    bkeys = ("joined_rows", "db_hit_up", "db_hit_down", "w503_hit_up", "w503_hit_down",
             "over_mask_up", "over_mask_down", "miss_mask_up", "miss_mask_down",
             "band_set_consistent")
    B = dict(zip(bkeys, b))

    # 库里真实涨跌停幅度分布（implied band = up_limit/prev_close - 1）
    band_dist = con.execute("""
        select implied_band, count(*) as n from dband2
        group by 1 order by 2 desc
    """).fetchall()
    band_by_board = con.execute("""
        select board, implied_band, count(*) as n,
               sum(case when implied_band = board_band or implied_band = 0.05
                        then 1 else 0 end) as in_work503_band_set
        from dband2 group by 1,2 order by 1,3 desc
    """).fetchall()

    # 额外口径诊断（三项，都是 WORK-503 静态规则的已知弱点，必须量化后披露）
    extra = con.execute("""
        select
          sum(case when abs(pre_close - prev_close) > 0.005 then 1 else 0 end) as ex_rights_rows,
          sum(case when num like '92%' then 1 else 0 end)                  as bse_920_rows,
          sum(case when num like '92%' and implied_band = 0.30 then 1 else 0 end) as bse_920_at_30,
          sum(case when implied_band >= 0.25 and num not like '92%' then 1 else 0 end) as no_limit_anomaly_rows,
          count(*)                                                        as total_rows
        from dband2
    """).fetchone()
    ekeys = ("ex_rights_rows", "bse_920_rows", "bse_920_at_30",
             "no_limit_anomaly_rows", "total_rows")
    E = dict(zip(ekeys, extra))

    out = {
        "note": ("WORK-557 段1 收尾交叉核对。数据源：data/WORK-557/cache/raw_local（Z:\\quantdb\\raw 的"
                 "只读本地副本）。成员日 = PIT 成分 × 交易日，窗口 2009-01-01~2024-12-31。"),
        "window": [WINDOW[0], WINDOW[1]],
        "check_A_suspend": {
            "columns": ["member_days", "no_bar", "in_suspend_d", "no_bar_and_susp",
                        "no_bar_not_susp", "bar_and_susp", "bar_vol_zero"],
            "values": {k: int(v) for k, v in A.items()},
            "real_gap_rate": (A["no_bar_not_susp"] / A["member_days"]) if A["member_days"] else None,
            "suspended_rate": (A["no_bar_and_susp"] / A["no_bar"]) if A["no_bar"] else None,
            "suspend_d_all_rows_in_window": int(s_all),
            "suspend_d_distinct_codes": int(s_codes),
            "by_year": [
                {"year": int(y), "member_days": int(md), "no_bar": int(nb),
                 "suspended": int(su), "real_gap": int(rg),
                 "real_gap_rate": (rg / md) if md else None}
                for y, md, nb, su, rg in yearly],
        },
        "check_B_limit": {
            "columns": list(bkeys),
            "values": {k: int(v) for k, v in B.items()},
            "implied_band_dist": [{"band": str(r[0]), "n": int(r[1])} for r in band_dist],
            "implied_band_by_board": [{"board": r[0], "band": str(r[1]), "n": int(r[2]),
                                       "in_work503_band_set": int(r[3])}
                                      for r in band_by_board],
            "band_set_consistent_rate": (B["band_set_consistent"] / B["joined_rows"]
                                         if B["joined_rows"] else None),
            "w503_over_mask_ratio_up": (B["w503_hit_up"] / B["db_hit_up"]
                                        if B["db_hit_up"] else None),
            "w503_over_mask_ratio_down": (B["w503_hit_down"] / B["db_hit_down"]
                                          if B["db_hit_down"] else None),
            "known_weaknesses": {k: int(v) for k, v in E.items()},
        },
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    # ---- 打印 ----
    print("\n=== 核对 A｜停牌 ===", flush=True)
    print("  member_days      = %d" % A["member_days"])
    print("  daily 无行        = %d" % A["no_bar"])
    print("  suspend_d 有行    = %d" % A["in_suspend_d"])
    print("  无行 ∧ 停牌       = %d" % A["no_bar_and_susp"])
    print("  无行 ∧ 非停牌     = %d   <- 真·数据缺口" % A["no_bar_not_susp"])
    print("  有行 ∧ suspend_d  = %d" % A["bar_and_susp"])
    print("  有行 ∧ vol=0      = %d" % A["bar_vol_zero"])
    print("  真缺口率 = %.6f%%" % (100.0 * A["no_bar_not_susp"] / max(A["member_days"], 1)))
    print("  「无行」中被 suspend_d 解释的比例 = %.4f%%"
          % (100.0 * A["no_bar_and_susp"] / max(A["no_bar"], 1)))
    print("\n=== 核对 B｜涨跌停 ===", flush=True)
    print("  可比对行数 = %d" % B["joined_rows"])
    print("  库里真 limit_up  = %d ; 503 判 limit_up = %d" % (B["db_hit_up"], B["w503_hit_up"]))
    print("  库里真 limit_down= %d ; 503 判 limit_down= %d" % (B["db_hit_down"], B["w503_hit_down"]))
    print("  过掩 up = %d (%.4f%% of joined), 过掩 down = %d"
          % (B["over_mask_up"], 100.0 * B["over_mask_up"] / max(B["joined_rows"], 1),
             B["over_mask_down"]))
    print("  漏掩 up = %d, 漏掩 down = %d" % (B["miss_mask_up"], B["miss_mask_down"]))
    print("  库真幅度落在 WORK-503 该板块候选带内 = %d (%.4f%%)"
          % (B["band_set_consistent"],
             100.0 * B["band_set_consistent"] / max(B["joined_rows"], 1)))
    print("  库里 implied band 分布: %s"
          % ", ".join("%s=%d" % (r[0], r[1]) for r in band_dist))
    print("  分板块 implied band:")
    for board, band, n, ok in band_by_board:
        print("    %-10s %-6s n=%-8d 落在候选带内=%d (%.2f%%)"
              % (board, band, n, ok, 100.0 * ok / max(n, 1)))
    print("\n=== WORK-503 静态规则的已知弱点（量化） ===")
    print("  除权除息日 (|pre_close - 前一有行情日收盘| > 0.005) = %d / %d"
          % (E["ex_rights_rows"], E["total_rows"]))
    print("  920xxx (北交所新号段) 行 = %d，其中真实带 30%% 的 = %d"
          % (E["bse_920_rows"], E["bse_920_at_30"]))
    print("  非 920xxx 却出现 >=25%% 幅度（无涨跌幅限制日/数据退化） = %d"
          % E["no_limit_anomaly_rows"])
    print("  503/库真 倍数: limit_up = %.2fx, limit_down = %.2fx"
          % ((B["w503_hit_up"] / B["db_hit_up"]) if B["db_hit_up"] else 0,
             (B["w503_hit_down"] / B["db_hit_down"]) if B["db_hit_down"] else 0))
    print("\nwrote -> %s" % out_path)


if __name__ == "__main__":
    main()
