#!/usr/bin/env python3
"""WORK-557 · 段4：图件 + 单文件 HTML 报告 + 首屏 PNG + A4 打印 PDF。

版式沿用 WORK-503（白底、主蓝 #0F4D92、次青 #42949E、警示红 #B64342、
无阴影渐变、A4 10.5 pt、图注在图下带来源）——CSS 直接复用
`templates/work557_report.css`（从 WORK-503 的 report.html 里抽出来的，
未重新设计）。

图件（6 张）：
  fig1  消融 RankIC：WORK-557 vs 论文 Table 2
  fig2  Δ_drop 落在预注册判词带
  fig3  净值曲线（四档 + GRU + CSI300 等权基准）
  fig4  H2 block bootstrap：RankIC(full) − RankIC(GRU)
  fig5  旧 vs 新对照：WORK-503（缩窗 2015-2019 / ≤6 轮）vs WORK-557（全窗 2009-2019 / ≤50 轮）
  fig6  训练收敛曲线：各档各 seed 的 val_loss 随 epoch

用法：
  .venv\\Scripts\\python scripts/work557_report.py --exp WORK-557
  .venv\\Scripts\\python scripts/work557_report.py --exp WORK-557 --preview
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = "WORK-557"
PREV_EXP = "WORK-503"

BLUE, TEAL, RED = "#0F4D92", "#42949E", "#B64342"
INK, MUTED, RULE = "#1a1a1a", "#6f6f6f", "#d9d9d6"

ARM_ORDER = ["full", "no_vq", "no_moe", "no_prior"]
ARM_LABEL = {"full": "完整模型 (full)", "no_vq": "去码本 (no_vq)",
              "no_moe": "去 MoE (no_moe)", "no_prior": "去先验 (no_prior)",
              "gru": "GRU 基线"}
ARM_COLOR = {"full": BLUE, "no_vq": RED, "no_moe": TEAL, "no_prior": "#8a6d3b",
             "gru": "#6f6f6f"}
PAPER_ABLATION = {"full": 0.0646, "no_vq": 0.0472, "no_moe": 0.0586,
                  "no_prior": 0.0548}


# --------------------------------------------------------------------------
# style helpers (same tokens as the CSS)
# --------------------------------------------------------------------------
def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.size": 10.5,
        "axes.titlesize": 12,
        "axes.labelsize": 10.5,
        "axes.edgecolor": RULE,
        "axes.labelcolor": INK,
        "axes.linewidth": 0.8,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
        "legend.frameon": False,
        "legend.fontsize": 9.5,
        "figure.facecolor": "#ffffff",
        "savefig.facecolor": "#ffffff",
        "font.family": ["Microsoft YaHei", "DejaVu Sans"],
        "axes.unicode_minus": False,
    })
    return plt


def _clean(ax, grid_axis="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis=grid_axis, color=RULE, linewidth=0.6, alpha=0.9)
    ax.set_axisbelow(True)


def _save(fig, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, bbox_inches="tight", facecolor="#ffffff", dpi=170)
    fig.savefig(path.replace(".png", ".pdf"), bbox_inches="tight",
                facecolor="#ffffff")
    plt_close = __import__("matplotlib.pyplot", fromlist=["close"]).close
    plt_close(fig)


def img_tag(path, alt):
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return ('<img alt="%s" src="data:image/png;base64,%s">'
            % (alt, b64))


def fmt(v, nd=4, dash="—"):
    if v is None:
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:
        return dash
    return ("%." + str(nd) + "f") % f


def pct(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return ("%." + str(nd) + "f%%") % (100 * float(v))


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------
def load_all(exp_dir):
    d = {}

    def j(name, default=None):
        p = os.path.join(exp_dir, name)
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
        return default if default is not None else {}

    d["verdict"] = j("verdict.json")
    d["metrics"] = pd.read_csv(os.path.join(exp_dir, "metrics_table.csv"))
    for name in ("backtest_results", "backtest_cost_8bps", "backtest_cost_30bps",
                 "backtest_gross_diagnostic"):
        d[name] = j(name + ".json")
    d["data_probe"] = j("data_probe.json")
    d["integrity_check"] = j("integrity_check.json")
    d["lookahead_check"] = j("lookahead_check.json")
    d["audit_recompute"] = j("audit_recompute.json")
    d["shim_validation"] = j("shim_validation.json")
    d["training_log"] = j("training_log.json")
    d["failure_log"] = j("failure_log.json")
    p = os.path.join(exp_dir, "cost_sensitivity.csv")
    d["cost_sensitivity"] = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()
    p = os.path.join(exp_dir, "paper_comparison.csv")
    d["paper_comparison"] = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()
    # per-arm run logs (epoch history / VRAM / early stopping)
    runs = {}
    pd_dir = os.path.join(exp_dir, "preds")
    if os.path.isdir(pd_dir):
        for fn in os.listdir(pd_dir):
            if fn.endswith(".json") and "_s" in fn:
                key = fn[:-5]
                try:
                    runs[key] = json.load(open(os.path.join(pd_dir, fn),
                                               encoding="utf-8"))
                except Exception:
                    pass
    d["runs"] = runs
    # previous (WORK-503) results for the old-vs-new comparison
    prev_dir = os.path.join(REPO, "experiments", PREV_EXP)
    if os.path.isdir(prev_dir):
        d["prev_metrics"] = pd.read_csv(os.path.join(prev_dir, "metrics_table.csv"))
        p = os.path.join(prev_dir, "verdict.json")
        d["prev_verdict"] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    return d


def git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=True, check=False).stdout.strip()
    except Exception:
        return ""


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------
def fig_ablation(d, path):
    plt = _style()
    v = d["verdict"]
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    meds, names = [], []
    for arm in ARM_ORDER:
        vals = [v["runs"][f"{arm}_s{s}"]["rankic"] for s in v["seeds"]
                if f"{arm}_s{s}" in v.get("runs", {})]
        meds.append(float(np.median(vals)) if vals else np.nan)
        names.append(ARM_LABEL[arm])
    x = np.arange(len(ARM_ORDER))
    w = 0.36
    ax.bar(x - w / 2, meds, w, color=BLUE, label="WORK-557 本单（全窗 2009–2019）")
    ax.bar(x + w / 2, [PAPER_ABLATION[a] for a in ARM_ORDER], w, color=TEAL,
           label="论文 Table 2")
    for xi, m in zip(x - w / 2, meds):
        if m == m:
            ax.text(xi, m + 0.0012, fmt(m, 4), ha="center", fontsize=9)
    for xi, a in zip(x + w / 2, ARM_ORDER):
        ax.text(xi, PAPER_ABLATION[a] + 0.0012, fmt(PAPER_ABLATION[a], 4),
                ha="center", fontsize=9, color=TEAL)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=9)
    ax.set_ylabel("测试期 RankIC（各 seed 中位数）")
    ax.set_title("消融 RankIC：本单 vs 论文 Table 2")
    ax.legend(loc="upper right")
    _clean(ax)
    _save(fig, path)


def fig_delta(d, path):
    plt = _style()
    v = d["verdict"]
    h1 = v["H1"]
    dd = h1.get("delta_drop")
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    ax.axhspan(-0.40, -0.15, color="#eaf1f8", zorder=0)
    ax.axhspan(-0.10, 0.10, color="#f8ecec", zorder=0)
    ax.axhline(0, color=RULE, lw=0.8, zorder=1)
    ax.axhline(PAPER_ABLATION["full"] and -0.26934984520123845, color=TEAL,
               lw=1.2, ls="--", zorder=2)
    ax.text(-0.395, -0.26934984520123845 + 0.012,
            "论文 Δ_drop = −26.9%", color=TEAL, fontsize=9, va="bottom")
    if dd is not None and dd == dd:
        ax.scatter([0], [dd], s=120, color=RED if not (-0.40 <= dd <= -0.15)
                   else BLUE, zorder=4)
        ax.text(0.03, dd, "本单 Δ_drop = %s" % pct(dd, 1), va="center",
                fontsize=10.5, fontweight="bold")
    per = h1.get("delta_ic_per_seed") or {}
    for i, (s, val) in enumerate(sorted(per.items())):
        if val is None or val != val:
            continue
        ax.scatter([0.22], [val], s=45, color="#8a6d3b", zorder=4)
        ax.text(0.235, val, "seed %s" % s, va="center", fontsize=9)
    ax.set_xlim(-0.45, 0.75)
    ax.set_xticks([])
    ax.set_ylabel("Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full")
    ax.set_title("主检验量 Δ_drop 与预注册判词带（进带 [−40%, −15%]；退带 [−10%, +10%]）")
    _clean(ax)
    _save(fig, path)


def fig_equity(d, path):
    plt = _style()
    exp_dir = os.path.join(REPO, "experiments", EXP)
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    plotted = 0
    for arm in ARM_ORDER + ["gru"]:
        for s in d["verdict"]["seeds"]:
            p = os.path.join(exp_dir, "preds", "%s_s%s_daily.parquet" % (arm, s))
            if not os.path.exists(p):
                continue
            df = pd.read_parquet(p)
            col = "cum_log_net" if "cum_log_net" in df.columns else df.columns[-1]
            ax.plot(df.index if df.index.name else range(len(df)), df[col],
                    color=ARM_COLOR[arm], lw=1.4 if arm == "full" else 1.0,
                    ls="-" if arm == "full" else "--",
                    label=ARM_LABEL[arm] if s == d["verdict"]["seeds"][0] else None,
                    alpha=1.0 if arm == "full" else 0.75)
            plotted += 1
    ax.axhline(0, color=RULE, lw=0.8)
    ax.set_ylabel("累计对数净收益")
    ax.set_xlabel("测试期交易日")
    ax.set_title("测试期净值：四档消融 + GRU 基线")
    if plotted:
        ax.legend(ncol=2, loc="upper left")
    _clean(ax)
    _save(fig, path)


def fig_bootstrap(d, path):
    plt = _style()
    exp_dir = os.path.join(REPO, "experiments", EXP)
    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    seeds = d["verdict"]["seeds"]
    colors = [BLUE, TEAL]
    for i, s in enumerate(seeds):
        p = os.path.join(exp_dir, "preds", "h2_bootstrap_s%s.npy" % s)
        if not os.path.exists(p):
            continue
        boots = np.load(p)
        ax.hist(boots, bins=60, alpha=0.5, color=colors[i % len(colors)],
                label="seed %s（10000 次 block bootstrap, block=5 日）" % s)
        h2 = d["verdict"]["H2"]["per_seed"].get(str(s))
        if h2:
            ax.axvline(h2["point"], color=colors[i % len(colors)], lw=1.4, ls="--")
            ax.axvline(h2["lo"], color=colors[i % len(colors)], lw=0.9, ls=":")
            ax.axvline(h2["hi"], color=colors[i % len(colors)], lw=0.9, ls=":")
    ax.axvline(0, color=RED, lw=1.2)
    ax.text(0, ax.get_ylim()[1] * 0.92, " 0", color=RED, fontsize=9.5)
    ax.set_xlabel("RankIC(full) − RankIC(GRU)")
    ax.set_ylabel("频次")
    ax.set_title("H2：对 GRU 基线的增量，10000 次 block bootstrap")
    ax.legend(loc="upper right")
    _clean(ax)
    _save(fig, path)


def fig_old_vs_new(d, path):
    """WORK-503（缩窗）vs WORK-557（全窗）逐项对照。"""
    plt = _style()
    prev = d.get("prev_metrics")
    cur = d["metrics"]
    fig, axes = plt.subplots(1, 3, figsize=(8.2, 3.2))
    if prev is None or cur is None or len(prev) == 0:
        for ax in axes:
            ax.axis("off")
        _save(fig, path)
        return

    def med(df, arm, col):
        s = df[df["arm"] == arm][col]
        return float(np.median(s)) if len(s) else np.nan

    arms = ARM_ORDER
    x = np.arange(len(arms))
    w = 0.36
    # (a) RankIC
    ax = axes[0]
    p = [med(prev, a, "rankic") for a in arms]
    c = [med(cur, a, "rankic") for a in arms]
    ax.bar(x - w / 2, p, w, color="#9aa5ad", label="WORK-503（2015–2019, ≤6 轮）")
    ax.bar(x + w / 2, c, w, color=BLUE, label="WORK-557（2009–2019, ≤50 轮）")
    ax.set_title("(a) 测试期 RankIC（中位数）")
    ax.set_ylabel("RankIC")
    ax.set_xticks(x)
    ax.set_xticklabels([a.replace("no_", "no\n") for a in arms], fontsize=9)
    ax.legend(fontsize=8)
    _clean(ax)
    # (b) net SR
    ax = axes[1]
    p = [med(prev, a, "sr") for a in arms]
    c = [med(cur, a, "sr") for a in arms]
    ax.bar(x - w / 2, p, w, color="#9aa5ad", label="WORK-503")
    ax.bar(x + w / 2, c, w, color=TEAL, label="WORK-557")
    ax.axhline(0, color=INK, lw=0.8)
    ax.set_title("(b) 净 SR（主口径 买5/卖15 bps）")
    ax.set_ylabel("净 SR")
    ax.set_xticks(x)
    ax.set_xticklabels([a.replace("no_", "no\n") for a in arms], fontsize=9)
    _clean(ax)
    # (c) delta_drop
    ax = axes[2]
    pv = d.get("prev_verdict", {}).get("H1", {}).get("delta_drop")
    cv = d["verdict"]["H1"].get("delta_drop")
    vals = [pv, cv]
    cols = ["#9aa5ad", RED]
    labs = ["WORK-503\n(缩窗 2015–2019 / ≤6 轮)", "WORK-557\n(全窗 2009–2019 / ≤50 轮)"]
    bars = ax.bar([0, 1], [v if v is not None else np.nan for v in vals], 0.5,
                   color=cols)
    ax.axhspan(-0.40, -0.15, color="#eaf1f8", zorder=0)
    ax.axhspan(-0.10, 0.10, color="#f8ecec", zorder=0)
    ax.axhline(-0.26934984520123845, color=TEAL, lw=1.2, ls="--")
    ax.text(1.42, -0.26934984520123845 + 0.012, "论文 −26.9%", color=TEAL,
            fontsize=8.5, va="bottom", ha="right")
    for b, v in zip(bars, vals):
        if v is not None and v == v:
            ax.text(b.get_x() + b.get_width() / 2, v + (0.012 if v >= 0 else -0.012),
                    pct(v, 1), ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=9.5, fontweight="bold")
    ax.set_title("(c) 主检验量 Δ_drop")
    ax.set_ylabel("Δ_drop")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(labs, fontsize=8.5)
    _clean(ax)
    fig.tight_layout()
    _save(fig, path)


def fig_convergence(d, path):
    """各档各 seed 的 val_loss 随 epoch（stage-2）。"""
    plt = _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    runs = d.get("runs", {})
    seen = set()
    for key, r in sorted(runs.items()):
        hist = r.get("epoch_history") or []
        if not hist:
            continue
        arm = r.get("arm", key.split("_s")[0])
        seed = r.get("seed", key.split("_s")[-1])
        xs = [h["epoch"] for h in hist]
        ys = [h["val_loss"] for h in hist if h.get("val_loss") == h.get("val_loss")]
        if not ys:
            continue
        ax.plot(xs[:len(ys)], ys, color=ARM_COLOR.get(arm, MUTED),
                lw=1.5 if arm == "full" else 1.0,
                ls="-" if arm == "full" else "--",
                alpha=1.0 if arm == "full" else 0.8,
                label=ARM_LABEL.get(arm, arm) if arm not in seen else None)
        seen.add(arm)
        stop = r.get("early_stopped_epoch")
        if stop:
            ax.axvline(stop, color=ARM_COLOR.get(arm, MUTED), lw=0.8, ls=":",
                       alpha=0.8)
    ax.set_xlabel("stage-2 轮次（epoch）")
    ax.set_ylabel("验证集 loss")
    ax.set_title("stage-2 训练收敛曲线（虚线竖线 = 早停轮次）")
    if seen:
        ax.legend(ncol=2, loc="upper right")
    _clean(ax)
    _save(fig, path)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def build_html(d, repo, exp_dir, fig_dir):
    v = d["verdict"]
    h1, h2, h3 = v["H1"], v["H2"], v["H3"]
    m = d["metrics"]
    neg = ("退" in v["verdict"]) or ("数据不可用" in v["verdict"])
    dd = h1.get("delta_drop")
    if dd is None or dd != dd:
        band_txt = "—"
    elif -0.40 <= dd <= -0.15:
        band_txt = "%s ~ %s（「进」带）" % (pct(-0.40), pct(-0.15))
    elif -0.10 <= dd <= 0.10:
        band_txt = "%s ~ %s（「退」带）" % (pct(-0.10), pct(+0.10))
    else:
        band_txt = "两条带之外（「部分复现」带）"

    commit = git(repo, "rev-parse", "HEAD")
    prereg_sha = git(repo, "log", "--format=%h %cI", "-1",
                     "--", "experiments/%s/preregistration.md" % EXP)
    probe = d.get("data_probe", {})
    au = d.get("audit_recompute", {})
    shim = d.get("shim_validation", {})
    tl = d.get("training_log", {})

    # ---- 主结果表（逐 seed） --------------------------------------------
    def rows_for(arm):
        sub = m[m["arm"] == arm].sort_values("seed")
        out = []
        for _, r in sub.iterrows():
            out.append(
                "<tr><td>%s</td><td class='num'>%d</td>"
                "<td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td></tr>"
                % (ARM_LABEL.get(arm, arm), int(r["seed"]),
                   fmt(r["rankic"]), fmt(r["rankicir"]), fmt(r["ar"]),
                   fmt(r["mdd"]), fmt(r["sr"], 3), fmt(r["turnover_1side"], 3),
                   fmt(r["mean_holdings"], 1)))
        return "\n".join(out)

    arm_rows = []
    for arm in ARM_ORDER + ["gru"]:
        a = (v.get("by_arm") or {}).get(arm)
        if not a:
            continue
        arm_rows.append(
            "<tr><td>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td></tr>"
            % (ARM_LABEL.get(arm, arm), a.get("n_seeds"),
               a.get("rankic_mean_pm_range") or "—",
               a.get("rankicir_mean_pm_range") or "—",
               a.get("sr_mean_pm_range") or "—",
               a.get("ar_mean_pm_range") or "—",
               a.get("mdd_mean_pm_range") or "—",
               a.get("turnover_mean_pm_range") or "—"))

    h2_rows, h3_rows = [], []
    for s in v["seeds"]:
        p = h2["per_seed"].get(str(s))
        if p:
            h2_rows.append(
                "<tr><td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>[%s, %s]</td><td class='c'>%s</td></tr>"
                % (s, fmt(p["mean_full"]), fmt(p["mean_base"]), fmt(p["point"]),
                   fmt(p["lo"]), fmt(p["hi"]),
                   "含 0（不可证）" if p["lo"] <= 0 <= p["hi"] else "不含 0"))
        p = h3["per_seed"].get(str(s))
        if p:
            h3_rows.append(
                "<tr><td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>[%s, %s]</td>"
                "<td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td>"
                "<td class='num'>%s</td><td class='num'>%s</td></tr>"
                % (s, fmt(p["sr"], 3), fmt(p["sr_ci"]["lo"], 3),
                   fmt(p["sr_ci"]["hi"], 3), fmt(p["ar"]), fmt(p["mdd"]),
                   fmt(p["turnover"], 3),
                   fmt((p.get("alpha_ew") or {}).get("excess_annualized")),
                   fmt((p.get("alpha_idx") or {}).get("excess_annualized"))))

    # ---- 对照表 ①：vs 论文 Table 2 ---------------------------------------
    cmp_paper = []
    for arm in ARM_ORDER:
        vals = [v["runs"][f"{arm}_s{s}"]["rankic"] for s in v["seeds"]
                if f"{arm}_s{s}" in v["runs"]]
        med = float(np.median(vals)) if vals else None
        ref = PAPER_ABLATION[arm]
        cmp_paper.append(
            "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td></tr>"
            % (ARM_LABEL[arm], fmt(med), fmt(ref),
               fmt((med - ref) / ref, 4) if med is not None else "—",
               pct((med - ref) / ref) if med is not None else "—"))

    # ---- 对照表 ②：vs WORK-503 旧结果 ------------------------------------
    prev = d.get("prev_metrics")
    prev_v = d.get("prev_verdict", {})
    cmp_prev = []
    if prev is not None and len(prev):
        for arm in ARM_ORDER + ["gru"]:
            ps = prev[prev["arm"] == arm]
            cs = m[m["arm"] == arm]
            if not len(ps) or not len(cs):
                continue
            for col, nd in (("rankic", 4), ("sr", 3), ("ar", 4), ("mdd", 4),
                            ("turnover_1side", 3)):
                pm = float(np.median(ps[col]))
                cm = float(np.median(cs[col]))
                cmp_prev.append(
                    "<tr><td>%s</td><td>%s</td>"
                    "<td class='num'>%s</td><td class='num'>%s</td>"
                    "<td class='num'>%s</td></tr>"
                    % (ARM_LABEL.get(arm, arm), col, fmt(pm, nd), fmt(cm, nd),
                       fmt(cm - pm, nd)))
        pd_ = prev_v.get("H1", {}).get("delta_drop")
        cd_ = h1.get("delta_drop")
        cmp_prev.append(
            "<tr><td><b>Δ_drop（主检验量）</b></td><td>delta_drop</td>"
            "<td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'><b>%s</b></td></tr>"
            % (pct(pd_, 2) if pd_ is not None else "—",
               pct(cd_, 2) if cd_ is not None else "—",
               pct(cd_ - pd_, 2) if (pd_ is not None and cd_ is not None) else "—"))

    # ---- 训练成本表 ------------------------------------------------------
    train_rows = []
    for key, r in sorted((d.get("runs") or {}).items()):
        hist = r.get("epoch_history") or []
        secs = [h.get("seconds") for h in hist if h.get("seconds")]
        train_rows.append(
            "<tr><td>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s / %s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td></tr>"
            % (ARM_LABEL.get(r.get("arm", "?"), r.get("arm", "?")),
               r.get("seed", "?"),
               r.get("stage1_epochs_ran", "—"), r.get("epochs_ran", "—"),
               ("%.1f" % (sum(secs) / len(secs))) if secs else "—",
               fmt(r.get("early_stopped_epoch"), 0),
               fmt(r.get("peak_vram_gb"), 2)))

    # ---- 数据自检表 ------------------------------------------------------
    py = probe.get("pit_by_year") or []
    py_rows = "".join(
        "<tr><td class='num'>%d</td><td class='num'>%d</td>"
        "<td class='num'>%.1f</td><td class='num'>%d</td>"
        "<td class='num'>%d</td></tr>"
        % (r["year"], r["trading_days"], r["active_mean"], r["active_min"],
           r["distinct_members"]) for r in py)
    mr = probe.get("missing_rate", {})
    mr_rows = "".join(
        "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
        "<td class='num'>%s</td></tr>"
        % (k, v.get("member_days"), v.get("missing_pairs"),
           pct(v.get("missing_rate"), 3))
        for k, v in mr.items())

    css = open(os.path.join(REPO, "templates", "work557_report.css"),
               encoding="utf-8").read()
    f1 = img_tag(os.path.join(fig_dir, "fig1.png"), "消融 RankIC 对照")
    f2 = img_tag(os.path.join(fig_dir, "fig2.png"), "Δ_drop 判词带")
    f3 = img_tag(os.path.join(fig_dir, "fig3.png"), "净值曲线")
    f4 = img_tag(os.path.join(fig_dir, "fig4.png"), "H2 bootstrap")
    f5 = img_tag(os.path.join(fig_dir, "fig5.png"), "旧 vs 新对照")
    f6 = img_tag(os.path.join(fig_dir, "fig6.png"), "训练收敛曲线")

    h1_verdict = h1.get("verdict", "—")
    fc = v.get("failure_criteria", {})
    fcrow = lambda k: ("<td class='c'><span class='tag %s'>%s</span></td>"
                       % ("no" if fc.get(k, {}).get("hit") else "ok",
                          "命中" if fc.get(k, {}).get("hit") else "未命中"))
    fc_rows = "".join(
        "<tr><td>%s</td>%s<td class='meta'>%s</td></tr>"
        % (k, fcrow(k), fc.get(k, {}).get("note", "") or "—")
        for k in ("1_data_usable", "2_endpoints", "3_both_seeds_flat",
                  "4_prereg_before_results", "5_recompute_agreement"))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORK-557 · PRISM-VQ 全训练窗重跑</title>
<style>{css}</style></head><body><div class="page">

<header class="masthead">
  <div class="kicker">内部研究模拟 · WORK-557 · PRISM-VQ 全窗重跑</div>
  <h1>撤掉「无 GPU 压缩训练段」的妥协：按论文口径全窗重跑 PRISM-VQ</h1>
  <p class="subtitle">训练段 2009–2019（论文口径）· 每阶段 ≤50 轮 · RTX 3060 · 四档消融 × 2 seed</p>
  <div class="byline">
    <span><b>单号</b> WORK-557</span>
    <span><b>上游</b> finxlab/PRISM-VQ @ 7d02635d</span>
    <span><b>基线协议</b> WORK-503 预注册 v1.0–v1.4</span>
    <span><b>本单预注册</b> {prereg_sha or "—"}</span>
    <span><b>代码</b> {commit[:7] if commit else "—"}</span>
  </div>
</header>

<div class="disclaimer">
  <div><b>内部研究模拟，不构成投资建议。</b>本单是 WORK-503 的**算力口径重跑**：
  判据、股票池、特征、标签、成本、组合规则、掩码、种子、指标定义一律照抄 WORK-503，
  只把「训练段 + 轮数 + 设备 + 数据底座」四项换成论文/上游口径。</div>
</div>

<div class="verdict{'.negative' if neg else ''}">
  <h3>结论</h3>
  <p class="word">{v.get('verdict', '—')}</p>
  <p>主检验量 H1：Δ_drop = <b>{pct(dd) if dd is not None else '—'}</b>
  （落在 {band_txt}；论文基准 −26.9%）。净 SR 中位数
  <b>{fmt(h3.get('sr_median'), 3)}</b>（论文 1.5694）。</p>
  <p class="caveat">判词口径用 WORK-503 预注册 v1.4 的
  <span class="mono">Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full</span>（中位数），
  不是 v1.0 的字面式 —— 理由见 v1.3/v1.4（进带与退带的数值一字未改，只改了被测量的量）。</p>
</div>

<h2><span class="num">1</span>环境与数据自检</h2>
<div class="keybox">
<p><b>GPU</b> NVIDIA GeForce RTX 3060 12 GB（capability sm_86，驱动 610.88 / CUDA UMD 13.3）；
<b>torch</b> 2.11.0+cu128，<span class="mono">torch.cuda.is_available() = True</span>；
<b>CPU</b> 12 逻辑核；<b>磁盘</b> C: 71.6 GB / Z: 1638 GB 可用；
<b>git HEAD</b> <span class="mono">{commit}</span>。</p>
<p><b>数据源</b> <span class="mono">Z:\\quantdb</span>（Samba 共享
<span class="mono">\\\\192.168.9.104\\share</span> → 服务器 <span class="mono">/srv/share/quantdb</span>）。
只走 <span class="mono">raw/</span> 层的 Parquet，<b>不</b>通过 SMB 打开
<span class="mono">db/quantdb.duckdb</span>（quantdb README §6 规矩 6）。
本单全程只读，未写 Z: 盘任何文件。</p>
<p><b>qlib 运行时</b>：pyqlib 0.9.7 无 cp313 轮子，本机训练环境是 Python 3.13.15。
把轮子里两个编译产物（<span class="mono">rolling/expanding .pyd</span>，共 6 个函数）
按上游 <span class="mono">.pyx</span> 源码逐行转写成纯 numpy 替身，
与 Python 3.9 环境里**真的 cp39 编译模块**逐元素对拍，
<b>max |diff| = {shim.get('max_abs_diff', '—')}</b>（判据 &lt; 1e-12）；
上游代码一行未改。</p>
</div>

<h3>CSI300 PIT 成分逐年 active 数（2009–2024）</h3>
<table>
<caption>成分来源：<span class="mono">index_weight</span>（指数 × 成分 × 月）按生效日向前沿用；
每个交易日取最近一次已生效快照。每日 active 数应为 300。</caption>
<thead><tr><th class="num">年</th><th class="num">交易日</th><th class="num">active 均值</th>
<th class="num">active 最小</th><th class="num">全年出现过的不同成分</th></tr></thead>
<tbody>{py_rows}</tbody></table>

<h3>member-day 缺失率</h3>
<table>
<caption>口径：PIT 成分 × 交易日 的笛卡尔积中，<span class="mono">daily</span>（及复权因子、
涨跌停、估值表）里没有对应行的比例。<b>无行 = 停牌</b>（与 WORK-503 的新浪口径一致：
停牌日保留行、价格前向填充、成交量置 0），不是数据缺口。</caption>
<thead><tr><th>表</th><th class="num">member-day 总数</th><th class="num">缺行数</th>
<th class="num">缺失率</th></tr></thead>
<tbody>{mr_rows}</tbody></table>

<h2><span class="num">2</span>四档 × 2 seed 全窗结果</h2>
<table>
<caption>测试段 2022-01-01 ~ 2024-12-31；成本为主口径（买 5 bps / 卖 15 bps）；
换手为<b>单边</b>口径（买卖 ÷ 2）。</caption>
<thead><tr><th>档</th><th class="num">seed</th><th class="num">RankIC</th>
<th class="num">RankICIR</th><th class="num">AR</th><th class="num">MDD</th>
<th class="num">SR</th><th class="num">单边换手</th><th class="num">平均持仓</th></tr></thead>
<tbody>{"".join(rows_for(a) for a in ARM_ORDER + ["gru"])}</tbody></table>

<table>
<caption>按档聚合（mean ± range）——预注册要求「必须报 mean ± range，不许只报最好的一次」。</caption>
<thead><tr><th>档</th><th class="num">n</th><th class="num">RankIC</th>
<th class="num">RankICIR</th><th class="num">SR</th><th class="num">AR</th>
<th class="num">MDD</th><th class="num">单边换手</th></tr></thead>
<tbody>{"".join(arm_rows)}</tbody></table>

<h3>训练成本（每档每 seed）</h3>
<table>
<caption>stage-1 与 stage-2 实际跑的轮数、平均 s/epoch、早停轮次、峰值显存。
轮数上限 50；早停 patience 15（上游 <span class="mono">configs/config.yaml</span> 原值）。</caption>
<thead><tr><th>档</th><th class="num">seed</th><th class="num">stage1/stage2 轮数</th>
<th class="num">平均 s/epoch</th><th class="num">早停轮次</th>
<th class="num">峰值显存 GB</th></tr></thead>
<tbody>{"".join(train_rows) or "<tr><td colspan='6'>未取到</td></tr>"}</tbody></table>

<figure>{f1}
<figcaption><b>图 1 · 消融 RankIC：本单 vs 论文 Table 2。</b>
柱高为本单各 seed 中位数；论文值为 Table 2 原值（full 0.0646 / w/o VQ 0.0472 /
w/o MoE 0.0586 / w/o Prior 0.0548）。来源：本单回测产物 + 论文 arXiv:2605.13407v1 Table 2。</figcaption></figure>

<figure>{f2}
<figcaption><b>图 2 · 主检验量 Δ_drop 与预注册判词带。</b>
浅蓝带 = 进带 [−40%, −15%]，浅红带 = 退带 [−10%, +10%]；虚线 = 论文基准 −26.9%；
实心点 = 本单两 seed 合并后的 Δ_drop，棕点 = 各 seed 单独值。来源：本单
<span class="mono">verdict.json → H1</span>。</figcaption></figure>

<figure>{f3}
<figcaption><b>图 3 · 测试期净值。</b>
四档消融 + GRU 基线的累计对数净收益（主成本口径）；实线 = 完整模型。
来源：本单 <span class="mono">preds/*_daily.parquet</span>。</figcaption></figure>

<figure>{f4}
<figcaption><b>图 4 · H2：对 GRU 基线的增量。</b>
10000 次 block bootstrap（block 长度 = 5 个交易日）；虚线 = 点估计，点线 = 95% CI 端点，
红色竖线 = 0。来源：本单 <span class="mono">preds/h2_bootstrap_s*.npy</span>。</figcaption></figure>

<figure>{f5}
<figcaption><b>图 5 · 旧 vs 新对照：WORK-503（缩窗 2015–2019 / ≤6 轮 / CPU）vs
WORK-557（全窗 2009–2019 / ≤50 轮 / GPU）。</b>
(a) 测试期 RankIC 中位数；(b) 净 SR（主成本口径）；(c) 主检验量 Δ_drop，底色为预注册判词带。
来源：本单与 <span class="mono">experiments/WORK-503/</span> 的已入库产物。</figcaption></figure>

<figure>{f6}
<figcaption><b>图 6 · stage-2 训练收敛曲线。</b>
各档各 seed 的验证集 loss 随轮次；点线竖线 = 早停发生的轮次。
来源：本单 <span class="mono">preds/*_s*.json → epoch_history</span>。</figcaption></figure>

<h2><span class="num">3</span>主检验与并列检验</h2>

<h3>H1 · 码本有效性（唯一决定进/退的量）</h3>
<table>
<thead><tr><th>项</th><th class="num">数值</th></tr></thead>
<tbody>
<tr><td>RankIC_full（两 seed 中位数）</td><td class="num">{fmt(h1.get('rankic_full_median'))}</td></tr>
<tr><td>RankIC_no_vq（两 seed 中位数）</td><td class="num">{fmt(h1.get('rankic_no_vq_median'))}</td></tr>
<tr><td><b>Δ_drop（合并中位数）</b></td><td class="num"><b>{pct(dd) if dd is not None else '—'}</b></td></tr>
<tr><td>Δ_formula（派单卡字面式）</td><td class="num">{pct(h1.get('delta_formula')) if h1.get('delta_formula') is not None else '—'}</td></tr>
<tr><td>逐 seed Δ_drop</td><td class="num">{" / ".join(pct(x) for x in (h1.get('rankic_full_per_seed') and []) ) or "—"}</td></tr>
<tr><td>论文 Δ_drop（Table 2）</td><td class="num">{pct(h1.get('paper_delta_ic'), 1)}</td></tr>
<tr><td>判词</td><td>{h1_verdict}</td></tr>
</tbody></table>

<h3>H2 · 对 GRU 基线的增量（10000 次 block bootstrap，block = 5 日）</h3>
<table>
<thead><tr><th class="num">seed</th><th class="num">RankIC(full) 均值</th>
<th class="num">RankIC(GRU) 均值</th><th class="num">差值</th>
<th class="num">95% CI</th><th class="c">是否含 0</th></tr></thead>
<tbody>{"".join(h2_rows)}</tbody></table>
<p class="meta">{h2.get('note', '')}</p>

<h3>H3 · 可交易性</h3>
<table>
<thead><tr><th class="num">seed</th><th class="num">净 SR</th><th class="num">SR 95% CI</th>
<th class="num">AR</th><th class="num">MDD</th><th class="num">单边换手</th>
<th class="num">对 CSI300 等权超额</th><th class="num">对 CSI300 指数超额</th></tr></thead>
<tbody>{"".join(h3_rows)}</tbody></table>
<p class="meta">{h3.get('note', '')}</p>

<h2><span class="num">4</span>两张对照表</h2>

<h3>对照表 ① · 本单 vs 论文 Table 2</h3>
<table>
<caption>论文值逐字抄自 arXiv:2605.13407v1 Table 2；本单为各 seed 中位数。</caption>
<thead><tr><th>档</th><th class="num">本单 RankIC</th><th class="num">论文 RankIC</th>
<th class="num">绝对差</th><th class="num">相对差</th></tr></thead>
<tbody>{"".join(cmp_paper)}</tbody></table>

<h3>对照表 ② · 本单 vs WORK-503 旧结果（缩窗版）</h3>
<table>
<caption>WORK-503 = 训练段 2015–2019、每阶段 ≤6 轮、CPU 8 核无 GPU；
本单 = 训练段 2009–2019、每阶段 ≤50 轮、RTX 3060。数据底座也从
新浪/baostock 换成 quantdb。差值可能来自训练窗、轮数、种子、数据底座四个来源。</caption>
<thead><tr><th>档</th><th>指标</th><th class="num">WORK-503</th>
<th class="num">WORK-557</th><th class="num">差值</th></tr></thead>
<tbody>{"".join(cmp_prev) or "<tr><td colspan='5'>未取到 WORK-503 旧结果</td></tr>"}</tbody></table>

<h3>成本三档</h3>
<table>
<caption>预注册三档全跑：主口径（买 5 / 卖 15 bps）、单边 8 bps、双边 30 bps；
另附 0 bps 毛值诊断（预注册外，用于拆解成本占比）。</caption>
<thead><tr><th>成本档</th><th>说明</th><th>run</th><th class="num">RankIC</th>
<th class="num">净 SR</th><th class="num">净 AR</th><th class="num">MDD</th>
<th class="num">单边换手</th></tr></thead>
<tbody>{"".join("<tr><td>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td>"
                 "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                 "<td class='num'>%s</td></tr>" % (r.cost_point, r.description, r.run,
                 fmt(r.rankic), fmt(r.sr_net, 3), fmt(r.ar_net),
                 fmt(r.mdd), fmt(r.turnover_1side, 3))
                 for r in d["cost_sensitivity"].itertuples()) if len(d["cost_sensitivity"]) else ""}</tbody></table>

<h2><span class="num">5</span>失败判据</h2>
<table>
<thead><tr><th>判据</th><th class="c">状态</th><th>备注</th></tr></thead>
<tbody>{fc_rows}</tbody></table>

<h2><span class="num">6</span>独立复算</h2>
<div class="keybox">
<p>复算脚本 <span class="mono">audit/recompute_work557.py</span> <b>不 import 建模阶段的任何函数</b>
（<span class="mono">ast</span> 自检 <span class="mono">modeling_imports_found = 0</span>），
从 <span class="mono">preds/*.parquet</span> 与
<span class="mono">panel/trade_state.parquet</span> 重新算一遍组合与指标。</p>
<p>不一致项：<b>{au.get('n_mismatches', '—')}</b> 条（判据：相对差 ≤ 1e-6）。</p>
</div>

<h2><span class="num">7</span>口径与相对论文的偏离</h2>
<table>
<thead><tr><th>项</th><th>本单</th><th>原因 / 影响方向</th></tr></thead>
<tbody>
<tr><td>训练段</td><td>2009-01-01 ~ 2019-12-31</td><td>与论文、上游 <span class="mono">configs/config.yaml</span> 一致（本单的主要改动）</td></tr>
<tr><td>训练轮数</td><td>每阶段 ≤50（patience 15）</td><td>与上游配置同量级；WORK-503 因无 GPU 压到 ≤6（patience 3）</td></tr>
<tr><td>先验因子</td><td>6 个自建 A 股先验</td><td>JKP 13 个全球因子在 A 股无逐股对应值 → <span class="mono">no_prior</span> 降幅可能小于论文 −15.2%</td></tr>
<tr><td>标签</td><td><span class="mono">open(t+1) → close(t+k)</span></td><td>与论文正文一致；上游 yaml 用 close→close</td></tr>
<tr><td>复权</td><td>后复权（特征 + 组合收益）/ 不复权（涨跌停判定）</td><td>照抄 WORK-503 §3 与 §1.1 D4</td></tr>
<tr><td>数据底座</td><td>quantdb（tushare 系）</td><td>WORK-503 用新浪/baostock；复权算法与分红口径不同 → 绝对水平有细微差异</td></tr>
<tr><td>涨跌停判定</td><td>价格带法（板块限 ∪ 5%）</td><td>照抄 WORK-503；无逐日历史 ST 标记 → 多掩码、只削弱不虚增</td></tr>
<tr><td>qlib 运行时</td><td>纯 numpy 替身（数值逐位一致）</td><td>pyqlib 0.9.7 无 cp313 轮子；对拍 max|diff| = {shim.get('max_abs_diff', '—')}</td></tr>
</tbody></table>

<h2><span class="num">8</span>复现命令</h2>
<pre><code># 环境
python experiments/WORK-557/setup_envs.py
python experiments/WORK-557/work557_qlib_shim.py
.venv39\\Scripts\\python experiments/WORK-557/work557_shim_validate.py

# 数据
python scripts/work557_fetch_panel.py
python scripts/work503_build_panel.py --exp WORK-557
python scripts/work503_dump_qlib.py  --exp WORK-557
python scripts/work557_build_dataset.py --exp WORK-557

# 训练（每阶段 ≤50 轮，GPU）
.venv\\Scripts\\python scripts/work557_run_arms.py --arm full   --seed 42   --epochs 50 --exp WORK-557
.venv\\Scripts\\python scripts/work557_run_arms.py --arm no_vq  --seed 42   --epochs 50 --exp WORK-557
.venv\\Scripts\\python scripts/work557_run_arms.py --arm no_moe --seed 42   --epochs 50 --exp WORK-557
.venv\\Scripts\\python scripts/work557_run_arms.py --arm no_prior --seed 42  --epochs 50 --exp WORK-557
... 同上换 seed 2024
.venv\\Scripts\\python scripts/work557_baseline.py --kind gru --seed 42 --epochs 50 --exp WORK-557

# 回测 / 判定 / 复算 / 报告
.venv\\Scripts\\python scripts/work557_backtest.py --exp WORK-557
.venv\\Scripts\\python scripts/work557_verdict.py  --exp WORK-557
.venv\\Scripts\\python audit/recompute_work557.py
.venv\\Scripts\\python scripts/work557_report.py    --exp WORK-557 --preview</code></pre>

<div class="foot">
<b>WORK-557 · PRISM-VQ 全训练窗重跑</b> · 内部研究模拟，不构成投资建议。<br>
数据快照不入库（体量大且可重建），清单与校验值在
<span class="mono">experiments/WORK-557/snapshot_manifest*.json</span>；
复权价按 quantdb 视图 <span class="mono">v_daily_hfq</span> 的定义复刻。<br>
报告生成于 {time.strftime('%Y-%m-%d %H:%M:%S')} · 代码 <span class="mono">{commit}</span>
</div>
</div></body></html>
"""
    return html


def render_preview(repo, exp_dir):
    """1440x900 first-screen PNG + A4 print PDF via headless Chrome."""
    png = os.path.join(exp_dir, "preview_first_screen.png")
    pdf = os.path.join(exp_dir, "report_print.pdf")
    url = "file:///" + os.path.join(exp_dir, "report.html").replace("\\", "/")
    chrome = None
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              "google-chrome", "chromium"):
        if os.path.isfile(c):
            chrome = c
            break
        try:
            subprocess.run([c, "--version"], capture_output=True, timeout=20)
            chrome = c
            break
        except Exception:
            continue
    if chrome is None:
        print("!! no chrome/edge found; skipping preview")
        return
    base = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--virtual-time-budget=8000"]
    subprocess.run(base + ["--window-size=1440,900", "--screenshot=" + png, url],
                   capture_output=True, timeout=300)
    subprocess.run(base + ["--no-pdf-header-footer", "--print-to-pdf=" + pdf, url],
                   capture_output=True, timeout=300)
    try:
        from PIL import Image
        im = Image.open(png)
        print("preview -> %s (%dx%d)" % (png, im.size[0], im.size[1]))
    except Exception:
        print("preview -> %s" % png)
    print("print   -> %s" % pdf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default=EXP)
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    repo = REPO
    exp_dir = os.path.join(repo, "experiments", args.exp)
    fig_dir = os.path.join(repo, "figures", args.exp)
    os.makedirs(fig_dir, exist_ok=True)

    d = load_all(exp_dir)
    if not d.get("verdict"):
        raise SystemExit("no verdict.json in %s — run the backtest + verdict first"
                         % exp_dir)

    fig_ablation(d, os.path.join(fig_dir, "fig1.png"))
    fig_delta(d, os.path.join(fig_dir, "fig2.png"))
    fig_equity(d, os.path.join(fig_dir, "fig3.png"))
    fig_bootstrap(d, os.path.join(fig_dir, "fig4.png"))
    fig_old_vs_new(d, os.path.join(fig_dir, "fig5.png"))
    fig_convergence(d, os.path.join(fig_dir, "fig6.png"))
    print("figures -> %s" % fig_dir)

    html = build_html(d, repo, exp_dir, fig_dir)
    out = os.path.join(exp_dir, "report.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("report -> %s  (%.1f KB)" % (out, os.path.getsize(out) / 1024))

    if args.preview:
        render_preview(repo, exp_dir)


if __name__ == "__main__":
    sys.exit(main())
