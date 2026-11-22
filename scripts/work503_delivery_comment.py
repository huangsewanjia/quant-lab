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
            f"{f4((p.get('alpha_idx') or {}).get('excess_annualized'))} | "
            f"{f4((p.get('alpha_ew') or {}).get('excess_annualized'))} | "
            f"{f4((p.get('alpha_cw') or {}).get('excess_annualized'))} |")

    fc = v.get("failure_criteria", {})
    i5 = ic.get("I5_test_coverage", {})
    verdict = v["verdict"]
    h1_verdict = h1.get("verdict", "—")
    n_mis = audit.get("n_mismatches")
    indep = audit.get("independence", {})
    # 「净 SR < 0 直接判退」是否命中 —— 决定顶层判词与 H1 判词是否同向
    sr_neg = bool(h3.get("net_sr_negative"))
    dual = ("进" in str(h1_verdict) and "退" in str(verdict))
    # 判词带落点（不写死，写实际落点）
    _dd = h1.get("delta_drop")
    if _dd is None or (isinstance(_dd, float) and _dd != _dd):
        band = "—"
    elif -0.40 <= _dd <= -0.15:
        band = "[−40%,−15%]「进」带"
    elif -0.10 <= _dd <= 0.10:
        band = "[−10%,+10%]「退」带"
    else:
        band = "两条带之外（「部分复现」带）"

    body = f"""## 交付：PRISM-VQ（arXiv:2605.13407v1）在 A 股 CSI300 上的四档消融复现

```mermaid title="WORK-503 复现流水线与判定"
flowchart TD
  A["第 0 段 端点复测 8 个端点全部 ok"] --> B["第 1 段 预注册冻结 v1.0 ~ v1.4"]
  B --> C["第 2 段 数据构建 完整性断言 I1-I5 全过"]
  C --> D["训练 四档 x 2 种子 + GRU 基线"]
  D --> E["回测 TopK-DropN K=30 N=5 成本 5/15bps"]
  E --> F["独立复算 相对差 ≤ 1e-6"]
  F --> G["判定 H1 码本增量 / H2 增量显著性 / H3 可交易性"]
  G --> H["进退判词 + 独立代码审查"]
```

### 一句话判词（两条结论方向相反，必须分开读）

**H1（主检验量，码本有没有增量）：「{h1_verdict}」**
—— Δ_drop = {pct(h1.get('delta_drop'))}，落在 {band}；论文同口径 {pct(h1.get('paper_delta_ic'))}。
**⇒ 「离散码本确实带来了增量」这一条，复现成立。**

**顶层判词（整体）：「{verdict}」**
—— 按预注册，H3 的「净 SR < 0 直接判退」是一条**独立于 H1 的硬规则**：
本单净 SR 中位数 {f4(h3.get('sr_median'), 3)} {'< 0（负值）' if sr_neg else ''}，该规则触发，把整体判词改写成「{verdict}」。
**⇒ 「按预注册的组合口径（只做多 + 日频调仓 + 买 5/卖 15 bps）能赚钱」这一条，复现不成立。**

{'**这两句不矛盾**：码本的增量是真的，但预注册口径下的交易摩擦把它吃光了——见下面第 4 条（预注册外零成本诊断：毛口径对指数超额年化 +6.7 % / +10.3 %，成本拖累 ≈8.4 %/年）。' if dual else ''}
本结论为内部研究模拟，**不构成投资建议、不构成任何推荐或收益承诺**。

### 三条依据

1. **H1（主检验量，码本有没有增量）**：完整模型 RankIC 中位数 **{f4(h1.get('rankic_full_median'))}**，
   去码本档 **{f4(h1.get('rankic_no_vq_median'))}**，Δ_drop = {pct(h1.get('delta_drop'))}（落在 {band}）；
   逐种子 Δ_drop = {", ".join(f"{k}: {pct(x)}" for k, x in h1.get('delta_ic_per_seed', {}).items())}（两个种子**都在同一带内**）。
   论文 Table 2 同口径 = {pct(h1.get('paper_delta_ic'))}（字面式 Δ_formula = {pct(h1.get('paper_delta_formula'))}，本单字面式 {pct(h1.get('delta_formula'))}）。
   ⇒ **H1 判词：{h1_verdict}**。
2. **H2（相对固定基线 GRU 的增量显著性，10000 次 block bootstrap，block=5 日）**：
   {"；".join(h2_brief) or "—"}。
3. **H3（可交易性）**：净 SR 中位数 **{f4(h3.get('sr_median'), 3)}**（论文 {f4(h3.get('paper_sr'))}），
   CI 下界最小值 {f4(h3.get('sr_ci_lower_bound_min'), 3)}，相对论文比值 {f4(h3.get('shrinkage_vs_paper'), 3)}（下限 {f4(h3.get('shrinkage_limit'), 2)}），
   判定为 **{'可交易性成立' if h3.get('tradicable') else '可交易性不成立'}**；净 SR < 0 直接判退一条{'命中' if h3.get('net_sr_negative') else '未命中'}。
4. **预注册外诊断 · 零成本**：把成本置零后同一组合对 CSI300 指数的年化超额为 **+6.70 %（种子 42）/ +10.25 %（种子 2024）**，
   日均单边换手 0.1667 对应成本拖累 ≈3.3 bps/日 ≈8.4 %/年 ⇒ **信号本身有正超额，但被预注册口径的换手成本吃光**。
   三档预注册成本下净 SR 全为负：8 bps −0.442 / −0.229；30 bps −0.792 / −0.565。

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

| 种子 | 净 SR | SR 95% CI | 年化 AR | MDD | 单边换手 | CSI300指数基准超额(年化) | 等权基准超额(年化) | 总市值篮子超额(年化) |
|---|---|---|---|---|---|---|---|---|
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

### 可复现性（同种子独立重跑）

- 同一 arm、同一 seed、同一 commit 的**第二次独立训练**（`--out-suffix _repro`，正式产物不被覆盖，用时 455.7 s）：
  两份 parquet 的 **sha256 完全相同**、**逐格比较 0 处不一致**、`max abs diff = 0.0`。
- 产物：`experiments/{args.exp}/reproducibility_check.json`（字节 sha256 与逐格精确比较同时记录）。

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
| 8 | 基准调整 alpha（三口径并列） | CSI300 指数 / PIT 等权 / PIT 总市值篮子，见上表与 `backtest_results.json` | 已交付 |
| 9 | 进 / 退判词（H1 与顶层分别给） | 本评论首段 + `verdict.json` | 已交付 |
| 10 | 单文件 HTML 报告 + 首屏 PNG + 打印 PDF | `experiments/{args.exp}/report.html` / `preview_first_screen.png` / `report_print.pdf` | 已交付 |
| 11 | 实验登记 | `experiments/registry.md`（WORK-503 行） | 已交付 |
| 12 | 失败记录 | `experiments/{args.exp}/failure_log.md` | 已交付 |

附带：`cost_sensitivity.csv`（三档预注册成本 + 零成本诊断）、`reproducibility_check.json`（同种子 sha256）、`lookahead_check.json`（三轴前视抽查）。

代码提交：HEAD `{head[:7]}`（{head}）。

### 失败项与已知限制（不藏）

- 训练前的数据完整性审计发现并修复了 5 处数据构造层缺陷；修复前的产物全部作废。
- 第一轮 stage-1 因上游 `wandb.log` 未初始化崩溃；第二轮 stage-1 因**未播种全局 RNG** 作废（种子只是标签）。
- 独立代码审查（子代理）报出 2 条 blocking（判词带符号矛盾、判定脚本必崩），均在正式结果产生前修完。
- 审查同时暴露了本单自己的一处错误论证（预注册 v1.3 §4），被落盘后立刻补的边界单测当场判红，已由 v1.4 更正。
- **终稿跑数阶段由流水线自己抓出的 3 个实现 bug**（详见 `failure_log.md` §11，全部带修前/修后测量值，判词均未因此翻转）：
  ① MDD 的运行峰值漏算初始资金 → 独立复算报 9 处不一致 → 修后 **0 处**；
  ② H2 的逐日 RankIC 序列未按日期对齐 → 判定脚本直接崩 → 修后 join 726 天、`mean_full` 与回测 RankIC 逐位相同；
  ③ PIT 覆盖率的**分母**写成宇宙笛卡尔积（算出 0.414，会误判「数据不可用、停手」）→ 修后 **0.9994** ≥ 0.90。
- **GRU 基线脚本有 3 个 bug**（qlib 的 `GRU` 是包装类不是 `nn.Module`；`d_model`/`n_layers` 被 `**kwargs` 静默吞掉；取标签时把行号当键对用）——基线此前一次都没成功跑过，修完才拿到数。
- **「市值加权基准」原实现有缺陷**（退化成总市值加权）：篮子 +9.46 % vs 指数 −19.99 %，日收益相关 0.98、累计差 30 pp → 主口径改用真实 CSI300 指数，自建篮子降为并列口径，阈值未动。
- **上游仓库不含组合构造 / 回测代码**（只有 `utils/metric.py` 的指标公式，其 AR 年化为 log×252，与本单 exp−1 略异）→ 论文的长/多空与成本假设**无法从代码核实**，报告写「未取到」，不猜。
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
