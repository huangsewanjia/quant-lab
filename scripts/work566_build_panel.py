# -*- coding: utf-8 -*-
"""WORK-566 · 面板构建：quantdb parquet → 稠密面板 → 40 维描述符 + 标签 + 掩码。

产物（落在 data/WORK-566/，不入库；sha256 写进 experiments/WORK-566/snapshot_manifest.json）：
  base/*.npy      (n_stocks, n_dates) float32 —— 原始价、复权价、成交量额、涨跌停、估值、基本掩码
  ret/*.npy       (n_stocks, n_dates) float32 —— r1/r5/r21/r63/r126
  feat.npy        (n_stocks, n_dates, 40) float32 —— 冻结的 40 维描述符
  targets.npy     (n_stocks, n_dates, 9)  float32 —— y21, R5,V5,M5,E5,R21,V21,M21,E21
  meta.json       股票池 / 交易日历 / 形状 / 口径参数

口径与 experiments/WORK-566/feature_contract.md 一一对应：
  * 后复权 hfq：logC = log(close_raw) + log(adj / adj_first)；close_hfq = close_raw × adj/adj_first
  * 停牌：保留行、价格前向填充到上一收盘、vol=0、amount=0；has_bar 单独记录（连续性断言用）
  * 描述符只含 t 当日及之前的信息；未来窗只用于标签与 S1 目标

运行：
  python scripts/work566_build_panel.py
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

RAW = "/srv/share/quantdb/raw"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")

WINDOW_START = 20130101
WINDOW_END = 20260626

FEAT_NAMES = [
    "r_1", "r_5", "r_21", "r_63", "r_126", "mom_21_5", "mom_63_21", "skew_21",            # G1 (8)
    "vol_21", "vol_63", "vol_126", "dvol_21",                                           # G2 (4)
    "pos_21", "pos_63", "dd_hi_63", "dev_ma21", "pe_ttm", "pb",                          # G3 (6)
    "turnover_rate", "turnover_rate_f", "log_amount", "log_circ_mv", "amihud_21",
    "amt_ratio_21",                                                                     # G4 (6)
    "exc_1", "exc_21", "exc_63", "exc_126",                                             # G5 (4)
    "ew_r_1", "ew_r_5", "ew_r_21", "ew_r_63", "ew_vol_21", "ew_vol_63",
    "disp_21", "disp_63", "breadth_up", "limit_up_ratio", "mkt_turnover", "ew_mom_21_5",  # G6 (12)
]
N_FEAT = len(FEAT_NAMES)
assert N_FEAT == 40
TARGET_NAMES = ["y21", "R5", "V5", "M5", "E5", "R21", "V21", "M21", "E21"]
G5_IDX = [FEAT_NAMES.index(x) for x in ("exc_1", "exc_21", "exc_63", "exc_126")]
G6_IDX = [FEAT_NAMES.index(x) for x in ("ew_r_1", "ew_r_5", "ew_r_21", "ew_r_63", "ew_vol_21",
                                       "ew_vol_63", "disp_21", "disp_63", "breadth_up",
                                       "limit_up_ratio", "mkt_turnover", "ew_mom_21_5")]
RET_IDX = [FEAT_NAMES.index(x) for x in ("r_1", "r_5", "r_21", "r_63", "r_126")]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ff(a):
    """沿 axis=1 前向填充（每行独立）。NaN 保持前导 NaN。"""
    m = np.isnan(a)
    idx = np.where(~m, np.arange(a.shape[1])[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    out = a[np.arange(a.shape[0])[:, None], idx]
    out[m & (idx == 0)] = np.nan
    return out


def roll(a, w, fn):
    """沿 axis=1 的滚动窗口函数（min/max/mean/std）。min_periods=w，NaN 不计入但需足够有效值。"""
    import pandas as pd
    df = pd.DataFrame(a)
    r = getattr(df.rolling(w, axis=1, min_periods=w), fn)()
    return r.to_numpy(dtype=np.float32)


def roll_stats(a, w):
    """同时返回 rolling mean / std(ddof=1) / min / max，沿 axis=1。"""
    import pandas as pd
    df = pd.DataFrame(a)
    r = df.rolling(w, axis=1, min_periods=w)
    return (r.mean().to_numpy(dtype=np.float32), r.std(ddof=1).to_numpy(dtype=np.float32),
            r.min().to_numpy(dtype=np.float32), r.max().to_numpy(dtype=np.float32))


def roll_skew(a, w):
    import pandas as pd
    df = pd.DataFrame(a)
    return df.rolling(w, axis=1, min_periods=w).skew().to_numpy(dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=str(WINDOW_START))
    ap.add_argument("--end", default=str(WINDOW_END))
    ap.add_argument("--chunk", type=int, default=400)
    ap.add_argument("--skip-extract", action="store_true")
    args = ap.parse_args()
    lo, hi = int(args.start), int(args.end)
    base_dir = os.path.join(DATA, "base")
    ret_dir = os.path.join(DATA, "ret")
    os.makedirs(base_dir, exist_ok=True)
    os.makedirs(ret_dir, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    t_all = time.time()

    import duckdb
    import pandas as pd

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")

    def P(name):
        return (f"read_parquet('{RAW}/{name}/**/*.parquet', "
                f"hive_partitioning=true, union_by_name=true)")

    # ---------------------------------------------------------------- 1. 日历 / 股票池
    def dstr(v):
        """YYYYMMDD -> 'YYYY-MM-DD'（trade_cal.cal_date 是 DATE 类型）"""
        v = int(v)
        return f"{v // 10000:04d}-{v // 100 % 100:02d}-{v % 100:02d}"

    dates = np.unique(con.execute(
        f"select distinct cast(strftime(cal_date, '%Y%m%d') as int) as cd from {P('trade_cal')} where exchange='SSE' and is_open=1 "
        f"and cal_date between date '{dstr(lo)}' and date '{dstr(hi)}' order by cd").df()["cd"].astype(int).to_numpy())
    n_dates = len(dates)
    stocks = con.execute(
        f"select distinct ts_code from {P('daily')} where trade_date between {lo} and {hi} "
        f"order by ts_code").df()["ts_code"].to_numpy()
    n_stocks = len(stocks)
    log(f"交易日历 {n_dates} 天 {dates[0]}~{dates[-1]}；股票池 {n_stocks} 只")
    dmap = np.full(int(dates[-1]) + 1, -1, dtype=np.int32)
    dmap[dates] = np.arange(n_dates, dtype=np.int32)
    shape = (n_stocks, n_dates)

    def mk(path, dtype=np.float32, fill=np.nan):
        m = np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=shape)
        if fill is not None:
            m[...] = fill
        return m

    def lm(path):
        return np.load(path, mmap_mode="r")

    # ---------------------------------------------------------------- 2. 抽取
    if not args.skip_extract:
        mats = {n: mk(os.path.join(base_dir, f"{n}.npy")) for n in
                ("open_raw", "high_raw", "low_raw", "close_raw", "vol", "amount",
                 "up_limit", "down_limit", "turnover_rate", "turnover_rate_f",
                 "circ_mv", "pe_ttm", "pb", "adj")}
        mats["has_bar"] = mk(os.path.join(base_dir, "has_bar.npy"), dtype=bool, fill=False)

        def fill(df, cols, mark_bar=False):
            df = df.copy()
            df["trade_date"] = df["trade_date"].astype(int)
            di = dmap[df["trade_date"].to_numpy()]
            keep = di >= 0
            df = df[keep]
            di = di[keep]
            codes = pd.Categorical(df["ts_code"].to_numpy(), categories=stocks).codes
            for col, name in cols:
                if col in df.columns:
                    mats[name][codes, di] = df[col].to_numpy(dtype=np.float32)
            if mark_bar:
                mats["has_bar"][codes, di] = True

        for yr in range(lo // 10000, hi // 10000 + 1):
            y0, y1 = max(yr * 10000 + 101, lo), min(yr * 10000 + 1231, hi)
            if y1 < lo or y0 > hi:
                continue
            df = con.execute(
                f"select ts_code, trade_date, open, high, low, close, vol, amount from {P('daily')} "
                f"where trade_date between {y0} and {y1}").df()
            fill(df, [("open", "open_raw"), ("high", "high_raw"), ("low", "low_raw"),
                      ("close", "close_raw"), ("vol", "vol"), ("amount", "amount")],
                 mark_bar=True)
            log(f"  daily {yr}: {len(df):,} 行")

        for yr in range(lo // 10000, hi // 10000 + 1):
            y0, y1 = max(yr * 10000 + 101, lo), min(yr * 10000 + 1231, hi)
            if y1 < lo or y0 > hi:
                continue
            df = con.execute(f"select ts_code, trade_date, adj_factor from {P('adj_factor')} "
                             f"where trade_date between {y0} and {y1}").df()
            fill(df, [("adj_factor", "adj")])
            del df
            df = con.execute(f"select ts_code, trade_date, turnover_rate, turnover_rate_f, circ_mv, "
                             f"pe_ttm, pb from {P('daily_basic')} where trade_date between {y0} and {y1}").df()
            fill(df, [("turnover_rate", "turnover_rate"), ("turnover_rate_f", "turnover_rate_f"),
                      ("circ_mv", "circ_mv"), ("pe_ttm", "pe_ttm"), ("pb", "pb")])
            del df
            df = con.execute(f"select ts_code, trade_date, up_limit, down_limit from {P('stk_limit')} "
                             f"where trade_date between {y0} and {y1}").df()
            fill(df, [("up_limit", "up_limit"), ("down_limit", "down_limit")])
            del df

        for m in mats.values():
            m.flush()
        log("基础矩阵抽取完成")

    # ---------------------------------------------------------------- 3. hfq / logC / 收益
    log("计算 hfq 与收益序列 …")
    logC = mk(os.path.join(base_dir, "logC.npy"))
    close_hfq = mk(os.path.join(base_dir, "close_hfq.npy"))
    open_hfq = mk(os.path.join(base_dir, "open_hfq.npy"))
    rets = {f"r{h}": mk(os.path.join(ret_dir, f"r{h}.npy")) for h in (1, 5, 21, 63, 126)}
    b = {n: lm(os.path.join(base_dir, f"{n}.npy")) for n in
         ("open_raw", "high_raw", "low_raw", "close_raw", "vol", "amount", "adj")}
    hb = lm(os.path.join(base_dir, "has_bar.npy"))

    CH = args.chunk
    for s0 in range(0, n_stocks, CH):
        s1 = min(s0 + CH, n_stocks)
        sl = slice(s0, s1)
        adj = np.asarray(b["adj"][sl], dtype=np.float64)
        adj = ff(adj)
        first = np.where(np.isnan(adj), np.nan, adj)
        # 每行首个有效 adj 作为归一化锚
        valid = ~np.isnan(adj)
        any_valid = valid.any(axis=1)
        idx_first = np.argmax(valid, axis=1)
        anchor = adj[np.arange(len(adj)), idx_first]
        anchor[~any_valid] = 1.0
        ratio = adj / anchor[:, None]
        ratio[~valid] = np.nan
        close_r = np.asarray(b["close_raw"][sl], dtype=np.float64)
        open_r = np.asarray(b["open_raw"][sl], dtype=np.float64)
        c_hfq = close_r * ratio
        o_hfq = open_r * ratio
        lc = np.log(np.where(close_r > 0, close_r, np.nan))
        with np.errstate(divide="ignore", invalid="ignore"):
            logc = lc + np.log(ratio)
        logC[sl] = logc.astype(np.float32)
        close_hfq[sl] = c_hfq.astype(np.float32)
        open_hfq[sl] = o_hfq.astype(np.float32)
        for h, name in ((1, "r_1"), (5, "r_5"), (21, "r_21"), (63, "r_63"), (126, "r_126")):
            r = np.full_like(logc, np.nan)
            r[:, h:] = logc[:, h:] - logc[:, :-h]
            rets[name][sl] = r.astype(np.float32)
        if (s0 // CH) % 5 == 0:
            log(f"  hfq {s1}/{n_stocks}")
    for m in [logC, close_hfq, open_hfq] + list(rets.values()):
        m.flush()
    log("hfq / 收益完成")

    # ---------------------------------------------------------------- 4. 40 维描述符
    log("计算 40 维描述符 …")
    feat = np.lib.format.open_memmap(os.path.join(DATA, "feat.npy"), mode="w+",
                                     dtype=np.float32, shape=(n_stocks, n_dates, N_FEAT))
    feat[...] = np.nan
    R = {k: lm(os.path.join(ret_dir, f"{k}.npy")) for k in rets}
    CH = args.chunk
    for s0 in range(0, n_stocks, CH):
        s1 = min(s0 + CH, n_stocks)
        sl = slice(s0, s1)
        lc = np.asarray(logC[sl], dtype=np.float64)
        clo = np.asarray(close_hfq[sl], dtype=np.float64)
        opn = np.asarray(open_hfq[sl], dtype=np.float64)
        vol = np.asarray(b["vol"][sl], dtype=np.float64)
        amt = np.asarray(b["amount"][sl], dtype=np.float64)
        tor = np.asarray(b["turnover_rate"][sl], dtype=np.float64)
        torf = np.asarray(b["turnover_rate_f"][sl], dtype=np.float64)
        cmv = np.asarray(b["circ_mv"][sl], dtype=np.float64)
        pe = np.asarray(b["pe_ttm"][sl], dtype=np.float64)
        pb = np.asarray(b["pb"][sl], dtype=np.float64)
        # hfq 高低价：与 close 同一复权比例（ratio 的对数已在 logC 中）
        close_raw_c = np.asarray(b["close_raw"][sl], dtype=np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio_hl = np.where(close_raw_c > 0,
                                np.asarray(b["high_raw"][sl], dtype=np.float64)
                                / np.where(close_raw_c > 0, close_raw_c, np.nan), np.nan)
            ratio_hl2 = np.where(close_raw_c > 0,
                                 np.asarray(b["low_raw"][sl], dtype=np.float64)
                                 / np.where(close_raw_c > 0, close_raw_c, np.nan), np.nan)
        high_h = np.exp(lc + np.log(ratio_hl))
        low_h = np.exp(lc + np.log(ratio_hl2))

        r1 = np.asarray(R["r_1"][sl], dtype=np.float64)
        f = {}
        f["r_1"] = r1
        f["r_5"] = np.asarray(R["r_5"][sl], dtype=np.float64)
        f["r_21"] = np.asarray(R["r_21"][sl], dtype=np.float64)
        f["r_63"] = np.asarray(R["r_63"][sl], dtype=np.float64)
        f["r_126"] = np.asarray(R["r_126"][sl], dtype=np.float64)
        f["mom_21_5"] = f["r_21"] - f["r_5"]
        f["mom_63_21"] = f["r_63"] - f["r_21"]
        f["skew_21"] = roll_skew(r1, 21)
        f["vol_21"] = roll(r1, 21, "std")
        f["vol_63"] = roll(r1, 63, "std")
        f["vol_126"] = roll(r1, 126, "std")
        f["dvol_21"] = np.sqrt(roll(np.minimum(r1, 0.0) ** 2, 21, "mean"))
        lo21 = roll(low_h, 21, "min")
        hi21 = roll(high_h, 21, "max")
        rng = hi21 - lo21
        f["pos_21"] = np.where(rng > 0, (clo - lo21) / np.where(rng > 0, rng, 1.0), 0.5)
        lo63 = roll(low_h, 63, "min")
        hi63 = roll(high_h, 63, "max")
        rng63 = hi63 - lo63
        f["pos_63"] = np.where(rng63 > 0, (clo - lo63) / np.where(rng63 > 0, rng63, 1.0), 0.5)
        hi63c = roll(clo, 63, "max")
        f["dd_hi_63"] = clo / np.where(hi63c > 0, hi63c, np.nan) - 1.0
        ma21 = roll(clo, 21, "mean")
        f["dev_ma21"] = clo / np.where(ma21 > 0, ma21, np.nan) - 1.0
        f["pe_ttm"] = pe
        f["pb"] = pb
        f["turnover_rate"] = tor
        f["turnover_rate_f"] = torf
        f["log_amount"] = np.log1p(np.maximum(amt, 0.0))
        f["log_circ_mv"] = np.log(np.where(cmv > 0, cmv, np.nan))
        illiq = np.where(amt > 0, np.abs(r1) / amt, np.nan)
        f["amihud_21"] = roll(illiq, 21, "mean") * 1e9
        a5 = roll(amt, 5, "mean")
        a21 = roll(amt, 21, "mean")
        f["amt_ratio_21"] = np.where(a21 > 0, a5 / np.where(a21 > 0, a21, 1.0), 0.0)
        for name, idx in (("r_1", FEAT_NAMES.index("r_1")), ("r_5", FEAT_NAMES.index("r_5")),
                          ("r_21", FEAT_NAMES.index("r_21")), ("r_63", FEAT_NAMES.index("r_63")),
                          ("r_126", FEAT_NAMES.index("r_126")), ("mom_21_5", FEAT_NAMES.index("mom_21_5")),
                          ("mom_63_21", FEAT_NAMES.index("mom_63_21")),
                          ("skew_21", FEAT_NAMES.index("skew_21")),
                          ("vol_21", FEAT_NAMES.index("vol_21")), ("vol_63", FEAT_NAMES.index("vol_63")),
                          ("vol_126", FEAT_NAMES.index("vol_126")), ("dvol_21", FEAT_NAMES.index("dvol_21")),
                          ("pos_21", FEAT_NAMES.index("pos_21")), ("pos_63", FEAT_NAMES.index("pos_63")),
                          ("dd_hi_63", FEAT_NAMES.index("dd_hi_63")),
                          ("dev_ma21", FEAT_NAMES.index("dev_ma21")), ("pe_ttm", FEAT_NAMES.index("pe_ttm")),
                          ("pb", FEAT_NAMES.index("pb")), ("turnover_rate", FEAT_NAMES.index("turnover_rate")),
                          ("turnover_rate_f", FEAT_NAMES.index("turnover_rate_f")),
                          ("log_amount", FEAT_NAMES.index("log_amount")),
                          ("log_circ_mv", FEAT_NAMES.index("log_circ_mv")),
                          ("amihud_21", FEAT_NAMES.index("amihud_21")),
                          ("amt_ratio_21", FEAT_NAMES.index("amt_ratio_21"))):
            feat[sl, :, idx] = np.asarray(f[name], dtype=np.float32)
        if (s0 // CH) % 3 == 0:
            log(f"  feat {s1}/{n_stocks}")
    feat.flush()
    log("资产级 28 维完成")

    # ---------------------------------------------------------------- 5. 市场级 + 市场相对
    log("计算市场级与市场相对维度 …")
    has = np.asarray(hb)
    r1_all = np.asarray(R["r_1"], dtype=np.float32)
    r5_all = np.asarray(R["r_5"], dtype=np.float32)
    r21_all = np.asarray(R["r_21"], dtype=np.float32)
    r63_all = np.asarray(R["r_63"], dtype=np.float32)
    r126_all = np.asarray(R["r_126"], dtype=np.float32)
    mask1 = has & np.isfinite(r1_all)
    ew1 = np.where(mask1, r1_all, np.nan)
    with np.errstate(invalid="ignore"):
        ew_daily = np.nanmean(ew1, axis=0)                       # (n_dates,) 等权日对数收益
        ew_daily = np.nan_to_num(ew_daily, nan=0.0)
    cum = np.zeros(n_dates + 1)
    cum[1:] = np.cumsum(ew_daily)

    def ew_h(h):
        out = np.full(n_dates, np.nan)
        out[h - 1:] = cum[h:] - cum[:-h]
        return out

    ew_r1 = ew_daily
    ew_r5, ew_r21, ew_r63 = ew_h(5), ew_h(21), ew_h(63)
    ew_vol21 = np.full(n_dates, np.nan)
    ew_vol63 = np.full(n_dates, np.nan)
    import pandas as pd
    s21 = pd.Series(ew_daily).rolling(21, min_periods=21).std(ddof=1).to_numpy()
    s63 = pd.Series(ew_daily).rolling(63, min_periods=63).std(ddof=1).to_numpy()
    ew_vol21, ew_vol63 = s21.astype(np.float32), s63.astype(np.float32)
    with np.errstate(invalid="ignore"):
        disp21 = np.nanstd(np.where(mask1, r21_all, np.nan), axis=0).astype(np.float32)
        disp63 = np.nanstd(np.where(mask1, r63_all, np.nan), axis=0).astype(np.float32)
    breadth = np.nanmean((np.where(mask1, r1_all, np.nan) > 0).astype(np.float32), axis=0)
    up = np.asarray(lm(os.path.join(base_dir, "up_limit.npy")), dtype=np.float32)
    close_raw = np.asarray(lm(os.path.join(base_dir, "close_raw.npy")), dtype=np.float32)
    at_limit = has & np.isfinite(up) & (close_raw >= up * (1 - 1e-6))
    limit_ratio = at_limit.sum(axis=0) / np.maximum(has.sum(axis=0), 1)
    amt_all = np.asarray(lm(os.path.join(base_dir, "amount.npy")), dtype=np.float32)
    cmv_all = np.asarray(lm(os.path.join(base_dir, "circ_mv.npy")), dtype=np.float32)
    amt_sum = np.nansum(np.where(has, amt_all, np.nan), axis=0)
    cmv_sum = np.nansum(np.where(has, cmv_all, np.nan), axis=0)
    mkt_turnover = np.where(cmv_sum > 0, amt_sum / np.where(cmv_sum > 0, cmv_sum, 1.0), np.nan).astype(np.float32)
    ew_mom = ew_r21 - ew_r5

    for t in range(n_dates):
        col = feat[:, t, :]
        for k, (name, val) in enumerate({
                "ew_r_1": ew_r1[t], "ew_r_5": ew_r5[t], "ew_r_21": ew_r21[t], "ew_r_63": ew_r63[t],
                "ew_vol_21": ew_vol21[t], "ew_vol_63": ew_vol63[t], "disp_21": disp21[t],
                "disp_63": disp63[t], "breadth_up": breadth[t], "limit_up_ratio": limit_ratio[t],
                "mkt_turnover": mkt_turnover[t], "ew_mom_21_5": ew_mom[t]}.items()):
            col[:, FEAT_NAMES.index(name)] = val
    # G5 市场相对（逐股）
    for name, rmat, ewm in (("exc_1", r1_all, ew_r1), ("exc_21", r21_all, ew_r21),
                            ("exc_63", r63_all, ew_r63), ("exc_126", r126_all, ew_h(126))):
        idx = FEAT_NAMES.index(name)
        feat[:, :, idx] = (rmat - ewm[None, :]).astype(np.float32)
    feat.flush()
    log("40 维完成")

    # ---------------------------------------------------------------- 6. 标签与 S1 目标
    log("计算标签与 S1 目标 …")
    tgt = np.lib.format.open_memmap(os.path.join(DATA, "targets.npy"), mode="w+",
                                    dtype=np.float32, shape=(n_stocks, n_dates, len(TARGET_NAMES)))
    tgt[...] = np.nan
    for s0 in range(0, n_stocks, CH):
        s1 = min(s0 + CH, n_stocks)
        sl = slice(s0, s1)
        lc = np.asarray(logC[sl], dtype=np.float64)
        out = np.full((s1 - s0, n_dates, len(TARGET_NAMES)), np.nan, dtype=np.float32)
        for h in (5, 21):
            # R^(h) = sum_{k=1..h} l_{t+k}
            fut = np.full_like(lc, np.nan)
            fut[:, :n_dates - h] = lc[:, h:] - lc[:, :-h]
            # V^(h) = sqrt(1/(h-1) * sum (l_{t+k} - R/h)^2)
            fut_daily = np.full_like(lc, np.nan)
            fut_daily[:, :n_dates - h] = lc[:, 1:n_dates - h + 1] - lc[:, :n_dates - h]
            vv = np.full_like(lc, np.nan)
            vv[:, :n_dates - h] = np.sqrt(np.nansum((fut_daily[:, :n_dates - h]
                                                    - fut[:, :n_dates - h] / h) ** 2, axis=1) / (h - 1))
            # M^(h) = min_{0<=k<=h} (logC_{t+k} - max_{0<=j<=k} logC_{t+j})  （前视窗口内的最大回撤）
            rev = lc[:, ::-1]
            mdd = np.full_like(lc, np.nan)
            best = np.inf
            for k in range(h + 1):
                # 前缀最大值（在 rev 上做 rolling max，窗口 k+1）
                pm = pd.DataFrame(rev).rolling(k + 1, axis=1, min_periods=k + 1).max().to_numpy()[:, ::-1]
                cand = lc - pm
                mdd = cand if k == 0 else np.fmin(mdd, cand)
            out[:, :, TARGET_NAMES.index(f"R{h}")] = fut.astype(np.float32)
            out[:, :, TARGET_NAMES.index(f"V{h}")] = vv.astype(np.float32)
            out[:, :, TARGET_NAMES.index(f"M{h}")] = mdd.astype(np.float32)
        # E^(h) = R^(h) - sum_{k=1..h} l^EW_{t+k}
        for h in (5, 21):
            fut = np.full_like(lc, np.nan)
            fut[:, :n_dates - h] = lc[:, h:] - lc[:, :-h]
            ew_fwd = np.full(n_dates, np.nan)
            ew_fwd[:n_dates - h] = np.array([cum[t + h] - cum[t] for t in range(n_dates - h)])
            out[:, :, TARGET_NAMES.index(f"E{h}")] = (fut - ew_fwd[None, :]).astype(np.float32)
        out[:, :, TARGET_NAMES.index("y21")] = out[:, :, TARGET_NAMES.index("R21")]
        tgt[sl] = out
        if (s0 // CH) % 3 == 0:
            log(f"  targets {s1}/{n_stocks}")
    tgt.flush()
    log("标签完成")

    # ---------------------------------------------------------------- 7. meta
    files = {}
    for d, sub in ((base_dir, "base"), (ret_dir, "ret"), (DATA, "")):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".npy"):
                p = os.path.join(d, fn)
                files[f"{sub}/{fn}" if sub else fn] = {"sha256": sha256(p),
                                                      "bytes": os.path.getsize(p)}
    meta = {
        "experiment": "WORK-566",
        "window": {"start": int(dates[0]), "end": int(dates[-1]), "n_dates": int(n_dates)},
        "universe": {"n_stocks": int(n_stocks), "first": str(stocks[0]), "last": str(stocks[-1]),
                     "rule": "quantdb.daily 中窗口内有过任意 bar 的全部 ts_code（含退市）"},
        "shape": {"stocks": int(n_stocks), "dates": int(n_dates)},
        "feat_names": FEAT_NAMES,
        "target_names": TARGET_NAMES,
        "adjust": "后复权 hfq：logC = log(close_raw) + log(adj/adj_first)",
        "suspended": "保留行、价格前向填充到上一收盘、vol=0、amount=0；has_bar 单独记录",
        "files": files,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": round(time.time() - t_all, 1),
    }
    with open(os.path.join(DATA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log(f"完成，用时 {time.time()-t_all:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
