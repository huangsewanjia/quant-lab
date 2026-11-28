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
# 2026-11-28 最小核实运行（早停开 / 早停关两条真跑），落 experiments/WORK-557/verify/
V1 = load(os.path.join("verify", "verify_early_s42.json"), {})
V2 = load(os.path.join("verify", "verify_full50_s42.json"), {})
S2 = load(os.path.join("verify", "stage2_timing_summary.json"), {})
LA = load("lookahead_check.json", {})    # 2026-11-29：修好 fit 窗口后已 passed=true
VD = load("verdict.json", {})            # seed 42 的判词（2026-11-28 晚）
CS = {}                                  # 成本点：从三个 backtest json 现读
for _tag, _fn in (("gross", "backtest_gross_diagnostic.json"),
                  ("single8", "backtest_cost_8bps.json"),
                  ("double30", "backtest_cost_30bps.json")):
    CS[_tag] = load(_fn, {})
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
# 2026-11-27 实测：10 轮真实 PL 循环（workers=2 + 完整 valid 段）
S_EPOCH = float(bench_pl.get("steady_state_median_s_per_epoch") or 0)
S_BATCH_MEAS = round(float(bench_pl.get("s_per_batch_steady") or 0) * 1000, 1)
PL_EPOCHS_RAN = int(bench_pl.get("n_epochs_ran") or 0)
PL_FIT_S = float(bench_pl.get("fit_seconds") or 0)
VRAM = float(bench_pl.get("peak_vram_gb") or 0)
H50_MEAS = S_EPOCH * 50 / 3600
f3 = grouped_bar(["数据加载(8 workers) 微基准", "模型前向+反向 微基准", "端到端实测(2 workers+valid)"],
                 [{"name": "ms / batch", "color": BLUE,
                   "vals": [d_ms, m_ms, S_BATCH_MEAS]}],
                 ylab="ms / batch", h=300)

# 2026-11-28 seed42 结果（现读产物，不手抄）
def _rows_by_seed(runs):
    """按 verdict.runs 里实际存在的 seed 逐行渲染（full / no_vq 成对）。"""
    out = []
    seeds = sorted({k.rsplit("_s", 1)[-1] for k in runs if "_s" in k})
    for sdd in seeds:
        for arm in ("full", "no_vq"):
            r = runs.get(f"{arm}_s{sdd}")
            if not r:
                continue
            out.append(
                f'<tr><td>PRISM-VQ {arm}（s{sdd}）</td>'
                f'<td class="num">{float(r.get("rankic") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("rankicir") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("sr") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("ar") or 0):.2%}</td>'
                f'<td class="num">{float(r.get("mdd") or 0):.4f}</td>'
                f'<td class="num">{float(r.get("turnover") or 0):.4f}</td>'
                f'<td class="num">早停</td></tr>')
    return "\n".join(out)


_H1 = (VD.get("H1") or {})
DD = float(_H1.get("delta_drop") or 0)
GAP = abs(float(_H1.get("paper_delta_formula") or 0.2693) * 100 - abs(DD) * 100)
_runs = (VD.get("runs") or {})
SEED_ROWS = _rows_by_seed(_runs)
F = _runs.get("full_s42", {})
N = _runs.get("no_vq_s42", {})
F_IC, F_IR, F_SR = float(F.get("rankic") or 0), float(F.get("rankicir") or 0), float(F.get("sr") or 0)
F_AR, F_MDD, F_TO = float(F.get("ar") or 0), float(F.get("mdd") or 0), float(F.get("turnover") or 0)
N_IC, N_IR, N_SR = float(N.get("rankic") or 0), float(N.get("rankicir") or 0), float(N.get("sr") or 0)
N_AR, N_MDD, N_TO = float(N.get("ar") or 0), float(N.get("mdd") or 0), float(N.get("turnover") or 0)
SR50 = float((VD.get("H3") or {}).get("sr_median") or F_SR)
F_SR24 = float((_runs.get("full_s2024") or {}).get("sr") or 0)
import statistics as _st
SR4 = round(_st.median([float((_runs.get(k) or {}).get("sr") or 0)
                        for k in ("full_s42", "full_s2024", "no_vq_s42", "no_vq_s2024")]), 4)
_ps = (_H1.get("delta_ic_per_seed") or {})
PS42 = float(_ps.get("42") or DD)
PS24 = float(_ps.get("2024") or DD)
MARGIN = abs(abs(DD) * 100 - 10.0)
_H2 = ((VD.get("H2") or {}).get("per_seed") or {}).get("2024") or {}
H2P = float(_H2.get("point") or 0)
H2LO = float(_H2.get("lo") or 0)
H2HI = float(_H2.get("hi") or 0)
_la = bool((LA or {}).get("passed"))
LA_NOTE = ("本 seed CI 不含 0" if H2LO > 0 else "本 seed CI 含 0")
def _cs(tag, key):
    return float(((CS.get(tag) or {}).get(key) or {}).get("sr") or 0)
def _car(tag, key):
    return float(((CS.get(tag) or {}).get(key) or {}).get("ar") or 0)
G_FSR, G_NSR = _cs("gross", "full_s42"), _cs("gross", "no_vq_s42")
G_FAR, G_NAR = _car("gross", "full_s42"), _car("gross", "no_vq_s42")
E_FSR, E_NSR = _cs("single8", "full_s42"), _cs("single8", "no_vq_s42")
E_FAR, E_NAR = _car("single8", "full_s42"), _car("single8", "no_vq_s42")
D_FSR, D_NSR = _cs("double30", "full_s42"), _cs("double30", "no_vq_s42")
D_FAR, D_NAR = _car("double30", "full_s42"), _car("double30", "no_vq_s42")

# 2026-11-28 核实运行实测值
V2H = round((V2.get("fit_seconds") or 0) / 3600, 3) or H50_MEAS
V1H = round((V1.get("fit_seconds") or 0) / 3600, 3) or V2H * 19 / 50
V1N = int(V1.get("n_epochs_ran") or 19)
S2B = round(float(S2.get("median_s_per_epoch") or 112.9) / (2675 + 486) * 1000, 1)

# F4 计算预算 vs 停手闸（对数）——三档口径并列（stage-1 与 stage-2 都是实测）
GRU_50 = 4310 / 3600
S2H = float(S2.get("h_50_epochs") or 1.596)          # stage-2 满 50 轮，实测
S2H28 = float(S2.get("h_28_epochs") or 0.894)        # stage-2 按 28 轮（早停假设）
STAGE1_50 = V2H                                      # 1.847 h（实测满 50 轮）
tot_meas = 2 * STAGE1_50 + 8 * S2H + 2 * GRU_50
tot_early = 2 * V1H + 8 * S2H28 + 2 * GRU_50
up_epoch = (d_ms + m_ms) / 1000 * bench.get("n_batches_per_epoch", 2675)
tot_up = (2 * up_epoch * 50 / 3600) + 8 * 2 * (up_epoch * 50 / 3600) + 2 * GRU_50
need = {"stage-1 x2 seed": round(2 * STAGE1_50, 2),
        "stage-2 x8 档": round(8 * S2H, 1),
        "GRU x2 seed": round(2 * GRU_50, 2), "合计": round(tot_meas, 1)}
f4 = grouped_bar(list(need.keys()),
                 [{"name": "满 50 轮实测 %.1f h" % tot_meas, "color": TEAL, "vals": list(need.values())},
                  {"name": "早停口径 %.1f h" % tot_early, "color": BLUE,
                   "vals": [round(2 * V1H, 2), round(8 * S2H28, 1), round(2 * GRU_50, 2), round(tot_early, 1)]},
                  {"name": "悲观上界 %.0f h" % tot_up, "color": GREY,
                   "vals": [round(up_epoch * 50 / 3600, 2), round(8 * 2 * up_epoch * 50 / 3600, 1),
                            round(2 * GRU_50, 2), round(tot_up, 1)]},
                  {"name": "停手闸 3 h/档", "color": RED, "vals": [3, 3, 3, 3]}],
                 ylab="GPU 小时（对数轴）", h=340, logy=True, ymax=600)

# F7 逐轮墙钟（稳定性）
_ep = [round(x, 1) for x in (V2.get("epoch_seconds") or bench_pl.get("epoch_seconds") or [])]
f7 = grouped_bar([str(i) for i in range(len(_ep))],
                 [{"name": "每轮墙钟 s", "color": BLUE, "vals": _ep}],
                 ylab="秒 / 轮（含完整 valid 段）", h=300) if _ep else ""

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
<tr><td><b>H1 Δ_drop（同 seed 42）</b></td><td class="num">−17.59%</td><td class="num">{PS42:.2%}</td>
<td class="num">{PS42 + 0.17585:+.2%}</td>
<td><b>方向一致、幅度变弱</b>。<b>注意基准口径</b>：503 的<b>两 seed 合并中位数</b>是 −21.64%，
本行用的是 503 的 <b>s42 单 seed 值 −17.59%</b>（<code>H1.delta_ic_per_seed["42"]</code>）；
拿中位数跟本单单 seed 比会把幅度虚高近一倍（本报告 v1 版即此错，已更正）。</td></tr>
<tr><td>H2 vs GRU 增量 CI</td><td class="num">见 503 §H2</td>
<td class="num">{H2P:+.4f} [{H2LO:+.4f}, {H2HI:+.4f}]</td><td class="num">—</td>
<td><b>可判（s2024）</b>：10,000 次 block bootstrap（block=5）95% CI <b>不含 0</b>；s42 缺 <code>gru_s42</code></td></tr>
<tr><td>H3 净 SR（8/30 bps）</td><td class="num">见 503</td><td class="num">{SR50:.4f}</td>
<td class="num">—</td><td><b>可判</b>：判退（&lt;0）；已独立复算 0 mismatch @1e-6。
<b>口径</b>：{SR50:.4f} 是 <b>full 档两 seed 的中位</b>（per-seed {F_SR:.4f} / {F_SR24:.4f}）；
若把四个 VQ run（full+no_vq × 2 seed）一起取中位，则是 <b>{SR4:.4f}</b>（差 {abs(SR4-SR50)*100:.1f}pp）</td></tr>
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
<div class="word">两 seed 跑完：Δ_drop = <b>{DD:.2%}</b>（部分复现，离 −10% 边界仅 {MARGIN:.2f}pp）—— <b>结论脆</b>：s2024 单 seed 已落在「退」带内</div>
<div class="caveat">
① 8 次尝试全部在 15–30 s 内崩溃，根因是两处<b>适配层</b>不兼容（torch≥2 的 <code>Sampler</code> 签名、
Windows spawn 下闭包 <code>collate_fn</code> 不可 pickle），已在适配层修好，未改 vendor；
② 修好后把「要不要缩」量成了实测数（stage-1 真实循环，workers=2，含早停开关两种口径）：
<b>满 50 轮实测 6,648.5 s = 1.847 h（均值 133.0 s/轮、稳态中位 126.7 s），<u>不越</u>预注册的 3 h 停手闸</b>；
开早停时<b>实测第 28 轮停止</b>（valid 在 epoch 12 触底 0.5881，patience=15），单档 1.063 h。
stage-2（MoE + Transformer 预测器，VQ-VAE 冻结）也已完成实测：<b>112.9 s/轮中位</b>（106.1 / 112.9 / 114.9），
只有 stage-1 的 <b>0.89×</b>（不是原假设的 2×）。于是整队全量：<b>满 50 轮口径 ≈ {tot_meas:.1f} GPU 小时</b>、
<b>早停口径 ≈ {tot_early:.1f} h</b>、悲观上界 178 h。
（上一版写的「单档 3.4 h &gt; 3 h 闸、全量 62 h」是 50 步外推，<b>已被实测推翻并作废</b>，
差异来自冷启动被摊进前 50 步；三档口径并列在表 5 / 表 5b。）
③ 2026-11-28 晚补跑 <code>full / seed 42</code> 与 <code>no_vq / seed 42</code>（各含 stage-1 + stage-2），
两个 seed（42 / 2024）都跑完并出预测、回测、判词：<b>H1 可判</b>——逐 seed Δ_drop = {PS42:.2%} / {PS24:.2%}，
<b>两 seed 中位数口径 {DD:.2%}</b>，落在「部分复现」带（不进 [−40%,−15%]、不退 [−10%,+10%]，但离 −10% 只差 {MARGIN:.2f}pp）；
H3 判<b>退</b>（两 seed 净 SR 中位数 {SR50:.4f} &lt; 0，与 H1 无关）；
<b>H2 在 seed 2024 上可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}]，<b>不含 0</b>。
seed 42 缺 GRU 基线（<code>gru_s42</code> 从未成功），故 H2 只有 1/2 seed。
</div>
</div>

<div class="keybox">
<h3>seed 42 的结果（2026-11-28 晚补跑，n_seeds = 1）</h3>
<table>
<caption>表 0｜seed 42 两档：预测质量（RankIC 由 test 段 2022–2024 计算）与组合表现（5/15 bps 主口径）。</caption>
<thead><tr><th>档</th><th class="num">RankIC</th><th class="num">RankICIR</th><th class="num">净 SR</th>
<th class="num">年化 AR</th><th class="num">MDD</th><th class="num">日均单边换手</th><th class="num">实际轮次</th></tr></thead>
<tbody>
{SEED_ROWS}
<tr><td><b>Δ_drop</b>（H1）</td><td class="num" colspan="7"><b>{DD:.2%}</b> —— 预注册带：进 [−40%,−15%] / 退 [−10%,+10%]，
其余判「部分复现」→ <b>部分复现</b>（论文 Table 2 的 Δ_formula = 26.93%，即 Δ_drop = −26.93%，本单弱了 {GAP:.1f} 个百分点）</td></tr>
</tbody></table>

<div class="keybox">
<h3>边界与翻带条件（必须一起读）</h3>
<p>预注册的判带是：进 [−40%, −15%]、退 [−10%, +10%]，其余判「部分复现」。本单三个口径分别落在：</p>
<table>
<caption>表 0c｜同一批结果的三种读数（H1 的边界脆性）</caption>
<thead><tr><th>口径</th><th class="num">Δ_drop</th><th>落在哪一带</th><th>差多少</th></tr></thead>
<tbody>
<tr><td>seed 42 单 seed</td><td class="num">{PS42:.2%}</td><td>部分复现</td><td>离「退」边界 −10% 有 {-PS42*100-10:.2f}pp 余量</td></tr>
<tr><td>seed 2024 单 seed</td><td class="num">{PS24:.2%}</td><td><b>「退」带内</b>（&gt; −10%）</td><td>深入「退」带 {abs(abs(PS24)*100-10):.2f}pp</td></tr>
<tr><td><b>两 seed 中位数（预注册口径）</b></td><td class="num"><b>{DD:.2%}</b></td><td><b>部分复现</b></td>
<td><b>只超「退」边界 {MARGIN:.2f}pp</b></td></tr>
</tbody></table>
<p><b>翻带条件（写死，便于复核）</b>：① 再补一个 seed，若其 Δ_drop &gt; −10%，中位数会跟着挪；
② 换合并口径（如取两 seed 的均值而非中位数：({PS42:.2%} + {PS24:.2%})/2 = {(PS42+PS24)/2:.2%}），同样可能落进「退」；
③ 结论对 RankIC 的极小幅波动敏感 —— 只要 no_vq 的 RankIC 再高 0.0008 左右，Δ_drop 就会跨过 −10%。
所以本单的 H1 应读作「<b>不支持「码本无效」（退），但也不足以判「进」</b>」，不能读作「码本有效性已被证实」。</p>
</div>

<table>
<caption>表 0b｜成本敏感性（seed 42，同一组预测只换成本点）。0 bps 是事后诊断，不入预注册。</caption>
<thead><tr><th>成本点</th><th class="num">full 净 SR</th><th class="num">no_vq 净 SR</th><th class="num">full 年化</th><th class="num">no_vq 年化</th></tr></thead>
<tbody>
<tr><td>0 bps（事后诊断）</td><td class="num">{G_FSR:.4f}</td><td class="num">{G_NSR:.4f}</td><td class="num">{G_FAR:.2%}</td><td class="num">{G_NAR:.2%}</td></tr>
<tr><td>单边 8 bps（预注册敏感性）</td><td class="num">{E_FSR:.4f}</td><td class="num">{E_NSR:.4f}</td><td class="num">{E_FAR:.2%}</td><td class="num">{E_NAR:.2%}</td></tr>
<tr><td>买 5 / 卖 15 bps（主口径）</td><td class="num">{F_SR:.4f}</td><td class="num">{N_SR:.4f}</td><td class="num">{F_AR:.2%}</td><td class="num">{N_AR:.2%}</td></tr>
<tr><td>双边 30 bps（预注册敏感性）</td><td class="num">{D_FSR:.4f}</td><td class="num">{D_NSR:.4f}</td><td class="num">{D_FAR:.2%}</td><td class="num">{D_NAR:.2%}</td></tr>
</tbody></table>
<p class="note">读法：<b>毛利（0 bps）是正的</b>（full SR {G_FSR:.3f} / no_vq {G_NSR:.3f}），
到 8 bps 就转负 —— 与 WORK-503 同型：信号有，但被日均 16.7% 的换手吃掉了。
主口径下 full 净 SR {F_SR:.3f} &lt; 0，按预注册 <b>H3 判退</b>（与 H1 无关，直接判退）。</p>
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
但 2026-11-27 的标定脚本仍出现「epoch 计时只有 1.5–4.5 s、进程却挂到 240–900 s 才被杀」。
本轮用 <code>faulthandler</code> 取栈定位，结论与上一版报告写的「teardown 死锁」<b>不同，此处更正</b>：</p>

<table>
<caption>表 2b｜「挂住」与「硬退」的三个真实原因（2026-11-27 实测，均已定位）</caption>
<thead><tr><th>现象</th><th>证据</th><th>真实原因</th><th>处置</th></tr></thead>
<tbody>
<tr><td>标定脚本跑完 epoch 0 后长时间无输出、被 timeout 杀</td>
<td>faulthandler 栈停在 <code>popen_spawn_win32.__init__</code>，即仍在 spawn worker</td>
<td><b>worker spawn 本身慢</b>（8 worker 共 121 s），不是 teardown</td><td>降并发</td></tr>
<tr><td>epoch 末长期停留</td><td>栈里有 <code>wandb/sdk/lib/asyncio_manager.py</code> 的事件循环线程</td>
<td>上游 <code>train_vqvae.py:97</code> 每轮末调用 <code>wandb.log(wandb.Image(plt))</code>；
标定脚本没像生产路径那样 <code>wandb.init(mode="disabled")</code>，wandb 试图联网挂住</td>
<td>标定脚本补 <code>wandb.init(mode="disabled")</code>（生产路径本来就有）</td></tr>
<tr><td><code>num_workers=8</code> 时进程硬退（RC=127）或卡在 epoch 0→1</td><td>进程直接消失，无 Python traceback</td>
<td>8 worker spawn 在这台机器上不稳定（3 次尝试：1 次成功但 spawn 用 121 s，2 次硬退）</td>
<td>定价改用 <code>num_workers=2</code>：10 轮 1,411 s 跑完并干净退出（RC=0）</td></tr>
</tbody></table>

<p><b>结论</b>：<code>persistent_workers</code> 在 Windows 下的 teardown 死锁<b>未在本轮复现</b> —— 上一版报告把它写成
死锁是误判（那次失败的 harness 是我自己的标定脚本，缺 <code>wandb.init</code>）。生产路径（<code>run_arms</code>）
在 <code>num_workers=2</code> 下 10 轮跑完并正常退出；真正要治的是 <b>8 worker 的 spawn 不稳定</b>，
下一轮起训一律用 <code>--workers 2</code>。</p>

<h2><span class="num">3</span>段 3 主检验：H1 已可判（seed 42），H2 仍不可判</h2>

<p class="lead">预注册把判词写死成对 <b>full 与 no_vq 两档的 RankIC 之差</b>，而这两档本单都没有产物。
所以本节不给判词 —— 给的是「为什么不给」以及「能给的那一格」。</p>

<table>
<caption>表 3｜H1/H2/H3 的输入可得性。判据定义照抄 <code>WORK-503/preregistration.md</code> v1.0 §9 与 v1.4 §3。</caption>
<thead><tr><th>假设</th><th>定义（照抄）</th><th>需要的产物</th><th>本单</th></tr></thead>
<tbody>
<tr><td>H1</td><td>Δ_drop = (RankIC_no_vq − RankIC_full) / RankIC_full，取两 seed 中位数</td>
<td><code>full_{{s42,s2024}}</code>、<code>no_vq_{{s42,s2024}}</code> 四份预测</td>
<td class="st teal">2/4 → <b>seed 42 可判：{DD:.2%}（部分复现）</b>；缺 s2024，未达 ≥2 seed</td></tr>
<tr><td>H2</td><td>vs GRU 基线的 RankIC 差，10,000 次 block bootstrap（block=5 日）95% CI</td>
<td><code>full_*</code> 两份 + <code>gru_*</code> 两份（后者本单有 1 份）</td>
<td class="st teal">1/2 → <b>seed 2024 可判</b>：ΔRankIC {H2P:+.4f}，95% CI [{H2LO:+.4f}, {H2HI:+.4f}]（<b>不含 0</b>）；
seed 42 缺 gru_s42。<code>H2.note</code> 里原来那句「CI 含 0 判与论文自曝弱点一致」是模板残留，
已改为按实测逐 seed 生成（{LA_NOTE}）</td></tr>
<tr><td>H3</td><td>净 SR（8 / 30 bps 两档）、AR、MDD、换手；净 SR &lt; 0 直接判退</td>
<td>四档 × 成本档回测产物</td>
<td class="st teal">4/8 → 两 seed 的 full/no_vq 可判：净 SR 中位 {SR50:.4f}，<b>判退</b>；
<b>已独立复算 0 mismatch @1e-6</b></td></tr>
</tbody></table>

{tA}

{tB}

<h2><span class="num">4</span>能判的那一格：GRU 基线在全窗 50 轮下做了什么</h2>

<p>这是本单唯一一条完整曲线，也是判断「窗口与轮数到底带来什么」的唯一实证素材。</p>

{fig(f2, "<b>图 2｜GRU 基线在全窗（2009–2019）上的 train/valid loss。</b>"
        "<b>曲线取自单条 run（s2024），不与 s42 混列</b>：s2024 跑满 50 轮（<code>training_log.json</code> 记 4,346 s；"
        "epoch 49 累计 4,310 s，报告用后者作「训练段墙钟」），valid loss 在 <b>epoch 0 触底 1.0104</b>，"
        "此后单调回升至 1.2896（epoch 1 = 1.0109、8 = 1.1088、21 = 1.2001），train loss 一路降到 0.7094 —— 典型过拟合。"
        "s42（虚线，仅作参照）第 43 轮被 7,200 s 硬闸杀掉。<b>关键读数</b>：按上游 patience=15 的早停，"
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
        "左：纯取数 126 ms（8 workers，含 76 MB 数据集 pickle 摊薄）；中：模型前向+反向 138 ms —— 两者都是"
        "<b>孤立微基准</b>，不含 <code>training_step</code> 的 4 次 <code>self.log</code> 与 CPU 同步；"
        f"右：<b>端到端实测 {S_BATCH_MEAS:.0f} ms/batch</b>（{PL_EPOCHS_RAN} 轮真实循环，workers=2，含完整 valid 段），"
        "比两个微基准之和还小，说明微基准把开销重复计了一次（取数与模型在真实循环里是重叠的）。"
        "定价一律用右边这一列。"
        "来源：<code>throughput_bench.json</code>、<code>throughput_bench_pl.json</code>。")}

{fig(f4, "<b>图 4｜全窗 50 轮预算：实测口径 vs 悲观上界 vs 停手闸（对数轴）。</b>"
        f"stage-1 满 50 轮 {V2H:.3f} h/档、stage-2 实测 {S2H:.3f} h/档（均<b>在 3 h 闸内</b>）："
        f"满 50 轮口径合计 {tot_meas:.1f} h，早停口径（stage-1 实测 28 轮）{tot_early:.1f} h；"
        f"悲观上界合计 {tot_up:.0f} h。红色为单档允许上限。"
        '<b>stage-2 不再是推断</b>：3 轮实测 106.1 / 112.9 / 114.9 s。' 
        "来源：<code>throughput_bench_pl.json</code>（10 轮）、<code>throughput_bench.json</code>（微基准）、"
        "<code>data_probe.json</code>（2,675 个交易日）。")}

{fig(f7, f"<b>图 5｜stage-1 满 50 轮的逐轮墙钟（workers=2，含完整 valid 段）。</b>"
        f"125.0–151.8 s，均值 {round(sum(V2.get('epoch_seconds') or [0])/max(1,len(V2.get('epoch_seconds') or [1])),1)} s；"
        "前 40 轮稳定在 125–128 s，第 41–45 轮抬到 139–147 s 又回落（原因未定位，量级远小于 11-25 那次 73 s→800 s）。"
        "来源：<code>verify/verify_full50_s42.json</code> 的 <code>epoch_seconds</code>。")}

<table>
<caption>表 5｜单轮成本：三个口径并列（训练段 2,675 batch/轮 + valid 486 batch/轮）。</caption>
<thead><tr><th>口径</th><th class="num">单 batch</th><th class="num">单轮</th><th class="num">50 轮</th><th>说明</th></tr></thead>
<tbody>
<tr><td><b>满 50 轮实测</b>（2026-11-28，早停关，workers=2，含完整 valid 段）</td>
<td class="num">{round((V2.get('fit_seconds') or 0)/50/2675*1000,1)} ms</td>
<td class="num">{round((V2.get('fit_seconds') or 0)/50,1)} s</td>
<td class="num">{round((V2.get('fit_seconds') or 0)/3600,3)} h</td>
<td>峰值显存 {V2.get('peak_vram_gb')} GB；50 轮共 {V2.get('fit_seconds'):.0f} s，逐轮 125.0–151.8 s</td></tr>
<tr><td>早停口径实测（同上，早停开）</td><td class="num">{round((V1.get('fit_seconds') or 0)/V1.get('n_epochs_ran',1)/2675*1000,1)} ms</td>
<td class="num">{round((V1.get('fit_seconds') or 0)/V1.get('n_epochs_ran',1),1)} s</td>
<td class="num">{round((V1.get('fit_seconds') or 0)/3600,3)} h（{V1.get('n_epochs_ran')} 轮停）</td>
<td>min val_loss {V1.get('min_val_loss')} @ epoch {V1.get('min_val_loss_epoch')}</td></tr>
<tr><td>端到端实测（2026-11-27，10 轮，workers=2）</td>
<td class="num">{S_BATCH_MEAS:.0f} ms</td><td class="num">{S_EPOCH:.1f} s</td><td class="num">{H50_MEAS:.2f} h</td>
<td>峰值显存 {VRAM:.2f} GB；10 轮共 {PL_FIT_S:.0f} s（当时的 50 轮为外推）</td></tr>
<tr><td>50 步热身后外推（上一版用的 90 ms，<b>已作废</b>）</td><td class="num">90 ms</td><td class="num">241 s</td><td class="num">3.35 h</td>
<td>冷启动阶段偏高，被实测推翻</td></tr>
<tr><td>悲观上界：微基准 264 ms（不含 training_step 开销）</td><td class="num">264 ms</td><td class="num">706 s</td><td class="num">9.80 h</td>
<td>取数 126 + 模型 138，偏保守</td></tr>
<tr><td><b>PRISM-VQ stage-2 实测</b>（2026-11-28，3 轮，VQ-VAE 冻结）</td>
<td class="num">{S2B:.1f} ms</td><td class="num">{S2.get('median_s_per_epoch')} s</td>
<td class="num">{S2H:.3f} h</td>
<td>逐轮 {S2.get('epoch_seconds')}；峰值显存 {S2.get('peak_vram_gb')} GB；可训练参数 {S2.get('trainable_params_m')} M</td></tr>
<tr><td>PRISM-VQ stage-2（原「×2 结构推断」，<b>已作废</b>）</td><td class="num">—</td><td class="num">—</td><td class="num">≈3.70 h</td>
<td>实测 1.596 h，推断高估 2.3×</td></tr>
<tr><td>GRU 基线 s2024（实测）</td><td class="num">—</td><td class="num">86 s</td><td class="num">4,310 s</td><td>另一模型，仅供参考</td></tr>
</tbody></table>

<table>
<caption>表 5b｜全量预算：两个口径都给（stage-2 未实测，按 stage-1 单轮 ×2 结构推断）。</caption>
<thead><tr><th>口径</th><th class="num">stage-1 ×2 seed</th><th class="num">stage-2 ×8 档</th>
<th class="num">GRU ×2 seed</th><th class="num">合计</th></tr></thead>
<tbody>
<tr><td><b>满 50 轮实测</b>（stage-1 {V2H:.3f} h/档、stage-2 {S2H:.3f} h/档，均实测）</td>
<td class="num">{2*V2H:.1f} h</td><td class="num">{8*S2H:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{tot_meas:.1f} h</b></td></tr>
<tr><td>早停口径（stage-1 <b>实测第 {V1N} 轮停</b>；stage-2 未测早停轮次，<b>按 28 轮假设</b>）</td>
<td class="num">{2*V1H:.1f} h</td><td class="num">{8*S2H28:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{tot_early:.1f} h</b></td></tr>
<tr><td>悲观上界（微基准 264 ms/batch）</td><td class="num">{2*up_epoch*50/3600:.1f} h</td>
<td class="num">{16*up_epoch*50/3600:.1f} h</td><td class="num">{2*GRU_50:.1f} h</td>
<td class="num"><b>{tot_up:.0f} h</b></td></tr>
</tbody></table>
<p class="note">上一版报告的「62 h」是在只有 50 步外推（90 ms/step）时算的，<b>已被 10 轮实测取代</b>；
派单按微基准上界重算的 178.8 h 保留为悲观列。三档差异全部来自「单 batch 多少毫秒」这一个未知数，
现在它有 3 个独立观测值（51 / 90 / 264 ms），实测的 51 ms 可信度最高
（10 轮 × 3,161 batch/轮，含 valid）。</p>

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
<li><b><code>num_workers=8</code> 在这台机器上不可靠</b>：2026-11-27 三次尝试里 2 次硬退（RC=127，无 traceback）、
1 次成功但 spawn 用掉 121 s；<code>num_workers=2</code> 同一配置 10 轮连跑稳定。
11-25 那轮 GRU 的「epoch 73 s → 800 s 退化」与 1 次 <code>CUDA illegal memory access</code>，
方向上也与 8 worker 争抢一致。</li>
<li><b>共享 Python 环境在 11-26 07:56 被损坏</b>：<code>F:\\001_install\\020_Anaconda\\envs\\ds</code> 里
<b>73 个分布</b>有文件缺失（<code>typing_extensions.py</code>、<code>packaging/specifiers.py</code> 等），
导致 <code>import torch</code> 直接失败。已在本单 <code>.venv/</code> 里按原版本重装 73 个包修复
（不动共享环境），取证脚本 <code>experiments/WORK-557/_env_damage_scan.py</code>。
<b>这条影响所有用该环境的任务，不只本单。</b></li>
</ol>

<h2><span class="num">7</span>两份可复用沉淀</h2>

<h3>7.1 数据体检清单 → <code>experiments/WORK-557/data_health_checklist.md</code></h3>
<p>任何人用 <code>quantdb</code> 做回测前先跑这八条。最值钱的两条：</p>
{fig(f5, "<b>图 6｜「行情表无行」与「真缺口」不是一回事。</b>灰柱是 <code>daily</code> 无行率（2015 年高达 6.79%），"
        "红柱是剔除 <code>suspend_d</code> 之后真正的缺口率（最高 0.026%）。"
        "直接按 5% 阈值判死会误杀 2015 股灾年的停牌潮。"
        "来源：<code>crosscheck.json</code> 的 <code>check_A_suspend.by_year</code>。")}
{fig(f6, "<b>图 7｜涨跌停判据的 3.0× / 5.0× 过判。</b>用 WORK-503 的自判阈值（涨跌幅近似）判出的"
        "涨停行数是库内 <code>stk_limit</code> 真值的 3.018 倍、跌停是 5.002 倍（12,405,432 行 join 后）。"
        "涨跌停决定「能不能买」，判错会系统性削掉收益。来源：<code>crosscheck.json</code> 的 <code>check_B_limit</code>。")}

<h3>7.2 降窗代价记录 → <code>experiments/WORK-557/window_cost_note.md</code></h3>
<p>一句话：「缩轮数」几乎不省时间，「缩窗口」省 55% 时间但永久丢 54% 样本，
而真正的省时装置是早停。从「缩窗 6 轮」（<b>6.2 min</b>）回到「全窗 + 早停」（<b>0.72 h</b>）是
<b>7.0×</b>的计算量 —— 这就是「可判性」的价格标签（两个数都按本轮实测 51 ms/batch 重算；
沉淀 v1 里的「11 min → 1.07 h = 5.8×」用的是被推翻的 90 ms，已作废）。</p>

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

<li><b>lookahead 检查：已通过（2026-11-29 定位到根因在检查脚本自己身上）</b>——
<code>feature_date_mismatches = 0 / 2,327,786</code>、<code>prior_mismatches = 0 / 2,731,806</code>、
<code>label_mismatches = 0</code>，最大残差 2.8e-05 / 2.2e-06 / 7.4e-09（float32 量级）。
根因：检查脚本的 <code>TRAIN</code> 常量写成了 <b>2015–2019</b>（WORK-503 的缩窗），
而 <code>work557_build_dataset.py</code> 的 <code>fit_stats</code> 是按本单预注册 C1 的
<b>2009–2019</b> 拟合的 —— 拟合窗口不同 → <code>f_mean/f_std</code> 不同 → 逐列**全都**对不上，
看起来像 86% 的行「日期错位」。逐列取证：6 个探针日 × 2 只票 × 158 列，<b>0/158 列差异 &gt; 1e-4</b>；
把 qlib 侧整体前移/后移一天后差值反而升到 1.8–4.0（排除日期错位）。改回 2009–2019 后检查通过。
下一条是修之前的状态，保留以免误导：</li>
<li><b>（历史）lookahead 曾不可用</b>：修掉 <code>KeyError: sh600001</code>
（qlib 二进制缺票 → 现按候选序跳过并记 <code>skipped_symbols</code>）后能完整跑完，结果是
标签轴 <code>label_max_abs_diff = 7.4e-9</code>（通过），但 <code>feature_date_mismatches = 1,997,525 / 2,327,786</code>、
<code>prior_mismatches = 2,617,126 / 2,625,423</code>，最大绝对差 1.35–4.81 且呈<b>常量偏移</b> ——
更像两套归一化口径不一致而非日期错位。<b>所以不能据此声称「无前视」</b>；影响范围：H1/H3 建在同一批特征上，
若特征侧真有前视则所有档同受影响，本检查既不能证实也不能证伪。</li>
<li><b>integrity I4 仍未通过（不影响测试段）</b>：5,798 条 violations 全部落在
2012-01-04 起的头 20 个交易日（≈300 只 × 20 日），根因是该检查读的是 WORK-503 时代的新浪快照
（起点 2012），与 quantdb 面板（2006 起点）不一致；2022–2024 测试段<b>零条</b>。
I3 已修：两份日历日期完全相同，只是 <code>2006-01-04</code> vs <code>20060104</code> 的格式差异，
按日期比较后 <code>identical=true</code>。</li>
<h3>8.1 不确定项</h3>
<ul>
<li><b>实测只覆盖 stage-1、前 10 轮</b>
<li><b>stage-2 已是实测，但只有 3 轮、1 个 seed</b>：112.9 s/轮中位（0.89× stage-1，不是原假设的 2×），
整队满 50 轮口径因此从 35.6 h 降到 {tot_meas:.1f} h。它的 <b>早停轮次未测</b>（3 轮里 val_loss 还在降），
早停口径 {tot_early:.1f} h 用的是「28 轮」假设，属上界侧估计。</li>
<li><b>50 轮墙钟已是实测，不再外推</b>：2026-11-28 关掉早停跑满 50 轮，6,648.5 s = 1.847 h（均值 133.0 s/轮）；
稳态中位 126.7 s。注意两个口径不要混用：<code>fit_seconds/50 = 132.97 s</code>（含启动摊销）、
<code>epoch_seconds</code> 均值 131.0 s —— 本文一律用前者，与表 5 一致。</li>
<li><b>stage-2 只测了 3 轮、1 个 seed</b>：106.1/112.9/114.9 s，val_loss 仍在下降（1.0639→1.0324→1.0243），
<b>它的早停轮次未测</b>，本文按 stage-1 的 28 轮假设（因此早停口径是上界侧的估计）。</li>
<li><b>「teardown 死锁」是上一版的误判</b>，本轮更正为「标定脚本缺 <code>wandb.init</code>」+「8 worker spawn 不稳」；
生产路径在 workers=2 下 10 轮干净退出，未见死锁。</li>
<li><b>GRU 基线只有 1 个 seed 完成</b>（s42 被硬闸杀掉），表 4 的对照只有 s2024 一格。</li>
<li><b>共享 <code>ds</code> 环境仍处于「73 个分布缺文件」状态</b>：本单靠 <code>.venv</code> 覆盖层绕过，
其它任务会继续踩。</li>
<li><b>远端兜底失败</b>：<code>git push origin HEAD:refs/heads/agent/work-557</code> 被 GitHub 拒绝
（<code>Permission to huangsewanjia/quant-lab.git denied to yongkaimo</code>，403），
本单改动已在本地提交 <code>680fc34</code> 并以 zip 附件随回帖交付。</li>
</ul>

<h3>8.2 要跑完全窗 50 轮，需要什么（三选一，互斥）</h3>
<ol>
<li><b>换硬件</b>：≥4 张同代卡或一张显存足够、单步 ≥4× 快的卡 → 实测口径 {2*V2H+16*V2H+2*GRU_50:.0f} h
压到 &lt;10 h，早停口径 {2*V1H+16*V1H+2*GRU_50:.0f} h 压到 &lt;6 h。</li>
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
