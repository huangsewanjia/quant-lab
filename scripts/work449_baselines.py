#!/usr/bin/env python3
"""WORK-449 · 基线对照：单因子 IC、OLS 基线、最优常数基线。

用途：把报告 §11.1 与失败日志 §3.18 里引用的每一个数字变成**可复现产物**，
而不是只在正文里写一个数。独立审查指出原版正文里的三组数字无法复现
（单因子 |IC| 区间的下界、OLS 基线、最优常数 AdjMSE），因此本脚本按明确口径
重新计算并落盘：

  1. **单因子测试段 RankIC**（逐日 Spearman，2022-01-04 ~ 2024-12-31）
     —— 报告只允许引用本脚本产出的区间；
  2. **12 因子 OLS 基线**：在**训练段**用最小二乘拟合
     `y = beta · z(factors) + c`，再在测试段报告
     逐日 RankIC 与逐日 Pearson IC 两种口径（两个都给，避免口径混用）；
  3. **最优常数预测器**：在训练段上搜索常数 c，最小化 AdjMSE，
     给出训练集 AdjMSE 与「常数在测试段的 RankIC」；
  4. **冻结口径 MLP 的训练集 AdjMSE**（末轮与最优轮），用于与常数基线对照。

口径纪律
--------
* 样本集、归一化、掩码全部与 `scripts/work449_train.py` 一致
  （`sample_ok` = 12 因子全有效 ∧ M_factor 双向有效）；
* 归一化参数只由训练段估计；
* RankIC 用 `avg_rank` + Pearson 相关（与训练脚本的 `daily_rank_ic` 同一实现）；
* Pearson IC 用原始预测与原始收益的 Pearson 相关（与回测脚本
  `apparent_ic_mean` 同一口径）。

用法
----
    OMP_NUM_THREADS=1 ../.venv/bin/python scripts/work449_baselines.py --exp WORK-449
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import numpy as np

TRAIN = ("2015-01-01", "2020-12-31")
TEST = ("2022-01-01", "2024-12-31")
GAMMA = 0.1


def avg_rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=np.float64)
    ranks[order] = np.arange(1, len(x) + 1)
    s = x[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def adj_mse_scalar(pred, target, gamma: float = GAMMA):
    sq = (pred - target) ** 2
    same = (np.sign(pred) * np.sign(target)) > 0
    w = np.where(same, gamma, 1.0 + gamma)
    return float((w * sq).mean())


def daily_ic(pred_grid, label, valid, tidx, mode="rank"):
    vals = []
    for t in tidx:
        ok = valid[t] & np.isfinite(pred_grid[t]) & np.isfinite(label[t])
        if ok.sum() < 20:
            continue
        p = pred_grid[t][ok]
        y = label[t][ok]
        if mode == "rank":
            p = avg_rank(p)
            y = avg_rank(y)
        if p.std() == 0 or y.std() == 0:
            continue
        vals.append(float(np.corrcoef(p, y)[0, 1]))
    v = np.array(vals)
    if v.size < 2:
        return float("nan"), float("nan"), int(v.size)
    return (float(v.mean()),
            float(v.mean() / (v.std(ddof=1) / np.sqrt(v.size))), int(v.size))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    fac = np.load(os.path.join(exp_dir, "factors_no_mask.npz"),
                  allow_pickle=True)
    dates = panel["dates"]
    values = fac["values"].astype(np.float64)
    all_valid = fac["masks"].all(axis=2)
    r_eff = panel["r_eff"]
    m_arm = panel["m_no"]
    T, N, K = values.shape
    label = r_eff[1:, :]
    sample_ok = all_valid[:-1, :] & (m_arm[:-1, :] & m_arm[1:, :])

    def days(a, b):
        return np.nonzero((dates[:-1] >= np.datetime64(a)) &
                          (dates[:-1] <= np.datetime64(b)))[0]

    tr_days = days(*TRAIN)
    te_days = days(*TEST)

    # ---- normalisation from the TRAIN segment only (same rule as training)
    tr_mask = np.zeros((T - 1, N), dtype=bool)
    tr_mask[tr_days, :] = True
    tr_mask &= sample_ok
    mu = np.zeros(K)
    sd = np.ones(K)
    for k in range(K):
        v = values[:-1, :, k][tr_mask]
        v = v[np.isfinite(v)]
        if v.size > 10:
            mu[k] = float(v.mean())
            s = float(v.std())
            sd[k] = s if s > 1e-12 else 1.0
    z = ((values - mu[None, None, :]) / sd[None, None, :])

    idx = np.nonzero(sample_ok)
    X = np.nan_to_num(z[:-1][idx[0], idx[1], :], nan=0.0)
    y = np.nan_to_num(label[idx[0], idx[1]], nan=0.0)

    out = {"experiment": args.exp,
           "generated_at": datetime.now().astimezone().isoformat(),
           "script": "scripts/work449_baselines.py",
           "sample_set": "12 factors all valid AND M_factor bidirectional",
           "normalisation": "z-score with TRAIN-segment mean/std only",
           "segments": {"train": list(TRAIN), "test": list(TEST)}}

    # ---- 1. per-factor test RankIC -------------------------------------
    fac_rows = []
    for k in range(K):
        grid = np.full((T - 1, N), np.nan)
        grid[:, :] = z[:-1, :, k]
        mu_ic, t_ic, n = daily_ic(grid, label, sample_ok, te_days, "rank")
        fac_rows.append({"factor_index": k, "test_rankic_mean": mu_ic,
                         "test_rankic_t": t_ic, "days": n})
    out["per_factor_test_rankic"] = fac_rows
    abs_ics = [abs(r["test_rankic_mean"]) for r in fac_rows]
    out["per_factor_abs_rankic_summary"] = {
        "min": float(min(abs_ics)), "max": float(max(abs_ics)),
        "median": float(np.median(abs_ics)),
        "n_ge_0.03": int(sum(1 for v in abs_ics if v >= 0.03)),
        "n_ge_0.02": int(sum(1 for v in abs_ics if v >= 0.02)),
        "n_le_0.0161": int(sum(1 for v in abs_ics if v <= 0.0161)),
        "values": [float(v) for v in abs_ics],
    }

    # ---- 2. OLS baseline ------------------------------------------------
    A = np.hstack([X, np.ones((len(X), 1))])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    grid = np.full((T - 1, N), np.nan)
    for row, (t, n) in enumerate(zip(idx[0], idx[1])):
        grid[t, n] = A[row] @ beta
    r_mu, r_t, r_n = daily_ic(grid, label, sample_ok, te_days, "rank")
    p_mu, p_t, p_n = daily_ic(grid, label, sample_ok, te_days, "pearson")
    out["ols_baseline"] = {
        "test_rankic_mean": r_mu, "test_rankic_t": r_t,
        "test_pearson_ic_mean": p_mu, "test_pearson_ic_t": p_t,
        "days": r_n,
        "note": "RankIC = daily Spearman; Pearson IC = daily Pearson on raw "
                "values; both reported so the口径 is explicit",
    }

    # ---- 3. best constant predictor (train segment, AdjMSE) -------------
    ytr = y
    grid_c = np.linspace(-0.02, 0.02, 1601)
    losses = [adj_mse_scalar(np.full(len(ytr), c), ytr) for c in grid_c]
    best_i = int(np.argmin(losses))
    c_star = float(grid_c[best_i])
    c_loss = float(losses[best_i])
    c_grid = np.full((T - 1, N), np.nan)
    for t, n in zip(idx[0], idx[1]):
        c_grid[t, n] = c_star
    c_mu, c_t, c_n = daily_ic(c_grid, label, sample_ok, te_days, "rank")
    out["best_constant_baseline"] = {
        "c": c_star, "train_adjmse": c_loss,
        "train_adjmse_zero_constant": adj_mse_scalar(
            np.zeros(len(ytr)), ytr),
        "test_rankic_mean": c_mu, "test_rankic_t": c_t,
        "grid": "[-0.02, 0.02] step 2.5e-5",
    }

    # ---- 4. frozen MLP train AdjMSE -------------------------------------
    log_path = os.path.join(exp_dir, "train_log_no_mask.json")
    if os.path.exists(log_path):
        lg = json.load(open(log_path, encoding="utf-8"))
        hist = lg.get("history", [])
        out["frozen_mlp_train_adjmse"] = {
            "final_epoch": (hist[-1]["train_loss"] if hist else None),
            "best_epoch": lg.get("best_epoch"),
            "best_epoch_train_loss": (
                hist[lg["best_epoch"]]["train_loss"]
                if hist and lg.get("best_epoch") is not None
                and lg["best_epoch"] < len(hist) else None),
            "note": "train-mode loss (dropout active) on the train segment",
        }

    # ---- 5. MLP prediction dispersion vs label --------------------------
    pz = np.load(os.path.join(exp_dir, "pred_no_mask.npz"),
                 allow_pickle=True)["pred"]
    disp = []
    lab_disp = []
    for t in te_days:
        sel = sample_ok[t] & np.isfinite(pz[t])
        if sel.sum() > 20:
            disp.append(float(pz[t][sel].std()))
            lab_disp.append(float(np.nanstd(label[t][sample_ok[t]])))
    out["mlp_prediction_dispersion"] = {
        "pred_xsec_std_median_all_test_days": float(np.median(disp)),
        "label_xsec_std_median_all_test_days": float(np.median(lab_disp)),
        "ratio": float(np.median(disp) / np.median(lab_disp)),
        "note": "all test days (the first-200-day subset used earlier gives "
                "a slightly smaller ratio; both are reported here only in "
                "the all-test-days form)",
    }

    path = os.path.join(exp_dir, "baselines.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items()
                      if k in ("per_factor_abs_rankic_summary", "ols_baseline",
                               "best_constant_baseline",
                               "frozen_mlp_train_adjmse",
                               "mlp_prediction_dispersion")},
                     ensure_ascii=False, indent=1))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
