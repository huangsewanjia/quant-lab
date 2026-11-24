#!/usr/bin/env python3
"""WORK-557 段1：数据自检 —— Z:\\quantdb 只读取数 + 2009–2019 CSI300 PIT 逐年 active 数 + 缺失率。

口径说明
--------
* 数据源：本机 `Z:\\quantdb`（Samba 共享 `\\\\192.168.9.104\\share` → 服务器 `/srv/share/quantdb`）。
  **只读**：只走 `raw/` 层的 Parquet，用 duckdb 的 `read_parquet` 直读；
  **不**通过 SMB 打开 `db/quantdb.duckdb`（quantdb README §6 规矩 6：SMB 直开 .duckdb 锁与性能都不可靠）。
* PIT 成分：`raw/index_weight/index_code=000300.SH/part.parquet`（指数 × 成分 × 月，权重）。
  每个交易日取「最近一个已生效快照」的成分 = PIT 名单，向后沿用至下一快照。
* 行情：`raw/daily/trade_date=*/part.parquet`；复权因子 `raw/adj_factor`；
  涨跌停 `raw/stk_limit`；停复牌 `raw/suspend_d`；估值 `raw/daily_basic`。
* 单位（quantdb README §6 规矩）：`vol`=手、`amount`=千元、`total_mv`/`circ_mv`=万元。

输出：experiments/WORK-557/data_probe.json（机器可读）+ 终端表。
"""
from __future__ import annotations

import json
import os
import sys
import time

import duckdb
import pandas as pd

RAW = "Z:/quantdb/raw"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_probe.json")

WIN_START, WIN_END = 20090101, 20241231

# 分区表的 trade_date 在 parquet 里就是 BIGINT（YYYYMMDD）；
# index_weight / trade_cal 是未分区的，日期列是 DATE —— 分别处理。
D = "trade_date::BIGINT"
DIW = "strftime(trade_date, '%Y%m%d')::BIGINT"
DC = "strftime(cal_date, '%Y%m%d')::BIGINT"


def q(con, sql, label):
    t0 = time.time()
    df = con.execute(sql).df()
    print("  [%s] %d rows in %.1fs" % (label, len(df), time.time() - t0), flush=True)
    return df


def dump_partial(probe: dict, path: str) -> None:
    """Write what we have so far, so a later crash does not lose the evidence."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(probe, fh, ensure_ascii=False, indent=1)


def main():
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("SET memory_limit='4GB'")

    probe = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "source_root": RAW,
             "access_mode": "read-only via raw parquet (SMB .duckdb not opened)"}

    # ---- 0. 目录可读性 ----------------------------------------------------
    print("=== 0. Z:\\quantdb 只读确认 ===", flush=True)
    dsk = con.execute(
        "select count(*) n, min(%s) dmin, max(%s) dmax "
        "from read_parquet('%s/daily/trade_date=*/part.parquet', hive_partitioning=1, union_by_name=True) "
        "where trade_date between 20060101 and 20261231" % (D, D, RAW)).df()
    probe["daily_parquet"] = {"rows_in_partitions": int(dsk.n.iloc[0]),
                              "trade_date_min": int(dsk.dmin.iloc[0]),
                              "trade_date_max": int(dsk.dmax.iloc[0])}
    print("  daily parquet rows=%s  %s..%s" % (dsk.n.iloc[0], dsk.dmin.iloc[0],
                                               dsk.dmax.iloc[0]), flush=True)

    # ---- 1. CSI300 PIT 成分快照 -------------------------------------------
    print("=== 1. CSI300 成分快照 ===", flush=True)
    iw = q(con, "select %s as trade_date, con_code, weight from "
               "read_parquet('%s/index_weight/index_code=000300.SH/part.parquet')"
           % (DIW, RAW), "index_weight")
    probe["index_weight_columns"] = list(iw.columns)
    snap = (iw.groupby("trade_date")["con_code"].nunique()
              .rename("n_members").reset_index().sort_values("trade_date"))
    probe["index_weight"] = {
        "rows": int(len(iw)),
        "snapshots": int(len(snap)),
        "snapshot_date_min": int(snap.trade_date.min()),
        "snapshot_date_max": int(snap.trade_date.max()),
        "members_per_snapshot_min": int(snap.n_members.min()),
        "members_per_snapshot_max": int(snap.n_members.max()),
        "members_per_snapshot_median": float(snap.n_members.median()),
    }
    print("  snapshots=%d  %s..%s  members/snapshot %d..%d (median %.0f)" % (
        len(snap), snap.trade_date.min(), snap.trade_date.max(),
        snap.n_members.min(), snap.n_members.max(), snap.n_members.median()),
        flush=True)

    snap["ym"] = snap.trade_date // 100
    ym = snap.groupby("ym").size()
    probe["index_weight"]["snapshots_per_month_min"] = int(ym.min())
    probe["index_weight"]["snapshots_per_month_max"] = int(ym.max())
    probe["index_weight"]["months_covered"] = int(len(ym))
    probe["index_weight"]["first_snapshots"] = [
        int(x) for x in snap.trade_date.head(5).tolist()]

    # ---- 2. 交易日历 -------------------------------------------------------
    print("=== 2. 交易日历 ===", flush=True)
    cal = q(con, """
        select distinct %s as cal_date
        from read_parquet('%s/trade_cal/partition=*/part.parquet', hive_partitioning=1, union_by_name=True)
        where is_open = 1
        order by cal_date
    """ % (DC, RAW), "trade_cal")
    cal = cal[cal["cal_date"].between(WIN_START, WIN_END)]
    probe["trade_calendar"] = {"open_days_in_window": int(len(cal)),
                                "min": int(cal.cal_date.min()),
                                "max": int(cal.cal_date.max())}
    print("  open days %d  %s..%s" % (len(cal), cal.cal_date.min(), cal.cal_date.max()),
          flush=True)

    # ---- 3. PIT 名单（每个交易日 = 最近一次已生效快照） ----------------------
    print("=== 3. PIT 名单展开 ===", flush=True)
    con.execute("create table snap as select trade_date as snap, con_code from iw")
    con.execute("create table cal as select cal_date from cal")
    pit = q(con, """
        select c.cal_date, s.con_code
        from cal c
        join snap s on s.snap = (
            select max(s2.snap) from snap s2 where s2.snap <= c.cal_date
        )
    """, "pit membership pairs")
    probe["pit"] = {"stock_day_pairs": int(len(pit)),
                    "unique_constituents": int(pit.con_code.nunique())}

    pit["year"] = pit.cal_date // 10000
    per_year = (pit.groupby(["year", "cal_date"]).size().rename("active")
                .reset_index())
    ystat = (per_year.groupby("year")["active"]
             .agg(["mean", "min", "max", "count"]).reset_index())
    ystat["distinct_members"] = (pit.groupby("year")["con_code"]
                                  .nunique().reindex(ystat.year).values)
    ystat["active_mean"] = ystat["mean"].round(1)
    ystat["active_min"] = ystat["min"].astype(int)
    ystat["active_max"] = ystat["max"].astype(int)
    ystat["trading_days"] = ystat["count"].astype(int)
    probe["pit_by_year"] = [
        {"year": int(r.year), "trading_days": int(r.trading_days),
         "active_mean": float(r.active_mean), "active_min": int(r.active_min),
         "active_max": int(r.active_max),
         "distinct_members": int(r.distinct_members)}
        for r in ystat.itertuples()]
    print(ystat[["year", "trading_days", "active_mean", "active_min",
                  "active_max", "distinct_members"]].to_string(index=False),
          flush=True)

    # ---- 4. 行情缺失率（member-day 口径） ----------------------------------
    print("=== 4. member-day 缺失率 ===", flush=True)
    con.execute("create table pitdays as "
                "select cal_date as trade_date, con_code as ts_code from pit")
    for label, glob in [
        ("daily", "%s/daily/trade_date=*/part.parquet"),
        ("adj_factor", "%s/adj_factor/trade_date=*/part.parquet"),
        ("stk_limit", "%s/stk_limit/trade_date=*/part.parquet"),
        ("daily_basic", "%s/daily_basic/trade_date=*/part.parquet"),
    ]:
        df = q(con, """
            with p as (select trade_date, ts_code from pitdays),
            d as (select %s as trade_date, ts_code
                  from read_parquet('%s', hive_partitioning=1, union_by_name=True)
                  where trade_date between %d and %d)
            select p.trade_date, p.ts_code
            from p left join d using (trade_date, ts_code)
            where d.ts_code is null
        """ % (D, glob % RAW, WIN_START, WIN_END), "%s missing pairs" % label)
        n_missing = int(len(df))
        by_year = (df.assign(year=df.trade_date // 10000)
                     .groupby("year").size().to_dict())
        probe.setdefault("missing_rate", {})[label] = {
            "member_days": int(len(pit)),
            "missing_pairs": n_missing,
            "missing_rate": round(n_missing / len(pit), 6),
            "missing_by_year": {str(int(k)): int(v) for k, v in sorted(by_year.items())},
        }
        dump_partial(probe, OUT)
        print("  %-12s missing=%d / %d  rate=%.4f%%  years=%s" % (
            label, n_missing, len(pit), 100 * n_missing / len(pit),
            sorted(by_year.keys())), flush=True)

    # ---- 5. 停复牌 ---------------------------------------------------------
    print("=== 5. 停复牌事件 ===", flush=True)
    sus = q(con, """
        select s.trade_date, s.ts_code, s.suspend_type, s.suspend_timing
        from read_parquet('%s/suspend_d/trade_date=*/part.parquet',
             hive_partitioning=1, union_by_name=True) s
        join pitdays p on p.trade_date = %s and p.ts_code = s.ts_code
        where s.trade_date between %d and %d
    """ % (RAW, D, WIN_START, WIN_END), "suspend rows in pool")
    sus["year"] = sus.trade_date // 10000
    byy = sus.groupby("year").size().to_dict()
    probe["suspend"] = {"rows_in_pool": int(len(sus)),
                        "by_year": {str(int(k)): int(v) for k, v in sorted(byy.items())},
                        "suspend_types": {str(k): int(v) for k, v in
                                          sus.suspend_type.value_counts().to_dict().items()}
                        if len(sus) else {}}
    dump_partial(probe, OUT)
    print("  suspend rows in pool: %d ; by_year=%s" % (len(sus), byy), flush=True)

    # ---- 6. 退市股体检（幸存者偏差） ---------------------------------------
    print("=== 6. 退市股体检 ===", flush=True)
    sb = q(con, """
        select list_status, count(*) n from
        read_parquet('%s/stock_basic/partition=*/part.parquet', hive_partitioning=1, union_by_name=True)
        group by list_status
    """ % RAW, "stock_basic status")
    probe["stock_basic_status"] = {str(r.list_status): int(r.n) for r in sb.itertuples()}
    delisted = con.execute(
        "select ts_code from read_parquet('%s/stock_basic/partition=*/part.parquet', "
        "hive_partitioning=1, union_by_name=True) where list_status='D'" % RAW).df().ts_code
    probe["delisted_stocks_seen_in_pool"] = int(pit.con_code.isin(set(delisted)).sum())
    print("  stock_basic status: %s" % probe["stock_basic_status"], flush=True)
    print("  delisted (D) member-days in PIT pool: %d"
          % probe["delisted_stocks_seen_in_pool"], flush=True)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(probe, fh, ensure_ascii=False, indent=1)
    print("\nwrote -> %s" % OUT)


if __name__ == "__main__":
    sys.exit(main())
