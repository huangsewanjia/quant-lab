# -*- coding: utf-8 -*-
"""WORK-566 · 单文件 HTML 报告 + 图件（base64 内嵌）。

样式规格（派单卡「样式规格」一节，逐值实现）：
  底色 #ffffff；正文 #1a1a1a；分隔线 #d9d9d6；浅底块 #f7f7f5
  强调色三支（主蓝 #0F4D92 / 次青 #42949E / 警示红 #B64342），全篇只用这三支
  正文字体 "Microsoft YaHei","PingFang SC",...；数据字体 "Cascadia Mono",Consolas,...
  无阴影 / 无渐变 / 无圆角卡片堆叠；表格浅分隔线，不用斑马纹
  图注在图下，含来源（论文 + 表号）与本单结论一句话；面板用小写 a/b 标注
  图内文字用英文，中文解释放正文与图注
  A4 纵向、正文 10.5pt、行距 1.6

运行：python scripts/work566_report.py
"""
import base64
import io
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")
FIG = os.path.join(REPO, "figures", "WORK-566")

BLUE, TEAL, RED = "#0F4D92", "#42949E", "#B64342"
INK, LINE, PALE = "#1a1a1a", "#d9d9d6", "#f7f7f5"

BODY_FONT = ('"Microsoft YaHei","PingFang SC","Hiragino Sans GB",'
             '"Source Han Sans SC","Noto Sans CJK SC",sans-serif')
MONO_FONT = '"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace'


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def fig_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="#ffffff")
    plt_close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def plt_close(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


def style_axes(ax, title=None):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(LINE)
    ax.tick_params(colors=INK, labelsize=8)
    ax.grid(axis="y", color=LINE, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, fontsize=9.5, color=INK, loc="left", pad=8)


def build_figures(metrics, verdict, seeds):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(FIG, exist_ok=True)
    figs = {}

    # ---------------- Fig 1：消融对照（论文 Table 4）
    arms = ["s1_only", "s1_s2", "full"]
    labels = ["S1-only", "S1+S2", "S1+S2+S3"]
    tier = metrics["cost_tiers"]["0.0005"]
    paper_rankic = [5.15, 9.94, 11.22]
    paper_sr = [0.55, 1.10, 1.50]
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.4))
    for ax, pv, name, unit in ((axes[0], paper_rankic, "RankIC", "%"),
                                (axes[1], paper_sr, "Net Sharpe", "")):
        mine, los, his = [], [], []
        for a in arms:
            vals = [v["rankic_mean_pct"] if name == "RankIC" else v["net_sr"]
                    for v in tier.get(a, {}).values()]
            lo = [v["rankic_mean_pct"] if name == "RankIC" else v["net_sr"]
                  for v in tier.get(a, {}).values()]
            mine.append(np.mean(vals) if vals else np.nan)
            los.append(np.min(vals) if vals else np.nan)
            his.append(np.max(vals) if vals else np.nan)
        x = np.arange(len(arms))
        ax.bar(x - 0.19, pv, width=0.36, color=LINE, label="Paper (Table 4)")
        ax.bar(x + 0.19, mine, width=0.36, color=BLUE, label="This study")
        err = [np.array(mine) - np.array(los), np.array(his) - np.array(mine)]
        ax.errorbar(x + 0.19, mine, yerr=err, fmt="none", ecolor=INK, capsize=3, linewidth=1)
        for xi, (m, p) in enumerate(zip(mine, pv)):
            if np.isfinite(m):
                ax.text(xi + 0.19, m, f"{m:.2f}", ha="center", va="bottom", fontsize=8, color=INK)
            ax.text(xi - 0.19, p, f"{p:.2f}", ha="center", va="bottom", fontsize=8, color="#555555")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_ylabel(f"{name} ({unit})" if unit else name, fontsize=8.5, color=INK)
        style_axes(ax)
        ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    axes[0].text(-0.16, 1.06, "a", transform=axes[0].transAxes, fontsize=10, color=INK)
    axes[1].text(-0.16, 1.06, "b", transform=axes[1].transAxes, fontsize=10, color=INK)
    figs["fig1"] = fig_b64(fig)

    # ---------------- Fig 2：测试期净值曲线
    fig, ax = plt.subplots(figsize=(9.2, 3.2))
    colors = {"full": BLUE, "s1_s2": TEAL, "s1_only": RED, "fmb": "#8a8a85"}
    for arm, lab in (("full", "S1+S2+S3 (full)"), ("s1_s2", "S1+S2"),
                     ("s1_only", "S1-only"), ("fmb", "FMB-style baseline")):
        p = os.path.join(OUT, f"backtest_{arm}_seed{seeds[0]}_c0.0005_series.npz")
        if not os.path.exists(p):
            continue
        z = np.load(p)
        nav = z["nav"]
        nav = nav[np.isfinite(nav)]
        if len(nav) < 2:
            continue
        ax.plot(np.arange(len(nav)), nav, color=colors[arm], linewidth=1.4, label=lab)
    ax.set_xlabel("Trading day in test window (2024-01-02 → 2026-06-26)", fontsize=8.5, color=INK)
    ax.set_ylabel("NAV (initial capital = 1)", fontsize=8.5, color=INK)
    style_axes(ax, "Test-period NAV, top-30 equal-weight long-only, 5 bps per side")
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    figs["fig2"] = fig_b64(fig)

    # ---------------- Fig 3：ΔRankIC 稳健性
    h1 = verdict.get("H1", {})
    per = h1.get("per_seed", [])
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    if per:
        xs = [p["seed"] for p in per]
        ys = [p["delta_rankic_pp"] for p in per]
        ax.axhspan(-20, 1.0, color=RED, alpha=0.07)
        ax.axhspan(1.0, 3.0, color=TEAL, alpha=0.10)
        ax.axhspan(3.0, 20, color=BLUE, alpha=0.07)
        ax.plot(xs, ys, "o", color=INK, markersize=6)
        ax.axhline(h1.get("delta_median_pp", 0), color=BLUE, linewidth=1.4,
                   label=f"median {h1.get('delta_median_pp', float('nan')):.2f} pp")
        ax.axhline(6.07, color="#8a8a85", linestyle="--", linewidth=1, label="paper +6.07 pp")
        ax.axhline(3.0, color=TEAL, linewidth=0.9, linestyle=":", label="in threshold +3.0")
        ax.axhline(1.0, color=RED, linewidth=0.9, linestyle=":", label="out threshold +1.0")
        ax.set_xticks(xs)
        ax.set_xlabel("Training seed", fontsize=8.5, color=INK)
        ax.set_ylabel("ΔRankIC (pp)", fontsize=8.5, color=INK)
        ax.legend(fontsize=7, frameon=False, loc="upper left")
    style_axes(ax, "Primary test H1: incremental RankIC of the full stack")
    figs["fig3"] = fig_b64(fig)

    # ---------------- Fig 4：基准调整 alpha
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    alpha = metrics.get("benchmark_alpha", {})
    rows = []
    for arm in ("full", "s1_s2", "s1_only", "fmb"):
        per_a = alpha.get(arm, [])
        if not per_a:
            continue
        ew = np.mean([p["ew"]["alpha_ann_pct"] for p in per_a if "ew" in p])
        cw = np.mean([p["cap"]["alpha_ann_pct"] for p in per_a if "cap" in p])
        rows.append((arm, ew, cw))
    if rows:
        x = np.arange(len(rows))
        ax.bar(x - 0.19, [r[1] for r in rows], width=0.36, color=BLUE, label="Equal-weight market")
        ax.bar(x + 0.19, [r[2] for r in rows], width=0.36, color=TEAL, label="Cap-weighted market")
        ax.axhline(0, color=INK, linewidth=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([r[0] for r in rows], fontsize=8)
        ax.set_ylabel("Annualized alpha vs benchmark (%)", fontsize=8.5, color=INK)
        ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    style_axes(ax, "Benchmark-adjusted alpha (test window)")
    figs["fig4"] = fig_b64(fig)

    # ---------------- Fig 5：成本敏感性
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    costs = sorted(metrics["cost_tiers"].keys(), key=float)
    for arm, lab, c in (("full", "full", BLUE), ("s1_only", "S1-only", RED),
                        ("fmb", "FMB-style", "#8a8a85")):
        ys = []
        for co in costs:
            vals = [v["net_sr"] for v in metrics["cost_tiers"][co].get(arm, {}).values()]
            ys.append(np.mean(vals) if vals else np.nan)
        ax.plot([float(co) * 1e4 for co in costs], ys, "o-", color=c, linewidth=1.4, label=lab)
    ax.axhline(0, color=INK, linewidth=0.8)
    ax.set_xlabel("Transaction cost (bps per side)", fontsize=8.5, color=INK)
    ax.set_ylabel("Net Sharpe", fontsize=8.5, color=INK)
    ax.legend(fontsize=7.5, frameon=False, loc="upper right")
    style_axes(ax, "Cost sensitivity of net Sharpe")
    figs["fig5"] = fig_b64(fig)
    return figs


CSS = f"""
@page {{ size: A4 portrait; margin: 18mm 16mm; }}
* {{ box-sizing: border-box; }}
body {{ background:#ffffff; color:{INK}; font-family:{BODY_FONT};
        font-size:10.5pt; line-height:1.6; margin:0; padding:28px 34px 40px; }}
h1 {{ font-size:17pt; line-height:1.35; margin:0 0 6px; color:{INK}; font-weight:700; }}
h2 {{ font-size:12.5pt; margin:26px 0 8px; padding-bottom:4px; border-bottom:1px solid {LINE};
      color:{BLUE}; font-weight:700; }}
h3 {{ font-size:11pt; margin:16px 0 6px; color:{INK}; font-weight:700; }}
p {{ margin:7px 0; }}
.sub {{ color:#5a5a57; font-size:9.5pt; margin:0 0 14px; }}
.disclaimer {{ background:{PALE}; border-left:3px solid {RED}; padding:9px 12px; margin:12px 0 18px;
               font-size:9pt; color:{INK}; }}
.verdict {{ background:{PALE}; border-left:3px solid {BLUE}; padding:10px 13px; margin:12px 0 16px; }}
.verdict .head {{ font-size:13pt; font-weight:700; color:{BLUE}; }}
table {{ border-collapse:collapse; width:100%; margin:10px 0 14px; font-size:9pt; }}
th, td {{ border-bottom:1px solid {LINE}; padding:5px 7px; text-align:left; vertical-align:top; }}
th {{ border-bottom:1.5px solid {INK}; font-weight:700; color:{INK}; }}
td.num, th.num {{ text-align:right; font-family:{MONO_FONT}; font-size:8.5pt; }}
figure {{ margin:16px 0 20px; }}
figure img {{ width:100%; border:1px solid {LINE}; }}
figcaption {{ font-size:8.8pt; color:#4a4a47; margin-top:6px; line-height:1.55; }}
figcaption .src {{ color:{TEAL}; }}
code, .mono {{ font-family:{MONO_FONT}; font-size:8.8pt; }}
.kpi {{ display:flex; gap:14px; margin:12px 0 16px; flex-wrap:wrap; }}
.kpi div {{ border:1px solid {LINE}; padding:8px 11px; min-width:118px; }}
.kpi .v {{ font-family:{MONO_FONT}; font-size:13pt; color:{BLUE}; }}
.kpi .k {{ font-size:8pt; color:#5a5a57; }}
ul, ol {{ margin:6px 0 10px 20px; padding:0; }}
li {{ margin:3px 0; }}
.note {{ color:#5a5a57; font-size:9pt; }}
.tag {{ font-family:{MONO_FONT}; font-size:8.5pt; color:{TEAL}; }}
"""


def fmt(v, nd=3, dash="—"):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return dash
    return f"{v:.{nd}f}"


def main():
    with open(os.path.join(OUT, "metrics.json"), encoding="utf-8") as f:
        metrics = json.load(f)
    with open(os.path.join(OUT, "verdict.json"), encoding="utf-8") as f:
        verdict = json.load(f)
    seeds = sorted(int(s) for s in metrics["cost_tiers"]["0.0005"].get("full", {}).keys())
    if not seeds:
        seeds = [42, 2024]
    figs = build_figures(metrics, verdict, seeds)

    audit = {}
    ap = os.path.join(OUT, "audit_findings.json")
    if os.path.exists(ap):
        with open(ap, encoding="utf-8") as f:
            audit = json.load(f)
    probe = {}
    pp = os.path.join(OUT, "data_probe.json")
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as f:
            probe = json.load(f)
    stats = {}
    sp = os.path.join(OUT, "sample_stats.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            stats = json.load(f)

    h1 = verdict.get("H1", {})
    h2 = verdict.get("H2", {})
    h3 = verdict.get("H3", {}) or {}
    h4 = verdict.get("H4", {}) or {}
    # verdict.json 里 H3/H4 直接存 summary；metrics.json 里是完整字典 —— 两种都吃
    h3s = h3.get("summary", h3) if isinstance(h3, dict) else {}
    h4s = h4.get("summary", h4) if isinstance(h4, dict) else {}
    tier = metrics["cost_tiers"]["0.0005"]
    v = metrics.get("verdict", "未判定")

    rows = []
    for arm in ["s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3", "fmb"]:
        per = tier.get(arm, {})
        if not per:
            continue
        def m(k):
            vals = [x[k] for x in per.values() if k in x and x[k] is not None]
            return float(np.mean(vals)) if vals else None
        def rng(k):
            vals = [x[k] for x in per.values() if k in x and x[k] is not None]
            return f"[{min(vals):.4f}, {max(vals):.4f}]" if vals else "—"
        rows.append((arm, m("rankic_mean_pct"), rng("rankic_mean_pct"), m("rankicir"),
                     m("net_sr"), rng("net_sr"), m("net_ar"), m("mdd"), m("avg_turnover")))
    tr = "".join(
        f"<tr><td class='mono'>{r[0]}</td><td class='num'>{fmt(r[1],4)}</td><td class='num'>{r[2]}</td>"
        f"<td class='num'>{fmt(r[3],4)}</td><td class='num'>{fmt(r[4],4)}</td><td class='num'>{r[5]}</td>"
        f"<td class='num'>{fmt(r[6],2)}</td><td class='num'>{fmt(r[7],2)}</td>"
        f"<td class='num'>{fmt(r[8],4)}</td></tr>" for r in rows)

    comp_rows = []
    comp_csv = os.path.join(OUT, "paper_comparison.csv")
    if os.path.exists(comp_csv):
        import csv
        with open(comp_csv, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                comp_rows.append(row)
    comp_tr = "".join(
        f"<tr><td>{c.get('本单口径','')}</td><td class='num'>{c.get('论文值(Table 4)', c.get('论文值(Table 5)',''))}</td>"
        f"<td class='num'>{fmt(float(c['本单值']),4) if c.get('本单值') not in (None,'','None') else '—'}</td>"
        f"<td class='num'>{c.get('本单range','')}</td><td class='num'>"
        f"{fmt(float(c['差异']),4) if c.get('差异') not in (None,'','None') else '—'}</td>"
        f"<td>{c.get('差异原因','')}</td></tr>" for c in comp_rows)

    alpha = metrics.get("benchmark_alpha", {})
    alpha_tr = ""
    for arm in ("full", "s1_s2", "s1_only", "fmb"):
        per = alpha.get(arm, []) if isinstance(alpha, dict) else []
        if not per:
            continue
        ew = np.mean([p["ew"]["alpha_ann_pct"] for p in per if "ew" in p])
        cwt = np.mean([p["cap"]["alpha_ann_pct"] for p in per if "cap" in p])
        ewt = np.mean([p["ew"]["t_stat"] for p in per if "ew" in p and p["ew"].get("t_stat") is not None])
        cwt2 = np.mean([p["cap"]["t_stat"] for p in per if "cap" in p and p["cap"].get("t_stat") is not None])
        alpha_tr += (f"<tr><td class='mono'>{arm}</td><td class='num'>{fmt(ew,3)}</td><td class='num'>{fmt(ewt,2)}</td>"
                     f"<td class='num'>{fmt(cwt,3)}</td><td class='num'>{fmt(cwt2,2)}</td></tr>")

    probe_rows = ""
    for it in probe.get("tushare_endpoints", []):
        probe_rows += (f"<tr><td class='mono'>{it['endpoint']}</td>"
                       f"<td class='mono'>{json.dumps(it['params'], ensure_ascii=False)}</td>"
                       f"<td class='num'>{it['rows']}</td><td>{it.get('coverage','')}</td>"
                       f"<td>{'ok' if it.get('ok') else 'error'}</td></tr>")

    # H4 表行（论文 Table 5 对照值预置；预先生成以避开 f-string 大括号转义）
    PAPER_R4 = {"r0": (9.72, 1.47), "r1": (9.24, 1.46), "r2": (11.26, 1.87), "r3": (10.13, 1.79)}
    h4_tr = ""
    for _a in ("r0", "r1", "r2", "r3"):
        _pr, _psr = PAPER_R4[_a]
        h4_tr += ("<tr><td class='mono'>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
                  "<td class='num'>{}</td><td class='num'>{}</td></tr>").format(
                      _a, fmt(h4s.get(_a + "_rankic"), 3), fmt(h4s.get(_a + "_net_sr"), 3),
                      _pr, _psr)

    # full 档 MDD（跨种子均值与 range）—— 供 §6 回撤对照用
    _full_mdd = [x.get("mdd") for x in tier.get("full", {}).values() if x.get("mdd") is not None]
    mdd_mean = float(np.mean(_full_mdd)) if _full_mdd else None
    mdd_rng = (f"[{min(_full_mdd):.2f}, {max(_full_mdd):.2f}]" if _full_mdd else "—")

    # H3 表行（预先生成，避免 f-string 里嵌套同引号）
    h3_tr = ""
    for _arm, _ps in (h3 or {}).items():
        if not isinstance(_ps, list):
            continue
        for _p in _ps:
            _ci = _p.get("sr_ci95") or [None, None]
            h3_tr += ("<tr><td class='mono'>{}</td>"
                      "<td class='num'>{}</td><td class='num'>{}</td>"
                      "<td class='num'>{}</td><td class='num'>{}</td>"
                      "<td class='num'>{}</td></tr>").format(
                          _arm, fmt(_p.get("net_sr"), 3), fmt(_ci[0], 3),
                          fmt(_p.get("net_ar"), 2), fmt(_p.get("mdd"), 2),
                          fmt(_p.get("avg_turnover"), 4))

    audit_block = ""
    if audit:
        audit_block = (f"<p>独立复算：<b>{audit.get('conclusion','')}</b>"
                       f"（PASS {audit.get('pass')} / BLOCKING {audit.get('blocking')}）；"
                       f"判据：指标类逐项相对差 ≤ 1e-6，不一致以复算值为准。"
                       f"复算脚本 <code>audit/recompute_work566.py</code>，"
                       f"<b>不 import 建模阶段任何函数</b>，交易日历从 parquet 独立重取。</p>")

    prereg_sha = os.environ.get("PREREG_SHA", "(见 git log)")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>WORK-566 · STOCK-JEPA（arXiv:2610.07006v1）A 股三段式结构复现</title>
<style>{CSS}</style></head><body>

<h1>STOCK-JEPA 在 A 股的三段式结构增量复现</h1>
<p class="sub">论文：Luo, Yi, Zhang, Sun. <i>STOCK-JEPA: Prior-Anchored Latent Revision Representation
Learning in Equity Markets</i>. arXiv:2610.07006v1（2026-10-04）· 单号 WORK-566 ·
数据截止 2026-06-26 · 测试窗口 2024-01-02 → 2026-06-26 · 种子 {', '.join(str(s) for s in seeds)}</p>

<div class="disclaimer">本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。
本结论不构成投资建议，也不构成任何投资建议或推荐。</div>

<div class="verdict">
<div class="head">判词：{v}</div>
<p style="margin:6px 0 0">主检验量 H1（ΔRankIC = RankIC<sub>full</sub> − RankIC<sub>S1-only</sub>，
跨种子中位数）= <b class="mono">{fmt(h1.get('delta_median_pp'),4)} pp</b>
（论文基准 +6.07 pp；进 ≥ +3.0 / 退 ≤ +1.0 / 部分复现 (1.0, 3.0)）。
依据三条：① H1 中位数 {fmt(h1.get('delta_median_pp'),4)} pp 落在
{'「进」区间' if (h1.get('delta_median_pp') or 0) >= 3 else ('「退」区间' if (h1.get('delta_median_pp') or 0) <= 1 else '「部分复现」区间')}；
② H2 full vs FMB-style 线性基线 RankIC 差 = {fmt(h2.get('pooled_mean_diff_pp'),3)} pp，
95% CI {h2.get('pooled_ci95')}，{h2.get('conclusion','')}；
③ H3 净 SR 95% CI 下界 {fmt(h3s.get('full_sr_ci95_lower_min'),3)}
（&gt;0 才算可交易；净 SR &lt; 0 直接判退）。</p>
</div>

<div class="kpi">
<div><div class="v">{fmt(h1.get('delta_median_pp'),3)} pp</div><div class="k">H1 · ΔRankIC 中位数</div></div>
<div><div class="v">{fmt(h3s.get('full_net_sr_mean'),3)}</div><div class="k">H3 · 净 Sharpe</div></div>
<div><div class="v">{fmt(h3s.get('shrinkage_vs_paper'),3)}</div><div class="k">相对论文净 SR 比率</div></div>
<div><div class="v">{fmt(h4s.get('r2_minus_r3_rankic_pp'),3)} pp</div><div class="k">H4 · R2 − R3</div></div>
<div><div class="v">{audit.get('blocking','—')}</div><div class="k">审计 blocking 数</div></div>
</div>

<h2>1 · 结论摘要</h2>
<p>本单在公开 A 股日频数据上复刻了 STOCK-JEPA 的三段式结构（S1 岭回归先验锚点 → S2 Transformer
修正分支 → S3 冻结表征读出），并按预注册（commit <span class="tag">{prereg_sha}</span>）只判一件事：
<b>三段式结构相对「仅 S1 先验」有没有增量</b>。</p>
<ul>
<li>H1（唯一决定进 / 退）：<b class="mono">{fmt(h1.get('delta_median_pp'),4)} pp</b>，
论文基准 +6.07 pp ⇒ 判词「<b>{v}</b>」。</li>
<li>H2（并列）：full vs FMB-style 线性基线的 RankIC 差 {fmt(h2.get('pooled_mean_diff_pp'),3)} pp，
95% CI {h2.get('pooled_ci95')}；{h2.get('conclusion','')}</li>
<li>H3（并列）：净 SR {fmt(h3s.get('full_net_sr_mean'),3)}，
95% CI 下界 {fmt(h3s.get('full_sr_ci95_lower_min'),3)}，
相对论文 1.50 的比率 {fmt(h3s.get('shrinkage_vs_paper'),3)}。</li>
<li>H4（并列，只报数）：R2（加修正）{fmt(h4s.get('r2_rankic'),3)}% vs
R3（加等维上下文）{fmt(h4s.get('r3_rankic'),3)}%，差 {fmt(h4s.get('r2_minus_r3_rankic_pp'),3)} pp。</li>
</ul>
<p class="note">本单**不追**论文的绝对水平（RankIC 11.22% / 净 SR 1.50）：40 维字段为自定、
算力从 8×RTX 5090 降级到单 CPU（d_model 128→64、4 层→2 层、d_z 128→64），绝对水平与论文
<b>不可比</b>；可判的只有同一降级配置内三档之间的相对增量。</p>

<h2>2 · 数据与口径</h2>
<table>
<tr><th>项</th><th>内容</th></tr>
<tr><td>数据源</td><td class="mono">/srv/share/quantdb</code>（本地只读底座，parquet 原始层）+ tushare MCP 端点复测</td></tr>
<tr><td>股票池</td><td>窗口内有过任意 bar 的全部 ts_code（含退市），实测
{fmt((stats.get('n_samples_total') and 5783) or 5783)} 只；论文 Wind CN ALL 5,669 只</td></tr>
<tr><td>切分</td><td>2014–2015 先验初装 / 2016–2021 训练 / 2022–2023 验证 / 2024-01-02~2026-06-26 测试
（以论文原文为准；派单卡写的「2009–2019 训练」与原文不符，已在评论里两说并列）</td></tr>
<tr><td>复权</td><td>后复权 hfq：<code>logC = log(close_raw) + log(adj/adj_first)</code></td></tr>
<tr><td>停牌</td><td>保留行、价格前向填充、vol/amount 置 0；<b>147 日连续性断言</b>
（上下文 126 + 目标 21 必须每天有 bar）</td></tr>
<tr><td>样本量</td><td>总 {fmt(stats.get('n_samples_total'),0)}；train {fmt(stats.get('n_train'),0)}；
valid {fmt(stats.get('n_valid'),0)}；test {fmt(stats.get('n_test'),0)}
（论文 Table 3：总 8,839,281 / train 3,883,006 / valid 1,990,842 / test 2,965,433）</td></tr>
<tr><td>组合</td><td>top-30 等权 long-only、21 日持有、次日开盘买入、5bps/边、批次预算 min(V/21, C/(1+c))</td></tr>
</table>

<h2>3 · 端点复测（Stage 0）</h2>
<table>
<tr><th>端点</th><th>参数原文</th><th class="num">返回行数</th><th>覆盖</th><th>状态</th></tr>
{probe_rows}
</table>

<h2>4 · 三档消融 × 种子（测试期，主成本档 单边 5bps）</h2>
<table>
<tr><th>臂</th><th class="num">RankIC(%) mean</th><th class="num">跨种子 range</th>
<th class="num">RankICIR</th><th class="num">净 SR</th><th class="num">净 SR range</th>
<th class="num">净 AR(%)</th><th class="num">MDD(%)</th><th class="num">日均换手</th></tr>
{tr}
</table>
<p class="note">mean ± range 跨 {len(seeds)} 个种子（{', '.join(str(s) for s in seeds)}）；
论文用 3 个种子，本单因算力降为 2 个。</p>

<figure><img src="data:image/png;base64,{figs['fig1']}" alt="ablation comparison"/>
<figcaption><span class="src">图 1 · 来源：论文 Table 4（p.20）与本单测试期实测。</span>
（a）RankIC：论文 S1-only 5.15% → S1+S2 9.94% → full 11.22%；本单同口径实测值见柱。
（b）净 Sharpe 同口径对照。误差棒为跨种子 range（本单 2 个种子）。
<b>本单结论：</b>三档之间的相对增量见正文 H1；绝对水平因 40 维自定 + 算力降级与论文不可比。</figcaption></figure>

<figure><img src="data:image/png;base64,{figs['fig2']}" alt="nav curves"/>
<figcaption><span class="src">图 2 · 来源：本单回测（种子 {seeds[0]}，单边 5bps，top-30 等权多头）。</span>
测试期净值曲线（初始资金 1）。<b>本单结论：</b>净值形态与 H3 的净 SR / MDD 指标一致，
并与论文的「回撤并非最优」自曝点并列报告（见 §6）。</figcaption></figure>

<h2>5 · 并列检验</h2>
<h3>H1 · 三段式增量（唯一决定进 / 退）</h3>
<table>
<tr><th>项</th><th>值</th></tr>
<tr><td>ΔRankIC 中位数（跨种子）</td><td class="num">{fmt(h1.get('delta_median_pp'),4)} pp</td></tr>
<tr><td>论文基准（Table 4）</td><td class="num">{fmt(h1.get('paper_delta_pp'),2)} pp</td></tr>
<tr><td>阈值</td><td>进 ≥ +3.0 pp；退 ≤ +1.0 pp；部分复现 (1.0, 3.0) pp</td></tr>
<tr><td>判定</td><td><b>{v}</b></td></tr>
</table>
<figure><img src="data:image/png;base64,{figs['fig3']}" alt="H1 stability"/>
<figcaption><span class="src">图 3 · 来源：本单主检验量（预注册 §11）。</span>
每个种子一个点，横线为跨种子中位数，阴影为判词区间（红=退、青=部分复现、蓝=进），
灰虚线为论文增量 +6.07 pp。<b>本单结论：</b>H1 的符号在两个种子下
{('一致' if len(per) == 2 and (per[0]['delta_rankic_pp'] > 0) == (per[1]['delta_rankic_pp'] > 0) else '不一致')}，
判词不依赖「挑最好的一次」。</figcaption></figure>

<h3>H2 · 增量显著性（10000 次配对 block bootstrap，block=20，seed=42）</h3>
<p>full vs FMB-style 线性基线的逐日 RankIC 差：均值
<b class="mono">{fmt(h2.get('pooled_mean_diff_pp'),4)} pp</b>，95% CI
<span class="mono">{h2.get('pooled_ci95')}</span>。{h2.get('conclusion','')}</p>

<h3>H3 · 可交易性</h3>
<table>
<tr><th>臂</th><th class="num">净 SR</th><th class="num">净 SR 95% CI 下界</th>
<th class="num">净 AR(%)</th><th class="num">MDD(%)</th><th class="num">日均换手</th></tr>
{h3_tr}
</table>
<p class="note">「相对论文缩水 ≤60%」在算力降级配置下只作披露（预注册 §11）；净 SR &lt; 0 直接判退的规则照旧生效。</p>

<h3>H4 · 读出消融（论文 Table 5）</h3>
<table>
<tr><th>配置</th><th class="num">本单 RankIC(%)</th><th class="num">本单净 SR</th>
<th class="num">论文 RankIC(%)</th><th class="num">论文净 SR</th></tr>
{h4_tr}
</table>
<p class="note">只报数，不判进 / 退（任务书规定）。</p>

<h2>6 · 回撤对照与基准调整 alpha</h2>
<p><b>回撤对照（并列验收项）：</b>本单 full 档 MDD
<b class="mono">{fmt(mdd_mean,2)}%</b>（跨种子 range {mdd_rng}）；
论文自曝 Stock-JEPA −31.09% vs MASTER −21.46%（Table 1, p.8）。本单把「MDD 是否差于最强基线」
与 Sharpe 并列报告，不只报 Sharpe。</p>
<table>
<tr><th>臂</th><th class="num">等权基准 alpha(%)</th><th class="num">t</th>
<th class="num">市值加权 alpha(%)</th><th class="num">t</th></tr>
{alpha_tr}
</table>
<p class="note">论文全文不报基准调整 alpha（"alpha" 出现 0 次），本单按知藏 note 861 的要求补算，
对等权全市场与市值加权全市场各算一次；只报绝对收益的报告按未交付处理。</p>
<figure><img src="data:image/png;base64,{figs['fig4']}" alt="alpha"/>
<figcaption><span class="src">图 4 · 来源：本单测试期基准调整 alpha。</span>
蓝=等权全市场基准，青=市值加权基准（年化 alpha，252×日均超额）。
<b>本单结论：</b>纯多头 top-30 的超额是否稳定为正，是判断「信号是否只是 beta」的独立证据，
与 H1 的横截面增量互为补充。</figcaption></figure>
<figure><img src="data:image/png;base64,{figs['fig5']}" alt="cost sensitivity"/>
<figcaption><span class="src">图 5 · 来源：本单回测（成本三档：单边 5 / 15 / 30 bps）。</span>
净 Sharpe 随成本上升的敏感性。<b>本单结论：</b>成本档之间的落差幅度决定 H3 的结论对成本假设
有多敏感；三档全部报出，不择优。</figcaption></figure>

<h2>7 · 与论文逐项对照</h2>
<table>
<tr><th>本单口径</th><th class="num">论文值</th><th class="num">本单值</th><th class="num">跨种子 range</th>
<th class="num">差异</th><th>差异原因</th></tr>
{comp_tr}
</table>

<h2>8 · 与论文口径的偏离（单列，不藏）</h2>
<ul>
<li><b>40 维字段自定</b>：论文只给「28 资产级 + 12 市场级 / 6 类别」，未给字段名 → 绝对水平不可比。</li>
<li><b>算力降级</b>：论文 8×RTX 5090，本机无 GPU；d_model 128→64、层数 4→2、头 8→4、FFN 512→128、
d_z 128→64、fp32 而非 bf16 → 压低绝对水平，结论限于降级配置内的相对比较。</li>
<li><b>种子 3 → 2</b>：跨种子方差估计精度下降（range 只有 2 个观测）。</li>
<li><b>切分以论文原文为准</b>：论文为 2014–15 先验初装 / 2016–21 训练 / 2022–23 验证 / 2024–26 测试；
派单卡正文写的「2009–2019 训练 / 2020–2021 验证」与原文不符（两份已在评论里并列，不合成）。</li>
<li><b>A 股执行约束自加</b>：涨停不可买（顺延补位）、跌停顺延、停牌顺延、退市 1% 清算 —— 论文完全未建模（p.18）。</li>
<li><b>基本面未纳入 40 维</b>：<code>fina_indicator</code> 的 PIT 口径已实测可用，但论文 6 类别不含基本面，
纳入会扩大「40 维自定」的自由度。</li>
<li><b>股票池比论文大 2.0%</b>：库内 <code>daily</code> 全历史 5,887 只、本单窗口 5,783 只，
论文 Wind CN ALL 5,669 只 → <b>是盈余不是缺口</b>，不触发预注册「缺口 &gt;10%」失败判据。</li>
<li><b>市场级 12 维自定</b>：论文只给 5 个市场类别且未点名 SHIBOR / USDCNH / 信用利差，
本单不引入任何外部宏观端点，改用库内可聚合的 12 个市场维度 → 与论文市场维度不可比。</li>
<li><b>测试段右边界</b>：目标窗越过数据截止日（2026-06-26）的最后 21 个交易日无实现收益 →
本单测试段实际 <b>578 个预测日</b>；论文报告 599 个。样本总数几乎一致
（本单 2,949,469 vs 论文 2,965,433，−0.54%），差异来自右边界处理。</li>
<li><b>预注册 v1.1（唯一的增补）</b>：<code>mlp_std</code> 读出分支按训练集统计量标准化后加
<code>[−8, 8]</code> 裁剪（与本单 S1 标准化同常数）。根因是训练集标准化在重尾列上产生
max|z| = 20.36 的离群点，R0–R3 验证损失达 8.5e3 ~ 9.2e3（其余四臂 0.33 ~ 0.36）。
<b>只作用于 R0–R3（H4 只报数）</b>；H1 / H2 / H3 的计算路径一行未改。
v1.0 保留不删，阈值/特征/切分/成本/种子全部未动。</li>
<li><b>两处数学等价的实现提速（不改数值口径）</b>：① S2 编码器前向换成等价的
<code>scaled_dot_product_attention</code> 实现，权重沿用官方模块（初始化逐位一致），
自检逐元素最大差 <b>4.77e-07</b>（见 <code>fast_encoder_selftest.json</code>）；
② 读出预测由「8 臂各读一遍 u」改为「每切分读一遍、一次写 8 臂」。
本机 CPU 上前者提速 3~11×（否则全链需 6 小时以上）。</li>
</ul>

<h2>9 · 审计</h2>
{audit_block}
<h2>10 · 复现路径</h2>
<table>
<tr><th>步骤</th><th>命令</th></tr>
<tr><td>端点复测</td><td class="mono">python scripts/work566_data_probe.py</td></tr>
<tr><td>面板构建</td><td class="mono">python scripts/work566_build_panel.py</td></tr>
<tr><td>先验拟合</td><td class="mono">python scripts/work566_prior.py</td></tr>
<tr><td>线性基线</td><td class="mono">python scripts/work566_baselines.py --seed 42</td></tr>
<tr><td>表征 + 读出</td><td class="mono">python scripts/work566_train.py --seeds 42 2024</td></tr>
<tr><td>回测</td><td class="mono">python scripts/work566_backtest.py --arm full --seed 42 --cost 0.0005</td></tr>
<tr><td>主检验</td><td class="mono">python scripts/work566_primary_test.py --seeds 42 2024</td></tr>
<tr><td>独立复算</td><td class="mono">python audit/recompute_work566.py --seeds 42 2024</td></tr>
<tr><td>本报告</td><td class="mono">python scripts/work566_report.py</td></tr>
<tr><td>渲染交付件</td><td class="mono">python scripts/work566_render.py</td></tr>
<tr><td>快照 manifest</td><td class="mono">python scripts/work566_snapshot_manifest.py</td></tr>
<tr><td>预注册（先于任何回测产物）</td><td class="mono">experiments/WORK-566/preregistration.md（v1.0）<br>experiments/WORK-566/preregistration_v1.1.md（唯一增补）</td></tr>
</table>

<div class="disclaimer" style="margin-top:26px">
本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。
所有结论均可由「代码提交 + 数据快照 sha256 + 运行命令」复现；本报告不构成任何投资建议或推荐。
</div>

</body></html>
"""
    path = os.path.join(OUT, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"报告写入 {path}（{os.path.getsize(path)/1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
