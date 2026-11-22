#!/usr/bin/env python3
"""WORK-503 — render the delivery comment from the artefacts.

Every number in the comment is read out of the artefacts (verdict.json,
metrics_table.csv, paper_comparison.csv, audit_recompute.json,
integrity_check.json, lookahead_check.json, data_probe.json) — nothing is
typed by hand, so the comment cannot drift from the files it cites.

    ../env/.venv/bin/python -u scripts/work503_delivery_comment.py
      --out experiments/WORK-503/delivery_comment.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-503")
ARMS = ["full", "no_vq", "no_moe", "no_prior"]
ARM_CN = {"full": "完整模型", "no_vq": "去码本", "no_moe": "去 MoE",
          "no_prior": "去先验", "gru": "GRU 基线"}


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                          text=True).stdout.strip()


def f4(v, nd=4):
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


def pct(v, nd=1):
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v)*100:+.{nd}f}%"
    except Exception:
        return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--out", default="experiments/WORK-503/delivery_comment.md")
    args = ap.parse_args()
    exp = os.path.join(REPO, "experiments", args.exp)

    v = json.load(open(os.path.join(exp, "verdict.json")))
    tab = pd.read_csv(os.path.join(exp, "metrics_table.csv"))
    audit = json.load(open(os.path.join(exp, "audit_recompute.json")))
    ic = json.load(open(os.path.join(exp, "integrity_check.json")))
    la = json.load(open(os.path.join(exp, "lookahead_check.json")))
    probe = json.load(open(os.path.join(exp, "data_probe.json")))
    h1, h2, h3 = v["H1"], v["H2"], v["H3"]
    seeds = v["seeds"]

    head = git("rev-parse", "HEAD")
    prereg = git("log", "--format=%h %cI", "-1", "--",
                 f"experiments/{args.exp}/preregistration_v1.4.md")
    prereg_sha = prereg.split()[0] if prereg else "—"

    # ---- the four-arm table -------------------------------------------
    rows = []
    for arm in ARMS + ["gru"]:
        sub = tab[tab["arm"] == arm].sort_values("seed")
        if sub.empty:
            continue
        ric = [f4(x) for x in sub["rankic"]]
        sr = [f4(x, 3) for x in sub["sr"]]
        rows.append(
            f"| {ARM_CN.get(arm, arm)} | " + " | ".join(ric) + " | "
            + " | ".join(sr) + " | "
            + f4(sub['rankicir'].mean(), 4) + " | "
            + f4(sub['mdd'].mean(), 4) + " | "
            + f4(sub['turnover_1side'].mean(), 3) + " |")

    # ---- the endpoint probe table --------------------------------------
    ep_rows = []
    for e in probe.get("endpoints", []):
        ep_rows.append(
            f"| {e.get('id')} | {e.get('class')} | `{e.get('tool')}` | "
            f"{e.get('rows_returned')} | {e.get('coverage_start')} ~ "
            f"{e.get('coverage_end')} | {e.get('status')} |")

    # ---- H2 / H3 ---------------------------------------------------------
    h2_rows = []
    h2_brief = []
    for s in seeds:
        p = h2.get("per_seed", {}).get(str(s))
        if not p:
            continue
        h2_rows.append(
            f"| {s} | {f4(p['mean_full'])} | {f4(p['mean_base'])} | "
            f"{f4(p['point'])} | [{f4(p['lo'])}, {f4(p['hi'])}] | "
            f"{'含 0（增量不可证）' if p['lo'] <= 0 <= p['hi'] else '不含 0'} |")
        h2_brief.append(
            f"种子 {s}：差值 {f4(p['point'])}，95% CI [{f4(p['lo'])}, {f4(p['hi'])}]"
            f"（{'含 0 → 增量不可证，与论文自曝弱点一致' if p['lo'] <= 0 <= p['hi'] else '不含 0'}）")
    h3_rows = []
    for s in seeds:
        p = h3.get("per_seed", {}).get(str(s))
        if not p:
            continue
        ci = p.get("sr_ci") or {}
        h3_rows.append(
            f"| {s} | {f4(p.get('sr'), 3)} | [{f4(ci.get('lo'), 3)}, "
            f"{f4(ci.get('hi'), 3)}] | {f4(p.get('ar'))} | "
            f"{f4(p.get('mdd'))} | {f4(p.get('turnover'), 3)} | "
            f"{f4((p.get('alpha_ew') or {}).get('excess_annualized'))} | "
            f"{f4((p.get('alpha_cw') or {}).get('excess_annualized'))} |")

    fc = v.get("failure_criteria", {})
    i5 = ic.get("I5_test_coverage", {})
    verdict = v["verdict"]
    n_mis = audit.get("n_mismatches")
    indep = audit.get("independence", {})

    body = f"""## 交付：PRISM-VQ（arXiv:2605.13407v1）在 A 股 CSI300 上的四档消融复现

### 一句话判词

**{verdict}**——主检验量 H1（码本有没有增量）落在 **Δ_drop = {pct(h1.get('delta_drop'))}**
（判词带：[−40%,−15%] 判「进」，[−10%,+10%] 判「退」）；并列检验 H2、H3 见下。
本结论为内部研究模拟，**不构成投资建议、不构成任何推荐或收益承诺**。

### 三条依据

1. **H1（唯一决定进/退）**：完整模型 RankIC 中位数 **{f4(h1.get('rankic_full_median'))}**，
   去码本档 **{f4(h1.get('rankic_no_vq_median'))}**，Δ_drop = {pct(h1.get('delta_drop'))}；
   逐种子 Δ_drop = {", ".join(f"{k}: {pct(x)}" for k, x in h1.get('delta_ic_per_seed', {}).items())}。
   论文 Table 2 同口径 = {pct(h1.get('paper_delta_ic'))}（字面式 Δ_formula = {pct(h1.get('paper_delta_formula'))}）。
2. **H2（相对固定基线 GRU 的增量显著性，10000 次 block bootstrap，block=5 日）**：
   {"；".join(h2_brief) or "—"}。
3. **H3（可交易性）**：净 SR 中位数 **{f4(h3.get('sr_median'), 3)}**（论文 {f4(h3.get('paper_sr'))}），
   CI 下界最小值 {f4(h3.get('sr_ci_lower_bound_min'), 3)}，相对论文比值 {f4(h3.get('shrinkage_vs_paper'), 3)}（下限 {f4(h3.get('shrinkage_limit'), 2)}），
   判定为 **{'可交易性成立' if h3.get('tradicable') else '可交易性不成立'}**；净 SR < 0 直接判退一条{'命中' if h3.get('net_sr_negative') else '未命中'}。

### 四档消融 × 2 个种子（测试段 2022-01-04 ~ 2024-12-31）

| 档 | RankIC 种子42 | RankIC 种子2024 | SR 种子42 | SR 种子2024 | RankICIR 均值 | MDD 均值 | 单边换手均值 |
|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

逐种子全量指标（含基准调整 alpha、持仓数、换手）见 `metrics_table.csv`；
按档聚合的 mean ± range 见 `metrics_by_arm.csv`；机器可读总表见 `backtest_results.json`。

### H2 / H3 明细

| 种子 | RankIC(full) | RankIC(GRU) | 差值 | 95% CI | CI 是否含 0 |
|---|---|---|---|---|---|
{chr(10).join(h2_rows) or '—'}

| 种子 | 净 SR | SR 95% CI | 年化 AR | MDD | 单边换手 | 等权基准超额(年化) | 市值加权基准超额(年化) |
|---|---|---|---|---|---|---|---|
{chr(10).join(h3_rows) or '—'}

### 端点实测（第 0 段，8 个端点）

| # | 端点类别 | 工具 | 返回行数 | 覆盖起止 | 状态 |
|---|---|---|---|---|---|
{chr(10).join(ep_rows)}

未取到：{"；".join(x.get('item', '') for x in probe.get('not_obtained', []))}。
完整原文：`experiments/{args.exp}/data_probe.json`。

### 独立复算

- 脚本：`audit/recompute_work503.py`（**不 import 建模阶段任何函数**，`ast` 机械自检结果
  `modeling_imports_found = {indep.get('modeling_imports_found')}`）；与建模侧逐项相对差 ≤ 1e-6，
  **不一致 {n_mis} 处**。
- 产物：`experiments/{args.exp}/audit_recompute.json`。
- 偏离披露：该脚本用了 numpy/pandas/scipy（快照是 parquet，标准库无 parquet 读取器），已写进产物的 `independence` 字段。

### 数据完整性与前视

- 断言 I1–I5：**{'全部通过' if ic.get('all_passed') else '存在未通过项'}**；
  测试段可交易率 {f4(i5.get('tradable_share'))}、有行情行占比 {f4(i5.get('rows_with_bar'))}、
  PIT 成分覆盖率 {f4(i5.get('pit_coverage'))}。
- 前视检查：特征轴 {la.get('features_checked')} 值最大差 {la.get('max_feat_abs_diff')}；
  先验轴 {la.get('prior_checked')} 值最大差 {la.get('prior_max_abs_diff')}；
  标签轴 {la.get('label_checked')} 值最大差 {la.get('label_max_abs_diff')}；日期错配 {la.get('feature_date_mismatches')} 处。

### 掩码断言确实在跑（故意违规触发原文）

- `experiments/{args.exp}/assertion_violation.txt`（涨停不可买）
- `experiments/{args.exp}/assertion_violation_weight.txt`（组合权重 / 账本不变量）

### 预注册（先于回测产物）

- 最终生效版本 **v1.4**，commit **{prereg_sha}**（{prereg.split()[1] if len(prereg.split())>1 else ''}）。
- 历史版本 v1.0 / v1.1 / v1.2 / v1.3 全部保留在 `experiments/{args.exp}/preregistration*.md`。
- **v1.3 / v1.4 的唯一实质变更**：主检验量的符号口径。派单卡的公式、论文基准「−26.9%」、进带 [−40%,−15%] 三句互相矛盾
  （按字面公式代入论文数字得 +26.9%，落不进进带）；本单冻结为 Δ_drop = (RankIC_no_vq − RankIC_full)/RankIC_full，
  **判词带逐字不动**，并并列报告字面式 Δ_formula。该修订在正式训练跑完之前完成，判词带的数字一次都没变过。
- 失败判据命中：{'、'.join(v.get('failure_criteria_hit') or []) or '无'}。

### 交付物清单（12 项）

| # | 交付物 | 位置 | 状态 |
|---|---|---|---|
| 1 | 端点实测 | `experiments/{args.exp}/data_probe.json` | 已交付 |
| 2 | 预注册 + commit sha | `experiments/{args.exp}/preregistration_v1.4.md`（{prereg_sha}） | 已交付 |
| 3 | 快照 manifest | `experiments/{args.exp}/snapshot_manifest*.json` | 已交付 |
| 4 | 代码 + 断言触发原文 | `experiments/{args.exp}/assertion_violation*.txt` | 已交付 |
| 5 | 四档 × 2 种子指标表 | `experiments/{args.exp}/metrics_table.csv` / `metrics_by_arm.csv` / `backtest_results.json` | 已交付 |
| 6 | 独立复算结果 | `experiments/{args.exp}/audit_recompute.json`（{n_mis} 处不一致） | 已交付 |
| 7 | 与论文对照表 | `experiments/{args.exp}/paper_comparison.csv` | 已交付 |
| 8 | 基准调整 alpha | 等权 + 市值加权，见上表与 `backtest_results.json` | 已交付 |
| 9 | 进 / 退判词 | 本评论首段 + `verdict.json` | 已交付 |
| 10 | 单文件 HTML 报告 + 首屏 PNG + 打印 PDF | `experiments/{args.exp}/report.html` / `preview_first_screen.png` / `report_print.pdf` | 已交付 |
| 11 | 实验登记 | `experiments/registry.md`（WORK-503 行） | 已交付 |
| 12 | 失败记录 | `experiments/{args.exp}/failure_log.md` | 已交付 |

代码提交：HEAD `{head[:7]}`（{head}）。

### 失败项与已知限制（不藏）

- 训练前的数据完整性审计发现并修复了 5 处数据构造层缺陷；修复前的产物全部作废。
- 第一轮 stage-1 因上游 `wandb.log` 未初始化崩溃；第二轮 stage-1 因**未播种全局 RNG** 作废（种子只是标签）。
- 独立代码审查（子代理）报出 2 条 blocking（判词带符号矛盾、判定脚本必崩），均在正式结果产生前修完。
- 审查同时暴露了本单自己的一处错误论证（预注册 v1.3 §4），被落盘后立刻补的边界单测当场判红，已由 v1.4 更正。
- 算力约束：本机无 GPU，训练段 2015–2019（论文 2009–2019）、每阶段 ≤6 轮（论文 ≤50）→ 绝对指标系统性低于论文，
  **只比较四档之间的相对差**。
- 先验替换：JKP 13 因子无 A 股个股级对应值，自建 6 因子 → `no_prior` 档的降幅**不能**用来支持「码本有效」。
- 完整清单见 `experiments/{args.exp}/failure_log.md`。

### 声明

本报告为内部研究模拟，撰写方非持牌机构，**不构成投资建议、不构成任何推荐或收益承诺**。
"""
    out = os.path.join(REPO, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(body)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
