#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 臂 C 早停退化 · 非预注册诊断对照（xgbfull）

为什么有这个东西
----------------
预注册 §4.6 冻结的臂 C 超参含 `early_stopping_rounds=50`，实现也忠实照抄。
但在本面板上，验证 RMSE 曲线**近乎平坦**：
    round0  = 0.0318024472
    best    = 0.0317868318 @ round 87
    round50 = 0.0318024720
patience=50 在第 0 轮之后连续 50 轮没有「改善」→ 触发早停，best_iteration=0。
xgboost 的 sklearn `predict()` 在早停后**自动截断到 best_iteration+1 轮**
（实测 `predict(X)` 与 `booster.predict(DMatrix(X), iteration_range=(0,1))` 逐元素相等，
max|diff| = 0；与全树预测 max|diff| = 1.8e-2），
于是臂 C 实际退化成**单树桩**。

本脚本产出什么
--------------
`xgbfull` 臂：**关掉早停、训练满 2000 轮，用验证曲线选最佳轮次**（轮次只在
**验证段**上选，不看测试段），再在该轮次上预测测试段。
目的是给读者一个「臂 C 若不被早停退化打断能做到什么水平」的参照，
让「DEE vs XGBoost」不被早停病态扭曲。

边界（必须一起披露）
--------------------
1. 这是**非预注册诊断对照**，**不参与进/退判词**。主检验量 Δ_ic 只用 DEE vs MLP-only（臂 B）。
2. 臂 C（`xgb`）按预注册原样保留，不改行为；本脚本不覆盖 `preds_xgb_s*.npz`。
3. 选轮次只看验证段，测试段仅用于最终报数，不参与任何选择。

运行：
  /tmp/w555venv/bin/python scripts/work555_xgb_diag.py --seed 42
"""
import argparse
import json
import os
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)

XGB_PARAMS = dict(
    n_estimators=2000, learning_rate=0.05, max_depth=6, subsample=0.8,
    colsample_bylevel=0.8, colsample_bytree=0.8, reg_lambda=1,
    objective="reg:squarederror", tree_method="hist",
)


def mse_metric(y_true, y_pred):
    return float(np.mean((np.asarray(y_true, dtype=np.float64)
                          - np.asarray(y_pred, dtype=np.float64)) ** 2))


def rank_ic_by_day(pred, y, day_remap, n_days):
    out = np.full(n_days, np.nan)
    order = np.argsort(day_remap, kind="stable")
    ds, ps, ys = day_remap[order], pred[order], y[order]
    bounds = np.searchsorted(ds, np.arange(n_days + 1))
    for d in range(n_days):
        a, b = bounds[d], bounds[d + 1]
        if b - a < 3:
            continue
        p, t = ps[a:b], ys[a:b]
        pr = np.argsort(np.argsort(p)).astype(np.float64)
        tr = np.argsort(np.argsort(t)).astype(np.float64)
        sp, st = pr.std(), tr.std()
        if sp <= 0 or st <= 0:
            continue
        out[d] = float(((pr - pr.mean()) * (tr - tr.mean())).mean() / (sp * st))
    return out


def pearson_ic_by_day(pred, y, day_remap, n_days):
    out = np.full(n_days, np.nan)
    order = np.argsort(day_remap, kind="stable")
    ds, ps, ys = day_remap[order], pred[order], y[order]
    bounds = np.searchsorted(ds, np.arange(n_days + 1))
    for d in range(n_days):
        a, b = bounds[d], bounds[d + 1]
        if b - a < 3:
            continue
        p, t = ps[a:b], ys[a:b]
        sp, st = p.std(), t.std()
        if sp <= 0 or st <= 0:
            continue
        out[d] = float(((p - p.mean()) * (t - t.mean())).mean() / (sp * st))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    args = ap.parse_args()

    t0 = time.time()
    import xgboost as xgb_

    d = np.load(os.path.join(OUT, "sample.npz"))
    X, y, split = d["X"], d["y"], d["split"]
    date_idx, bt_row_id = d["date_idx"], d["bt_row_id"]
    tr = np.where(split == 0)[0]
    va = np.where(split == 1)[0]
    te = np.where(split == 2)[0]

    model = xgb_.XGBRegressor(random_state=args.seed, n_jobs=4,
                              eval_metric=mse_metric, **XGB_PARAMS)
    model.fit(X[tr], y[tr], eval_set=[(X[va], y[va])], verbose=False)
    booster = model.get_booster()
    er = model.evals_result
    er = er() if callable(er) else er
    curve = [(i, float(v)) for i, v in enumerate(er["validation_0"]["rmse"])]
    best_round = int(min(curve, key=lambda t: t[1])[0])

    dtest = xgb_.DMatrix(X[te])
    pred_best = booster.predict(dtest, iteration_range=(0, best_round + 1))
    pred_full = booster.predict(dtest)

    te_days = np.unique(date_idx[te])
    te_day_remap = np.searchsorted(te_days, date_idx[te])

    daily_best = rank_ic_by_day(pred_best, y[te], te_day_remap, len(te_days))
    daily_full = rank_ic_by_day(pred_full, y[te], te_day_remap, len(te_days))
    ic_best = pearson_ic_by_day(pred_best, y[te], te_day_remap, len(te_days))
    ic_full = pearson_ic_by_day(pred_full, y[te], te_day_remap, len(te_days))

    np.savez_compressed(os.path.join(OUT, f"preds_xgbfull_s{args.seed}.npz"),
                        pred=pred_best.astype(np.float32),
                        row=bt_row_id[te].astype(np.int64),
                        date_idx=te_day_remap.astype(np.int32))

    log = {
        "arm": "xgbfull",
        "status": "diagnostic_only_non_preregistered",
        "seed": args.seed,
        "purpose": ("诊断对照：臂 C 因 early_stopping_rounds=50 在近乎平坦的验证曲线上"
                    "退化为单树桩；本臂关早停、用验证曲线选轮次，给出未被早停病态扭曲的参照"),
        "not_used_for": "进/退判词与主检验量 Δ_ic（那只用 DEE vs MLP-only）",
        "selection_data": "仅验证段（验证曲线 argmin），测试段不参与任何选择",
        "params": dict(XGB_PARAMS),
        "eval_metric": "mse(自定义 callable)",
        "n_trees_fitted": int(booster.num_boosted_rounds()),
        "best_round_selected_on_valid": best_round,
        "valid_rmse_at_round0": curve[0][1],
        "valid_rmse_at_best": curve[best_round][1],
        "test_rankic_mean_best_iter": float(np.nanmean(daily_best)),
        "test_ic_mean_best_iter": float(np.nanmean(ic_best)),
        "test_rankic_mean_full_2000": float(np.nanmean(daily_full)),
        "test_ic_mean_full_2000": float(np.nanmean(ic_full)),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT, f"run_xgbfull_s{args.seed}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=2)
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
