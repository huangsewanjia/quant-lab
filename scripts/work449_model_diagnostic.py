#!/usr/bin/env python3
"""WORK-449 · 模型能力诊断（失败实验登记，非主检验）。

目的：回答「本单三臂比较的**绝对水平**被模型能力压低了多少」，并把
「掩码背离未复现」这一判词的**适用边界**量化清楚。

背景（本脚本要证伪/量化的怀疑）
--------------------------------
按预注册 v1.0/v1.3 冻结的模型类（MLP 12→128→128→1 + AdjMSE γ=0.1）训练后：

* MLP 预测的**截面标准差**中位数只有标签标准差的约 0.5%；
* MLP 在测试段的**表观 IC 远低于**它自己的输入因子（单因子 |IC| 达 0.058）；
* 训练集上的 AdjMSE 甚至**不如一个最优常数预测器**。

三条合起来说明：不是「AdjMSE 偏好常数解」，而是**优化没走完 / 模型类不合适**。
本脚本在**完全相同的数据、样本集、归一化、优化器、学习率、batch、epoch 数、
梯度裁剪、seed** 下，只改变两件事——

  1. 模型类：MLP（协议冻结） vs 线性（0 隐藏层退化 MLP）
  2. 损失：AdjMSE γ=0.1（协议冻结） vs 纯 MSE

——并**分别**在 2021 验证段（选择用数据）与 2022-2024 测试段（观察用数据）上
报告逐日 RankIC。

口径纪律
--------
* **验证段**（2021）是协议允许的**选择用数据**：模型类与损失的比较结论以它为准。
* **测试段**（2022-2024）只作**观察**，不得反过来用于选择；本脚本不改变任何
  主检验量，也不改变预注册冻结的模型类。
* 本脚本**不 import** 建模阶段的任何函数（因子与标签直接从产物读取），
  与 `audit/recompute_work449.py` 的独立性纪律一致。
* 固定随机种子；记录实际运行命令与数据快照。

用法
----
    OMP_NUM_THREADS=1 WORK449_TORCH_THREADS=2 \
      ../.venv/bin/python scripts/work449_model_diagnostic.py --exp WORK-449
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

SEED = 42
EPOCHS = 60
BATCH = 8192
LR = 5e-4
WD = 1e-5
GRAD_CLIP = 1.0
HIDDEN = 128
DROPOUT = 0.1
GAMMA = 0.1
VAL = ("2021-01-01", "2021-12-31")
TEST = ("2022-01-01", "2024-12-31")
SEEDS = (42, 43)


def seed_all(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(int(os.environ.get("WORK449_TORCH_THREADS", "2")))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def adj_mse(pred, target, gamma: float = GAMMA):
    """AdjMSE (paper §3.5.2): wrong-sign errors carry (1+gamma)/gamma weight."""
    sq = (pred - target) ** 2
    same = (torch.sign(pred) * torch.sign(target)) > 0
    w = torch.where(same, torch.full_like(sq, gamma),
                    torch.full_like(sq, 1.0 + gamma))
    return (w * sq).mean()


class Linear(nn.Module):
    """0-hidden-layer MLP: the linear baseline."""

    def __init__(self, d_in: int):
        super().__init__()
        self.lin = nn.Linear(d_in, 1)

    def forward(self, x):
        return self.lin(x).squeeze(-1)


class MLP(nn.Module):
    """Protocol-frozen architecture: paper §3.5.3 (128 units, GELU, dropout 0.1)."""

    def __init__(self, d_in: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_in, HIDDEN), nn.GELU(), nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, HIDDEN), nn.GELU(), nn.Dropout(DROPOUT),
            nn.Linear(HIDDEN, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


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


def daily_rank_ic(pred_grid, label, valid, tidx):
    vals = []
    for t in tidx:
        ok = valid[t] & np.isfinite(pred_grid[t]) & np.isfinite(label[t])
        if ok.sum() < 20:
            continue
        rp = avg_rank(pred_grid[t][ok])
        ry = avg_rank(label[t][ok])
        if rp.std() == 0 or ry.std() == 0:
            continue
        vals.append(float(np.corrcoef(rp, ry)[0, 1]))
    v = np.array(vals)
    if v.size < 2:
        return float("nan"), float("nan"), int(v.size)
    return float(v.mean()), float(v.mean() / (v.std(ddof=1) / np.sqrt(v.size))), int(v.size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    fac = np.load(os.path.join(exp_dir, "factors_no_mask.npz"), allow_pickle=True)
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

    va_days = days(*VAL)
    te_days = days(*TEST)

    # ---- normalisation from the TRAIN segment only (same rule as training) ----
    tr_days = days("2015-01-01", "2020-12-31")
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
    norm = ((values - mu[None, None, :]) / sd[None, None, :]).astype(np.float32)

    idx = np.nonzero(sample_ok)
    x_all = norm[idx[0], idx[1], :]
    y_all = label[idx[0], idx[1]].astype(np.float32)
    is_tr = np.isin(idx[0], tr_days)
    Xtr = torch.from_numpy(x_all[is_tr])
    Ytr = torch.from_numpy(y_all[is_tr])
    print(f"train samples = {len(Xtr)}  features = {K}", flush=True)

    results = []
    for model_name, model_cls in (("mlp", MLP), ("linear", Linear)):
        for loss_name in ("adj", "mse"):
            for seed in SEEDS:
                seed_all(seed)
                model = model_cls(K)
                opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
                gen = torch.Generator().manual_seed(seed)
                t0 = time.time()
                for _ep in range(EPOCHS):
                    model.train()
                    perm = torch.randperm(len(Xtr), generator=gen)
                    for i in range(0, len(Xtr), BATCH):
                        b = perm[i:i + BATCH]
                        opt.zero_grad(set_to_none=True)
                        o = model(Xtr[b])
                        t = Ytr[b]
                        loss = (adj_mse(o, t) if loss_name == "adj"
                                else ((o - t) ** 2).mean())
                        loss.backward()
                        nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP)
                        opt.step()
                model.eval()
                with torch.no_grad():
                    p_all = model(torch.from_numpy(x_all)).numpy().astype(np.float64)
                pgrid = np.full((T - 1, N), np.nan)
                pgrid[idx[0], idx[1]] = p_all
                v_mu, v_t, v_n = daily_rank_ic(pgrid, label, sample_ok, va_days)
                t_mu, t_t, t_n = daily_rank_ic(pgrid, label, sample_ok, te_days)
                # cross-sectional dispersion of the predictions vs the label
                disp = []
                for t in te_days[:200]:
                    sel = sample_ok[t] & np.isfinite(pgrid[t])
                    if sel.sum() > 20:
                        disp.append(float(pgrid[t][sel].std()))
                results.append({
                    "model": model_name, "loss": loss_name, "seed": seed,
                    "val_rankic_mean": v_mu, "val_rankic_t": v_t, "val_days": v_n,
                    "test_rankic_mean": t_mu, "test_rankic_t": t_t, "test_days": t_n,
                    "pred_xsec_std_median": float(np.median(disp)) if disp else None,
                    "label_xsec_std_median": float(np.median(
                        [np.nanstd(label[t][sample_ok[t]]) for t in te_days[:200]
                         if sample_ok[t].sum() > 20])),
                    "train_seconds": round(time.time() - t0, 1),
                })
                print(f"  {model_name:6s} {loss_name:3s} seed={seed} "
                      f"valIC={v_mu:+.5f} (t={v_t:.2f})  testIC={t_mu:+.5f} "
                      f"(t={t_t:.2f})  predStd={results[-1]['pred_xsec_std_median']:.3e}",
                      flush=True)

    # ---- aggregate by (model, loss) ----
    agg = {}
    for r in results:
        key = f"{r['model']}+{r['loss']}"
        agg.setdefault(key, []).append(r)
    summary = {}
    for key, rows in agg.items():
        summary[key] = {
            "val_rankic_mean": float(np.mean([r["val_rankic_mean"] for r in rows])),
            "val_rankic_mean_by_seed": {str(r["seed"]): r["val_rankic_mean"]
                                        for r in rows},
            "val_rankic_t_by_seed": {str(r["seed"]): r["val_rankic_t"]
                                     for r in rows},
            "test_rankic_mean": float(np.mean([r["test_rankic_mean"] for r in rows])),
            "test_rankic_mean_by_seed": {str(r["seed"]): r["test_rankic_mean"]
                                         for r in rows},
            "pred_xsec_std_median": float(np.median(
                [r["pred_xsec_std_median"] for r in rows])),
        }
    # dispersion relative to the label's own cross-sectional std
    lab_std = float(np.median([r["label_xsec_std_median"] for r in results]))
    for key in summary:
        summary[key]["dispersion_ratio_vs_label"] = (
            summary[key]["pred_xsec_std_median"] / lab_std)

    out = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "script": "scripts/work449_model_diagnostic.py",
        "purpose": ("量化「掩码背离未复现」判词的适用边界：本单冻结的 MLP+AdjMSE "
                    "在 12 因子 / CSI300 上的绝对水平被模型能力压低；本脚本在完全相同"
                    "的数据、样本集、归一化与优化设置下对比模型类与损失。"
                    "验证段（2021）为选择用数据，测试段（2022-2024）仅作观察。"),
        "protocol": {
            "model_frozen": "MLP 12->128->128->1 (GELU, dropout 0.1) + AdjMSE gamma=0.1",
            "optimizer": "AdamW lr 5e-4 wd 1e-5 clip 1.0 batch 8192 epochs 60",
            "seeds": list(SEEDS),
            "selection_segment": "2021-01-01..2021-12-31",
            "observation_segment": "2022-01-01..2024-12-31",
        },
        "runs": results,
        "summary": summary,
        "label_xsec_std_median": lab_std,
    }
    path = os.path.join(exp_dir, "model_diagnostic.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n-> {path}")
    for key, s in sorted(summary.items()):
        print(f"  {key:<12} valIC={s['val_rankic_mean']:+.5f}  "
              f"testIC={s['test_rankic_mean']:+.5f}  "
              f"dispersion={s['dispersion_ratio_vs_label']:.4f}")


if __name__ == "__main__":
    main()
