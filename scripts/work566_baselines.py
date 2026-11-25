# -*- coding: utf-8 -*-
"""WORK-566 · FMB-style 线性基线（论文附录 E 的基线之一，H2 的对照）。

论文口径：每个训练日单独做**截面正则化回归**，再**平均系数**用于样本外预测；
正则强度由验证集 MSE 选。输入为同一套 40 维描述符（训练段标准化）。

本脚本同时承担任务书要求的「最便宜的口径校验」：先跑通
「数据 → 描述符 → 排序 → 组合 → 净收益」全链，确认口径没错，再上 S1/S2。

产出：data/WORK-566/preds_seed{seed}_fmb.npz（结构与其它臂一致）
"""
import argparse
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")
ALPHAS = [1.0, 10.0, 100.0, 1000.0]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-dates", type=int, default=0, help=">0 时只取前 N 个训练日（调试用）")
    args = ap.parse_args()

    feat = np.load(os.path.join(DATA, "feat.npy"), mmap_mode="r")
    tgt = np.load(os.path.join(DATA, "targets.npy"), mmap_mode="r")
    norm = json.load(open(os.path.join(DATA, "norm.json"), encoding="utf-8"))
    s = np.load(os.path.join(DATA, "samples.npz"))
    stock, date, dts = s["stock_idx"], s["date_idx"], s["dts"]
    m_train, m_valid, m_test = s["m_train"], s["m_valid"], s["m_test"]
    mean = np.asarray(norm["mean"], dtype=np.float32)
    std = np.asarray(norm["std"], dtype=np.float32)
    n_feat = feat.shape[2]

    tr = np.nonzero(m_train)[0]
    va = np.nonzero(m_valid)[0]
    te = np.nonzero(m_test)[0]
    ytr_all = np.asarray(tgt[stock[tr], date[tr], 0], dtype=np.float64)
    mu_y, sd_y = float(ytr_all.mean()), float(ytr_all.std(ddof=0))
    sd_y = sd_y if sd_y > 1e-9 else 1.0

    def X(idx):
        x = np.asarray(feat[stock[idx], date[idx], :], dtype=np.float32)
        x = (x - mean) / std
        x[~np.isfinite(x)] = 0.0
        np.clip(x, -8, 8, out=x)
        return x.astype(np.float64)

    def Y(idx):
        y = np.asarray(tgt[stock[idx], date[idx], 0], dtype=np.float64)
        return (y - mu_y) / sd_y

    # 按日期分组
    def group(idx):
        g = {}
        for j, k in enumerate(idx):
            g.setdefault(int(dts[k]), []).append(j)
        return g

    gtr = group(tr)
    dates_tr = sorted(gtr)
    if args.max_dates:
        dates_tr = dates_tr[:args.max_dates]
    log(f"训练日 {len(dates_tr)} 天；验证样本 {len(va):,}；测试样本 {len(te):,}")

    def fit_avg_coef(alpha):
        """逐日截面岭回归 → 平均系数（特征已标准化；截距不惩罚）。"""
        acc_w = np.zeros(n_feat)
        acc_b = 0.0
        n = 0
        for d in dates_tr:
            rows = gtr[d]
            xb = X(tr[np.asarray(rows)])
            yb = Y(tr[np.asarray(rows)])
            ok = np.isfinite(yb)
            xb, yb = xb[ok], yb[ok]
            if len(yb) < n_feat + 5:
                continue
            xc = xb - xb.mean(axis=0)
            yc = yb - yb.mean()
            A = xc.T @ xc + alpha * np.eye(n_feat)
            w = np.linalg.solve(A, xc.T @ yc)
            b = yb.mean() - xb.mean(axis=0) @ w
            acc_w += w
            acc_b += b
            n += 1
        return acc_w / n, acc_b / n, n

    def predict(idx, w, b):
        return (X(idx) @ w + b).astype(np.float32)

    best = None
    for alpha in ALPHAS:
        t0 = time.time()
        w, b, n = fit_avg_coef(alpha)
        pv = predict(va, w, b)
        yv = Y(va)
        mse = float(np.mean((pv - yv) ** 2))
        log(f"  α={alpha:g}: 训练日 {n}, 验证 MSE {mse:.6f} ({time.time()-t0:.0f}s)")
        if best is None or mse < best[0]:
            best = (mse, alpha, w, b)
    mse, alpha, w, b = best
    log(f"选中 α={alpha:g}（验证 MSE {mse:.6f}）")

    out = {}
    for split, idx in (("train", tr), ("valid", va), ("test", te)):
        out[f"pred_{split}"] = predict(idx, w, b)
        out[f"idx_{split}"] = idx
        out[f"stock_{split}"] = stock[idx]
        out[f"date_{split}"] = date[idx]
        out[f"y_{split}"] = np.asarray(tgt[stock[idx], date[idx], 0], dtype=np.float32)
    np.savez_compressed(os.path.join(DATA, f"preds_seed{args.seed}_fmb.npz"), **out)
    with open(os.path.join(OUT, f"fmb_meta_seed{args.seed}.json"), "w", encoding="utf-8") as f:
        json.dump({"alpha": alpha, "val_mse": mse, "n_train_dates": len(dates_tr),
                   "n_train_days_fitted": int(n), "seed": args.seed,
                   "coef": w.tolist(), "intercept": float(b)}, f, ensure_ascii=False, indent=2)
    log("基线预测已保存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
