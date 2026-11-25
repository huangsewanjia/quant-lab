# -*- coding: utf-8 -*-
"""WORK-566 · 主检验量与判词（H1 / H2 / H3 / H4）+ 基准调整 alpha + 论文对照表。

判词规则（预注册 §11，任务书原样抄写）：H1 = ΔRankIC = RankIC_full − RankIC_s1_only，
取跨种子中位数；≥+3.0pp 判「进」，≤+1.0pp 判「退」，(1.0, 3.0) 判「部分复现」。
H2：full vs FMB-style 线性基线的 RankIC 差，10000 次配对 block bootstrap 的 95% CI。
H3：净 SR 的 95% CI 下界 >0 且相对论文缩水 ≤60%（降级配置下该项只披露不判定）。
H4：R2（加修正）vs R3（加等维上下文），只报数。

产出：experiments/WORK-566/{metrics.json, metrics_table.csv, paper_comparison.md,
paper_comparison.csv, verdict.json}

运行：python scripts/work566_primary_test.py --seeds 42 2024
"""
import argparse
import csv
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")

ARMS = ["s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3"]
COSTS = [0.0005, 0.0015, 0.0030]
BOOT_N = 10000
BOOT_BLOCK = 20
BOOT_SEED = 42

# 论文数字（arXiv:2610.07006v1 原文核对，见 paper_excerpt）
PAPER = {
    "table1": {
        "Stock-JEPA": {"IC": 10.70, "RankIC": 11.22, "NetAR": 45.66, "NetSR": 1.50, "MDD": -31.09},
        "MASTER": {"IC": 7.34, "RankIC": 9.84, "NetAR": 41.81, "NetSR": 1.23, "MDD": -21.46},
        "FMB-style": {"IC": 5.09, "RankIC": 8.76, "NetAR": 9.51, "NetSR": 0.67, "MDD": -16.36},
        "IPCA": {"IC": 7.19, "RankIC": 8.69, "NetAR": 20.99, "NetSR": 0.78, "MDD": -27.02},
    },
    "table4": {
        "s1_only": {"RankIC": 5.15, "NetAR": 14.03, "NetSR": 0.55},
        "s2_only": {"RankIC": 6.21, "NetAR": 21.43, "NetSR": 0.85},
        "s3_only": {"RankIC": 6.76, "NetAR": 21.04, "NetSR": 0.89},
        "s1_s2": {"RankIC": 9.94, "NetAR": 33.86, "NetSR": 1.10},
        "full": {"RankIC": 11.22, "NetAR": 45.66, "NetSR": 1.50},
    },
    "table5": {
        "r0": {"RankIC": 9.72, "NetSR": 1.47},
        "r1": {"RankIC": 9.24, "NetSR": 1.46},
        "r2": {"RankIC": 11.26, "NetSR": 1.87},
        "r3": {"RankIC": 10.13, "NetSR": 1.79},
    },
    "table3": {"securities": 5669, "valid_samples": 8839281, "train": 3883006,
               "valid": 1990842, "test": 2965433, "test_dates": "2024-01-02–2026-06-26"},
}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def block_bootstrap_ci(x, stat=np.mean, n=BOOT_N, block=BOOT_BLOCK, seed=BOOT_SEED):
    """配对 block bootstrap：返回 (点估计, lo95, hi95)。"""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    if len(x) < block:
        return (float(stat(x)) if len(x) else None, None, None)
    rng = np.random.default_rng(seed)
    nblocks = int(np.ceil(len(x) / block))
    starts = np.arange(0, len(x) - block + 1)
    out = np.empty(n)
    for i in range(n):
        pick = rng.choice(starts, size=nblocks, replace=True)
        idx = (pick[:, None] + np.arange(block)[None, :]).ravel()[:len(x)]
        out[i] = stat(x[idx])
    return float(stat(x)), float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def sharpe_from(r):
    r = np.asarray(r, dtype=np.float64)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return None
    sd = r.std(ddof=1)
    return float(np.sqrt(252.0) * r.mean() / sd) if sd > 0 else None


def load_backtest(arm, seed, cost):
    p = os.path.join(OUT, f"backtest_{arm}_seed{seed}_c{cost}.json")
    if not os.path.exists(p):
        return None, None
    with open(p, encoding="utf-8") as f:
        j = json.load(f)
    s = np.load(p.replace(".json", "_series.npz"))
    return j, s


def benchmarks():
    """等权全市场与市值加权全市场的日收益序列（测试期）。"""
    import duckdb
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    r1 = np.load(os.path.join(DATA, "ret", "r_1.npy"), mmap_mode="r")
    has = np.load(os.path.join(DATA, "base", "has_bar.npy"), mmap_mode="r")
    cmv = np.load(os.path.join(DATA, "base", "circ_mv.npy"), mmap_mode="r")
    s = np.load(os.path.join(DATA, "samples.npz"))
    te = np.nonzero(s["m_test"])[0]
    dts = s["dts"][te]
    udates = np.unique(dts)
    # 日期下标：样本里的 date_idx
    didx = s["date_idx"][te]
    out = {}
    for d in np.unique(didx):
        col = np.asarray(r1[:, d], dtype=np.float64)
        hb = np.asarray(has[:, d])
        m = hb & np.isfinite(col)
        ew = col[m].mean() if m.any() else 0.0
        w = np.asarray(cmv[:, d], dtype=np.float64)[m]
        cw = (w * col[m]).sum() / w.sum() if w.sum() > 0 else ew
        out[int(d)] = (float(ew), float(cw))
    return out, didx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 2024])
    args = ap.parse_args()

    metrics = {"experiment": "WORK-566", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "paper": PAPER, "arms": {}, "cost_tiers": {}}

    # ---------------- 各臂各成本档
    for cost in COSTS:
        tier = {}
        for arm in ARMS + ["fmb"]:
            per_seed = {}
            for seed in args.seeds:
                j, s = load_backtest(arm, seed, cost)
                if j is None:
                    continue
                per_seed[str(seed)] = {k: j[k] for k in
                                       ("rankic_mean_pct", "rankic_std_pct", "rankicir", "net_ar",
                                        "net_sr", "mdd", "avg_turnover", "n_batches", "n_days",
                                        "n_skipped_limitup", "n_skipped_suspend", "deferred_exits",
                                        "delist_liquidations", "total_return")
                                       if k in j}
                per_seed[str(seed)]["_series_file"] = os.path.basename(
                    p.replace(".json", "_series.npz")) if (p := os.path.join(
                        OUT, f"backtest_{arm}_seed{seed}_c{cost}.json")) else None
            if per_seed:
                tier[arm] = per_seed
        metrics["cost_tiers"][str(cost)] = tier
        log(f"成本档 c={cost}: {sorted(tier)}")

    def agg(arm, cost, key):
        vals = [v[key] for v in metrics["cost_tiers"][str(cost)].get(arm, {}).values()
                if key in v and v[key] is not None]
        if not vals:
            return None, None, None
        return float(np.mean(vals)), float(min(vals)), float(max(vals))

    # ---------------- H1
    main_cost = COSTS[0]
    h1_rows = []
    for seed in args.seeds:
        jf, _ = load_backtest("full", seed, main_cost)
        j1, _ = load_backtest("s1_only", seed, main_cost)
        if jf and j1:
            h1_rows.append({"seed": seed,
                            "rankic_full": jf["rankic_mean_pct"],
                            "rankic_s1_only": j1["rankic_mean_pct"],
                            "delta_rankic_pp": jf["rankic_mean_pct"] - j1["rankic_mean_pct"]})
    deltas = [r["delta_rankic_pp"] for r in h1_rows]
    h1 = {"per_seed": h1_rows,
          "n_seeds": len(h1_rows),
          "delta_median_pp": float(np.median(deltas)) if deltas else None,
          "delta_min_pp": float(min(deltas)) if deltas else None,
          "delta_max_pp": float(max(deltas)) if deltas else None,
          "paper_delta_pp": 6.07,
          "paper_note": "论文 Table 4：S1-only 5.15% → full 11.22% = +6.07pp（已对原文核对）"}
    # 预注册 §9：所有指标报 mean ± range，**只报单种子结果 = 未交付**（2026-11-25 审查 N6：补代码守卫）
    # 预注册失败判据 3：**两个种子均 ≤ +1.0 → 判退**
    n_seeds = len(h1_rows)
    if n_seeds < 2:
        verdict = "未交付（有效种子 < 2）"
        h1["verdict_rule"] = "预注册 §9「只报单种子结果 = 未交付」"
    elif h1["delta_median_pp"] is None:
        verdict = "数据不可用"
        h1["verdict_rule"] = "无 full/s1_only 配对结果"
    elif all(d <= 1.0 for d in deltas):
        verdict = "未复现"
        h1["verdict_rule"] = "预注册失败判据 3：两个种子 ΔRankIC 均 ≤ +1.0pp → 判退"
    elif h1["delta_median_pp"] >= 3.0:
        verdict = "三段式增量复现成立"
        h1["verdict_rule"] = "预注册 §11 H1：跨种子中位数 ≥ +3.0pp"
    elif h1["delta_median_pp"] <= 1.0:
        verdict = "未复现"
        h1["verdict_rule"] = "预注册 §11 H1：跨种子中位数 ≤ +1.0pp（或负）"
    else:
        verdict = "部分复现"
        h1["verdict_rule"] = "预注册 §11 H1：跨种子中位数落在 (1.0, 3.0)pp"
    h1["verdict"] = verdict
    metrics["H1"] = h1
    log(f"H1 ΔRankIC 中位数 = {h1['delta_median_pp']} pp（论文 +6.07；种子数 {n_seeds}）→ {verdict}")

    # ---------------- H2（block bootstrap）
    h2 = {"n_boot": BOOT_N, "block": BOOT_BLOCK, "seed": BOOT_SEED}
    diffs = []
    for seed in args.seeds:
        _, sf = load_backtest("full", seed, main_cost)
        _, sb = load_backtest("fmb", seed, main_cost)
        if sf is None or sb is None:
            continue
        dm = dict(zip(sf["ic_dates"].tolist(), sf["ic_vals"].tolist()))
        bm = dict(zip(sb["ic_dates"].tolist(), sb["ic_vals"].tolist()))
        common = sorted(set(dm) & set(bm))
        if len(common) < BOOT_BLOCK:
            continue
        d = np.asarray([dm[k] - bm[k] for k in common]) * 100.0
        diffs.append(d)
        point, lo, hi = block_bootstrap_ci(d)
        h2.setdefault("per_seed", []).append(
            {"seed": seed, "n_days": len(common), "mean_diff_pp": point, "ci_lo": lo, "ci_hi": hi})
    if diffs:
        all_diff = np.concatenate(diffs)
        point, lo, hi = block_bootstrap_ci(all_diff)
        h2["pooled_mean_diff_pp"] = point
        h2["pooled_ci95"] = [lo, hi]
        h2["ci_contains_zero"] = bool(lo <= 0 <= hi)
        h2["conclusion"] = ("增量统计上不可证（CI 含 0）—— 不算失败，报告原样写这句话"
                            if h2["ci_contains_zero"] else "增量统计上可证（CI 不含 0）")
    # N-R9（2026-11-25 第 3 轮复审）：H1 有「种子 < 2 → 未交付」守卫，H2 原来没有 →
    # 缺一个种子的 fmb 预测时，H2 会用**单种子**照常出结论。补同等守卫。
    h2["n_seeds"] = len(diffs)
    if len(diffs) < 2:
        h2["conclusion"] = "未交付（有效种子 < 2）—— 预注册 §9「只报单种子结果 = 未交付」"
        h2["delivered"] = False
    else:
        h2["delivered"] = True
    metrics["H2"] = h2
    log(f"H2 full vs FMB-style: {h2.get('pooled_mean_diff_pp')} pp, CI {h2.get('pooled_ci95')}"
        f"（有效种子 {h2['n_seeds']}）")

    # ---------------- H3（可交易性）
    h3 = {}
    for arm in ("full", "s1_only", "s1_s2", "fmb"):
        per = []
        for seed in args.seeds:
            j, s = load_backtest(arm, seed, main_cost)
            if j is None or s is None:
                continue
            r = s["ret"]
            point, lo, hi = block_bootstrap_ci(r, stat=sharpe_from)
            per.append({"seed": seed, "net_sr": j["net_sr"], "net_ar": j["net_ar"],
                        "mdd": j["mdd"], "avg_turnover": j["avg_turnover"],
                        "sr_ci95": [lo, hi]})
        if per:
            h3[arm] = per
            # N-R9：H3 与 H1/H2 同口径 —— 有效种子 < 2 的臂标「未交付」，不参与结论
            h3.setdefault("delivered", {})[arm] = len(per) >= 2
    if h3.get("full"):
        los = [p["sr_ci95"][0] for p in h3["full"] if p["sr_ci95"][0] is not None]
        srs = [p["net_sr"] for p in h3["full"] if p["net_sr"] is not None]
        paper_sr = PAPER["table1"]["Stock-JEPA"]["NetSR"]
        h3["summary"] = {
            "full_net_sr_mean": float(np.mean(srs)) if srs else None,
            "full_sr_ci95_lower_min": float(min(los)) if los else None,
            "sr_ci_lower_gt_0": bool(los and min(los) > 0),
            "paper_net_sr": paper_sr,
            "shrinkage_vs_paper": (float(np.mean(srs)) / paper_sr) if (srs and paper_sr) else None,
            "note": ("算力降级配置下「相对论文缩水 ≤60%」只披露不判定（预注册 §11）；"
                     "净 SR < 0 → 直接判退 仍然生效"),
        }
        if srs and float(np.mean(srs)) < 0:
            verdict = "未复现（H3：跨种子平均净 SR < 0，直接判退）"
            metrics["verdict"] = verdict
    metrics["H3"] = h3
    log(f"H3 full 净 SR = {h3.get('summary', {}).get('full_net_sr_mean')}")

    # ---------------- H4（R2 vs R3）
    h4 = {}
    for arm in ("r0", "r1", "r2", "r3"):
        per = []
        for seed in args.seeds:
            j, _ = load_backtest(arm, seed, main_cost)
            if j is None:
                continue
            per.append({"seed": seed, "rankic_mean_pct": j["rankic_mean_pct"],
                        "net_sr": j["net_sr"], "net_ar": j["net_ar"]})
        if per:
            h4[arm] = per
    h4["n_seeds"] = {a: len(v) for a, v in h4.items() if isinstance(v, list)}
    h4["delivered"] = all(v >= 2 for v in h4["n_seeds"].values()) if h4["n_seeds"] else False
    if h4.get("r2") and h4.get("r3"):
        r2r = float(np.mean([p["rankic_mean_pct"] for p in h4["r2"]]))
        r3r = float(np.mean([p["rankic_mean_pct"] for p in h4["r3"]]))
        r2s = float(np.mean([p["net_sr"] for p in h4["r2"]]))
        r3s = float(np.mean([p["net_sr"] for p in h4["r3"]]))
        h4["summary"] = {"r2_rankic": r2r, "r3_rankic": r3r, "r2_minus_r3_rankic_pp": r2r - r3r,
                         "r2_net_sr": r2s, "r3_net_sr": r3s, "r2_minus_r3_net_sr": r2s - r3s,
                         "paper": PAPER["table5"]}
    metrics["H4"] = h4
    log(f"H4 R2 {h4.get('summary', {}).get('r2_rankic')} vs R3 {h4.get('summary', {}).get('r3_rankic')}")

    # ---------------- 基准调整 alpha
    try:
        bm, didx = benchmarks()
        alpha = {}
        for arm in ("full", "s1_only", "s1_s2", "fmb"):
            per = []
            for seed in args.seeds:
                j, s = load_backtest(arm, seed, main_cost)
                if j is None or s is None:
                    continue
                dates = sorted(bm.keys())
                nav = s["nav"]
                # 用日收益对齐基准：基准按导航日序
                row = {}
                for kind, k in (("ew", 0), ("cap", 1)):
                    bench = np.asarray([bm[d][k] for d in dates], dtype=np.float64)
                    r = s["ret"]
                    n = min(len(bench), len(r))
                    ex = r[:n] - bench[:n]
                    ex = ex[np.isfinite(ex)]
                    if len(ex) < 30:
                        continue
                    row[kind] = {
                        "alpha_ann_pct": float(252.0 * ex.mean()),
                        "t_stat": float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else None,
                        "info_ratio_ann": float(np.sqrt(252.0) * ex.mean() / ex.std(ddof=1)) if ex.std(ddof=1) > 0 else None,
                        "excess_total_pct": float((np.prod(1 + ex) - 1) * 100),
                        "n_days": int(len(ex)),
                    }
                per.append({"seed": seed, **row})
            if per:
                alpha[arm] = per
        metrics["benchmark_alpha"] = alpha
        log("基准调整 alpha 完成")
    except Exception as e:  # noqa: BLE001
        metrics["benchmark_alpha"] = {"error": f"{type(e).__name__}: {e}"}
        log(f"基准调整 alpha 失败：{e}")

    # ---------------- 交付守卫（预注册 §9：只报单种子结果 = 未交付）
    thin = []
    for cost in COSTS:
        for arm in sorted(set(ARMS) | {"fmb"}):
            n = len(metrics["cost_tiers"][str(cost)].get(arm, {}))
            if 0 < n < 2:
                thin.append({"cost": cost, "arm": arm, "n_seeds": n})
    metrics["seed_guard"] = {
        "rule": "预注册 §9：所有指标报 mean ± range（两档种子）；只报单种子结果 = 未交付",
        "thin_arms": thin,
        "all_present_arms_have_2_seeds": not thin,
    }
    if thin:
        verdict = (verdict + f"（注意：{len(thin)} 个臂×成本档只有 1 个种子，"
                             f"按预注册 §9 该档次结果按未交付处理）")

    # ---------------- 结论
    metrics["verdict"] = verdict
    metrics["verdict_rule"] = {
        "进": "ΔRankIC ≥ +3.0 pp",
        "退": "ΔRankIC ≤ +1.0 pp 或为负",
        "部分复现": "+1.0 < ΔRankIC < +3.0 pp",
        "H3_override": "净 SR < 0 → 直接判退",
        "失败判据3": "两个种子 ΔRankIC 均 ≤ +1.0 pp → 判退",
        "种子守卫": "有效种子 < 2 → 未交付（不判进/退）",
    }
    with open(os.path.join(OUT, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # ---------------- 人读表
    rows = []
    for cost in COSTS:
        for arm in ARMS + ["fmb"]:
            mean_r, min_r, max_r = agg(arm, cost, "rankic_mean_pct")
            sr_m, sr_lo, sr_hi = agg(arm, cost, "net_sr")
            ar_m, _, _ = agg(arm, cost, "net_ar")
            mdd_m, _, _ = agg(arm, cost, "mdd")
            to_m, _, _ = agg(arm, cost, "avg_turnover")
            if mean_r is None:
                continue
            rows.append({
                "成本(单边bps)": cost * 1e4, "臂": arm,
                "RankIC(%)mean": mean_r, "RankIC range": f"[{min_r:.4f}, {max_r:.4f}]",
                "净SR mean": sr_m, "净SR range": f"[{sr_lo:.4f}, {sr_hi:.4f}]",
                "净AR(%)": ar_m, "MDD(%)": mdd_m, "日均换手": to_m,
                "种子数": len(metrics["cost_tiers"][str(cost)].get(arm, {})),
            })
    with open(os.path.join(OUT, "metrics_table.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---------------- 论文对照表
    comp = []
    for arm, key in (("s1_only", "s1_only"), ("s3_only", "s3_only"), ("s1_s2", "s1_s2"),
                     ("full", "full")):
        for field, pfield in (("rankic_mean_pct", "RankIC"), ("net_ar", "NetAR"), ("net_sr", "NetSR")):
            pm, plo, phi = agg(arm, main_cost, field)
            pv = PAPER["table4"][key][pfield]
            comp.append({
                "本单口径": f"{arm} · {field}", "论文表号": "Table 4", "论文值": pv, "本单值": pm,
                "本单range": f"[{plo:.4f}, {phi:.4f}]" if pm is not None else "",
                "差异": (pm - pv) if pm is not None else None,
                "差异原因": ("40 维字段自定 + 算力降级（d_model 64/2 层）+ 股票池 + 成本模型"
                             if arm != "full" else
                             "40 维字段自定 + 算力降级（d_model 64/2 层，d_z 64）+ 股票池 + A 股执行约束"),
            })
    for arm in ("r0", "r1", "r2", "r3"):
        pm, plo, phi = agg(arm, main_cost, "rankic_mean_pct")
        pv = PAPER["table5"][arm]["RankIC"]
        comp.append({"本单口径": f"{arm} · rankic", "论文表号": "Table 5", "论文值": pv, "本单值": pm,
                     "本单range": f"[{plo:.4f}, {phi:.4f}]" if pm is not None else "",
                     "差异": (pm - pv) if pm is not None else None,
                     "差异原因": "同 Table 4 偏离项（读出特征集与论文一致：s/P/anchor/revision/context）"})
    with open(os.path.join(OUT, "paper_comparison.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(comp[0].keys()))
        w.writeheader()
        w.writerows(comp)

    # 人读版（Markdown）
    md = ["# WORK-566 与论文逐项对照", "",
          "论文原文：STOCK-JEPA，arXiv:2610.07006v1（表号见表头）。",
          "本单口径：测试期 2024-01-02 ~ 2026-06-26，主成本档 单边 5bps，",
          "跨种子 mean ± range（种子 42 / 2024）。", "",
          "| 本单口径 | 论文表号 | 论文值 | 本单值 | 本单 range | 差异 | 差异原因 |",
          "|---|---|---|---|---|---|---|"]
    for r in comp:
        pv = r.get("论文值")
        mine = r["本单值"]
        diff = r["差异"]
        md.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            r["本单口径"], r.get("论文表号", "—"),
            pv if pv is not None else "—",
            f"{mine:.4f}" if isinstance(mine, (int, float)) else "—",
            r["本单range"] or "—",
            f"{diff:+.4f}" if isinstance(diff, (int, float)) else "—",
            r["差异原因"]))
    md += ["", "## 不可比性声明", "",
           "本单的 40 维特征字段为**自定**（论文未给字段名），市场级 12 维亦为自定，",
           "且模型为算力降级配置（d_model 64 / 2 层 / 4 头 / FFN 128 / d_z 64 / fp32）、",
           "股票池口径不同、并叠加 A 股执行约束（涨停不可买 / 跌停顺延 / 停牌顺延 / 退市 1% 清算）。",
           "因此**绝对水平与论文不可比**；H1 只判**相对增量**。"]
    with open(os.path.join(OUT, "paper_comparison.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    with open(os.path.join(OUT, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict, "H1": h1, "H2": {k: v for k, v in h2.items() if k != "per_seed"},
                   "H3": h3.get("summary"), "H4": h4.get("summary"),
                   "benchmark_alpha": metrics.get("benchmark_alpha")},
                  f, ensure_ascii=False, indent=2)
    log(f"判词：{verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
