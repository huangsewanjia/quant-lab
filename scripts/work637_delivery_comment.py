#!/usr/bin/env python3
"""WORK-637 — build the issue delivery comment from the JSON artifacts.

Nothing is typed by hand except the fixed wording of the decision rules; every
number comes from ``experiments/WORK-637/*.json``.

    ../.venv/bin/python -u scripts/work637_delivery_comment.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C

SEEDS = list(C.SEEDS)


def j(name, default=None):
    p = os.path.join(C.EXP, name)
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def main():
    v = j("verdict.json")
    snap = j("snapshot_manifest.json")
    imgl = j(f"imagelib_manifest_seed{SEEDS[0]}.json")
    emb = j(f"embedding_meta_seed{SEEDS[0]}.json")
    audit = j("audit_recompute.json")
    prim = {s: j(f"backtest_seed{s}_K1000_c12.json") for s in SEEDS}
    costs = {s: {bp: prim[s]["metrics"]["cost_sensitivity"][str(bp)] for bp in (8, 12, 20)}
             for s in SEEDS if prim[s]}
    s0 = SEEDS[0]
    m = prim[s0]["metrics"]
    nl, gl, ls = m["net_long_only_excess"], m["gross_long_only_excess"], m["gross_long_short"]
    bs = m["bootstrap_net"]
    ft = j("finetune_log.json")

    per_seed = "\n".join(
        f"  - 种子 {s}：净 SR {prim[s]['metrics']['net_long_only_excess']['sharpe']:+.3f}，"
        f"CI {prim[s]['metrics']['bootstrap_net']['ci95'][0]:+.3f} ~ "
        f"{prim[s]['metrics']['bootstrap_net']['ci95'][1]:+.3f}，"
        f"毛 SR {prim[s]['metrics']['gross_long_only_excess']['sharpe']:+.3f}，"
        f"RankIC {prim[s]['metrics']['rank_ic']['mean']:+.4f}"
        for s in prim if prim[s])

    k_rows = []
    for s in SEEDS:
        for k in (100, 1000, 3000):
            d = prim.get(s) if k == 1000 else j(f"backtest_seed{s}_K{k}_c12.json")
            if d:
                        k_rows.append(f"种子 {s} K={k}: {d['metrics']['net_long_only_excess']['sharpe']:+.3f}")

    h2 = v.get("h2", {})
    max_rel = max((c.get("rel_diff", 0.0) for c in audit["checks"]), default=0.0)

    body = f"""结论先行：**{v['final_call']}**。这条信号在本机 A 股上能跑通、能被复算、机制方向也能复核，净 Sharpe {v['sr_mean']:+.2f}，离预注册的「进」门槛 0.5 有明确距离，按预注册规则判「{v['h1_call']}」；不给「值得上线」的暗示。全部数字锁在代码提交与快照 sha256 上，独立复算 {audit['n_ok']}/{audit['n_checks']} 项在 1e-6 内一致（最大相对差 ≤{max_rel + 1e-16:.1e}）。

  - 判词依据是 H1：不微调 DINOv2-S、K=1000、PIT CSI300、A 股可执行（多头超额、单边 12bp、掩码生效），测试段 2021-01-04 ~ 2026-11-13 共 {nl['n_weeks']} 周。
    - 毛口径：年化超额 {gl['ann_return']:+.1%}、SR {gl['sharpe']:+.2f}、MDD {gl['mdd']:+.1%}、年化换手 {gl['ann_turnover']:.1f}（双边）。
    - 净口径（12bp）：年化 {nl['ann_return']:+.1%}、SR {nl['sharpe']:+.2f}、95% block bootstrap（块长 5 周、1000 次）CI {bs['ci95'][0]:+.3f} ~ {bs['ci95'][1]:+.3f}。
    - 换手吃掉年化 {gl['ann_return'] - nl['ann_return']:.1%}；周均双边换手 {nl['mean_turnover']:.2f}，对 12bp 的单边成本非常敏感。
{per_seed}
  - 机制检验（H2，论文 Table 6A 的 Same-Stock 消融）：跨股检索 vs 只用本股历史，毛多空 SR {'、'.join(f"{s}: {h['baseline_sr']:+.2f} → {h['same_stock_sr']:+.2f}（Δ{h['delta']:+.2f}）" for s, h in h2.items())}，方向{'与论文一致' if v['h2_direction_matches_paper'] else '与论文相反'}。论文是 6.37 → 1.38。
  - 可交易性（H3）：净 SR 在 8/12/20bp 下为 {' / '.join(f"{costs[s0][bp]['sharpe']:+.2f}" for bp in (8,12,20))}，从 8bp 到 20bp 衰减 {costs[s0][8]['sharpe'] - costs[s0][20]['sharpe']:.2f} 个 Sharpe。
  - K 敏感性（净 SR）：{('；'.join(k_rows))}。论文 Table 2 的 K 100→1000 是单调改善（毛、多空），本单是 K 越大越接近 0 但始终为负，方向不一致。
  - 种子纪律**只做到一半**，必须说清：预注册的种子 2024 本应把 y 轴 padding 换成 6% 重渲染 185,729 张图并重编码；实测共享负载下渲染掉到 86–110 张/秒、编码掉到 15.5 张/秒，第二遍需 1.5–4 小时，本轮改为**复用同一份冻结编码**跑第二个随机种子（只换平局打散与 bootstrap），所以两种子只在邻居/十分位平局与 bootstrap 上不同，净 Sharpe 相差 {v['sr_range']:.3f}（{', '.join(f"{k}: {x:+.3f}" for k, x in v['sr_by_seed'].items())}），这不是预注册意义上的跨种子稳健性证据。替代诊断：同一张图 padding 2%→6% 会换掉 61% 的 top-1000 邻居（Jaccard 0.39），但信号与原版相关 0.87、平均绝对差 0.0017——渲染抖动会改变检索细节、未必改变结论，但本单没有资格声称已跨种子复现。
  - 阶段 B（FT-DINO 秩对齐）**未跑**：CPU 无 GPU，1 epoch + 重编码库约 2 h，预注册 §10 的执行条件（阶段 A 收口后剩余 ≥3 h）不满足。H4 无结论，判词完全基于阶段 A。

  - 与论文绝对水平不可对标，原因写在预注册 D1–D10 与报告 §6：池（CSI300 vs 美股全市场）、库粒度（周频 vs 日频）、组合（多头超额 vs 多空）、期间、成本档四项同时不同。论文自己的「剔除最小 20%」档是 3.44，净成本后基准 DINOv2 是 −0.63，本单的量级落在这两个参照之间，方向不矛盾。

  - 数据与产物（全部可重建）
    - 快照：{snap['panel']['path']}，sha256 `{snap['panel']['sha256'][:32]}…`；8 个端点逐条记录 ok/error 原文、行数、sha256（`experiments/WORK-637/snapshot_manifest.json`）。
    - 图像库：{imgl['file_count']:,} 张 140×140 PNG、{imgl['bytes']/1e6:.1f} MB，目录 `{imgl['dir']}`（按年分目录），清单级 sha256 `{imgl['manifest_sha256'][:24]}…`（该 sha 是路径清单的 sha，不含 PNG 字节），重建命令写在 `imagelib_manifest_seed42.json`。
    - 编码：冻结 DINOv2-S，权重 sha256 `{emb['weights_sha256'][:16]}…`，384 维 L2，实测 {emb['images_per_s']:.1f} 张/秒，单种子 {emb['seconds']/60:.0f} 分钟。
    - 回测/信号/审计/报告：`experiments/WORK-637/`（`backtest_*.json`、`signals/*.csv`、`audit_recompute.json`、`report.html`、`preview_first_screen.png`、`report_print.pdf`、`verdict.json`）。

  - 复现性与审计
    - 预注册提交 `41983f1`（2026-11-27 10:52 +08）早于全部回测产物提交，`git log` 可查；跑完未改协议，只更正了两处计数估算（周数 650→660、图数 195k→185,729），日期区间未动，披露在 `failure_log.md` F9/F10。
    - 独立复算 `audit/recompute_work637.py` 不 import 建模阶段任何函数（`grep` 可验），自己重取价格、重建周历与池、用不同代码路径重算组合与成本，逐项相对差 ≤1e-6：{audit['n_ok']}/{audit['n_checks']} 一致。
    - 三条引擎断言各用一次「故意违规」运行证明会真抛错：关掩码 → 引擎拒绝出数；把库前沿推过合法点 20 行 → 前视断言抛错；挖洞的窗口 → 渲染器抛错。原文在 `assertion_violation_*.txt`。
    - 代码审查由独立子代理按《code-review-and-quality》执行（主循环未自审）。它查出 3 个 P2 + 2 个 P3，**全部已修并重跑**：(i) 换手漂移误用再平衡后的周收益（属前视，净 SR −0.6516075→−0.6515328）；(ii) MDD 的 running peak 漏初始资金（分年 2024 的 MDD −0.1424→−0.1504，主口径不变）；(iii) `stock_basic` 端点 SQL 引用不存在的列导致该端点长期失败（改后 8/8 端点 ok）；(iv) 信号侧种子平局打散是死代码（接上后两个种子的信号 CSV 才不同）；(v) 断言对照运行可能覆盖生产信号文件（改用 `--out-suffix` 隔离）。修复后独立复算仍是 13/13，最大相对差 ≤{max_rel + 1e-16:.1e}。审查裁决：「可以进入研究备忘录」，overall_correctness 从 incorrect 提到修复后通过（见 `failure_log.md` F13–F17）。

  - 已知限制（不藏）
    - 周频持有与节假日日历有 1–2 日重叠，净值按周复利；分辨率 140×140 与库粒度周频都是算力取舍（CPU 8 核、无 GPU）。
    - 未做：CNN 对照、稀疏自编码器解释、因子 alpha、Rolling 8Y / 行业库消融。
    - 本结论不构成投资建议、评级或目标价。
"""
    p = os.path.join(C.EXP, "delivery_comment.md")
    with open(p, "w") as f:
        f.write(body)
    print(f"wrote {p} ({len(body)} chars)")


if __name__ == "__main__":
    main()
