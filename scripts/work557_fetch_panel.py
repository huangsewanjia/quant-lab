#!/usr/bin/env python3
"""WORK-557 段2a · 适配层：从 `Z:\\quantdb` 造出 WORK-503 下游脚本期望的数据布局。

**为什么要有这一层**
WORK-503 的下游脚本（build_panel / dump_qlib / build_dataset / run_arms / backtest）
吃的是一套自建快照格式：每票一个 CSV（不复权 + 后复权）+ PIT 名单 CSV + 指数 CSV
+ 每股 fundamentals CSV。本单换数据源（quantdb 底座代替新浪/baostock），但**不换靶**：
下游脚本一行不改，只换本脚本产出的输入。

**产物（data/WORK-557/ 与 experiments/WORK-557/）**
    data/WORK-557/sina/<code>.csv         不复权日线（涨跌停判定、组合收益用）
    data/WORK-557/sina_hfq/<code>.csv     后复权日线（特征与组合收益用）
    data/WORK-557/fundamentals/<code>.csv 每股净资产（value_bp 先验）
    data/WORK-557/index_sh000300.csv      沪深300 指数日线（同时是交易日历）
    experiments/WORK-557/pool_membership.csv  PIT CSI300 名单（effective_date,code,name）
    experiments/WORK-557/snapshot_manifest.json  来源、行数、区间、sha256

**口径（逐条对齐 WORK-503 预注册）**
* 复权：WORK-503 §3「输入数据 = 后复权价（open/high/low/close）+ volume + vwap」，
  §1.1 D4「后复权用于特征与组合收益，不复权用于涨跌停判定」。后复权价**逐字复刻**
  quantdb 视图 `v_daily_hfq` 的定义（见 Z:\\quantdb\\code\\quant-lab\\db\\schema.sql）：
      hfq = close * adj_factor / MIN(adj_factor) OVER (PARTITION BY ts_code)
  （复权视图不落盘、只存 close + adj_factor —— quantdb README §2.2）
* 单位换算（quantdb README §6 规矩 4/5 → WORK-503 的新浪口径）：
      vol 手 -> 股 ×100 ；amount 千元 -> 元 ×1000 ；float_share 万股 -> 股 ×10000
  换算后 `amount / volume` 才是「每股价格（元）」，dump_qlib 的 vwap 依赖这一点。
* PIT 成分：`index_weight`（指数 × 成分 × **月**）的每个快照日 = 一个生效名单，
  向后沿用至下一快照；**禁止**用今天的名单回填历史。
* 每股净资产：`fina_indicator.bps`，生效日取 **ann_date**（公告日即公开日，PIT 安全；
  quantdb README §6 规矩 2「财务按 ann_date」）。WORK-503 用新浪快照、只能按
  「报告期 + 120 天」近似；本单改用真公告日，属**口径升级**（只会更早、且只用
  已公开信息），在报告 §偏离表单列。

**只读**：只读 `Z:\\quantdb\\raw\\**\\*.parquet`，不写 Z: 盘、不碰 .duckdb。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time

import duckdb
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# 本地快照副本（由 experiments/WORK-557/work557_copy_raw.py 从 Z:\quantdb\raw
# 并行拷来）。**为什么不直接 glob 网络盘**：Samba 单文件延迟 ~250–300 ms，
# raw/daily 有 5000+ 个日分区文件，duckdb 直接扫 Z: 会卡死（实测读计数 60 s 增量为 0）；
# 并行拷贝 5075 个文件只要 33 s，之后本地跑 SQL 快一个数量级。源盘全程只读。
RAW_LOCAL = os.path.join(REPO, "data", "WORK-557", "cache", "raw_local")
RAW = RAW_LOCAL if os.path.isdir(os.path.join(RAW_LOCAL, "daily")) \
    else "Z:/quantdb/raw"
WIN_START, WIN_END = 20060101, 20241231
INDEX = "000300.SH"

CACHE = "cache"          # 本地 parquet 中间层
CSV_COLS = ["date", "open", "high", "low", "close", "volume", "amount",
            "outstanding_share", "turnover"]
FUND_COLS = ["effective_date", "bvps"]
IDX_COLS = ["date", "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg"]


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_bs_code(ts_code: str) -> str:
    """600519.SH -> sh.600519 ; 000001.SZ -> sz.000001 ; 430047.BJ -> bj.430047"""
    num, mkt = ts_code.split(".")
    return "%s.%s" % (mkt.lower(), num)


def sql_list(values):
    return ", ".join("'%s'" % v for v in values)


def build(con, cache_dir: str, codes: list[str]) -> dict:
    """拉取全部需要的表，落到本地 parquet 中间层（只读 Z:，写本地）。"""
    t0 = time.time()
    files = {}

    def copy(name: str, sql: str):
        out = os.path.join(cache_dir, name + ".parquet")
        if os.path.exists(out):
            print("  [%s] cached" % name, flush=True)
            files[name] = out
            return
        con.execute("copy (%s) to '%s' (format parquet, compression zstd)"
                    % (sql, out.replace("\\", "/")))
        print("  [%s] built in %.0fs" % (name, time.time() - t0), flush=True)
        files[name] = out

    cl = sql_list(codes)

    # ---- 1a. 复权因子（整表扫一次；adj_min 取该股全历史最小值，
    #          与 quantdb 视图 v_daily_hfq 的定义一致，故不按日期截断） ----
    copy("adj_factor", """
        SELECT ts_code, trade_date, adj_factor,
               MIN(adj_factor) OVER (PARTITION BY ts_code) AS adj_min
        FROM read_parquet('%s/adj_factor/trade_date=*/part.parquet')
        WHERE ts_code IN (%s)
    """ % (RAW, cl))

    # ---- 1b. 日线（不复权） ------------------------------------------------
    copy("daily", """
        SELECT ts_code, trade_date, open, high, low, close, pre_close,
               pct_chg, vol, amount
        FROM read_parquet('%s/daily/trade_date=*/part.parquet')
        WHERE ts_code IN (%s) AND trade_date BETWEEN %d AND %d
    """ % (RAW, cl, WIN_START, WIN_END))

    # ---- 2. 流通股本（size 先验） ----------------------------------------
    copy("shares", """
        SELECT ts_code, trade_date, float_share, circ_mv, total_mv, turnover_rate
        FROM read_parquet('%s/daily_basic/trade_date=*/part.parquet')
        WHERE ts_code IN (%s) AND trade_date BETWEEN %d AND %d
    """ % (RAW, cl, WIN_START, WIN_END))

    # ---- 3. 沪深300 指数日线（兼交易日历 + mkt_20d 先验 + 基准） ---------
    copy("index", """
        SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg,
               vol, amount
        FROM read_parquet('%s/index_daily/trade_date=*/part.parquet')
        WHERE ts_code = '%s' AND trade_date BETWEEN %d AND %d
    """ % (RAW, INDEX, WIN_START, WIN_END))

    # ---- 4. 每股净资产（value_bp 先验），生效日 = 公告日 ------------------
    copy("fund", """
        SELECT ts_code, ann_date, end_date, bps
        FROM read_parquet('%s/fina_indicator/ts_code=*/part.parquet',
             hive_partitioning=1, union_by_name=True)
        WHERE ts_code IN (%s) AND bps IS NOT NULL
    """ % (RAW, cl))

    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-557")
    ap.add_argument("--limit-codes", type=int, default=0)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    cache_dir = os.path.join(data_dir, CACHE)
    for d in (exp_dir, data_dir, cache_dir,
              os.path.join(data_dir, "sina"), os.path.join(data_dir, "sina_hfq"),
              os.path.join(data_dir, "fundamentals")):
        os.makedirs(d, exist_ok=True)

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute("SET memory_limit='6GB'")

    # ---- PIT 成分快照 -> pool_membership.csv ------------------------------
    mem_path = os.path.join(exp_dir, "pool_membership.csv")
    iw = con.execute("""
        SELECT strftime(trade_date, '%%Y%%m%%d')::BIGINT AS d, con_code
        FROM read_parquet('%s/index_weight/index_code=%s/part.parquet')
        WHERE trade_date BETWEEN DATE '%s-01-01' AND DATE '%s-12-31'
        ORDER BY trade_date, con_code
    """ % (RAW, INDEX, str(WIN_START)[:4], str(WIN_END)[:4])).df()
    codes = sorted(iw.con_code.unique())
    if args.limit_codes:
        codes = codes[:args.limit_codes]
    names = {}
    sb = con.execute("""
        SELECT ts_code, name FROM read_parquet('%s/stock_basic/partition=*/part.parquet',
             hive_partitioning=1)
    """ % RAW).df()
    for r in sb.itertuples():
        names[r.ts_code] = r.name

    with open(mem_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["effective_date", "code", "name"])
        for r in iw.itertuples():
            if r.con_code not in codes:
                continue
            w.writerow([int(r.d), to_bs_code(r.con_code), names.get(r.con_code, "")])
    print("pool_membership.csv: %d snapshot rows, %d codes" % (len(iw), len(codes)),
          flush=True)

    # ---- 拉数 ------------------------------------------------------------
    files = build(con, cache_dir, codes)

    # ---- 写每票 CSV ------------------------------------------------------
    daily = pd.read_parquet(files["daily"])
    adjf = pd.read_parquet(files["adj_factor"])
    shares = pd.read_parquet(files["shares"])
    fund = pd.read_parquet(files["fund"])
    idx = pd.read_parquet(files["index"])

    # 后复权价（逐字复刻 quantdb 视图 v_daily_hfq：
    #   hfq = ROUND(px * adj_factor / MIN(adj_factor) OVER (PARTITION BY ts_code), 4)）
    daily = daily.merge(adjf, on=["ts_code", "trade_date"], how="left")
    ratio = (daily["adj_factor"] / daily["adj_min"]).astype("float64")
    for col in ("open", "high", "low", "close"):
        daily[col + "_hfq"] = (daily[col] * ratio).round(4)
    daily["factor"] = ratio
    print("joined bars: %d rows, %d codes" % (len(daily), daily.ts_code.nunique()),
          flush=True)

    sh_map = {c: g.set_index("trade_date") for c, g in shares.groupby("ts_code")}
    fu_map = {c: g.sort_values("ann_date") for c, g in fund.groupby("ts_code")}

    items = []
    for code, g in daily.groupby("ts_code"):
        bs = to_bs_code(code)
        g = g.sort_values("trade_date").copy()
        sh = sh_map.get(code)
        if sh is not None:
            g = g.merge(sh.reset_index()[["trade_date", "float_share", "turnover_rate"]],
                        on="trade_date", how="left")
        else:
            g["float_share"] = pd.NA
            g["turnover_rate"] = pd.NA

        # 单位换算：vol 手->股，amount 千元->元，float_share 万股->股
        out = pd.DataFrame({
            "date": g.trade_date.astype("string"),
            "open": g["open"], "high": g["high"], "low": g["low"], "close": g["close"],
            "volume": g["vol"] * 100.0,
            "amount": g["amount"] * 1000.0,
            "outstanding_share": g["float_share"] * 10000.0,
            "turnover": g["turnover_rate"] / 100.0,
        })
        out_hfq = pd.DataFrame({
            "date": g.trade_date.astype("string"),
            "open": g["open_hfq"], "high": g["high_hfq"], "low": g["low_hfq"],
            "close": g["close_hfq"],
            "volume": g["vol"] * 100.0,
            "amount": g["amount"] * 1000.0,
            "outstanding_share": g["float_share"] * 10000.0,
            "turnover": g["turnover_rate"] / 100.0,
        })
        p_raw = os.path.join(data_dir, "sina", "%s.csv" % bs)
        p_hfq = os.path.join(data_dir, "sina_hfq", "%s.csv" % bs)
        out.to_csv(p_raw, index=False)
        out_hfq.to_csv(p_hfq, index=False)

        fu = fu_map.get(code)
        if fu is not None and len(fu):
            fdf = pd.DataFrame({
                "effective_date": fu.ann_date.astype("string"),
                "bvps": fu.bps,
            }).dropna(subset=["bvps"])
            fdf.to_csv(os.path.join(data_dir, "fundamentals", "%s.csv" % bs),
                       index=False)
        else:
            fdf = pd.DataFrame(columns=FUND_COLS)

        items.append({
            "code": bs, "ts_code": code, "rows": int(len(out)),
            "date_min": str(int(g.trade_date.min())), "date_max": str(int(g.trade_date.max())),
            "sha256_raw": sha256_of(p_raw), "sha256_hfq": sha256_of(p_hfq),
            "bvps_rows": int(len(fdf)),
            "first_adj_factor_ref": None,
        })
        if len(items) % 100 == 0:
            print("  wrote %d/%d" % (len(items), len(codes)), flush=True)

    # ---- 指数 CSV --------------------------------------------------------
    idx = idx.sort_values("trade_date")
    idx_out = pd.DataFrame({
        "date": idx.trade_date.astype("string"),
        "open": idx["open"], "high": idx["high"], "low": idx["low"],
        "close": idx["close"], "preclose": idx["pre_close"],
        "volume": idx["vol"], "amount": idx["amount"], "pct_chg": idx["pct_chg"],
    })
    p_idx = os.path.join(data_dir, "index_sh000300.csv")
    idx_out.to_csv(p_idx, index=False)

    # ---- manifest --------------------------------------------------------
    manifest = {
        "experiment": args.exp,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "window": {"start": str(WIN_START), "end": str(WIN_END)},
        "source": {
            "root": RAW,
            "access": "read-only over SMB, raw parquet layer only "
                      "(the .duckdb is NOT opened over SMB per quantdb README §6.6)",
            "daily": "raw/daily/trade_date=*/part.parquet",
            "adj_factor": "raw/adj_factor/trade_date=*/part.parquet",
            "daily_basic": "raw/daily_basic/trade_date=*/part.parquet",
            "index_daily": "raw/index_daily/trade_date=*/part.parquet",
            "index_weight": "raw/index_weight/index_code=000300.SH/part.parquet",
            "fina_indicator": "raw/fina_indicator/ts_code=*/part.parquet",
            "stk_limit": "raw/stk_limit/trade_date=*/part.parquet",
            "suspend_d": "raw/suspend_d/trade_date=*/part.parquet",
        },
        "price_basis": {
            "features_and_portfolio": "后复权 (hfq) = close * adj_factor / "
                                      "MIN(adj_factor) OVER (PARTITION BY ts_code) "
                                      "— 与 quantdb 视图 v_daily_hfq 逐字一致",
            "limit_up_down_mask": "不复权 (raw)",
            "note": "WORK-503 预注册 §3 / §1.1 D4 同口径",
        },
        "units": {
            "volume": "股 (源 vol 为手，×100)",
            "amount": "元 (源 amount 为千元，×1000)",
            "outstanding_share": "股 (源 float_share 为万股，×10000)",
            "turnover": "小数 (源 turnover_rate 为 %，÷100)",
        },
        "pool": {"index": INDEX,
                 "source": "index_weight 月度快照，按生效日向前沿用（PIT）",
                 "snapshot_rows": int(len(iw)), "unique_codes": len(codes),
                 "membership_csv": "experiments/%s/pool_membership.csv" % args.exp},
        "fundamentals": {"source": "fina_indicator.bps",
                         "effective_date": "ann_date（公告日即公开日，PIT 安全）",
                         "differs_from": "WORK-503 用新浪快照，只能按『报告期+120 天』近似"},
        "index_csv": {"path": "data/%s/index_sh000300.csv" % args.exp,
                      "rows": int(len(idx_out)),
                      "sha256": sha256_of(p_idx)},
        "items": sorted(items, key=lambda x: x["code"]),
    }
    mpath = os.path.join(exp_dir, "snapshot_manifest.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print("manifest -> %s  (items=%d)" % (mpath, len(items)))
    print("index rows=%d  index_sha256=%s" % (len(idx_out), manifest["index_csv"]["sha256"]))


if __name__ == "__main__":
    sys.exit(main())
