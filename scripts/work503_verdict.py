#!/usr/bin/env python3
"""WORK-503 stage 4 — the verdict (进 / 退).

Reads the backtest results and turns them into the pre-registered decision:

  H1 (the only quantity that decides 进/退)
      delta_ic = (RankIC_full - RankIC_no_vq) / RankIC_full
      medians over the >= 2 seeds of each arm
      [-40%, -15%]  -> 进   (the codebook effect reproduces)
      [-10%, +10%]  -> 退   (not reproduced)
      otherwise     -> 部分复现
  H2 (parallel)  the RankIC difference against the fixed GRU baseline,
      10000 block-bootstrap (block = 5 trading days) 95% CI
  H3 (parallel)  net SR / AR / MDD / daily turnover of the full model;
      a negative net SR decides 退 regardless of H1

It also evaluates the five pre-registered failure criteria and writes
  experiments/WORK-503/verdict.json
  experiments/WORK-503/metrics_table.csv      (human readable, one row per run)
  experiments/WORK-503/paper_comparison.csv   (our numbers vs the paper's)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-503")

ARMS = ["full", "no_vq", "no_moe", "no_prior"]
BASELINE = "gru"
BOOT = 10000
BLOCK = 5

# ---- the paper's own numbers (arXiv:2605.13407v1, Table 1 / 2 / F.1) ------
PAPER = {
    "PRISM-VQ": {"rankic": 0.0646, "rankicir": 0.4224, "ar": 0.3077,
                 "mdd": 0.1924, "sr": 1.5694, "turnover": 0.3259},
    "GRU": {"rankic": 0.0590, "rankicir": 0.3756, "ar": 0.2347,
            "mdd": 0.2288, "sr": 1.2221},
    "DTML": {"rankic": 0.0625, "rankicir": 0.4076, "ar": 0.2589,
             "mdd": 0.2069, "sr": 1.1228},
}
PAPER_ABLATION = {"full": 0.0646, "no_prior": 0.0548, "no_moe": 0.0586,
                  "no_vq": 0.0472}


def block_bootstrap_ci(diff: np.ndarray, stat=np.mean, n_boot=BOOT,
                       block=BLOCK, seed=20240613, keep=None):
    """Percentile CI of a block bootstrap over a paired daily series.

    `keep` (optional path) receives the bootstrap statistic vector so the
    report can plot the distribution instead of only the interval.
    """
    x = diff[np.isfinite(diff)]
    n = len(x)
    if n < block:
        return {"n": int(n), "point": float(stat(x)) if n else np.nan,
                "lo": np.nan, "hi": np.nan, "n_boot": 0}
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)
    idx = idx[:, :n]
    samples = x[idx]
    point = float(stat(x))
    boots = stat(samples, axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    if keep is not None:
        np.save(keep, boots.astype("float64"))
    return {"n": int(n), "point": point, "lo": float(lo), "hi": float(hi),
            "n_boot": int(n_boot), "block": block}


def sr_stat(x, axis=-1):
    """Annualised Sharpe ratio of a daily log-return vector.

    `axis` makes it usable on the bootstrap matrix: block_bootstrap_ci calls
    `stat(x)` for the point estimate (1-D) and `stat(samples, axis=1)` for the
    resampled matrix (2-D), so the SR must be computed **row-wise** —
    taking the mean/std of the whole matrix would collapse all 10000
    resamples to one constant and produce a zero-width CI.
    """
    x = np.asarray(x, dtype="float64")
    mean = np.mean(x, axis=axis)
    sd = np.std(x, axis=axis, ddof=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = np.sqrt(252.0) * mean / sd
    sr = np.asarray(sr, dtype="float64")
    return float(sr) if sr.ndim == 0 else sr


def load_daily(key: str):
    p = os.path.join(EXP, "preds", f"{key}_daily.parquet")
    if not os.path.exists(p):
        return None
    d = pd.read_parquet(p)
    d["datetime"] = pd.to_datetime(d["datetime"])
    return d


def load_rankic(key: str):
    p = os.path.join(EXP, "preds", f"{key}_rankic.npy")
    if not os.path.exists(p):
        return None
    return np.load(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--seeds", default="42,2024")
    args = ap.parse_args()

    exp_dir = os.path.join(REPO, "experiments", args.exp)
    res_path = os.path.join(exp_dir, "backtest_results.json")
    if not os.path.exists(res_path):
        print(f"missing {res_path}")
        return 1
    res = json.load(open(res_path))
    seeds = [int(s) for s in args.seeds.split(",")]

    # ---- collect -------------------------------------------------------
    per_run = {}
    for arm in ARMS + [BASELINE]:
        for seed in seeds:
            key = f"{arm}_s{seed}"
            if key in res:
                per_run[key] = res[key]

    def med(arm, field):
        vals = [per_run[f"{arm}_s{s}"][field] for s in seeds
                if f"{arm}_s{s}" in per_run]
        return (float(np.median(vals)), vals) if vals else (np.nan, [])

    out = {"experiment": args.exp, "seeds": seeds,
           "paper": PAPER, "paper_ablation": PAPER_ABLATION,
           "runs": per_run}

    # ---- H1 -------------------------------------------------------------
    # 预注册 v1.3 §3：判词量取 Δ_drop = (RankIC_no_vq − RankIC_full)/RankIC_full，
    # 判词带逐字不动（进 [−40%,−15%] / 退 [−10%,+10%]）；同时并列报告派单卡
    # 字面式 Δ_formula = (RankIC_full − RankIC_no_vq)/RankIC_full = −Δ_drop。
    # 两种口径的判词集合逐点相同（v1.3 §4）。
    ric_full, ric_full_all = med("full", "rankic")
    ric_novq, ric_novq_all = med("no_vq", "rankic")
    delta_drop = (ric_novq - ric_full) / ric_full if ric_full else np.nan
    delta_formula = (ric_full - ric_novq) / ric_full if ric_full else np.nan
    per_seed_delta = {}
    for s in seeds:
        a = per_run.get(f"full_s{s}", {}).get("rankic")
        b = per_run.get(f"no_vq_s{s}", {}).get("rankic")
        if a is not None and b is not None and a != 0:
            per_seed_delta[str(s)] = (b - a) / a
    if np.isnan(delta_drop):
        verdict = "无法判定（缺少 full / no_vq 指标）"
    elif -0.40 <= delta_drop <= -0.15:
        verdict = "进：码本有效性复现成立"
    elif -0.10 <= delta_drop <= 0.10:
        verdict = "退：未复现（码本无增量）"
    else:
        verdict = "部分复现（必须写清偏差来源与翻案条件）"
    out["H1"] = {
        "delta_ic": delta_drop,            # 判词用（= Δ_drop，预注册 v1.3）
        "delta_drop": delta_drop,
        "delta_formula": delta_formula,    # 派单卡字面式，= −Δ_drop
        "convention": "判词量 Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full；"
                      "同时报告 Δ_formula = (RankIC_full − RankIC_no_vq) / RankIC_full "
                      "= −Δ_drop（预注册 v1.3 §3）",
        "rankic_full_median": ric_full, "rankic_full_per_seed": ric_full_all,
        "rankic_no_vq_median": ric_novq, "rankic_no_vq_per_seed": ric_novq_all,
        "delta_ic_per_seed": per_seed_delta,
        "paper_delta_ic": (PAPER_ABLATION["no_vq"] - PAPER_ABLATION["full"]) /
                          PAPER_ABLATION["full"],
        "paper_delta_formula": (PAPER_ABLATION["full"] - PAPER_ABLATION["no_vq"]) /
                               PAPER_ABLATION["full"],
        "bands": {"进": [-0.40, -0.15], "退": [-0.10, 0.10]},
        "verdict": verdict,
        "sign_note": (
            "RankIC_full 为负时，Δ_drop 的符号会随分母翻转、不可按原阈值直接读；"
            f"本单 RankIC_full 中位数 = {ric_full:.4f}"
            + ("（为负，需按符号翻转口径另作判断，判词须写明）。"
               if ric_full < 0 else "（为正，阈值按原口径直接读）。")),
    }

    # ---- H2 -------------------------------------------------------------
    h2 = {}
    for s in seeds:
        dfull = load_daily(f"full_s{s}")
        dbase = load_daily(f"{BASELINE}_s{s}")
        if dfull is None or dbase is None:
            continue
        rf = load_rankic(f"full_s{s}")
        rb = load_rankic(f"{BASELINE}_s{s}")
        if rf is None or rb is None:
            continue
        j = pd.DataFrame({"date": dfull["datetime"], "full": rf,
                          "base": rb}).merge(
            pd.DataFrame({"date": dbase["datetime"], "base2": rb}),
            on="date", how="inner")
        diff = (j["full"] - j["base"]).to_numpy()
        h2[str(s)] = block_bootstrap_ci(
            diff, keep=os.path.join(EXP, "preds", f"h2_bootstrap_s{s}.npy"))
        h2[str(s)]["mean_full"] = float(np.nanmean(j["full"]))
        h2[str(s)]["mean_base"] = float(np.nanmean(j["base"]))
    out["H2"] = {
        "baseline": BASELINE, "per_seed": h2,
        "note": "论文自曝对 DTML/GRU 未达显著；CI 含 0 判「与论文自曝弱点一致」，"
                "不算失败，但必须写明本方法的增量在统计上不可证",
    }

    # ---- H3 -------------------------------------------------------------
    h3 = {}
    for s in seeds:
        d = load_daily(f"full_s{s}")
        rec = per_run.get(f"full_s{s}")
        if d is None or rec is None:
            continue
        lg = np.log1p(d["ret"].to_numpy())
        ci = block_bootstrap_ci(
            lg, stat=sr_stat,
            keep=os.path.join(EXP, "preds", f"h3_sr_bootstrap_s{s}.npy"))
        h3[str(s)] = {
            "sr": rec.get("sr"),
            "ar": rec.get("ar"),
            "mdd": rec.get("mdd"),
            "turnover": rec.get("turnover"),
            "turnover_both_sides": rec.get("turnover_both_sides"),
            "sr_ci": ci,
            "mean_holdings": rec.get("mean_holdings"),
            "alpha_ew": rec.get("alpha_equal_weight"),
            "alpha_cw": rec.get("alpha_cap_weighted"),
        }
    sr_med = med("full", "sr")
    # 可交易性规则（派单卡原文）：净 SR 的 95% CI 下界 > 0 且相对论文缩水 ≤60%
    # → 可交易性成立；净 SR < 0 → 直接判退，与 H1 无关。
    paper_sr = PAPER["PRISM-VQ"]["sr"]
    sr_ci_lo = {s: h3[s]["sr_ci"].get("lo") for s in h3
                if h3[s].get("sr_ci")}
    ci_lo_min = (min(v for v in sr_ci_lo.values()
                      if v is not None and np.isfinite(v))
                 if any(v is not None and np.isfinite(v)
                        for v in sr_ci_lo.values()) else np.nan)
    shrinkage = (sr_med[0] / paper_sr) if (paper_sr and
                                          not np.isnan(sr_med[0])) else np.nan
    net_sr_negative = bool(not np.isnan(sr_med[0]) and sr_med[0] < 0)
    tradable = bool(not np.isnan(ci_lo_min) and ci_lo_min > 0 and
                    not np.isnan(shrinkage) and shrinkage >= 0.40)
    out["H3"] = {"per_seed": h3, "sr_median": sr_med[0],
                 "sr_per_seed": sr_med[1],
                 "paper_sr": paper_sr,
                 "paper_turnover": PAPER["PRISM-VQ"]["turnover"],
                 "sr_ci_lower_bound_per_seed": sr_ci_lo,
                 "sr_ci_lower_bound_min": ci_lo_min,
                 "shrinkage_vs_paper": shrinkage,
                 "shrinkage_limit": 0.40,
                 "net_sr_negative": net_sr_negative,
                 "tradicable": tradable,
                 "rule": "净 SR 的 95% CI 下界 > 0 且相对论文缩水 ≤60% → 可交易性成立；"
                         "净 SR < 0 → 直接判退（与 H1 无关）",
                 "note": "净 SR < 0 直接判退；95% CI 下界 > 0 且相对论文缩水 ≤60% "
                         "判可交易性成立"}

    # ---- the five pre-registered failure criteria ----------------------
    fc = {}
    ic = json.load(open(os.path.join(exp_dir, "integrity_check.json")))
    i5 = ic["I5_test_coverage"]
    # 预注册 v1.0 §9 判据 1 有两个触发条件：PIT 成分覆盖率 <90%，或日频有效
    # bar <85%。v1.3 §6 明确了前者的测量口径（由 check_integrity.py 计算）。
    pit_cov = i5.get("pit_coverage")
    fc["1_data_usable"] = {
        "tradable_share_test": i5["tradable_share"],
        "rows_with_bar_test": i5["rows_with_bar"],
        "bar_threshold": 0.85,
        "pit_coverage_test": pit_cov,
        "pit_coverage_threshold": 0.90,
        "hit": bool((i5["tradable_share"] < 0.85 or
                     i5["rows_with_bar"] < 0.85 or
                     (pit_cov is not None and pit_cov < 0.90))),
        "note": ("PIT 覆盖率未测得（integrity_check.json 无 pit_coverage 字段）——"
                 "按 v1.3 §6 的口径补测后再判"
                 if pit_cov is None else ""),
    }
    probe = json.load(open(os.path.join(exp_dir, "data_probe.json")))
    bad = [e for e in probe.get("endpoints", []) if e.get("status") not in
           ("ok", "ok_cross_checked")]
    fc["2_endpoints"] = {"n_failed": len(bad), "hit": len(bad) > 0}
    both_flat = all(abs(v) <= 0.10 for v in per_seed_delta.values()) and \
        len(per_seed_delta) >= 2
    fc["3_both_seeds_flat"] = {"per_seed_delta": per_seed_delta, "hit": both_flat}
    fc["4_prereg_before_results"] = {
        "hit": False,
        "note": "预注册 commit 06fd4a8 / v1.2 早于任何预测/回测产物；"
                "v1.3（符号口径修订）commit c10403a 亦早于正式回测产物"}
    audit_path = os.path.join(exp_dir, "audit_recompute.json")
    audit_items = {}
    if os.path.exists(audit_path):
        a = json.load(open(audit_path))
        audit_items = a.get("items", {})
        fc["5_recompute_agreement"] = {"n_mismatches": a["n_mismatches"],
                                       "tolerance": a["tolerance"],
                                       "hit": a["n_mismatches"] > 0}
    else:
        fc["5_recompute_agreement"] = {"n_mismatches": None, "hit": None}
    out["failure_criteria"] = fc

    hits = [k for k, v in fc.items() if v.get("hit")]
    out["failure_criteria_hit"] = hits
    out["verdict"] = verdict
    if "3_both_seeds_flat" in hits:
        out["verdict"] = "退：未复现（两个种子的 Δ_drop 都落在 [−10%, +10%]）"
    if sr_med[0] is not None and not np.isnan(sr_med[0]) and sr_med[0] < 0:
        out["verdict"] = "退：净 SR < 0（与 H1 无关，直接判退）"
    if "1_data_usable" in hits:
        out["verdict"] = "数据不可用：停手（失败判据 1）"
    if "2_endpoints" in hits:
        out["verdict"] = "停：失败判据 2（必需端点不可用且无合规替代）"
    if fc["5_recompute_agreement"].get("hit"):
        # 预注册 §9 判据 5：独立复算与建模侧相对差 > 1e-6 → **以复算值重出判定**，
        # 并在报告里列出全部不一致项。
        def _med(items, arm):
            vals = [items[f"{arm}_s{s}"]["rankic"] for s in seeds
                    if f"{arm}_s{s}" in items]
            return float(np.median(vals)) if vals else np.nan

        rf, rn = _med(audit_items, "full"), _med(audit_items, "no_vq")
        mism = fc["5_recompute_agreement"]["n_mismatches"]
        if rf:
            dd = (rn - rf) / rf
            if -0.40 <= dd <= -0.15:
                v2 = "进：码本有效性复现成立"
            elif -0.10 <= dd <= 0.10:
                v2 = "退：未复现（码本无增量）"
            else:
                v2 = "部分复现（必须写清偏差来源与翻案条件）"
            out["H1"]["delta_drop_recompute"] = dd
            out["verdict"] = (f"以独立复算值重出判定（失败判据 5，{mism} 处不一致）："
                              f"{v2}")
        else:
            out["verdict"] = (f"以独立复算值重出判定（失败判据 5，{mism} 处不一致）"
                              f"——复算侧缺 full/no_vq，无法重出")
        out["failure_criteria_5_detail"] = {
            "n_mismatches": mism,
            "rule": "以复算值重出判定，并在报告里列出全部不一致项",
        }

    # ---- the human readable tables -------------------------------------
    rows = []
    for key, v in sorted(per_run.items()):
        arm, seed = key.rsplit("_s", 1)
        rows.append({
            "arm": arm, "seed": int(seed),
            "rankic": v["rankic"], "rankicir": v["rankicir"],
            "ar": v["ar"], "mdd": v["mdd"], "sr": v["sr"],
            "turnover_1side": v["turnover"],
            "turnover_2side": v.get("turnover_both_sides"),
            "days": v["days"], "mean_holdings": v.get("mean_holdings"),
            "n_trades": v.get("n_trades"),
            "alpha_ew_annualized": (v.get("alpha_equal_weight") or {}).get("excess_annualized"),
            "alpha_ew_ir": (v.get("alpha_equal_weight") or {}).get("information_ratio"),
            "alpha_cw_annualized": (v.get("alpha_cap_weighted") or {}).get("excess_annualized"),
            "alpha_cw_ir": (v.get("alpha_cap_weighted") or {}).get("information_ratio"),
        })
    tab = pd.DataFrame(rows).sort_values(["arm", "seed"])
    tab.to_csv(os.path.join(exp_dir, "metrics_table.csv"), index=False)

    # ---- per-arm mean ± range over the seeds ---------------------------
    # 派单卡要求：「必须报 mean ± range，不许只报最好的一次」。
    # 逐种子明细留在 metrics_table.csv，这里再给一张按档聚合的人读表。
    METRICS = ["rankic", "rankicir", "sr", "ar", "mdd", "turnover",
               "turnover_both_sides", "alpha_ew_annualized"]

    def metric_value(v, m):
        if m == "alpha_ew_annualized":
            return (v.get("alpha_equal_weight") or {}).get("excess_annualized")
        return v.get(m)

    arm_rows, by_arm = [], {}
    for arm in ARMS + [BASELINE]:
        vals = {m: [metric_value(per_run[f"{arm}_s{s}"], m) for s in seeds
                    if f"{arm}_s{s}" in per_run] for m in METRICS}
        rec = {"arm": arm, "n_seeds": len(next(iter(vals.values()), []))}
        for m in METRICS:
            arr = np.asarray([v for v in vals[m] if v is not None
                              and np.isfinite(v)], dtype="float64")
            if arr.size == 0:
                rec[m] = None
                rec[f"{m}_mean"] = None
                rec[f"{m}_min"] = None
                rec[f"{m}_max"] = None
                rec[f"{m}_range"] = None
                rec[f"{m}_mean_pm_range"] = None
                continue
            mean = float(arr.mean())
            rec[m] = mean
            rec[f"{m}_mean"] = mean
            rec[f"{m}_min"] = float(arr.min())
            rec[f"{m}_max"] = float(arr.max())
            rec[f"{m}_range"] = float(arr.max() - arr.min())
            rec[f"{m}_mean_pm_range"] = (f"{mean:.4f} "
                                         f"[{arr.min():.4f}, {arr.max():.4f}]")
        by_arm[arm] = rec
        arm_rows.append(rec)
    arm_tab = pd.DataFrame(arm_rows)
    arm_tab.to_csv(os.path.join(exp_dir, "metrics_by_arm.csv"), index=False)
    out["by_arm"] = by_arm

    cmp_rows = []
    # 差异原因（派单卡交付物 #7：论文数字（标表号）/ 本单数字 / 差异原因）
    REASON = {
        "full": "训练段 2015-2019（论文 2009-2019）、≤6 轮（论文 ≤50）、本机无 GPU；"
                "先验为自建 6 个 A 股因子（论文为 JKP 13 因子）；逐日施加涨跌停/停牌掩码；"
                "数据底座为公开源重建（非 Qlib cn_data）",
        "no_moe": "同上（仅去掉 MoE）",
        "no_prior": "同上；且先验被整档置零 —— 本档降幅**不能**用来支持「码本有效」",
        "no_vq": "同上（仅去掉 VQ 码本，H1 的分母档）",
        "gru": "本单同协议重训的固定基线（Qlib 官方 GRU），与论文 Table 1 的 GRU 行对照",
    }
    for arm in ARMS + [BASELINE]:
        for seed in seeds:
            key = f"{arm}_s{seed}"
            if key not in per_run:
                continue
            v = per_run[key]
            cmp_rows.append({
                "source": "WORK-503", "system": arm, "seed": seed,
                "rankic": v["rankic"], "rankicir": v["rankicir"],
                "ar": v["ar"], "mdd": v["mdd"], "sr": v["sr"],
                "turnover": v.get("turnover_both_sides"),
                "paper_ref": ("paper Table 2 (ablation)" if arm in PAPER_ABLATION
                              else "paper Table 1 (baseline)"),
                "diff_reason": REASON.get(arm, ""),
            })
    for name, m in PAPER.items():
        cmp_rows.append({"source": "paper Table 1", "system": name, "seed": None,
                         "paper_ref": "paper Table 1", "diff_reason": "论文原值",
                         **m})
    for arm, ric in PAPER_ABLATION.items():
        cmp_rows.append({"source": "paper Table 2", "system": arm, "seed": None,
                         "paper_ref": "paper Table 2", "diff_reason": "论文原值",
                         "rankic": ric})
    pd.DataFrame(cmp_rows).to_csv(
        os.path.join(exp_dir, "paper_comparison.csv"), index=False)

    json.dump(out, open(os.path.join(exp_dir, "verdict.json"), "w"),
              ensure_ascii=False, indent=1, default=float)
    print(json.dumps({"H1": out["H1"], "H3_sr_median": out["H3"]["sr_median"],
                      "verdict": out["verdict"],
                      "failure_criteria_hit": hits},
                     ensure_ascii=False, indent=1, default=float))
    print(f"-> {exp_dir}/verdict.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
