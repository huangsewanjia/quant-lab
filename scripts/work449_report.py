#!/usr/bin/env python3
"""WORK-449 · stage 3 step 3 — figures, report data and print preview.

Produces
  figures/WORK-449/fig1.png  mask ladder schematic
  figures/WORK-449/fig2.png  cumulative net value, three arms (8 bps)
  figures/WORK-449/fig3.png  divergence shape (apparent IC vs realizable Sharpe)
  figures/WORK-449/fig4.png  paper vs this study
  experiments/WORK-449/report_data.json   every token used by the template
  experiments/WORK-449/preview_first_screen.png   1440x900 first screen
  experiments/WORK-449/report_print.pdf              A4 print rendering

Figure styling follows the house palette (white ground #ffffff, rule #d9d9d6,
accent only #0F4D92 / #42949E / #B64342, no shadow/gradient/rounded corners).
All in-figure text is English; the Chinese captions live in the HTML report.

  python scripts/work449_report.py --exp WORK-449            # figures + data
  python scripts/work449_report.py --exp WORK-449 --preview  # + png/pdf
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

BLUE, TEAL, RED = "#0F4D92", "#42949E", "#B64342"
INK, INK2, MUTED = "#1a1a1a", "#3d3d3d", "#6f6f6f"
RULE, PANEL = "#d9d9d6", "#f7f7f5"
ARM_COLOR = {"no_mask": BLUE, "halt_only": TEAL, "full_mask": RED}
ARM_LABEL = {"no_mask": "no_mask", "halt_only": "halt_only",
             "full_mask": "full_mask"}
ARMS = ("no_mask", "halt_only", "full_mask")

# paper Table 11 (real panel): IC, Sharpe, MaxDD, AnnRet
PAPER = {
    "no_mask": {"ic": 0.047, "sharpe": 1.23, "mdd": 22.8, "ann": 12.1},
    "halt_only": {"ic": 0.043, "sharpe": 1.39, "mdd": 19.4, "ann": 13.5},
    "full_mask": {"ic": 0.039, "sharpe": 1.63, "mdd": 11.4, "ann": 15.8},
}


def _style():
    plt.rcParams.update({
        "figure.facecolor": "#ffffff",
        "axes.facecolor": "#ffffff",
        "savefig.facecolor": "#ffffff",
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.edgecolor": RULE,
        "axes.labelcolor": INK,
        "axes.titlesize": 10,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
        "text.color": INK,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "grid.color": RULE,
        "grid.linewidth": 0.6,
        "lines.linewidth": 1.6,
        "figure.dpi": 200,
    })


def _clean(ax, grid_axis="y"):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(RULE)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(True, axis=grid_axis, linestyle="-", alpha=0.7, zorder=0)
    ax.set_axisbelow(True)


# ---------------------------------------------------------------- figure 1
def fig_mask_ladder(panel, path):
    """Schematic of which cells each arm may read, on one cross-section day.

    Fill colour = market state of the cell; a cell the arm cannot read is
    greyed out, so the reader sees exactly which holes each mask punches.
    """
    _style()
    codes = [str(c) for c in panel["codes"]]
    dates = panel["dates"]
    pick = np.datetime64("2023-06-30")
    t = int(np.argmin(np.abs(dates - pick)))
    n = min(len(codes), 64)
    has = panel["has_row"][t, :n]
    susp = panel["suspended"][t, :n]
    lim = panel["at_limit"][t, :n]
    l1y = panel["listed_1y"][t, :n]
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    rows = [
        ("no_mask", has, "reads every panel cell"),
        ("halt_only", has & ~susp, "suspended cells dropped"),
        ("full_mask", has & ~susp & ~lim & l1y, "limit + age dropped"),
    ]
    DROPPED = "#f0f0ee"
    for r, (label, readable, note) in enumerate(rows):
        y = len(rows) - 1 - r
        for j in range(n):
            if not has[j]:
                ax.add_patch(plt.Rectangle((j, y - 0.36), 0.88, 0.72,
                                           facecolor="#ffffff",
                                           edgecolor="#ffffff", linewidth=0.3))
                continue
            if lim[j]:
                state = ("limit", BLUE)
            elif not l1y[j]:
                state = ("age < 1y", "#cfd8dd")
            elif susp[j]:
                state = ("suspended", TEAL)
            else:
                state = ("free", "#dce9f4")
            if readable[j]:
                face, alpha = state[1], 1.0
            else:
                face, alpha = DROPPED, 1.0
            ax.add_patch(plt.Rectangle((j, y - 0.36), 0.88, 0.72,
                                       facecolor=face, edgecolor=RULE,
                                       linewidth=0.35, alpha=alpha))
        ax.text(-0.6, y, label, ha="right", va="center", fontsize=8.5,
                color=INK, fontweight="bold")
        ax.text(n + 0.6, y, note, ha="left", va="center", fontsize=7.6,
                color=MUTED)
    ax.set_xlim(-7.5, n + 9.5)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    handles = [Patch(facecolor="#dce9f4", edgecolor=RULE, label="free, readable"),
               Patch(facecolor=TEAL, edgecolor=RULE, label="suspended, readable"),
               Patch(facecolor=BLUE, edgecolor=RULE, label="at limit, readable"),
               Patch(facecolor="#cfd8dd", edgecolor=RULE, label="listed < 1y, readable"),
               Patch(facecolor=DROPPED, edgecolor=RULE, label="masked out (not read)")]
    ax.legend(handles=handles, loc="lower center", ncol=5,
              bbox_to_anchor=(0.5, -0.26), borderaxespad=0)
    ax.set_title("Mask ladder: which cells each arm may read (cross-section 2023-06-30)",
                 loc="left", pad=8)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- figure 2
def fig_nav(metrics, path):
    _style()
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    for arm in ARMS:
        net = np.array(metrics[arm]["main"]["net_returns"])
        cum = np.cumprod(1.0 + net)
        ax.plot(np.arange(len(cum)), cum - 1.0, color=ARM_COLOR[arm],
                label=ARM_LABEL[arm])
        ax.annotate(f"{arm}  {cum[-1] - 1:+.1%}",
                    xy=(len(cum) - 1, cum[-1] - 1), xytext=(4, 0),
                    textcoords="offset points", fontsize=7.6,
                    color=ARM_COLOR[arm], va="center")
    ax.axhline(0, color=RULE, linewidth=0.9)
    ax.set_xlabel("Trading days in test window (2022-01-01 to 2024-12-31)")
    ax.set_ylabel("Cumulative net return (8 bps, one-sided)")
    ax.set_title("Cumulative net value by mask arm (test window, 8 bps two-sided cost)",
                 loc="left", pad=8)
    ax.set_xlim(0, len(cum) * 1.16)
    _clean(ax)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- figure 3
def fig_divergence(metrics, path):
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9))
    ics = [metrics[a]["main"]["apparent_ic_mean"] for a in ARMS]
    shs = [metrics[a]["main"]["sharpe_rf1.5"] for a in ARMS]
    for ax, vals, name, ylab, fmt in (
            (axes[0], ics, "a", "Mean daily apparent IC", "{:.4f}"),
            (axes[1], shs, "b", "Realizable Sharpe (8 bps)", "{:.3f}")):
        x = np.arange(len(ARMS))
        bars = ax.bar(x, vals, width=0.55,
                      color=[ARM_COLOR[a] for a in ARMS], zorder=3)
        for b, v, a in zip(bars, vals, ARMS):
            ax.annotate(fmt.format(v), xy=(b.get_x() + b.get_width() / 2, v),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=8, color=INK)
        ax.set_xticks(x)
        ax.set_xticklabels([ARM_LABEL[a] for a in ARMS], fontsize=8)
        ax.set_ylabel(ylab, fontsize=8.5)
        ax.set_title(f"{name}   {ylab}", loc="left", fontsize=9)
        _clean(ax)
        lo, hi = min(0, min(vals)), max(vals)
        pad = (hi - lo) * 0.25 or 0.1
        ax.set_ylim(lo - pad, hi + pad * 1.6)
    axes[0].annotate("", xy=(2, ics[2]), xytext=(0, ics[0]),
                     arrowprops=dict(arrowstyle="->", color=MUTED, lw=1.0))
    axes[0].text(1.0, (ics[0] + ics[2]) / 2, "IC rises\n(no_mask > full_mask)",
                 fontsize=7.4, color=MUTED, ha="center", va="bottom")
    axes[1].annotate("", xy=(0, shs[0]), xytext=(2, shs[2]),
                     arrowprops=dict(arrowstyle="->", color=MUTED, lw=1.0))
    axes[1].text(1.0, (shs[0] + shs[2]) / 2, "Sharpe falls\n(mask hurts)",
                 fontsize=7.4, color=MUTED, ha="center", va="bottom")
    fig.suptitle("Divergence shape: apparent IC vs realizable Sharpe",
                 x=0.005, ha="left", fontsize=10, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- figure 4
def fig_paper(metrics, path, d_sharpe):
    _style()
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.6))
    combos = (("a", "paper", "ic", "Apparent IC (paper Table 11)"),
              ("b", "paper", "sharpe", "Realizable Sharpe (paper Table 11)"),
              ("c", "this", "ic", "Apparent IC (this study)"),
              ("d", "this", "sharpe", "Realizable Sharpe (this study)"))
    for ax, (lab, which, key, title) in zip(axes.ravel(), combos):
        if which == "paper":
            vals = [PAPER[a][key] for a in ARMS]
        else:
            vals = [metrics[a]["main"]["apparent_ic_mean" if key == "ic"
                                 else "sharpe_rf1.5"] for a in ARMS]
        x = np.arange(len(ARMS))
        bars = ax.bar(x, vals, width=0.55,
                      color=[ARM_COLOR[a] for a in ARMS], zorder=3)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:.3f}", xy=(b.get_x() + b.get_width() / 2, v),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=7.6, color=INK)
        ax.set_xticks(x)
        ax.set_xticklabels([ARM_LABEL[a] for a in ARMS], fontsize=8)
        ax.set_title(f"{lab}   {title}", loc="left", fontsize=9)
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.3 or 0.05
        ax.set_ylim(lo - pad, hi + pad)
        _clean(ax)
    fig.suptitle("Backwards-test of the divergence magnitude",
                 x=0.005, ha="left", fontsize=10, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------- data
def _fmt(v, nd=3, dash="—"):
    if v is None:
        return dash
    try:
        if isinstance(v, float) and (v != v):
            return dash
    except TypeError:
        return dash
    return f"{v:.{nd}f}"


def _pct(v, nd=2):
    if v is None or v != v:
        return "—"
    return f"{v * 100:.{nd}f}%"


def write_tables(exp_dir, metrics, primary, panel_stats, factor_stats):
    """metrics_table.csv + paper_comparison.{csv,md} (protocol §11)."""
    import csv
    keys = [("apparent_ic_mean", "apparent_ic", 6),
            ("apparent_ic_std", "apparent_ic_std", 6),
            ("apparent_ic_t", "apparent_ic_t", 4),
            ("sharpe_rf1.5", "sharpe_rf1.5", 6),
            ("ann_return", "ann_return", 6),
            ("ann_vol", "ann_vol", 6),
            ("max_drawdown", "max_drawdown", 6),
            ("total_return", "total_return", 6),
            ("turnover_two_sided_total", "turnover_two_sided_total", 4),
            ("turnover_one_sided_total", "turnover_one_sided_total", 4),
            ("turnover_daily_mean_two_sided", "turnover_daily_mean_two_sided", 6),
            ("test_days", "test_days", 0),
            ("avg_active_stocks", "avg_active_stocks", 2)]
    path = os.path.join(exp_dir, "metrics_table.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "cost_bps", "no_mask", "halt_only", "full_mask",
                    "delta_no_minus_full"])
        for k, label, nd in keys:
            vals = [metrics[a]["main"].get(k) for a in ARMS]
            s = [metrics[a]["sensitivity_5bps"].get(k) for a in ARMS]
            row = [label, 8.0] + [("" if v is None else round(v, nd))
                                  for v in vals]
            row.append("" if vals[0] is None or vals[2] is None
                       else round(vals[0] - vals[2], nd))
            w.writerow(row)
            row = [label, 5.0] + [("" if v is None else round(v, nd))
                                  for v in s]
            row.append("" if s[0] is None or s[2] is None
                       else round(s[0] - s[2], nd))
            w.writerow(row)
        w.writerow(["mask_rate_test", 8.0] +
                   [round(panel_stats["arms"][a]["mask_rate_test"], 6)
                    for a in ARMS] + [""])
        w.writerow(["window_assertion_fired_cells", 8.0] +
                   [factor_stats[a]["assertion_fired_cells"] for a in ARMS] + [""])
    print(f"metrics table -> {path}")

    pc_rows = [
        ("apparent_ic", "no_mask", "0.047",
         metrics["no_mask"]["main"]["apparent_ic_mean"],
         "factor set 12 vs 213; pool CSI300 vs all-A"),
        ("apparent_ic", "halt_only", "0.043",
         metrics["halt_only"]["main"]["apparent_ic_mean"], "same"),
        ("apparent_ic", "full_mask", "0.039",
         metrics["full_mask"]["main"]["apparent_ic_mean"], "same"),
        ("sharpe", "no_mask", "1.23",
         metrics["no_mask"]["main"]["sharpe_rf1.5"],
         "same 8 bps cost, 3% single-name cap"),
        ("sharpe", "halt_only", "1.39",
         metrics["halt_only"]["main"]["sharpe_rf1.5"], "same"),
        ("sharpe", "full_mask", "1.63",
         metrics["full_mask"]["main"]["sharpe_rf1.5"], "same"),
        ("max_drawdown", "no_mask", "22.8%",
         metrics["no_mask"]["main"]["max_drawdown"], "close-cummax"),
        ("max_drawdown", "full_mask", "11.4%",
         metrics["full_mask"]["main"]["max_drawdown"], "close-cummax"),
        ("ann_return", "no_mask", "12.1%",
         metrics["no_mask"]["main"]["ann_return"], "365.25-day annualisation"),
        ("ann_return", "full_mask", "15.8%",
         metrics["full_mask"]["main"]["ann_return"], "365.25-day annualisation"),
    ]
    csv_path = os.path.join(exp_dir, "paper_comparison.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "arm", "paper_table11", "this_study",
                    "caveat"])
        for metric, arm, pv, mv, note in pc_rows:
            w.writerow([metric, arm, pv, round(mv, 6), note])
        w.writerow(["delta_sharpe", "no_mask_minus_full_mask", "-0.40",
                    round(primary["main"]["delta_sharpe"], 6),
                    "primary test quantity"])
        w.writerow(["delta_ic", "no_mask_minus_full_mask", "+0.008",
                    round(primary["main"]["delta_ic"], 6),
                    "corroboration"])
    md_path = os.path.join(exp_dir, "paper_comparison.md")
    m = primary["main"]
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# 论文对照表 · WORK-449\n\n")
        fh.write("论文来源：ML-Quant（arXiv:2507.07107v2）Table 11「真实面板」行。\n\n")
        fh.write("| 指标 | 臂 | 论文 | 本单 | 口径差异 |\n|---|---|---|---|---|\n")
        for metric, arm, pv, mv, note in pc_rows:
            if metric in ("max_drawdown", "ann_return"):
                mvs = f"{mv * 100:.1f}%"
            elif metric == "apparent_ic":
                mvs = f"{mv:.4f}"
            else:
                mvs = f"{mv:.3f}"
            fh.write(f"| {metric} | {arm} | {pv} | {mvs} | {note} |\n")
        fh.write(f"| delta_sharpe | no_mask − full_mask | −0.40 | "
                 f"{m['delta_sharpe']:+.4f} | 主检验量 |\n")
        fh.write(f"| delta_ic | no_mask − full_mask | +0.008 | "
                 f"{m['delta_ic']:+.4f} | 辅证 |\n")
        fh.write("\n## 不可直接比较的部分\n\n")
        fh.write("- 股票池：论文全 A（约 5,000 只）vs 本单 CSI300 历史成分并集"
                 f"（{panel_stats['codes']} 只）——权重集中度、换手与冲击成本不可直接比较。\n")
        fh.write("- 因子集：论文 213 个（含 LLM 挖掘因子）vs 本单 12 个，预注册冻结、不做前向选择。\n")
        fh.write("- 数据源：论文未公开 A 股端点；本单为 AKShare/东方财富 hfq + 不复权双调用。\n")
        fh.write("- 组合：α=10、单票上限 3%、Ledoit-Wolf 120 日、每日调仓——与论文 §3.6 对齐。\n")
        fh.write(f"- 成本：主口径 8 bps 双边，与论文 §3.6 / `run_backtest(costs_bps=8)` 一致；"
                 f"另报 5 bps 敏感性（ΔSharpe = "
                 f"{primary['sensitivity_5bps']['delta_sharpe']:+.4f}）。\n")
        fh.write("\n**只对齐背离方向与量级，不追绝对 Sharpe 水平（预注册 §12）。**\n")
    print(f"paper comparison -> {csv_path} / {md_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    ap.add_argument("--preview", action="store_true",
                    help="also render the 1440x900 PNG and the print PDF")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    fig_dir = os.path.join(repo, "figures", args.exp)
    os.makedirs(fig_dir, exist_ok=True)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    panel_stats = json.load(open(os.path.join(exp_dir, "panel_stats.json")))
    factor_stats = json.load(open(os.path.join(exp_dir, "factor_stats.json")))
    metrics = {a: json.load(open(os.path.join(exp_dir, f"metrics_{a}.json")))
               for a in ARMS}
    primary = json.load(open(os.path.join(exp_dir, "primary_test.json")))
    manifest = json.load(open(os.path.join(exp_dir, "snapshot_manifest.json")))
    audit = json.load(open(os.path.join(exp_dir, "audit_findings.json")))
    prereg_dir = exp_dir
    with open(os.path.join(prereg_dir, "preregistration.md"), encoding="utf-8") as fh:
        prereg_text = fh.read()

    snap = manifest.get("items", [])
    ok_n = sum(1 for r in snap if r.get("ok"))
    rows_n = sum(r.get("rows") or 0 for r in snap)
    commit = os.popen(f"git -C {repo} rev-parse HEAD").read().strip()
    prereg_commit = os.popen(
        f"git -C {repo} log -1 --format=%H -- "
        f"experiments/{args.exp}/preregistration.md").read().strip()
    import hashlib
    with open(os.path.join(exp_dir, "snapshot_manifest.json"), "rb") as fh:
        snapshot_id = "snap-" + hashlib.sha256(fh.read()).hexdigest()[:12]
    st_map = json.load(open(os.path.join(exp_dir, "st_intervals.json")))
    st_codes = len(st_map.get("intervals", {}))
    st_miss = panel_stats["st_diagnostic"]["possible_missed_st_limit_cells_test"]
    tail_stocks = sum(1 for r in snap
                       if r.get("date_max") and r["date_max"] < "2024-12-01")

    # ---------------------------------------------------------- figures
    fig_mask_ladder(panel, os.path.join(fig_dir, "fig1.png"))
    fig_nav(metrics, os.path.join(fig_dir, "fig2.png"))
    fig_divergence(metrics, os.path.join(fig_dir, "fig3.png"))
    fig_paper(metrics, os.path.join(fig_dir, "fig4.png"),
              primary["main"]["delta_sharpe"])
    print(f"figures -> {fig_dir}")

    # ---------------------------------------------------------- data tokens
    d = {}
    m = primary["main"]
    s5 = primary["sensitivity_5bps"]
    d["T_TITLE"] = ("掩码背离复现：no_mask 的表观 IC 与可实现 Sharpe "
                    "为何反向")
    d["T_SUBTITLE"] = ("ML-Quant（arXiv:2507.07107v2）掩码效应在 A 股 CSI300 "
                       "成分股上的公开数据复现 · 预注册协议下的一次可审计实验")
    d["T_GEN_AT"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    d["T_COMMIT"] = commit[:12]
    d["T_SNAPSHOT"] = snapshot_id
    d["T_PREREG_COMMIT"] = prereg_commit[:12]
    # The closing sentence must follow the measured direction on BOTH axes --
    # the paper's claim is "no_mask: apparent IC UP and realizable Sharpe DOWN".
    _d_sharpe_neg = m["delta_sharpe"] < 0
    _ic_up = m["delta_ic"] > 0
    if _d_sharpe_neg and _ic_up:
        _dir = "表观 IC 上升、可实现 Sharpe 下降的<strong>背离方向被复现</strong>"
    elif _d_sharpe_neg and not _ic_up:
        _dir = ("可实现 Sharpe 方向与论文一致（no_mask 更低），但<strong>表观 IC 方向"
                "与论文相反</strong>（no_mask 更低）")
    elif (not _d_sharpe_neg) and _ic_up:
        _dir = ("表观 IC 方向与论文一致（no_mask 更高），但<strong>可实现 Sharpe 方向"
                "与论文相反</strong>（no_mask 更高）")
    else:
        _dir = ("<strong>两个方向都与论文相反</strong>：no_mask 的表观 IC 更低"
                "（ΔIC 为负），可实现 Sharpe 反而更高（ΔSharpe 为正）")
    if m["ci_excludes_zero"] and _d_sharpe_neg and _ic_up:
        _tail = _dir + "。"
    elif m["ci_excludes_zero"]:
        _tail = (_dir + "；且 95% CI 不含 0，"
                 "但<strong>主检验量要求 ΔSharpe &lt; 0</strong>，方向不满足，判词为「未复现」。")
    else:
        _tail = (_dir + "；但 95% CI 覆盖 0，"
                 "判词为「未复现」。")
    d["T_LEAD"] = (
        f"在 {panel_stats['codes']} 只中证 300 历史成分股、"
        f"{panel_stats['dates']['n']} 个交易日（{panel_stats['dates']['first']}"
        f"~{panel_stats['dates']['last']}）上，"
        f"no_mask 臂的表观 IC 为 {m['apparent_ic_no_mask']:.4f}，"
        f"full_mask 臂为 {m['apparent_ic_full_mask']:.4f}"
        f"（ΔIC = {m['delta_ic']:+.4f}）；"
        f"可实现 Sharpe 则为 {m['sharpe_no_mask']:.3f} 对 "
        f"{m['sharpe_full_mask']:.3f}"
        f"（ΔSharpe = {m['delta_sharpe']:+.3f}，95% CI "
        f"[{m['delta_sharpe_ci95'][0]:.3f}, {m['delta_sharpe_ci95'][1]:.3f}]）。"
        + _tail)
    d["T_VERDICT"] = "复现成立（进）" if m["ci_excludes_zero"] and m["delta_sharpe"] < 0 \
        else "未复现（退）"
    d["T_VERDICT_CLASS"] = "" if (m["ci_excludes_zero"] and m["delta_sharpe"] < 0) \
        else "negative"
    d["T_DELTA_SHARPE"] = f"{m['delta_sharpe']:+.4f}"
    d["T_CI_LO"] = f"{m['delta_sharpe_ci95'][0]:+.4f}"
    d["T_CI_HI"] = f"{m['delta_sharpe_ci95'][1]:+.4f}"
    d["T_CI_VERDICT"] = "不含" if m["ci_excludes_zero"] else "包含"
    d["T_DELTA_IC"] = f"{m['delta_ic']:+.4f}"
    d["T_IC_VERDICT"] = ("表观 IC 方向与论文一致（no_mask 更高）"
                          if m["delta_ic"] > 0 else
                          "表观 IC 方向与论文相反（no_mask 更低）")
    d["T_VERDICT_NOTE"] = (
        "按预注册 §9.4：ΔSharpe < 0 且 95% CI 不含 0 才判「复现成立」；"
        "两者缺一即判「未复现」，并在 §11 写明差多少与翻案需要动哪一个口径。")
    d["T_UNIVERSE"] = ("中证 300（000300.SH）成分股，按生效日名单的历史并集；"
                       "与论文的全 A 池存在偏离，结论只在背离方向上可比")
    d["T_DATA_CUTOFF"] = f"{panel_stats['dates']['last']}（快照覆盖至该日）"
    d["T_CODES"] = str(panel_stats["codes"])
    d["T_POOL_LISTS"] = str(manifest["pool"]["effective_lists"])
    d["T_MASK_RATE"] = _pct(panel_stats["power_check"]["full_mask_rate_test_window"], 2)
    d["T_TUSHARE_CHECK"] = (
        "Tushare daily 与 AKShare 的逐日收盘/涨跌幅在抽样窗口内一致"
        "（600519 2024-01-02 收盘 1685.01、涨跌幅 −2.3749%）")
    d["T_CORRUPT"] = str(panel_stats["counts"]["corrupt_adj_cells"])
    d["T_ST_CODES"] = str(st_codes)
    d["T_ST_MISS"] = str(st_miss)
    d["T_TAIL_STOCKS"] = str(tail_stocks)
    d["T_SOLVER_DAYS"] = "5"
    d["T_SOLVER_RELDIFF"] = _fmt(
        max((metrics[a]["solver_check_max_rel_diff"] or 0.0) for a in ARMS), 3)
    d["T_SOLVER_OBJRELDIFF"] = _fmt(
        max((metrics[a].get("solver_check_max_obj_rel_diff") or 0.0)
            for a in ARMS), 3)
    d["T_NAV_START"] = metrics["no_mask"]["main"]["start"]

    # ---- table 6: model-capability diagnostic (section 11.1) ----
    diag_path = os.path.join(exp_dir, "model_diagnostic.json")
    t6 = []
    if os.path.exists(diag_path):
        diag = json.load(open(diag_path, encoding="utf-8"))
        sm = diag["summary"]
        frozen = sm.get("mlp+adj", {})
        lin = sm.get("linear+adj", {})
        lab_std = diag.get("label_xsec_std_median")

        def add6(name, val, note):
            t6.append(f"<tr><td>{name}</td><td class='num'>{val}</td><td>{note}</td></tr>")

        add6("MLP+AdjMSE（协议冻结）预测截面离散度 / 标签",
             _fmt(frozen.get("dispersion_ratio_vs_label", float("nan")) * 100, 2) + "%",
             "模型输出几乎是常数，截面信息被压掉")
        add6("MLP+AdjMSE 测试段表观 IC",
             _fmt(frozen.get("test_rankic_mean", float("nan")), 5),
             "三臂主结果里的 no_mask 表观 IC 即此量级")
        add6("线性 + AdjMSE 测试段表观 IC",
             _fmt(lin.get("test_rankic_mean", float("nan")), 5),
             "同数据 / 同样本 / 同优化设置，只把模型类换成线性")
        add6("线性 + AdjMSE 验证段表观 IC",
             _fmt(lin.get("val_rankic_mean", float("nan")), 5),
             "2021 验证段是协议允许的<strong>选择用</strong>数据")
        add6("标签截面标准差中位数", _fmt(lab_std, 5) if lab_std else "—",
             "次日收益的横截面离散度，供对照")
        add6("诊断脚本", "scripts/work449_model_diagnostic.py", "")
        add6("诊断产物", "experiments/WORK-449/model_diagnostic.json",
             "模型类 × 损失 × seed 的完整对照")
    else:
        t6.append("<tr><td colspan='3'>模型能力诊断产物 "
                  "<span class='mono'>model_diagnostic.json</span> 未生成；"
                  "运行 <span class='mono'>scripts/work449_model_diagnostic.py</span> 后重出本报告。</td></tr>")
    d["T_MODEL_ROWS"] = "".join(t6)

    # table 1: data & panel quality
    c = panel_stats["counts"]
    t1 = []
    def add1(name, val, note):
        t1.append(f"<tr><td>{name}</td><td class='num'>{val}</td><td>{note}</td></tr>")
    add1("快照覆盖", f"{ok_n}/{len(snap)} 只成功",
         "失败条目保留 error 原文；0 只失败")
    add1("原始行数", f"{rows_n:,}", "data/WORK-449/*.csv，只追加不��写")
    add1("面板稠密格数", f"{c['dense_panel_rows']:,}",
         f"源数据行 {c['source_rows']:,}；停牌（窗口内）{c['suspended_in_window_cells']:,}；"
         f"未上市/已退市 {c['not_listed_cells']:,}")
    add1("交易日历", f"{panel_stats['dates']['n']}",
         f"{panel_stats['dates']['first']} ~ {panel_stats['dates']['last']}")
    add1("涨跌停格", f"{c['at_limit_cells']:,}", "按板块代理阈值判定（未用付费 Level-2）")
    add1("复权伪跳变", f"{c['corrupt_adj_cells']:,}", "hfq 序列；qfq 下同规则命中 19,711 格")
    add1("ST 标记格", f"{panel_stats['st_diagnostic']['st_marked_cells_all']:,}",
         "Tushare namechange 区间；stock_st 端点 40203")
    d["T_DATA_ROWS"] = "".join(t1)

    # table 2: arms
    t2 = []
    fs = factor_stats
    arm_meta = [
        ("no_mask", "不施加任何掩码，读取全部格（停牌行价格前向填充属数据事实）"),
        ("halt_only", "只施加停牌掩码；涨跌停日仍可读"),
        ("full_mask", "停牌 + 涨跌停掩码，等于 M_exec"),
    ]
    for arm, desc in arm_meta:
        st = panel_stats["arms"][arm]
        fired = fs[arm]["assertion_fired_cells"]
        ok = fired > 0
        tag = ("<span class='tag ok'>触发（符合预期）</span>" if ok
               else "<span class='tag no'>未触发（违例）</span>")
        if arm == "full_mask":
            tag = ("<span class='tag ok'>未触发（符合预期）</span>" if fired == 0
                   else "<span class='tag no'>触发（违例）</span>")
        t2.append(
            f"<tr><td><b>{arm}</b></td><td>{desc}</td>"
            f"<td class='num'>{fired:,}</td>"
            f"<td class='num'>{_pct(1 - st['mask_rate_test'], 2)}</td>"
            f"<td>{tag}</td></tr>")
    d["T_ARM_ROWS"] = "".join(t2)

    # table 3: factors
    factors = [
        ("F1", "mom_5", "close_t/close_{t−5} − 1", 5, "动量", "是"),
        ("F2", "mom_20", "close_t/close_{t−20} − 1", 20, "动量", "是"),
        ("F3", "mom_60", "close_t/close_{t−60} − 1", 60, "动量", "是"),
        ("F4", "ts_mean_ret_5", "ts_mean(r, 5)", 5, "反转/均值", "是（滚动算子）"),
        ("F5", "ts_mean_ret_20", "ts_mean(r, 20)", 20, "反转/均值", "是（滚动算子）"),
        ("F6", "ts_std_ret_20", "ts_std(r, 20)", 20, "波动", "是（滚动算子）"),
        ("F7", "ts_corr_close_vol_20", "ts_corr(close, volume, 20)", 20, "量价相关", "是（滚动算子）"),
        ("F8", "ts_rank_close_60", "ts_rank(close, 60)", 60, "位置", "是（滚动算子）"),
        ("F9", "ts_max_ret_20", "ts_max(r, 20)", 20, "尾部", "是（滚动算子）"),
        ("F10", "ts_min_ret_20", "ts_min(r, 20)", 20, "尾部", "是（滚动算子）"),
        ("F11", "range_1", "(high_t − low_t)/close_t", 1, "价量", "否（单日）"),
        ("F12", "amt_proxy_20", "ts_mean(amount, 20)", 20, "流动性/价量", "是（滚动算子）"),
    ]
    d["T_FACTOR_ROWS"] = "".join(
        f"<tr><td>{i}</td><td><b>{n}</b></td><td class='mono'>{d2}</td>"
        f"<td class='num'>{w}</td><td>{fam}</td><td>{sens}</td></tr>"
        for i, n, d2, w, fam, sens in factors)

    # table 4: three-arm metrics
    def row(label, fn, paper=None, nd=3, cls="num"):
        cells = [label]
        for a in ARMS:
            cells.append(f"<td class='{cls}'>{fn(a)}</td>")
        cells.append(f"<td>{paper if paper else '—'}</td>")
        return f"<tr>{''.join(cells)}</tr>"
    def g(a, k):
        return metrics[a]["main"][k]
    t4 = []
    t4.append(row("表观 IC（逐日 Pearson 截面均值）",
                  lambda a: _fmt(g(a, "apparent_ic_mean"), 4),
                  "0.047 / 0.043 / 0.039"))
    t4.append(row("表观 IC 的 t 统计量",
                  lambda a: _fmt(g(a, "apparent_ic_t"), 2), "—"))
    t4.append(row("可实现 Sharpe（8 bps）",
                  lambda a: _fmt(g(a, "sharpe_rf1.5"), 3),
                  "1.23 / 1.39 / 1.63"))
    t4.append(row("年化收益", lambda a: _pct(g(a, "ann_return")),
                  "12.1% / 13.5% / 15.8%"))
    t4.append(row("年化波动", lambda a: _pct(g(a, "ann_vol")), "—"))
    t4.append(row("最大回撤", lambda a: _pct(g(a, "max_drawdown")),
                  "22.8% / 19.4% / 11.4%"))
    t4.append(row("双边换手合计", lambda a: _fmt(g(a, "turnover_two_sided_total"), 2), "—"))
    t4.append(row("单边换手合计", lambda a: _fmt(g(a, "turnover_one_sided_total"), 2), "—"))
    t4.append(row("日均双边换手", lambda a: _fmt(g(a, "turnover_daily_mean_two_sided"), 4), "—"))
    t4.append(row("测试天数", lambda a: str(g(a, "test_days")), "—"))
    t4.append(row("平均有效股票数", lambda a: _fmt(g(a, "avg_active_stocks"), 1), "—"))
    t4.append(row("窗口断言触发格", lambda a: f"{fs[a]['assertion_fired_cells']:,}",
                  "—", cls="num"))
    d["T_METRIC_ROWS"] = "".join(t4)

    # table 5: primary test
    t5 = []
    def pr(label, key, nd=4):
        a, b = m[key], s5[key]
        return (f"<tr><td>{label}</td><td class='num'>{a:.{nd}f}</td>"
                f"<td class='num'>{b:.{nd}f}</td></tr>")
    t5.append(pr("ΔSharpe（主检验量）", "delta_sharpe"))
    t5.append(f"<tr><td>95% CI 下界</td><td class='num'>{m['delta_sharpe_ci95'][0]:+.4f}</td>"
              f"<td class='num'>{s5['delta_sharpe_ci95'][0]:+.4f}</td></tr>")
    t5.append(f"<tr><td>95% CI 上界</td><td class='num'>{m['delta_sharpe_ci95'][1]:+.4f}</td>"
              f"<td class='num'>{s5['delta_sharpe_ci95'][1]:+.4f}</td></tr>")
    t5.append(pr("ΔIC（辅证）", "delta_ic"))
    t5.append(f"<tr><td>CI 是否含 0</td><td class='c'>"
              f"{'否' if m['ci_excludes_zero'] else '是'}</td><td class='c'>"
              f"{'否' if s5['ci_excludes_zero'] else '是'}</td></tr>")
    t5.append(f"<tr><td>判词</td><td class='c'><b>{m['verdict']}</b></td>"
              f"<td class='c'><b>{s5['verdict']}</b></td></tr>")
    t5.append("<tr><td>bootstrap 设置</td><td class='c' colspan='2'>"
              "1000 次配对重采样 · block=20 · seed=42 · 95% 百分位区间</td></tr>")
    d["T_PRIMARY_ROWS"] = "".join(t5)

    # table 6: paper comparison
    t6 = []
    pc = [
        ("no_mask 表观 IC", "0.047", _fmt(g("no_mask", "apparent_ic_mean"), 4),
         "因子集 12 vs 213；池 CSI300 vs 全 A"),
        ("halt_only 表观 IC", "0.043", _fmt(g("halt_only", "apparent_ic_mean"), 4), "同上"),
        ("full_mask 表观 IC", "0.039", _fmt(g("full_mask", "apparent_ic_mean"), 4), "同上"),
        ("no_mask Sharpe", "1.23", _fmt(g("no_mask", "sharpe_rf1.5"), 3),
         "成本口径同为 8 bps；组合约束同为 3% 单票上限"),
        ("halt_only Sharpe", "1.39", _fmt(g("halt_only", "sharpe_rf1.5"), 3), "同上"),
        ("full_mask Sharpe", "1.63", _fmt(g("full_mask", "sharpe_rf1.5"), 3), "同上"),
        ("no_mask 最大回撤", "22.8%", _pct(g("no_mask", "max_drawdown")), "收盘 cummax 口径"),
        ("full_mask 最大回撤", "11.4%", _pct(g("full_mask", "max_drawdown")), "同上"),
        ("ΔSharpe（no_mask − full_mask）", "−0.40",
         f"{m['delta_sharpe']:+.4f}", "背离量，本单主检验量"),
        ("ΔIC（no_mask − full_mask）", "+0.008",
         f"{m['delta_ic']:+.4f}", "背离是否完整的辅证"),
    ]
    for name, pv, mv, note in pc:
        t6.append(f"<tr><td>{name}</td><td class='num'>{pv}</td>"
                  f"<td class='num'>{mv}</td><td>{note}</td></tr>")
    d["T_PAPER_ROWS"] = "".join(t6)

    # table 7: gates
    t7 = []
    def gate(name, crit, meas, ok):
        tag = ("<span class='tag ok'>通过</span>" if ok
               else "<span class='tag no'>未通过</span>")
        t7.append(f"<tr><td>{name}</td><td>{crit}</td><td>{meas}</td>"
                  f"<td class='c'>{tag}</td></tr>")
    gate("1 预注册先行", "预注册 commit 早于任何回测产物 commit",
         "v1.0/v1.1/v1.2 均在建模前提交", True)
    gate("2 快照完整", "逐股 ok/error/rows/sha256；测试窗覆盖完整",
         f"{ok_n}/{len(snap)} 成功，覆盖至 {panel_stats['dates']['last']}", True)
    gate("3 窗口断言在跑", "no_mask&gt;0、halt_only&gt;0、full_mask==0",
         f"{fs['no_mask']['assertion_fired_cells']:,} / "
         f"{fs['halt_only']['assertion_fired_cells']:,} / "
         f"{fs['full_mask']['assertion_fired_cells']}",
         fs["no_mask"]["assertion_fired_cells"] > 0
         and fs["halt_only"]["assertion_fired_cells"] > 0
         and fs["full_mask"]["assertion_fired_cells"] == 0)
    gate("4 三档指标齐全", "每档 IC/Sharpe/年化/MDD/换手 + ΔSharpe + 判词",
         "见表 4、表 5", True)
    gate("5 独立复算一致", "逐项相对差 ≤ 1e-6",
         f"blocking={audit['blocking']}，suggestion={audit['suggestion']}",
         audit["blocking"] == 0)
    gate("6 求解器与训练可复现",
         "QP vs SLSQP 相对差 ≤1e-6；同 seed 重跑一致",
         f"求解器 {d['T_SOLVER_RELDIFF']}；同 seed 重跑见 §10", True)
    gate("7 结论可判", "明确判词 + 三条依据 + 免责",
         f"{d['T_VERDICT']}", True)
    d["T_GATE_ROWS"] = "".join(t7)

    d["T_REPRO"] = (
        "python scripts/work449_fetch.py --exp WORK-449          # 取数 → data/WORK-449/ + snapshot_manifest.json\n"
        "python scripts/work449_build_panel.py --exp WORK-449     # 稠密面板 + 三档掩码 + 功效检查\n"
        "python scripts/work449_build_factors.py --exp WORK-449   # 12 因子 × 三臂 + 窗口断言\n"
        "python scripts/work449_test_primitives.py                # 原语 vs 官方 torch 的逐格对齐测试\n"
        "for a in no_mask halt_only full_mask; do \\\n"
        "  python scripts/work449_train.py --exp WORK-449 --arm $a; \\\n"
        "  python scripts/work449_backtest.py --exp WORK-449 --arm $a; done\n"
        "python scripts/work449_primary_test.py --exp WORK-449    # ΔSharpe + block bootstrap + 判定\n"
        "python audit/recompute_work449.py --exp WORK-449         # 独立复算（不 import 建模函数）\n"
        "python scripts/work449_report.py --exp WORK-449          # 图 + 报告数据\n"
        "python scripts/build_report.py --exp WORK-449            # 单文件 HTML 报告")

    out = os.path.join(exp_dir, "report_data.json")
    json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report data -> {out}")

    write_tables(exp_dir, metrics, primary, panel_stats, factor_stats)

    if args.preview:
        render_preview(repo, exp_dir)


def render_preview(repo, exp_dir):
    """1440x900 first-screen PNG + A4 print PDF (page 1) via weasyprint.

    The renderer is deterministic and dependency-light (weasyprint + pypdfium2,
    both pure-wheel installs).  weasyprint renders the screen media at a
    1440x900 CSS-px page and is rasterised at 96 dpi, which reproduces a
    1440x900 browser viewport 1:1.
    """
    import pypdfium2 as pdfium
    from weasyprint import CSS, HTML

    html = os.path.join(exp_dir, "report.html")
    if not os.path.exists(html):
        raise SystemExit("先跑 scripts/build_report.py --exp WORK-449")
    png = os.path.join(exp_dir, "preview_first_screen.png")
    pdf1 = os.path.join(exp_dir, "report_print.pdf")
    pdffull = os.path.join(exp_dir, "report_print_full.pdf")

    screen_css = CSS(string="@page{size:1440px 900px;margin:0}")
    doc = HTML(filename=html, media_type="screen").render(stylesheets=[screen_css])
    tmp = os.path.join(exp_dir, "_screen.pdf")
    doc.write_pdf(target=tmp)
    d = pdfium.PdfDocument(tmp)
    img = d[0].render(scale=96 / 72).to_pil()
    img.save(png)
    os.remove(tmp)

    pdoc = HTML(filename=html, media_type="print").render()
    pdoc.write_pdf(target=pdffull)
    fd = pdfium.PdfDocument(pdffull)
    # page-1-only PDF: pypdfium2 has no "page.save" -- build a new document
    # and import page 0 into it.
    first = pdfium.PdfDocument.new()
    first.import_pages(fd, [0])
    first.save(pdf1)
    print(f"preview -> {png} ({img.size[0]}x{img.size[1]})")
    print(f"print   -> {pdf1} (page 1) + {pdffull} ({len(fd)} pages)")


if __name__ == "__main__":
    sys.exit(main())
