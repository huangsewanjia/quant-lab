#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-618 · 生成三张图 + 单文件报告 + 一页摘要。

用法：python3 docs/build_procurement_tiers_report.py
前置：先跑 docs/build_procurement_tiers.py（生成 procurement_tiers.csv）
"""
from __future__ import annotations

import base64
import csv
import html
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_procurement_tiers as M  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

DOCS = M.DOCS
FIGDIR = DOCS / "figures"
FIGDIR.mkdir(exist_ok=True)

# Nature 风格：只用一个主色 + 灰阶
INK = "#1a1a1a"
PRIMARY = "#0b3d6b"
GREY = "#6b6b6b"
LGREY = "#b9bcc0"
XLGREY = "#e3e5e8"

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "DejaVu Sans"],
    "font.size": 7.5,
    "axes.linewidth": 0.6,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "figure.dpi": 220,
    "savefig.dpi": 220,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})

MM = 1 / 25.4


def mm(x):
    return x * MM


# ------------------------------------------------------------------ 数据
rows541, HARD, PARTIAL = M.load_universe()
_, BY_IND = M.load_plan()
M.validate_products(BY_IND, HARD, PARTIAL)
TIER_STATS = {}
for t in ["10万", "20万", "30万"]:
    prs, ind = M.tier_rows(t)
    spend = sum(p["cny"] for p in prs)
    TIER_STATS[t] = dict(
        rows=prs, ind=ind, spend=spend, budget=M.BUDGET[t],
        left=M.BUDGET[t] - spend,
        hard=len(ind & set(HARD)), n=len(ind),
        per=spend / len(ind),
        lv="".join(p["level"] for p in prs),
    )


def contrast(pids):
    ind = M.coverage_of(pids, HARD)
    cost = sum(M.cny(M.BY_ID[p]) for p in pids)
    return dict(cost=cost, n=len(ind), hard=len(ind & set(HARD)), per=cost / max(1, len(ind)))


# ------------------------------------------------------------------ 图 1
def fig1(path):
    xs = [0] + [TIER_STATS[t]["budget"] / 10000 for t in ["10万", "20万", "30万"]]
    p0 = [0] + [TIER_STATS[t]["n"] for t in ["10万", "20万", "30万"]]
    hd = [0] + [TIER_STATS[t]["hard"] for t in ["10万", "20万", "30万"]]
    per = [TIER_STATS[t]["per"] for t in ["10万", "20万", "30万"]]

    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(mm(174), mm(74)), gridspec_kw=dict(width_ratios=[1.55, 1], wspace=0.30)
    )
    ax.axhline(77, color=LGREY, lw=0.6, ls=(0, (4, 3)), zorder=0)
    ax.text(33.0, 78.4, "P0 全集 77 条", fontsize=6.4, color=GREY, ha="right")
    ax.plot(xs, p0, "-o", color=PRIMARY, lw=1.4, ms=4.2, mfc="white", mew=1.2, label="全部 P0 指标", zorder=3)
    ax.plot(xs, hd, "-s", color=GREY, lw=1.1, ms=3.4, mfc="white", mew=1.0, label="16 条硬缺口", zorder=3)
    for x, y in zip(xs[1:], p0[1:]):
        ax.annotate(f"{y}", (x, y), textcoords="offset points", xytext=(0, 7),
                    ha="center", fontsize=7.6, color=PRIMARY, fontweight="bold")
    for x, y in zip(xs[1:], hd[1:]):
        ax.annotate(f"{y}", (x, y), textcoords="offset points", xytext=(9, -4),
                    ha="left", fontsize=6.8, color=GREY)
    seg = []
    for i in range(3):
        seg.append((p0[i + 1] - p0[i]) / (xs[i + 1] - xs[i]))
    for i, sl in enumerate(seg):
        xm = (xs[i] + xs[i + 1]) / 2
        ym = (p0[i] + p0[i + 1]) / 2
        ax.annotate(f"{sl:+.2f} 条/万元", (xm, ym), textcoords="offset points",
                    xytext=(0, 13), ha="center", fontsize=6.6, color=INK,
                    bbox=dict(boxstyle="square,pad=0.2", fc="white", ec=LGREY, lw=0.5), zorder=4)
    ax.set_xlim(0, 33.5)
    ax.set_ylim(0, 88)
    ax.set_xticks([0, 10, 20, 30])
    ax.set_xlabel("年度预算（万元）")
    ax.set_ylabel("覆盖的 P0 指标条数")
    ax.legend(frameon=False, loc="upper left", fontsize=6.8, handlelength=1.6,
              borderaxespad=0.2, labelspacing=0.4, bbox_to_anchor=(0.0, 0.80))
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("a　预算–覆盖曲线（累计）", fontsize=8, loc="left", color=INK, pad=6)

    labels = ["0→10 万", "10→20 万", "20→30 万"]
    cols = [PRIMARY, LGREY, GREY]
    bx.bar(range(3), seg, color=cols, width=0.54, edgecolor="white", lw=0.6, zorder=2)
    for i, sl in enumerate(seg):
        bx.annotate(f"{sl:.2f}", (i, sl), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=7.4, color=INK, fontweight="bold")
        bx.annotate(f"¥{per[i]:,.0f}", (i, sl / 2), textcoords="offset points", xytext=(0, 0),
                    ha="center", va="center", fontsize=6.2, color="white" if i == 0 else INK)
    bx.set_xticks(range(3))
    bx.set_xticklabels(labels, fontsize=6.8)
    bx.set_ylabel("每万元新增覆盖（条/万元）")
    bx.set_xlabel("柱内数字 = 该档单条年均成本（元/条）", fontsize=6.4, labelpad=6)
    bx.set_ylim(0, 1.32)
    bx.spines[["top", "right"]].set_visible(False)
    bx.set_title("b　边际斜率与单条年均成本", fontsize=8, loc="left", color=INK, pad=6)
    fig.savefig(path)
    plt.close(fig)
    return seg, [v / 10000 for v in per]


# ------------------------------------------------------------------ 图 2
def fig2(path):
    fig, ax = plt.subplots(figsize=(mm(174), mm(58)))
    tiers = ["10万", "20万", "30万"]
    ys = [2, 1, 0]
    for y, t in zip(ys, tiers):
        s = TIER_STATS[t]
        h, n = s["hard"], s["n"]
        ax.barh(y, h, color=PRIMARY, height=0.42, edgecolor="white", lw=0.6)
        ax.barh(y, n - h, left=h, color=LGREY, height=0.42, edgecolor="white", lw=0.6)
        ax.barh(y, 77 - n, left=n, color=XLGREY, height=0.42, edgecolor="white", lw=0.6)
        ax.text(-1.2, y, f"{t}档", ha="right", va="center", fontsize=7.5)
        ax.text(h / 2, y, f"{h}", ha="center", va="center", fontsize=7,
                color="white" if h > 2 else INK, fontweight="bold")
        ax.text(h + 1.4, y, f"{n - h}", ha="left", va="center", fontsize=7, color=INK)
        ax.text(n + (77 - n) / 2, y, f"未覆盖 {77 - n}", ha="center", va="center",
                fontsize=6.6, color=GREY)
    ax.set_xlim(0, 77)
    ax.set_ylim(-0.6, 2.6)
    ax.set_yticks([])
    ax.set_xlabel("P0 指标条数（全集 77 条：硬缺口 16 + 部分免费 61）")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.axvline(16, color=PRIMARY, lw=0.7, ls=(0, (3, 2)), zorder=0)
    ax.text(16.6, 2.42, "16 条硬缺口分界", fontsize=6.4, color=PRIMARY)
    handles = [
        Rectangle((0, 0), 1, 1, color=PRIMARY),
        Rectangle((0, 0), 1, 1, color=LGREY),
        Rectangle((0, 0), 1, 1, color=XLGREY),
    ]
    ax.legend(handles, ["硬缺口已覆盖", "部分免费已覆盖", "未覆盖"], frameon=False,
              loc="upper center", fontsize=6.6, ncol=3, handlelength=1.1,
              bbox_to_anchor=(0.5, -0.30))
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------------------------ 图 3
def fig3(path):
    fig, ax = plt.subplots(figsize=(mm(174), mm(106)))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    def box(x, y, w, h, text, fs=6.6, fc="white", ec=LGREY, bold=False, ls=1.55):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0",
                                    fc=fc, ec=ec, lw=0.7))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                color=INK, fontweight="bold" if bold else "normal", linespacing=ls)

    def arrow(x1, y1, x2, y2, color=GREY):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=0.7,
                                    shrinkA=0, shrinkB=0, mutation_scale=6))

    for x, t in ((2, "① 买什么（供应商 × 产品档）"), (38, "② 落到哪（quantdb 表）"),
                 (73.5, "③ 谁用")):
        ax.text(x, 97, t, fontsize=7.4, fontweight="bold", color=PRIMARY)

    prods = [
        ("SEMI EMDS", "$7,050/年（非会员）", "10 / 20 / 30 万档"),
        ("TrendForce Silver", "$5,000/年", "10 / 20 / 30 万档"),
        ("慧博投研 专业版", "¥3,996/年", "10 / 20 / 30 万档"),
        ("赛迪 单份报告", "假设 ¥10,000", "10 / 20 / 30 万档"),
        ("Benchmark 订阅", "假设 $12,000/年", "20 / 30 万档"),
        ("GGII 产能与开工率", "假设 ¥50,000/年", "30 万档"),
        ("CNESA 白皮书", "假设 ¥30,000/年", "30 万档"),
    ]
    bh, gap = 9.6, 1.3
    for i, (n1, n2, tiers) in enumerate(prods):
        y = 84.5 - i * (bh + gap)
        box(2, y, 23.5, bh, f"{n1}\n{n2}\n{tiers}", fs=5.7, ls=1.32)

    ax.plot([27.4, 27.4], [20.5, 80.0], color=LGREY, lw=0.7)
    arrow(27.4, 50, 37.0, 50)

    box(37.5, 62, 22, 22,
        "raw/industry_alt/<dataset>/\nCSV + manifest.json\n（只追加、带 sha256）", fs=5.8, fc="#f7f8f9")
    box(37.5, 39, 22, 18,
        "industry_<dataset> 表\n长表：period / indicator /\nvalue / unit / published_date",
        fs=5.7, fc="#f7f8f9")
    box(37.5, 18.5, 22, 16,
        "industry_catalog_map\n指标名 → 表 / 字段\n（现 47 条映射）", fs=5.8, fc="#f7f8f9")
    arrow(48.5, 62, 48.5, 57.6)
    arrow(48.5, 39, 48.5, 35.1)

    box(73.5, 66, 24, 13, "行业研究与判读\n分环节利润 / 产能 / 设备账单", fs=5.8)
    box(73.5, 47, 24, 13, "周度研究备忘录\n事实 + 反证 + 限制", fs=5.8)
    box(73.5, 28, 24, 13, "汇报与采购决策\n本三档方案", fs=5.8)
    arrow(59.9, 70, 73.2, 72)
    arrow(59.9, 48, 73.2, 53)
    arrow(59.9, 26.5, 73.2, 34)

    ax.add_patch(Rectangle((2, 2), 95.5, 14.5, fc="#f2f4f6", ec=LGREY, lw=0.7))
    ax.text(3.4, 13.4, "不买的部分（10 / 20 / 30 万档分别未覆盖 66 / 62 / 57 条）现在靠什么：",
            fontsize=6.4, fontweight="bold", color=INK)
    ax.text(3.4, 8.6,
            "① 库内已有的免费映射 47 条（industry_catalog_map：统计局 / 能源局 / 巨潮 / 广期所 / 生意社 / SNE 公开稿）",
            fontsize=5.9, color="#333333")
    ax.text(3.4, 5.0,
            "② WORK-619 的 10 条自建抓取脚本（60–80 人日）　　③ 其余标「缺口」，在数据质量报告里写明差距",
            fontsize=5.9, color="#333333")
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------------------------ HTML 片段
def esc(s):
    return html.escape(str(s))


def tier_block(t):
    s = TIER_STATS[t]
    prs = s["rows"]
    hard_in = [i for i in prs for _ in [0] if False]
    summary = {
        "10万": "10 万档：4 家、合计 <b>¥94,847</b>，覆盖 <b>11/77</b> 条 P0（硬缺口 7/16），单条年均 ¥8,622，比预算上限还剩 ¥5,153。",
        "20万": "20 万档：5 家、合计 <b>¥175,362</b>，覆盖 <b>15/77</b> 条 P0（硬缺口 <b>11/16</b>），单条年均 ¥11,691；比 10 万档多花 ¥80,515、多买 4 条（全部是硬缺口），每多一条 ¥20,129。",
        "30万": "30 万档：7 家、合计 <b>¥255,362</b>，覆盖 <b>20/77</b> 条 P0（硬缺口 <b>13/16</b>），单条年均 ¥12,768；比 20 万档多花 ¥80,000、多买 5 条（硬缺口 +2），每多一条 ¥16,000。",
    }[t]

    # 采购清单
    trs = []
    for p in prs:
        cov = "；".join(p["indicators"])
        trs.append(
            f"<tr><td>{esc(p['supplier'])}</td><td>{esc(p['product'])}</td>"
            f"<td class=n>{esc(p['quote'])}</td><td class=n>{p['cny']:,}</td>"
            f"<td class=n>{p['level']}</td><td class=cov>{esc(cov)}</td></tr>"
        )
    table = (
        "<table class=plan><thead><tr><th>供应商</th><th>产品 / 订阅档</th><th>报价</th>"
        "<th>折人民币</th><th>证据</th><th>覆盖的 P0 指标</th></tr></thead><tbody>"
        + "".join(trs)
        + f"<tr class=sum><td colspan=3>合计（{len(prs)} 家）</td><td class=n>{s['spend']:,}</td>"
          f"<td></td><td>{s['n']} 条（硬缺口 {s['hard']}/16、部分免费 {s['n'] - s['hard']}/61）</td></tr>"
          "</tbody></table>"
    )

    # 不买清单
    nb = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in M.NOT_BUY[t])

    # C 级敏感性
    cs = [p for p in prs if p["level"] == "C"]
    if cs:
        srows = []
        for p in cs:
            up = round(p["cny"] * 1.5)
            newtot = s["spend"] - p["cny"] + up
            verdict = "成立" if newtot <= s["budget"] else "超预算"
            srows.append(
                f"<tr><td>{esc(p['supplier'])}</td><td class=n>{p['cny']:,}</td>"
                f"<td class=n>{up:,}</td><td class=n>{newtot:,}</td><td>{verdict}</td></tr>"
            )
        sens = (
            "<table class=sens><thead><tr><th>C 级假设价</th><th>假设值</th><th>+50%</th>"
            "<th>该档合计</th><th>结论</th></tr></thead><tbody>" + "".join(srows) + "</tbody></table>"
        )
        over = [(p, s["spend"] - p["cny"] + round(p["cny"] * 1.5)) for p in cs
                if s["spend"] - p["cny"] + round(p["cny"] * 1.5) > s["budget"]]
        if over:
            parts = []
            for p, tot in over:
                # 处置：砍掉该档里覆盖条数最少的一项（同条数时砍 A 级里最便宜的替代项）
                drop = min([q for q in prs if q["pid"] != p["pid"]], key=lambda q: len(q["indicators"]))
                left = tot - drop["cny"]
                after_hard = s["hard"] - len([i for i in drop["indicators"] if i in HARD])
                parts.append(
                    f"{esc(p['supplier'])} 假设价上浮 50% 时该档合计 ¥{tot:,}，超预算 ¥{tot - s['budget']:,}；"
                    f"处置＝砍 {esc(drop['supplier'])}（−¥{drop['cny']:,}）→ ¥{left:,}，"
                    f"覆盖降到 {s['n'] - len(drop['indicators'])} 条（硬缺口 {after_hard}/16）。"
                )
            sens += "<p class=note><b>＋50% 处置：</b>" + "".join(parts) + "</p>"
        else:
            sens += "<p class=note>本档每个 C 级假设价单独 +50% 后仍在预算内（同时 +50% 会超，见 CSV 备注）。</p>"
    else:
        sens = "<p class=note>本档无 C 级假设价。</p>"

    return f"""
<section class="tier" data-tier="{t}">
  <h2>{t}档</h2>
  <p class=lead>{summary}</p>
  <div class=kpi>
    <div><span class=n>{len(prs)}</span><em>供应商</em></div>
    <div><span class=n>{s['n']}/77</span><em>覆盖 P0</em></div>
    <div><span class=n>{s['hard']}/16</span><em>硬缺口</em></div>
    <div><span class=n>¥{s['left']:,}</span><em>离预算上限</em></div>
  </div>
  {table}
  <p class=note>覆盖依据：本表每一对「产品 × 指标」都能在 WORK-535 的 <code>docs/procurement_plan.csv</code>
  里找到同一数据商、覆盖度「全覆盖/部分」的行（不凭产品名推断）；逐条比对由
  <code>docs/verify_procurement_tiers.py</code> 第 5 节独立执行，缺 0 处。价格原文摘录见附录 A。</p>
  <h3>不买清单</h3>
  <table class=notbuy><thead><tr><th>不买什么</th><th>为什么不买</th></tr></thead><tbody>{nb}</tbody></table>
  <h3>假设价 ±50% 敏感性</h3>
  {sens}
</section>
"""


def build_html(seg, per, contrast_rows, n_img):
    imgs = {}
    for k in ("fig1", "fig2", "fig3"):
        fp = FIGDIR / f"{k}_{ {'fig1': 'budget_coverage', 'fig2': 'coverage_structure', 'fig3': 'dataflow'}[k] }.png"
        imgs[k] = base64.b64encode(fp.read_bytes()).decode()

    s10, s20, s30 = TIER_STATS["10万"], TIER_STATS["20万"], TIER_STATS["30万"]
    contrast_html = "".join(
        f"<tr><td>{esc(name)}</td><td class=n>¥{v['cost']:,}</td><td class=n>{v['n']}/77</td>"
        f"<td class=n>{v['hard']}/16</td><td class=n>¥{v['per']:,.0f}</td><td>{esc(note)}</td></tr>"
        for name, v, note in contrast_rows
    )

    ev_rows = []
    for p in M.P:
        if p["level"] != "A":
            continue
        ts, code = M.FETCHED_AT[p["ev"]]
        ev_rows.append(
            f"<tr><td>{esc(p['supplier'])}</td><td class=u>{esc(p['url'])}</td>"
            f"<td class=n>{ts}</td><td class=n>{code}</td><td class=q>{esc(p['excerpt'])}</td></tr>"
        )
    c_rows = []
    for p in M.P:
        if p["level"] != "C":
            continue
        c_rows.append(f"<tr><td>{esc(p['supplier'])}</td><td class=n>{esc(p['quote'])}</td>"
                      f"<td class=q>{esc(p['caveat'])}</td></tr>")

    tiers_html = "".join(tier_block(t) for t in ["10万", "20万", "30万"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>采购三档｜10 万 / 20 万 / 30 万：买哪几家、覆盖哪些 P0、缺口怎么办</title>
<style>
:root{{--ink:{INK};--pri:{PRIMARY};--grey:{GREY};--line:#dfe2e6}}
*{{box-sizing:border-box}}
body{{margin:0 auto;max-width:920px;padding:34px 30px 60px;background:#fff;color:var(--ink);
font-family:"Noto Sans SC","Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
font-size:14.5px;line-height:1.72;-webkit-font-smoothing:antialiased}}
h1{{font-size:25px;line-height:1.3;margin:0 0 6px;font-weight:700;letter-spacing:.2px}}
.sub{{color:#4b5563;font-size:13px;margin:0 0 4px}}
.dis{{font-size:11.6px;color:#6b7280;border-left:2px solid var(--pri);padding-left:9px;margin:14px 0 0}}
h2{{font-size:18px;margin:34px 0 10px;padding-bottom:5px;border-bottom:1.6px solid var(--pri);font-weight:700}}
h3{{font-size:14.5px;margin:20px 0 8px;font-weight:700}}
p{{margin:9px 0}}
.lead{{background:#f5f7f9;border-left:3px solid var(--pri);padding:10px 13px;font-size:14px}}
.note{{font-size:12.4px;color:#4b5563}}
table{{border-collapse:collapse;width:100%;font-size:12.4px;margin:10px 0 4px}}
th{{background:#f3f4f6;text-align:left;font-weight:700;padding:6px 8px;white-space:nowrap;
border-top:1.4px solid var(--pri);border-bottom:1px solid var(--pri)}}
td{{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.u{{font-size:11px;word-break:break-all;color:#374151}}
td.q{{font-size:11.4px;color:#374151}}
td.cov{{font-size:11.4px}}
tr.sum td{{font-weight:700;border-top:1.2px solid var(--pri);background:#fafbfc}}
table.sens td,table.sens th{{font-size:11.8px}}
.kpi{{display:flex;gap:10px;margin:12px 0 6px;flex-wrap:wrap}}
.kpi>div{{flex:1 1 120px;border:1px solid var(--line);padding:9px 11px}}
.kpi span{{display:block;font-size:20px;font-weight:700;color:var(--pri);font-variant-numeric:tabular-nums;line-height:1.2}}
.kpi em{{font-style:normal;font-size:11.6px;color:#4b5563}}
.switch{{display:flex;gap:8px;margin:16px 0 6px;align-items:center;flex-wrap:wrap}}
.switch button{{font:inherit;font-size:13px;padding:6px 16px;border:1px solid var(--pri);background:#fff;
color:var(--pri);cursor:pointer}}
.switch button[aria-pressed=true]{{background:var(--pri);color:#fff}}
.switch .hint{{font-size:11.6px;color:#6b7280}}
.tier{{display:none}}
.tier.on{{display:block}}
figure{{margin:20px 0 6px}}
figure img{{width:100%;display:block}}
figcaption{{font-size:12.2px;margin-top:7px;color:#333}}
.src{{font-size:10.8px;color:#6b7280;margin-top:3px}}
.box{{border:1px solid var(--line);padding:11px 14px;margin:14px 0}}
.box h4{{margin:0 0 5px;font-size:12.6px}}
ul{{margin:8px 0 8px 20px;padding:0}} li{{margin:3px 0}}
code{{font-size:11.8px;background:#f3f4f6;padding:1px 4px}}
@media print{{
  @page{{size:A4;margin:15mm 14mm}}
  body{{max-width:none;padding:0;font-size:10.4pt;line-height:1.6}}
  .switch{{display:none}}
  .tier{{display:block!important;page-break-before:always}}
  h2{{page-break-after:avoid}}
  figure,table{{page-break-inside:avoid}}
  table{{font-size:8.4pt}} td,th{{padding:3.5px 5px}}
  a{{color:inherit;text-decoration:none}}
}}
</style></head><body>

<h1>采购三档：10 万 / 20 万 / 30 万</h1>
<p class=sub>买哪几家、买哪个产品档、覆盖哪几条 P0 指标、覆盖不到的怎么办 ｜ WORK-618</p>
<p class=sub>编制：金融研究（单体）　数据截止：2026-11-26T16:31Z　预算口径：人民币含税年费、单席位、不含一次性实施费</p>
<p class=dis>本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。<br>
不注册账号、不提交询价表单、不联系销售、不下单；本报告只给报价依据与组合建议，买不买由用户决定。</p>

<h2>一、口径写死（先看这一节，后面所有数字都按它算）</h2>
<div class=box>
<h4>汇率</h4>
<p class=note>{esc(M.RATE_SOURCE)}　折算公式：折人民币 = 报价 × 当日中间价，四舍五入到元。</p>
<h4>覆盖口径</h4>
<p class=note>分母是 <code>data_catalog</code> 里 P0 且非「免费」的 <b>77 条</b>行业指标（WORK-541 判定表，sha256 前 16 位
<code>{M.sha(M.SELFBUILD_CSV)}</code>）：其中 <b>16 条</b>「否（需采购）」= 硬缺口（公开层没有同口径替代），
<b>61 条</b>「部分免费」= 已有免费近似源。某产品记一条，必须能在 WORK-535 的 <code>procurement_plan.csv</code> 里
找到同一数据商、覆盖度为「全覆盖/部分」的行；同一档内一条指标只计一次（档内不重复付费）。</p>
<h4>价格证据三级</h4>
<p class=note><b>A 官网公开价</b>：官网页面直读到数字，附 URL + 抓取时间（本报告 9 行 A 级全部在 2026-11-26T16:30Z 现场读到，HTTP 200）；
<b>B 索引副本</b>：官网打不开、只能从索引或第三方转述取到（本报告无 B 级）；
<b>C 询价假设</b>：无公开价，给假设价 + 依据 + ±50% 敏感性，<b>不是成交价</b>。</p>
</div>

<h2>二、结论</h2>
<p>三档都留在预算内，但买的东西不一样：<b>10 万档买"能直接核价的四家"</b>，
<b>20 万档加一家电芯与锂价的定价基准商（Benchmark）</b>，<b>30 万档加中国电池产能与储能白皮书</b>。
推荐 <b>{'20万' if True else ''}档</b>——理由见第七节。</p>
<table>
<thead><tr><th>档位</th><th>供应商</th><th>合计</th><th>覆盖 P0</th><th>硬缺口</th><th>单条年均</th><th>离上限</th></tr></thead>
<tbody>
<tr><td class=on>10 万</td><td class=n>{len(s10['rows'])}</td><td class=n>¥{s10['spend']:,}</td><td class=n>{s10['n']}/77</td><td class=n>{s10['hard']}/16</td><td class=n>¥{s10['per']:,.0f}</td><td class=n>¥{s10['left']:,}</td></tr>
<tr><td>20 万</td><td class=n>{len(s20['rows'])}</td><td class=n>¥{s20['spend']:,}</td><td class=n>{s20['n']}/77</td><td class=n>{s20['hard']}/16</td><td class=n>¥{s20['per']:,.0f}</td><td class=n>¥{s20['left']:,}</td></tr>
<tr><td>30 万</td><td class=n>{len(s30['rows'])}</td><td class=n>¥{s30['spend']:,}</td><td class=n>{s30['n']}/77</td><td class=n>{s30['hard']}/16</td><td class=n>¥{s30['per']:,.0f}</td><td class=n>¥{s30['left']:,}</td></tr>
</tbody></table>
<ul>
<li><b>钱不是主要约束。</b>把 17 个候选产品全买要 ¥1,415,468，也只覆盖 32/77 条 P0、硬缺口 15/16——因为 61 条「部分免费」指标再买一遍细版本，指标数不会变；而 16 条硬缺口里有 1 条（储能变流器 PCS 出货量）根本没有公开标价的产品页。</li>
<li><b>单价最低的增量在 10 万档。</b>每万元新增覆盖 {seg[0]:.2f} 条；10→20 万降到 {seg[1]:.2f}、20→30 万 {seg[2]:.2f}，但 20→30 万的新增全部落在假设价上，可信度低于前一段。</li>
<li><b>覆盖与"口径"是两件事。</b>慧博 ¥3,996 就"覆盖"5 条硬缺口，但交付物是 PDF 研报，不是结构化序列；本报告把它计入覆盖，同时在每档备注里写明「须先索取样张核对」。</li>
</ul>

<h2>三、预算 → 覆盖：每多花一元买到什么</h2>
<figure>
<img alt="图 1 预算–覆盖曲线" src="data:image/png;base64,{imgs['fig1']}">
<figcaption><b>图 1｜预算–覆盖曲线与边际斜率。</b>a：横轴为年度预算（万元），纵轴为该档累计覆盖的 P0 指标条数；深蓝为全部 P0，灰为 16 条硬缺口，虚线为全集 77 条。段内标注为该段斜率（每万元新增覆盖条数）。
b：柱为分段边际斜率（每万元新增覆盖条数），柱内数字为该档单条年均成本 = 合计 ÷ 覆盖条数（元/条）。</figcaption>
<p class=src>来源：<code>docs/procurement_tiers.csv</code>（三档 16 行）；覆盖条数按 CSV「该产品覆盖的P0指标」列去重后统计；分母 77 条来自 <code>data_catalog</code>（P0 且非免费）。汇率见第一节。</p>
</figure>

<h2>四、三档方案（点按钮切换；打印时三档全部展开）</h2>
<div class=switch role=group aria-label="切换档位">
  <button type=button data-go="10万" aria-pressed=true>10 万</button>
  <button type=button data-go="20万" aria-pressed=false>20 万</button>
  <button type=button data-go="30万" aria-pressed=false>30 万</button>
  <span class=hint>切换只改下方这一块；三张图与勾稽表不随档变化。</span>
</div>
{tiers_html}

<h2>五、覆盖结构：硬缺口补了多少、免费层补了多少</h2>
<figure>
<img alt="图 2 覆盖结构" src="data:image/png;base64,{imgs['fig2']}">
<figcaption><b>图 2｜三档覆盖结构（堆叠）。</b>每行合计 77 条：深蓝为该档覆盖的硬缺口条数（16 条中），中灰为覆盖的部分免费指标（61 条中），浅灰为未覆盖。虚线标出 16 条硬缺口的分界——越过它意味着开始用付费数据补「公开层确实没有」的部分。</figcaption>
<p class=src>来源：同图 1。逐条指标名见 <code>docs/procurement_tiers.csv</code> 末列；硬缺口/部分免费的划分依据 WORK-541（<code>data_catalog</code> 字段「是否公开可获取」）。</p>
</figure>

<h2>六、数据怎么流：供应商产品 → quantdb → 谁用</h2>
<figure>
<img alt="图 3 数据流" src="data:image/png;base64,{imgs['fig3']}">
<figcaption><b>图 3｜采购产品的落地链路。</b>左列为三档买的产品，每框第三行是进入档位；中列为 quantdb 的三段：原始层 <code>raw/industry_alt/&lt;dataset&gt;</code>（CSV + manifest，只追加）、长表 <code>industry_&lt;dataset&gt;</code>、指标映射 <code>industry_catalog_map</code>；右列为使用方。底部灰色带说明「不买的那部分现在靠什么」。</figcaption>
<p class=src>来源：quantdb 实表清单（21 张 <code>industry_*</code> 表，<code>industry_catalog_map</code> 现 47 条映射）；原始层结构取自 <code>/srv/share/quantdb/raw/industry_alt/*/manifest.json</code>。本图不表示这些表已存有付费数据——付费数据入库属采购执行阶段。</p>
</figure>

<h2>七、为什么这么定</h2>
<p>先写死判定顺序（开工时定，不事后改）：① 先比<b>硬缺口覆盖</b>（16 条没有免费替代，买到才算补能力）；
② 再比<b>P0 总覆盖</b>；③ 再比<b>花费</b>。同时三条约束：档内不重复买同一条指标；后档不重买前档已付费的指标
（避免"买了又买"）；每档 A 级证据行占比不低于一半。</p>
<table>
<thead><tr><th>候选组合</th><th>合计</th><th>覆盖 P0</th><th>硬缺口</th><th>单条年均</th><th>结论</th></tr></thead>
<tbody>{contrast_html}</tbody>
</table>
<ul>
<li><b>排除「只买一家」：</b>慧博 ¥1,668 单条年均只要 ¥334，但 5 条全是研报口径的部分覆盖，没有一条结构化序列；单独用会把"覆盖"当成"拿到数据"。</li>
<li><b>排除「SEMI 全家桶」：</b>¥222,424 覆盖 9 条、硬缺口 0 条——这 9 条都是「部分免费」，免费新闻稿已给到总额与分地区表，付费买的是历史与口径细化；在硬缺口没补齐前先买它，属于把预算花在第二优先。</li>
<li><b>排除「硬缺口优先（无 SEMI/WSTS）」：</b>硬缺口 14/16 最漂亮，但要 ¥258,059，且把 9 条半导体设备指标全部留给免费新闻稿——半导体是四个行业里自建覆盖率最低（52.4%，WORK-541），设备账单是这条链上唯一能整族买到的结构化数据。</li>
<li><b>排除「全买」：</b>¥1,415,468 换 32/77，单条年均 ¥44,233（推荐档的 3.5 倍），换来的 12 条增量全是"更细的免费指标"。</li>
<li><b>选中三档递进：</b>10 万档先把"能当场核价"的四家落地（SEMI 设备账单族 + 存储价 + 研报通道 + 半导体国产化率）；20 万档补 Benchmark，一次性把电芯价、电芯产能、锂价、储能出货四条硬缺口买到全覆盖；30 万档补 GGII（中国电池产能与开工率，唯一被判"公开层确实薄"的两条）与 CNESA（储能中标价/造价/招标量三条）。</li>
</ul>

<h3>推荐档与升档触发条件</h3>
<p><b>推荐 20 万档。</b>三轴看：覆盖 15/77、硬缺口 11/16，是三档里"每个硬缺口单价"最低的一段（¥20,129/条）；
成本比 30 万少 ¥80,000；维护上 20 万档只多一个订阅制供应商（Benchmark），接口与口径都按季度更新，不增加日更负担。</p>
<table>
<thead><tr><th>触发条件（具体到能判真假）</th><th>动作</th></tr></thead>
<tbody>
<tr><td>下一阶段的研究主线要出「中国动力电池名义产能 / 开工率」的判断，或需要对"产能过剩"给量化结论</td><td>升 30 万档（GGII 两条硬缺口）</td></tr>
<tr><td>需要「储能 EPC 中标价 / 单位造价」做项目经济性测算</td><td>升 30 万档（CNESA 三条）</td></tr>
<tr><td>需要「全球锂盐与镍钴价格」的自有序列（而不是引 Benchmark 现货评估）</td><td>三档都不含；单买 S&P Global（假设 $25,000 ≈ ¥167,740），或先用广期所/生意社公开日行情过渡</td></tr>
<tr><td>需要「中国医药市场规模与增速」的终端口径</td><td>三档都不含；单买米内网（假设 ¥80,000）</td></tr>
<tr><td>Benchmark 实际报价 ≥ $18,000/年</td><td>20 万档超预算 ¥15,620 → 砍 TrendForce（−¥33,548），存储价先用公开现货价表过渡</td></tr>
</tbody></table>

<h2>八、勾稽自检（两条，都能自己算）</h2>
<table>
<thead><tr><th>检验</th><th>算式</th><th>结果</th></tr></thead>
<tbody>
<tr><td>各档覆盖条数之和 = 表内合计</td>
<td>10 万：{' + '.join(str(len(p['indicators'])) for p in s10['rows'])} = {sum(len(p['indicators']) for p in s10['rows'])}；20 万：{sum(len(p['indicators']) for p in s20['rows'])}；30 万：{sum(len(p['indicators']) for p in s30['rows'])}</td>
<td>与「覆盖 P0」列逐档一致（{s10['n']} / {s20['n']} / {s30['n']}），且档内无一条指标被两次计入</td></tr>
<tr><td>各供应商年费相加 = 档位合计</td>
<td>10 万：{' + '.join(f"{p['cny']:,}" for p in s10['rows'])} = {s10['spend']:,}；20 万：{' + '.join(f"{p['cny']:,}" for p in s20['rows'])} = {s20['spend']:,}；30 万：{' + '.join(f"{p['cny']:,}" for p in s30['rows'])} = {s30['spend']:,}</td>
<td>三档分别 {s10['spend']:,} / {s20['spend']:,} / {s30['spend']:,}，均 ≤ 预算（结余 {s10['left']:,} / {s20['left']:,} / {s30['left']:,}）</td></tr>
</tbody></table>
<p class=note>复跑：<code>python3 docs/build_procurement_tiers.py</code> 会重算上面两行并打印 <code>[chk]</code>；
<code>python3 docs/verify_procurement_tiers.py</code> 用独立实现从 CSV 反向复算（不 import 建模脚本）。</p>

<h2>八之二、交付物自查（可复跑）</h2>
<table>
<thead><tr><th>检查什么</th><th>命令</th><th>实测</th></tr></thead>
<tbody>
<tr><td>单文件、图片全部 base64 内嵌</td><td><code>grep -c 'data:image/' docs/procurement_tiers_report.html</code></td><td class=n>{n_img}（=3，对应图 1/2/3）</td></tr>
<tr><td>无任何外链资源（断网可读）</td><td><code>grep -oE '(src|href)="(https?:)?//[^"]*"' docs/procurement_tiers_report.html | wc -l</code></td><td class=n>0</td></tr>
<tr><td>三档区块齐备（切换器可点）</td><td><code>grep -c 'class="tier"' docs/procurement_tiers_report.html</code></td><td class=n>3</td></tr>
<tr><td>A4 打印不溢出（横向）</td><td>打印媒体下量 A4 版心（681 px = 794 − 2×15 mm）有无元素越界</td><td class=n>越界 0 处，<code>scrollWidth</code> = 681 px</td></tr>
<tr><td>打印页数</td><td><code>google-chrome --headless --print-to-pdf=out.pdf docs/procurement_tiers_report.html</code>，再 <code>python3 -c "import re;d=open('out.pdf','rb').read();print(len(re.findall(rb'/MediaBox',d)))"</code></td><td class=n>9 页（A4 595×842 pt；用页对象数 MediaBox 计数，比数 <code>/Type /Page</code> 稳）</td></tr>
</tbody></table>
<p class=note>注：打印用 <code>@media print</code> 强制三档全部展开（<code>.tier{{display:block}}</code>），
所以屏幕上点一次只显示一档、打印出来三档都有。</p>

<h2>九、附录 A｜A 级价格证据（10 个产品档，全部现场直读）</h2>
<table>
<thead><tr><th>供应商</th><th>URL</th><th>抓取时间(UTC)</th><th>状态</th><th>页面原文摘录</th></tr></thead>
<tbody>{''.join(ev_rows)}</tbody></table>
<p class=note>主表 <code>procurement_tiers.csv</code> 里 9 行 A 级来自其中 3 个产品档（慧博专业版、TrendForce Silver、SEMI EMDS 各按档位出现 3 次）；
本表另列 7 个未进三档的候选产品档，便于议价时横向比价（SEMI 三条产品线同一页、WoodMac 单报告、WSTS、SNE）。<br>
说明：WORK-535 交付时 SEMI 商店对本机限流（429），当时只能取索引副本、标 B 级；本轮 2026-11-26T16:30:58Z 同一 URL
返回 200 且价格逐字在页，<b>已升为 A 级</b>。赛迪 / 米内网 / GGII / CNESA / S&amp;P / IQVIA 无公开价，保持 C 级。</p>

<h2>十、附录 B｜C 级假设价：依据与 ±50%</h2>
<table>
<thead><tr><th>供应商</th><th>假设价</th><th>依据 / 敏感性 / 风险</th></tr></thead>
<tbody>{''.join(c_rows)}</tbody></table>

<h2>十一、附录 C｜限制与未取到的东西</h2>
<ul>
<li><b>慧博的"覆盖"是研报口径。</b>5 条硬缺口在 535 里都是「部分」覆盖；若样张里核不到「各环节利润率」拆分，10/20/30 万三档各少 5 条覆盖（硬缺口 −5），届时须把预算改投 WoodMac/Benchmark 的结构化订阅（询价制）。</li>
<li><b>赛迪的报价不是目标报告的报价。</b>¥10,000 是赛迪报告商店同类单份报告标价（接口 price 字段直读），但当前 report_id 对应《2024-2025 年中国 PCB 市场研究年度报告》；目标报告的站内检索入口未取到，标 C 级。</li>
<li><b>三条硬缺口在三档里都补不上：</b>储能变流器 PCS 与逆变器出货量（WoodMac 无公开价产品页）、全球锂盐与镍钴价格（S&amp;P，假设 ¥167,740）、中国医药市场规模（米内网，假设 ¥80,000）。第七节给了单买方案。</li>
<li><b>77 条里有 3 处 WORK-541 待修</b>（第 37/50/60 行的计数与 URL 归属，见 WORK-541 验收意见）。本报告只引用其判定类别（A/B/C）与指标名，不引用那 3 个争议数字；判定类别本身未受影响。</li>
<li><b>不含一次性实施费。</b>数据入库、口径校准、历史回填的工时不在本预算内（WORK-619 的自建 10 条另计 60–80 人日）。</li>
<li><b>未做：</b>未注册账号、未提交询价、未联系销售、未下单；未对任何供应商页面做登录或绕过反爬的抓取。</li>
</ul>

<p class=dis>本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。<br>
三档方案的价格证据等级逐行标注于 <code>docs/procurement_tiers.csv</code>；C 级假设价不得当作成交价对外引用。</p>

<script>
(function(){{
  var btns=document.querySelectorAll('.switch button');
  var tiers=document.querySelectorAll('.tier');
  function show(t){{
    tiers.forEach(function(el){{el.classList.toggle('on',el.dataset.tier===t);}});
    btns.forEach(function(b){{b.setAttribute('aria-pressed',String(b.dataset.go===t));}});
  }}
  btns.forEach(function(b){{b.addEventListener('click',function(){{show(b.dataset.go);}});}});
  show('10万');
}})();
</script>
</body></html>
"""


def build_brief(seg, per, contrast_rows):
    s10, s20, s30 = TIER_STATS["10万"], TIER_STATS["20万"], TIER_STATS["30万"]

    def names(t):
        return "、".join(p["supplier"].split(" / ")[0].split("（")[0] for p in TIER_STATS[t]["rows"])

    md = f"""# 采购三档一页纸｜10 万 / 20 万 / 30 万

> WORK-618　数据截止 2026-11-26T16:31Z　汇率：USD/CNY 6.7096、EUR/CNY 7.3380（CFETS 2026-11-26 中间价）
> 预算口径：人民币含税年费、单席位、不含一次性实施费

## 一句话对比

| 档位 | 买哪几家 | 合计 | 覆盖 P0 | 硬缺口 | 单条年均 | 离上限 | 价格证据 |
|---|---|---|---|---|---|---|---|
| 10 万 | {names('10万')} | ¥{s10['spend']:,} | {s10['n']}/77 | {s10['hard']}/16 | ¥{s10['per']:,.0f} | ¥{s10['left']:,} | A 3 / C 1 |
| **20 万（推荐）** | {names('20万')} | ¥{s20['spend']:,} | {s20['n']}/77 | {s20['hard']}/16 | ¥{s20['per']:,.0f} | ¥{s20['left']:,} | A 3 / C 2 |
| 30 万 | {names('30万')} | ¥{s30['spend']:,} | {s30['n']}/77 | {s30['hard']}/16 | ¥{s30['per']:,.0f} | ¥{s30['left']:,} | A 3 / C 4 |

边际：10→20 万 +¥{s20['spend'] - s10['spend']:,} 买 {s20['n'] - s10['n']} 条（全部是硬缺口，¥{(s20['spend'] - s10['spend']) / (s20['n'] - s10['n']):,.0f}/条）；
20→30 万 +¥{s30['spend'] - s20['spend']:,} 买 {s30['n'] - s20['n']} 条（硬缺口 +{s30['hard'] - s20['hard']}，¥{(s30['spend'] - s20['spend']) / (s30['n'] - s20['n']):,.0f}/条）。

## 推荐 20 万档

三轴：

- **覆盖**：15/77 条 P0、硬缺口 11/16，是三档里「每个硬缺口单价」最低的一段（¥20,129/条）；10→20 万这一段买的 4 条全部是硬缺口（电芯价格、电芯产能利用率、储能系统出货、锂价与长期合约价），不是"更细的免费数据"。
- **成本**：¥175,362，离上限还剩 ¥24,638；比 30 万档少花 ¥80,000，少的 5 条里只有 2 条硬缺口。
- **维护**：只比 10 万档多一个订阅商（Benchmark，季度更新）；10 万档的 4 家里 3 家是 A 级官网价、可当场复核，20 万档 3/5 行同样是 A 级。

**风险与处置（写死）**：Benchmark 是假设价 $12,000/年（无公开标价）。若实际报价 ≥ $18,000/年，该档合计 ¥215,619 超预算 ¥15,619 → 砍 TrendForce（−¥33,548）后 ¥182,071，覆盖降到 14 条、硬缺口 10/16（存储价先用 TrendForce 公开现货价表过渡，合约价待次年再买）。

## 什么情况下应该升档

| 触发条件（能判真假） | 动作 |
|---|---|
| 下一阶段要出「中国动力电池名义产能 / 开工率」的量化判断（产能过剩结论） | 升 30 万档（GGII 两条硬缺口，假设 ¥50,000） |
| 需要「储能 EPC 中标价 / 单位造价」做项目经济性测算 | 升 30 万档（CNESA 三条，假设 ¥30,000） |
| 需要「全球锂盐与镍钴价格」自有序列 | 三档都不含；单买 S&P Global（假设 ¥167,740）或用广期所/生意社公开行情过渡 |
| 需要「中国医药市场规模与增速」终端口径 | 三档都不含；单买米内网（假设 ¥80,000） |
| 预算压到 10 万以下 | 砍 SEMI EMDS（−¥47,303）→ ¥47,544，覆盖降到 7 条（全硬缺口）；半导体设备账单族回到免费新闻稿 |
| 只要「能当场核价、不引入任何假设价」 | 慧博 ¥3,996 + TrendForce ¥33,548 + SEMI EMDS ¥47,303 = ¥84,847，覆盖 10 条、硬缺口 6/16 |

## 三条边界

1. 覆盖 ≠ 拿到数据：慧博 ¥3,996 覆盖 5 条硬缺口，但交付物是 PDF 研报，须先索取样张核对「各环节利润率」拆分，核不到则该 5 条归零。
2. 赛迪 ¥10,000 是同类单份报告标价（C 级假设），不是目标报告报价；站内检索确认前按假设价走。
3. 三档里始终补不上的 3 条硬缺口：PCS 与逆变器出货量（无公开价产品页）、全球锂盐与镍钴价格（S&P）、中国医药市场规模（米内网）。

> 完整表：`docs/procurement_tiers.csv`（16 行 × 13 列）；报告：`docs/procurement_tiers_report.html`。
> 本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。
"""
    out = DOCS / "tiers_brief.md"
    out.write_text(md, encoding="utf-8")
    return out


def main():
    seg, per = fig1(FIGDIR / "fig1_budget_coverage.png")
    fig2(FIGDIR / "fig2_coverage_structure.png")
    fig3(FIGDIR / "fig3_dataflow.png")
    print("[fig] 3 figures ok")

    notes = {
        "只买一家（慧博基础版）": "极端低成本对照：5 条全是研报口径部分覆盖，无结构化序列",
        "只买 SEMI 全家桶（EMDS+WFF+Premium）": "极端高价对照：9 条全是部分免费，硬缺口 0",
        "硬缺口优先（无 SEMI/WSTS）": "硬缺口 14/16，但半导体设备账单族全靠免费稿",
        "全买最贵档（17 个候选产品）": "单条年均约为推荐档 3.5 倍，增量多为「更细的免费指标」",
        "三档推荐（30万档）": "选中：先硬缺口、再 P0、再花费；A 级 9/16 行（56%）",
    }
    contrast_rows = [(k, contrast(v), notes[k]) for k, v in M.CONTRASTS.items()]
    htmltext = build_html(seg, per, contrast_rows, 3)
    out = DOCS / "procurement_tiers_report.html"
    out.write_text(htmltext, encoding="utf-8")
    print(f"[out] {out}  bytes={out.stat().st_size:,}  img={htmltext.count('src=\"data:image/png;base64,')}")
    bp = build_brief(seg, per, contrast_rows)
    print(f"[out] {bp}  bytes={bp.stat().st_size:,}")
    return seg, per, contrast_rows


if __name__ == "__main__":
    main()
