#!/usr/bin/env python3
"""WORK-637 — write the experiments/registry.md row from the JSON artifacts.

Idempotent: re-running replaces the WORK-637 row instead of appending a second
one. Numbers come from ``verdict.json`` / ``backtest_*.json`` only.

    ../.venv/bin/python -u scripts/work637_registry.py
"""
from __future__ import annotations

import json
import os
import re
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
    prim = {s: j(f"backtest_seed{s}_K1000_c12.json") for s in SEEDS}
    prim = {s: d for s, d in prim.items() if d}
    s0 = SEEDS[0]
    m = prim[s0]["metrics"]
    nl, gl = m["net_long_only_excess"], m["gross_long_only_excess"]
    snap = j("snapshot_manifest.json")
    imgl = j(f"imagelib_manifest_seed{s0}.json")
    emb = j(f"embedding_meta_seed{s0}.json")
    audit = j("audit_recompute.json")
    ft = j("finetune_log.json")

    sr = " / ".join(f"{s}:{prim[s]['metrics']['net_long_only_excess']['sharpe']:+.2f}" for s in prim)
    ci = " / ".join(f"{s}:{prim[s]['metrics']['bootstrap_net']['ci95'][0]:+.2f}~"
                    f"{prim[s]['metrics']['bootstrap_net']['ci95'][1]:+.2f}" for s in prim)
    h2 = " / ".join(f"{s}:{v['h2'][str(s)]['baseline_sr']:+.2f}→{v['h2'][str(s)]['same_stock_sr']:+.2f}"
                    for s in prim if str(s) in v.get("h2", {}))
    row = (
        f"| `WORK-637` | 2026-11-27 | 复现 arXiv:2611.23723v1《When Price Charts Rhyme》的"
        f"**图表视觉检索 + 收益秩对齐**：冻结 DINOv2-S、20 日 K 线、递归扩张历史库 top-K 邻居的已实现 5 日收益，"
        f"在 PIT CSI300 上做多头超额 | 2026-11-26 | `experiments/WORK-637/snapshot_manifest.json`"
        f"（8 端点 ok/row/sha256；面板 sha256 `{snap['panel']['sha256'][:16]}…`）+ 图像库 "
        f"{imgl['file_count']:,} 张 / 清单 sha256 `{imgl['manifest_sha256'][:16]}…` | "
        f"池 PIT CSI300（250 份月度快照 as-of，932 只）；周频 R5（信号 2021-01-04~2026-11-13，{nl['n_weeks']} 周）；"
        f"图 140×140（20 日 qfq + MA20 + 量）；DINOv2-S 权重 sha256 `{emb['weights_sha256'][:16]}…`，"
        f"384 维 L2；K=1000 精确 top-K（附 100/3000）；标签 open(t+1)→open(t+6)；"
        f"多头 top 十分位等权 − 全池等权；单边 12 bp（附 8/20）；涨停/跌停/停牌掩码；种子 42/2024 | "
        f"毛 SR {gl['sharpe']:+.2f}、年化 {gl['ann_return']:+.1%}；**净 SR {nl['sharpe']:+.2f}**"
        f"（年化 {nl['ann_return']:+.1%}，MDD {nl['mdd']:+.1%}）；两种子净 SR {sr}；"
        f"95% CI {ci}；H2 同股消融 {h2}；独立复算 {audit['n_ok']}/{audit['n_checks']} 项 ≤1e-6 | "
        f"**{v['final_call']}**（{v['h1_why']}；{v.get('h2_direction_matches_paper') and 'H2 方向与论文一致' or 'H2 方向未复现'}） | "
        f"阶段 B（FT-DINO 秩对齐）{'已跑' if ft else '未跑'}（CPU 算力，预注册 §10 执行条件未满足）；"
        f"库粒度周频（论文日频）、池 CSI300（论文美股全市场）、多头（论文多空）、分辨率 140（论文未写）四项偏离，"
        f"见 `experiments/WORK-637/preregistration.md` D1–D10 与 `failure_log.md` |\n")

    p = os.path.join(C.REPO, "experiments", "registry.md")
    s = open(p).read()
    lines = s.splitlines(keepends=True)
    out, replaced = [], False
    for ln in lines:
        if ln.startswith("| `WORK-637`"):
            out.append(row)
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        # insert after the last experiment row (line starting with "| `WORK-")
        idx = max(i for i, ln in enumerate(out) if ln.startswith("| `WORK-"))
        out.insert(idx + 1, row)
    open(p, "w").write("".join(out))
    print(("updated" if replaced else "inserted") + f" WORK-637 row in {p}")


if __name__ == "__main__":
    main()
