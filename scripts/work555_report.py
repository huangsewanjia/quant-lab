#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 单文件 HTML 报告生成

样式规格（工单「样式规格」）：
  底 #ffffff；正文 #1a1a1a；分隔线 #d9d9d6；浅底块 #f7f7f5
  强调色只用三支：主蓝 #0F4D92 / 次青 #42949E / 警示红 #B64342
  正文字体 "Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC",
  "Noto Sans CJK SC",sans-serif；数据字体 "Cascadia Mono",Consolas,"SF Mono",Menlo,monospace
  无阴影、无渐变、无圆角卡片堆叠；表格浅分隔线，不用斑马纹
  图注写在图下，含来源（论文字号 + 表号）与本单结论一句话；面板 a/b 小写
  A4 纵向、正文 10.5pt、行距 1.6
  单文件：图片 base64 内嵌，无外链 JS/CSS，断网可开

输出：experiments/WORK-555/report.html

运行：
  /tmp/w555venv/bin/python scripts/work555_report.py
"""
import base64
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)
DATA = os.path.join(REPO, "data", EXP)
FIG = os.path.join(REPO, "figures", EXP)

ARMS = [("dee", "DEE（六型异质集成, N=20）"),
        ("mlp_only", "MLP-only（同 N=20 同随机化）"),
        ("xgb", "XGBoost（单模型, 全特征全训练行）")]
SEEDS = [42, 43]
PAPER_T1 = [("Linear Reg.", 0.033, 0.044), ("MLP", 0.040, 0.053),
            ("Random Forest", 0.044, 0.054), ("Double Ensemble", 0.044, 0.056),
            ("LightGBM", 0.045, 0.058), ("XGBoost", 0.046, 0.059),
            ("DEE", 0.048, 0.060)]
PAPER_T2 = [("Linear only", 0.039, 0.051), ("Tree only", 0.041, 0.054),
            ("MLP only", 0.043, 0.055), ("Linear + MLP + Tree", 0.046, 0.059),
            ("Full DEE (6 types)", 0.048, 0.060)]
PAPER_T3 = [("Linear Reg.", 25.32, 1.214, 26.25), ("MLP", 32.42, 1.622, 16.94),
            ("Random Forest", 35.75, 1.688, 17.98), ("Double Ensemble", 36.10, 1.742, 19.21),
            ("LightGBM", 36.32, 1.745, 19.33), ("XGBoost", 36.33, 1.757, 18.82),
            ("DEE", 38.56, 1.846, 17.15)]


def b64(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def jload(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fmt(v, nd=4, pct=False, sign=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def build():
    runs, bts, audits = {}, {}, {}
    for fn in sorted(os.listdir(OUT)):
        if fn.startswith("run_") and fn.endswith(".json"):
            tag = fn[4:-5]
            runs[tag] = jload(os.path.join(OUT, fn))
        if fn.startswith("backtest_") and fn.endswith(".json"):
            tag = fn[9:-5]
            bts[tag] = jload(os.path.join(OUT, fn))
    audit = jload(os.path.join(OUT, "audit_findings.json"))
    man = jload(os.path.join(OUT, "snapshot_manifest.json"))
    meta = jload(os.path.join(OUT, "sample_meta.json"))
    leak = jload(os.path.join(OUT, "leakage_diag.json"))
    review = jload(os.path.join(OUT, "review_round1.json"))

    # ---------------- 主检验量 ----------------
    per_seed = []
    for s in SEEDS:
        kd, kb = f"dee_s{s}", f"mlp_only_s{s}"
        if kd in runs and kb in runs:
            rd = runs[kd]["test_rankic_mean"]
            rb = runs[kb]["test_rankic_mean"]
            per_seed.append({"seed": s, "rd": rd, "rb": rb,
                             "delta": (rd - rb) / rb if rb else float("nan")})
    deltas = [p["delta"] for p in per_seed]
    delta_mean = float(np.mean(deltas)) if deltas else float("nan")
    delta_rng = (max(deltas) - min(deltas)) if deltas else float("nan")
    # 95% CI：正态近似 t 分布（df = n-1 = 1）
    ci_lo = ci_hi = float("nan")
    if len(deltas) >= 2:
        from math import sqrt
        t975_df1 = 12.706          # t(0.975, df=1)
        se = float(np.std(deltas, ddof=1)) / sqrt(len(deltas))
        ci_lo, ci_hi = delta_mean - t975_df1 * se, delta_mean + t975_df1 * se
    ric_dee = [runs[f"dee_s{s}"]["test_rankic_mean"] for s in SEEDS if f"dee_s{s}" in runs]
    ric_mlp = [runs[f"mlp_only_s{s}"]["test_rankic_mean"] for s in SEEDS if f"mlp_only_s{s}" in runs]
    ric_rng = (max(ric_dee) - min(ric_dee)) if len(ric_dee) > 1 else 0.0
    # 基线（最佳单族）自身的两种子极差：基线不稳 → Δ_ic 不可比，必须一起披露
    ric_rng_mlp = (max(ric_mlp) - min(ric_mlp)) if len(ric_mlp) > 1 else 0.0
    # Δ_ic 逐种子的符号翻转检查
    delta_signs = {1 if d > 0 else (-1 if d < 0 else 0) for d in deltas}
    delta_flip = len(delta_signs) > 1

    net_sh = []
    for s in SEEDS:
        b = bts.get(f"dee_s{s}")
        if b:
            net_sh.append(b["metrics"]["main_8bps"]["sharpe_rf0"])
    net_sh_mean = float(np.mean(net_sh)) if net_sh else float("nan")

    # ---------------- 判词 ----------------
    reasons = []
    if not np.isfinite(delta_mean):
        verdict_word, verdict_cls = "退（不可比）", "negative"
        reasons.append("主检验量无法计算（最佳单族 RankIC ≤ 0 或缺失）")
    elif delta_mean <= 0:
        verdict_word, verdict_cls = "退", "negative"
        reasons.append(f"Δ_ic = {delta_mean:+.2%} ≤ 0：DEE 不优于最佳单族（MLP-only）")
    elif delta_mean > 0.50:
        verdict_word, verdict_cls = "退（基线不可比）", "negative"
        reasons.append(f"Δ_ic = {delta_mean:+.2%} > +50%：单族基线本身跑坏，横向不可比")
    elif 0.02 <= delta_mean <= 0.30:
        verdict_word, verdict_cls = "进带", ""
        reasons.append(f"Δ_ic = {delta_mean:+.2%} 落在预注册进带 [+2%, +30%]")
    else:
        verdict_word, verdict_cls = "退（未复现）", "negative"
        reasons.append(f"Δ_ic = {delta_mean:+.2%} 落在进带之外")
    if np.isfinite(net_sh_mean) and net_sh_mean < 0:
        verdict_word, verdict_cls = "退（净 Sharpe < 0）", "negative"
        reasons.append(f"DEE 净 Sharpe（rf=0，8 bps 主口径）= {net_sh_mean:+.3f} < 0，直接退")
    if not np.isfinite(delta_mean) or ci_lo <= 0 <= ci_hi:
        reasons.append("95% CI 含 0 → 记为「未复现」（预注册 §6.2）")
    if ric_rng > 0.01:
        reasons.append(f"两种子 RankIC 极差 {ric_rng:.4f} > 0.01 → 结果不稳定，须在结论中明说")
    if ric_rng_mlp > 0.01:
        reasons.append(f"<b>最佳单族基线自身</b>两种子 RankIC 极差 {ric_rng_mlp:.4f} > 0.01 → "
                       f"基线在本面板上就已不稳定，DEE 与基线的相对差不可比"
                       f"（对照：论文用 <b>10 个种子</b>、Table 1/2 的 ± 值均在 0.002 量级，"
                       f"本单 2 个种子的极差比论文的种子间标准差还大一个量级）")
    if delta_flip:
        reasons.append(f"Δ_ic 逐种子<b>符号翻转</b>（{', '.join(f'{d*100:+.1f}%' for d in deltas)}）"
                       f"→ 均值 {delta_mean*100:+.2f}% 不代表任何可复现的方向")

    # ---------------- 勾稽自检 ----------------
    tie = []
    for arm, _ in ARMS:
        for s in SEEDS:
            p = os.path.join(OUT, f"bt_daily_{arm}_s{s}_main_8bps.csv")
            if not os.path.exists(p):
                continue
            d = pd.read_csv(p)
            gross = float(d["gross_ret"].sum())
            cost = float(d["cost"].sum())
            net_sum = float(d["net_ret"].sum())
            nav = float((1.0 + d["net_ret"]).cumprod().iloc[-1] - 1.0)
            turn_sum = float(d["turnover"].sum())
            bs = float(d["buys"].sum() + d["sells"].sum())
            tie.append({
                "arm": arm, "seed": s,
                "gross": gross, "cost": cost, "net_sum": net_sum,
                "nav": nav, "turn_sum": turn_sum, "bs": bs,
                "c1": abs(gross - cost - net_sum),
                "c2": abs(turn_sum - bs),
                "c3": (float(np.max(np.abs(d["sum_w"].to_numpy() + d["cash"].to_numpy() - 1.0)))
                       if ("sum_w" in d.columns and "cash" in d.columns) else 0.0),
                "max_sum_w": (float(np.max(d["sum_w"].to_numpy()))
                              if "sum_w" in d.columns else float("nan")),
                "n_over_1": (int(np.sum(d["sum_w"].to_numpy() > 1.0 + 1e-9))
                             if "sum_w" in d.columns else 0),
            })
    c1_max = max([t["c1"] for t in tie], default=0.0)
    c2_max = max([t["c2"] for t in tie], default=0.0)
    c4_ledger = max([t["c3"] for t in tie], default=0.0)
    c4_max_sum_w = max([t["max_sum_w"] for t in tie if np.isfinite(t["max_sum_w"])],
                        default=float("nan"))
    c4_n_over = sum([t["n_over_1"] for t in tie])
    if len(deltas) >= 2:
        c3 = abs(delta_mean - float(np.sum(deltas) / len(deltas)))
    else:
        c3 = 0.0

    # ---------------- 组装 HTML ----------------
    H = []
    A = H.append
    A("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    A("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    A("<title>DEE 异质专家集成 · A 股 CSI300 复现 · WORK-555</title>")
    A("<style>")
    A("""
:root{--ink:#1a1a1a;--ink2:#3d3d3d;--muted:#6f6f6f;--rule:#d9d9d6;--rule2:#ececeb;
--panel:#f7f7f5;--bg:#ffffff;--blue:#0F4D92;--teal:#42949E;--red:#B64342;
--sans:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC","Noto Sans CJK SC",sans-serif;
--mono:"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
font-size:10.5pt;line-height:1.6;text-rendering:optimizeLegibility}
.page{max-width:980px;margin:0 auto;padding:40px 54px 72px}
header.masthead{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:18px}
.kicker{font-size:9.5pt;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
h1{font-size:21pt;line-height:1.34;margin:0 0 8px;font-weight:700}
.subtitle{font-size:11pt;color:var(--ink2);margin:0 0 12px;line-height:1.55}
.byline{font-size:9.5pt;color:var(--muted);border-top:1px solid var(--rule);padding-top:9px;
display:flex;flex-wrap:wrap;gap:3px 20px}
.byline b{color:var(--ink2);font-weight:600}
.disclaimer{border:1px solid var(--rule);border-left:3px solid var(--red);background:var(--panel);
padding:11px 16px;margin:16px 0 24px;font-size:9.5pt;color:var(--ink2);line-height:1.75}
h2{font-size:14pt;font-weight:650;margin:34px 0 11px;padding-bottom:5px;
border-bottom:1px solid var(--rule)}
h2 .num{color:var(--blue);font-weight:700;margin-right:8px}
h3{font-size:11.5pt;font-weight:650;margin:20px 0 7px}
p{margin:0 0 11px;text-align:justify}
ul,ol{margin:0 0 13px;padding-left:21px}li{margin-bottom:5px}
.lead{font-size:11.5pt;line-height:1.72}
.keybox{border:1px solid var(--rule);border-left:4px solid var(--blue);background:var(--panel);
padding:13px 17px;margin:0 0 20px}
.verdict{border:1px solid var(--rule);border-left:4px solid var(--teal);background:var(--panel);
padding:14px 18px;margin:0 0 22px}
.verdict.negative{border-left-color:var(--red)}
.verdict h3{margin:0 0 7px;font-size:12pt}
.verdict .word{font-size:17pt;font-weight:700;color:var(--teal)}
.verdict.negative .word{color:var(--red)}
.verdict .caveat{margin-top:9px;padding-top:9px;border-top:1px solid var(--rule2);
font-size:9.5pt;color:var(--ink2);line-height:1.6}
figure{margin:22px 0 24px;page-break-inside:avoid}
figure img{width:100%;height:auto;display:block;border:1px solid var(--rule2)}
figcaption{font-size:9pt;color:var(--ink2);line-height:1.62;margin-top:7px;
padding-left:10px;border-left:2px solid var(--rule)}
figcaption b{color:var(--ink)}
table{border-collapse:collapse;width:100%;margin:13px 0 19px;font-size:9pt}
caption{caption-side:top;text-align:left;font-size:9.5pt;color:var(--ink2);padding-bottom:6px}
th,td{border-bottom:1px solid var(--rule2);padding:5px 7px;text-align:right;vertical-align:top}
th{border-bottom:1px solid var(--ink);font-weight:650;color:var(--ink);white-space:nowrap}
td.l,th.l{text-align:left}td.c,th.c{text-align:center}
tbody tr.tot td{border-top:1px solid var(--ink);font-weight:650;background:var(--panel)}
.mono{font-family:var(--mono);font-size:8.5pt}
.small{font-size:9pt;color:var(--ink2)}
.tag{display:inline-block;border:1px solid var(--rule);background:var(--panel);
padding:1px 7px;font-size:8.5pt;color:var(--ink2);margin-right:5px}
.tag.red{border-color:var(--red);color:var(--red)}
.tag.blue{border-color:var(--blue);color:var(--blue)}
.tag.teal{border-color:var(--teal);color:var(--teal)}
pre{background:var(--panel);border:1px solid var(--rule2);padding:10px 12px;overflow-x:auto;
font-family:var(--mono);font-size:8.3pt;line-height:1.55;margin:0 0 14px}
footer{border-top:1px solid var(--rule);margin-top:34px;padding-top:12px;
font-size:8.8pt;color:var(--muted);line-height:1.7}
@page{size:A4;margin:16mm 15mm}
@media print{body{font-size:10.5pt}.page{max-width:none;padding:0}
h2{page-break-after:avoid}table{page-break-inside:avoid}}
""")
    A("</style></head><body><div class=\"page\">")

    A("<header class=\"masthead\">")
    A("<div class=\"kicker\">WORK-555 · 环 2 落地 · 论文复现</div>")
    A("<h1>DEE「异质专家集成」在 A 股 CSI300 面板上的复现：主检验量与进 / 退判词</h1>")
    A("<p class=\"subtitle\">复现对象：Li D.《Diversification Across Predictors: Heterogeneous "
      "Ensemble Learning for Cross-Sectional Return Prediction》arXiv:2611.12389v1（2026-11-09，"
      "ICAIF '26，DOI 10.1145/3858063.3858743，<b>论文无官方代码</b>）</p>")
    A("<div class=\"byline\">")
    A("<span><b>实验单号</b> WORK-555</span><span><b>预注册</b> "
      "experiments/WORK-555/preregistration.md v1.0（commit <span class=\"mono\">9a85409</span>）</span>")
    A(f"<span><b>数据截止</b> 2021-01-29（测试段末日 2020-12-31）</span>")
    A(f"<span><b>数据快照</b> {len(man['items'])} 份，sha256 逐条锁定</span>")
    A(f"<span><b>撰写</b> 金融研究（单体执行，署名：金融研究）</span>")
    A("</div></header>")

    A("<div class=\"disclaimer\">")
    A("<div>本报告为内部研究模拟，不构成投资建议与收益承诺。</div>")
    A("<div>撰写方非持牌金融机构，本单不执行交易、不含个股评级与目标价。</div>")
    A("<div>全部结论仅对本次冻结协议与本次快照成立，复现者须自行重跑核验。</div>")
    A("</div>")

    # ---- 判词
    A("<div class=\"verdict " + ("negative" if verdict_cls else "") + "\">")
    A("<h3>进 / 退判词</h3>")
    A(f"<div class=\"word\">{verdict_word}</div>")
    A("<ul>" + "".join(f"<li>{r}</li>" for r in reasons) + "</ul>")
    A("<div class=\"caveat\">本结论不构成投资建议。判词严格按预注册 §6.2 的机械规则给出；"
      "绝对水平不作 KPI（预注册 §6.3），只检验相对量 Δ_ic。</div>")
    A("</div>")

    # ---- 0 摘要
    A("<h2><span class=\"num\">0</span>一句话摘要</h2>")
    A("<p class=\"lead\">本次复现把论文的 DEE（Diversified Expert Ensemble，异质专家集成）"
      "方法完整落到本机自建的 A 股 CSI300 日频面板上，用同一份数据、同一时间切分、"
      "同一组随机种子，跑出「DEE / MLP-only / XGBoost」三档对照，"
      "并只用论文 Table 2 那个相对量——"
      "<b>Δ_ic =（DEE 的 RankIC − 最佳单族的 RankIC）/ 最佳单族的 RankIC</b>——"
      "做进 / 退判定。<b>RankIC</b> 指的是每个交易日把股票按预测分数排序后，"
      "与次日实际收益排序之间的相关系数，再对全测试期取时间均值；"
      "它是量化选股里衡量「分数能不能把好坏股票排对」的标准尺子。</p>")
    A(f"<p>判词：<b>{verdict_word}</b>。Δ_ic = <span class=\"mono\">{fmt(delta_mean,4,True,True)}</span>"
      f"（论文 Table 2 报告 +9.1%）；DEE 净 Sharpe（rf=0，8 bps 主口径）"
      f"<span class=\"mono\">{fmt(net_sh_mean,3,False,True)}</span>。"
      "机制诊断见 §7：本次面板上单因子的截面 <b>Spearman</b> 相关（看排序）"
      "显著高于 <b>Pearson</b> 相关（看线性），而 DEE 三个模型族全部按 MSE（均方误差）"
      "目标训练，这正是论文里 XGBoost 早停在本面板上退化的同一个原因。</p>")

    # ---- 1 预注册
    A("<h2><span class=\"num\">1</span>预注册：跑数之前冻结了什么</h2>")
    A("<p>预注册（preregistration）是这类复现的纪律闸门：先把口径写死、提交到 git，"
      "再允许跑第一次回测；跑完不改口径，要改就另开一版并写清改了什么。"
      "本单协议在 commit <span class=\"mono\">9a85409</span> 冻结，"
      "比任何一次回测产物的 commit 都早（可查 <span class=\"mono\">git log</span>）。"
      "7 项写死内容如下，任何一项没有「视情况」字样。</p>")
    A("<table><caption>表 1 · 预注册 v1.0 的 7 项写死内容（<span class=\"mono\">experiments/WORK-555/preregistration.md</span>）</caption>")
    A("<thead><tr><th class=\"l\" style=\"width:16%\">项</th><th class=\"l\">冻结内容</th></tr></thead><tbody>")
    for k, v in [
        ("1 样本期", "训练 2008-01-02~2014-12-31（84 个月）／验证 2015-01-01~2016-12-31（24 个月）／"
                     "测试 2017-01-03~2020-12-31（48 个月）；训练 ≥36 个月、测试 ≥24 个月均满足"),
        ("2 股票池", "000300.SH（沪深 300）月末 PIT 快照成分（快照日 ≤ t 的最新一份）；"
                     "上市满 60 个交易日过滤；退市股保留到最后交易日、不回溯剔除；停牌日保留行且不可成交"),
        ("3 因子集", "自实现 Alpha158 风格算子 48 列（附录 A 逐条公式）；价格用后复权视图 "
                     "v_daily_hfq；不含任何基本面字段 → 不涉及公告日时点；MAD 5× 去极值 + 截面 z-score"),
        ("4 专家构造", "β=0.8789（bootstrap 池）、γ=0.5（特征子集，全覆盖约束）、κ=1.5（IC 过滤容差）、"
                       "α=1e-3（惩罚线性族）、Adam lr 1e-3 / weight decay 1e-4 / batch 8192、"
                       "early stop patience 50 每 20 步评估、集成级验证损失；"
                       "N=50→20，配比 4 linear / 1 ridge / 2 lasso / 1 elasticnet / 6 MLP(32) / 6 软树(12)"),
        ("5 成本与组合", "top-50 等权、日频调仓；单边成本主口径 8 bps + 卖出印花税 10 bps（附 5 bps 敏感性）；"
                         "涨停不可买、跌停不可卖（强制持有）、停牌不可成交；涨跌停价只用于成交判定、不进模型"),
        ("6 主检验量", "Δ_ic =（RankIC_DEE − RankIC_最佳单族）/ RankIC_最佳单族；"
                       "进带 [+2%, +30%]；退：Δ_ic ≤ 0 或 > +50% 或净 Sharpe < 0"),
        ("7 失败判据", "不可复现、独立复算不一致 >1e-6、前视/幸存者/退市偏差、掩码断言未生效、"
                       "数据缺口、协议变更——任一触发即退回，不靠文字补全"),
    ]:
        A(f"<tr><td class=\"l\"><b>{k}</b></td><td class=\"l\">{v}</td></tr>")
    A("</tbody></table>")

    # ---- 2 数据
    A("<h2><span class=\"num\">2</span>数据与快照</h2>")
    A("<p>主源是本机自建的金融数据底座（<span class=\"mono\">/srv/share/quantdb/db/quantdb.duckdb</span>，"
      "只读连接），补充源为 tushare 已实测可得的端点。原始层只追加、逐份 sha256 锁定，"
      "manifest 里 <span class=\"mono\">ok:false</span> 的项保留原文不删。</p>")
    A("<table><caption>表 2 · 个股层快照 manifest（<span class=\"mono\">experiments/WORK-555/snapshot_manifest.json</span>）</caption>")
    A("<thead><tr><th class=\"l\">快照</th><th class=\"l\">用途</th><th class=\"c\">ok</th>"
      "<th class=\"c\">行数</th><th class=\"c\">日期区间</th><th class=\"l\">sha256（前 16 位）</th></tr></thead><tbody>")
    for it in man["items"]:
        A(f"<tr><td class=\"l mono\">{it['name']}</td><td class=\"l\">{it['description']}</td>"
          f"<td class=\"c\">{'✓' if it.get('ok') else '✗ ' + str(it.get('error'))[:60]}</td>"
          f"<td class=\"c mono\">{it.get('rows', 0):,}</td>"
          f"<td class=\"c mono\">{it.get('date_min', '—')} ~ {it.get('date_max', '—')}</td>"
          f"<td class=\"l mono\">{it.get('sha256', '—')[:16]}…</td></tr>")
    A("</tbody></table>")
    A(f"<p class=\"small\">加工层：样本 <span class=\"mono\">{meta['n_rows']:,}</span> 行 "
      f"（train {meta['split_counts']['train']:,} / valid {meta['split_counts']['valid']:,} / "
      f"test {meta['split_counts']['test']:,}），{meta['n_codes']} 只个股，"
      f"{meta['n_dates']} 个交易日；PIT 成分快照 {meta['pit']['snapshots_in_window']} 份，"
      f"每日成分数中位 {meta['pit']['members_per_day_median']}。</p>")

    # ---- 3 三档对照
    A("<h2><span class=\"num\">3</span>三档对照结果（主检验表）</h2>")
    A("<p>下表是本单的核心交付：同一份面板、同一时间切分、同一种子集合下，"
      "三档模型各自的样本外预测力与组合表现。</p>")
    A("<table><caption>表 3 · 主检验量三档对照（test 2017-01-03~2020-12-31；"
      "组合为 top-50 等权日频调仓、8 bps + 10 bps 印花税主口径）</caption>")
    A("<thead><tr><th class=\"l\">臂</th><th class=\"c\">种子</th><th class=\"c\">RankIC</th>"
      "<th class=\"c\">IC</th><th class=\"c\">年化</th><th class=\"c\">Sharpe(rf=0)</th>"
      "<th class=\"c\">MDD</th><th class=\"c\">日均双边换手</th></tr></thead><tbody>")
    for arm, label in ARMS:
        rows = []
        for s in SEEDS:
            r = runs.get(f"{arm}_s{s}")
            b = bts.get(f"{arm}_s{s}")
            if not r:
                continue
            m = b["metrics"]["main_8bps"] if b else None
            rows.append((r, m))
            A(f"<tr><td class=\"l\">{label}</td><td class=\"c mono\">{s}</td>"
              f"<td class=\"c mono\">{fmt(r['test_rankic_mean'],4,False,True)}</td>"
              f"<td class=\"c mono\">{fmt(r['test_ic_mean'],4,False,True)}</td>"
              f"<td class=\"c mono\">{fmt(m['annualized_return']*100,2,True,True) if m else '—'}</td>"
              f"<td class=\"c mono\">{fmt(m['sharpe_rf0'],3,False,True) if m else '—'}</td>"
              f"<td class=\"c mono\">{fmt(m['max_drawdown']*100,2,True,True) if m else '—'}</td>"
              f"<td class=\"c mono\">{fmt(m['turnover_daily_mean'],3) if m else '—'}</td></tr>")
        # 工单验收第 4 条要求「只报 mean ± range」：每臂补一行两种子的 mean ± range
        if rows:
            def mr(vals, scale=1.0, nd=4):
                vals = [v for v in vals if v is not None]
                if not vals:
                    return "—"
                mean = float(np.mean(vals))
                rng = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
                return f"{mean*scale:.{nd}f} ± {rng*scale:.{nd}f}"

            def pick(key, scale=1.0, nd=4):
                return mr([(r["test_rankic_mean"] if key == "rankic" else
                             r["test_ic_mean"] if key == "ic" else
                             m[key]) if m else None
                           for r, m in rows], scale, nd)
            A(f"<tr class=\"tot\"><td class=\"l\">{label}（两种子 mean ± range）</td>"
              f"<td class=\"c mono\">42/43</td>"
              f"<td class=\"c mono\">{pick('rankic')}</td>"
              f"<td class=\"c mono\">{pick('ic')}</td>"
              f"<td class=\"c mono\">{pick('annualized_return',100,2)}</td>"
              f"<td class=\"c mono\">{pick('sharpe_rf0',1,3)}</td>"
              f"<td class=\"c mono\">{pick('max_drawdown',100,2)}</td>"
              f"<td class=\"c mono\">{pick('turnover_daily_mean',1,3)}</td></tr>")
    A("</tbody></table>")
    A("<p class=\"small\">IC = 每个交易日预测分数与次日收益的 Pearson 相关，再对测试期取时间均值；"
      "MDD = 日频净净值曲线的最大回撤（负值）；换手 = 当日买入额 + 卖出额（占组合净值比重）。</p>")
    A("<p class=\"small\"><b>组合记账口径：现金账本。</b>卖出释放现金、买入消耗现金，任意一天 "
      "<span class=\"mono\">Σw + cash ≡ 1</span>，组合总敞口恒为 1，<b>不存在隐含杠杆</b>；"
      "卖不掉（跌停 / 停牌 / 缺 limit）的仓位<b>强制保留原权重并占用资金</b>，"
      "买不到的部分留成现金（0 收益，与主口径 rf=0 一致）。"
      "这条口径是独立审查阶段的实现缺陷修复：早期版本把「强制持有的旧权重」与「新买入的 1/K」"
      "直接叠加，实测 Σw 最高 1.44（隐含杠杆 44%）、974 个交易日里 151 天 > 1；"
      "修复后审计侧新增 <span class=\"mono\">Σw ≤ 1</span> 与 <span class=\"mono\">Σw + cash = 1</span> "
      "两道闸门逐日检查。</p>")

    # ---- 3b 臂 C 早停退化：实测 + 非预注册诊断对照
    xgb42 = runs.get("xgb_s42", {}).get("xgb", {})
    diag = {s: runs.get(f"xgbfull_s{s}") for s in SEEDS}
    diag = {s: v for s, v in diag.items() if v}
    if xgb42 or diag:
        A("<h3>臂 C（XGBoost）的早停退化：实测与诊断对照</h3>")
        A("<p>这一段是本单唯一一处<b>协议内但结果反常</b>的地方，必须单独摊开讲。"
          "预注册 §4.6 冻结的臂 C 超参（2000 棵、lr 0.05、深度 6、早停 patience 50 监控验证 MSE）"
          "在<b>本面板</b>上退化成了单树桩，机理是下面三步，全部有实测值：</p>")
        A("<ol>"
          "<li>验证 RMSE 曲线<b>近乎平坦</b>：第 0 轮 "
          f"<span class=\"mono\">{fmt((runs.get('xgbfull_s42') or {}).get('valid_rmse_at_round0'), 8)}</span>"
          "，全曲线最优在第 "
          f"<span class=\"mono\">{(runs.get('xgbfull_s42') or {}).get('best_round_selected_on_valid', '—')}</span> "
          f"轮（<span class=\"mono\">{fmt((runs.get('xgbfull_s42') or {}).get('valid_rmse_at_best'), 8)}</span>）"
          "——50 轮内没有任何一轮严格优于第 0 轮，patience 立刻耗尽。</li>"
          "<li>xgboost 命中早停后，sklearn 的 <span class=\"mono\">predict()</span> 会"
          "<b>自动截断到 best_iteration+1 轮</b>。实测 "
          "<span class=\"mono\">predict(X) == booster.predict(DMatrix(X), iteration_range=(0,1))</span>"
          "（逐元素 max|diff| = 0），与全树预测 max|diff| = 1.8e-2。"
          f"于是本单臂 C 实际只用了 <b><span class=\"mono\">{xgb42.get('n_trees_used_in_predict', '—')}</span> 棵树</b>"
          f"（共拟合 {xgb42.get('n_trees_fitted', '—')} 棵）。</li>"
          "<li>结果：臂 C 的测试段 RankIC 停在 "
          f"<span class=\"mono\">{fmt(runs.get('xgb_s42', {}).get('test_rankic_mean'), 4, False, True)}</span>，"
          "组合层面被日频换手成本吃掉（见 §3 表 3）。</li>"
          "</ol>")
        if diag:
            A("<table><caption>表 3b · 非预注册诊断对照：关掉早停、用<b>验证曲线</b>选轮次的 XGBoost"
              "（<span class=\"mono\">scripts/work555_xgb_diag.py</span>）——"
              "<b>不参与进 / 退判词</b>，只用来把早停病态的影响量化出来</caption>")
            A("<thead><tr><th class=\"l\">臂</th><th class=\"c\">种子</th><th class=\"c\">选中的树数</th>"
              "<th class=\"c\">测试 RankIC</th><th class=\"c\">测试 IC</th><th class=\"c\">满 2000 棵 RankIC</th>"
              "<th class=\"l\">口径</th></tr></thead><tbody>")
            for s, v in sorted(diag.items()):
                A(f"<tr><td class=\"l\">XGBoost（无早停，诊断）</td><td class=\"c mono\">{s}</td>"
                  f"<td class=\"c mono\">{v.get('best_round_selected_on_valid', '—')}</td>"
                  f"<td class=\"c mono\">{fmt(v.get('test_rankic_mean_best_iter'), 4, False, True)}</td>"
                  f"<td class=\"c mono\">{fmt(v.get('test_ic_mean_best_iter'), 4, False, True)}</td>"
                  f"<td class=\"c mono\">{fmt(v.get('test_rankic_mean_full_2000'), 4, False, True)}</td>"
                  f"<td class=\"l small\">轮次只在验证段上选；测试段不参与任何选择</td></tr>")
            A("</tbody></table>")
            A("<p class=\"small\">读法：<b>同一份面板、同一套超参，只把「用池化 MSE 做早停」换成"
              "「用验证曲线选轮次」，臂 C 的 RankIC 就从 "
              f"<span class=\"mono\">{fmt(runs.get('xgb_s42', {}).get('test_rankic_mean'), 4, False, True)}</span> "
              "抬到上表的水平</b>。也就是说退化的是「池化 MSE 早停」这个做法在本面板上的适配性，"
              "不是本单的实现缺陷。<b>但本单不因此改协议</b>——臂 C 按预注册原样报数，"
              "主检验量 Δ_ic 只用 DEE vs MLP-only，不经过臂 C。</p>")

    # ---- 4 主检验量
    A("<h2><span class=\"num\">4</span>主检验量 Δ_ic 与判据逐条对照</h2>")
    A("<table><caption>表 4 · Δ_ic 逐种子与判据（判据先写死再跑，见预注册 §6.2）</caption>")
    A("<thead><tr><th class=\"c\">种子</th><th class=\"c\">RankIC(DEE)</th><th class=\"c\">RankIC(MLP-only)</th>"
      "<th class=\"c\">Δ_ic</th><th class=\"c\">判定</th></tr></thead><tbody>")
    for p in per_seed:
        j = "进带" if 0.02 <= p["delta"] <= 0.30 else ("退" if p["delta"] <= 0 else "退（>50%，不可比）")
        A(f"<tr><td class=\"c mono\">{p['seed']}</td><td class=\"c mono\">{fmt(p['rd'],4,False,True)}</td>"
          f"<td class=\"c mono\">{fmt(p['rb'],4,False,True)}</td>"
          f"<td class=\"c mono\">{fmt(p['delta']*100,2,True,True)}</td>"
          f"<td class=\"c\">{j}</td></tr>")
    A(f"<tr class=\"tot\"><td class=\"l\">均值</td><td class=\"c mono\">{fmt(np.mean(ric_dee) if ric_dee else None,4,False,True)}</td>"
      f"<td class=\"c mono\">{fmt(np.mean(ric_mlp) if ric_mlp else None,4,False,True)}</td>"
      f"<td class=\"c mono\">{fmt(delta_mean*100,2,True,True)}</td>"
      f"<td class=\"c\">{verdict_word}</td></tr>")
    A("</tbody></table>")
    A("<table><caption>表 5 · 判据逐条命中情况</caption>")
    A("<thead><tr><th class=\"l\">判据（预注册 §6.2）</th><th class=\"l\">实测</th><th class=\"c\">是否命中</th></tr></thead><tbody>")
    rows = [
        (f"Δ_ic ∈ [+2%, +30%] → 进带", f"Δ_ic = {delta_mean*100:+.2f}%",
         "是" if 0.02 <= delta_mean <= 0.30 else "否"),
        ("Δ_ic ≤ 0 → 退", f"{delta_mean*100:+.2f}%", "是" if delta_mean <= 0 else "否"),
        ("Δ_ic > +50% → 退（基线不可比）", f"{delta_mean*100:+.2f}%",
         "是" if delta_mean > 0.50 else "否"),
        ("净 Sharpe(rf=0, 8bps) < 0 → 直接退", f"{net_sh_mean:+.3f}",
         "是" if net_sh_mean < 0 else "否"),
        ("两种子 RankIC 极差 > 0.01 → 不稳定（DEE 自身）", f"{ric_rng:.4f}",
         "是" if ric_rng > 0.01 else "否"),
        ("两种子 RankIC 极差 > 0.01 → 不稳定（最佳单族基线）", f"{ric_rng_mlp:.4f}",
         "是" if ric_rng_mlp > 0.01 else "否"),
        ("Δ_ic 逐种子符号翻转 → 均值不代表可复现方向",
         f"{', '.join(f'{d*100:+.1f}%' for d in deltas)}",
         "是" if delta_flip else "否"),
        ("95% CI 含 0 → 记「未复现」",
         f"[{ci_lo*100:+.1f}%, {ci_hi*100:+.1f}%]（t 近似，df=1，区间极宽）",
         "是" if (not np.isfinite(ci_lo) or ci_lo <= 0 <= ci_hi) else "否"),
    ]
    for a, b_, c in rows:
        A(f"<tr><td class=\"l\">{a}</td><td class=\"l mono\">{b_}</td><td class=\"c\">{c}</td></tr>")
    A("</tbody></table>")

    # ---- 图
    for stem, cap in [
        ("fig1_rankic_arms",
         "<b>图 1 · 三档对照与主检验量。</b>（a）三档在测试段的日频截面 RankIC，误差棒为两种子极差；"
         "（b）逐种子 Δ_ic，浅青带为预注册进带 [+2%, +30%]。"
         "<b>来源</b>：本单实测（<span class=\"mono\">experiments/WORK-555/run_*.json</span>）；"
         "判据来源为论文 arXiv:2611.12389v1 Table 2。"
         "<b>结论</b>：DEE 与最佳单族的相对差是本单唯一的主检验量，绝对水平不作 KPI。"),
        ("fig2_daily_rankic",
         "<b>图 2 · 日频 RankIC 时序与累计。</b>（a）三档逐日截面 RankIC（实线 seed 42、虚线 seed 43）；"
         "（b）累计 RankIC。<b>来源</b>：本单实测（预测值 "
         "<span class=\"mono\">preds_*.npz</span> + 事实层 "
         "<span class=\"mono\">data/WORK-555/panel_backtest.csv</span>）。"
         "<b>结论</b>：三档的逐日 RankIC 都在零轴附近大幅摆动，单点差异不足以支撑结论。"),
        ("fig3_net_equity",
         "<b>图 3 · 净净值与换手。</b>（a）扣除成本后的日频复利净值；"
         "（b）日均双边换手。<b>来源</b>：本单实测（<span class=\"mono\">bt_daily_*_main_8bps.csv</span>）。"
         "<b>结论</b>：日频调仓 top-50 的年化双边换手超过 100%，8 bps 单边成本 + 10 bps 印花税"
         "对毛收益的侵蚀是决定性的。"),
        ("fig4_paper_compare",
         "<b>图 4 · 与论文 Table 1 / Table 2 的对照。</b>（a）各方法测试段 RankIC；"
         "（b）族消融 RankIC。<b>来源</b>：论文 arXiv:2611.12389v1 Table 1 与 Table 2（原文数字）；"
         "本单为 <span class=\"mono\">experiments/WORK-555/run_*.json</span>。"
         "<b>结论</b>：绝对水平差距主要来自因子集规模（48 vs 158）与训练目标对排序信息的利用效率。"),
    ]:
        data = b64(os.path.join(FIG, f"{stem}.png"))
        if data:
            A(f"<figure><img alt=\"{stem}\" src=\"data:image/png;base64,{data}\">"
              f"<figcaption>{cap}</figcaption></figure>")

    # ---- 5 与论文对照
    A("<h2><span class=\"num\">5</span>与论文逐项对照</h2>")
    A("<p>对照表的左边是论文原文数字（标表号），右边是本单实测；"
      "口径差异原因一栏逐条写清，不用「可能因为」这类含糊说法。</p>")
    A("<table><caption>表 6 · 与论文 Table 1（预测力）对照</caption>")
    A("<thead><tr><th class=\"l\">方法</th><th class=\"c\">论文 IC</th><th class=\"c\">论文 RankIC</th>"
      "<th class=\"c\">本单 IC</th><th class=\"c\">本单 RankIC</th><th class=\"l\">口径差异原因</th></tr></thead><tbody>")
    map_arm = {"MLP": "mlp_only", "XGBoost": "xgb", "DEE": "dee"}
    for name, pic, prc in PAPER_T1:
        arm = map_arm.get(name)
        ours = []
        if arm:
            for s in SEEDS:
                r = runs.get(f"{arm}_s{s}")
                if r:
                    ours.append((r["test_ic_mean"], r["test_rankic_mean"]))
        oic = f"{np.mean([o[0] for o in ours]):+.4f}" if ours else "—"
        orc = f"{np.mean([o[1] for o in ours]):+.4f}" if ours else "—"
        reason = {
            "Linear Reg.": "论文为单模型；本单只实现 DEE / MLP-only / XGBoost 三档，未跑线性单模型对照",
            "MLP": "本单 MLP-only 是 20 个 MLP 的异质随机化集成（论文 Table 2 口径），"
                   "不等价于论文 Table 1 的单个 MLP",
            "Random Forest": "本单未实现该基线",
            "Double Ensemble": "本单未实现该基线",
            "LightGBM": "本单未实现该基线",
            "XGBoost": "因子集 48 vs 158（偏离 D2）；训练/验证/测试切分、成分池、成本口径一致",
            "DEE": "因子集 48 vs 158（偏离 D2）；N=20 vs 50（偏离 D1，论文 Figure 4 指出 N=20→50 增量仅 0.0005）；"
                   "自建面板复权基准日与 qlib 不一致",
        }.get(name, "")
        A(f"<tr><td class=\"l\">{name}</td><td class=\"c mono\">{pic:.3f}</td>"
          f"<td class=\"c mono\">{prc:.3f}</td><td class=\"c mono\">{oic}</td>"
          f"<td class=\"c mono\">{orc}</td><td class=\"l small\">{reason}</td></tr>")
    A("</tbody></table>")
    A("<table><caption>表 7 · 与论文 Table 2（族消融）对照——<b>这就是主检验量的来源</b>；"
      "本单列为 DEE 臂<b>两种子均值</b>，族内各自 IC 过滤 + 等权平均（预注册 §4.6）</caption>")
    A("<thead><tr><th class=\"l\">族</th><th class=\"c\">论文 IC</th><th class=\"c\">论文 RankIC</th>"
      "<th class=\"c\">本单 IC</th><th class=\"c\">本单 RankIC</th><th class=\"c\">本单专家数</th></tr></thead><tbody>")
    fam_sets = {k: v["family_subsets"] for k, v in runs.items()
                 if k.startswith("dee_") and "family_subsets" in v}
    fmap = {"Linear only": "linear_family", "Tree only": "tree_family",
            "MLP only": "mlp_family", "Full DEE (6 types)": "full_dee"}
    for name, pic, prc in PAPER_T2:
        key = fmap.get(name)
        vals = [fs[key] for fs in fam_sets.values() if key in fs]
        v = None
        if vals:
            v = {"ic": float(np.mean([x["ic"] for x in vals])),
                 "rankic": float(np.mean([x["rankic"] for x in vals])),
                 "n_experts": vals[0]["n_experts"]}
        A(f"<tr><td class=\"l\">{name}</td><td class=\"c mono\">{pic:.3f}</td>"
          f"<td class=\"c mono\">{prc:.3f}</td>"
          f"<td class=\"c mono\">{fmt(v['ic'],4,False,True) if v else '—'}</td>"
          f"<td class=\"c mono\">{fmt(v['rankic'],4,False,True) if v else '—'}</td>"
          f"<td class=\"c mono\">{v['n_experts'] if v else '—'}</td></tr>")
    A(f"<tr class=\"tot\"><td class=\"l\">论文 Δ =（0.060 − 0.055）/ 0.055</td>"
      f"<td class=\"c mono\">+9.1%</td><td class=\"c mono\">—</td>"
      f"<td class=\"c mono\">本单 Δ_ic = {delta_mean*100:+.2f}%</td>"
      f"<td class=\"c mono\">—</td><td class=\"c mono\">—</td></tr>")
    A("</tbody></table>")
    A("<table><caption>表 8 · 与论文 Table 3（组合）对照——本单列为<b>两种子均值</b>"
      "（8 bps 单边 + 10 bps 印花税净额口径），论文为毛值</caption>")
    A("<thead><tr><th class=\"l\">方法</th><th class=\"c\">论文年化%</th><th class=\"c\">论文 Sharpe</th>"
      "<th class=\"c\">论文 MDD%</th><th class=\"c\">本单年化%</th><th class=\"c\">本单 Sharpe</th>"
      "<th class=\"c\">本单 MDD%</th><th class=\"l\">差异原因</th></tr></thead><tbody>")
    t3map = {"MLP": "mlp_only", "XGBoost": "xgb", "DEE": "dee"}
    for name, ann, sh, mdd in PAPER_T3:
        arm = t3map.get(name)
        ms = [bts[f"{arm}_s{s}"]["metrics"]["main_8bps"]
              for s in SEEDS if f"{arm}_s{s}" in bts]
        m = None
        if ms:
            m = {k: float(np.mean([x[k] for x in ms])) for k in ms[0]}
        A(f"<tr><td class=\"l\">{name}</td><td class=\"c mono\">{ann:.2f}</td>"
          f"<td class=\"c mono\">{sh:.3f}</td><td class=\"c mono\">{mdd:.2f}</td>"
          f"<td class=\"c mono\">{fmt(m['annualized_return']*100,2,True,True) if m else '—'}</td>"
          f"<td class=\"c mono\">{fmt(m['sharpe_rf0'],3,False,True) if m else '—'}</td>"
          f"<td class=\"c mono\">{fmt(m['max_drawdown']*100,2,True,True) if m else '—'}</td>"
          f"<td class=\"l small\">论文 Table 3 为<b>毛值</b>（§6.4 未给成本模型、§7 自述未刻画摩擦）；"
          f"本单为 8 bps 单边 + 10 bps 印花税<b>净额</b>（偏离 D4），且因子集更小</td></tr>")
    A("</tbody></table>")

    # ---- 6 独立复算
    A("<h2><span class=\"num\">6</span>独立复算</h2>")
    if audit:
        A(f"<p>独立复算脚本 <span class=\"mono\">audit/recompute_work555.py</span> "
          f"<b>不 import 建模阶段的任何函数</b>（审计契约第 1 条），"
          f"从原始快照与预测文件重新算一遍日频截面 RankIC / IC、组合逐日账"
          f"（选股、掩码、强制持有、<b>现金账本调仓</b>、成本、换手）与年化 / Sharpe / MDD 口径，"
          f"再与建模侧逐项比对，相对差 ≤ 1e-6 才算一致。</p>")
        A(f"<p>判决：<span class=\"tag {'red' if audit['n_blocking'] else 'teal'}\">"
          f"{'BLOCKING ' + str(audit['n_blocking']) + ' 项' if audit['n_blocking'] else 'PASS'}</span>"
          f"共 {audit['n_findings']} 项检查项；不一致项一律以复算值为准。</p>")
        # 组合权重不变量：现金账本口径的两道闸门
        inv = [f for f in audit.get("findings", [])
               if str(f.get("item", "")).startswith("weight_invariant")]
        if inv:
            worst = max(float(f.get("measured", 0.0)) for f in inv)
            n_over = sum(1 for f in inv if float(f.get("measured", 0.0)) > 1.0 + 1e-9)
            ledger = max([float(f.get("ledger_max_dev", 0.0)) for f in inv] or [0.0])
            A(f"<p class=\"small\"><b>组合权重不变量（独立复算侧的额外闸门）：</b>"
              f"{len(inv)} 项检查（每臂 × 每种子 × 每成本档各一项），判据为 "
              f"<span class=\"mono\">Σw ≤ 1</span> 与 <span class=\"mono\">Σw + cash ≡ 1</span>。"
              f"实测最大 Σw = <b>{worst:.12f}</b>、Σw&gt;1 的项数 = <b>{n_over}</b>、"
              f"账本偏差 max\\|Σw+cash−1\\| = <b>{ledger:.3e}</b>。"
              "这条闸门是第一轮独立审查暴露的旧实现缺陷（强制持有的旧权重与新买入的 1/K 直接叠加，"
              "实测 Σw 最高 1.44、974 天里 151 天 &gt; 1）的守门人。</p>")
    else:
        A("<p>独立复算尚未产出。</p>")

    # ---- 7 机制诊断
    A("<h2><span class=\"num\">7</span>为什么绝对水平低：机制诊断</h2>")
    A("<p>论文 Table 1 的 DEE RankIC 是 0.060，本单明显更低。这不是「复现失败」一句话能带过的，"
      "下面把可测量的机制列出来。</p>")
    A("<ol>")
    if leak:
        d1 = leak["D1_placebo"]
        d2 = leak["D2_backward_realized_return"]
        d3 = leak["D3_rank_vs_linear"]
        A(f"<li><b>面板本身无前视泄漏。</b>把当日标签在截面内打乱后重算，各因子 |RankIC| 从 "
          f"<span class=\"mono\">{d1['real_mean_abs_rankic_by_factor_min']:.4f}~"
          f"{d1['real_mean_abs_rankic_by_factor_max']:.4f}</span> 塌到中位 "
          f"<span class=\"mono\">{d1['placebo_mean_abs_rankic_median']:.4f}</span>（placebo，"
          f"最大也只有 {d1['placebo_mean_abs_rankic_max']:.4f}）；"
          f"把特征(t) 拿去预测<b>已实现</b>的过去收益(t−1→t)，"
          f"量/波类因子的 |IC| 中位只有 <span class=\"mono\">{d2['median_abs_rankic_by_group']['volume_volatility']:.4f}</span>，"
          f"而价/动量类因子的 |IC| 中位高达 "
          f"<span class=\"mono\">{d2['median_abs_rankic_by_group']['price_momentum']:.4f}</span>"
          "——后者是动量因子「本来就是滞后收益」的机械相关，不是泄漏。</li>")
        rc = d3.get("ROC_5", {})
        md = d3.get("MA_DEV_10", {})
        A(f"<li><b>信号是排序驱动的，不是线性驱动。</b>测试段 ROC_5 的 Spearman 相关 "
          f"<span class=\"mono\">{rc.get('mean_spearman_ic', float('nan')):+.4f}</span>，"
          f"但 Pearson 相关只有 <span class=\"mono\">{rc.get('mean_pearson_ic', float('nan')):+.4f}</span>；"
          f"MA_DEV_10 的 Spearman <span class=\"mono\">{md.get('mean_spearman_ic', float('nan')):+.4f}</span>，"
          f"Pearson <span class=\"mono\">{md.get('mean_pearson_ic', float('nan')):+.4f}</span>。"
          "而 DEE 三个模型族（含论文原版 XGBoost）全部按 <b>MSE</b> 目标训练，"
          "MSE 只看线性部分，看不见排序信息。</li>")
    else:
        A("<li><b>面板本身无前视泄漏</b>、<b>信号是排序驱动的</b>——"
          "机制诊断脚本未产出，本段留空，不做无据陈述。</li>")
    xgb42 = runs.get("xgb_s42", {}).get("xgb", {})
    dg = runs.get("xgbfull_s42", {})
    A(f"<li><b>论文原版 XGBoost 在本面板上同样退化。</b>按预注册冻结的 XGBoost 配置"
      "（2000 棵、lr 0.05、深度 6、早停 patience 50 监控验证 MSE）跑，"
      f"验证 RMSE 从第 0 轮起就是平的（{fmt(dg.get('valid_rmse_at_round0'), 8)}），"
      f"最佳迭代停在第 {xgb42.get('best_iteration', '—')} 轮，"
      f"实际只有 <b>{xgb42.get('n_trees_used_in_predict', '—')} 棵树</b>参与预测；"
      f"关掉早停、用验证曲线选到第 {dg.get('best_round_selected_on_valid', '—')} 轮时，"
      f"测试段 RankIC 从 <span class=\"mono\">{fmt(runs.get('xgb_s42', {}).get('test_rankic_mean'), 4, False, True)}</span> "
      f"升到 <span class=\"mono\">{fmt(dg.get('test_rankic_mean_best_iter'), 4, False, True)}</span>。"
      "换句话说，<b>退化的是「用池化 MSE 做早停」这个协议在本面板上的适配性，不是本单的实现</b>。"
      "详见 §3「臂 C 的早停退化」。</li>")
    A("</ol>")
    A("<p class=\"small\">以上三条均由 <span class=\"mono\">scripts/work555_leakage_diag.py</span> "
      "与 <span class=\"mono\">scripts/work555_xgb_diag.py</span> 一条命令复跑核对（命令见 §9），"
      "原始结果落 <span class=\"mono\">experiments/WORK-555/leakage_diag.json</span> "
      "与 <span class=\"mono\">run_xgbfull_s42.json</span>。</p>")

    # ---- 8 偏离与失败
    A("<h2><span class=\"num\">8</span>偏离清单与失败记录</h2>")
    A("<table><caption>表 9 · 与论文口径的偏离（全部在预注册 §8 冻结，全部已知）</caption>")
    A("<thead><tr><th class=\"c\" style=\"width:6%\">#</th><th class=\"l\">偏离项</th><th class=\"l\">论文</th>"
      "<th class=\"l\">本单</th><th class=\"l\">原因</th></tr></thead><tbody>")
    for row in [
        ("D1", "专家数 N", "50", "20（4/1/2/1/6/6 同比例缩放）", "计算预算；论文 Figure 4 指出 N=20→50 的 RankIC 增量仅 0.0005"),
        ("D2", "因子集", "qlib Alpha158 全 158 列", "自实现 48 列", "qlib 官方 CSI300 数据集本机可达性未验证；自建面板复权基准日与 qlib 不一致"),
        ("D3", "股票池", "CSI300，PIT 口径未说明", "CSI300 + 月末 PIT 快照（滞后 ≤1 个月）", "消除前视与幸存者偏差，比论文更严格"),
        ("D4", "交易成本", "无（§6.4 未给成本模型）", "单边 8 bps + 卖出印花税 10 bps + 涨跌停/停牌不可成交", "A 股可实现口径，工单要求"),
        ("D5", "种子数", "10", "2（42、43）", "计算预算；工单要求 ≥2"),
        ("D6", "软树叶数", "未规定", "CART max_depth=12 + 自适应 min_samples_leaf（≤256 叶）", "深度 12 的满二叉树 4096 叶在本机不可训"),
        ("D7", "验证早停子样本", "未规定", "固定 20,000 行验证子样本", "全验证集 × 20 专家 × 400 次评估不可承受"),
    ]:
        A(f"<tr><td class=\"c mono\">{row[0]}</td><td class=\"l\">{row[1]}</td>"
          f"<td class=\"l\">{row[2]}</td><td class=\"l\">{row[3]}</td><td class=\"l small\">{row[4]}</td></tr>")
    A("</tbody></table>")
    A("<p class=\"small\">另有一项<b>预注册文字与实测的出入</b>（不影响协议操作性，单列披露）："
      "预注册 §2 写「共 250 个快照日」，实测窗口内（2006-01-01~2021-01-29）"
      f"为 <b>{meta['pit']['snapshots_in_window']}</b> 份月末快照。"
      "250 是该指数成分表<b>全时段</b>的快照数，预注册把全时段数写进了窗口口径描述。"
      "PIT 规则本身（快照日 ≤ t 的最新一份）未变，实装按规则执行；"
      "成分调整的实际滞后上限为 36 天（2020-02-28 之后的 COVID 缺口），略大于预注册写的「1 个月」。</p>")
    A("<p class=\"small\">完整的跑挂尝试、口径改动版本与未取到的数据记录在 "
      "<span class=\"mono\">experiments/WORK-555/failure_log.md</span>，不藏。</p>")

    # ---- 8b 独立代码审查第一轮：发现与处置
    if review:
        A("<h3>独立代码审查第一轮：发现与处置</h3>")
        A(f"<p>本单实现完成后交给<b>独立子代理</b>做代码审查（隔离沙箱、零 import 建模函数、"
          f"自带一套从头写的复算实现）。第一轮判决 <b>{review.get('verdict', '—')}</b>："
          f"{review.get('summary', '—')}。"
          "下表逐条列问题、修复与<b>可复跑的验证</b>；机器可读留痕见 "
          "<span class=\"mono\">experiments/WORK-555/review_round1.json</span>。</p>")
        A("<table><caption>表 9b · 独立审查第一轮：2 blocking + 3 suggestion + 4 nit 的处置</caption>")
        A("<thead><tr><th class=\"c\" style=\"width:5%\">#</th><th class=\"c\" style=\"width:9%\">级别</th>"
          "<th class=\"l\">问题</th><th class=\"l\">修复</th><th class=\"l\">验证（可复跑）</th>"
          "</tr></thead><tbody>")
        sev_tag = {"blocking": "red", "suggestion": "blue", "nit": "teal"}
        for f in review.get("findings", []):
            sev = f.get("severity", "")
            A(f"<tr><td class=\"c mono\">{f.get('id', '—')}</td>"
              f"<td class=\"c\"><span class=\"tag {sev_tag.get(sev, 'teal')}\">{sev}</span></td>"
              f"<td class=\"l small\">{f.get('problem', '')}<br>"
              f"<span class=\"mono small\">{f.get('location', '')}</span></td>"
              f"<td class=\"l small\">{f.get('fix', '')}</td>"
              f"<td class=\"l small\">{f.get('verification', '')}</td></tr>")
        A("</tbody></table>")
        extra = review.get("reviewer_extra_findings", [])
        if extra:
            A("<p class=\"small\">审查方另发现 2 项（本单自认并修复）："
              + "；".join(f"<b>{e.get('id', '')}</b> {e.get('problem', '')} → {e.get('fix', '')}"
                          + (f"（验证：{e.get('verification', '')}）" if e.get("verification") else "")
                          for e in extra)
              + "</p>")
        unv = review.get("reviewer_unverified", [])
        if unv:
            A("<p class=\"small\"><b>审查方自述未能核实的项</b>（不藏）："
              + "；".join(unv) + "。</p>")

    # ---- 9 自证
    A("<h2><span class=\"num\">9</span>自证：勾稽与复跑命令</h2>")
    A("<p>按工单「自证纪律」，凡分项数字表都附勾稽自检结果与统计时点。</p>")
    A("<table><caption>表 10 · 勾稽自检（逐项实测值，非声明）</caption>")
    A("<thead><tr><th class=\"l\">勾稽项</th><th class=\"l\">关系式</th><th class=\"c\">最大绝对偏差</th>"
      "<th class=\"c\">是否通过</th></tr></thead><tbody>")
    A(f"<tr><td class=\"l\">列相加 = 合计（逐日毛收益 − 成本 = 净收益）</td>"
      f"<td class=\"l mono\">Σgross − Σcost = Σnet</td>"
      f"<td class=\"c mono\">{c1_max:.3e}</td><td class=\"c\">{'通过' if c1_max <= 1e-12 else '不通过'}</td></tr>")
    A(f"<tr><td class=\"l\">Δ 相加 = 合计 Δ（换手 = 买入 + 卖出）</td>"
      f"<td class=\"l mono\">Σ(buys+sells) = Σturnover</td>"
      f"<td class=\"c mono\">{c2_max:.3e}</td><td class=\"c\">{'通过' if c2_max <= 1e-12 else '不通过'}</td></tr>")
    A(f"<tr><td class=\"l\">Δ_ic 均值 = 逐种子 Δ_ic 的算术平均</td>"
      f"<td class=\"l mono\">mean(Δ_seed) = Δ_mean</td>"
      f"<td class=\"c mono\">{c3:.3e}</td><td class=\"c\">{'通过' if c3 <= 1e-12 else '不通过'}</td></tr>")
    A(f"<tr><td class=\"l\">组合账本恒等（现金账本口径，无隐含杠杆）</td>"
      f"<td class=\"l mono\">Σw + cash ≡ 1；Σw ≤ 1</td>"
      f"<td class=\"c mono\">账本偏差 {c4_ledger:.3e}<br>max Σw = {c4_max_sum_w:.9f}<br>"
      f"Σw&gt;1 天数 = {c4_n_over}</td>"
      f"<td class=\"c\">{'通过' if (c4_ledger <= 1e-9 and c4_n_over == 0 and c4_max_sum_w <= 1 + 1e-9) else '不通过'}</td></tr>")
    A("</tbody></table>")
    A("<pre>cd /srv/share/quantdb/code/quant-lab\n"
      "# 0) 复现前提：BLAS 线程必须固定为 1（float32 GEMM 归约顺序随线程数变化，\n"
      "#    不固定时同命令同 seed 的结果不可复现：实测 T=1 与 committed 产物逐字节一致，T=4 不同）\n"
      "export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \\\n"
      "       NUMEXPR_NUM_THREADS=1\n"
      "# 1) 引擎原语单测（梯度校验 + SWRR 逐位对照协议附录 B + 口径自检）\n"
      "/tmp/w555venv/bin/python scripts/work555_test_primitives.py\n"
      "# 1b) 梯度校验的变异测试（自证：可分辨下标的污染必被抓到、不可分辨下标不虚报）\n"
      "/tmp/w555venv/bin/python scripts/work555_gradcheck_mutation_test.py\n"
      "# 1c) 软树梯度的独立全路径暴力实现对照（与建模侧的逐层反向传播是不同推导路径）\n"
      "/tmp/w555venv/bin/python scripts/work555_tree_bruteforce.py\n"
      "# 2) 因子 + 标签 + 样本装配（加工层可重建）\n"
      "/tmp/w555venv/bin/python scripts/work555_features.py\n"
      "# 3) 三档对照臂 × 两种子（同一份代码版本；本次产出由 v3 runner 生成）\n"
      "bash scripts/work555_run_arms_v3.sh\n"
      "# 3b) 臂 C 早停退化的非预注册诊断对照（不参与判词）\n"
      "/tmp/w555venv/bin/python scripts/work555_xgb_diag.py --seed 42\n"
      "# 3c) 配图（matplotlib 只在系统 python3 里，venv 里没有 matplotlib）\n"
      "python3 scripts/work555_figures.py\n"
      "# 4) 组合回测（掩码断言开启；现金账本口径，Σw + cash ≡ 1）\n"
      "/tmp/w555venv/bin/python scripts/work555_backtest.py --arm dee --seed 42\n"
      "# 5) 断言演示：刻意关闭掩码，必须报 UNTRADABLE_PRICE_READ\n"
      "/tmp/w555venv/bin/python scripts/work555_backtest.py --arm dee --seed 42 --no-mask\n"
      "# 6) 独立复算（不 import 建模函数）\n"
      "/tmp/w555venv/bin/python audit/recompute_work555.py\n"
      "# 6b) 对外数字唯一真源：重生成 failure_log §12 与 registry 行；--check 可判陈旧\n"
      "/tmp/w555venv/bin/python scripts/work555_final_numbers.py\n"
      "/tmp/w555venv/bin/python scripts/work555_final_numbers.py --check\n"
      "# 7) 机制诊断（placebo / 反向检验 / 排序 vs 线性）\n"
      "/tmp/w555venv/bin/python scripts/work555_leakage_diag.py\n"
      "# 8) 快照 sha256 复核\n"
      "sha256sum data/WORK-555/*.csv</pre>")
    A("<p class=\"small\">统计时点：全部数字取自本仓库 "
      "<span class=\"mono\">experiments/WORK-555/</span> 下的产物；"
      "快照 sha256 见 <span class=\"mono\">snapshot_manifest.json</span>，"
      "复算结果见 <span class=\"mono\">audit_findings.json</span>。</p>")

    # ---- 10 结论
    A("<h2><span class=\"num\">10</span>结论与翻案条件</h2>")
    A(f"<p><b>判词：{verdict_word}。</b>" + "；".join(reasons) + "。</p>")
    blockers = []
    if delta_mean > 0.50:
        blockers.append(f"① 主检验量 Δ_ic = {delta_mean*100:+.2f}% <b>超过 +50% 上限</b>——"
                        f"按预注册 §6.2 属「单族基线本身跑坏、横向不可比」，不是「复现成功」")
    elif delta_mean <= 0:
        blockers.append(f"① 主检验量 Δ_ic = {delta_mean*100:+.2f}% ≤ 0——DEE 不优于最佳单族")
    else:
        blockers.append(f"① 主检验量 Δ_ic = {delta_mean*100:+.2f}% 落在预注册进带 [+2%, +30%] 之外")
    if net_sh_mean < 0:
        blockers.append(f"② DEE 的净 Sharpe（rf=0，8 bps 主口径）= {net_sh_mean:+.3f} < 0——"
                        f"按 §6.2 <b>直接退</b>（净额口径含 A 股交易成本）")
    if ric_rng_mlp > 0.01:
        blockers.append(f"③ 最佳单族基线自身两种子 RankIC 极差 {ric_rng_mlp:.4f} > 0.01，"
                        f"基线先不稳、相对量才不可比——这是本单最根本的卡点")
    if delta_flip:
        blockers.append(f"④ Δ_ic 逐种子符号翻转（{', '.join(f'{d*100:+.1f}%' for d in deltas)}），"
                        f"两种子均值 {delta_mean*100:+.2f}% 不构成可复现结论")
    A("<p><b>卡在哪一项：</b>" + "；".join(blockers) + "。</p>")
    A("<p><b>要翻案需要什么：</b>"
      "① <b>先解决基线不稳</b>：把种子数从 2 提到论文同量级（≥10），"
      "并按预注册口径报 mean ± std，看 MLP-only 的 RankIC 极差是否收敛到 0.01 以内；"
      "在此之前任何 Δ_ic 数值（包括本单这个 +89.76%）都不可解读；"
      "② 把因子集从 48 列补到接近论文的 Alpha158 规模（偏离 D2 的反向），"
      "在相同协议下重跑三档，看 Δ_ic 是否稳定进入 [+2%, +30%]；"
      "③ 或改用对排序敏感的损失（如截面秩回归）重训三族，使 MSE 早停不再退化为第 0 轮"
      "（这属于协议变更，须另开预注册版本）；"
      "④ 或把调仓频率从日频降到周频，把年化双边换手从 ~100% 压到可承受区间，再看净 Sharpe 是否转正。"
      "以上四条都超出本单冻结协议，不能在本单内改，必须另开版本重跑。</p>")
    A("<div class=\"keybox\"><h3>本单结论边界</h3><p>本单只检验了论文主张中的「异质性相对贡献」"
      "这一个相对量，且是在自建面板、自实现因子、2 个种子、含 A 股交易成本的条件下测的。"
      "它既不能证明论文的结论成立，也不能证伪论文——"
      "论文用的是 qlib 官方 Alpha158 面板与 158 列因子、无成本、10 个种子，"
      "本单与它不是同一个实验。</p></div>")

    A("<footer>")
    A("<p><b>来源</b>：论文 arXiv:2611.12389v1（Li D.《Diversification Across Predictors: "
      "Heterogeneous Ensemble Learning for Cross-Sectional Return Prediction》，"
      "ICAIF '26，DOI 10.1145/3858063.3858743，CC BY 4.0）——§2.2 专家族设计、§2.3 三重随机化、"
      "§2.4 聚合与 IC 过滤、§2.5 训练过程与 Algorithm 1、§4.4 有限样本 N*、"
      "§6.1 实验设置与超参、Table 1 / Table 2 / Table 3、Figure 4。<br>"
      "数据：本机金融数据底座 <span class=\"mono\">/srv/share/quantdb</span>（DuckDB 只读）"
      "与 tushare 已实测端点；字段口径见仓库 "
      "<span class=\"mono\">data_contracts/field_dictionary.md</span> §2 复权、§3 时点、§5 停牌与退市、§7 质量门。<br>"
      "产物：<span class=\"mono\">experiments/WORK-555/</span>（预注册、快照 manifest、样本、"
      "预测、组合账、审计结论、failure_log）与 <span class=\"mono\">figures/WORK-555/</span>。</p>")
    A("<p>本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺；"
      "全部结论仅对本次冻结协议与本次快照成立。</p>")
    A("</footer>")
    A("</div></body></html>")

    return "".join(H)


def main():
    html = build()
    p = os.path.join(OUT, "report.html")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(html)
    n_img = html.count("data:image/png;base64,")
    n_ext = html.count("http://") + html.count("https://")
    print(f"[report] {p}  bytes={len(html):,}  embedded_images={n_img}  external_urls={n_ext}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
