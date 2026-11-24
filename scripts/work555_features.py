# -*- coding: utf-8 -*-
"""
WORK-555 · 因子 + 标签 + 样本装配（金融数据工程师阶段，续）

严格照抄 experiments/WORK-555/preregistration.md v1.0 的 §1 / §2 / §3 与附录 A：
  - 停牌日保留行、价格前向填充到上一收盘、成交量/额置 0（字段字典 §5）
  - 退市股保留到最后交易日，其后不再补行（不回溯剔除）
  - PIT 成分 = 快照日 <= t 的最新快照
  - 上市满 60 个交易日过滤
  - 48 个自实现 Alpha158 风格算子（附录 A），MAD 5x 去极值 + 截面 z-score
  - 标签 = hfq 次日收益；t 或 t+1 任一停牌 -> 标签无效

输出：
  data/WORK-555/panel_backtest.csv  测试段可成交面板（回测引擎的输入事实层）
  experiments/WORK-555/sample.npz   建模输入矩阵（train/valid/test）
  experiments/WORK-555/sample_meta.json

运行：
  /tmp/w555venv/bin/python scripts/work555_features.py
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)
DB = "/srv/share/quantdb/db/quantdb.duckdb"

FEATURES = [
    "ROC_5", "ROC_10", "ROC_20", "ROC_60",
    "MA_DEV_10", "MA_DEV_20", "MA_DEV_60", "MA_SLOPE_5_20",
    "VOL_STD_10", "VOL_STD_20", "VOL_STD_60", "HL_RANGE_20", "ATR_14", "DOWN_SEMIVAR_20",
    "RANK_20", "QTLU_5", "QTLD_5", "MAXDIFF_5", "MINDIFF_5", "RSV_9", "IMXD_10", "IMAX_10",
    "RSI_6", "RSI_14", "KMID_20", "KMID_60",
    "CORR_20", "CORD_20", "RSQR_20", "RESI_20", "BETA_60", "VOL_RATIO_5_20",
    "VSTD_20", "WVMA_20", "VSUMP_5", "VSUMN_5", "VSUMD_5", "AMT_RATIO_5_20", "AMT_STD_20",
    "CTP_5", "CTN_5", "CTD_5", "SUMP_5", "SUMN_5", "SUMD_5",
    "AMT_MA_20_60", "VWAP_DEV_20", "RET_SKEW_60",
]
assert len(FEATURES) == 48, len(FEATURES)

LIST_MIN_DAYS = 60           # 预注册 §2：上市满 N=60 个交易日
MAX_LOOKBACK = 60            # 附录 A 最长回看 60 -> 预热索引 >= 60
TRAIN_START = "2008-01-01"
TRAIN_END = "2014-12-31"
VALID_END = "2016-12-31"
TEST_END = "2020-12-31"
BT_START = "2017-01-01"      # 回测事实层起始（测试段）

DDOF_NOTE = ("rolling std/cov use ddof=0 (population); preregistration is silent on ddof; "
             "IC filter sigma uses ddof=0 per prereg §4.4")


def _roll(s, w, how):
    return getattr(s.rolling(w, min_periods=w), how)()


def factors_one(g):
    """单只股票：48 个因子（预注册附录 A）。g 已按 trade_date 升序。"""
    c = g["close_hfq"].astype("float64")
    h = g["high_hfq"].astype("float64")
    l = g["low_hfq"].astype("float64")
    v = g["vol"].astype("float64")
    a = g["amount"].astype("float64")
    r = c / c.shift(1) - 1.0
    dc = c.diff()
    dv = v.pct_change()

    def mean(x, w):
        return _roll(x, w, "mean")

    def std(x, w):
        return x.rolling(w, min_periods=w).std(ddof=0)

    def rollsum(x, w):
        return _roll(x, w, "sum")

    def roll_cov(x, y, w):
        mx, my = mean(x, w), mean(y, w)
        return mean((x - mx) * (y - my), w)

    def roll_var(x, w):
        mx = mean(x, w)
        return mean((x - mx) ** 2, w)

    f = {}
    for w in (5, 10, 20, 60):
        f[f"ROC_{w}"] = c / c.shift(w) - 1.0
    for w in (10, 20, 60):
        f[f"MA_DEV_{w}"] = c / mean(c, w) - 1.0
    f["MA_SLOPE_5_20"] = mean(c, 5) / mean(c, 20) - 1.0
    for w in (10, 20, 60):
        f[f"VOL_STD_{w}"] = std(r, w)
    f["HL_RANGE_20"] = (_roll(h, 20, "max") - _roll(l, 20, "min")) / c
    f["ATR_14"] = mean((h - l) / c, 14)
    f["DOWN_SEMIVAR_20"] = std(r.clip(upper=0.0), 20)
    f["RANK_20"] = (c - _roll(c, 20, "min")) / (_roll(c, 20, "max") - _roll(c, 20, "min"))
    f["QTLU_5"] = (c - _roll(l, 5, "min")) / (_roll(h, 5, "max") - _roll(l, 5, "min"))
    f["QTLD_5"] = (_roll(h, 5, "max") - c) / (_roll(h, 5, "max") - _roll(l, 5, "min"))
    f["MAXDIFF_5"] = (_roll(h, 5, "max") - c) / c
    f["MINDIFF_5"] = (c - _roll(l, 5, "min")) / c
    f["RSV_9"] = (c - _roll(l, 9, "min")) / (_roll(h, 9, "max") - _roll(l, 9, "min"))
    f["IMXD_10"] = mean(h, 10) / mean(l, 10) - 1.0
    f["IMAX_10"] = mean(h, 10) / c - 1.0
    for w in (6, 14):
        f[f"RSI_{w}"] = rollsum(dc.clip(lower=0.0), w) / rollsum(dc.abs(), w)
    f["KMID_20"] = (c - c.shift(20)) / (c.shift(20) + 20.0 * mean(r, 20))
    f["KMID_60"] = (c - c.shift(60)) / (c.shift(60) + 60.0 * mean(r, 60))
    cov20 = roll_cov(c, v, 20)
    f["CORR_20"] = cov20 / np.sqrt(roll_var(c, 20) * roll_var(v, 20))
    f["CORD_20"] = roll_cov(r, dv, 20) / np.sqrt(roll_var(r, 20) * roll_var(dv, 20))
    f["RSQR_20"] = f["CORR_20"] ** 2
    beta20 = cov20 / roll_var(v, 20)
    alpha20 = mean(c, 20) - beta20 * mean(v, 20)
    f["RESI_20"] = (c - (alpha20 + beta20 * v)) / std(c, 20)
    f["BETA_60"] = roll_cov(c, v, 60) / roll_var(v, 60)
    f["VOL_RATIO_5_20"] = mean(v, 5) / mean(v, 20) - 1.0
    f["VSTD_20"] = std(v, 20) / mean(v, 20)
    f["WVMA_20"] = std(c * v, 20) / mean(c * v, 20)
    abs_vdc = (v * dc).abs()
    f["VSUMP_5"] = rollsum((v * dc).clip(lower=0.0), 5) / rollsum(abs_vdc, 5)
    f["VSUMN_5"] = rollsum(-(v * dc).clip(upper=0.0), 5) / rollsum(abs_vdc, 5)
    f["VSUMD_5"] = rollsum(v * (dc > 0).astype("float64"), 5) / rollsum(v, 5)
    f["AMT_RATIO_5_20"] = mean(a, 5) / mean(a, 20) - 1.0
    f["AMT_STD_20"] = std(a, 20) / mean(a, 20)
    f["CTP_5"] = rollsum((c > c.shift(1)).astype("float64"), 5) / 5.0
    f["CTN_5"] = rollsum((c < c.shift(1)).astype("float64"), 5) / 5.0
    f["CTD_5"] = rollsum((r > 0).astype("float64"), 5) / 5.0
    abs_dc = dc.abs()
    f["SUMP_5"] = rollsum(dc.clip(lower=0.0), 5) / rollsum(abs_dc, 5)
    f["SUMN_5"] = rollsum(-dc.clip(upper=0.0), 5) / rollsum(abs_dc, 5)
    f["SUMD_5"] = rollsum(dc, 5) / rollsum(abs_dc, 5)
    f["AMT_MA_20_60"] = mean(a, 20) / mean(a, 60) - 1.0
    f["VWAP_DEV_20"] = c / (mean(c * v, 20) / mean(v, 20)) - 1.0
    f["RET_SKEW_60"] = _roll(r, 60, "skew")

    out = pd.DataFrame(f, index=g.index).replace([np.inf, -np.inf], np.nan)
    # 预热边界：附录 A 最长回看 60 -> ROC_60 / KMID_60 需 c.shift(60)（索引 >= 60），
    # VOL_STD_60 需 r[1..60]（索引 >= 60）。故 warm 取索引 >= MAX_LOOKBACK（60），非 59。
    warm = np.arange(len(out)) >= MAX_LOOKBACK
    out["warmup_ok"] = warm
    return out


def build_panel():
    """读 5 个原始层 CSV，拼成逐股面板；停牌日按字段字典 §5 补行。"""
    d = pd.read_csv(
        os.path.join(DATA, "panel_daily.csv"),
        parse_dates=["trade_date"], dtype={"ts_code": "string"},
    )
    lim = pd.read_csv(
        os.path.join(DATA, "panel_stk_limit.csv"),
        parse_dates=["trade_date"], dtype={"ts_code": "string"},
    )
    sus = pd.read_csv(
        os.path.join(DATA, "panel_suspend.csv"),
        parse_dates=["trade_date"], dtype={"ts_code": "string"},
    )
    basic = pd.read_csv(
        os.path.join(DATA, "panel_stock_basic.csv"),
        parse_dates=["list_date"], dtype={"ts_code": "string"},
    )
    # 停牌判定：suspend_type='S' 停牌日（不可成交、标签无效）；'R' 复牌日正常交易
    sus_s = sus[sus["suspend_type"] == "S"][["ts_code", "trade_date"]].drop_duplicates()
    sus_s["is_suspended"] = True          # 非键标记列：left-merge 命中即停牌
    d = d.merge(basic[["ts_code", "list_date"]], on="ts_code", how="left")
    d = d.merge(sus_s, on=["ts_code", "trade_date"], how="left")
    d = d.merge(lim, on=["ts_code", "trade_date"], how="left")
    d["is_suspended"] = d["is_suspended"].fillna(False).astype(bool)
    d = d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return d, sus_s


def assemble_rows(d, sus_s):
    """逐股：把停牌日补进面板（价格 ffill、量额置 0），产出统一行表。"""
    parts = []
    sus_by_code = {k: v for k, v in sus_s.groupby("ts_code", sort=False)}
    for ts, g in d.groupby("ts_code", sort=True):
        g = g.sort_values("trade_date").reset_index(drop=True)
        extra = sus_by_code.get(ts)
        if extra is not None:
            have = set(g["trade_date"])
            need = extra.loc[~extra["trade_date"].isin(have), "trade_date"]
            if len(need):
                add = pd.DataFrame({
                    "ts_code": ts, "trade_date": need.values,
                    "list_date": g["list_date"].iloc[0],
                    "is_suspended": True,
                })
                g = pd.concat([g, add], ignore_index=True).sort_values("trade_date").reset_index(drop=True)
        # 补行后统一 ffill 价格、量额置 0（字段字典 §5）
        for col in ("open_hfq", "high_hfq", "low_hfq", "close_hfq", "close_raw"):
            g[col] = g[col].ffill()
        for col in ("vol", "amount", "pct_chg"):
            g[col] = g[col].fillna(0.0)
        g["has_trade"] = ~g["is_suspended"].astype(bool)
        parts.append(g)
    rows = pd.concat(parts, ignore_index=True)
    return rows


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    d, sus_s = build_panel()
    print(f"[panel] daily rows={len(d):,} stocks={d['ts_code'].nunique()}", flush=True)

    rows = assemble_rows(d, sus_s)
    del d
    print(f"[panel] 补行后 rows={len(rows):,} ({time.time()-t0:.0f}s)", flush=True)

    # ---- 交易日历（上市满 60 个交易日计数用）
    import duckdb
    con = duckdb.connect(DB, read_only=True)
    cal = con.execute(
        "SELECT DISTINCT cal_date FROM trade_cal WHERE is_open = 1 ORDER BY cal_date"
    ).df()["cal_date"].to_numpy(dtype="datetime64[ns]")
    con.close()

    # ---- 次日收益（hfq 复权口径 + raw 口径），t 或 t+1 任一停牌 -> 无效
    rows = rows.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = rows.groupby("ts_code", sort=False)
    ch = rows["close_hfq"]
    cr = rows["close_raw"]
    rows["fwd_ret_hfq"] = g["close_hfq"].shift(-1) / ch - 1.0
    rows["fwd_ret_raw"] = g["close_raw"].shift(-1) / cr - 1.0
    ht = g["has_trade"].shift(-1).fillna(False).astype(bool)
    rows["label"] = np.where(rows["has_trade"].to_numpy() & ht.to_numpy(),
                             rows["fwd_ret_hfq"].to_numpy(), np.nan)
    print(f"[label] label rows={int(rows['label'].notna().sum()):,}", flush=True)

    # ---- 上市满 60 个交易日
    ld = rows["list_date"].to_numpy(dtype="datetime64[ns]")
    dt = rows["trade_date"].to_numpy(dtype="datetime64[ns]")
    pos_list = np.searchsorted(cal, ld, side="left")
    pos_now = np.searchsorted(cal, dt, side="left")
    rows["listed_days"] = pos_now - pos_list + 1
    rows["listed_ok"] = rows["listed_days"] >= LIST_MIN_DAYS

    # ---- PIT 成分（快照日 <= t 的最新快照）
    # 注：merge 用右表的非键列 _m 标记命中。pandas 的 left merge 不会把左表键列置空，
    #     直接用左表 ts_code.notna() 判成员会把「全部行」当成成员（见 failure_log §1）。
    iw = pd.read_csv(
        os.path.join(DATA, "panel_index_weight.csv"),
        parse_dates=["trade_date"], dtype={"con_code": "string"},
    )
    iw = iw[iw["trade_date"] <= pd.Timestamp(TEST_END)]
    snap_dates = sorted(iw["trade_date"].unique())
    blocks = []
    for i, sd in enumerate(snap_dates):
        nxt = (snap_dates[i + 1] if i + 1 < len(snap_dates) else pd.Timestamp("2021-02-01"))
        mem = iw.loc[iw["trade_date"] == sd, "con_code"].dropna().unique()
        span = pd.date_range(sd, nxt - pd.Timedelta(days=1))
        blocks.append(pd.DataFrame({
            "trade_date": np.repeat(span.values, len(mem)),
            "con_code": np.tile(mem, len(span)),
            "_m": 1,
        }))
    members = pd.concat(blocks, ignore_index=True)
    members = members.merge(pd.DataFrame({"trade_date": cal}), on="trade_date", how="inner")
    rows = rows.merge(members.rename(columns={"con_code": "ts_code"}),
                      on=["ts_code", "trade_date"], how="left")
    rows["is_member"] = rows["_m"].notna()
    rows = rows.drop(columns=["_m"])
    mem_counts = rows.loc[rows["is_member"]].groupby("trade_date").size()
    print(f"[pit] member rows={int(rows['is_member'].sum()):,} | 每日成分数 "
          f"min={int(mem_counts.min())} med={int(mem_counts.median())} max={int(mem_counts.max())} "
          f"n_dates={len(mem_counts)}", flush=True)

    # ---- 逐股算因子
    feat_frames = []
    for ts, gg in rows.groupby("ts_code", sort=True):
        gg = gg.sort_values("trade_date").reset_index(drop=True)
        f = factors_one(gg)
        f["ts_code"] = ts
        feat_frames.append(f[FEATURES + ["warmup_ok"]])
    F = pd.concat(feat_frames, ignore_index=True)
    del feat_frames
    rows = pd.concat([rows.reset_index(drop=True), F.reset_index(drop=True)], axis=1)
    del F
    print(f"[factors] computed ({time.time()-t0:.0f}s)", flush=True)

    rows["feat_nan"] = rows[FEATURES].isna().any(axis=1)

    # ---- 样本：成员 ∩ 上市满 60 日 ∩ 因子有效 ∩ 标签有效 ∩ 区间内
    in_win = (rows["trade_date"] >= pd.Timestamp(TRAIN_START)) & (rows["trade_date"] <= pd.Timestamp(TEST_END))
    samp_mask = (rows["is_member"] & rows["listed_ok"] & rows["label"].notna()
                 & ~rows["feat_nan"] & rows["warmup_ok"] & in_win)
    samp = rows[samp_mask].copy()
    print(f"[sample] rows={len(samp):,}", flush=True)

    # ---- 截面 MAD 5x 去极值 + z-score
    # 池 = 当日 PIT 成分 ∩ 有行情 ∩ 非停牌 ∩ 上市满 60 日（预注册 §3.3）
    pool_mask = (rows["is_member"] & rows["has_trade"] & rows["listed_ok"]
                 & rows["warmup_ok"] & in_win)
    pool = rows[pool_mask]
    poolX = pool[FEATURES].to_numpy(dtype="float64")
    pool_dates = pool["trade_date"].to_numpy(dtype="datetime64[ns]")
    pool_uniq = np.unique(pool_dates)
    pool_didx = np.searchsorted(pool_uniq, pool_dates)
    poolX = _cs_norm(poolX, pool_didx, len(pool_uniq))

    key_pool = pd.MultiIndex.from_arrays([pool["ts_code"].astype(str).values, pool["trade_date"].values])
    key_samp = pd.MultiIndex.from_arrays([samp["ts_code"].astype(str).values, samp["trade_date"].values])
    # key_pool.get_indexer(key_samp)：对每个「样本行」取它在「池」里的行号
    # （注意方向：get_indexer 返回的是「调用方索引」中目标键的位置，不能反着写）
    idx = key_pool.get_indexer(key_samp)
    assert (idx >= 0).all(), (
        f"样本行未能在归一化池中定位：未命中 {int((idx < 0).sum())} / {len(idx)}")
    X = poolX[idx].astype("float32")
    y = samp["label"].to_numpy(dtype="float32")
    dates = samp["trade_date"].to_numpy(dtype="datetime64[ns]")
    uniq = np.unique(dates)
    date_idx = np.searchsorted(uniq, dates)
    cat = pd.Categorical(samp["ts_code"].astype(str))
    codes, codelist = cat.codes, cat.categories.to_numpy()
    split = np.where(dates <= np.datetime64(TRAIN_END), 0,
                     np.where(dates <= np.datetime64(VALID_END), 1, 2))

    # ---- 回测事实层（测试段）：与 sample 行用 bt_row_id 对齐
    bt = rows[(rows["trade_date"] >= pd.Timestamp(BT_START))
              & (rows["trade_date"] <= pd.Timestamp(TEST_END))].copy()
    bt = bt.reset_index(drop=True)
    bt["bt_row_id"] = np.arange(len(bt), dtype="int64")
    bt_key = pd.MultiIndex.from_arrays([bt["ts_code"].astype(str).values, bt["trade_date"].values])
    bt_row_id = bt_key.get_indexer(key_samp)
    # 回测事实层只覆盖测试段，训练/验证段样本行在 bt 中不存在 -> -1（不是错误）
    test_rows = split == 2
    assert (bt_row_id[test_rows] >= 0).all(), (
        f"测试段样本行未能在回测事实层中定位：未命中 {int((bt_row_id[test_rows] < 0).sum())}")
    assert (bt_row_id[~test_rows] == -1).all(), "训练/验证段样本行不应出现在回测事实层中"

    np.savez_compressed(
        os.path.join(OUT, "sample.npz"),
        X=X, y=y,
        date_idx=date_idx.astype("int32"), code_idx=codes.astype("int32"),
        split=split.astype("int8"), bt_row_id=bt_row_id.astype("int64"),
        feat_nan=samp["feat_nan"].to_numpy(dtype="bool"),
    )
    meta = {
        "n_rows": int(len(samp)), "n_dates": int(len(uniq)), "n_codes": int(len(codelist)),
        "features": FEATURES,
        "date_min": str(pd.Timestamp(uniq.min()).date()), "date_max": str(pd.Timestamp(uniq.max()).date()),
        "split_counts": {"train": int((split == 0).sum()), "valid": int((split == 1).sum()),
                         "test": int((split == 2).sum())},
        "dates": [str(pd.Timestamp(x).date()) for x in uniq],
        "codes": [str(x) for x in codelist],
        "ddof_note": DDOF_NOTE, "list_min_days": LIST_MIN_DAYS,
        "cs_pool_rows": int(len(pool)),
        "pit": {
            "snapshots_in_window": int(iw["trade_date"].nunique()),
            "snapshot_rows": int(len(iw)),
            "member_rows_total": int(rows["is_member"].sum()),
            "members_per_day_min": int(mem_counts.min()),
            "members_per_day_median": int(mem_counts.median()),
            "members_per_day_max": int(mem_counts.max()),
        },
        "backtest_frame_rows": int(len(bt)),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT, "sample_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    out = bt[["ts_code", "trade_date", "is_member", "listed_ok", "has_trade", "is_suspended",
              "close_hfq", "close_raw", "up_limit", "down_limit", "label",
              "fwd_ret_hfq", "fwd_ret_raw"]].copy()
    out.to_csv(os.path.join(DATA, "panel_backtest.csv"), index=False)
    print(f"[done] {meta['elapsed_sec']}s splits={meta['split_counts']} bt_rows={len(bt):,}")


def _cs_norm(X, date_idx, n_dates):
    """截面 MAD 5x 去极值 + z-score（逐日逐因子，预注册 §3.3）。

    逐因子 NaN 感知：某因子当日全为 NaN 时该列保持 NaN（预注册 §3.4 整行剔除）；
    个别股缺该因子时用当日该因子的非缺失子集算 median/MAD/std。
    """
    out = np.empty_like(X)
    with np.errstate(all="ignore"):
        for d in range(n_dates):
            m = date_idx == d
            sub = X[m]
            med = np.nanmedian(sub, axis=0)
            mad = np.nanmedian(np.abs(sub - med), axis=0)
            scale = 1.4826 * mad
            lo, hi = med - 5.0 * scale, med + 5.0 * scale
            bad = ~(scale > 0)
            if bad.any():
                with np.errstate(all="ignore"):
                    q_lo = np.nanquantile(sub[:, bad], 0.01, axis=0)
                    q_hi = np.nanquantile(sub[:, bad], 0.99, axis=0)
                lo[bad], hi[bad] = q_lo, q_hi
            sub = np.clip(sub, lo, hi)
            mu = np.nanmean(sub, axis=0)
            sd = np.nanstd(sub, axis=0)
            sd = np.where(sd > 0, sd, 1.0)
            out[m] = (sub - mu) / sd
    return out


if __name__ == "__main__":
    sys.exit(main())
