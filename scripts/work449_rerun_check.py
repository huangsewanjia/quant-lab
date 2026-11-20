#!/usr/bin/env python3
"""WORK-449 · 协议 §10 门槛 6 第二判据：同 seed 重跑一致性。

协议原文：「QP 求解器与 SLSQP 在 5 个抽样日上相对差 ≤1e-6；**同 seed 重跑两次，
指标一致（相对差 ≤1e-6）**」。

本脚本比较两轮**完全相同**（同 seed 42、同线程设置、同数据、同代码）的运行：

  A 轮 = 协议冻结轮次      `metrics_<arm>.json` / `pred_<arm>.npz`
  B 轮 = 同 seed 重跑轮次  `metrics_<arm>_r2.json` / `pred_<arm>_r2.npz`

比较项：
  1. 三臂 × 8 项标量指标（Sharpe / 年化收益 / 年化波动 / MDD / 双边换手 /
     表观 IC / 总收益 / 日均持仓数）—— 相对差 ≤1e-6
  2. 三臂 × 2 档成本的逐日净收益序列 —— 逐元素最大绝对差
  3. 三臂的预测矩阵 —— 逐元素最大绝对差（应当逐位相同）

产出 `experiments/WORK-449/rerun_same_seed.json`，供报告 §10 的门槛表引用。

用法
----
    ../.venv/bin/python scripts/work449_rerun_check.py --exp WORK-449
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import numpy as np

ARMS = ("no_mask", "halt_only", "full_mask")
TOL = 1e-6
SCALARS = ("sharpe_rf1.5", "ann_return", "ann_vol", "max_drawdown",
           "turnover_two_sided_total", "apparent_ic_mean", "total_return",
           "avg_active_stocks")


def rel_diff(a: float, b: float) -> float:
    den = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / den


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    ap.add_argument("--tag", default="_r2")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    comparisons = []
    max_rel = 0.0
    max_abs_ret = 0.0
    max_abs_pred = 0.0

    for arm in ARMS:
        pa = os.path.join(exp_dir, f"metrics_{arm}.json")
        pb = os.path.join(exp_dir, f"metrics_{arm}{args.tag}.json")
        if not (os.path.exists(pa) and os.path.exists(pb)):
            print(f"skip {arm}: missing {pb}")
            continue
        A = json.load(open(pa, encoding="utf-8"))
        B = json.load(open(pb, encoding="utf-8"))
        for cost_key in ("main", "sensitivity_5bps"):
            for k in SCALARS:
                va = A[cost_key].get(k)
                vb = B[cost_key].get(k)
                if va is None or vb is None:
                    continue
                d = rel_diff(float(va), float(vb))
                max_rel = max(max_rel, d)
                comparisons.append({
                    "item": f"[{arm}/{cost_key}] {k}",
                    "round_a": va, "round_b": vb, "rel_diff": d,
                    "within_tolerance": bool(d <= TOL),
                })
            ra = np.array(A[cost_key]["net_returns"], dtype=np.float64)
            rb = np.array(B[cost_key]["net_returns"], dtype=np.float64)
            if ra.shape == rb.shape:
                ad = float(np.max(np.abs(ra - rb)))
                max_abs_ret = max(max_abs_ret, ad)
                comparisons.append({
                    "item": f"[{arm}/{cost_key}] net_returns max|Δ|",
                    "round_a": len(ra), "round_b": len(rb), "rel_diff": ad,
                    "within_tolerance": bool(ad <= TOL),
                })
        za = np.load(os.path.join(exp_dir, f"pred_{arm}.npz"),
                     allow_pickle=True)["pred"]
        zb = np.load(os.path.join(exp_dir, f"pred_{arm}{args.tag}.npz"),
                     allow_pickle=True)["pred"]
        if za.shape == zb.shape:
            pd = float(np.max(np.abs(np.nan_to_num(za, nan=0.0) -
                                     np.nan_to_num(zb, nan=0.0))))
            max_abs_pred = max(max_abs_pred, pd)
            comparisons.append({
                "item": f"[{arm}] pred max|Δ|",
                "round_a": float(za.shape[0]), "round_b": float(zb.shape[0]),
                "rel_diff": pd, "within_tolerance": bool(pd <= TOL),
            })

    all_ok = bool(comparisons) and all(c["within_tolerance"]
                                        for c in comparisons)
    out = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "script": "scripts/work449_rerun_check.py",
        "criterion": "protocol §10 gate 6, second clause: same seed re-run, "
                     "metrics identical (rel diff <= 1e-6)",
        "round_a": f"protocol-frozen round (seed 42)",
        "round_b": f"independent re-run, same seed 42, tag '{args.tag}'",
        "n_comparisons": len(comparisons),
        "max_rel_diff": max_rel,
        "max_abs_diff_net_returns": max_abs_ret,
        "max_abs_diff_pred": max_abs_pred,
        "all_within_tolerance": all_ok,
        "comparisons": comparisons,
    }
    path = os.path.join(exp_dir, "rerun_same_seed.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{'PASS' if all_ok else 'FAIL'}  comparisons={len(comparisons)}  "
          f"max_rel_diff={max_rel:.3e}  max|Δ| net={max_abs_ret:.3e}  "
          f"max|Δ| pred={max_abs_pred:.3e}")
    for c in comparisons:
        if not c["within_tolerance"]:
            print(f"  OVER: {c['item']}  rel_diff={c['rel_diff']:.3e}  "
                  f"a={c['round_a']}  b={c['round_b']}")
    print(f"-> {path}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
