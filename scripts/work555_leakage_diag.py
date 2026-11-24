#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 机制诊断（无前视泄漏 / 排序驱动 vs 线性驱动）

报告 §7 的三段机制说法都必须能被一条命令复跑，本脚本就是那条命令。
不训练任何模型，只在已冻结的面板快照上做截面统计。

三项诊断
--------
D1 placebo（标签置乱）：
    把每个交易日的标签在截面内随机打乱（固定种子），重算 48 个因子的日频截面 RankIC。
    若面板没有前视泄漏，打乱后的 |RankIC| 应塌到接近 0。

D2 反向检验（已实现的过去收益）：
    用特征(t) 去预测**已经实现**的过去收益 (t−1→t)。这类相关是动量类因子的
    机械性质（因子本来就是滞后收益），量/波类因子则不应有同样的量级。
    目的：把「机械相关」与「泄漏」区分开。

D3 排序驱动 vs 线性驱动：
    同一批因子的日频 Spearman 与 Pearson IC 对比。若 Spearman 明显大于 Pearson，
    说明信号携带的是截面排序信息，而 MSE 目标只看得到线性部分。

输出：experiments/WORK-555/leakage_diag.json
运行：/tmp/w555venv/bin/python scripts/work555_leakage_diag.py
"""
import json
import os
import time

import numpy as np
import pandas as pd
from scipy.stats import rankdata

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)
DATA = os.path.join(REPO, "data", EXP)

SEED = 20261124

# 因子分组（按名字归类，写死在此，报告引用同一分组）
GROUPS = {
    "price_momentum": ["ROC_5", "ROC_10", "ROC_20", "ROC_60",
                       "MA_DEV_10", "MA_DEV_20", "MA_DEV_60", "MA_SLOPE_5_20",
                       "CTP_5", "CTN_5", "CTD_5", "SUMP_5", "SUMN_5", "SUMD_5",
                       "VWAP_DEV_20", "HL_RANGE_20", "MAXDIFF_5", "MINDIFF_5",
                       "RSV_9"],
    "volume_volatility": ["VOL_STD_10", "VOL_STD_20", "VOL_STD_60", "VSTD_20",
                          "WVMA_20", "VSUMP_5", "VSUMN_5", "VSUMD_5",
                          "AMT_STD_20", "AMT_RATIO_5_20", "AMT_MA_20_60",
                          "VOL_RATIO_5_20", "ATR_14", "DOWN_SEMIVAR_20",
                          "RET_SKEW_60"],
}
REPORTED = ["ROC_5", "MA_DEV_10"]


def daily_corr(Xd, yd, method="spearman"):
    """Xd: (n, k) 因子矩阵；yd: (n,) 标签。返回 (k,) 的逐因子截面相关。"""
    n = len(yd)
    if n < 10:
        return np.full(Xd.shape[1], np.nan)
    if method == "spearman":
        A = rankdata(Xd, axis=0)
        b = rankdata(yd)
    else:
        A = Xd.astype("float64")
        b = yd.astype("float64")
    A = A - A.mean(axis=0, keepdims=True)
    b = b - b.mean()
    sa = A.std(axis=0, ddof=0)
    sb = b.std(ddof=0)
    if sb <= 0:
        return np.full(Xd.shape[1], np.nan)
    num = (A * b[:, None]).mean(axis=0)
    den = sa * sb
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 0, num / den, np.nan)
    return out


def group_medians(values, names, groups):
    out = {}
    for gname, members in groups.items():
        idx = [i for i, nm in enumerate(names) if nm in members]
        if idx:
            out[gname] = float(np.nanmedian(np.abs(values[idx])))
    return out


def main():
    t0 = time.time()
    d = np.load(os.path.join(OUT, "sample.npz"))
    X, y, date_idx, split = d["X"], d["y"], d["date_idx"], d["split"]
    with open(os.path.join(OUT, "sample_meta.json"), encoding="utf-8") as fh:
        names = json.load(fh)["features"]

    te = np.where(split == 2)[0]
    days = np.unique(date_idx[te])
    remap = np.searchsorted(days, date_idx[te])
    Xte, yte, day_of = X[te], y[te].astype("float64"), remap
    order = np.argsort(day_of, kind="stable")
    bounds = np.searchsorted(day_of[order], np.arange(len(days) + 1))

    # ---------------- D1 placebo ----------------
    rng = np.random.default_rng(SEED)
    real_s, real_p, plac_s, plac_p = [], [], [], []
    for di in range(len(days)):
        a, b = bounds[di], bounds[di + 1]
        idx = order[a:b]
        Xd, yd = Xte[idx], yte[idx]
        rs = daily_corr(Xd, yd, "spearman")
        rp = daily_corr(Xd, yd, "pearson")
        ysh = rng.permutation(yd)          # 标签在截面内打乱
        ps = daily_corr(Xd, ysh, "spearman")
        pp = daily_corr(Xd, ysh, "pearson")
        real_s.append(rs); real_p.append(rp); plac_s.append(ps); plac_p.append(pp)
    real_s = np.vstack(real_s); real_p = np.vstack(real_p)
    plac_s = np.vstack(plac_s); plac_p = np.vstack(plac_p)

    real_ric = np.nanmean(real_s, axis=0)
    plac_ric = np.nanmean(plac_s, axis=0)

    # ---------------- D2 反向检验 ----------------
    bt = pd.read_csv(os.path.join(DATA, "panel_backtest.csv"),
                     parse_dates=["trade_date"], dtype={"ts_code": "string"},
                     usecols=["ts_code", "trade_date", "close_hfq"])
    bt = bt.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    bt["prev_ret"] = bt.groupby("ts_code", sort=False)["close_hfq"].pct_change()
    bt["trade_date"] = bt["trade_date"].astype("int64")
    prev_by_row = bt["prev_ret"].to_numpy(dtype="float64")

    bt_row_id = d["bt_row_id"][te]
    prev_ret = prev_by_row[bt_row_id]
    ok = np.isfinite(prev_ret)
    back_s = np.full((len(days), len(names)), np.nan)
    for di in range(len(days)):
        a, b = bounds[di], bounds[di + 1]
        idx = order[a:b]
        m = ok[idx]
        if m.sum() < 10:
            continue
        back_s[di] = daily_corr(Xte[idx][m], prev_ret[idx][m], "spearman")
    back_ric = np.nanmean(back_s, axis=0)

    # ---------------- D3 排序 vs 线性 ----------------
    rank_vs_pearson = {}
    for nm in REPORTED:
        j = names.index(nm)
        rank_vs_pearson[nm] = {
            "mean_spearman_ic": float(np.nanmean(real_s[:, j])),
            "mean_pearson_ic": float(np.nanmean(real_p[:, j])),
        }

    res = {
        "seed": SEED,
        "segment": "test (2017-01-03~2020-12-31)",
        "n_rows": int(len(te)), "n_days": int(len(days)), "n_features": len(names),
        "D1_placebo": {
            "real_mean_abs_rankic_by_factor_min": float(np.nanmin(np.abs(real_ric))),
            "real_mean_abs_rankic_by_factor_max": float(np.nanmax(np.abs(real_ric))),
            "real_mean_abs_rankic_median": float(np.nanmedian(np.abs(real_ric))),
            "placebo_mean_abs_rankic_median": float(np.nanmedian(np.abs(plac_ric))),
            "placebo_mean_abs_rankic_max": float(np.nanmax(np.abs(plac_ric))),
            "placebo_median_pearson_ic_median": float(np.nanmedian(np.abs(np.nanmean(plac_p, axis=0)))),
            "note": "标签在截面内打乱后重算；打乱后 |RankIC| 应塌到接近 0",
        },
        "D2_backward_realized_return": {
            "median_abs_rankic_by_group": group_medians(back_ric, names, GROUPS),
            "real_forward_median_abs_rankic_by_group": group_medians(real_ric, names, GROUPS),
            "note": ("特征(t) 对已实现的过去收益(t-1->t)；价/动量类因子的高相关是"
                     "「因子本来就是滞后收益」的机械性质，不是泄漏"),
        },
        "D3_rank_vs_linear": rank_vs_pearson,
        "per_factor_mean_abs_rankic_forward": {
            names[j]: float(abs(real_ric[j])) for j in range(len(names))},
        "per_factor_mean_abs_rankic_backward": {
            names[j]: float(abs(back_ric[j])) for j in range(len(names))},
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT, "leakage_diag.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: res[k] for k in
                      ("D1_placebo", "D2_backward_realized_return", "D3_rank_vs_linear")},
                     ensure_ascii=False, indent=2))
    print(f"[leakage_diag] elapsed={res['elapsed_sec']}s -> "
          f"{os.path.join(OUT, 'leakage_diag.json')}")


if __name__ == "__main__":
    main()
