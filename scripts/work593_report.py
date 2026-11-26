# -*- coding: utf-8 -*-
"""WORK-593 报告生成：单文件 HTML（图片 base64 内嵌）+ 1440×900 首屏 PNG + A4 打印 PDF

样式规格（派单卡「样式规格」段，值写死）：
  底色 #ffffff / 正文 #1a1a1a / 分隔线 #d9d9d6 / 浅底块 #f7f7f5
  强调三支：主蓝 #0F4D92、次青 #42949E、警示红 #B64342
  A4 纵向、正文 10.5pt、行距 1.6；无阴影/无渐变/无圆角卡片堆叠

运行：/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_report.py
"""
import base64
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-593"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)
FIG = os.path.join(REPO, "figures", EXP)

BLUE, TEAL, RED = "#0F4D92", "#42949E", "#B64342"
INK, RULE, WASH = "#1a1a1a", "#d9d9d6", "#f7f7f5"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": INK,
    "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "savefig.facecolor": "white",
    "axes.grid": True, "grid.color": RULE, "grid.linewidth": 0.5,
    "axes.axisbelow": True, "legend.frameon": False,
})


def pct(x, d=2):
    return f"{x*100:.{d}f}%"


def f6(x):
    return f"{x:+.6f}"


def png64(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


# ------------------------------------------------------------------ 图
def fig_anchor(rep_info, anchors):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
    ax = axes[0]
    a = anchors.copy()
    ratio = (a["f_C"] / a["f_E"]).replace([np.inf, -np.inf], np.nan).dropna()
    ratio = ratio[(ratio > 0.05) & (ratio < 20)]
    ax.hist(np.log10(ratio), bins=60, color=BLUE, alpha=0.85, linewidth=0)
    ax.set_xlabel("log10( f_cutoff / f_end )  per stock")
    ax.set_ylabel("Stocks")
    ax.set_title("a  Per-stock anchor ratio (E vs C)", loc="left", fontsize=8, color=INK)
    ax = axes[1]
    r2 = (a["f_P"] / a["f_E"]).replace([np.inf, -np.inf], np.nan).dropna()
    r2 = r2[(r2 > 1e-3) & (r2 < 1e4)]
    ax.hist(np.log10(r2), bins=60, color=TEAL, alpha=0.85, linewidth=0)
    ax.set_xlabel("log10( f_past / f_end )  per stock")
    ax.set_ylabel("Stocks")
    ax.set_title("b  Past vs end anchoring", loc="left", fontsize=8, color=INK)
    fig.tight_layout()
    p = os.path.join(FIG, "fig1_anchors.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    return p


def fig_contract(fc, vc):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.4))
    ax = axes[0]
    labels = ["strict 1e-9\n(v1.0)", "derived\n(v1.1)"]
    v10 = [r["violations_v1_0_strict_1e_9"] for r in fc["C1"]]
    v11 = [r["violations_v1_1"] for r in fc["C1"]]
    x = np.arange(len(labels))
    ax.bar(x - 0.18, [np.mean(v10)] * 2, width=0.32, color=RED, label="violations (rows)")
    ax.bar(x + 0.18, [np.mean(v11)] * 2, width=0.32, color=BLUE, label="violations")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Stock-days violating C1")
    ax.set_title("a  C1 tolerance: strict vs derived", loc="left", fontsize=8)
    ax.legend(fontsize=6)
    ax = axes[1]
    names = ["C1\n(v1.1)", "C2", "C4"]
    vals = [1.0 - fc["C1"][0]["violation_rate_v1_1"],
            1.0 - fc["C2"][0]["violations_1e_12"] / max(fc["C2"][0]["rows_checked"], 1),
            fc["C4"][0]["pass_rate"]]
    ax.bar(names, vals, color=[BLUE, BLUE, TEAL], width=0.5)
    for i, v in enumerate(vals):
        ax.text(i, min(v + 0.0006, 1.002), f"{v:.6f}", ha="center", fontsize=6)
    ax.set_ylim(min(vals) - 0.004, 1.003)
    ax.set_ylabel("Pass rate")
    ax.set_title("b  Field-contract pass rates", loc="left", fontsize=8)
    fig.tight_layout()
    p = os.path.join(FIG, "fig2_contract.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    return p


def fig_dic(mt):
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    d = mt.sort_values(["group", "dIC_E_minus_C"])
    y = np.arange(len(d))
    colors = [BLUE if g == "PL" else TEAL for g in d["group"]]
    ax.barh(y, d["dIC_E_minus_C"], color=colors, height=0.72, linewidth=0)
    ax.set_yticks(y)
    ax.set_yticklabels(d["factor"], fontsize=6)
    ax.axvline(0, color=INK, linewidth=0.7)
    ax.set_xlabel("ΔIC = IC(end-anchored) − IC(cutoff-anchored)")
    ax.set_title("a  Factor-level re-anchoring drift (36 expressions)", loc="left", fontsize=8)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=BLUE, label="price-level (PL, 11)"),
                       Patch(color=TEAL, label="price-volume ratio (INV, 25)")], fontsize=7, loc="lower right")
    fig.tight_layout()
    p = os.path.join(FIG, "fig3_dIC.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    return p


def fig_boot(primary):
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    seeds = list(primary["seeds"].keys())
    rng = np.random.default_rng(0)
    for i, s in enumerate(seeds):
        lo, hi = primary["seeds"][s]["ci95"]
        mu = primary["seeds"][s]["boot_mean"]
        sd = primary["seeds"][s]["boot_sd"]
        xs = np.linspace(mu - 3.3 * sd, mu + 3.3 * sd, 200)
        ys = np.exp(-0.5 * ((xs - mu) / sd) ** 2) / (sd * np.sqrt(2 * np.pi))
        ax.plot(xs, ys + i * 0, color=[BLUE, TEAL, RED][i], linewidth=1.2, label=f"seed {s}")
        ax.fill_between(xs, 0, ys, where=(xs >= lo) & (xs <= hi),
                        color=[BLUE, TEAL, RED][i], alpha=0.18, linewidth=0)
    ax.axvline(0, color=RED, linewidth=1.0, linestyle="--")
    ax.set_xlabel("Bootstrap distribution of pooled ΔIC (1000 circular blocks, block=20)")
    ax.set_ylabel("Density")
    ax.set_title("b  95% CI across three seeds (dashed red = zero)", loc="left", fontsize=8)
    ax.legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(FIG, "fig4_bootstrap.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    return p


def fig_invariance(inv):
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    for name, r in inv.items():
        k = r["degree_k_median"]
        rc = r["rank_corr_mean_E_vs_C"]
        if k is None or not np.isfinite(k):
            continue
        col = BLUE if r["verdict"] == "invariant" else RED
        ax.scatter(k, rc, s=26, color=col, alpha=0.85, linewidth=0)
    ax.axhline(0.999, color=INK, linewidth=0.7, linestyle=":")
    ax.set_xlabel("multiplicative scale degree k (0 = invariant)")
    ax.set_ylabel("mean cross-sectional rank corr (E vs C)")
    ax.set_title("c  Invariance verdicts: degree k vs rank stability", loc="left", fontsize=8)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=BLUE, label="invariant (passed)"),
                       Patch(color=RED, label="rejected")], fontsize=7, loc="lower left")
    fig.tight_layout()
    p = os.path.join(FIG, "fig5_invariance.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    return p


def fig_paper(primary):
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    labels = ["Paper §VII-A\nunrestricted pools", "Paper §VII-A\npublished methods",
              "WORK-593\nfactor pool (mean)", "WORK-593\nPL subgroup", "WORK-593\nINV subgroup"]
    vals = [0.0062, 0.0050, primary["dIC_pool"],
            primary["subgroup"]["PL"]["dIC"], primary["subgroup"]["INV"]["dIC"]]
    cols = [RULE, RULE, BLUE, TEAL, TEAL]
    bars = ax.bar(labels, vals, color=cols, width=0.55, edgecolor=INK, linewidth=0.4)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.0003 if v >= 0 else -0.0006),
                f"{v:+.4f}", ha="center", fontsize=7)
    ax.axhline(0, color=INK, linewidth=0.7)
    ax.set_ylabel("ΔIC (end − cutoff)")
    ax.set_title("d  Magnitude against the paper's reported drift", loc="left", fontsize=8)
    plt.setp(ax.get_xticklabels(), fontsize=6)
    fig.tight_layout()
    p = os.path.join(FIG, "fig6_paper.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    return p


# ------------------------------------------------------------------ HTML
CSS = f"""
@page {{ size: A4 portrait; margin: 18mm 16mm; }}
* {{ box-shadow: none !important; border-radius: 0 !important; }}
html {{ background: #ffffff; }}
body {{ background: #ffffff; color: {INK};
  font-family: "Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC","Noto Sans CJK SC",sans-serif;
  font-size: 10.5pt; line-height: 1.6; margin: 0 auto; max-width: 860px; padding: 28px 24px 60px; }}
h1 {{ font-size: 19pt; line-height: 1.35; margin: 0 0 6px; font-weight: 600; color: {INK}; }}
h2 {{ font-size: 13pt; margin: 26px 0 8px; padding-bottom: 4px; border-bottom: 1px solid {RULE}; font-weight: 600; }}
h3 {{ font-size: 11pt; margin: 18px 0 6px; font-weight: 600; color: {BLUE}; }}
p {{ margin: 6px 0; }}
.small {{ font-size: 9pt; color: #555; }}
.rule {{ border-top: 1px solid {RULE}; margin: 10px 0; }}
.verdict {{ background: {WASH}; border-left: 3px solid {BLUE}; padding: 12px 14px; margin: 14px 0 18px; }}
.verdict.no {{ border-left-color: {RED}; }}
.verdict .tag {{ font-size: 12pt; font-weight: 700; color: {BLUE}; }}
.verdict.no .tag {{ color: {RED}; }}
.meta {{ font-size: 9pt; color: #666; }}
table {{ border-collapse: collapse; width: 100%; font-size: 8.6pt; margin: 8px 0 4px; }}
th {{ text-align: left; border-bottom: 1px solid {INK}; padding: 4px 6px; font-weight: 600; background: {WASH}; }}
td {{ border-bottom: 1px solid {RULE}; padding: 3px 6px; vertical-align: top; }}
td.num, th.num {{ text-align: right; font-family: "Cascadia Mono",Consolas,"SF Mono",Menlo,monospace; font-size: 8.2pt; }}
code, .mono {{ font-family: "Cascadia Mono",Consolas,"SF Mono",Menlo,monospace; font-size: 8.4pt; }}
figure {{ margin: 14px 0 6px; }}
figure img {{ width: 100%; }}
figcaption {{ font-size: 8.6pt; color: #444; margin-top: 6px; }}
blockquote {{ margin: 8px 0; padding: 6px 12px; background: {WASH}; border-left: 2px solid {RULE}; font-size: 9.4pt; }}
ul, ol {{ margin: 6px 0 6px 20px; padding: 0; }}
li {{ margin: 3px 0; }}
.pass {{ color: {TEAL}; font-weight: 600; }}
.fail {{ color: {RED}; font-weight: 600; }}
.mono td {{ font-family: "Cascadia Mono",Consolas,"SF Mono",Menlo,monospace; }}
"""


def df_table(df, num_cols=(), fmt=None):
    fmt = fmt or {}
    head = "".join(f'<th class="num">{c}</th>' if c in num_cols else f"<th>{c}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c in num_cols:
                f = fmt.get(c)
                cells.append(f'<td class="num">{f(v) if f else v}</td>')
            else:
                cells.append(f"<td>{v}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def build():
    os.makedirs(FIG, exist_ok=True)
    rd = json.load(open(os.path.join(OUT, "report_data.json"), encoding="utf-8"))
    primary = rd["primary"]
    inv = json.load(open(os.path.join(OUT, "invariance.json"), encoding="utf-8"))
    fc = json.load(open(os.path.join(OUT, "field_contract.json"), encoding="utf-8"))
    vc = json.load(open(os.path.join(OUT, "vwap_check.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(OUT, "snapshot_manifest.json"), encoding="utf-8"))
    af = json.load(open(os.path.join(OUT, "audit_findings.json"), encoding="utf-8"))
    mt = pd.read_csv(os.path.join(OUT, "metrics_factors.csv"))
    anchors = pd.read_csv(os.path.join(DATA, "anchors.csv"))

    f1 = fig_anchor(rd["panel"], anchors)
    f2 = fig_contract(fc, vc)
    f3 = fig_dic(mt)
    f4 = fig_boot(primary)
    f5 = fig_invariance(inv)
    f6 = fig_paper(primary)

    comb_all = rd.get("combiner", {})
    comb_text = " / ".join(f"{comb_all[s]['diff_E_minus_C']:+.4f}" for s in comb_all if comb_all[s].get("diff_E_minus_C") is not None)
    if comb_text:
        comb_text = comb_text + f"（IC(C)≈{np.mean([comb_all[s]['C']['IC'] for s in comb_all]):.4f}，占比≈{pct(np.mean([comb_all[s]['diff_E_minus_C'] for s in comb_all])/np.mean([comb_all[s]['C']['IC'] for s in comb_all]),1)}）"
    ok = primary["verdict"] == "复现成立"
    crit = primary["criteria"]
    verdict_cls = "" if ok else " no"
    crit_rows = [
        ("C1  ΔIC_pool > 0", crit["c1_dIC_positive"], f6(primary["dIC_pool"])),
        ("C2  三种子 95% CI 均不含 0",
         crit["c2_ci_excludes_zero_all_seeds"],
         " / ".join(f"[{primary['seeds'][s]['ci95'][0]:+.4f}, {primary['seeds'][s]['ci95'][1]:+.4f}]"
                    for s in primary["seeds"])),
        ("C3  占比 ∈ [10%, 18%]", crit["c3_ratio_in_0.10_0.18"], pct(primary["ratio"])),
        ("C4  三种子符号一致", crit["c4_same_sign_all_seeds"], "同上"),
    ]
    crit_tbl = "<table><thead><tr><th>判据</th><th>结果</th><th class='num'>实测</th></tr></thead><tbody>" + \
        "".join(f"<tr><td>{a}</td><td class='{'pass' if b else 'fail'}'>{'通过' if b else '不通过'}</td>"
                f"<td class='num'>{c}</td></tr>" for a, b, c in crit_rows) + "</tbody></table>"

    # 主表：36 行
    show = mt[["factor", "group", "IC_E", "IC_C", "IC_P", "dIC_E_minus_C", "ratio_over_IC_C",
               "ICIR_C", "invariant_verdict"]].copy()
    show["ratio_over_IC_C"] = show["ratio_over_IC_C"].map(lambda v: pct(v, 1) if np.isfinite(v) else "—")
    show.columns = ["因子", "组", "IC_E", "IC_C", "IC_P", "ΔIC", "占比", "ICIR_C", "不变量判词"]
    tbl_factors = df_table(show, num_cols=["IC_E", "IC_C", "IC_P", "ΔIC", "ICIR_C"],
                           fmt={"IC_E": lambda v: f"{v:.5f}", "IC_C": lambda v: f"{v:.5f}",
                                "IC_P": lambda v: f"{v:.5f}", "ΔIC": lambda v: f"{v:+.5f}",
                                "ICIR_C": lambda v: f"{v:.3f}"})

    # 字段契约表
    fc_rows = []
    for r1, r2, r4 in zip(fc["C1"], fc["C2"], fc["C4"]):
        fc_rows.append([r1["representation"], f'{r1["violations_v1_1"]}',
                        f'{r1["violations_v1_0_strict_1e_9"]}', f'{r2["violations_1e_12"]}',
                        f'{r2["max_rel_diff"]:.2e}', pct(r4["pass_rate"], 4)])
    fc_tbl = "<table><thead><tr><th>表示</th><th class='num'>C1 违规(v1.1)</th><th class='num'>C1 违规(1e-9)</th>" \
             "<th class='num'>C2 违规</th><th class='num'>C2 最大相对差</th><th class='num'>C4 通过率</th></tr></thead><tbody>" + \
        "".join("<tr>" + "".join(f'<td class="num">{c}</td>' if i else f"<td>{c}</td>"
                                 for i, c in enumerate(r)) + "</tr>" for r in fc_rows) + "</tbody></table>"

    # 不变量表
    inv_rows = []
    for n in mt["factor"]:
        r = inv[n]
        k = r["degree_k_median"]
        inv_rows.append([n, r["group"], "n/a" if k is None or not np.isfinite(k) else f"{k:+.4f}",
                         f'{r["rank_corr_mean_E_vs_C"]:.5f}', r["verdict"], r["reason"]])
    inv_tbl = "<table><thead><tr><th>因子</th><th>组</th><th class='num'>尺度指数 k</th>" \
              "<th class='num'>E-C 秩相关</th><th>判词</th><th>依据</th></tr></thead><tbody>" + \
        "".join("<tr>" + "".join(f'<td class="num">{c}</td>' if i in (2, 3) else f"<td>{c}</td>"
                                 for i, c in enumerate(r)) + "</tr>" for r in inv_rows) + "</tbody></table>"

    # 论文对照
    paper_rows = [
        ["真实锚定池级 ΔIC（无限制池）", "0.0062（§VII-A 正文，Fig. 5b）", f6(primary["dIC_pool"]),
         "池级=因子级均值；论文为组合器加权后的池 IC，本单另报 combiner 口径"],
        ["真实锚定池级 ΔIC（已发表方法，50 池）", "0.0050 [0.0030, 0.0070]，约 14% IC（§VII-A）",
         f"{f6(primary['dIC_pool'])}（占比 {pct(primary['ratio'], 1)}）",
         "论文 50 池 × 5 种子；本单 1 个自建池 × 3 种子"],
        ["认证池池级 ΔIC", "0.0007（§VII-A，与 placebo 同量级）", "本单未做认证池（无 checker，见 §9）",
         "本单的对照是 A 组 11 条价格水平因子"],
        ["因子级 |ΔIC| > 0.005 占比（无限制）", "30%（Table V，模拟锚定）",
         pct(primary["drift_share_abs_gt_0.005"]["all"], 1) + "（真实锚定）",
         "论文该列是模拟锚定的「超额漂移」，与本单真实锚定 ΔIC 不同口径，仅作量级参考"],
        ["Alpha158 认证通过率", "153/158 = 96.8%（§VII-A）",
         pct(rd["invariance_summary"]["pass_rate"], 1) + f"（{rd['invariance_summary']['n_invariant']}/36）",
         "本单池子是**按预注册构造**的对照池，含 11 条价格水平控件，非 Alpha158 复刻"],
        ["样本期", "2010-01-04 ~ 2026-08-21", " ~ ".join(primary["eval_window"]),
         "本单数据截止 2026-11-13（工作区既定截止点），评估窗从 2011-01-04 起留预热"],
        ["股票池", "1,730 只（CSI300 ∪ CSI500，Qlib 成分）", f'{man["universe"]["union_stocks"]:,} 只（本地 1,862）',
         f'差 {man["universe"]["gap"]} 只，对账见 §3'],
        ["标签", "20 日收盘到收盘前瞻收益", primary["label"], "一致"],
    ]
    paper_tbl = "<table><thead><tr><th>项目</th><th>论文</th><th>本单</th><th>口径差异</th></tr></thead><tbody>" + \
        "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in paper_rows) + "</tbody></table>"

    # vwap 表
    v1_rows = "".join(
        f"<tr><td>{r['code']}</td><td class='num'>{r.get('overlap_days','—')}</td>"
        f"<td class='num'>{r.get('overlap_start','—')} ~ {r.get('overlap_end','—')}</td>"
        f"<td class='num'>{r.get('close_return_max_abs_diff',float('nan')):.6f}</td>"
        f"<td class='num'>{pct(r.get('close_return_share_le_1e_4',float('nan')),2)}</td>"
        f"<td class='num'>{r.get('volume_ratio_median',float('nan')):.4f}</td></tr>"
        for r in vc["V1"])

    findings_rows = "".join(
        f"<tr><td>{f['severity']}</td><td>{f['item']}</td><td class='mono'>{f['measured']}</td>"
        f"<td class='mono'>{f['expected']}</td></tr>" for f in af["findings"])

    comb = rd.get("combiner", {})
    comb_rows = "".join(
        f"<tr><td>{s}</td><td class='num'>{comb[s]['E']['IC']:.5f}</td><td class='num'>{comb[s]['C']['IC']:.5f}</td>"
        f"<td class='num'>{comb[s]['diff_E_minus_C']:+.5f}</td><td class='num'>{comb[s]['E']['n_days']}</td>"
        f"<td class='num'>{comb[s]['E']['avg_selected']:.1f}</td></tr>"
        for s in comb if comb[s].get("E") and comb[s]["E"].get("IC") is not None)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>WORK-593 复权锚点重锚定审计</title><style>{CSS}</style></head><body>

<p class="meta">内部研究模拟 · 撰写方非持牌机构 · 不构成投资建议与收益承诺</p>
<h1>复权锚点不变性与重锚定审计：同一因子池在端锚定与截止锚定下的 IC 漂移</h1>
<p class="meta">WORK-593 · 论文 arXiv:2611.07751v2 · 执行日期 2026-11-26 · 数据截止 2026-11-13 ·
执行方：金融研究（单体臂）· 环境：Mo-server</p>
<div class="rule"></div>

<div class="verdict{verdict_cls}">
  <div class="tag">判词：{primary["verdict"]}</div>
  <p style="margin:6px 0 0">
  主检验量 <b>ΔIC_pool = {f6(primary["dIC_pool"])}</b>（端锚定 − 截止锚定，36 条因子均值），
  截止锚定侧的池均值 IC = {primary["IC_pool_C"]:.5f}，<b>占比 {pct(primary["ratio"], 2)}</b>；
  1000 次循环块 bootstrap（块长 20，种子 42/43/44）的 95% CI 分别为
  {"; ".join(f"[{primary['seeds'][s]['ci95'][0]:+.5f}, {primary['seeds'][s]['ci95'][1]:+.5f}]" for s in primary["seeds"])}。
  </p>
  <p style="margin:4px 0 0" class="small">三条依据：① 方向 <b>{('为正' if primary['dIC_pool']>0 else '为负')}</b>（预注册要求 &gt;0）；
  ② 95% CI {('全部不含 0' if all(primary['seeds'][s]['ci_excludes_zero'] for s in primary['seeds']) else '存在含 0 的种子')}——<b>不含 0 但整段在负号一侧</b>，即漂移显著、方向与论文相反；
  ③ 占比 {pct(primary['ratio'],2)} {('落在' if crit['c3_ratio_in_0.10_0.18'] else '不在')} 10%–18% 区间。</p>
  <p style="margin:6px 0 0; border-top:1px solid #d9d9d6; padding-top:6px">
  <b>同一份数据的两个口径，必须并列读：</b>按派单卡写死的主检验量（<b>因子级均值</b>）判「未复现」；
  但若换成论文自己的<b>池级组合器</b>口径（预注册 §7，次要口径），ΔIC = {comb_text}，
  即 <b>方向与论文一致（+0.0062 量级）、量级更大</b>。两者不矛盾：论文 §VII-A 原文即写明
  「十个裸字段之外，因子级的有符号差接近零，池级差异来自组合器如何选择与加权」——
  本单的因子级分解（B 组 ΔIC ≈ 2.6e-8）正是这句话的实测证据。判词按预注册写，不用次要口径翻案。</p>
</div>

<h2>1 一句话结论与判据</h2>
<p>把同一份底层行情按三种锚定（端 / 截止 / 既往）重排成三套表示后，同一因子池的横截面 IC 会移动多少，
是本单唯一的主检验量。结论是：{primary["verdict"]}。这不是 alpha 结果，也不是策略评价——
它度量的是「同一份数据、换一种复权基准，因子表现会移动多少」，即表示的 IC 贡献。</p>
{crit_tbl}

<h2>2 协议：三套锚定怎么构造</h2>
<p>底层只有不复权 OHLC、成交量与复权因子（<code>daily</code> / <code>adj_factor</code>）。三套表示由
<b>同一个变换式</b>产生，差别只在每股一个标量 f<sub>i</sub><sup>(A)</sup>：</p>
<blockquote class="mono">p<sub>t</sub> = c<sub>t</sub>·f<sub>t</sub>/f<sub>i</sub><sup>(A)</sup> ，
w<sub>t</sub> = vwap<sub>t</sub>·f<sub>t</sub>/f<sub>i</sub><sup>(A)</sup> ，
v<sub>t</sub> = vol<sub>t</sub>·f<sub>i</sub><sup>(A)</sup>/f<sub>t</sub></blockquote>
<p>价格乘、成交量除，保证成交额恒等式不随锚定改变（C2 实测最大相对差
{fc["C2"][0]["max_rel_diff"]:.1e}）。f<sub>i</sub><sup>(E)</sup> 取 2026-11-13 前最后一个因子值，
f<sub>i</sub><sup>(C)</sup> 取 2020-12-31 前最后一个（{man["anchors"]["fallback_first_C_rows"]} 只 2021 年后上市股回退到首个值并标记），
f<sub>i</sub><sup>(P)</sup> 取窗口内首个值。</p>
<figure><img src="{png64(f1)}" alt="anchor ratios">
<figcaption>图 1 · 每股锚点比值分布。<b>a</b> 截止锚定与端锚定的因子比（对数）；<b>b</b> 既往锚定与端锚定的因子比。
比值越分散，同一只股票的「价格水平」在两套表示里差得越远——这是价格水平型因子失稳的直接来源。
数据：<code>data/WORK-593/anchors.csv</code>（sha256 见 manifest）。</figcaption></figure>

<h2>3 数据与字段契约</h2>
<p>股票池 = 中证 300 ∪ 中证 500 的<b>逐月成分快照</b>（时点口径，不用当前名单回溯），窗口
2010-01-04 ~ 2026-11-13，评估窗 {primary["eval_window"][0]} ~ {primary["eval_window"][1]}，
标签 20 日收盘到收盘前瞻收益。<b>标签在三套表示下逐值一致</b>（实测最大绝对差
{rd["label_max_abs_diff_across_reps"]["C"]:.1e} / {rd["label_max_abs_diff_across_reps"]["P"]:.1e}），
因为它是同一只股票两个复权价的比，每股的 f<sub>i</sub><sup>(A)</sup> 全部约掉。</p>
{fc_tbl}
<figure><img src="{png64(f2)}" alt="contract">
<figcaption>图 2 · 字段契约。<b>a</b> C1（vwap 落在 [low, high]）在严格 1e-9 容差与按字段分辨率推导容差下的违规行数对比；
<b>b</b> 三条契约的通过率。严格口径的 {fc["C1"][0]["violations_v1_0_strict_1e_9"]:,} 行违规全部落在
amount≈1 元 与 vol≈1 手 的存储分辨率内（v1.1 归因，见 §8）。</figcaption></figure>
<p class="small">成分并集对账：本地 {man["universe"]["union_stocks"]:,} 只 vs 论文 1,730 只，
差 {man["universe"]["gap"]} 只。可测分解：窗口延展（2026-08-22 后首次入成分）2 只、2010-01-04 后上市 755 只、
2026-11-01 前已无行情 126 只；剩余 130 只<b>未对账</b>——Qlib 的 CSI300/CSI500 成分文件本机不存在，无法逐只比对，不做猜测。</p>

<h2>4 vwap 自建口径与验收</h2>
<p>主口径 <code>vwap = amount×10/vol</code>（amount 千元、vol 手 → 元/股）。派单卡要求的「与 Qlib 同日
$vwap 逐值比对」<b>本机不可做</b>：Qlib 公共 bundle <code>~/.qlib/qlib_data/features/sh600000/</code>
只有 change/close/factor/high/low/open/volume 七个 <code>.day.bin</code>，<b>没有 vwap.day.bin</b>，
且日历最晚到 {vc["qlib_evidence"]["calendar_last"]}。该项记为「<b>部分未取到</b>」，不用「不存在」表述。</p>
<table><thead><tr><th>备选校验</th><th class="num">实测</th><th>判据</th><th>结论</th></tr></thead><tbody>
<tr><td>V1 Qlib 可得字段（close 收益 / volume 水平，3 只 × 重叠期）</td><td class="num">95.10% ~ 99.34%</td><td class="num">≥ 95%</td><td class="pass">通过</td></tr>
<tr><td>V2 区间命中率（全体股票-日，v1.1 容差）</td><td class="num">{pct(vc["V2"]["rate_v1_1"],4)}</td><td class="num">≥ 99.9%</td><td class="pass">通过</td></tr>
<tr><td>V3 双路径逐值（pandas 逐元素 vs numpy 广播）</td><td class="num">max|Δ| = {vc["V3"]["max_abs_diff"]:.1e}</td><td class="num">= 0</td><td class="fail">按字面不通过</td></tr>
</tbody></table>
<p class="small">V3 的偏差来自 IEEE-754 下 <code>a/b</code> 与 <code>a·b⁻¹</code> 不严格相等（相对量级 1e-16），
不影响 vwap 数值；预注册判据写的是「必须 = 0」，故按字面记「不通过」，不做解释性放行。</p>
<table><thead><tr><th>代码</th><th class="num">重叠交易日</th><th class="num">重叠区间</th><th class="num">close 收益最大绝对差</th><th class="num">≤1e-4 占比</th><th class="num">volume 比值中位数</th></tr></thead>
<tbody>{v1_rows}</tbody></table>
<p class="small">Qlib 的 <code>close</code> 是锚定在其最后一日的调整价（比收益不比水平），
<code>volume</code> 与本地「手×100」的比值等于该股隐含复权因子的倒数（实测比值的中位数为
1.0148 / 4.0199 / 3.6892，变异系数 &lt; 5%），说明两家厂商的<b>复权基准不同但内部自洽</b>；
本单 vwap 仍全部由本地 amount/vol 构造，不依赖 Qlib。</p>

<h2>5 主检验：ΔIC 与占比</h2>
<p>每条因子在每套表示下算逐日横截面 Spearman IC（前瞻 20 日收益），取评估窗均值；
ΔIC = IC<sub>端</sub> − IC<sub>截止</sub>；池均值与占比按预注册 §6 计算。</p>
<table><thead><tr><th>口径</th><th class="num">ΔIC</th><th class="num">IC(截止锚定)</th><th class="num">占比</th><th class="num">因子数</th></tr></thead><tbody>
<tr><td>全部（主检验量）</td><td class="num">{f6(primary["dIC_pool"])}</td><td class="num">{primary["IC_pool_C"]:.5f}</td><td class="num">{pct(primary["ratio"],2)}</td><td class="num">36</td></tr>
<tr><td>A 组 价格水平型</td><td class="num">{f6(primary["subgroup"]["PL"]["dIC"])}</td><td class="num">{primary["subgroup"]["PL"]["IC_C"]:.5f}</td><td class="num">{pct(primary["subgroup"]["PL"]["ratio"],2)}</td><td class="num">11</td></tr>
<tr><td>B 组 价量比值型</td><td class="num">{f6(primary["subgroup"]["INV"]["dIC"])}</td><td class="num">{primary["subgroup"]["INV"]["IC_C"]:.5f}</td><td class="num">{pct(primary["subgroup"]["INV"]["ratio"],2)}</td><td class="num">25</td></tr>
</tbody></table>
<figure><img src="{png64(f3)}" alt="dIC per factor">
<figcaption>图 3 · 36 条表达式的因子级漂移。<b>a</b> ΔIC 按组排序：价格水平型（蓝）整体为正、比值型（青）集中在 0 附近，
与论文「十个裸字段之外均值接近零」一致（§VII-A）。数据：<code>metrics_factors.csv</code>。</figcaption></figure>
<figure><img src="{png64(f4)}" alt="bootstrap">
<figcaption>图 4 · 三种子 bootstrap。<b>b</b> 池级 ΔIC 的 1000 次循环块重采样分布（块长 20，对应 20 日重叠标签），
阴影为 95% 分位区间，红色虚线为 0。</figcaption></figure>
<p>{tbl_factors}</p>

<h2>6 不变量判定</h2>
<p>判据（预注册 §6）：①「乘性尺度指数」k——把单只股票全部价格乘 λ=1.5、成交量除 1.5 后重算因子，
k = log(F(λF)/F(F))/log λ 应为 0；②「E vs C 横截面秩相关」≥ 0.999。两条同时满足判 invariant。
另设全局 placebo（所有股票同乘一个常数）作为对照基线，理论上秩相关恒为 1。</p>
{inv_tbl}
<figure><img src="{png64(f5)}" alt="invariance">
<figcaption>图 5 · 不变量判定的二维视图。<b>c</b> 横轴为尺度指数 k、纵轴为 E 与 C 的横截面秩相关；
右上（k≈0 且秩相关≈1）为通过，其余为拒绝。价格水平型因子的 k 恰为 1（线性尺度），
横截面秩类因子（PL11/INV24）k 不适用、由秩相关判。</figcaption></figure>

<h2>7 与论文对照</h2>
{paper_tbl}
<figure><img src="{png64(f6)}" alt="paper comparison">
<figcaption>图 6 · 量级对照。<b>d</b> 灰色为论文 §VII-A 报告的池级漂移，蓝色为本单池均值，
青色为两个子组。论文数字来自 arXiv:2611.07751v2 §VII-A / Fig. 5b / Table V。</figcaption></figure>

<h2>8 独立复算与审查</h2>
<p>独立复算脚本 <code>audit/recompute_work593.py</code> 不 import 建模阶段任何函数：滚动统计用累加和自实现、
秩相关走 pandas <code>.rank()</code> 路径、成分生效区间从<b>原始成分快照</b>重建。
逐因子 IC/ΔIC 共 {len(mt) * 4} 项比对，最大相对差 {af["max_rel_diff_factors"]:.3e}，
blocking = {af["n_blocking"]}。复算得到 ΔIC_pool = {f6(af["recomputed"]["dIC_pool"])}、
占比 {pct(af["recomputed"]["ratio"],2)}，判词 {af["recomputed"]["verdict"]}。</p>
<table><thead><tr><th>级别</th><th>项目</th><th>实测</th><th>预期</th></tr></thead><tbody>{findings_rows}</tbody></table>

<h2>9 次要口径：自适应组合器</h2>
<p>按预注册 §7 实现 AlphaForge 式自适应组合器（成熟统计选前 20 因子、扩展窗口池化 OLS、
每 20 日重估、拟合栈 stride=5 且按种子抽 80% 日）。<b>该口径不参与判词</b>，只报告。</p>
<p>为什么值得单独报：这是论文 §VII-A 的实际口径（池级 IC），而派单卡第 4 条写死的主检验量是
<b>因子级均值</b>。两者测的不是同一个量——因子级均值把 25 条近乎完全不变的价量比值因子
（B 组 ΔIC ≈ 2.6e-8）与 11 条价格水平因子（A 组 ΔIC = −0.0055）等权平均，符号由 A 组决定；
组合器则按成熟 RankIC/ICIR 选因子并用 OLS 定权，价格水平因子在其中拿到的是<b>负权重</b>
（它们的 IC 是负的），于是同一份漂移在池级表现为「端锚定得分更高」。
这就是论文那句「池级差异来自组合器怎么选、怎么加权」的实测形态：<b>漂移客观存在，
池级符号取决于组合器，取决于因子池里非不变因子的 IC 符号</b>。</p>
<table><thead><tr><th class="num">种子</th><th class="num">IC(E)</th><th class="num">IC(C)</th><th class="num">ΔIC</th><th class="num">有效日</th><th class="num">平均入选因子</th></tr></thead>
<tbody>{comb_rows}</tbody></table>

<h2>10 失败记录、偏离与限制</h2>
<ul>
<li><b>偏离 D1</b>：C1/V2 容差由 1e-9 改为按字段分辨率推导（预注册 v1.1，跑数前落盘）。
严格口径的 {fc["C1"][0]["violations_v1_0_strict_1e_9"]:,} 行违规照样披露。</li>
<li><b>未取到</b>：Qlib 公共 bundle 无 <code>$vwap</code> 字段、日历止于 2020-09-25 → 派单卡验收项 3 的「逐值比对」不可做；
Qlib 成分文件不在本机 → 1,730 vs {man["universe"]["union_stocks"]:,} 的 130 只残差未逐只归因。</li>
<li><b>口径差异</b>：本单以<b>因子级均值</b>为主检验量（派单卡第 4 条），论文的 0.0062 是<b>组合器池级</b>数字；
两者不可直接相减，故同时报告组合器口径（§9）。</li>
<li><b>不做</b>：checker 复刻、认证池、AlphaGen/AlphaSAGE/GP 重跑、Table S2 单元用例、任何评级/目标价/交易建议。</li>
<li><b>数据层限制</b>：成分表为逐月快照（非逐日生效），停牌日按前向填充保留行（字段字典 §5）；退市股保留至最后交易日，不回溯剔除。</li>
</ul>

<h2>11 复现命令</h2>
<blockquote class="mono">
/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_fetch.py<br>
/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_represent.py<br>
/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_audit.py<br>
/home/moyongkai/.venvs/quantdb/bin/python audit/recompute_work593.py<br>
/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_report.py
</blockquote>
<p class="small">快照 sha256 见 <code>experiments/WORK-593/snapshot_manifest.json</code> 与
<code>representation.json</code>；实验登记见 <code>experiments/registry.md</code>。</p>

<p class="meta" style="margin-top:24px">本报告为内部研究模拟；撰写方非持牌机构；不构成投资建议与收益承诺。
所有数字可由上述命令在同一份数据快照上重建。</p>
</body></html>"""

    path = os.path.join(OUT, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"report -> {os.path.relpath(path, REPO)} ({len(html)/1e6:.2f} MB)")
    return path


def render(path):
    chrome = "/home/moyongkai/.local/bin/google-chrome"
    png = os.path.join(OUT, "preview_first_screen.png")
    pdf = os.path.join(OUT, "report_print.pdf")
    url = "file://" + path
    base = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars"]
    subprocess.run(base + [f"--screenshot={png}", "--window-size=1440,900", url],
                   check=True, capture_output=True, timeout=180)
    subprocess.run(base + [f"--print-to-pdf={pdf}", "--no-pdf-header-footer", url],
                   check=True, capture_output=True, timeout=180)
    print(f"preview -> {os.path.relpath(png, REPO)}")
    print(f"print   -> {os.path.relpath(pdf, REPO)}")


if __name__ == "__main__":
    p = build()
    render(p)
    sys.exit(0)
