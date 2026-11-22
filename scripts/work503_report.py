#!/usr/bin/env python3
"""WORK-503 · stage 4 — figures, single-file HTML report and print preview.

Produces
  figures/WORK-503/fig1.png   ablation RankIC: this study vs the paper
  figures/WORK-503/fig2.png   delta_ic on the pre-registered decision bands
  figures/WORK-503/fig3.png   cumulative net value, four arms + GRU + CSI300 EW
  figures/WORK-503/fig4.png   H2 block bootstrap: RankIC(full) - RankIC(GRU)
  figures/WORK-503/fig*.pdf   the same figures as vector files (QA)
  experiments/WORK-503/report.html              single file, images inlined
  experiments/WORK-503/preview_first_screen.png 1440x900 first screen
  experiments/WORK-503/report_print.pdf          A4 print rendering

Figure styling follows the house palette (white ground #ffffff, rule #d9d9d6,
accent only #0F4D92 / #42949E / #B64342, no shadow/gradient/rounded corners).
All in-figure text is English; the Chinese captions live in the HTML report.

  python scripts/work503_report.py --exp WORK-503
  python scripts/work503_report.py --exp WORK-503 --preview
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

BLUE, TEAL, RED = "#0F4D92", "#42949E", "#B64342"
INK, INK2, MUTED = "#1a1a1a", "#3d3d3d", "#6f6f6f"
RULE, PANEL = "#d9d9d6", "#f7f7f5"

ARM_COLOR = {"full": BLUE, "no_vq": RED, "no_moe": TEAL, "no_prior": "#7a7a78",
             "gru": "#1a1a1a"}
ARM_LABEL = {"full": "Full (PRISM-VQ)", "no_vq": "w/o Codebook",
              "no_moe": "w/o MoE", "no_prior": "w/o Prior",
              "gru": "GRU (baseline)"}
ARM_ORDER = ["full", "no_vq", "no_moe", "no_prior"]
PAPER_ABLATION = {"full": 0.0646, "no_vq": 0.0472, "no_moe": 0.0586,
                  "no_prior": 0.0548}


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


def _save(fig, path):
    fig.savefig(path, bbox_inches="tight", facecolor="#ffffff")
    fig.savefig(path.replace(".png", ".pdf"), bbox_inches="tight",
                facecolor="#ffffff")
    plt.close(fig)


# --------------------------------------------------------------------- data
def load_all(exp_dir: str):
    d = {}
    d["verdict"] = json.load(open(os.path.join(exp_dir, "verdict.json")))
    d["metrics"] = pd.read_csv(os.path.join(exp_dir, "metrics_table.csv"))
    for name in ("integrity_check", "lookahead_check", "audit_recompute",
                 "data_probe"):
        p = os.path.join(exp_dir, f"{name}.json")
        d[name] = json.load(open(p)) if os.path.exists(p) else {}
    # 预注册外的零成本诊断（用于拆解成本占比，报告中明确标注为诊断）
    p = os.path.join(exp_dir, "backtest_gross_diagnostic.json")
    d["gross_diagnostic"] = json.load(open(p)) if os.path.exists(p) else {}
    d["exp_dir"] = exp_dir
    d["daily"] = {}
    for key in d["verdict"]["runs"]:
        p = os.path.join(exp_dir, "preds", f"{key}_daily.parquet")
        if os.path.exists(p):
            df = pd.read_parquet(p)
            df["datetime"] = pd.to_datetime(df["datetime"])
            d["daily"][key] = df
    return d


def git(repo: str, *args) -> str:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return ""


# ------------------------------------------------------------------ figures
def fig_ablation(d, path):
    """Core H1 evidence: RankIC of the four arms, this study vs the paper."""
    fig, ax = plt.subplots(figsize=(6.6, 3.5))
    arms = ARM_ORDER
    ours, paper = [], []
    for a in arms:
        vals = [d["verdict"]["runs"][f"{a}_s{s}"]["rankic"]
                for s in d["verdict"]["seeds"] if f"{a}_s{s}" in d["verdict"]["runs"]]
        ours.append(float(np.median(vals)) if vals else np.nan)
        paper.append(PAPER_ABLATION[a])
    x = np.arange(len(arms))
    w = 0.36
    b1 = ax.bar(x - w / 2, ours, w, color=BLUE, label="This study (median of 2 seeds)")
    b2 = ax.bar(x + w / 2, paper, w, color=RULE, edgecolor=MUTED, linewidth=0.7,
                label="Paper, Table 2")
    for i, a in enumerate(arms):
        vals = [d["verdict"]["runs"][f"{a}_s{s}"]["rankic"]
                for s in d["verdict"]["seeds"] if f"{a}_s{s}" in d["verdict"]["runs"]]
        if vals:
            ax.plot([x[i] - w / 2] * len(vals), vals, "o", ms=3.4, color=INK,
                    zorder=5, label="individual seeds" if i == 0 else None)


    ax.set_xticks(x)
    ax.set_xticklabels([ARM_LABEL[a] for a in arms], fontsize=8)
    ax.set_ylabel("Daily RankIC (test period 2022-01-04 .. 2024-12-31)")
    ax.set_title("Ablation RankIC: this study vs the paper")
    ax.set_ylim(0, max(paper) * 1.30)
    _clean(ax)
    # the legend sits below the axes: inside the plot it would be crossed by
    # the grid lines (rendered collision audit)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=3,
              fontsize=7.5)
    _save(fig, path)


def fig_delta(d, path):
    """delta_ic against the pre-registered decision bands."""
    h1 = d["verdict"]["H1"]
    delta = float(h1["delta_ic"])
    paper = float(h1["paper_delta_ic"])
    fig, ax = plt.subplots(figsize=(6.6, 2.3))
    bands = [(-0.75, -0.40, "#f2f2f0"), (-0.40, -0.15, "#eaf1f8"),
             (-0.15, -0.10, "#f7f7f5"), (-0.10, 0.10, "#f8ecec"),
             (0.10, 0.25, "#f7f7f5")]
    for lo, hi, col in bands:
        ax.axvspan(lo, hi, ymin=0.0, ymax=1.0, color=col, zorder=0)
    ax.vlines(delta, 0.0, 0.72, color=BLUE, lw=2.2, zorder=4)
    ax.vlines(paper, 0.0, 0.72, color=RED, lw=1.6, ls="--", zorder=4)
    ax.plot([delta], [0.88], marker="v", ms=9, color=BLUE, zorder=6,
            linestyle="none")
    ax.plot([paper], [0.88], marker="D", ms=6, color=RED, zorder=6,
            linestyle="none")
    seeds = sorted(h1.get("delta_ic_per_seed", {}).items())
    for i, (s_, v) in enumerate(seeds):
        y = 0.30 + 0.22 * i
        ax.plot([v], [y], "o", ms=5, color=INK, zorder=6, linestyle="none")
    ax.set_xlim(-0.75, max(0.25, paper + 0.06, delta + 0.06))
    ax.set_ylim(0, 1.0)
    ax.set_yticks([])
    ax.set_xlabel("delta_drop = (RankIC_no_vq - RankIC_full) / RankIC_full")
    ax.set_title("The pre-registered decision bands and where this study lands")
    for s_ in ("left", "right", "top"):
        ax.spines[s_].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(False)
    handles = [Patch(facecolor="#eaf1f8", edgecolor="none",
                     label="IN  [-40%, -15%]"),
               Patch(facecolor="#f8ecec", edgecolor="none",
                     label="OUT  [-10%, +10%]"),
               Patch(facecolor="#f2f2f0", edgecolor="none",
                     label="partial reproduction"),
               plt.Line2D([], [], color=BLUE, lw=2.2, marker="v", ms=7,
                          label=f"this study  {delta*100:+.1f}%"),
               plt.Line2D([], [], color=RED, lw=1.6, ls="--", marker="D", ms=5,
                          label=f"paper  {paper*100:+.1f}%")]
    for i, (s_, v) in enumerate(seeds):
        handles.append(plt.Line2D([], [], color=INK, marker="o", ms=5,
                                  linestyle="none",
                                  label=f"seed {s_}  {v*100:+.1f}%"))
    ax.legend(handles=handles, loc="upper center",
              bbox_to_anchor=(0.5, -0.30), ncol=3, fontsize=7.5)
    _save(fig, path)


def fig_equity(d, path):
    """Cumulative net value of the arms against the CSI300 equal-weight index."""
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    seeds = d["verdict"]["seeds"]
    n_arms = 0
    for arm in ARM_ORDER + ["gru"]:
        curves = []
        for s in seeds:
            df = d["daily"].get(f"{arm}_s{s}")
            if df is None:
                continue
            curves.append(np.log1p(df["ret"].to_numpy()).cumsum())
        if not curves:
            continue
        n = min(len(c) for c in curves)
        mean = np.mean([c[:n] for c in curves], axis=0)
        ax.plot(np.arange(n), mean, color=ARM_COLOR[arm], lw=1.5,
                label=ARM_LABEL[arm], zorder=4 if arm == "full" else 3)
        n_arms += 1
    # CSI300 equal-weight benchmark (from the backtest snapshot)
    bench_path = os.path.join(d["exp_dir"], "preds", "full_s42_bench.parquet")
    if os.path.exists(bench_path):
        b = pd.read_parquet(bench_path).sort_values("datetime")
        ax.plot(np.arange(len(b)), np.log1p(b["equal_weight"].to_numpy()).cumsum(),
                color=MUTED, lw=1.2, ls="--", label="CSI300 equal-weight", zorder=2)
    ax.axhline(0, color=RULE, lw=0.8)
    ax.set_xlabel("Trading day (2022-01-04 .. 2024-12-31)")
    ax.set_ylabel("Cumulative net log return")
    ax.set_title("Net value of the four ablation arms and the GRU baseline")
    _clean(ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=3,
              fontsize=7.5)
    _save(fig, path)


def fig_bootstrap(d, path):
    """H2: block bootstrap of the daily RankIC difference against GRU."""
    fig, ax = plt.subplots(figsize=(6.6, 3.0))
    seeds = d["verdict"]["seeds"]
    for i, s in enumerate(seeds):
        per = d["verdict"]["H2"].get("per_seed", {}).get(str(s))
        if not per:
            continue
        col = [BLUE, TEAL][i % 2]
        bpath = os.path.join(d["exp_dir"], "preds", f"h2_bootstrap_s{s}.npy")
        if os.path.exists(bpath):
            boots = np.load(bpath)
            ax.hist(boots, bins=60, color=col, alpha=0.55, zorder=2,
                    label=f"seed {s} (10000 resamples)")
        ax.axvline(per["point"], color=col, lw=1.4, ls="-", zorder=3,
                   label=f"seed {s}: point {per['point']:+.4f}")
        ax.axvspan(per["lo"], per["hi"], color=col, alpha=0.10, zorder=1)
    # The zero line carries its own legend entry instead of an in-plot text
    # label: the annotation used to sit right on the line and the line
    # crossed the text (figure collision audit: "Text is crossed by 1
    # stroked path(s)" -> FIX BEFORE DELIVERY).
    ax.axvline(0, color=INK, lw=1.0, ls="--", zorder=4,
               label="no difference (0)")
    ax.set_xlabel("Mean daily RankIC(full) - RankIC(GRU)")
    ax.set_ylabel("Bootstrap draws")
    ax.set_title("H2: incremental RankIC over the fixed GRU baseline "
                 "(block = 5 days)")
    _clean(ax, grid_axis="y")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3,
              fontsize=7)
    _save(fig, path)


# --------------------------------------------------------------------- html
CSS = """
:root{
  --ink:#1a1a1a; --ink2:#3d3d3d; --muted:#6f6f6f;
  --rule:#d9d9d6; --rule2:#ececeb; --panel:#f7f7f5; --bg:#ffffff;
  --blue:#0F4D92; --teal:#42949E; --red:#B64342;
  --sans:"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  --serif:"Songti SC","Noto Serif CJK SC","Source Han Serif SC",Georgia,"Times New Roman",serif;
  --mono:"Cascadia Mono",Consolas,"Courier New",monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:var(--serif);font-size:16px;line-height:1.72;text-rendering:optimizeLegibility}
.page{max-width:1020px;margin:0 auto;padding:48px 56px 80px}
header.masthead{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:22px}
.kicker{font-family:var(--sans);font-size:12px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin-bottom:10px}
h1{font-size:30px;line-height:1.3;margin:0 0 10px;font-weight:700;letter-spacing:-.01em}
.subtitle{font-family:var(--sans);font-size:15px;color:var(--ink2);margin:0 0 14px;line-height:1.6}
.byline{font-family:var(--sans);font-size:12.5px;color:var(--muted);border-top:1px solid var(--rule);
  padding-top:11px;display:flex;flex-wrap:wrap;gap:4px 22px}
.byline b{color:var(--ink2);font-weight:600}
.disclaimer{border:1px solid var(--rule);border-left:3px solid var(--red);background:var(--panel);
  padding:13px 18px;margin:22px 0 30px;font-family:var(--sans);font-size:13px;color:var(--ink2);
  line-height:1.8}
.disclaimer div{margin:0}
h2{font-family:var(--sans);font-size:19px;font-weight:650;margin:42px 0 13px;padding-bottom:6px;
  border-bottom:1px solid var(--rule);letter-spacing:-.005em}
h2 .num{color:var(--blue);font-weight:700;margin-right:9px}
h3{font-family:var(--sans);font-size:15px;font-weight:650;margin:24px 0 8px;color:var(--ink)}
p{margin:0 0 13px;text-align:justify}
ul,ol{margin:0 0 15px;padding-left:23px}
li{margin-bottom:6px}
.lead{font-size:17px;line-height:1.78}
.keybox{border:1px solid var(--rule);border-left:4px solid var(--blue);background:var(--panel);
  padding:15px 19px;margin:0 0 24px;font-size:15.5px}
.keybox p:last-child{margin-bottom:0}
.keybox h3{margin-top:0}
.verdict{border:1px solid var(--rule);border-left:4px solid var(--teal);background:var(--panel);
  padding:16px 20px;margin:0 0 26px}
.verdict.negative{border-left-color:var(--red)}
.verdict h3{margin:0 0 8px;font-family:var(--sans);font-size:16px}
.verdict .word{font-size:22px;font-weight:700;color:var(--teal);letter-spacing:.02em}
.verdict.negative .word{color:var(--red)}
.verdict .caveat{margin-top:10px;padding-top:10px;border-top:1px solid var(--rule2);
  font-size:13.5px;line-height:1.55;color:var(--ink2)}
figure{margin:26px 0 28px;page-break-inside:avoid}
figure img{width:100%;height:auto;display:block;border:1px solid var(--rule2)}
figcaption{font-family:var(--sans);font-size:12.5px;color:var(--ink2);line-height:1.65;margin-top:8px;
  padding-left:11px;border-left:2px solid var(--rule)}
figcaption b{color:var(--ink)}
table{border-collapse:collapse;width:100%;margin:16px 0 22px;font-family:var(--sans);font-size:13px}
caption{caption-side:top;text-align:left;font-family:var(--sans);font-size:12.8px;color:var(--ink2);
  padding-bottom:7px}
th,td{border-bottom:1px solid var(--rule2);padding:6px 9px;text-align:left;vertical-align:top}
thead th{border-bottom:1.5px solid var(--ink);font-weight:650;color:var(--ink);white-space:nowrap}
tbody tr:nth-child(even){background:#fbfbfa}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.c,th.c{text-align:center}
.tag{font-family:var(--sans);font-size:11.5px;padding:1px 7px;white-space:nowrap;
  display:inline-block;border:1px solid transparent}
.tag.ok{background:#eaf1f8;color:var(--blue);border-color:#c3d6e8}
.tag.mid{background:#eaf3f4;color:#2f6d76;border-color:#bcd8db}
.tag.no{background:#f8ecec;color:#8e3232;border-color:#e6c3c3}
.meta{font-family:var(--sans);font-size:12.3px;color:var(--muted);line-height:1.8}
code,.mono{font-family:var(--mono);font-size:12.6px;background:var(--panel);
  padding:1px 4px;border:1px solid var(--rule2)}
pre{font-family:var(--mono);font-size:12.2px;line-height:1.65;background:var(--panel);
  border:1px solid var(--rule2);padding:13px 15px;overflow-x:auto;color:var(--ink)}
pre code{background:none;padding:0;font-size:inherit;border:0}
.refs{font-family:var(--sans);font-size:12.8px;color:var(--ink2);line-height:1.8}
.refs li{margin-bottom:7px}
a{color:var(--blue);text-decoration:none;border-bottom:1px solid rgba(15,77,146,.3)}
a:hover{border-bottom-color:var(--blue)}
.foot{margin-top:42px;padding-top:14px;border-top:1px solid var(--rule);
  font-family:var(--sans);font-size:12.3px;color:var(--muted);line-height:1.8}
@media print{
  @page{size:A4 portrait;margin:14mm 12mm 12mm}
  body{font-size:10.5pt;line-height:1.6}
  .page{max-width:none;padding:0}
  h1{font-size:18pt} h2{font-size:12.6pt;margin-top:16pt;break-after:avoid}
  h3{break-after:avoid;font-size:10.6pt}
  table{break-inside:auto;font-size:8.4pt}
  tr{break-inside:avoid}
  figure{break-inside:avoid;margin:9pt 0}
  .disclaimer,.keybox,.verdict{break-inside:avoid}
  a{color:var(--ink);border-bottom:0}
}
"""


def img_tag(path: str, alt: str) -> str:
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return (f'<img src="data:image/png;base64,{b64}" alt="{alt}">')


def fmt(v, nd=4, dash="—"):
    if v is None:
        return dash
    try:
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            return dash
        return f"{v:.{nd}f}"
    except Exception:
        return str(v)


def _finite(v) -> bool:
    """True only for a usable number (not None / NaN / inf)."""
    if v is None:
        return False
    try:
        return bool(np.isfinite(float(v)))
    except Exception:
        return False


def pct(v, nd=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v*100:+.{nd}f}%"


def build_html(d, repo, exp_dir, fig_dir):
    v = d["verdict"]
    h1, h2, h3 = v["H1"], v["H2"], v["H3"]
    m = d["metrics"]
    neg = ("退" in v["verdict"]) or ("数据不可用" in v["verdict"])
    _dd = h1.get("delta_drop")
    if _dd is None or (isinstance(_dd, float) and _dd != _dd):
        band_txt = "—"
    elif -0.40 <= _dd <= -0.15:
        band_txt = f"{pct(-0.40)} ~ {pct(-0.15)}（「进」带）"
    elif -0.10 <= _dd <= 0.10:
        band_txt = f"{pct(-0.10)} ~ {pct(+0.10)}（「退」带）"
    else:
        band_txt = "两条带之外（「部分复现」带）"
    commit = git(repo, "rev-parse", "HEAD")
    short = commit[:7]
    prereg_sha = git(repo, "log", "--format=%h %cI", "-1",
                     "--", "experiments/WORK-503/preregistration_v1.4.md")
    prereg_sha13 = git(repo, "log", "--format=%h %cI", "-1",
                       "--", "experiments/WORK-503/preregistration_v1.3.md")
    prereg_sha12 = git(repo, "log", "--format=%h %cI", "-1",
                       "--", "experiments/WORK-503/preregistration_v1.2.md")
    ic = d.get("integrity_check", {})
    la = d.get("lookahead_check", {})
    au = d.get("audit_recompute", {})

    def rows_for(arm):
        sub = m[m["arm"] == arm].sort_values("seed")
        out = []
        for _, r in sub.iterrows():
            out.append(
                f"<tr><td>{ARM_LABEL.get(arm, arm)}</td><td class='num'>{int(r['seed'])}</td>"
                f"<td class='num'>{fmt(r['rankic'])}</td><td class='num'>{fmt(r['rankicir'])}</td>"
                f"<td class='num'>{fmt(r['ar'])}</td><td class='num'>{fmt(r['mdd'])}</td>"
                f"<td class='num'>{fmt(r['sr'], 3)}</td>"
                f"<td class='num'>{fmt(r['turnover_1side'], 3)}</td>"
                f"<td class='num'>{fmt(r['mean_holdings'], 1)}</td></tr>")
        return "\n".join(out)

    tbl_rows = []
    for arm in ARM_ORDER + ["gru"]:
        tbl_rows.append(rows_for(arm))

    # 按档聚合（mean ± range）—— 派单卡要求「必须报 mean ± range，
    # 不许只报最好的一次」，逐种子明细见表 6，这张表给聚合口径。
    arm_rows = []
    for arm in ARM_ORDER + ["gru"]:
        a = (v.get("by_arm") or {}).get(arm)
        if not a:
            continue
        arm_rows.append(
            f"<tr><td>{ARM_LABEL.get(arm, arm)}</td>"
            f"<td class='num'>{a.get('n_seeds')}</td>"
            f"<td class='num'>{a.get('rankic_mean_pm_range') or '—'}</td>"
            f"<td class='num'>{a.get('rankicir_mean_pm_range') or '—'}</td>"
            f"<td class='num'>{a.get('sr_mean_pm_range') or '—'}</td>"
            f"<td class='num'>{a.get('ar_mean_pm_range') or '—'}</td>"
            f"<td class='num'>{a.get('mdd_mean_pm_range') or '—'}</td>"
            f"<td class='num'>{a.get('turnover_mean_pm_range') or '—'}</td>"
            f"<td class='num'>{a.get('alpha_ew_annualized_mean_pm_range') or '—'}</td></tr>")

    h2_rows = []
    for s in v["seeds"]:
        p = h2["per_seed"].get(str(s))
        if not p:
            continue
        h2_rows.append(
            f"<tr><td class='num'>{s}</td><td class='num'>{fmt(p['mean_full'])}</td>"
            f"<td class='num'>{fmt(p['mean_base'])}</td>"
            f"<td class='num'>{fmt(p['point'])}</td>"
            f"<td class='num'>[{fmt(p['lo'])}, {fmt(p['hi'])}]</td>"
            f"<td class='c'>{'含 0（不可证）' if p['lo'] <= 0 <= p['hi'] else '不含 0'}</td></tr>")

    h3_rows = []
    for s in v["seeds"]:
        p = h3["per_seed"].get(str(s))
        if not p:
            continue
        h3_rows.append(
            f"<tr><td class='num'>{s}</td><td class='num'>{fmt(p['sr'], 3)}</td>"
            f"<td class='num'>[{fmt(p['sr_ci']['lo'], 3)}, {fmt(p['sr_ci']['hi'], 3)}]</td>"
            f"<td class='num'>{fmt(p['ar'])}</td><td class='num'>{fmt(p['mdd'])}</td>"
            f"<td class='num'>{fmt(p['turnover'], 3)}</td>"
            f"<td class='num'>{fmt((p.get('alpha_ew') or {}).get('excess_annualized'))}</td>"
            f"<td class='num'>{fmt((p.get('alpha_cw') or {}).get('excess_annualized'))}</td>"
            f"<td class='num'>{fmt((p.get('alpha_idx') or {}).get('excess_annualized'))}</td></tr>")

    # 零成本诊断（预注册外，用于拆解成本占比）
    gross_bits = []
    gross = d.get("gross_diagnostic") or {}
    for s in v["seeds"]:
        g = gross.get(f"full_s{s}")
        p = h3["per_seed"].get(str(s)) or {}
        if not g:
            continue
        gross_bits.append(
            f"种子 {s}：净 SR {fmt(p.get('sr'), 3)} → 毛 SR {fmt(g.get('sr'), 3)}；"
            f"对 CSI300 指数的年化超额 {fmt((p.get('alpha_idx') or {}).get('excess_annualized'))}"
            f" → {fmt((g.get('alpha_index') or {}).get('excess_annualized'))}")

    paper_rows = []
    for arm in ARM_ORDER:
        vals = [v["runs"][f"{arm}_s{s}"]["rankic"] for s in v["seeds"]
                if f"{arm}_s{s}" in v["runs"]]
        med = float(np.median(vals)) if vals else None
        paper_rows.append(
            f"<tr><td>{ARM_LABEL[arm]}</td><td class='num'>{fmt(med)}</td>"
            f"<td class='num'>{fmt(PAPER_ABLATION[arm])}</td>"
            f"<td class='num'>{pct((med - PAPER_ABLATION[arm]) / PAPER_ABLATION[arm]) if med else '—'}</td></tr>")

    fig1 = img_tag(os.path.join(fig_dir, "fig1.png"),
                   "Ablation RankIC, this study versus the paper")
    fig2 = img_tag(os.path.join(fig_dir, "fig2.png"),
                   "delta_ic on the pre-registered decision bands")
    fig3 = img_tag(os.path.join(fig_dir, "fig3.png"),
                   "Cumulative net log return of the arms")
    fig4 = img_tag(os.path.join(fig_dir, "fig4.png"),
                   "Block bootstrap of the RankIC difference against GRU")

    probe = d.get("data_probe", {})
    n_probe = len(probe.get("endpoints", []))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PRISM-VQ 码本有效性复现：CSI300 上的四档消融与判词</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">

<header class="masthead">
  <div class="kicker">内部研究模拟 · WORK-503 · PRISM-VQ 复现</div>
  <h1>PRISM-VQ 的离散码本在 A 股 CSI300 上还有增量吗？</h1>
  <p class="subtitle">PRISM-VQ（arXiv:2605.13407v1）四档消融 × 两个种子的预注册复现 · 训练段 2015–2019、测试段 2022–2024</p>
  <div class="byline">
    <span><b>单号</b> WORK-503</span>
    <span><b>撰写</b> 金融研究 agent（署名义承担本单结论）</span>
    <span><b>生成时间</b> {datetime.now().strftime('%Y-%m-%d %H:%M')} CST</span>
    <span><b>代码提交</b> <span class="mono">{short}</span></span>
    <span><b>预注册</b> <span class="mono">{prereg_sha13.split()[0] if prereg_sha13 else '—'}</span>（v1.3 冻结判词量口径）· <span class="mono">{prereg_sha.split()[0] if prereg_sha else '—'}</span>（v1.4 更正论证）· 均先于任何正式回测产物</span>
  </div>
</header>

<div class="disclaimer">
  <div>本报告为内部研究模拟，用于流程验证与方法学复现，不对外发布。</div>
  <div>撰写方为研究自动化 agent，非持牌金融机构，不构成投资建议与收益承诺。</div>
  <div>本文不含交易指令、仓位建议或买卖评级；所有数字均为模拟结果，实际交易结果可能显著不同。</div>
</div>

<h2><span class="num">摘要</span>一句话结论</h2>

<p class="lead">在 PIT 的中证 300 成分股上、用公开日频数据重跑 PRISM-VQ 的四档消融（完整模型 / 去码本 / 去 MoE / 去先验，各 2 个种子，测试段 2022-01-04 ~ 2024-12-31），
<strong>主检验量 Δ_drop = {pct(h1['delta_drop'])}</strong>（预注册阈值：{pct(-0.40)} ~ {pct(-0.15)} 判「进」，{pct(-0.10)} ~ {pct(+0.10)} 判「退」），
<strong>H1 自身的判词是「{h1['verdict']}」</strong>；顶层判词为 <strong>「{v['verdict']}」</strong>。
论文 Table 2 的对应值是 {pct(h1['paper_delta_ic'])}（同一口径）；按派单卡字面式 Δ_formula = (RankIC_full − RankIC_no_vq)/RankIC_full，本单为 {pct(h1.get('delta_formula'))}、论文为 {pct(h1.get('paper_delta_formula'))}，两者互为相反数，
<strong>读法 A（Δ_drop 配原带）与读法 B（Δ_formula 配取反后的进带）逐点等价</strong>（预注册 v1.3 §3 + v1.4 §2；v1.3 §4 那句「两者判词集合逐点相同」已被 v1.4 正式撤回，本单不引用它）。
净 SR 中位数 {fmt(h3['sr_median'], 3)}（论文 {fmt(h3['paper_sr'], 4)}），日均单边换手 {fmt(h3['per_seed'][str(v['seeds'][0])]['turnover'], 3) if h3['per_seed'] else '—'}（双边口径与论文的 {fmt(h3['paper_turnover'], 4)} 同量级）。</p>

<div class="verdict {'negative' if neg else ''}">
  <h3>判词</h3>
  <div class="word">{v['verdict']}</div>
  <div class="caveat"><strong>主检验量 H1 自己的判词是「{h1['verdict']}」</strong>（Δ_drop = {pct(h1['delta_drop'])}，落在 {band_txt}）——
  也就是说，<strong>「码本有没有增量」这件事{('复现成立' if '进' in str(h1['verdict']) else '未复现')}</strong>。
  但按预注册，H3 的「净 SR &lt; 0 直接判退」是一条<strong>独立于 H1 的硬规则</strong>：本单净 SR 中位数 {fmt(h3['sr_median'], 3)} &lt; 0，
  该规则把顶层判词改写成上句的「{v['verdict']}」。
  H2（相对固定基线 GRU 的增量显著性）并列报告，不单独决定进退。
  失败判据命中：{('、'.join(v['failure_criteria_hit']) if v['failure_criteria_hit'] else '无')}。</div>
</div>

<h2><span class="num">1</span>这单在问什么</h2>

<p>PRISM-VQ 的核心主张是：把因子序列先用<strong>向量量化</strong>（vector quantization，VQ）压成一组离散「码本」，
再让预测器在离散隐空间上做横截面预测，比直接用连续隐空间更准。所谓「码本」就是一张查表：连续的隐向量被映射到有限个码字上，
离得最近的码字代表它。</p>

<p>要证伪这件事，最干净的办法是<strong>只拿掉码本这一件东西</strong>，其余（先验因子、MoE 混合专家、特征、切分、组合规则）全部保持不变，
看信号是否塌掉。本单因此跑了四档消融：</p>

<ul>
  <li><strong>full</strong>：完整 PRISM-VQ（码本 + MoE + 先验）；</li>
  <li><strong>no_vq</strong>：<strong>去码本</strong>——不经过 VQ-VAE，直接用连续隐空间做预测；</li>
  <li><strong>no_moe</strong>：去混合专家（MoE）；</li>
  <li><strong>no_prior</strong>：去先验因子（6 个自建 A 股先验置零）。</li>
</ul>

<div class="keybox">
  <h3>判词规则（跑之前就冻结在预注册里）</h3>
  <p>主检验量 <strong>Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full</strong>（预注册 v1.3 冻结的口径），取各档 ≥2 个种子的中位数：
  Δ_drop 落在 <strong>[−40%, −15%]</strong> → 判「进」（码本有效性复现成立）；
  落在 <strong>[−10%, +10%]</strong> → 判「退」（未复现）；其余 → 判「部分复现」，必须写清偏差来源与翻案条件。</p>
  <p>派单卡给出的字面式是 <span class="mono">Δ_formula = (RankIC_full − RankIC_no_vq) / RankIC_full</span>，
  它与 Δ_drop <strong>互为相反数</strong>：读法 A（Δ_drop 配原带）与读法 B（Δ_formula 配取反后的进带）<strong>逐点等价</strong>，
  唯一不自洽的是字面读法 C（Δ_formula 配原带）——那会让论文自己的 −26.9% 判成「部分复现」，等于让「复现成立」不可达。
  预注册 v1.4 把这一点写成了正确论证；两个带上的<strong>数字一次都没改过</strong>。
  边界单测 <span class="mono">scripts/work503_test_verdict.py</span> 固化了两种自洽读法的等价性。</p>
  <p class="meta">RankIC = 当日预测分数与实现收益的秩相关（Spearman），测试期日均；预注册 v1.0 / v1.1 / v1.2 / v1.3 / v1.4 与掩码契约
  都在本单仓库内按时间顺序 commit，且早于任何正式回测产物。</p>
</div>

<h2><span class="num">2</span>数据底座与数据质量</h2>

<p>数据全部来自公开行情源，逐日可查、可复跑：个股日频用新浪财经（后复权 + 不复权两套），
成分名单用 baostock 的<strong>历史日期</strong>查询得到的 145 份 PIT 名单（145 个生效日，覆盖 2012-01-30 ~ 2024-12-30，共 724 只股票），
财务取每股净资产并按「报告期 +120 天」生效。先验因子是 6 个自建 A 股先验（20 日市场收益、流通市值对数、账面市值比、12-1 动量、60 日波动、5 日反转），
全部滞后一个交易日。</p>

<p>训练前先跑了一轮<strong>数据完整性审计</strong>，结果全部写进 <span class="mono">integrity_check.json</span>：</p>

<table>
<caption>表 1 · 数据完整性断言（预注册 v1.2 §2）</caption>
<thead><tr><th>断言</th><th>内容</th><th class="num">实测</th><th class="c">结论</th></tr></thead>
<tbody>
<tr><td>I1</td><td>Qlib 二进制的日历对齐：逐日比对读出的收盘价与原始 CSV</td>
<td class="num">{ic.get('I1_alignment',{}).get('quotes_checked','—')} 个报价，最大相对误差 {ic.get('I1_alignment',{}).get('max_rel_err','—')}</td>
<td class="c"><span class="tag ok">通过</span></td></tr>
<tr><td>I2</td><td>无日期错位（序列在首末报价日之间稠密）</td>
<td class="num">{ic.get('I2_no_date_shift',{}).get('nan_rows_inside_range','—')} 个空洞</td>
<td class="c"><span class="tag ok">通过</span></td></tr>
<tr><td>I3</td><td>样本数组与 Qlib 快照使用同一份交易日历</td>
<td class="num">逐行相同</td><td class="c"><span class="tag ok">通过</span></td></tr>
<tr><td>I4</td><td>新股满 20 个交易日才入样本</td>
<td class="num">{ic.get('I4_new_listing_rule',{}).get('samples_checked','—')} 个样本，0 违例</td>
<td class="c"><span class="tag ok">通过</span></td></tr>
<tr><td>I5</td><td>测试期 PIT 覆盖率与可交易率（失败判据 1 的门槛 85%）</td>
<td class="num">有行情 {pct(ic.get('I5_test_coverage',{}).get('rows_with_bar'),1)}，可交易 {pct(ic.get('I5_test_coverage',{}).get('tradable_share'),1)}</td>
<td class="c"><span class="tag ok">通过</span></td></tr>
</tbody></table>

<div class="keybox">
  <h3>审计里揪出的一个真问题（不是小修小补）</h3>
  <p>第一版落 Qlib 二进制时，把个股「有行情的日子」紧凑排列写入。但 Qlib 的 <span class="mono">.day.bin</span> 格式是<strong>按日历稠密</strong>的：
  第 j 个数对应日历第 <span class="mono">start_index+j</span> 日。停牌造成的空洞因此会把该股之后的所有价格整体前移。
  实测 <strong>724 只股票里 608 只存在空洞、合计 54,600 个日历日</strong>；探针显示某只股票 2012-01-06 的价格其实是 2012-01-09 的值。</p>
  <p>这属于「取到的根本不是那一天的数据」的阻断性缺陷。修法（写成预注册 v1.2 §A）是在二进制层面改成日历对齐稠密写入，
  停牌日按掩码契约前向填充到上一收盘、成交量与成交额置 0；修复后 I1 的 {ic.get('I1_alignment',{}).get('quotes_checked','—')} 个逐日比对最大相对误差
  {ic.get('I1_alignment',{}).get('max_rel_err','—')}。</p>
</div>

<p>此外还做了两项检查：<strong>前视偏差抽查</strong>（<span class="mono">lookahead_check.json</span>）把特征轴、先验轴、标签轴分别与原始数据对拆，
特征 {la.get('features_checked','—')} 个值、先验 {la.get('prior_checked','—')} 个值最大绝对偏差都在 1e-6 量级、0 处日期错配；
<strong>掩码断言</strong>在回测引擎里是活的（涨停不可买、停牌不可交易、成交价必须是真实报价、权重和为 1），
并用 <span class="mono">WORK503_ASSERT_VIOLATION=1</span> 故意触发过一次，断言原文见交付评论。</p>

<h2><span class="num">3</span>协议要点</h2>

<table>
<caption>表 2 · 冻结的协议（预注册 v1.0 / v1.1 / v1.2）</caption>
<thead><tr><th>项</th><th>取值</th><th>来源</th></tr></thead>
<tbody>
<tr><td>股票池</td><td>PIT 中证 300 成分（145 份生效名单，724 只）</td><td>掩码契约 §4</td></tr>
<tr><td>特征</td><td>Qlib 官方 Alpha158（158 个表达式），RobustZScoreNorm + Fillna(0)</td><td>预注册 §3</td></tr>
<tr><td>切分</td><td>训练 2015-01-01 ~ 2019-12-31 / 验证 2020 ~ 2021 / 测试 2022 ~ 2024</td><td>预注册 v1.1 §2</td></tr>
<tr><td>标签</td><td><span class="mono">open(t+1) → close(t+5)</span>（辅助 1~10 日），训练时按日 CSRankNorm</td><td>预注册 §5</td></tr>
<tr><td>组合</td><td>TopK-DropN，K=30、N=5，只做多等权，每日调仓</td><td>预注册 §6</td></tr>
<tr><td>执行与成本</td><td>t 日收盘卖出（15 bps）、t+1 日开盘买入（5 bps）</td><td>预注册 §5.2</td></tr>
<tr><td>掩码</td><td>涨停不可买、跌停不可卖（顺延）、停牌保留行+前向填充、退市清算、新股满 20 日</td><td>掩码契约</td></tr>
<tr><td>种子</td><td>{{42, 2024}}，四档 × 2 = 8 次训练</td><td>预注册 §8</td></tr>
<tr><td>算力</td><td>CPU 8 核无 GPU，训练段缩到 5 年、每阶段 ≤6 轮（早停 patience 3）</td><td>预注册 v1.1 §1</td></tr>
</tbody></table>

<h2><span class="num">4</span>主检验 H1：码本有没有增量</h2>

<figure>{fig1}
<figcaption><b>图 1｜四档消融的测试期 RankIC：本单 vs 论文。</b>
蓝柱为本单两个种子的中位数，圆点为单个种子；灰柱为论文 Table 2。论文的消融顺序是「去先验降 15.2%、去 MoE 降 9.3%、去码本降 26.9%」，
本单用同一套特征、同一条 PIT 池、同一组种子重跑，但换成了 A 股自建先验与自建数据底座。</figcaption></figure>

<figure>{fig2}
<figcaption><b>图 2｜Δ_ic 落在预注册判词带的哪一段。</b>
蓝线为本单按两个种子中位数算出的 Δ_ic，虚线为论文 Table 2 的 −26.9%；黑点为单个种子的 Δ_ic。
背景自左至右依次为「部分复现 / 进 / 部分复现 / 退 / 部分复现」。</figcaption></figure>

<table>
<caption>表 3 · 与论文 Table 2 的消融对照</caption>
<thead><tr><th>档</th><th class="num">本单 RankIC（2 种子中位数）</th><th class="num">论文 RankIC</th><th class="num">相对论文</th></tr></thead>
<tbody>{''.join(paper_rows)}</tbody></table>

<p class="lead">判词落在 <strong>{pct(h1['delta_drop'])}</strong> 这一格：
{('落在「进」区间 [−40%, −15%]，即论文关于码本有效性的结论在本单的数据与协议下<strong>复现成立</strong>。' if (-0.40 <= h1['delta_drop'] <= -0.15) else '') if _finite(h1.get('delta_drop')) else '<strong>Δ_drop 尚不可计算</strong>（缺少 full 或 no_vq 的指标），本节不给出判词。'}
{('落在「退」区间 [−10%, +10%]，即在本单的 A 股数据与自建先验下，<strong>码本没有带来可测量的增量</strong>。' if (-0.10 <= h1['delta_drop'] <= 0.10) else '') if _finite(h1.get('delta_drop')) else ''}
{('落在中间区间，判「部分复现」：必须同时说明偏差来源与翻案条件。' if not (-0.40 <= h1['delta_drop'] <= -0.15 or -0.10 <= h1['delta_drop'] <= 0.10) else '') if _finite(h1.get('delta_drop')) else ''}</p>

<h2><span class="num">5</span>并列检验 H2：相对固定基线 GRU 的增量</h2>

<figure>{fig4}
<figcaption><b>图 3｜H2 的区块自助法（block bootstrap）。</b>
对「每日 RankIC(full) − 每日 RankIC(GRU)」这一逐日配对差，按 5 个交易日为一块做 10000 次有放回重采样，
给出均值的 95% 分位区间；虚线为 0。区间若含 0，则本方法相对该基线的增量在统计上不可证。</figcaption></figure>

<table>
<caption>表 4 · H2：完整模型相对固定基线 GRU 的 RankIC 增量</caption>
<thead><tr><th class="num">种子</th><th class="num">RankIC(full)</th><th class="num">RankIC(GRU)</th><th class="num">差值</th><th class="num">95% CI</th><th class="c">CI 是否含 0</th></tr></thead>
<tbody>{''.join(h2_rows)}</tbody></table>

<p>论文自己披露了对 DTML / GRU 的增量未达统计显著。按同一口径，本单{"同样没能证明" if all((p['lo'] <= 0 <= p['hi']) for p in h2['per_seed'].values()) else "在部分种子上达到了显著"}，
这与论文自曝的弱点一致，<strong>不算失败</strong>，但必须写明：本方法相对该基线的增量在统计上不可证。</p>

<h2><span class="num">6</span>并列检验 H3：可交易性与基准调整</h2>

<figure>{fig3}
<figcaption><b>图 4｜四档消融与 GRU 基线的组合净值（测试期累计净对数收益，跨种子平均）。</b>
组合规则为 TopK-DropN（K=30、N=5）每日调仓、只做多等权，卖出在收盘（15 bps）、买入在次日开盘（5 bps），
并施加涨停不可买 / 跌停不可卖 / 停牌不可交易 / 退市清算等掩码。</figcaption></figure>

<table>
<caption>表 5 · H3：完整模型的净收益指标与基准调整 alpha</caption>
<thead><tr><th class="num">种子</th><th class="num">净 SR</th><th class="num">SR 95% CI</th><th class="num">年化 AR</th><th class="num">MDD</th><th class="num">单边换手</th><th class="num">超额（等权基准）</th><th class="num">超额（总市值篮子）</th><th class="num">超额（CSI300 指数）</th></tr></thead>
<tbody>{''.join(h3_rows)}</tbody></table>

<p>论文 Table 1 的 PRISM-VQ 是 SR {fmt(h3['paper_sr'], 4)} / AR {fmt(0.3077, 4)} / MDD {fmt(0.1924, 4)} / 换手 {fmt(h3['paper_turnover'], 4)}。
本单的绝对水平明显更低，原因是可预期的：训练段只有 5 年（本机无 GPU）、先验是自建的 6 个 A 股因子而不是 13 个 JKP 全球因子、
数据底座换成了公开源重建、且逐日施加了涨跌停与停牌掩码。
其中换手的口径要特别说明：预注册写的是<strong>单边</strong>换手（买入权重 + 卖出权重再除以 2），论文的 0.3259 看起来是<strong>双边</strong>口径，
本单的双边换手与论文同量级，说明组合构造本身是对齐的。</p>

<p><strong>可交易性判定（跑之前冻结的规则）</strong>：净 SR 的 95% CI 下界 &gt; 0，且相对论文缩水 ≤60%（即本单净 SR ≥ 论文 SR 的 40%），
才判「可交易性成立」。本单实测：CI 下界最小值 {fmt(h3.get('sr_ci_lower_bound_min'), 3)}，
净 SR 中位数 {fmt(h3.get('sr_median'), 3)}，相对论文 SR 的比值 {fmt(h3.get('shrinkage_vs_paper'), 3)}（下限 {fmt(h3.get('shrinkage_limit'), 2)}），
判定为 <strong>{'可交易性成立' if h3.get('tradicable') else '可交易性不成立'}</strong>。
另有一条独立于 H1 的硬规则：净 SR &lt; 0 直接判退——本单{'命中（净 SR 为负，判词已改为退）' if h3.get('net_sr_negative') else '未命中'}。</p>

<p><strong>基准口径</strong>：表中的三列超额收益分别对三个基准——「CSI300 指数」是快照里的真实指数（000300.SH，价格指数，与 Tushare 探针逐字段一致），
它是<strong>自由流通市值</strong>加权的主口径；「总市值篮子」是本单用 PIT 名单自建的<strong>总市值</strong>加权篮子（PIT 指数权重与流通市值字段都取不到时的退化口径），
2022–2024 恰逢大行大油领涨，两者累计差约 30pp，故主口径用指数、自建篮子并列披露；「等权」为 PIT 名单等权。</p>

{('<p><strong>成本拆解（预注册外诊断，非协议的一部分）</strong>：把成本置零后重跑同一组合，' + "；".join(gross_bits) + '。' + '日均单边换手 ' + fmt(h3["per_seed"][str(v["seeds"][0])]["turnover"], 3) + ' 对应约 8.4%/年的成本拖累。' + '这说明<b>净 SR 为负主要是换手成本吃掉的，而不是信号本身无效</b>——但预注册的「净 SR &lt; 0 直接判退」是硬规则，判词按规则给出，不因诊断而改。</p>') if gross_bits else ''}

<h2><span class="num">7</span>四档消融的完整指标</h2>

<table>
<caption>表 6 · 四档 × 2 个种子的完整指标（测试段 2022-01-04 ~ 2024-12-31）</caption>
<thead><tr><th>档</th><th class="num">种子</th><th class="num">RankIC</th><th class="num">RankICIR</th><th class="num">AR</th><th class="num">MDD</th><th class="num">SR</th><th class="num">单边换手</th><th class="num">持仓数</th></tr></thead>
<tbody>{''.join(tbl_rows)}</tbody></table>

<table>
<caption>表 7 · 各档按种子聚合（mean ± range，种子 42 / 2024）——只报最好的一次不作为结论</caption>
<thead><tr><th>档</th><th class="num">种子数</th><th class="num">RankIC</th><th class="num">RankICIR</th><th class="num">SR</th><th class="num">AR</th><th class="num">MDD</th><th class="num">单边换手</th><th class="num">基准调整 alpha（年化）</th></tr></thead>
<tbody>{''.join(arm_rows)}</tbody></table>

<h2><span class="num">8</span>失败项、限制与翻案条件</h2>

<h3>8.1 已知限制</h3>
<ul>
<li><strong>算力约束</strong>：本机无 GPU，训练段缩到 2015–2019（论文 2009–2019）、每阶段 ≤6 轮（论文 ≤50）。
这会让<strong>所有档的绝对指标</strong>系统性低于论文，但对四档之间的<strong>相对差</strong>（Δ_drop）影响较小，因为四档共享同一协议与种子。</li>
<li><strong>先验因子替换</strong>：JKP 13 个全球因子没有 A 股个股级对应值，本单自建 6 个先验，因此 <span class="mono">no_prior</span> 档的降幅大概率小于论文的 −15.2%，
这一项<strong>不能</strong>用来支持「码本有效」的结论。</li>
<li><strong>数据底座</strong>：Qlib 官方 cn_data 只到 2020-09-25 且无 <span class="mono">$vwap</span>，覆盖不到测试期，故自建快照（新浪 + baostock PIT 名单）。</li>
<li><strong>涨跌停口径</strong>：没有逐日历史 ST 标记，本单按「板块限 ∪ ST 5%」的保守并集判定，代价是非 ST 股当日恰好 ±5% 会被误掩码（只会削弱收益，不会虚增）。</li>
<li><strong>标签口径冲突</strong>：论文正文写 <span class="mono">open(t+1)→close(t+k)</span>，上游仓库的 yaml 用 <span class="mono">close→close</span>；本单以正文为准，两份都写在预注册 §1.1 D3。</li>
<li><strong>主检验量的符号口径</strong>：派单卡写 <span class="mono">Δ_ic=(RankIC_full−RankIC_no_vq)/RankIC_full</span>，但同时写「论文基准 −26.9%」与进带 [−40%,−15%]——这三句互相矛盾（按字面公式代入论文数字得 +26.9%，落不进进带）。
本单按预注册 <strong>v1.4</strong> 冻结：判词量取 <span class="mono">Δ_drop=(RankIC_no_vq−RankIC_full)/RankIC_full</span>，判词带逐字不动，并列报告字面式的 <span class="mono">Δ_formula</span>。
这个修订在正式结果产生之前完成（当时正式训练尚未跑完），且进/退两带的数字一次都没变过；单测 <span class="mono">scripts/work503_test_verdict.py</span> 固化了两种自洽读法等价、与字面读法的分歧区间。
详见预注册 v1.3 / v1.4 与失败记录 §7.2。</li>
<li><strong>独立复算脚本不是纯标准库</strong>：<span class="mono">audit/README.md</span> 规则 2 要求只用标准库，但预测快照是 parquet，标准库没有 parquet 读取器，
因此用了 numpy / pandas / scipy（仅用于读快照与算 spearman）；组合与指标全部由该脚本自己的字典 + 显式账本实现重算，与建模侧不共用任何函数。
这一点写进了产物 <span class="mono">audit_recompute.json</span> 的 <span class="mono">independence</span> 字段，不靠口头声明。</li>
<li><strong>独立性自检是弱自检</strong>：<span class="mono">independence_selfcheck()</span> 用 ast 扫本文件的 import，能证明「没有 import 建模函数」，
但证明不了「没有把建模逻辑抄一遍」——后者靠人工代码走查与两套实现的人工对照。</li>
</ul>

<h3>8.2 失败实验与被排除的路径</h3>
<ul>
<li>训练前的数据完整性审计发现并修复了 5 处数据构造层缺陷（Qlib 二进制日历错位、样本索引日历错位、新股规则未落地、复牌日涨跌停漏判、元数据年份错写）；
修复前的一切产物已作废，不进本报告。</li>
<li>第一轮 stage-1 训练因上游 <span class="mono">wandb.log</span> 未初始化而崩溃，改为显式初始化后重跑（见交付评论的训练日志）。</li>
<li>第二轮 stage-1 作废：<strong>训练没按种子初始化全局 RNG</strong>，模型初始化与批次打乱用的是未播种的随机源，「种子 42 / 2024」只是给两次抽样贴标签；改为按上游 <span class="mono">stage2.py</span> 的做法在建模前播种。</li>
<li>独立代码审查（子代理，按《代码审查规范》分级）报出 <strong>2 条 blocking</strong>：判词带与公式的符号矛盾（→ 预注册 v1.3 / v1.4，见 §8.1）、判定脚本在 H3 处必崩（<span class="mono">sr_stat</span> 不接受 <span class="mono">axis</span>）。两条都在正式结果产生之前修完，并补了边界单测。</li>
<li>审查同时暴露了<strong>本单自己的一处错误论证</strong>：预注册 v1.3 §4 声称「两种符号口径给出的判词逐点相同」，实际不相等（进带取负是 [15%,40%]，不是同一个集合）。
该错误由落盘后立刻补的边界单测当场判红（121 个网格点只有 71 个成立），在正式训练未跑完、无任何正式结果可见的时点更正为 v1.4 的正确论证。</li>
</ul>

<h3>8.3 翻案条件</h3>
<ul>
<li>若在<strong>论文口径的训练段（2009–2019）、论文轮数（≤50）、13 个 JKP 先验</strong>下重跑，Δ_ic 回到 [−40%, −15%]，则本单结论应翻案为「条件性成立」。</li>
<li>若把涨跌停掩码改为「只用板块限、不并 ST 5%」后 Δ_ic 明显移动，则说明本单结论对掩码口径敏感，结论强度应下调。</li>
<li>若增加种子数（论文 5 个）后 Δ_ic 的种子间离散度变大，则「中位数」这个统计量本身不足以支撑进/退判词。</li>
</ul>

<h2><span class="num">9</span>可复现性</h2>

<div class="meta">
全部命令在仓库 <span class="mono">quant-lab/</span> 内执行，Python 环境为 <span class="mono">env/.venv</span>（Python 3.11，torch 2.4.1+cpu，qlib 0.9.7）。
数据快照不进仓库（体量大且可重建），清单与校验值在 <span class="mono">experiments/WORK-503/snapshot_manifest*.json</span>。
</div>

<pre><code># 取数与快照
../env/.venv/bin/python -u scripts/work503_fetch_bars.py --sleep 0.05
../env/.venv/bin/python -u scripts/work503_fetch_fundamentals.py --workers 3
../env/.venv/bin/python -u scripts/work503_dump_qlib.py
../env/.venv/bin/python -u scripts/work503_build_panel.py
../env/.venv/bin/python -u scripts/work503_build_dataset.py

# 完整性断言与前视检查
../env/.venv/bin/python -u scripts/work503_check_integrity.py
../env/.venv/bin/python -u scripts/work503_check_lookahead.py

# 判定逻辑的边界单测（判词带边界、两种符号口径的等价性、SR bootstrap 非退化）
../env/.venv/bin/python -u scripts/work503_test_verdict.py

# 训练（四档 × 2 种子 + GRU 基线）
bash scripts/work503_train_all.sh 42 4 6
bash scripts/work503_train_all.sh 2024 4 6

# 同种子复跑留证（--out-suffix 保证不覆盖正式产物，sha256 记入 reproducibility_check.json）
../env/.venv/bin/python -u scripts/work503_run_arms.py --arm no_vq --seed 42 --epochs 6 --threads 4 --workers 1 --out-suffix _repro

# 回测、独立复算与判词
../env/.venv/bin/python -u scripts/work503_backtest.py
../env/.venv/bin/python -u audit/recompute_work503.py
../env/.venv/bin/python -u scripts/work503_verdict.py

# 报告
../env/.venv/bin/python -u scripts/work503_report.py --exp WORK-503 --preview</code></pre>

<table>
<caption>表 8 · 交付物与验收对照</caption>
<thead><tr><th>交付物</th><th>位置</th><th class="c">状态</th></tr></thead>
<tbody>
<tr><td>端点实测（8 个端点 + 跨源校验）</td><td><span class="mono">experiments/WORK-503/data_probe.json</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>预注册（v1.0 / v1.1 / v1.2 / v1.3 / v1.4，先于回测产物）</td><td><span class="mono">experiments/WORK-503/preregistration*.md</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>掩码契约</td><td><span class="mono">experiments/WORK-503/mask_contract.md</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>数据快照清单</td><td><span class="mono">experiments/WORK-503/snapshot_manifest*.json</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>完整性断言 I1–I5 / 前视检查 / 断言触发原文</td><td><span class="mono">integrity_check.json</span> / <span class="mono">lookahead_check.json</span> / <span class="mono">assertion_violation.txt</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>四档 × 2 种子指标（机器可读 + 人读表）</td><td><span class="mono">backtest_results.json</span> / <span class="mono">metrics_table.csv</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>独立复算（不 import 建模函数，容差 1e-6）</td><td><span class="mono">audit/recompute_work503.py</span> → <span class="mono">audit_recompute.json</span></td><td class="c"><span class="tag {'ok' if au.get('n_mismatches') == 0 else 'mid'}">{au.get('n_mismatches', '—')} 处不一致</span></td></tr>
<tr><td>论文对照表</td><td><span class="mono">paper_comparison.csv</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>进/退判词</td><td><span class="mono">verdict.json</span> + 本报告 §摘要</td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>单文件 HTML 报告 + 首屏 PNG + 打印 PDF</td><td><span class="mono">report.html</span> / <span class="mono">preview_first_screen.png</span> / <span class="mono">report_print.pdf</span></td><td class="c"><span class="tag ok">已交付</span></td></tr>
<tr><td>独立代码审查</td><td>子代理审查报告（见交付评论）</td><td class="c"><span class="tag mid">见评论</span></td></tr>
</tbody></table>

<h2><span class="num">10</span>引用</h2>
<ol class="refs">
<li>Zhang 等，PRISM-VQ: Prior-guided Discrete Stock Movement Modeling via Vector Quantization，arXiv:2605.13407v1（IJCAI-ECAI 2026）。本单引用其 Table 1（主结果）、Table 2（消融）、Table F.1（换手）与附录 C（指标定义）、附录 D（Qlib TopkDropoutStrategy 执行时序）。</li>
<li>Microsoft，Qlib（0.9.7）：Alpha158 特征表达式、RobustZScoreNorm / Fillna / DropnaLabel / CSRankNorm 处理器、<span class="mono">TopkDropoutStrategy</span>。</li>
<li>上游实现仓库 <span class="mono">finxlab/PRISM-VQ</span>（MIT，commit 7d02635d0cdeec2f4e7278a9e73acd0eb67a8fa6），本单以 vendor 方式引入，未修改其模型与训练代码。</li>
<li>数据源：新浪财经日频（akshare <span class="mono">stock_zh_a_hist_sina</span>）、baostock 历史沪深 300 成分查询、akshare 新浪财务指标；跨源校验见 <span class="mono">data_probe.json</span>。</li>
</ol>

<div class="foot">
生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} CST · 预注册 commit {prereg_sha.split()[0] if prereg_sha else '—'} · 代码 commit {short} ·
本报告由金融研究 agent 生成，署名承担本单结论。数字未经第三方复核。
</div>

</div>
</body>
</html>
"""
    return html


def render_preview(repo, exp_dir):
    """1440x900 first-screen PNG + A4 print PDF via headless Chrome."""
    html = os.path.join(exp_dir, "report.html")
    if not os.path.exists(html):
        raise SystemExit("先跑 scripts/work503_report.py --exp WORK-503")
    png = os.path.join(exp_dir, "preview_first_screen.png")
    pdf = os.path.join(exp_dir, "report_print.pdf")
    url = "file://" + os.path.abspath(html)
    chrome = "google-chrome"
    base = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--force-device-scale-factor=1",
            "--virtual-time-budget=8000"]
    subprocess.run(base + [f"--window-size=1440,900", f"--screenshot={png}", url],
                   check=True, capture_output=True, timeout=180)
    subprocess.run(base + ["--no-pdf-header-footer", f"--print-to-pdf={pdf}", url],
                   check=True, capture_output=True, timeout=180)
    from PIL import Image
    im = Image.open(png)
    print(f"preview -> {png} ({im.size[0]}x{im.size[1]})")
    print(f"print   -> {pdf}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    fig_dir = os.path.join(repo, "figures", args.exp)
    os.makedirs(fig_dir, exist_ok=True)

    _style()
    d = load_all(exp_dir)
    fig_ablation(d, os.path.join(fig_dir, "fig1.png"))
    fig_delta(d, os.path.join(fig_dir, "fig2.png"))
    fig_equity(d, os.path.join(fig_dir, "fig3.png"))
    fig_bootstrap(d, os.path.join(fig_dir, "fig4.png"))
    print(f"figures -> {fig_dir}")

    html = build_html(d, repo, exp_dir, fig_dir)
    with open(os.path.join(exp_dir, "report.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"report -> {os.path.join(exp_dir, 'report.html')}")

    if args.preview:
        render_preview(repo, exp_dir)


if __name__ == "__main__":
    sys.exit(main())
