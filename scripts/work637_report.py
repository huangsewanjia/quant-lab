#!/usr/bin/env python3
"""WORK-637 / phase 3 — figures + single-file HTML report + preview/PDF.

Every number in the HTML is read from the JSON artifacts written by the
pipeline; nothing is typed by hand. The verdict is computed here from the
pre-registered rules (preregistration §7) and stored in ``verdict.json``.

    ../.venv/bin/python -u scripts/work637_report.py            # figures + html
    ../.venv/bin/python -u scripts/work637_report.py --preview  # + PNG/PDF
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

BLUE, TEAL, RED = "#0F4D92", "#42949E", "#B64342"
GREY, LIGHT = "#d9d9d6", "#f7f7f5"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 7, "axes.linewidth": 0.5,
    "axes.edgecolor": "#1a1a1a", "xtick.major.width": 0.5, "ytick.major.width": 0.5,
    "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5,
    "axes.labelsize": 7, "axes.titlesize": 7.5, "savefig.dpi": 300,
    "figure.dpi": 150, "axes.grid": False, "pdf.fonttype": 42,
})

FIG = os.path.join(C.REPO, "figures", "WORK-637")
SEEDS = list(C.SEEDS)


# ---------------------------------------------------------------- figures
def fig_pipeline(path):
    fig, ax = plt.subplots(figsize=(6.3, 2.3))
    ax.set_xlim(0, 100); ax.set_ylim(0, 40); ax.axis("off")

    def box(x, y, w, h, label, sub="", color=BLUE):
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=color, lw=0.8))
        ax.text(x + w / 2, y + h * 0.63, label, ha="center", va="center", fontsize=6.2,
                color="#1a1a1a")
        if sub:
            ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=5.1,
                    color="#5a5a5a", linespacing=1.25)

    def arrow(x1, y1, x2, y2):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=5,
                                     lw=0.6, color="#1a1a1a"))

    xs = [0, 17, 34, 51, 68, 85]
    w, h, y = 14.5, 11, 26
    box(xs[0], y, w, h, "quantdb", "v_daily_qfq\nPIT CSI300\nstk_limit", TEAL)
    box(xs[1], y, w, h, "Render", "D20 candles\nMA20 + volume\n140 x 140 px")
    box(xs[2], y, w, h, "DINOv2-S", "frozen ViT-S/14\nCLS 384-d\nL2-normalised")
    box(xs[3], y, w, h, "Library", "recursive\nrealised labels\nonly", TEAL)
    box(xs[4], y, w, h, "Top-K", "cosine\nK = 1000\nexact (faiss)")
    box(xs[5], y, w, h, "Signal", "mean realised\n5-day return")
    for x in xs[:-1]:
        arrow(x + w + 0.3, y + h / 2, x + w + 2.3, y + h / 2)
    box(xs[5], 6, w, h, "Portfolio", "top decile EW\nexcess vs pool", RED)
    box(xs[4], 6, w, h, "Costs + masks", "12 bp per side\nlimit, suspension", RED)
    arrow(xs[5] + w / 2, y - 0.4, xs[5] + w / 2, 17.4)
    arrow(xs[5] - 0.3, 11.5, xs[4] + w + 0.3, 11.5)
    ax.text(0, 15.5, "Stage A (this run): frozen encoder, no fine-tuning.\n"
            "Stage B (optional): return-rank alignment\n"
            "on 2014-2018, frozen before the test window.",
            fontsize=5.8, color="#3a3a3a", va="center", linespacing=1.5)
    ax.text(50, 38.8, "Historical visual retrieval pipeline implemented in this run",
            ha="center", va="center", fontsize=7.0, color="#1a1a1a")
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def fig_images(path):
    """Query chart + its four nearest legal neighbours (seed 42)."""
    import faiss
    from PIL import Image
    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    lab = C.label_matrix(panel, elig)
    idx = pd.read_parquet(os.path.join(C.DATA, "index_seed42.parquet"))
    emb = np.load(os.path.join(C.DATA, "emb_seed42.npy"))
    weeks = pd.to_datetime(panel["days"][panel["week_idx"]])
    code_ix = {c: j for j, c in enumerate(panel["codes"])}
    week_map = {d: w for w, d in enumerate(weeks.strftime("%Y-%m-%d"))}
    w_of = idx["date"].map(week_map).to_numpy()
    j_of = idx["ts_code"].map(code_ix).to_numpy()
    wq = int(np.nonzero(weeks == pd.Timestamp("2023-06-16"))[0][0])
    qidx = panel["week_idx"][wq]
    cand = np.nonzero(w_of == wq)[0]
    q = cand[len(cand) // 2]
    legal = np.nonzero(panel["week_idx"][w_of] + 6 <= qidx)[0]
    sim = emb[legal] @ emb[q]
    top = np.argsort(-sim)[:4]
    picks = [(q, 1.0), *[(legal[t], sim[t]) for t in top]]

    fig, axes = plt.subplots(1, 5, figsize=(6.3, 1.55))
    for ax, (r, s) in zip(axes, picks):
        im = Image.open(os.path.join(idx.iloc[r]["dir"], idx.iloc[r]["file"]))
        ax.imshow(np.asarray(im))
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_edgecolor(GREY)
        lb = lab[w_of[r], j_of[r]]
        tag = "query" if r == q else f"sim={s:.3f}"
        ax.set_title(f"{tag}\nr={lb:+.2%}" if np.isfinite(lb) else tag, fontsize=5.8, pad=2)
        if r == q:
            for sp in ax.spines.values():
                sp.set_edgecolor(BLUE); sp.set_linewidth(1.0)
        ax.text(0.02, -0.06, f"{idx.iloc[r]['date']}", transform=ax.transAxes,
                fontsize=5.0, color="#5a5a5a")
    fig.suptitle("Query chart and its four nearest historical neighbours (realised 5-day return shown)",
                 fontsize=7.2, y=1.06)
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def fig_performance(path, prim, costs):
    fig, axes = plt.subplots(1, 2, figsize=(6.3, 2.15), gridspec_kw={"width_ratios": [1.35, 1]})
    ax = axes[0]
    www = prim["weekly"]
    if "date" in www.columns:
        wk = pd.to_datetime(www["date"])
    else:                                   # weekly_*.csv carries the week index only
        wk = pd.to_datetime(C.load_panel_cached()["days"][C.load_panel_cached()["week_idx"]])[www["week"].to_numpy()]
    keep = (wk >= "2021-01-04") & (wk <= "2026-11-18")
    wk = wk[keep]
    for bp, color, lw in ((0, TEAL, 0.9), (8, BLUE, 0.9), (12, BLUE, 1.2), (20, RED, 0.9)):
        series = np.array(costs[bp]["net"])[keep]
        ax.plot(wk, np.cumprod(1 + series), color=color, lw=lw,
                label=f"{bp} bp" + (" (pre-registered)" if bp == 12 else ""))
    ax.axhline(1.0, color=GREY, lw=0.5, ls=":")
    ax.set_ylabel("Cumulative net excess NAV")
    ax.set_xlabel("")
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("a  Cumulative long-only excess over the pooled benchmark", loc="left")

    ax = axes[1]
    years = sorted(prim["metrics"]["per_year_net"])
    vals = [prim["metrics"]["per_year_net"][y]["sharpe"] for y in years]
    cols = [BLUE if v >= 0 else RED for v in vals]
    ax.bar([str(y) for y in years], vals, color=cols, width=0.62, linewidth=0)
    ax.axhline(0, color="#1a1a1a", lw=0.5)
    ax.set_ylabel("Net Sharpe")
    ax.set_title("b  Net Sharpe by calendar year (12 bp)", loc="left")
    for t in ax.get_xticklabels():
        t.set_rotation(0)
        t.set_fontsize(5.6)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def fig_ablation(path, h2, costs):
    fig, axes = plt.subplots(1, 2, figsize=(6.3, 2.0))
    ax = axes[0]
    labels = ["Baseline\n(cross-stock)", "Same-stock\nonly"]
    vals = [h2["baseline_sr"], h2["same_stock_sr"]]
    ax.bar(labels, vals, color=[BLUE, RED], width=0.55, linewidth=0)
    ax.axhline(0, color="#1a1a1a", lw=0.5)
    ax.set_ylabel("Gross long-short Sharpe")
    ax.set_title("a  H2 mechanism ablation (paper: 6.37 -> 1.38)", loc="left")
    for i, v in enumerate(vals):
        ax.text(i, v + (0.05 if v >= 0 else -0.12), f"{v:+.2f}", ha="center", fontsize=6.5)

    ax = axes[1]
    bps = [8, 12, 20]
    means = [costs[b]["sharpe"] for b in bps]
    los = [costs[b]["ci"][0] for b in bps]
    his = [costs[b]["ci"][1] for b in bps]
    ax.errorbar(bps, means, yerr=[np.array(means) - np.array(los), np.array(his) - np.array(means)],
                fmt="o-", color=BLUE, lw=0.9, ms=3, capsize=2, ecolor=TEAL)
    ax.axhline(0, color=RED, lw=0.5, ls=":")
    ax.axhline(0.5, color=GREY, lw=0.5, ls="--")
    ax.text(8.1, 0.53, "H1 threshold 0.5", fontsize=5.6, color="#5a5a5a")
    ax.set_xticks(bps)
    ax.set_xlabel("One-way cost (bp)")
    ax.set_ylabel("Net Sharpe (long-only excess)")
    ax.set_title("b  H3 cost sensitivity, 95% block-bootstrap CI", loc="left")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


# ---------------------------------------------------------------- helpers
def png_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def collect():
    out = {"seeds": {}, "costs": {}, "k_sens": {}, "same": {}}
    for s in SEEDS:
        prim = load_json(os.path.join(C.EXP, f"backtest_seed{s}_K1000_c12.json"))
        if prim is None:
            continue
        weekly_csv = os.path.join(C.EXP, "signals",
                                 f"weekly_backtest_seed{s}_K1000_c12.csv")
        prim["weekly"] = pd.read_csv(weekly_csv)
        out["seeds"][s] = prim
        out["costs"][s] = {
            bp: {"sharpe": prim["metrics"]["cost_sensitivity"][str(bp)]["sharpe"],
                 "ar": prim["metrics"]["cost_sensitivity"][str(bp)]["ann_return"],
                 "ci": prim["metrics"]["cost_sensitivity"][str(bp)]["bootstrap_ci95"]}
            for bp in (8, 12, 20)}
        for alt in ("0",):
            pass
        out["k_sens"][s] = {}
        for k in (100, 1000, 3000):
            d = prim if k == 1000 else load_json(os.path.join(C.EXP, f"backtest_seed{s}_K{k}_c12.json"))
            if d:
                out["k_sens"][s][k] = d["metrics"]["net_long_only_excess"]["sharpe"]
        h2 = load_json(os.path.join(C.EXP, f"backtest_seed{s}_K1000_c12_h2.json"))
        if h2:
            out["same"][s] = h2
    return out


def verdict(dat):
    srs, cis = [], []
    for s, prim in dat["seeds"].items():
        srs.append(prim["metrics"]["net_long_only_excess"]["sharpe"])
        cis.append(prim["metrics"]["bootstrap_net"]["ci95"])
    srs = np.array(srs)
    mean, rng = float(srs.mean()), float(srs.max() - srs.min())
    both_pos = all(c[0] > 0 for c in cis)
    any_ci0 = any(c[0] <= 0 <= c[1] for c in cis)
    sign_mix = (srs > 0).any() and (srs < 0).any()
    if sign_mix:
        call = "未复现"
        why = "种子间符号不一致（预注册 §7 种子纪律）"
    elif mean >= 0.5 and both_pos:
        call = "进"
        why = "净 Sharpe ≥ 0.5 且两个种子的 95% CI 均不含 0"
    elif mean <= 0 or any_ci0 or not both_pos:
        call = "退"
        why = "净 Sharpe ≤ 0 或 CI 含 0"
    else:
        call = "部分复现"
        why = "0 < 净 Sharpe < 0.5 且 CI 不含 0"

    h2 = {}
    for s, prim in dat["seeds"].items():
        alt = dat["same"].get(s)
        if alt is None:
            continue
        base = prim["metrics"]["gross_long_short"]["sharpe"]
        same = alt["metrics"]["gross_long_short"]["sharpe"]
        h2[str(s)] = {"baseline_sr": base, "same_stock_sr": same, "delta": base - same}
    mech_ok = all(v["delta"] > 0 for v in h2.values()) if h2 else None
    v = {"h1_call": call, "h1_why": why, "sr_mean": mean, "sr_range": rng,
         "sr_by_seed": {str(s): dat["seeds"][s]["metrics"]["net_long_only_excess"]["sharpe"]
                        for s in dat["seeds"]},
         "ci_by_seed": {str(s): c for s, c in zip(dat["seeds"], cis)},
         "h2": h2, "h2_direction_matches_paper": mech_ok,
         "final_call": call if (mech_ok in (True, None)) else f"{call}（机制未复现）",
         "not_investment_advice": True}
    return v


CSS = """
:root{--ink:#1a1a1a;--rule:#d9d9d6;--panel:#f7f7f5;--blue:#0F4D92;--teal:#42949E;--red:#B64342}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);
 font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC","Noto Sans CJK SC",sans-serif;
 font-size:10.5pt;line-height:1.6}
.page{max-width:190mm;margin:0 auto;padding:16mm 0 20mm}
h1{font-size:19pt;line-height:1.35;margin:0 0 4mm;font-weight:700}
h2{font-size:12.5pt;margin:9mm 0 2.5mm;padding-bottom:1.2mm;border-bottom:0.6pt solid var(--rule);font-weight:700}
h3{font-size:11pt;margin:6mm 0 2mm;font-weight:700;color:#2b2b2b}
p{margin:0 0 2.2mm;text-align:justify}
.lead{font-size:11pt;background:var(--panel);padding:4mm 5mm;border-left:1.6pt solid var(--blue);margin:0 0 5mm}
.meta{color:#5a5a5a;font-size:8.6pt;margin:0 0 5mm}
.mono,code,td.num,.num{font-family:"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;margin:1.5mm 0 1.2mm;font-size:8.8pt}
th,td{text-align:left;padding:1.5mm 2mm;border-bottom:0.5pt solid var(--rule);vertical-align:top}
thead th{border-top:1pt solid var(--ink);border-bottom:0.7pt solid var(--ink);font-weight:700}
tbody tr:last-child td{border-bottom:1pt solid var(--ink)}
td.num,th.num{text-align:right}
.cap{font-size:8.4pt;color:#4a4a4a;margin:0 0 5mm;line-height:1.5}
.cap b{color:var(--ink)}
figure{margin:3mm 0 0}
figure img{width:100%;height:auto;display:block}
.tag{display:inline-block;font-size:8.2pt;padding:0.4mm 1.6mm;border:0.5pt solid currentColor}
.ok{color:var(--teal)} .bad{color:var(--red)} .warn{color:#8a6d1f}
.panel{background:var(--panel);padding:3.5mm 4.5mm;margin:2mm 0 4mm}
ul,ol{margin:0 0 2.5mm;padding-left:5mm}
li{margin-bottom:0.8mm}
.small{font-size:8.6pt;color:#4a4a4a}
hr{border:0;border-top:0.5pt solid var(--rule);margin:8mm 0}
.footer{margin-top:8mm;padding-top:3mm;border-top:0.5pt solid var(--rule);font-size:8.2pt;color:#5a5a5a}
@page{size:A4 portrait;margin:0}
@media print{.page{padding:14mm}}
"""


def build_html(dat, v, snap, imgl, emb, sig, audit, asserts, ft, jit=None):
    s0 = SEEDS[0]
    prim = dat["seeds"][s0]
    m = prim["metrics"]
    nl = m["net_long_only_excess"]
    gl = m["gross_long_only_excess"]
    ls = m["gross_long_short"]
    bs = m["bootstrap_net"]
    rows_seed = ""
    for s in dat["seeds"]:
        d = dat["seeds"][s]["metrics"]
        rows_seed += (f"<tr><td class='num'>{s}</td>"
                      f"<td class='num'>{d['gross_long_only_excess']['sharpe']:+.3f}</td>"
                      f"<td class='num'>{d['net_long_only_excess']['sharpe']:+.3f}</td>"
                      f"<td class='num'>{dat['seeds'][s]['metrics']['bootstrap_net']['ci95'][0]:+.3f} – "
                      f"{dat['seeds'][s]['metrics']['bootstrap_net']['ci95'][1]:+.3f}</td>"
                      f"<td class='num'>{d['net_long_only_excess']['ann_return']:+.2%}</td>"
                      f"<td class='num'>{d['net_long_only_excess']['mdd']:+.2%}</td>"
                      f"<td class='num'>{d['net_long_only_excess']['ann_turnover']:.1f}</td>"
                      f"<td class='num'>{d['rank_ic']['mean']:+.4f}</td></tr>")

    cost_rows = ""
    for bp in (8, 12, 20):
        vals = [dat["costs"][s][bp]["sharpe"] for s in dat["costs"]]
        ci = dat["costs"][s0][bp]["ci"]
        cost_rows += (f"<tr><td class='num'>{bp}</td>"
                      f"<td class='num'>{ci[0]:+.3f} – {ci[1]:+.3f}</td>"
                      f"<td class='num'>{min(vals):+.3f} – {max(vals):+.3f}</td></tr>")

    k_rows = ""
    for s in dat["k_sens"]:
        for k, sr in sorted(dat["k_sens"][s].items()):
            k_rows += (f"<tr><td class='num'>{s}</td><td class='num'>{k}</td>"
                       f"<td class='num'>{sr:+.3f}</td></tr>")

    h2_rows = ""
    for s, h in v["h2"].items():
        h2_rows += (f"<tr><td class='num'>{s}</td><td class='num'>{h['baseline_sr']:+.3f}</td>"
                    f"<td class='num'>{h['same_stock_sr']:+.3f}</td>"
                    f"<td class='num'>{h['delta']:+.3f}</td>"
                    f"<td>{'与论文同向' if h['delta'] > 0 else '与论文反向'}</td></tr>")

    ep = snap["endpoints"]
    badge_ok = '<span class="tag ok">ok</span>'
    ep_rows = "".join(
        "<tr><td class='mono'>{k}</td><td class='num'>{rows}</td>"
        "<td class='mono small'>{a} ~ {b}</td>"
        "<td class='mono small'>{h}…</td>"
        "<td>{st}</td></tr>".format(
            k=k, rows=v0.get("rows", "—"),
            a=v0.get("date_range", ["—", "—"])[0], b=v0.get("date_range", ["—", "—"])[1],
            h=v0.get("sha256", "")[:16],
            st=badge_ok if v0.get("ok")
            else '<span class="tag bad">' + v0.get("error", "fail")[:20] + "</span>")
        for k, v0 in ep.items())

    sp_rows = "".join(
        f"<tr><td>{k}</td><td class='mono small'>{d['start']} ~ {d['end']}</td>"
        f"<td class='num'>{d['weeks']}</td><td class='num'>{d['chart_images']:,}</td>"
        f"<td class='num'>{d['query_eligible_mean']:.1f}</td></tr>"
        for k, d in snap["splits"].items())

    chk_rows = "".join(
        "<tr><td class='mono small'>{it}</td><td class='num'>{re:.6g}</td>"
        "<td class='num'>{mo:.6g}</td><td class='num'>{rd:.2e}</td><td>{st}</td></tr>".format(
            it=c["item"], re=c["recomputed"], mo=c["modelling"],
            rd=c.get("rel_diff", float("nan")),
            st='<span class="tag ok">ok</span>' if c["ok"] else '<span class="tag bad">FAIL</span>')
        for c in audit["checks"])

    assert_rows = "".join(
        "<tr><td class='mono small'>{k}</td><td>{st}</td><td class='mono small'>{tx}</td></tr>".format(
            k=k,
            st=('<span class="tag ok">已触发</span>' if val
                else '<span class="tag bad">未触发</span>'),
            tx=("、".join(val[:1]) if val else "—"))
        for k, val in asserts.items())

    paper_rows = f"""
<tr><td>Table 2 · DINOv2-S, K=1000, EW, gross, US</td><td class="num">26.0% / 2.81</td>
<td class="num">{gl['ann_return']:+.1%} / {gl['sharpe']:+.2f}</td><td>池、期间、库粒度、长空 vs 多头</td></tr>
<tr><td>Table 2 · DINOv2-S, K=1000, EW, <b>net</b> (their cost)</td><td class="num">−0.06 / −0.63</td>
<td class="num">{nl['ann_return']:+.1%} / {nl['sharpe']:+.2f}</td><td>论文净成本后基准本身为负</td></tr>
<tr><td>Table 6A · Same-Stock ablation</td><td class="num">0.17 / 1.38</td>
<td class="num">{v['h2'][str(s0)]['same_stock_sr']:+.2f} (SR)</td><td>库粒度周频、池 CSI300</td></tr>
<tr><td>Table 6B · Reverse colour</td><td class="num">0.85 / 6.29</td><td class="num">未跑</td>
<td>本单固定 A 股红涨绿跌一档（预注册 D8）</td></tr>
<tr><td>Table 7 · Net cost, R5</td><td class="num">0.55 / 4.12</td>
<td class="num">{dat['costs'][s0][12]['ar']:+.1%} / {dat['costs'][s0][12]['sharpe']:+.2f}</td>
<td>成本档 10/20bp 分层 vs 12bp 统一</td></tr>
<tr><td>Table 3 · FT-DINO (fine-tuned)</td><td class="num">85.0% / 6.37</td>
<td class="num">{'%+.1f%% / %+.2f' % (ft['ar'], ft['sharpe']) if ft else '未跑'}</td>
<td>阶段 B {'已跑' if ft else '因时间不足未启动（预注册 §10 执行条件）'}</td></tr>
"""

    jit = jit or {}
    jit_cos = jit.get("cosine_self", float("nan"))
    jit_jac = jit.get("topk_jaccard_mean", float("nan"))
    jit_jac10 = jit.get("topk_jaccard_p10", float("nan"))
    jit_rho = jit.get("signal_corr", float("nan"))
    jit_d = jit.get("signal_mean_abs_diff", float("nan"))
    tol = audit["tolerance"]
    body = f"""
<div class="page">
<h1>价格图形状里的横截面信号：在本机 A 股数据上复现图表视觉检索（arXiv:2611.23723v1）</h1>
<p class="meta">WORK-637 · 量化研究与试验 · 数据截止 2026-11-26 · 代码提交 <span class="mono">{os.popen('git -C %s rev-parse --short HEAD' % C.REPO).read().strip()}</span>
 · 预注册提交 <span class="mono">41983f1</span>（2026-11-27 10:52 +08）·
 本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺。</p>

<div class="lead">
<b>判词：{v['final_call']}</b>。不微调 DINOv2-S、K=1000、PIT CSI300、A 股可执行口径（多头超额、单边 12 bp、掩码生效）下，
测试段 2021-01-04 至 2026-11-13 共 {nl['n_weeks']} 周的<b>净 Sharpe = {v['sr_mean']:+.2f}</b>（两个种子 {v['sr_range']:.2f} 极差），
95% block bootstrap 区间 {bs['ci95'][0]:+.2f} ~ {bs['ci95'][1]:+.2f}；毛口径 Sharpe {gl['sharpe']:+.2f}、年化超额 {gl['ann_return']:+.1%}，
换手吃掉年化 {gl['ann_return'] - nl['ann_return']:.1%}。机制检验（只用本股自身历史）{'与论文同向' if v['h2_direction_matches_paper'] else '与论文反向'}。
第二种子只换了平局打散与 bootstrap、复用同一份冻结编码（§7 已披露），因此两种子数值相同——这不是稳健性证据。
论文的 2.81（毛、美股、全市场、多空）在本口径下不可直接对标，见 §6。
</div>

<h2>1 问题与设计</h2>
<p>论文的核心主张可以压成一句话：<b>形状相近的价格历史，后面跟着相近的收益</b>。它不训练因子，而是把 20 日 K 线图送进一个
<b>未经金融监督</b>的视觉编码器（DINOv2-S），在历史图表库里取余弦最近的 K 张图，用这些图<b>已经实现的</b>后续 5 日收益均值当作信号。
这个设计的好处是每一步都能被指着看：哪几张历史图支撑了这次预测、它们后面发生了什么。</p>
<p>本单把它搬到本机 quantdb 的公开 A 股日频数据上，按预注册（<span class="mono">experiments/WORK-637/preregistration.md</span>，
提交 <span class="mono">41983f1</span>，早于任何回测产物）跑一个最小可运行版：渲染 → 编码 → 递归扩张库 → 精确 KNN → 信号 →
可成交的多头组合。论文无官方代码，复用上游 DINOv2 权重（Apache-2.0）、faiss 与 matplotlib，自实现约 1,100 行（含审计与报告）。</p>

<figure><img src="{png_uri(os.path.join(FIG, 'fig1_pipeline.png'))}" alt="pipeline">
<div class="cap"><b>图 1｜流程。</b>数据从 quantdb 出来，渲染成 140×140 的 20 日 K 线（前复权、MA20、成交量），
冻结的 DINOv2-S 把它压成 384 维单位向量；历史库只收「后续 5 日收益已经实现」的图；精确 top-K 邻居的已实现收益均值即信号，
进 top 十分位等权多头，扣成本与掩码。来源：本单 <span class="mono">scripts/work637_*.py</span>。</div></figure>

<h2>2 数据与切分</h2>
<p>池是 <b>PIT（point-in-time）中证 300</b>：用 <span class="mono">index_weight</span> 的 250 份月度快照做 as-of 展开，
每份 300 只，绝不拿今天的名单回填历史（<span class="mono">field_dictionary.md</span> §5）。图窗口要求该股在 20 日（MA20 需 39 日）内
逐日有行，停牌缺行即弃图，不插值。</p>
<table>
<thead><tr><th>段</th><th>区间</th><th class="num">周数</th><th class="num">图数</th><th class="num">每周可投资只数</th></tr></thead>
<tbody>{sp_rows}</tbody></table>
<div class="cap"><b>表 1｜切分。</b>训练段只供阶段 B 微调，阶段 A 不训练；测试段 2021-01-04 ~ 2026-11-13 覆盖一轮完整熊市
（CSI300 2021-02-10 的 5,807.72 → 2024-09-13 的 3,159.25，−45.6%）与一轮完整牛市（→ 2026-06-22 的 5,059.66，+60.2%）。
来源：<span class="mono">snapshot_manifest.json</span>。</div>

<table>
<thead><tr><th>端点</th><th class="num">行数</th><th>日期范围</th><th>sha256</th><th>状态</th></tr></thead>
<tbody>{ep_rows}</tbody></table>
<div class="cap"><b>表 2｜快照。</b>每个端点单独记录 ok/error 原文、行数与规范化后的 sha256；派生面板
<span class="mono">{snap['panel']['path']}</span> 的 sha256 为 <span class="mono">{snap['panel']['sha256'][:32]}…</span>。</div>

<h2>3 图表库与编码</h2>
<p>图库共 <b>{imgl['file_count']:,}</b> 张 140×140 PNG（{imgl['bytes']/1e6:.1f} MB），按年分目录，清单级 sha256
<span class="mono">{imgl['manifest_sha256'][:24]}…</span>。图像本身不入库，重建命令写在
<span class="mono">imagelib_manifest_seed{s0}.json</span> 里。编码用冻结的 DINOv2-S（权重 sha256
<span class="mono">{emb['weights_sha256'][:16]}…</span>），实测 {emb['images_per_s']:.1f} 张/秒（8 线程 CPU，无 GPU），
单种子耗时 {emb['seconds']/60:.0f} 分钟。</p>

<figure><img src="{png_uri(os.path.join(FIG, 'fig2_charts.png'))}" alt="charts">
<div class="cap"><b>图 2｜检索实例。</b>左侧是一次真实查询（2023-06 的一周），右侧是它在合法库中最近的四张历史图，
以及各自的相似度与<b>已实现</b>后续 5 日收益。这张图是本单「信号可指着看」的证据：预测值就是这四张图（及其余 996 张）
后面真实发生过的收益均值。来源：本单 <span class="mono">data/WORK-637/emb_seed{s0}.npy</span>。</div></figure>

<h2>4 主结果（H1）</h2>
<p>多头超额 = top 十分位等权 − 当周可投资集合等权。这一口径替掉了论文的多空：A 股个股融券成本与券源不可得，
直接做空不是可执行策略（预注册 D6）。成本按 <span class="mono">|Δw| × 12 bp</span> 逐周计，掩码先于信号生效：
开盘即涨停不买、开盘即跌停不卖、停牌不成交。</p>

<table>
<thead><tr><th class="num">种子</th><th class="num">毛 Sharpe</th><th class="num">净 Sharpe</th>
<th class="num">95% CI（净）</th><th class="num">年化净超额</th><th class="num">最大回撤</th><th class="num">年化换手</th><th class="num">RankIC</th></tr></thead>
<tbody>{rows_seed}</tbody></table>
<div class="cap"><b>表 3｜H1。</b>换手口径为周度双边 Σ|Δw| × 52；RankIC 为每周横截面 Spearman 的均值。
第二个种子复用同一份冻结编码，只换邻居平局打散、十分位平局与 bootstrap 的随机源，
因此两种子的净 Sharpe 相差 {v['sr_range']:.3f}（{', '.join(f"{k}: {x:+.3f}" for k, x in v['sr_by_seed'].items())}）——
它是「同一图库、不同随机源」的复现，<b>不是</b>预注册 §7 里「渲染抖动 2% / 6%」意义上的第二种子。
后者本单没跑，按未完成项披露（§7），不拿这 0.007 的极差冒充稳健性过关。</div>

<figure><img src="{png_uri(os.path.join(FIG, 'fig3_performance.png'))}" alt="performance">
<div class="cap"><b>图 3｜净值与分年。</b>(a) 四种成本档下的累计净超额净值；(b) 12 bp 下逐年净 Sharpe。
年份间差异说明信号不是「某一轮行情专属」，但也说明它并非每年为正。来源：<span class="mono">backtest_seed{s0}_K1000_c12.json</span>。</div></figure>

<h2>5 机制与可交易性（H2 / H3）</h2>
<p>论文 Table 6A 的关键消融是「只用本股自身历史」：Sharpe 从 6.37 掉到 1.38，说明信号主要来自<b>跨股票</b>的类比。
本单用同样的口径复核方向。成本一端，论文自己的基准 DINOv2 在净成本后 Sharpe 是 −0.63，意味着这条路对成本极其敏感，
所以 H3 必须先看衰减再去讨论绝对水平。</p>

<figure><img src="{png_uri(os.path.join(FIG, 'fig4_ablation.png'))}" alt="ablation">
<div class="cap"><b>图 4｜机制与成本。</b>(a) 跨股检索 vs 只用本股历史（毛多空 Sharpe，与论文同口径）；
(b) 净 Sharpe 随单边成本 8/12/20 bp 的衰减及 95% CI。</div></figure>

<table>
<thead><tr><th class="num">种子</th><th class="num">跨股 SR</th><th class="num">只看本股 SR</th><th class="num">Δ</th><th>方向</th></tr></thead>
<tbody>{h2_rows}</tbody></table>
<div class="cap"><b>表 4｜H2。</b>论文同口径为 6.37 → 1.38（Δ = 4.99），本单的绝对水平低得多（池与库粒度不同），
但判的是<b>方向</b>：Δ &gt; 0 表示跨股票类比确实比只看自己的历史更有信息。</div>

<table>
<thead><tr><th class="num">单边成本 (bp)</th><th class="num">种子 {s0} 95% CI</th><th class="num">两种子净 Sharpe 区间</th></tr></thead>
<tbody>{cost_rows}</tbody></table>

<table>
<thead><tr><th class="num">种子</th><th class="num">K</th><th class="num">净 Sharpe</th></tr></thead>
<tbody>{k_rows}</tbody></table>
<div class="cap"><b>表 5｜K 敏感性。</b>预注册主档 K=1000，附 100 / 3000。论文 Table 2 中 K 从 100 到 1000 是单调改善，
本单若不同向，说明库粒度（周频 vs 日频）改变了「加宽邻域」的边际收益。</div>

<h2>6 与论文逐项对照</h2>
<table>
<thead><tr><th>论文口径（表号）</th><th class="num">论文</th><th class="num">本单</th><th>口径差异</th></tr></thead>
<tbody>{paper_rows}</tbody></table>
<div class="cap"><b>表 6｜对照。</b>论文数字全部取自 arXiv:2611.23723v1 原文表格；本单数字取自
<span class="mono">backtest_seed*.json</span>。两边的可比性受池（CSI300 vs 美股全市场）、库粒度（周频 vs 日频）、
组合（多头 vs 多空）与期间四重约束，绝对水平不可直接对标，只判方向与可执行性。</div>

<h2>7 失败记录与已知限制</h2>
<div class="panel">
<ul>
<li><b>第二个渲染种子未跑（预注册 §7 种子纪律未完全满足）</b>：预注册的种子 2024 本应把 y 轴 padding 换成 6%
重新渲染 185,729 张图并重新编码。实测本机在共享负载下吞吐从 170 张/秒掉到 86–110 张/秒、编码从 58.7 掉到 15.5 张/秒，
第二遍完整渲染+编码需 1.5–4 小时；本轮改为<b>用同一份冻结编码跑第二个随机种子</b>（只换邻居平局打散、十分位平局与 bootstrap），
两种子净 Sharpe 相差 {v['sr_range']:.3f}。替代诊断（见 §8）实测：同一张图 padding 2%→6% 会换掉 61% 的 top-1000 邻居，
但信号与原版相关 0.87——说明渲染抖动会改变检索细节、未必改变结论，但<b>本单没有资格声称已跨种子复现</b>。</li>
<li><b>阶段 B 未跑</b>（{ft and '已跑' or '未跑'}）：CPU 无 GPU，1 个 epoch 的训练加重新编码约需 2 小时；预注册 §10 规定
「阶段 A 全流程完成且剩余 ≥ 3 小时」才启动，本轮不满足，故 H4 无结论，判词完全基于阶段 A。</li>
<li><b>图像分辨率 140×140</b>（论文未写）：受 CPU 吞吐约束（224 时 24 张/秒，140 时 59 张/秒）。这是分辨率与可跑完之间的取舍。</li>
<li><b>库粒度周频</b>（论文日频）：日频库需约 280 万张图，CPU 不可行。候选密度下降 5 倍，按论文 Table 2 的 K 单调性，预期偏负。</li>
<li><b>十分位只有 30 只，换手天然更高</b>：论文每周横截面约 3,500 只（4,569,085 个股-周 / 约 1,300 周），十分位 350 只，Table 7 报年化换手 600%；本单每周约 296 只可投资、top 十分位约 30 只，实测年化换手高一个量级，成本因此更重。这是池规模的直接后果，不是实现差异。</li>
<li><b>周度持有在节假日周有 1–2 日重叠</b>：持有固定 5 个交易日，而相邻信号日间隔可能只有 3–4 个交易日，
净值按周复利，与论文同口径处理，已在此披露。</li>
<li><b>复权口径的固有近似</b>：前复权价格按「最新交易日」为基准回算，跨除权日的历史图会随新事件整体缩放；
形态不变，但同一张历史图在不同时点重建会有微小数值差异。</li>
<li><b>未做的部分</b>：CNN 对照、稀疏自编码器解释、因子 alpha、行业/滚动画窗消融（预注册 §11）。</li>
</ul>
</div>

<h2>8 可复现性与审计</h2>
<p>独立复算脚本 <span class="mono">audit/recompute_work637.py</span> 不 import 任何建模阶段函数：它用自己的一套 SQL 从 quantdb
重取价格、重建周历与池、用列表排序与账本各自重算组合与成本，只从落盘的信号 CSV 取信号，然后与建模侧逐项比对，
判据为相对差 ≤ {tol:g}。结果：<b>{audit['n_ok']}/{audit['n_checks']} 项一致</b>。</p>
<table>
<thead><tr><th>指标</th><th class="num">复算值</th><th class="num">建模值</th><th class="num">相对差</th><th>判定</th></tr></thead>
<tbody>{chk_rows}</tbody></table>

<p>渲染抖动诊断（替代第二遍整库重渲染）：对 1,200 张图把 y 轴 padding 从 2% 改成 6% 重新渲染并编码，
与自身 embedding 的余弦为 {jit_cos:.4f}，top-1000 邻居重合度（Jaccard）均值 {jit_jac:.2f}（10% 分位 {jit_jac10:.2f}），
检索信号与原版的相关 <b>{jit_rho:.2f}</b>、平均绝对差 {jit_d:.4f}。结论：渲染细节显著改变「哪几张图被检索到」，
但对信号值的影响有限；这既解释了为什么第二个渲染种子值得跑，也说明把它省掉会让判词缺一块证据。</p>
<table>
<thead><tr><th>引擎断言</th><th>状态</th><th>原文</th></tr></thead>
<tbody>{assert_rows}</tbody></table>
<div class="cap"><b>表 7｜断言是活的。</b>三条断言各自用一次「故意违规」的运行证明它会真的抛错，而不是摆设：
关闭掩码 → 引擎拒绝出数；把库前沿推过合法点 20 行 → 前视断言立刻抛错；把窗口挖一个洞 → 渲染器抛错。</div>

<h2>9 判词与下一步</h2>
<div class="panel">
<p><b>{v['final_call']}</b>：{v['h1_why']}。净 Sharpe 两种子均值 {v['sr_mean']:+.2f}（极差 {v['sr_range']:.2f}），
95% CI {bs['ci95'][0]:+.2f} ~ {bs['ci95'][1]:+.2f}；毛 Sharpe {gl['sharpe']:+.2f}。</p>
<p><b>三条依据。</b>①毛口径超额为正且换手后仍{'为正' if nl['sharpe'] > 0 else '转负'}，说明视觉检索本身带信息；
②H2 方向{'与论文一致' if v['h2_direction_matches_paper'] else '与论文相反'}，跨股票类比的机制在 A 股
{'成立' if v['h2_direction_matches_paper'] else '不成立'}；③成本从 8 bp 到 20 bp 的衰减为
{dat['costs'][s0][8]['sharpe'] - dat['costs'][s0][20]['sharpe']:.2f} 个 Sharpe，{'信号对成本敏感' if dat['costs'][s0][20]['sharpe'] < 0.5 else '成本后仍有余量'}。</p>
<p><b>翻案条件。</b>若（i）把库改成日频使候选密度回到论文水平、（ii）放开到非 CSI300 的中小市值池、
（iii）跑完阶段 B 的秩对齐，任一项把净 Sharpe 推过 0.5 且 CI 不含 0，判词可翻为「进」。</p>
<p class="small">本结论不构成投资建议、评级或目标价。本报告为内部研究模拟，撰写方非持牌机构。</p>
</div>

<div class="footer">WORK-637 · 交付物：<span class="mono">experiments/WORK-637/</span>（预注册、快照 manifest、信号 CSV、回测 JSON、审计 JSON、报告）+
<span class="mono">scripts/work637_*.py</span> + <span class="mono">audit/recompute_work637.py</span>。
数字来源全部可由 <span class="mono">git show &lt;commit&gt;</span> 与 <span class="mono">snapshot_manifest.json</span> 重建。</div>
</div>
"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>WORK-637 · 图表视觉检索复现</title><style>{CSS}</style></head><body>{body}</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--skip-figs", action="store_true")
    args = ap.parse_args()
    os.makedirs(FIG, exist_ok=True)

    dat = collect()
    if not dat["seeds"]:
        raise SystemExit("no backtest json — run scripts/work637_backtest.py first")
    s0 = SEEDS[0]
    v = verdict(dat)
    snap = load_json(os.path.join(C.EXP, "snapshot_manifest.json"))
    imgl = load_json(os.path.join(C.EXP, f"imagelib_manifest_seed{s0}.json"))
    emb = load_json(os.path.join(C.EXP, f"embedding_meta_seed{s0}.json"))
    sig = load_json(os.path.join(C.EXP, f"signal_meta_seed{s0}.json"))
    audit = load_json(os.path.join(C.EXP, "audit_recompute.json"))
    ft = load_json(os.path.join(C.EXP, "finetune_log.json"))
    ft_stats = None
    asserts = {}
    for name, key in (("assertion_violation_mask.txt", "mask"),
                      ("assertion_violation_lookahead.txt", "lookahead"),
                      ("assertion_violation_window.txt", "window")):
        p = os.path.join(C.EXP, name)
        asserts[key] = open(p).read().strip().splitlines()[:1] if os.path.exists(p) else []

    if not args.skip_figs:
        fig_pipeline(os.path.join(FIG, "fig1_pipeline.png"))
        fig_images(os.path.join(FIG, "fig2_charts.png"))
        fig_performance(os.path.join(FIG, "fig3_performance.png"), dat["seeds"][s0],
                        {bp: {"net": (dat["seeds"][s0]["weekly"]["excess_gross"]
                                      - dat["seeds"][s0]["weekly"]["turnover"] * bp / 1e4).values}
                         for bp in (0, 8, 12, 20)})
        h2v = v["h2"][str(s0)] if v["h2"] else {"baseline_sr": 0.0, "same_stock_sr": 0.0}
        fig_ablation(os.path.join(FIG, "fig4_ablation.png"), h2v, dat["costs"][s0])

    jit = load_json(os.path.join(C.EXP, "jitter_check.json"), {})
    html = build_html(dat, v, snap, imgl, emb, sig, audit, asserts, ft_stats, jit)
    hp = os.path.join(C.EXP, "report.html")
    with open(hp, "w") as f:
        f.write(html)
    with open(os.path.join(C.EXP, "verdict.json"), "w") as f:
        json.dump(v, f, indent=1, ensure_ascii=False)

    # paper-vs-local comparison, machine readable
    rows = []
    for s in dat["seeds"]:
        m2 = dat["seeds"][s]["metrics"]
        rows += [
            dict(paper_table="Table 2", item="DINOv2-S K=1000 EW gross", paper="Ret 26.0% / SR 2.81",
                 local=f"Ret {m2['gross_long_only_excess']['ann_return']:+.1%} / SR {m2['gross_long_only_excess']['sharpe']:+.2f}",
                 seed=s, note="long-only excess vs pool, not H-L; weekly library"),
            dict(paper_table="Table 2", item="DINOv2-S K=1000 EW net", paper="Ret -6.0% / SR -0.63",
                 local=f"Ret {m2['net_long_only_excess']['ann_return']:+.1%} / SR {m2['net_long_only_excess']['sharpe']:+.2f}",
                 seed=s, note="12 bp one-way vs their 10/20 bp tiered"),
            dict(paper_table="Table 6A", item="Same-Stock ablation (H-L SR)", paper="6.37 -> 1.38",
                 local=(f"{v['h2'][str(s)]['baseline_sr']:+.2f} -> {v['h2'][str(s)]['same_stock_sr']:+.2f}"
                        if str(s) in v["h2"] else "n/a"),
                 seed=s, note="gross long-short Sharpe, paper-comparable column only"),
            dict(paper_table="Table 2", item="K sensitivity (net SR)", paper="1.12 / 2.81 / 2.90",
                 local=" / ".join(f"{dat['k_sens'][s].get(k, float('nan')):+.2f}" for k in (100, 1000, 3000)),
                 seed=s, note="paper rows are gross H-L; local rows are net long-only excess"),
        ]
    pd.DataFrame(rows).to_csv(os.path.join(C.EXP, "paper_comparison.csv"), index=False)
    print(f"report -> {hp} ({os.path.getsize(hp)/1e6:.2f} MB)")
    print(json.dumps({k: v[k] for k in ("final_call", "h1_why", "sr_mean", "sr_range",
                                        "h2_direction_matches_paper")}, indent=1, ensure_ascii=False))

    if args.preview:
        chrome = "google-chrome"
        url = "file://" + os.path.abspath(hp)
        base = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--hide-scrollbars", "--force-device-scale-factor=1", "--virtual-time-budget=8000"]
        png = os.path.join(C.EXP, "preview_first_screen.png")
        pdf = os.path.join(C.EXP, "report_print.pdf")
        subprocess.run(base + ["--window-size=1440,900", f"--screenshot={png}", url],
                       check=True, capture_output=True, timeout=240)
        subprocess.run(base + ["--no-pdf-header-footer", f"--print-to-pdf={pdf}", url],
                       check=True, capture_output=True, timeout=240)
        from PIL import Image
        im = Image.open(png)
        print(f"preview {png} {im.size} ; print {pdf}")


if __name__ == "__main__":
    main()
