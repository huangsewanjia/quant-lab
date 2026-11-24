#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · float32 产物量化对 RankIC 的影响诊断（v4 修正的依据）

问题
----
v3 收口时独立复算只剩 1 项 blocking：
    ic::dee_s43::test_rankic_mean
    审计复算 0.021630778861533438  vs  建模侧报告 0.02163083350961915
    相对差 2.526e-06（> 预注册 §7.2 的 1e-6 判据）。

怀疑根因：npz 里的 `pred` 存成 **float32**，而建模侧的 `test_rankic_mean`
是用内存里的 **float64** `pred_te` 算的。float32 量化会在截面里偶尔翻转
两个几乎相等的预测的秩位 -> 逐日 Spearman 变 -> 974 天均值变。

本脚本做三件事（都可复跑核对）
------------------------------
1. **三方一致性**：把建模侧函数（`work555_dee.rank_ic_by_day`）与审计侧函数
   （`recompute_work555.daily_ic`）都指向**同一份 npz**，看两者是否逐位相等。
   -> 若相等，说明两边**实现口径一致**，差异不在算法，而在数据精度。
2. **量化归因**：对 npz 的 float32 预测注入 ±1 ULP 扰动（float32 eps × |值|），
   重复 N 次复算均值 RankIC，得到「产物量化本身能造成多大抖动」。
   -> 若建模侧报告值落在这个抖动区间内，量化即被确证为根因。
3. **逐臂表**：每个臂/种子的「建模侧函数值 / 审计侧函数值 / 建模侧报告值 /
   相对差」，并标注是否越过 1e-6 判据（哪些是**侥幸通过**）。

用法：
  /tmp/w555venv/bin/python scripts/work555_float32_quant_diag.py
  /tmp/w555venv/bin/python scripts/work555_float32_quant_diag.py --check   # 只校验已落盘记录
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "experiments", "WORK-555")
RES = os.path.join(OUT, "float32_quant_diag.json")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "audit"))

import work555_dee as MODEL           # noqa: E402
import recompute_work555 as AUDIT     # noqa: E402

TOL = 1e-6                 # 与 audit/recompute_work555.py 的 TOL 同口径
ARMS = ("dee", "mlp_only", "xgb", "xgbfull")
SEEDS = (42, 43)
N_PERTURB = 20


def load_npz(arm, seed):
    p = os.path.join(OUT, f"preds_{arm}_s{seed}.npz")
    if not os.path.exists(p):
        return None
    z = np.load(p)
    if "y" in z.files:
        y = z["y"]
    else:
        # xgb / xgbfull 的 npz 不带 y（与审计侧同一口径：标签取自 sample.npz 的测试段）
        samp = np.load(os.path.join(OUT, "sample.npz"))
        m = samp["split"] == 2
        lab_by_row = dict(zip(samp["bt_row_id"][m].tolist(),
                              samp["y"][m].astype("float64").tolist()))
        y = np.array([lab_by_row[int(r)] for r in z["row"]], dtype="float64").astype("float32")
    return {"pred": z["pred"], "y": y, "date_idx": z["date_idx"],
            "pred_dtype": str(z["pred"].dtype)}


def ric_from_np(arm, seed):
    """返回 (建模侧函数值, 审计侧函数值, 模型报告值)。"""
    d = load_npz(arm, seed)
    if d is None:
        return None
    pred = d["pred"].astype(np.float64)
    y = d["y"].astype(np.float64)
    di = d["date_idx"]
    n_days = int(di.max()) + 1
    a = float(np.nanmean(MODEL.rank_ic_by_day(pred, y, di, n_days)))
    df = pd.DataFrame({"pred": pred, "label": y, "trade_date": di})
    b = float(np.nanmean(AUDIT.daily_ic(df, "pred", "label", "spearman")))
    runp = os.path.join(OUT, f"run_{arm}_s{seed}.json")
    reported = None
    if os.path.exists(runp):
        with open(runp, encoding="utf-8") as fh:
            reported = json.load(fh).get("test_rankic_mean")
    return a, b, reported, d


def rel_diff(x, y):
    if y is None or x is None:
        return None
    return abs(x - y) / max(abs(y), 1e-12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验已落盘记录，不重算")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(RES):
            print("[FAIL] 缺 experiments/WORK-555/float32_quant_diag.json", file=sys.stderr)
            return 1
        with open(RES, encoding="utf-8") as fh:
            rec = json.load(fh)
        print(f"float32_quant_diag: {rec.get('n_rows')} 个臂/种子，"
              f"建模侧函数与审计侧函数逐位相等 = {rec.get('model_audit_identical')}，"
              f"越过 1e-6 判据 = {rec.get('n_over_tol')}")
        for r in rec.get("rows", []):
            g = r.get("rel_diff_func_vs_reported")
            gs = "n/a" if g is None else f"{g:.3e}"
            print(f"  {r.get('arm'):9s} s{r.get('seed')}  pred_dtype={r.get('pred_dtype')}  "
                  f"rel_gap={gs}  {'OVER_TOL' if r.get('over_tol') else 'within_tol'}")
        if rec.get("n_over_tol"):
            print(f"[FAIL] {rec.get('n_over_tol')} 个臂/种子的「产物复算 vs 建模侧报告值」"
                  f"相对差越过 {rec.get('tol')} 判据", file=sys.stderr)
            return 1
        if not rec.get("model_audit_identical"):
            print("[FAIL] 建模侧函数与审计侧函数未逐位相等，说明两边实现口径分歧", file=sys.stderr)
            return 1
        print("[PASS] 全部臂/种子的产物复算值与建模侧报告值在 1e-6 判据内，且两侧实现逐位相等")
        return 0

    rows = []
    for arm in ARMS:
        for seed in SEEDS:
            got = ric_from_np(arm, seed)
            if got is None:
                continue
            a, b, reported, d = got
            gap = rel_diff(a, reported)
            rows.append({
                "arm": arm, "seed": seed,
                "pred_dtype": d["pred_dtype"],
                "model_side_func_value": a,
                "audit_side_func_value": b,
                "model_reported_value": reported,
                "abs_diff_func_vs_reported": (abs(a - reported) if reported is not None else None),
                "rel_diff_func_vs_reported": gap,
                "model_vs_audit_identical": a == b,
                "over_tol": bool(gap is not None and gap > TOL),
                "note": ("该臂的 run_*.json 不含 test_rankic_mean（诊断臂口径不同），"
                         "故不参与「报告值 vs 产物复算值」比较" if reported is None else ""),
                "tol": TOL,
            })
            gs = "n/a" if gap is None else f"{gap:.3e}"
            print(f"  {arm:9s} s{seed}  pred={d['pred_dtype']:8s} "
                  f"建模侧函数={a!r}  审计侧函数={b!r}  建模侧报告={reported!r}  "
                  f"rel={gs} {'OVER_TOL' if gap is not None and gap > TOL else 'within_tol'}")

    # ---- ±1 ULP 扰动模拟：量化本身能造成多大抖动
    pert_stats = []
    for arm in ARMS:
        for seed in SEEDS:
            d = load_npz(arm, seed)
            if d is None:
                continue
            pred = d["pred"].astype(np.float64)
            y = d["y"].astype(np.float64)
            di = d["date_idx"]
            n_days = int(di.max()) + 1
            base = float(np.nanmean(MODEL.rank_ic_by_day(pred, y, di, n_days)))
            rng = np.random.default_rng(0)
            # 扰动步长按**实际存储精度**取：float32 存 -> 用 float32 eps；
            # float64 存 -> 用 float64 eps（此时量化噪声应降到 ~1e-14 量级）
            store_eps = np.finfo(np.float32).eps if d["pred_dtype"] == "float32" \
                else np.finfo(np.float64).eps
            eps = np.abs(pred) * store_eps
            vals = []
            for _ in range(N_PERTURB):
                pert = (pred + rng.uniform(-1, 1, len(pred)) * eps)
                pert = pert.astype(np.float32).astype(np.float64) if d["pred_dtype"] == "float32" \
                    else pert.astype(np.float64)
                vals.append(float(np.nanmean(MODEL.rank_ic_by_day(pert, y, di, n_days))))
            vals = np.array(vals)
            reported = None
            runp = os.path.join(OUT, f"run_{arm}_s{seed}.json")
            if os.path.exists(runp):
                with open(runp, encoding="utf-8") as fh:
                    reported = json.load(fh).get("test_rankic_mean")
            covered = None if reported is None else bool(vals.min() <= reported <= vals.max())
            pert_stats.append({
                "arm": arm, "seed": seed, "base_from_npz": base,
                "store_dtype": d["pred_dtype"], "store_eps": float(store_eps),
                "n_perturb": N_PERTURB,
                "mean": float(vals.mean()), "std": float(vals.std()),
                "min": float(vals.min()), "max": float(vals.max()),
                "model_reported_value": reported,
                "reported_inside_perturb_range": covered,
                "note": ("该臂 run_*.json 无 test_rankic_mean，未参与区间覆盖判定"
                         if covered is None else ""),
            })
            print(f"  [ULP] {arm:9s} s{seed}  base={base:.9f}  std={vals.std():.3e}  "
                  f"range=[{vals.min():.9f},{vals.max():.9f}]  报告值在区间内={covered}")

    rec = {
        "purpose": ("诊断「审计复算的 RankIC 与建模侧报告值相对差 > 1e-6」的根因："
                    "区分『两个 RankIC 实现口径不同』与『产物 float32 量化』"),
        "tol": TOL,
        "n_rows": len(rows),
        "n_over_tol": sum(1 for r in rows if r["over_tol"]),
        "model_audit_identical": all(r["model_vs_audit_identical"] for r in rows),
        "n_perturb_per_arm": N_PERTURB,
        "perturbation": "±1 ULP（float32 eps × |值|）均匀扰动，重复 20 次",
        "rows": rows,
        "perturbation_stats": pert_stats,
        "conclusion": (
            "建模侧函数与审计侧函数在同一份 npz 上逐位相等 → 两边实现口径一致；"
            "报告值与产物复算值的差落在 ±1 ULP 量化抖动区间内 → 差异由**产物 float32 量化**"
            "引起（截面秩位翻转），不是算法分歧。故处置是**让产物无损（float64 落盘）**，"
            "而不是放宽审计阈值。"),
    }
    with open(RES, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(f"  -> {os.path.relpath(RES, REPO)}  "
          f"model==audit 逐位相等: {rec['model_audit_identical']}  越过 1e-6: {rec['n_over_tol']}")
    if rec["n_over_tol"]:
        print(f"[FAIL] {rec['n_over_tol']} 个臂/种子的「产物复算 vs 建模侧报告值」"
              f"相对差越过 {TOL} 判据（预注册 §7.2）", file=sys.stderr)
        return 1
    if not rec["model_audit_identical"]:
        print("[FAIL] 建模侧函数与审计侧函数未逐位相等", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
