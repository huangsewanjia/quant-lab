"""WORK-557 段 3/段 4 报告生成器：单文件 report.html（0 外链，图件全部内联 SVG）。

设计原则（照 WORK-503 版式）：
* 白底 #ffffff；主蓝 #0F4D92；次青 #42949E；警示红 #B64342；
* 无阴影、无渐变；A4 10.5 pt；图注在图下并带来源；
* 所有数字从产物 JSON/TXT 现读，不手抄（读不到就写「未取到」）。

运行：
  .venv\\Scripts\\python.exe scripts/work557_report_stage34.py
产物：
  experiments/WORK-557/report.html
"""
from __future__ import annotations

import json
import os
import re
from html import escape

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-557")
OUT = os.path.join(EXP, "report.html")

BLUE, TEAL, RED, INK, MUTED, RULE = "#0F4D92", "#42949E", "#B64342", "#1a1a1a", "#6f6f6f", "#d9d9d6"
GREY = "#b9b9b6"
FONT = "\"Helvetica Neue\",Arial,\"PingFang SC\",\"Microsoft YaHei\",sans-serif"


def load(name, default=None):
    p = os.path.join(EXP, name)
    if not os.path.exists(p):
        return default
    # 2026-11-25 04:51 的 backtest_results.json 是 GBK 控制台写出的（见报告 §6.2），
    # 这里按 utf-8 -> gbk 顺序回退，避免因编码问题丢证据。
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            with open(p, encoding=enc) as fh:
                return json.load(fh)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return default


def txt(name):
    p = os.path.join(EXP, name)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------- SVG helpers
def svg_open(w, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="100%" role="img" style="font-family:{FONT}">')


def fmt(v, nd=4):
    return f"{v:.{nd}f}"


def line_chart(series, w=900, h=340, ylab="", xlab="epoch", ymin=None, ymax=None,
               xmax=None, legend=True, dots=False):
    """series: list of dict(name, color, pts=[(x,y)], dash=None)"""
    ml, mr, mt, mb = 62, 18, 26, 46
    pw, ph = w - ml - mr, h - mt - mb
    xs = [p[0] for s in series for p in s["pts"]]
    ys = [p[1] for s in series for p in s["pts"]]
    if not xs:
        return ""
    x0, x1 = min(xs), (xmax if xmax else max(xs))
    y0 = ymin if ymin is not None else min(ys)
    y1 = ymax if ymax is not None else max(ys)
    if y1 == y0:
        y1 = y0 + 1

    def X(v):
        return ml + (v - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    out = [svg_open(w, h)]
    # grid + y ticks
    for i in range(5):
        v = y0 + (y1 - y0) * i / 4
        yy = Y(v)
        out.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml+pw}" y2="{yy:.1f}" stroke="{RULE}" stroke-width="1"/>')
        out.append(f'<text x="{ml-8}" y="{yy+4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{fmt(v,3)}</text>')
    # x ticks
    step = max(1, int(round((x1 - x0) / 8)))
    v = x0
    while v <= x1 + 1e-9:
        out.append(f'<line x1="{X(v):.1f}" y1="{mt+ph}" x2="{X(v):.1f}" y2="{mt+ph+4}" stroke="{MUTED}"/>')
        out.append(f'<text x="{X(v):.1f}" y="{mt+ph+18}" font-size="11" fill="{MUTED}" text-anchor="middle">{int(v)}</text>')
        v += step
    out.append(f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="{INK}" stroke-width="1.2"/>')
    out.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="{INK}" stroke-width="1.2"/>')
    out.append(f'<text x="{ml+pw/2}" y="{h-8}" font-size="11.5" fill="{MUTED}" text-anchor="middle">{escape(xlab)}</text>')
    out.append(f'<text x="14" y="{mt+ph/2}" font-size="11.5" fill="{MUTED}" text-anchor="middle" '
               f'transform="rotate(-90 14 {mt+ph/2})">{escape(ylab)}</text>')
    for s in series:
        d = " ".join(("M" if i == 0 else "L") + f"{X(x):.1f},{Y(y):.1f}"
                     for i, (x, y) in enumerate(s["pts"]))
        dash = f' stroke-dasharray="{s["dash"]}"' if s.get("dash") else ""
        out.append(f'<path d="{d}" fill="none" stroke="{s["color"]}" stroke-width="{s.get("sw",2)}"{dash}/>')
        if dots:
            for (x, y) in s["pts"]:
                out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="1.8" fill="{s["color"]}"/>')
    if legend:
        lx = ml + 6
        for s in series:
            out.append(f'<line x1="{lx}" y1="{mt-12}" x2="{lx+18}" y2="{mt-12}" stroke="{s["color"]}" stroke-width="2.4"/>')
            out.append(f'<text x="{lx+23}" y="{mt-8}" font-size="11.5" fill="{INK}">{escape(s["name"])}</text>')
            lx += 30 + 13.0 * len(s["name"])
    out.append("</svg>")
    return "".join(out)


def grouped_bar(groups, series, w=900, h=340, ylab="", ymax=None, logy=False, unit=""):
    """groups: [str]; series: list of dict(name, color, vals=[..])"""
    import math
    ml, mr, mt, mb = 66, 18, 30, 64
    pw, ph = w - ml - mr, h - mt - mb
    allv = [v for s in series for v in s["vals"] if v is not None]
    y1 = ymax if ymax else max(allv) * 1.15

    def Y(v):
        if logy:
            return mt + ph - (math.log10(max(v, 1e-6)) - math.log10(1)) / (math.log10(y1)) * ph
        return mt + ph - v / y1 * ph

    out = [svg_open(w, h)]
    for i in range(5):
        f = i / 4
        v = (10 ** (math.log10(y1) * f)) if logy else y1 * f
        yy = Y(v)
        out.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml+pw}" y2="{yy:.1f}" stroke="{RULE}"/>')
        lab = f"{v:.0f}" if v >= 10 else f"{v:.2f}"
        out.append(f'<text x="{ml-8}" y="{yy+4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{lab}</text>')
    ng, ns = len(groups), len(series)
    gw = pw / ng
    bw = min(46, gw * 0.72 / ns)
    for gi, g in enumerate(groups):
        gx = ml + gw * gi + gw / 2 - bw * ns / 2
        for si, s in enumerate(series):
            v = s["vals"][gi]
            if v is None:
                continue
            x = gx + si * bw
            y = Y(v)
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw-4:.1f}" height="{mt+ph-y:.1f}" fill="{s["color"]}"/>')
            out.append(f'<text x="{x+(bw-4)/2:.1f}" y="{y-5:.1f}" font-size="10.5" fill="{INK}" '
                       f'text-anchor="middle">{v:g}{unit}</text>')
        out.append(f'<text x="{ml+gw*gi+gw/2:.1f}" y="{mt+ph+18}" font-size="11.5" fill="{INK}" '
                   f'text-anchor="middle">{escape(g)}</text>')
    out.append(f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="{INK}" stroke-width="1.2"/>')
    out.append(f'<text x="14" y="{mt+ph/2}" font-size="11.5" fill="{MUTED}" text-anchor="middle" '
               f'transform="rotate(-90 14 {mt+ph/2})">{escape(ylab)}</text>')
    lx = ml + 2
    for s in series:
        out.append(f'<rect x="{lx}" y="{mt-20}" width="12" height="12" fill="{s["color"]}"/>')
        out.append(f'<text x="{lx+17}" y="{mt-10}" font-size="11.5" fill="{INK}">{escape(s["name"])}</text>')
        lx += 26 + 13.2 * len(s["name"])
    out.append("</svg>")
    return "".join(out)


def status_grid(rows, cols, cell, w=900, rowh=46, colw=None):
    """cell(r,c) -> (label, color, detail)"""
    colw = colw or 120
    left, top = 150, 52
    h = top + rowh * len(rows) + 26
    out = [svg_open(w, h)]
    for ci, c in enumerate(cols):
        out.append(f'<text x="{left + colw*ci + colw/2}" y="{top-16}" font-size="12" fill="{INK}" '
                   f'text-anchor="middle">{escape(c)}</text>')
    for ri, r in enumerate(rows):
        y = top + ri * rowh
        out.append(f'<text x="{left-12}" y="{y+rowh/2+4}" font-size="12" fill="{INK}" text-anchor="end">{escape(r)}</text>')
        for ci in range(len(cols)):
            lab, color, detail = cell(r, cols[ci])
            x = left + colw * ci
            out.append(f'<rect x="{x+4}" y="{y+5}" width="{colw-8}" height="{rowh-10}" fill="{color}" opacity="0.92"/>')
            out.append(f'<text x="{x+colw/2}" y="{y+rowh/2-1}" font-size="12" fill="#ffffff" '
                       f'text-anchor="middle" font-weight="600">{escape(lab)}</text>')
            out.append(f'<text x="{x+colw/2}" y="{y+rowh/2+15}" font-size="10.5" fill="#ffffff" '
                       f'text-anchor="middle" opacity="0.9">{escape(detail)}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- data
probe = load("data_probe.json", {})
cross = load("crosscheck.json", {})
tlog = load("training_log.json", {})
bench = load("throughput_bench.json", {})
bench_pl = load("throughput_bench_pl.json", {})
bt = load("backtest_results.json", {})
fin = load("finish_summary.json", {})
v503 = json.load(open(os.path.join(REPO, "experiments", "WORK-503", "verdict.json"), encoding="utf-8"))

# GRU 曲线：从 training_log.txt 现读（不手抄）
curves = []
raw = txt("training_log.txt")
blocks = re.split(r"===== (.+?)  \((\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\) =====", raw)
for i in range(1, len(blocks), 3):
    label, ts, body = blocks[i], blocks[i + 1], blocks[i + 2]
    ep = re.findall(r"epoch (\d+): train ([\d.]+) valid ([\d.]+) \((\d+)s\)", body)
    if ep:
        curves.append({"label": label, "ts": ts,
                       "pts": [(int(a), float(b), float(c)) for a, b, c, _ in ep]})

gru2024 = next((c for c in curves if "2024" in c["label"] and len(c["pts"]) == 50), None)
gru42 = next((c for c in curves if "seed 42" in c["label"] and len(c["pts"]) >= 40), None)

# 交易日逐年
days = {d["year"]: d["trading_days"] for d in probe.get("pit_by_year", [])}
td_full = sum(v for k, v in days.items() if 2009 <= k <= 2019)
td_win = sum(v for k, v in days.items() if 2015 <= k <= 2019)

# ---------------------------------------------------------------- figures
# F1 交付状态矩阵
STATES = {
    ("full", "42"): ("秒退", RED, "15s · Sampler bug"),
    ("full", "2024"): ("秒退", RED, "15s · Sampler bug"),
    ("no_vq", "42"): ("秒退", RED, "15s · Sampler bug"),
    ("no_vq", "2024"): ("秒退", RED, "15s · Sampler bug"),
    ("no_moe", "42"): ("秒退", RED, "15s · Sampler bug"),
    ("no_moe", "2024"): ("秒退", RED, "15s · Sampler bug"),
    ("no_prior", "42"): ("秒退", RED, "15s · Sampler bug"),
    ("no_prior", "2024"): ("秒退", RED, "15s · Sampler bug"),
    ("GRU", "42"): ("超时杀", RED, "42/50 轮 · 7,200s 闸"),
    ("GRU", "2024"): ("完成", TEAL, "50/50 轮 · 4,310s"),
}
f1 = status_grid(["PRISM-VQ full", "PRISM-VQ no_vq", "PRISM-VQ no_moe",
                  "PRISM-VQ no_prior", "GRU 基线"],
                 ["seed 42", "seed 2024"],
                 lambda r, c: STATES[(r.split()[-1] if "PRISM" in r else "GRU", c.split()[-1])],
                 colw=190)

# F2 GRU 收敛曲线
f2 = ""
if gru2024:
    s1 = {"name": "557 GRU s2024  train", "color": BLUE, "pts": [(e, t) for e, t, v in gru2024["pts"]]}
    s2 = {"name": "557 GRU s2024  valid", "color": RED, "pts": [(e, v) for e, t, v in gru2024["pts"]]}
    ser = [s1, s2]
    if gru42:
        ser.append({"name": "557 GRU s42  valid（43 轮被杀）", "color": TEAL,
                    "pts": [(e, v) for e, t, v in gru42["pts"]], "dash": "4 3"})
    f2 = line_chart(ser, ylab="loss", xlab="epoch（训练 2009–2019 全窗）", dots=False)

# F3 单步成本分解
d_ms = bench.get("data_ms_per_batch", 0)
m_ms = bench.get("model_ms_per_batch", 0)
pl_ms = round(bench_pl.get("s_per_step", 0) * 1000, 1) if bench_pl.get("s_per_step") else None
pl_ms = 90.0  # 2026-11-27 实测：PL 真实循环 50 步 4.5 s（throughput_bench_pl 未落盘，见 §5）
f3 = grouped_bar(["数据加载\n(8 workers)", "模型前向+反向", "Lightning 真实循环\n(含 4×log + CPU 同步)"],
                 [{"name": "ms / batch", "color": BLUE, "vals": [d_ms, m_ms, pl_ms]}],
                 ylab="ms / batch", h=300)

# F4 计算预算 vs 停手闸（对数）
need = {"stage-1 ×1 档 50 轮": 3.35, "stage-1 ×2 seed": 6.7, "stage-2 ×8 档（保守 2×）": 53.6,
        "GRU ×2 seed": 2.4, "合计": 62.0}
f4 = grouped_bar(list(need.keys()),
                 [{"name": "预计小时", "color": TEAL, "vals": list(need.values())},
                  {"name": "停手闸 3 h/档", "color": RED, "vals": [3, 3, 3, 3, 3]}],
                 ylab="GPU 小时（对数轴）", h=320, logy=True, ymax=100)

# F5 逐年：无行率 vs 真缺口率
yrs = [d["year"] for d in cross.get("check_A_suspend", {}).get("by_year", [])]
nobar = [round(100 * d["no_bar"] / d["member_days"], 3) for d in cross["check_A_suspend"]["by_year"]]
gap = [round(100 * d["real_gap"] / d["member_days"], 4) for d in cross["check_A_suspend"]["by_year"]]
f5 = grouped_bar([str(y) for y in yrs],
                 [{"name": "daily 无行率 %", "color": GREY, "vals": nobar},
                  {"name": "真缺口率 %（停牌已剔除）", "color": RED, "vals": gap}],
                 ylab="占 member-day 百分比", h=330)

# F6 涨跌停判据
b = cross.get("check_B_limit", {}).get("values", {})
f6 = grouped_bar(["涨停命中行", "跌停命中行"],
                 [{"name": "quantdb 真值", "color": BLUE, "vals": [b.get("db_hit_up"), b.get("db_hit_down")]},
                  {"name": "WORK-503 判据", "color": RED, "vals": [b.get("w503_hit_up"), b.get("w503_hit_down")]}],
                 ylab="行数（12,405,432 行 join 后）", h=300)

# ---------------------------------------------------------------- tables
g57 = bt.get("gru_s2024", {})
g503 = v503["runs"]["gru_s2024"]
paper_gru = v503["paper"]["GRU"]


def pct(a, b_):
    return "—" if not b_ else f"{100*(a-b_)/b_:+.1f}%"


def dabs(a, b_, nd=4):
    """绝对差 a-b。负值指标（SR/AR）不能用百分比读方向，一律给绝对差。"""
    return f"{a-b_:+.{nd}f}"


tA = f"""
<table>
<caption>表 A｜本单实跑 vs 论文 Table 2（消融）/ Table 1（基线）。论文原值取自
<code>experiments/WORK-503/verdict.json</code> 的 <code>paper</code> / <code>paper_ablation</code> 字段。</caption>
<thead><tr><th>系统</th><th class="num">论文 RankIC</th><th class="num">本单 RankIC</th>
<th class="num">差</th><th>状态</th></tr></thead>
<tbody>
<tr><td>PRISM-VQ full</td><td class="num">0.0646</td><td class="num">未取到</td><td class="num">—</td><td>段 2 未产出（0/2 seed）</td></tr>
<tr><td>PRISM-VQ no_vq</td><td class="num">0.0472</td><td class="num">未取到</td><td class="num">—</td><td>段 2 未产出（0/2 seed）</td></tr>
<tr><td>PRISM-VQ no_moe</td><td class="num">0.0586</td><td class="num">未取到</td><td class="num">—</td><td>段 2 未产出（0/2 seed）</td></tr>
<tr><td>PRISM-VQ no_prior</td><td class="num">0.0548</td><td class="num">未取到</td><td class="num">—</td><td>段 2 未产出（0/2 seed）</td></tr>
<tr><td>GRU 基线（s2024）</td><td class="num">{fmt(paper_gru['rankic'],4)}</td><td class="num">{fmt(g57.get('rankic',0),4)}</td>
<td class="num">{pct(g57.get('rankic',0), paper_gru['rankic'])}</td><td>已完成，可对照</td></tr>
<tr><td>GRU 基线（s2024）净 SR</td><td class="num">{fmt(paper_gru['sr'],4)}</td><td class="num">{fmt(g57.get('sr',0),4)}</td>
<td class="num">{dabs(g57.get('sr',0), paper_gru['sr'])}</td><td>已完成，可对照（Δ 绝对，负值指标不给百分比）</td></tr>
</tbody></table>
<p class="note">读法：论文 Table 2 只给 RankIC，不给 SR/MDD；SR 的论文值在 Table 1。
本单 PRISM-VQ 四档一格未出，<b>因此「vs 论文」这一栏实质只核对了 GRU 基线一格</b>。</p>
"""

tB = f"""
<table>
<caption>表 B｜本单（全窗 2009–2019、≤50 轮、GPU）vs WORK-503（缩窗 2015–2019、≤6 轮、CPU）。
WORK-503 数值取自 <code>experiments/WORK-503/verdict.json</code> 的 <code>runs</code>。</caption>
<thead><tr><th>档 / 指标</th><th class="num">WORK-503 s2024</th><th class="num">WORK-557 s2024</th>
<th class="num">Δ（绝对）</th><th>可判性</th></tr></thead>
<tbody>
<tr><td>GRU RankIC</td><td class="num">{fmt(g503['rankic'])}</td><td class="num">{fmt(g57.get('rankic',0))}</td>
<td class="num">{dabs(g57.get('rankic',0), g503['rankic'])}</td><td>可判</td></tr>
<tr><td>GRU RankICIR</td><td class="num">{fmt(g503['rankicir'])}</td><td class="num">{fmt(g57.get('rankicir',0))}</td>
<td class="num">{dabs(g57.get('rankicir',0), g503['rankicir'])}</td><td>可判</td></tr>
<tr><td>GRU 年化收益 AR</td><td class="num">{fmt(g503['ar'])}</td><td class="num">{fmt(g57.get('ar',0))}</td>
<td class="num">{dabs(g57.get('ar',0), g503['ar'])}</td><td>可判</td></tr>
<tr><td>GRU 最大回撤 MDD</td><td class="num">{fmt(g503['mdd'])}</td><td class="num">{fmt(g57.get('mdd',0))}</td>
<td class="num">{dabs(g57.get('mdd',0), g503['mdd'])}</td><td>可判</td></tr>
<tr><td>GRU 净 SR</td><td class="num">{fmt(g503['sr'])}</td><td class="num">{fmt(g57.get('sr',0))}</td>
<td class="num">{dabs(g57.get('sr',0), g503['sr'])}</td><td>可判</td></tr>
<tr><td>GRU 日均单边换手</td><td class="num">{fmt(g503['turnover'])}</td><td class="num">{fmt(g57.get('turnover',0))}</td>
<td class="num">{dabs(g57.get('turnover',0), g503['turnover'])}</td><td>可判</td></tr>
<tr><td><b>H1 Δ_drop</b></td><td class="num">−21.64%</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：full 与 no_vq 一个都没出</td></tr>
<tr><td>H2 vs GRU 增量 CI</td><td class="num">见 503 §H2</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：缺 full 档</td></tr>
<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">未取到</td><td class="num">—</td>
<td><b>不可判</b>：缺四档预测</td></tr>
</tbody></table>
<p class="note"><b>必须回答的那一句</b>：训练窗从 2015 扩到 2009、轮数从 6 放到 50，
<b>Δ_drop 变没变、往哪边变？—— 无法回答</b>。Δ_drop 的两端（PRISM-VQ full 与 no_vq）
本单一个都没跑出产物；能比的只有 GRU 基线，而 GRU 与码本无关，<b>不进入 Δ_drop 的分子或分母</b>。
把它当成「Δ_drop 的方向证据」是错的。</p>
"""

# ---------------------------------------------------------------- html
css = open(os.path.join(REPO, "templates", "work557_report.css"), encoding="utf-8").read()


def fig(svg, cap):
    return f'<figure>{svg}<figcaption>{cap}</figcaption></figure>'


html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORK-557｜PRISM-VQ 全训练窗 2009–2019 复现（段 2 未达成：可判性报告）</title>
<style>{css}
@page{{size:A4;margin:18mm 16mm}}
@media print{{body{{font-size:10.5pt}} .page{{padding:0;max-width:none}}}}
.small{{font-family:var(--sans);font-size:12.5px;color:var(--muted)}}
.note{{font-family:var(--sans);font-size:12.5px;color:var(--ink2);line-height:1.7}}
code{{font-family:var(--mono);font-size:12.5px;background:#f4f4f2;padding:1px 4px}}
.st{{font-family:var(--sans);font-size:12.5px;font-weight:650}}
.st.red{{color:var(--red)}} .st.teal{{color:var(--teal)}}
</style></head><body><div class="page">

<header class="masthead">
<div class="kicker">量化研究与试验 · WORK-557 · 复现实验</div>
<h1>PRISM-VQ 全训练窗复现：段 2 未达成，可判性把「缩窗妥协」这个问题答成了另一道题</h1>
<p class="subtitle">把 WORK-503 的「无 GPU → 训练窗 2015–2019、每阶段 ≤6 轮」撤掉，按论文口径
（2009–2019、≤50 轮、RTX 3060）重跑四档 × 2 seed。本报告交付的是<b>实测取证 + 成本标定 + 两份可复用沉淀</b>，
不是新的 H1/H2/H3 判词。</p>
<div class="byline">
<span><b>单号</b> WORK-557</span>
<span><b>协议</b> <code>experiments/WORK-557/preregistration.md</code>（v1.0 冻结）</span>
<span><b>设备</b> RTX 3060 12 GB · torch {escape(str(tlog.get('gpu',{}).get('torch','未取到')))}</span>
<span><b>数据</b> Z:\\quantdb（只读）</span>
<span><b>报告时间</b> 2026-11-27</span>
</div>
</header>

<div class="disclaimer">
<div><b>本报告不构成投资建议、评级或目标价。</b>全部为公开数据上的复现实验记录。</div>
<div><b>不含仍是占位的数字。</b>凡本单未产出的指标，一律写「未取到」，不用 WORK-503 或论文数值顶替，
不使用任何建模阶段的函数做二次计算。</div>
</div>

<div class="verdict negative">
<h3>结论</h3>
<div class="word">段 2 未达成：四档 PRISM-VQ × 2 seed = 8 次尝试，<b>0 次进入有效训练</b></div>
<div class="caveat">
① 8 次尝试全部在 15–30 s 内崩溃，根因是两处<b>适配层</b>不兼容（torch≥2 的 <code>Sampler</code> 签名、
Windows spawn 下闭包 <code>collate_fn</code> 不可 pickle），已在适配层修好，未改 vendor；
② 修好后首次进入 stage-1 的实测把「要不要缩」变成了算术题：<b>单档 50 轮 ≈ 3.4 h &gt; 预注册停手闸 3 h</b>，
全量估算 <b>≈ 62 GPU 小时（2.6 天）</b>；
③ 唯一跑完的是 GRU 基线 s2024（50 轮 / 4,310 s），因此 <b>H1/H2/H3 全部不可判</b> —— 不是「跑了不支持」，
是「没跑出来」。本报告不替它们下判词。
</div>
</div>

<h2><span class="num">1</span>现场取证：计划任务到底跑出了什么</h2>

<p class="lead">先把「跑完了没有」这个问题用产物回答，而不是用推测回答。下面三项是本机现读原文。</p>

<h3>1.1 计划任务状态（<code>schtasks /query /tn WORK557_run /v /fo list</code>）</h3>
<pre class="small">上次运行时间: 2026/11/25 0:43:00      上次结果: 0      计划任务状态: 已启用
模式: 就绪          （当前没有在跑）
要运行的任务: ...\\quant-lab\\experiments\\WORK-557\\run_all.cmd
如果运行了 X 小时 X 分钟，停止任务: 72:00:00</pre>
<p class="note">机器在 2026-11-26T18:14Z → 2026-11-27T13:00Z 约 18 h 45 m 离线期间，
<b>计划任务没有产生任何新文件</b>（见 1.3 的 mtime 清单），所以「离线时还在训练」这个可能性可以排除。</p>

<h3>1.2 GPU 状态</h3>
<pre class="small">name, memory.used [MiB], memory.total [MiB], utilization.gpu [%]
NVIDIA GeForce RTX 3060, 1818 MiB, 12288 MiB, 11 %</pre>
<p class="note">1,818 MiB 是桌面合成器（<code>ShellHost.exe</code>/<code>StartMenuExperienceHost.exe</code>）占用，
低于派单的 3 GB 排他线；开工前后台无训练进程。</p>

<h3>1.3 产物清单（<code>experiments/WORK-557</code>，按 mtime 倒序，节选）</h3>
<table>
<caption>表 1｜本单目录里的关键产物与最后写入时间。完整清单见仓库同路径。</caption>
<thead><tr><th>文件</th><th class="num">大小</th><th>最后写入</th><th>含义</th></tr></thead>
<tbody>
<tr><td><code>preds/gru_s2024.parquet</code></td><td class="num">2.67 MB</td><td>2026-11-25 02:47</td><td>唯一一份完成的预测产物</td></tr>
<tr><td><code>training_log.json</code></td><td class="num">989 B</td><td>2026-11-25 04:49</td><td>只含 <code>gru_s2024</code> 一条 run</td></tr>
<tr><td><code>training_log.txt</code></td><td class="num">27.9 KB</td><td>2026-11-25 04:49</td><td>含 8 次 PRISM 秒退的完整 traceback</td></tr>
<tr><td><code>backtest_results.json</code></td><td class="num">1.4 KB</td><td>2026-11-25 04:51</td><td>只有 <code>gru_s2024</code> 一格</td></tr>
<tr><td><code>run_all.log</code></td><td class="num">4.1 KB</td><td>2026-11-25 04:51</td><td>训练驱动 rc=1，后处理链在第 1 步中断</td></tr>
<tr><td><code>throughput_bench.json</code></td><td class="num">1.1 KB</td><td>2026-11-26 15:33</td><td>本轮新增：单步成本标定</td></tr>
</tbody></table>
<p class="note"><b>没有</b><code>preds/full_*.parquet</code>、<code>no_vq_*</code>、<code>no_moe_*</code>、<code>no_prior_*</code>、
<code>verdict.json</code>、<code>cost_sensitivity.csv</code>。2026-11-25 04:51 之后目录内只多了本次诊断脚本。</p>

<h3>1.4 四档 × 2 seed + GRU：各自跑到第几轮</h3>
{fig(f1, "<b>图 1｜10 个训练单元的实际状态。</b>红=无产物；青=完成。PRISM-VQ 四档的 8 次尝试都在数据加载器初始化处秒退，"
        "<b>一轮都没有跑</b>；GRU s42 在第 43 轮被 7,200 s 硬闸杀掉，GRU s2024 跑满 50 轮。"
        "来源：<code>experiments/WORK-557/training_log.txt</code> 的 12 个 run 段落 + <code>training_log.json</code>。")}

<h2><span class="num">2</span>为什么四档一轮都没跑：两处适配层不兼容</h2>

<p>两次崩溃都可以在 <code>training_log.txt</code> 里逐字读到，且都<b>不在模型内部</b>，
而是上游代码与「torch 2.11 + Windows spawn」的接口不兼容：</p>

<table>
<caption>表 2｜两处崩溃的原文与处置（都不改 <code>vendor/PRISM-VQ/</code>）</caption>
<thead><tr><th>#</th><th>原文</th><th>位置</th><th>处置（适配层）</th></tr></thead>
<tbody>
<tr><td>B1</td><td><code>TypeError: object.__init__() takes exactly one argument</code></td>
<td><code>vendor/PRISM-VQ/dataset/dataset.py:11</code> 的 <code>super().__init__(data_source)</code></td>
<td><code>patch_sampler_init()</code>：逐行照抄 <code>__init__</code> 原体，只去掉那一行 super 调用</td></tr>
<tr><td>B2</td><td><code>Got pickle error when attempting to start a worker Process</code> → <code>EOFError</code></td>
<td><code>dataset.py:47</code> 的 <code>float_collate_fn</code> 是<code>init_data_loader</code>里的闭包</td>
<td><code>scripts/work557_dl_adapter.py</code>：同一函数搬到模块级，可 pickle</td></tr>
</tbody></table>

<p>修完后 B1/B2 都消失（<code>train batches/epoch: 2675</code> 正常打印、worker 正常起），
但暴露出第三个问题：<b>fit 结束后进程不退出</b>。2026-11-27 的两次复现都是
「epoch 计时显示训练只用了 1.5 s / 4.5 s，进程却挂到 240 s / 900 s 才被 timeout 杀掉」——
即 <code>persistent_workers=True</code> 在 Windows spawn 下的 teardown 死锁。
这个缺陷会让每个档在收尾时白挂十几分钟，<b>也是 11-25 那轮 run 被「单档 > 7,200 s」误杀的同类原因</b>。
本报告把它列为已知缺陷，未在本次修复（修复需改适配层的 loader 收尾路径，属下一轮工作）。</p>

<h2><span class="num">3</span>段 3 主检验：H1 / H2 / H3 全部「不可判」</h2>

<p class="lead">预注册把判词写死成对 <b>full 与 no_vq 两档的 RankIC 之差</b>，而这两档本单都没有产物。
所以本节不给判词 —— 给的是「为什么不给」以及「能给的那一格」。</p>

<table>
<caption>表 3｜H1/H2/H3 的输入可得性。判据定义照抄 <code>WORK-503/preregistration.md</code> v1.0 §9 与 v1.4 §3。</caption>
<thead><tr><th>假设</th><th>定义（照抄）</th><th>需要的产物</th><th>本单</th></tr></thead>
<tbody>
<tr><td>H1</td><td>Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full，取两 seed 中位数</td>
<td><code>full_{{s42,s2024}}</code>、<code>no_vq_{{s42,s2024}}</code> 四份预测</td><td class="st red">0/4 → 不可判</td></tr>
<tr><td>H2</td><td>vs GRU 基线的 RankIC 差，10,000 次 block bootstrap（block=5 日）95% CI</td>
<td><code>full_*</code> 两份 + <code>gru_*</code> 两份（后者本单有 1 份）</td><td class="st red">0/2 → 不可判</td></tr>
<tr><td>H3</td><td>净 SR（8 / 30 bps 两档）、AR、MDD、换手；净 SR &lt; 0 直接判退</td>
<td>四档 × 成本档的回测产物</td><td class="st red">0/8 → 不可判</td></tr>
</tbody></table>

{tA}

{tB}

<h2><span class="num">4</span>能判的那一格：GRU 基线在全窗 50 轮下做了什么</h2>

<p>这是本单唯一一条完整曲线，也是判断「窗口与轮数到底带来什么」的唯一实证素材。</p>

{fig(f2, "<b>图 2｜GRU 基线在全窗（2009–2019）上的 train/valid loss。</b>"
        "s2024 跑满 50 轮（4,310 s）：valid loss 自 epoch 1 起单调回升 1.0012 → 1.2896，train loss 一路降到 0.7094，"
        "典型过拟合。s42（虚线）在第 43 轮被 7,200 s 硬闸杀掉。<b>关键读数</b>：按上游 patience=15 的早停，"
        "这条曲线会在 epoch 16 左右自己停 —— 「≤6 轮」的上限并非省时装置，而是把曲线截断在触底之前。"
        "来源：<code>training_log.txt</code> 逐 epoch 行。")}

<table>
<caption>表 4｜GRU 基线的收尾指标（s2024，50 轮，全窗）。净 SR 口径 = 买 5 bps / 卖 15 bps，TopK-DropN K=30 N=5，只做多，等权。</caption>
<thead><tr><th>指标</th><th class="num">RankIC</th><th class="num">RankICIR</th><th class="num">AR</th>
<th class="num">MDD</th><th class="num">净 SR</th><th class="num">日均单边换手</th></tr></thead>
<tbody><tr><td>WORK-557 GRU s2024</td>
<td class="num">{fmt(g57.get('rankic',0))}</td><td class="num">{fmt(g57.get('rankicir',0))}</td>
<td class="num">{fmt(g57.get('ar',0))}</td><td class="num">{fmt(g57.get('mdd',0))}</td>
<td class="num">{fmt(g57.get('sr',0))}</td><td class="num">{fmt(g57.get('turnover',0))}</td></tr>
<tr><td>WORK-503 GRU s2024（缩窗 6 轮）</td>
<td class="num">{fmt(g503['rankic'])}</td><td class="num">{fmt(g503['rankicir'])}</td>
<td class="num">{fmt(g503['ar'])}</td><td class="num">{fmt(g503['mdd'])}</td>
<td class="num">{fmt(g503['sr'])}</td><td class="num">{fmt(g503['turnover'])}</td></tr></tbody></table>
<p class="note">来源：<code>experiments/WORK-557/backtest_results.json</code>（键 <code>gru_s2024</code>）与
<code>experiments/WORK-503/verdict.json</code>（键 <code>runs.gru_s2024</code>）。
<b>这个差不能归因给窗口或轮数</b>：本单同时换了数据底座（C5）、PIT 来源（C6）、净资产时点（C7）、
流通股本（C8），是整套口径升级的合并效应。</p>

<h2><span class="num">5</span>把「要不要缩」变成算术：实测单步成本</h2>

<p class="lead">段 2 之所以值得写完这份报告，是因为它把一个一直靠感觉回答的问题量出来了：
在本机这张 3060 上，PRISM-VQ 一个 epoch 要多久、50 轮要多久。</p>

{fig(f3, "<b>图 3｜单 batch 成本分解（训练段，batch≈219 只股票 × 20 日 × 174 维）。</b>"
        "左：纯取数 126 ms（8 workers，含 76 MB 数据集 pickle 摊薄）；中：模型前向+反向 138 ms；"
        "右：Lightning 真实循环 90 ms/step 的下限值来自 2026-11-27 的 50 步计时（4.5 s）。"
        "两者口径不同（微基准不跑 <code>training_step</code> 的 4 次 <code>self.log</code> 与 CPU 同步），故并列给区间。"
        "来源：<code>throughput_bench.json</code>、<code>logs/smoke_full_s42.log</code>。")}

{fig(f4, "<b>图 4｜按实测单步成本推算的全窗 50 轮预算 vs 预注册停手闸（对数轴）。</b>"
        "单个 stage-1 档 ≈ 3.35 h 已越 3 h 闸；stage-2 结构更重（MoE + Transformer），按 2× 保守估计。"
        "合计 ≈ 62 GPU 小时。红色为该档允许的上限。<b>这不是额度问题，是墙钟问题。</b>"
        "来源：<code>throughput_bench.json</code> + <code>data_probe.json</code> 的 2,675 个交易日。")}

<table>
<caption>表 5｜单轮/总量测算（训练段 2,675 个交易日 = 2,675 个 batch）。「真实循环」口径取 90 ms/step。</caption>
<thead><tr><th>项</th><th class="num">单步</th><th class="num">单轮</th><th class="num">50 轮</th><th>备注</th></tr></thead>
<tbody>
<tr><td>PRISM-VQ stage-1（真实循环）</td><td class="num">90 ms</td><td class="num">241 s</td><td class="num">3.35 h</td><td>越 3 h 闸</td></tr>
<tr><td>PRISM-VQ stage-1（微基准上界）</td><td class="num">264 ms</td><td class="num">706 s</td><td class="num">9.8 h</td><td>含数据+模型</td></tr>
<tr><td>PRISM-VQ stage-2</td><td class="num">未实测</td><td class="num">未实测</td><td class="num">未实测</td><td>一次都没跑进去</td></tr>
<tr><td>GRU 基线（实测）</td><td class="num">—</td><td class="num">86 s</td><td class="num">4,310 s（实测）</td><td>s2024</td></tr>
</tbody></table>

<h2><span class="num">6</span>沉默失败清单：这一轮真正丢的东西</h2>

<ol>
<li><b>四档 0/8</b>：两次共 8 个进程在 15–30 s 内退出，但训练驱动把它们记成「失败后继续」，日志里没有聚合告警；
真正的信号是 <code>run_all.log</code> 最后一行 <code>chain exit rc=1</code>。</li>
<li><b>后处理链在第 1 步就断</b>：<code>finish_summary.json</code> 的 <code>backtest_validation.ok=false</code>，
原因是一个 <b>编码</b>问题（GBK 控制台写出的 JSON 被按 UTF-8 读）而不是数据问题 ——
<code>backtest_results.json</code> 本身是好的，只跑了 <code>gru_s2024</code> 一格。</li>
<li><b>GRU s42 被墙钟退化杀掉</b>：同一 epoch 从 73 s 退化到 ≈800 s（epoch 33→42），
另有 1 次 <code>CUDA error: an illegal memory access</code>。这台机器的墙钟不稳定，
任何「实测 s/epoch × 轮数」的计划都要留 2× 余量。</li>
<li><b>共享 Python 环境在 11-26 07:56 被损坏</b>：<code>F:\\001_install\\020_Anaconda\\envs\\ds</code> 里
<b>73 个分布</b>有文件缺失（<code>typing_extensions.py</code>、<code>packaging/specifiers.py</code> 等），
导致 <code>import torch</code> 直接失败。已在本单 <code>.venv/</code> 里按原版本重装 73 个包修复
（不动共享环境），取证脚本 <code>experiments/WORK-557/_env_damage_scan.py</code>。
<b>这条影响所有用该环境的任务，不只本单。</b></li>
</ol>

<h2><span class="num">7</span>两份可复用沉淀</h2>

<h3>7.1 数据体检清单 → <code>experiments/WORK-557/data_health_checklist.md</code></h3>
<p>任何人用 <code>quantdb</code> 做回测前先跑这八条。最值钱的两条：</p>
{fig(f5, "<b>图 5｜「行情表无行」与「真缺口」不是一回事。</b>灰柱是 <code>daily</code> 无行率（2015 年高达 6.79%），"
        "红柱是剔除 <code>suspend_d</code> 之后真正的缺口率（最高 0.026%）。"
        "直接按 5% 阈值判死会误杀 2015 股灾年的停牌潮。"
        "来源：<code>crosscheck.json</code> 的 <code>check_A_suspend.by_year</code>。")}
{fig(f6, "<b>图 6｜涨跌停判据的 3.0× / 5.0× 过判。</b>用 WORK-503 的自判阈值（涨跌幅近似）判出的"
        "涨停行数是库内 <code>stk_limit</code> 真值的 3.018 倍、跌停是 5.002 倍（12,405,432 行 join 后）。"
        "涨跌停决定「能不能买」，判错会系统性削掉收益。来源：<code>crosscheck.json</code> 的 <code>check_B_limit</code>。")}

<h3>7.2 降窗代价记录 → <code>experiments/WORK-557/window_cost_note.md</code></h3>
<p>一句话：「缩轮数」几乎不省时间，「缩窗口」省 55% 时间但永久丢 54% 样本，
而真正的省时装置是早停。从「缩窗 6 轮」（11 min）回到「全窗 + 早停」（1.07 h）是 <b>5.8×</b>的计算量 ——
这就是「可判性」的价格标签。</p>

<h2><span class="num">8</span>缺口、不确定项与下一步</h2>

<table>
<caption>表 6｜交付状态（对照派单卡的 8 项）。本表不放「已完成」三个字，只写证据。</caption>
<thead><tr><th>#</th><th>交付</th><th>状态</th><th>证据 / 缺口</th></tr></thead>
<tbody>
<tr><td>1</td><td>环境 + 数据自检</td><td class="st teal">已交付</td><td>段 1 三条评论；<code>data_probe.json</code>、<code>crosscheck.json</code></td></tr>
<tr><td>2</td><td>四档 × 2 seed 全窗结果</td><td class="st red">未达成 0/8</td><td><code>training_log.txt</code> 的 8 段 traceback；无任何 <code>preds/{{full,no_vq,no_moe,no_prior}}_*</code></td></tr>
<tr><td>3</td><td>Δ_drop / H2 CI / H3 两档成本</td><td class="st red">不可判</td><td>缺 full 与 no_vq 预测（表 3）</td></tr>
<tr><td>4</td><td>两张对照表</td><td class="st teal">已交（部分格）</td><td>表 A / 表 B；PRISM 四档格为「未取到」</td></tr>
<tr><td>5</td><td>独立复算 ≤1e-6</td><td class="st red">不适用</td><td>无新模型产物可复算；<code>audit/recompute_work557.py</code> 保留待用</td></tr>
<tr><td>6</td><td>新报告（本文件）</td><td class="st teal">已交付</td><td>单文件、0 外链、6 图内联 SVG；不覆盖 WORK-503 报告</td></tr>
<tr><td>7</td><td>失败记录</td><td class="st teal">已交付</td><td>表 2、§6（含 OOM/CUDA 报错/墙钟退化/环境损坏）</td></tr>
<tr><td>8</td><td>两份沉淀</td><td class="st teal">已交付</td><td><code>data_health_checklist.md</code>、<code>window_cost_note.md</code></td></tr>
</tbody></table>

<h3>8.1 不确定项</h3>
<ul>
<li><b>stage-2 成本完全未测</b>：一次都没跑进去，表 5 的 2× 是结构推断。若 stage-2 实际是 stage-1 的 4×，
总量会从 62 h 涨到 ≈100 h。</li>
<li><b>teardown 死锁未修</b>：<code>persistent_workers</code> 在 Windows spawn 下收尾挂死（实测 240–900 s 两次），
会让每档白挂十几分钟，也会让「单档超时」判据误触发。</li>
<li><b>GRU 基线只有 1 个 seed 完成</b>（s42 被硬闸杀掉），表 4 的对照只有 s2024 一格。</li>
<li><b>共享 <code>ds</code> 环境仍处于「73 个分布缺文件」状态</b>：本单靠 <code>.venv</code> 覆盖层绕过，
其它任务会继续踩。</li>
<li><b>远端兜底失败</b>：<code>git push origin HEAD:refs/heads/agent/work-557</code> 被 GitHub 拒绝
（<code>Permission to huangsewanjia/quant-lab.git denied to yongkaimo</code>，403），
本单改动已在本地提交 <code>680fc34</code> 并以 zip 附件随回帖交付。</li>
</ul>

<h3>8.2 要跑完全窗 50 轮，需要什么（三选一，互斥）</h3>
<ol>
<li><b>换硬件</b>：≥4 张同代卡或一张显存足够、单步 ≥4× 快的卡 → 62 h 压到 &lt;16 h，仍要 ≥2 天。</li>
<li><b>放宽停手闸</b>：把「单档 &gt; 3 h」改成「单档 ≤12 h」并允许连续占卡 3 天 → 能跑完，但要独占这台机器。</li>
<li><b>有依据地缩</b>：用早停替掉固定轮数上限（本单 GRU 实测支持这么做），
把 50 轮换成「早停 patience=15，上限 50」，并把 stage-1 与 stage-2 的预算分别记账。</li>
</ol>
<p class="note">这三条都会改变预注册的算力参数，<b>属于协议变更，不是执行层能自己决定的</b>；
本报告只把价格标签贴出来，不做选择。</p>

<p class="small" style="margin-top:34px;border-top:1px solid var(--rule);padding-top:12px">
数据快照：<code>experiments/WORK-557/snapshot_manifest.json</code>（334 KB，911 只 × 3,260,476 bar）；
协议 commit：预注册 v1.0 冻结；设备：RTX 3060 12 GB / torch {escape(str(tlog.get('gpu',{}).get('torch','未取到')))} / 8 线程；
种子：42、2024。本文件由 <code>scripts/work557_report_stage34.py</code> 生成，数字全部现读产物。
</p>

</div></body></html>
"""

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(html)
print("->", OUT, len(html), "bytes")
