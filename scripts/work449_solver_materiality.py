#!/usr/bin/env python3
"""WORK-449 · 求解器口径材料性探针（协议 v1.4 §8 补充披露项）。

为什么需要这个脚本
------------------
预注册 v1.0 §8/§10 门槛 6 写的是「QP 求解器与 SLSQP 在 5 个抽样日上相对差
≤1e-6」——**没有写明分母**。交付轮次的实测：

  * 权重口径（`|w_qp - w_slsqp|` 相对 `max|w_slsqp|`）：15/15 个实例超阈，
    最大 4.686871e-03（no_mask/halt_only/full_mask 三臂各 5 日）；
  * 目标函数口径（`|f_qp - f_slsqp|` 相对 `|f_slsqp|`）：15 个实例中
    **1 个也超阈**（no_mask / 2023-12-18，1.2721851344898035e-06）。

也就是说「两个求解器落在同一个最优点上」这个论断在**数值上不完全成立**。
本脚本回答一个更关键的问题：**这个不成立会不会翻掉主检验的判词？**

方法
----
1. 复现回测主循环里**抽样日**的 QP 设置（mu / 股票池 / 可交易集合 /
   固定块 / 120 日 Ledoit-Wolf 协方差），分别用交付用的投影梯度求解器与
   参考求解器 SLSQP 各解一次；
2. 检查投影梯度解是否**逐位复现**交付权重（自检：|w_recomputed −
   weights_delivered| 应为 0），证明探针重建的是同一天同一个问题；
3. 构造**反事实收益序列**：把抽样日的权重换成 SLSQP 解，其余日不动。
   换手与收益的扰动**只影响 t 与 t+1 两天**（t 日收益由 w_{t−1} 承担，
   而 t+1 日的 QP 最优解与 warm start 无关），因此反事实序列可以解析地
   算出来，不必重跑整条回测；
4. 对三臂分别算出反事实 Sharpe，再给主检验量 ΔSharpe 的**材料性上界**：

       |ΔSharpe' − ΔSharpe| ≤ |δ_Sharpe(no_mask)| + |δ_Sharpe(full_mask)|

输出：`experiments/WORK-449/solver_materiality.json`

注意
----
本脚本**不是**独立复算脚本。它刻意 `import` 建模阶段的求解器，因为探针的
目的就是检查**交付用的那一个求解器**的行为。真正的独立复算在
`audit/recompute_work449.py`（不 import 建模阶段任何函数）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from work449_backtest import (  # noqa: E402
    ALPHA,
    COV_LOOKBACK,
    RF,
    W_MAX,
    ledoit_wolf,
    psd_clip,
    slsqp_qp,
    solve_qp,
)

COST_MAIN_BPS = 8.0
COST_SENS_BPS = 5.0


def build_pool(exp_dir, dates, codes):
    """按生效日取最近一份名单（与 work449_backtest.py 同逻辑）。"""
    lists = json.load(open(os.path.join(exp_dir, "pool_membership.json")))
    eff = np.array(sorted(lists))
    members = {d: {c for c, _ in lists[d]} for d in lists}
    in_pool = np.zeros((len(dates), len(codes)), dtype=bool)
    cur = set()
    for t, d in enumerate(dates):
        key = str(d)
        if key in members:
            cur = members[key]
        else:
            past = [e for e in eff if e <= key]
            cur = members[past[-1]] if past else set()
        in_pool[t] = [c in cur for c in codes]
    return in_pool


def day_sigma(r_eff, has_row, t, n_codes):
    """复现回测里第 t 日的协方差构造。"""
    lo = max(0, t - COV_LOOKBACK)
    live = has_row[lo:t, :].any(axis=0)
    idx = np.nonzero(live)[0]
    Sigma = np.zeros((n_codes, n_codes))
    if len(idx) > 5:
        X = np.nan_to_num(r_eff[lo:t, idx].T, nan=0.0)
        if X.shape[0] > 5 and X.shape[1] > 10:
            Xc = X - X.mean(axis=1, keepdims=True)
            S, _ = ledoit_wolf(Xc)
            S = psd_clip(S)
            Sigma[np.ix_(idx, idx)] = S
    else:
        Sigma = np.eye(n_codes) * 1e-6
    return Sigma


def sharpe_of(net, ann_days):
    cum = np.cumprod(1.0 + net)
    total = float(cum[-1] - 1.0)
    ann = (1.0 + total) ** (365.25 / ann_days) - 1.0
    vol = float(np.std(net, ddof=1) * np.sqrt(252))
    return (ann - RF) / vol if vol > 0 else None, ann, vol


def probe_arm(arm, dates, codes, r_eff, m_exec, has_row, in_pool, pred,
              weights_delivered, tidx, sample_days):
    """对一个臂做抽样日探针。

    返回 (rows, cf_delta)：
      rows     —— 每个抽样日的两解差异（权重口径 / 目标函数口径）+ 自检复现差
      cf_delta —— {t+1: {gross_diff, turnover_diff, ...}}，反事实收益增量
    """
    n_codes = len(codes)
    rows = []
    cf_delta = {}          # t+1 -> 反事实净收益增量
    for t in sample_days:
        mu = np.nan_to_num(pred[t], nan=0.0)
        pool = in_pool[t]
        prev_pool = in_pool[t - 1] if t > 0 else np.zeros_like(pool)
        left = prev_pool & ~pool
        tradable = pool | left
        blocked = tradable & ~m_exec[t]
        forced_zero = left & m_exec[t]
        frozen = blocked | (left & ~m_exec[t])
        free = tradable & m_exec[t] & ~left
        w_prev = weights_delivered[t - 1] if t > 0 else np.zeros(n_codes)
        free &= (w_prev > 0) | pool
        w_fixed = np.where(frozen, w_prev, 0.0)
        w_fixed[forced_zero] = 0.0
        target = 1.0 - w_fixed.sum()
        if target < 0:
            target = 0.0
            w_fixed = w_fixed * 0.0

        Sigma = day_sigma(r_eff, has_row, t, n_codes)
        obj = lambda v, S=Sigma, m=mu: float(ALPHA * v @ S @ v - m @ v)  # noqa: E731

        w_qp = solve_qp(mu, Sigma, w_fixed, free, target,
                        warm=w_prev[free] if free.any() else None)
        w_slsqp = slsqp_qp(mu, Sigma, w_fixed, free, target)

        f_qp = obj(w_qp)
        f_slsqp = obj(w_slsqp)
        denom_w = max(np.abs(w_slsqp).max(), 1e-12)

        # 自检：投影梯度解是否逐位复现交付权重
        reprod = float(np.max(np.abs(w_qp - weights_delivered[t])))

        # 次日（t+1）毛收益贡献与换手
        if t + 1 < len(dates):
            rp = np.nan_to_num(r_eff[t + 1], nan=0.0)
            g_qp = float(w_qp @ rp)
            g_slsqp = float(w_slsqp @ rp)
            dw_qp = float(np.abs(weights_delivered[t + 1] - w_qp).sum())
            dw_slsqp = float(np.abs(weights_delivered[t + 1] - w_slsqp).sum())
            cf_delta[t + 1] = {
                "gross_diff": g_slsqp - g_qp,
                "turnover_diff": dw_slsqp - dw_qp,
                "gross_base": g_qp,
                "turnover_base": dw_qp,
            }
        rows.append({
            "date": str(dates[t]),
            "reproduce_max_abs_diff_vs_delivered": reprod,
            "weight_max_abs_diff": float(np.max(np.abs(w_qp - w_slsqp))),
            "weight_max_rel_diff": float(np.max(np.abs(w_qp - w_slsqp)) / denom_w),
            "obj_pgp": f_qp,
            "obj_slsqp": f_slsqp,
            "obj_rel_diff": float(abs(f_qp - f_slsqp) / max(abs(f_slsqp), 1e-16)),
            "n_free": int(free.sum()),
            "sum_w_pgp": float(w_qp.sum()),
            "sum_w_slsqp": float(w_slsqp.sum()),
        })

    # ---- 反事实净收益序列 ----
    def net_series(cost_bps, use_cf):
        net = []
        for t in tidx:
            rp = np.nan_to_num(r_eff[t], nan=0.0)
            wp = weights_delivered[t - 1] if t > 0 else np.zeros(n_codes)
            dw = float(np.abs(weights_delivered[t] - wp).sum())
            g = float(wp @ rp)
            n = g - (cost_bps * 1e-4) * dw
            if use_cf and t in cf_delta:
                d = cf_delta[t]
                n = n + d["gross_diff"] - (cost_bps * 1e-4) * d["turnover_diff"]
            net.append(n)
        return np.array(net)

    ann_days = (np.datetime64(str(dates[tidx[-1]])) -
                np.datetime64(str(dates[tidx[0]]))).astype(int)
    summary = {"arm": arm, "sampled_days": rows}
    for cost in (COST_MAIN_BPS, COST_SENS_BPS):
        sh_base, ann_base, vol_base = sharpe_of(net_series(cost, False), ann_days)
        sh_cf, ann_cf, vol_cf = sharpe_of(net_series(cost, True), ann_days)
        summary[f"cost_{int(cost)}bps"] = {
            "delivered_sharpe": sh_base,
            "counterfactual_sharpe": sh_cf,
            "sharpe_perturbation": (sh_cf - sh_base)
            if None not in (sh_cf, sh_base) else None,
            "delivered_ann": ann_base,
            "counterfactual_ann": ann_cf,
            "delivered_vol": vol_base,
            "counterfactual_vol": vol_cf,
        }
    summary["max_reproduce_abs_diff"] = max(
        r["reproduce_max_abs_diff_vs_delivered"] for r in rows)
    summary["max_weight_rel_diff"] = max(r["weight_max_rel_diff"] for r in rows)
    summary["max_obj_rel_diff"] = max(r["obj_rel_diff"] for r in rows)
    summary["obj_rel_diff_over_1e6"] = sum(1 for r in rows if r["obj_rel_diff"] > 1e-6)
    summary["weight_rel_diff_over_1e6"] = sum(
        1 for r in rows if r["weight_max_rel_diff"] > 1e-6)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    ap.add_argument("--arms", nargs="+",
                    default=["no_mask", "halt_only", "full_mask"])
    ap.add_argument("--days", type=int, default=6,
                    help="抽样日数：前 5 日与交付轮次的求解器交叉校验日重合")
    ap.add_argument("--tag", default="",
                    help="输入产物后缀（权重/预测），默认用交付轮次")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    dates, codes = panel["dates"], panel["codes"]
    r_eff, m_exec, has_row = panel["r_eff"], panel["m_exec"], panel["has_row"]

    in_pool = build_pool(exp_dir, dates, codes)
    test_sel = ((dates >= np.datetime64("2022-01-01")) &
                (dates <= np.datetime64("2024-12-31")))
    tidx = np.nonzero(test_sel)[0]

    # 抽样日：前 5 日与 work449_backtest.py 的 rng(42) 抽样完全重合
    rng = np.random.default_rng(42)
    base5 = rng.choice(tidx[1:], size=5, replace=False).tolist()
    extra = rng.choice(tidx[1:], size=max(args.days - 5, 0), replace=False).tolist()
    sample_days = sorted(set(base5 + extra))

    out = {
        "purpose": "求解器口径材料性上界（协议 v1.4 §8 披露项）",
        "cost_bps": COST_MAIN_BPS,
        "n_sample_days": len(sample_days),
        "sample_dates": [str(dates[t]) for t in sample_days],
        "note": "反事实 = 把抽样日权重换成 SLSQP 解；扰动只落在 t 与 t+1 两天",
        "arms": {},
    }

    pert = {}
    for arm in args.arms:
        pred_npz = np.load(os.path.join(exp_dir, f"pred_{arm}{args.tag}.npz"),
                           allow_pickle=True)
        w_npz = np.load(os.path.join(exp_dir, f"weights_{arm}{args.tag}.npz"),
                        allow_pickle=True)
        res = probe_arm(arm, dates, codes, r_eff, m_exec, has_row, in_pool,
                        pred_npz["pred"], w_npz["weights"], tidx, sample_days)
        out["arms"][arm] = res
        pert[arm] = res["cost_8bps"]["sharpe_perturbation"]

    def delta_of(block, arms):
        d_base = block[arms[0]]["cost_8bps"]["delivered_sharpe"] - \
            block[arms[1]]["cost_8bps"]["delivered_sharpe"]
        d_cf = block[arms[0]]["cost_8bps"]["counterfactual_sharpe"] - \
            block[arms[1]]["cost_8bps"]["counterfactual_sharpe"]
        return d_base, d_cf

    def delta_of_cost(block, arms, cost):
        d_base = block[arms[0]][f"cost_{cost}bps"]["delivered_sharpe"] - \
            block[arms[1]][f"cost_{cost}bps"]["delivered_sharpe"]
        d_cf = block[arms[0]][f"cost_{cost}bps"]["counterfactual_sharpe"] - \
            block[arms[1]][f"cost_{cost}bps"]["counterfactual_sharpe"]
        return d_base, d_cf

    if "no_mask" in pert and "full_mask" in pert:
        d_base, d_cf = delta_of(out["arms"], ["no_mask", "full_mask"])
        bound = abs(pert["no_mask"]) + abs(pert["full_mask"])
        out["delta_sharpe"] = {
            "delivered": d_base,
            "counterfactual": d_cf,
            "abs_perturbation": abs(d_cf - d_base),
            "materiality_bound": bound,
            "materiality_share_of_delta": bound / abs(d_base) if d_base else None,
            "verdict_unchanged": (d_cf < 0) == (d_base < 0),
        }
        b5, c5 = delta_of_cost(out["arms"], ["no_mask", "full_mask"], 5)
        bound5 = abs(out["arms"]["no_mask"]["cost_5bps"]["sharpe_perturbation"]) + \
            abs(out["arms"]["full_mask"]["cost_5bps"]["sharpe_perturbation"])
        out["delta_sharpe_5bps"] = {
            "delivered": b5,
            "counterfactual": c5,
            "abs_perturbation": abs(c5 - b5),
            "materiality_bound": bound5,
            "materiality_share_of_delta": bound5 / abs(b5) if b5 else None,
            "verdict_unchanged": (c5 < 0) == (b5 < 0),
        }

    path = os.path.join(exp_dir, "solver_materiality.json")
    # numpy scalars (bool_/float64/int64) are not JSON serializable by default
    json.dump(out, open(path, "w"), indent=1, ensure_ascii=False,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps({
        "delta_sharpe_8bps": out.get("delta_sharpe"),
        "delta_sharpe_5bps": out.get("delta_sharpe_5bps"),
        "max_obj_rel_diff": {a: out["arms"][a]["max_obj_rel_diff"]
                             for a in out["arms"]},
        "obj_over_1e6": {a: out["arms"][a]["obj_rel_diff_over_1e6"]
                         for a in out["arms"]},
        "max_weight_rel_diff": {a: out["arms"][a]["max_weight_rel_diff"]
                                for a in out["arms"]},
        "max_reproduce_abs_diff": {a: out["arms"][a]["max_reproduce_abs_diff"]
                                   for a in out["arms"]},
    }, indent=1, ensure_ascii=False,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("written:", path)


if __name__ == "__main__":
    sys.exit(main())
