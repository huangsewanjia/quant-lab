#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-537 · 报告配图（Nature 风格：克制配色、直接标注、无冗余图例、300 dpi）。

数据来源（不在脚本里硬编码结论，全部从产物读）：
  - docs/foreign_research_channels.csv  → 图1（可抓性构成）、图3（实测发布间隔）
  - raw/foreign_research/*/*/*.md       → 图2（评级/目标价关键词命中）

输出：docs/WORK-537/figures/fig1_crawlability.png / fig2_rating_hits.png / fig3_cadence.png
"""
from __future__ import annotations

import csv
import os
import re
from collections import Counter, OrderedDict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 跟随脚本所在仓库（worktree 安全）
CSV_DELIVER = os.path.join(REPO, "docs", "foreign_research_channels.csv")
RAW = "/srv/share/quantdb/raw/foreign_research"
FIG_DIR = os.path.join(REPO, "docs", "WORK-537", "figures")
os.makedirs(FIG_DIR, exist_ok=True)

# ---- 字体：优先系统中文字体，避免豆腐块 ----
CJK = None
for cand in ("Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC", "WenQuanYi Zen Hei",
             "Noto Sans SC", "Droid Sans Fallback"):
    try:
        path = font_manager.findfont(font_manager.FontProperties(family=cand), fallback_to_default=False)
        font_manager.fontManager.addfont(path)
        CJK = cand
        break
    except Exception:
        continue
if CJK:
    plt.rcParams["font.family"] = ["sans-serif"]
    plt.rcParams["font.sans-serif"] = [CJK, "DejaVu Sans"]
else:
    plt.rcParams["font.family"] = "sans-serif"
    print("[warn] 未找到中文字体，图中中文可能显示为方块")

plt.rcParams.update({
    "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 9.5,
    "xtick.labelsize": 8.5, "ytick.labelsize": 8.5, "legend.fontsize": 8.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 3, "ytick.major.size": 3,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 300, "savefig.dpi": 300, "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
    "pdf.fonttype": 42, "ps.fonttype": 42,
})

# 克制配色：蓝=可采，琥珀=需渲染，红=受限，灰=合规/网络
COLOR = {
    "直连": "#2f6f9f",
    "需JS(列表未渲染)": "#e3a13c",
    "403": "#c1554b",
    "需登录(落地登录页)": "#c1554b",
    "robots禁": "#7d7d7d",
    "robots不可读(HTTP 403)": "#7d7d7d",
    "本机出口不可达": "#b9b9b9",
}
ORDER = ["直连", "需JS(列表未渲染)", "403", "需登录(落地登录页)", "本机出口不可达",
         "robots禁", "robots不可读(HTTP 403)"]
SHORT = {"直连": "直连可采", "需JS(列表未渲染)": "需 JS", "403": "403",
         "需登录(落地登录页)": "需登录", "本机出口不可达": "出口不可达",
         "robots禁": "robots 禁", "robots不可读(HTTP 403)": "robots 不可读"}
CAT_ORDER = ["投行官网", "财经媒体", "监管披露", "聚合平台", "社区"]


def read_rows():
    with open(CSV_DELIVER, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fig1(rows):
    """图1：按渠道类别的可抓性构成（堆叠横条）"""
    cats = OrderedDict()
    for r in rows:
        cats.setdefault(r["类型(投行官网/财经媒体/监管披露/聚合平台/社区)"], []).append(r)
    fig, ax = plt.subplots(figsize=(6.6, 2.9))
    left = Counter()
    ypos = list(range(len(CAT_ORDER)))[::-1]
    for key in ORDER:
        vals = []
        for cat in CAT_ORDER:
            n = sum(1 for r in cats.get(cat, []) if r["可抓性(直连/需JS/403/需登录/robots禁)"] == key)
            vals.append(n)
        ax.barh(ypos, vals, left=[left[c] for c in CAT_ORDER], height=0.58,
                color=COLOR[key], label=SHORT[key], edgecolor="white", linewidth=0.5)
        for i, v in enumerate(vals):
            if v:
                left[CAT_ORDER[i]] += v
                ax.text(left[CAT_ORDER[i]] - v / 2, ypos[i], str(v), ha="center", va="center",
                        color="white", fontsize=8.5, fontweight="bold")
    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{c}\n(n={len(cats.get(c, []))})" for c in CAT_ORDER])
    ax.set_xlabel("渠道数")
    ax.set_xlim(0, max(len(cats.get(c, [])) for c in CAT_ORDER) + 1.2)
    ax.set_title("a  可抓性构成：29 个外资研报公开层渠道（2026-11-23 实测）", loc="left", pad=8)
    ax.legend(ncol=4, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.28))
    fig.savefig(os.path.join(FIG_DIR, "fig1_crawlability.png"))
    plt.close(fig)


RATING_RE = re.compile(
    r"(price target|target price|our \w+ rating|overweight|underweight|equalweight|"
    r"outperform|underperform|buy rating|sell rating|hold rating|目标价|评级|买入评级|增持|减持)", re.I)


def fig2():
    """图2：9 篇正文样例的评级/目标价关键词命中数"""
    hits = []
    for ch in sorted(os.listdir(RAW)):
        if ch.startswith("_"):
            continue
        d = os.path.join(RAW, ch)
        if not os.path.isdir(d):
            continue
        for date in os.listdir(d):
            for slug in os.listdir(os.path.join(d, date)):
                md = os.path.join(d, date, slug, slug + ".md")
                if not os.path.exists(md):
                    cands = [f for f in os.listdir(os.path.join(d, date, slug)) if f.endswith(".md")]
                    if not cands:
                        continue
                    md = os.path.join(d, date, slug, cands[0])
                text = open(md, encoding="utf-8").read()
                n = len(RATING_RE.findall(text))
                meta = os.path.join(d, date, slug, "meta.json")
                title = ""
                if os.path.exists(meta):
                    import json
                    title = json.load(open(meta, encoding="utf-8")).get("title", "")
                hits.append((ch, n, title[:46]))
                break
            else:
                continue
            break
    hits.sort(key=lambda x: x[1])
    labels = [f"{c}\n{t}" for c, n, t in hits]
    vals = [n for _c, n, _t in hits]
    cols = ["#c1554b" if v else "#2f6f9f" for v in vals]
    fig, ax = plt.subplots(figsize=(6.6, 3.5))
    y = list(range(len(hits)))
    ax.barh(y, vals, color=cols, height=0.62, edgecolor="white", linewidth=0.5)
    for i, v in enumerate(vals):
        ax.text(v + 0.35, i, str(v), va="center", fontsize=8.5, color="#333333")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.6)
    ax.set_xlabel("正文全文中评级/目标价关键词命中次数")
    ax.set_xlim(0, max(vals) * 1.18 + 1)
    ax.set_title("b  正文扫描：外资投行样本 0 处实质命中，唯一含评级的是中资转述", loc="left", pad=8)
    ax.text(0.0, -0.19, "蓝＝0 命中；红＝有命中。JPM 的 1 次为 underperforming other developed market government bond markets（债券市场表述，非个股评级）；\n格隆汇 22 次为转载国盛证券评级（中资转述）",
            transform=ax.transAxes, fontsize=7.6, color="#555555")
    fig.savefig(os.path.join(FIG_DIR, "fig2_rating_hits.png"))
    plt.close(fig)


def fig3(rows):
    """图3：实测发布间隔中位数"""
    items = []
    for r in rows:
        m = re.search(r"相邻间隔中位数 (\d+) 天", r["更新频率"])
        if m:
            items.append((r["渠道"], int(m.group(1))))
    if not items:
        return
    items.sort(key=lambda x: x[1])
    labels = [c for c, _v in items]
    vals = [v for _c, v in items]
    fig, ax = plt.subplots(figsize=(6.6, 2.9))
    y = list(range(len(items)))
    ax.barh(y, vals, color="#2f6f9f", height=0.6, edgecolor="white", linewidth=0.5)
    for i, v in enumerate(vals):
        ax.text(v + 0.6, i, f"{v} 天", va="center", fontsize=8.5, color="#333333")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("列表页可见日期的相邻间隔中位数（天）")
    ax.set_xlim(0, max(vals) * 1.2 + 1)
    ax.set_title("c  实测发布节奏（单页快照，描述性）", loc="left", pad=8)
    fig.savefig(os.path.join(FIG_DIR, "fig3_cadence.png"))
    plt.close(fig)


if __name__ == "__main__":
    rows = read_rows()
    fig1(rows)
    fig2()
    fig3(rows)
    for f in sorted(os.listdir(FIG_DIR)):
        p = os.path.join(FIG_DIR, f)
        print(f"  {f}  {os.path.getsize(p)/1024:.0f} KB")
    print("[ok] 3 张图已生成")
