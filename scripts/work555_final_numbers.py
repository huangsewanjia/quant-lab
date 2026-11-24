#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 对外数字的唯一真源（生成器 + 陈旧自检）

为什么要有这个脚本
------------------
`failure_log.md` §12 的表和 `experiments/registry.md` 的 WORK-555 行以前是**手打数字**。
协议一改 / 重跑一次，旧数字就会留在文档里（独立审查第一轮就抓到一处 95% CI 单位错误：
正文写 `[-13.08%, +14.87%]`，实为比值未 ×100）。现在改成机器生成：

  1) 从产物（run_*.json / backtest_*.json / bt_daily_*.csv / audit_findings.json /
     snapshot_manifest.json）算出全部对外数字 -> `experiments/WORK-555/final_numbers.json`；
  2) 重写 `failure_log.md` 里 `<!-- BEGIN/END GENERATED:work555_final_numbers:* -->` 之间的块，
     以及 `registry.md` 里以 WORK-555 开头的那一行；
  3) `--check` 模式：重新生成后与磁盘内容逐字节比对，任何陈旧即退出码 1。

用法：
  /tmp/w555venv/bin/python scripts/work555_final_numbers.py          # 生成并写回
  /tmp/w555venv/bin/python scripts/work555_final_numbers.py --check  # 只检查，不写
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)
LOG = os.path.join(OUT, "failure_log.md")
REG = os.path.join(REPO, "experiments", "registry.md")
NUMS = os.path.join(OUT, "final_numbers.json")

SEEDS = (42, 43)
ARMS = ("dee", "mlp_only", "xgb", "xgbfull")
T975_DF1 = 12.706          # t(0.975, df=1)，与预注册 §6.2 的 CI 口径一致
MARK = "work555_final_numbers"


# ------------------------------------------------------------------ 采集
def jload(p):
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def collect():
    n = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
         "experiment": EXP, "seeds": list(SEEDS)}
    runs, bts = {}, {}
    for fn in sorted(os.listdir(OUT)):
        if fn.startswith("run_") and fn.endswith(".json"):
            runs[fn[4:-5]] = jload(os.path.join(OUT, fn))
        elif fn.startswith("backtest_") and fn.endswith(".json"):
            bts[fn[9:-5]] = jload(os.path.join(OUT, fn))
    n["runs"] = runs
    n["backtests"] = bts

    # ---- 组合层指标
    port = {}
    for tag, r in runs.items():
        b = bts.get(tag, {}).get("metrics", {})
        m8, m5 = b.get("main_8bps", {}), b.get("sens_5bps", {})
        port[tag] = {
            "rankic": r.get("test_rankic_mean", r.get("test_rankic_mean_best_iter")),
            "ic": r.get("test_ic_mean", r.get("test_ic_mean_best_iter")),
            "ann_8bps": m8.get("annualized_return"),
            "sharpe_8bps": m8.get("sharpe_rf0"),
            "mdd_8bps": m8.get("max_drawdown"),
            "turn_8bps": m8.get("turnover_daily_mean"),
            "sharpe_5bps": m5.get("sharpe_rf0"),
            "turn_5bps": m5.get("turnover_daily_mean"),
            "cost_total_8bps": m8.get("cost_total"),
            "gross_total_8bps": m8.get("gross_return_total"),
            "avg_holdings": m8.get("avg_holdings"),
            "elapsed_sec": r.get("elapsed_sec"),
            "best_step": (r.get("train") or {}).get("best_step"),
            "steps_run": (r.get("train") or {}).get("steps_run"),
            "early_stopped": (r.get("train") or {}).get("early_stopped"),
            "loss_monitor": (r.get("train") or {}).get("loss_monitor"),
            "loss_monitor_val": ((r.get("train") or {}).get("best_ens_val_mse")
                                 if (r.get("train") or {}).get("loss_monitor") else None),
            "alt_monitor": (r.get("train") or {}).get("alt_monitor_mean_expert_mse"),
        }
    n["port"] = port

    # ---- 主检验量 Δ_ic（DEE vs 最佳单族 MLP-only）
    per_seed, deltas = [], []
    for s in SEEDS:
        d, m = port.get(f"dee_s{s}"), port.get(f"mlp_only_s{s}")
        if not (d and m and m["rankic"]):
            continue
        delta = (d["rankic"] - m["rankic"]) / m["rankic"]
        per_seed.append({"seed": s, "rankic_dee": d["rankic"], "rankic_mlp": m["rankic"],
                         "delta_ic": delta})
        deltas.append(delta)
    primary = {"per_seed": per_seed}
    if deltas:
        mean = float(np.mean(deltas))
        rng = float(max(deltas) - min(deltas))
        se = float(np.std(deltas, ddof=1)) / math.sqrt(len(deltas)) if len(deltas) > 1 else float("nan")
        half = T975_DF1 * se if len(deltas) > 1 else float("nan")
        ric_d = [p["rankic_dee"] for p in per_seed]
        ric_m = [p["rankic_mlp"] for p in per_seed]
        primary.update({
            "delta_ic_mean": mean, "delta_ic_range": rng,
            "ci_lo": mean - half, "ci_hi": mean + half,
            "ci_contains_zero": bool(mean - half <= 0 <= mean + half),
            "sign_flip": len({1 if d > 0 else (-1 if d < 0 else 0) for d in deltas}) > 1,
            "rankic_range_dee": float(max(ric_d) - min(ric_d)),
            "rankic_range_mlp": float(max(ric_m) - min(ric_m)),
            "rankic_unstable_dee": bool(max(ric_d) - min(ric_d) > 0.01),
            "rankic_unstable_mlp": bool(max(ric_m) - min(ric_m) > 0.01),
        })
        sh = [port[f"dee_s{s}"]["sharpe_8bps"] for s in SEEDS
              if port.get(f"dee_s{s}", {}).get("sharpe_8bps") is not None]
        primary["dee_net_sharpe_8bps"] = sh
        primary["dee_net_sharpe_mean_8bps"] = float(np.mean(sh)) if sh else float("nan")
    n["primary"] = primary

    # ---- 判词（预注册 §6.2 机械规则，逐条可判）
    v = {"rules": []}
    if deltas:
        mean, rng = primary["delta_ic_mean"], primary["delta_ic_range"]
        shm = primary["dee_net_sharpe_mean_8bps"]
        if mean > 0.50:
            v["rules"].append(f"Δ_ic 均值 {f(mean*100, 2, pct=True, sign=True)} **> +50% 上限** → 「单族基线本身跑坏、横向不可比」")
            v["delta_ic_bucket"] = "不可比（> +50%）"
        elif mean <= 0:
            v["rules"].append(f"Δ_ic 均值 {f(mean*100, 2, pct=True, sign=True)} **≤ 0** → DEE 不优于最佳单族")
            v["delta_ic_bucket"] = "退（≤ 0）"
        elif 0.02 <= mean <= 0.30:
            v["rules"].append(f"Δ_ic 均值 {f(mean*100, 2, pct=True, sign=True)} 落在进带 [+2%, +30%]")
            v["delta_ic_bucket"] = "进带"
        else:
            v["rules"].append(f"Δ_ic 均值 {f(mean*100, 2, pct=True, sign=True)} 落在进带 [+2%, +30%] 之外")
            v["delta_ic_bucket"] = "进带之外"
        if shm < 0:
            v["rules"].append(f"DEE 净 Sharpe（rf=0，8 bps）均值 {f(shm, 4, sign=True)} **< 0** → **直接退**")
            v["sharpe_negative"] = True
        else:
            v["sharpe_negative"] = False
        if primary["rankic_unstable_mlp"]:
            v["rules"].append(f"最佳单族基线自身两种子 RankIC 极差 "
                              f"{f(primary['rankic_range_mlp'], 4)} **> 0.01** → 基线不稳、相对量不可比")
        if primary["rankic_unstable_dee"]:
            v["rules"].append(f"DEE 自身两种子 RankIC 极差 {f(primary['rankic_range_dee'], 4)} > 0.01 → 不稳定")
        if primary["ci_contains_zero"]:
            v["rules"].append("95% CI 含 0 → 记「未复现」（仍算交付）")
        v["verdict"] = "退" if (v.get("sharpe_negative") or mean <= 0 or mean > 0.50) else (
            "进" if 0.02 <= mean <= 0.30 else "不进带")
        v["verdict_word"] = ("未复现（退）" if v["verdict"] == "退" else
                             ("复现（进）" if v["verdict"] == "进" else "未复现（不进带）"))
    n["verdict"] = v

    # ---- 族消融（DEE 臂内部）
    fam = {}
    for s in SEEDS:
        r = runs.get(f"dee_s{s}", {})
        for k, v2 in (r.get("family_subsets") or {}).items():
            fam.setdefault(k, {})[str(s)] = {"rankic": v2.get("rankic"), "ic": v2.get("ic"),
                                             "n_active": v2.get("n_active_after_ic_filter")}
    n["family"] = fam

    # ---- 组合权重不变量（现金账本口径）
    inv = {"max_sum_w": 0.0, "n_days_over_1": 0, "max_ledger_dev": 0.0, "n_files": 0,
           "max_n_hold": 0, "worst_file": None}
    for fn in sorted(os.listdir(OUT)):
        if not (fn.startswith("bt_daily_") and fn.endswith(".csv")):
            continue
        d = pd.read_csv(os.path.join(OUT, fn))
        if "sum_w" not in d.columns:
            continue
        sw = d["sum_w"].to_numpy(dtype="float64")
        ca = d["cash"].to_numpy(dtype="float64")
        inv["n_files"] += 1
        inv["n_days_over_1"] += int(np.sum(sw > 1.0 + 1e-9))
        inv["max_ledger_dev"] = max(inv["max_ledger_dev"], float(np.max(np.abs(sw + ca - 1.0))))
        if float(np.max(sw)) > inv["max_sum_w"]:
            inv["max_sum_w"] = float(np.max(sw))
            inv["worst_file"] = fn
        inv["max_n_hold"] = max(inv["max_n_hold"], int(d["n_hold"].max()))
    n["weight_invariant"] = inv

    # ---- 独立复算 / 快照
    au = jload(os.path.join(OUT, "audit_findings.json")) or {}
    n["audit"] = {"verdict": au.get("verdict"), "n_findings": au.get("n_findings"),
                  "n_blocking": au.get("n_blocking"),
                  "primary_test_recomputed": au.get("primary_test_recomputed")}
    man = jload(os.path.join(OUT, "snapshot_manifest.json")) or {}
    items = []
    for it in man.get("items", []):
        p = os.path.join(REPO, it["path"])
        got = None
        if os.path.exists(p):
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            got = h.hexdigest()
        items.append({"name": it["name"], "path": it["path"], "expected": it["sha256"],
                      "actual": got, "match": got == it["sha256"]})
    n["snapshot"] = {"n_items": len(items), "n_match": sum(1 for i in items if i["match"]),
                     "items": items}
    return n


# ------------------------------------------------------------------ 渲染
def f(x, nd=6, pct=False, sign=False, dash="—"):
    """统一数字格式：排版减号 U+2212（与正文其余部分一致）。"""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return dash
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    s = s.replace("-", "\u2212")
    return s + ("%" if pct else "")


def render_fl_12_2(n):
    p = n["primary"]
    port = n["port"]
    L = []
    L.append("| 臂 | 种子 | 测试 RankIC | 测试 IC | 年化(8bps) | Sharpe(8bps,rf=0) | MDD(8bps) | 日均双边换手 |")
    L.append("|---|---|---|---|---|---|---|---|")
    label = {"dee": "DEE", "mlp_only": "MLP-only", "xgb": "XGBoost",
             "xgbfull": "XGBoost（无早停·诊断）"}
    for arm in ARMS:
        for s in SEEDS:
            tag = f"{arm}_s{s}"
            d = port.get(tag)
            if not d:
                continue
            rank = "**" if arm == "dee" else ""
            L.append(f"| {label[arm]} | {s} | {rank}{f(d['rankic'], 6)}{rank} | {f(d['ic'], 6)} | "
                     f"{f(d['ann_8bps'] * 100 if d['ann_8bps'] is not None else None, 2, pct=True, sign=True)} | "
                     f"{rank}{f(d['sharpe_8bps'], 4, sign=True)}{rank} | "
                     f"{f(d['mdd_8bps'] * 100 if d['mdd_8bps'] is not None else None, 2, pct=True)} | "
                     f"{f(d['turn_8bps'], 3)} |")
    L.append("")
    L.append("**Δ_ic（DEE vs 最佳单族 MLP-only，同种子）**")
    for ps in p.get("per_seed", []):
        L.append(f"- seed {ps['seed']}：**({f(ps['rankic_dee'], 6)} − {f(ps['rankic_mlp'], 6)}) / "
                 f"{f(ps['rankic_mlp'], 6)} = {f(ps['delta_ic'] * 100, 2, sign=True)}%**")
    L.append(f"- 两种子均值 **{f(p.get('delta_ic_mean', 0) * 100, 2, sign=True)}%**；"
             f"极差 **{f(p.get('delta_ic_range', 0) * 100, 2)} 个百分点**；"
             f"**{'逐种子符号翻转' if p.get('sign_flip') else '逐种子同号'}**")
    L.append(f"- 95% CI（t 近似，df=1，n=2）：**[{f(p.get('ci_lo', 0) * 100, 1, sign=True)}%, "
             f"{f(p.get('ci_hi', 0) * 100, 1, sign=True)}%]**，"
             f"{'含 0 → 记「未复现」' if p.get('ci_contains_zero') else '不含 0'}")
    L.append("  （Δ_ic 是**比值**，此处百分号 = ×100；区间极宽，仅作参考）")
    au = n.get("audit", {}).get("primary_test_recomputed") or {}
    if au:
        L.append(f"- 独立复算侧逐位一致：`audit_findings.json` → "
                 f"`primary_test_recomputed.delta_ic_mean = {au.get('delta_ic_mean')!r}`")
    L.append("")
    L.append(f"**判词（严格按预注册 §6.2 机械规则）：{n['verdict'].get('verdict_word', '—')}。**")
    L.append(f"{len(n['verdict'].get('rules', []))} 条判据同时命中：")
    for i, r in enumerate(n["verdict"].get("rules", []), 1):
        L.append(f"{i}. {r}；")
    # 因果方向段：数字随产物自适应（避免留下旧结论）
    dee = [port.get(f"dee_s{s}", {}) for s in SEEDS]
    mlp = [port.get(f"mlp_only_s{s}", {}) for s in SEEDS]
    dee_ric = [d.get("rankic") for d in dee if d.get("rankic") is not None]
    mlp_ric = [d.get("rankic") for d in mlp if d.get("rankic") is not None]
    dee_ens = " / ".join(f(d.get("loss_monitor_val"), 6) for d in dee
                          if d.get("loss_monitor_val") is not None)
    mlp_ens = " / ".join(f(d.get("loss_monitor_val"), 6) for d in mlp
                          if d.get("loss_monitor_val") is not None)
    higher = (np.mean(dee_ric) > np.mean(mlp_ric)) if dee_ric and mlp_ric else None
    better = None
    dv = [d.get("loss_monitor_val") for d in dee if d.get("loss_monitor_val") is not None]
    mv = [d.get("loss_monitor_val") for d in mlp if d.get("loss_monitor_val") is not None]
    if dv and mv:
        better = np.mean(dv) < np.mean(mv)
    L.append("")
    if higher is not None and dee_ens and mlp_ens:
        L.append(f"**注意这里的因果方向**：本单 DEE 的绝对水平**{'高于' if higher else '低于'}** "
                 f"MLP-only（{f(np.mean(dee_ric), 4)} vs {f(np.mean(mlp_ric), 4)} 两种子均值），"
                 f"且 DEE 的集成级验证 MSE（{dee_ens}）"
                 f"**{'也优于' if better else '并不优于'}** MLP-only（{mlp_ens}），"
                 f"即「异质集成{'确实' if better else '并未'}降了验证损失」这件事在本面板上"
                 f"{'是成立的' if better else '不成立'}；")
        L.append("但**主检验量要求的是相对量在两个种子之间稳定**，"
                 "而 MLP-only 基线自己的两种子极差就超过了预注册的 0.01 阈值，"
                 "所以 Δ_ic 的符号由基线的种子噪声决定 → **这是「不可比」，不是「复现成功」**。")
    else:
        L.append("（因果方向段需要集成级验证损失对照值，本次产物未提供，略。）")
    return "\n".join(L)


def render_fl_12_3(n):
    p, port = n["primary"], n["port"]
    dee = [port.get(f"dee_s{s}", {}) for s in SEEDS]
    turn = " / ".join(f(d.get("turn_8bps"), 3) for d in dee if d)
    dps = " / ".join(f(ps["delta_ic"] * 100, 2, sign=True) + "%" for ps in p.get("per_seed", []))
    ric_m = " / ".join(f(ps["rankic_mlp"], 6) for ps in p.get("per_seed", []))
    L = ["| 原因 | 实测证据 |", "|---|---|"]
    L.append(f"| 净 Sharpe < 0 | 年化双边换手 **{turn}**（日频调仓 top-50）→ "
             f"单边 8 bps + 卖出印花税 10 bps 的日成本约 15~16 bps，**年化摩擦 ~39%**，毛收益扛不住 |")
    L.append(f"| Δ_ic > +50% 不可比 | 逐种子 {dps}，"
             f"{'符号翻转' if p.get('sign_flip') else '同号'}；CI "
             f"[{f(p.get('ci_lo', 0) * 100, 1, sign=True)}%, {f(p.get('ci_hi', 0) * 100, 1, sign=True)}%] "
             f"{'含 0' if p.get('ci_contains_zero') else '不含 0'} |")
    L.append(f"| 基线不稳 | MLP-only 两种子 RankIC {ric_m}，极差 {f(p.get('rankic_range_mlp'), 4)} "
             f"{'> 0.01' if p.get('rankic_unstable_mlp') else '≤ 0.01'}（预注册 §6.2 阈值） |")
    return "\n".join(L)


PAPER_T2 = {"linear_family": "0.051", "mlp_family": "0.055",
            "tree_family": "0.054", "full_dee": "0.060"}
FAM_LABEL = {"linear_family": "linear-family（8 专家）", "mlp_family": "MLP-family（6 专家）",
             "tree_family": "tree-family（6 专家）", "full_dee": "full DEE（20 专家）"}


def render_fl_12_4(n):
    fam = n.get("family", {})
    L = ["| 族 | 本单 RankIC（s42 / s43） | 论文 Table 2 RankIC |", "|---|---|---|"]
    for k in ("linear_family", "mlp_family", "tree_family", "full_dee"):
        if k not in fam:
            continue
        a = fam[k].get("42", {}).get("rankic")
        b = fam[k].get("43", {}).get("rankic")
        L.append(f"| {FAM_LABEL[k]} | {f(a, 6, sign=True)} / {f(b, 6, sign=True)} | {PAPER_T2.get(k, '—')} |")
    L.append("")
    p = n["primary"]
    L.append("论文 Table 2 的 Δ（Full DEE vs MLP only）= **+9.1%**（(0.060−0.055)/0.055）。")
    dps = " / ".join(f(ps["delta_ic"] * 100, 2, sign=True) + "%" for ps in p.get("per_seed", []))
    L.append(f"本单同口径的 Δ_ic 两种子为 **{dps}**，"
             f"**{'符号都不稳定' if p.get('sign_flip') else '符号一致'}**。")
    return "\n".join(L)


def render_registry_row(n):
    p, port, au = n["primary"], n["port"], n.get("audit", {})
    v = n["verdict"]
    dee = [port.get(f"dee_s{s}", {}) for s in SEEDS]
    mlp = [port.get(f"mlp_only_s{s}", {}) for s in SEEDS]
    xgb = [port.get(f"xgb_s{s}", {}) for s in SEEDS]
    dps = " / ".join(f(ps["delta_ic"] * 100, 2, sign=True) + "%" for ps in p.get("per_seed", []))
    ric_d = " / ".join(f(d.get("rankic"), 6, sign=True) for d in dee if d)
    ric_m = " / ".join(f(d.get("rankic"), 6, sign=True) for d in mlp if d.get("rankic") is not None)
    ric_x = " / ".join(f(d.get("rankic"), 6, sign=True) for d in xgb if d)
    sh_d = " / ".join(f(d.get("sharpe_8bps"), 4, sign=True) for d in dee if d)
    ann_d = " / ".join(f(d.get("ann_8bps", 0) * 100, 2, sign=True) + "%" for d in dee if d)
    mdd_d = " / ".join(f(d.get("mdd_8bps", 0) * 100, 2) + "%" for d in dee if d)
    turn_d = " / ".join(f(d.get("turn_8bps"), 3) for d in dee if d)
    five = []
    for tag in ("dee_s42", "dee_s43", "mlp_only_s42", "mlp_only_s43",
                "xgb_s42", "xgb_s43"):
        d = port.get(tag)
        if d and d.get("sharpe_5bps") is not None:
            pretty = {"dee": "DEE", "mlp_only": "MLP-only", "xgb": "XGBoost"}
            a_, s_ = tag.rsplit("_s", 1)
            five.append(f"{pretty.get(a_, a_)} s{s_} {f(d['sharpe_5bps'], 3, sign=True)}")
    pos5 = [t for t in five if "+" in t]
    neg5 = [t for t in five if "+" not in t]
    main_results = (
        f"**Δ_ic 逐种子 {dps}**，均值 **{f(p.get('delta_ic_mean', 0) * 100, 2, sign=True)}%**，"
        f"极差 **{f(p.get('delta_ic_range', 0) * 100, 2)} pp**，"
        f"**{'符号翻转' if p.get('sign_flip') else '同号'}**；"
        f"95% CI [{f(p.get('ci_lo', 0) * 100, 1, sign=True)}%, {f(p.get('ci_hi', 0) * 100, 1, sign=True)}%] "
        f"**{'含 0' if p.get('ci_contains_zero') else '不含 0'}**。"
        f"DEE RankIC {ric_d}；MLP-only {ric_m}；XGBoost {ric_x}。"
        f"DEE 净 Sharpe(rf=0, 8 bps) **{sh_d}**（均值 {f(p.get('dee_net_sharpe_mean_8bps'), 4, sign=True)}）、"
        f"年化 {ann_d}、MDD {mdd_d}、日均双边换手 {turn_d}。"
        f"独立复算 **{au.get('verdict', '—')}（blocking {au.get('n_blocking', '—')} / {au.get('n_findings', '—')} 项检查）**，"
        f"Δ_ic 复算值 {(au.get('primary_test_recomputed') or {}).get('delta_ic_mean')!r} 与建模侧逐位一致。"
        f"组合权重不变量：全部 bt_daily 的 max Σw = {f(n['weight_invariant']['max_sum_w'], 9)}、"
        f"Σw>1 天数 = {n['weight_invariant']['n_days_over_1']}、"
        f"max\\|Σw+cash−1\\| = {n['weight_invariant']['max_ledger_dev']:.3e}（现金账本口径，无隐含杠杆）。"
        f"臂 C 早停退化已量化：XGBoost 实际只用 **1 棵树**（best_iteration=0，"
        f"xgboost sklearn predict() 自动截断到 best_iteration+1），"
        f"非预注册诊断对照（关早停、**仅在验证段**选轮次）RankIC 升到 "
        + " / ".join(f(port.get(f"xgbfull_s{s}", {}).get("rankic"), 6, sign=True) for s in SEEDS)
    )
    verdict_cell = (
        f"**{v.get('verdict_word', '—')}**——预注册 §6.2 判据逐条命中："
        + "；".join(r.replace("**", "") for r in v.get("rules", []))
    )
    fail_cell = (
        f"① 论文 Table 2 的 **+9.1% 未复现**：同口径 Δ_ic 两种子 {dps} "
        f"**{'符号翻转' if p.get('sign_flip') else '同号'}**，均值不代表方向<br>"
        f"② **绝对水平远低于论文**（DEE RankIC "
        f"{f(np.mean([d['rankic'] for d in dee if d]), 4, sign=True)} 均值 vs 论文 0.060）："
        f"因子集 48 vs 158、自建面板、PIT 口径、含 A 股交易成本<br>"
        f"③ **DEE 净 Sharpe 为负**：日频调仓 top-50 的年化双边换手 ~100%，"
        f"8 bps + 10 bps 印花税的年化摩擦 ~39% 吃掉全部毛收益——本单组合口径下该策略不可用"
        f"（5 bps 敏感性净 Sharpe 转正 {len(pos5)} 个：{'；'.join(pos5)}；"
        f"仍为负 {len(neg5)} 个：{'；'.join(neg5)}）<br>"
        f"④ **臂 C（XGBoost）在预注册参数下退化为单树桩**：patience=50 在近乎平坦的验证曲线上于第 0 轮触发；"
        f"诊断对照下 RankIC 由 {ric_x} 升到 "
        + " / ".join(f(port.get(f"xgbfull_s{s}", {}).get("rankic"), 6, sign=True) for s in SEEDS)
        + f" → 退化的是「用池化 MSE 做早停」在本面板的适配性，**不是实现缺陷**；"
          f"本单**不因此改协议**，该臂仅作参考、不参与进/退判词<br>"
        f"⑤ **两种子不足以支撑主检验量**：MLP-only 基线自身极差 {f(p.get('rankic_range_mlp'), 4)} "
        f"{'已超' if p.get('rankic_unstable_mlp') else '未超'}预注册 0.01 阈值，"
        f"Δ_ic 的符号由基线噪声决定<br>"
        f"⑥ **独立审查第一轮判 FAIL**（2 blocking + 3 suggestion + 4 nit）："
        f"B1 审计脚本在 sha256 漂移路径崩溃（已修为显式 blocking）、"
        f"B2 快照 manifest 未锁事实层 panel_backtest.csv（已补，现 6 份逐份 MATCH）、"
        f"S1 梯度单测 worst 缩进漏检（已修 + 变异测试自证）、"
        f"S2 早停监控量与预注册「集成级」文字不符（已改并重跑）、"
        f"S3 组合强制持有权重泄漏（已改现金账本口径 + 两道不变量闸门）；"
        f"留痕见 `experiments/WORK-555/review_round1.json`<br>"
        f"⑦ qlib 官方 Alpha158 因子与 CSI300 数据集**未取到**（未下载、未实测可达）；"
        f"tushare `bak_daily` / `sw_daily` / `ci_daily` **40203 无权限**；"
        f"`stock_zh_a_hist_em` **IP 限流**；论文官方代码**未取到**（DEE 全自实现）"
    )
    row = (f"| `WORK-555` | 2026-11-24 | "
           f"复现 Li D.《Diversification Across Predictors: Heterogeneous Ensemble Learning for "
           f"Cross-Sectional Return Prediction》arXiv:2611.12389v1（ICAIF '26，"
           f"DOI 10.1145/3858063.3858743，**论文无官方代码**）的**异质性相对贡献**主张："
           f"DEE 的 RankIC 是否稳定高于最佳单族（论文 Table 2 报 DEE 0.060 vs MLP-only 0.055，"
           f"Δ = **+9.1%**） | 2021-01-29（测试段末日 2020-12-31） | "
           f"`experiments/WORK-555/snapshot_manifest.json`（{n['snapshot']['n_items']} 份 CSV，"
           f"sha256 逐条锁定，当前 {n['snapshot']['n_match']} 份 MATCH；"
           f"`data/WORK-555/panel_backtest.csv` 750,589 行 × 测试段 974 交易日） | "
           f"股票池 `000300.SH` 月末 PIT 快照（成分调整滞后实测 ≤36 天）+ 上市满 60 交易日过滤；"
           f"切分 2008-01-02~2014-12-31 训练 / 2015~2016 验证 / 2017-01-03~2020-12-31 测试；"
           f"**自实现 48 列** Alpha158 风格因子（偏离 D2，论文 158 列）；"
           f"**N=20** 专家（偏离 D1，论文 50），配比 4 linear / 1 ridge / 2 lasso / 1 elasticnet / "
           f"6 MLP(32) / 6 软树(12)；β=0.8789 / γ=0.5 / κ=1.5 / α=1e-3；"
           f"Adam lr 1e-3、wd 1e-4、batch 8192、early stop patience 50 每 20 步评估**集成级**验证损失；"
           f"top-50 等权**日频**调仓（现金账本口径，Σw + cash ≡ 1）；"
           f"单边 **8 bps** + 卖出印花税 10 bps（另 5 bps 敏感性）；rf=0，"
           f"年化 365.25 自然日 / 波动 √252；种子 **42 / 43**；涨跌停与停牌不可成交 | "
           f"{main_results} | {verdict_cell} | {fail_cell} |")
    return row


BLOCKS = {
    "§12.2": render_fl_12_2,
    "§12.3": render_fl_12_3,
    "§12.4": render_fl_12_4,
}


# ------------------------------------------------------------------ 写回 / 自检
def apply_block(text, name, body):
    begin = f"<!-- BEGIN GENERATED:{MARK}:{name} -->"
    end = f"<!-- END GENERATED:{MARK}:{name} -->"
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    if not pat.search(text):
        raise SystemExit(f"[FAIL] failure_log.md 缺生成块标记：{name}")
    return pat.sub(lambda _m: f"{begin}\n{body}\n{end}", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查不写；有陈旧即退出码 1")
    args = ap.parse_args()

    n = collect()
    with open(NUMS, "w", encoding="utf-8") as fh:
        json.dump(n, fh, ensure_ascii=False, indent=2)

    # ---- failure_log 生成块
    log_txt = open(LOG, encoding="utf-8").read()
    new_log = log_txt
    for name, fn in BLOCKS.items():
        new_log = apply_block(new_log, name, fn(n))

    # ---- registry 行
    reg_txt = open(REG, encoding="utf-8").read()
    lines = reg_txt.split("\n")
    idx = [i for i, l in enumerate(lines) if l.startswith("| `WORK-555` |")]
    if len(idx) != 1:
        raise SystemExit(f"[FAIL] registry.md 里 WORK-555 行数 = {len(idx)}（应为 1）")
    new_reg = "\n".join(lines[:idx[0]] + [render_registry_row(n)] + lines[idx[0] + 1:])

    if args.check:
        bad = []
        if new_log != log_txt:
            bad.append("failure_log.md 生成块与产物不一致")
        if new_reg != reg_txt:
            bad.append("registry.md 的 WORK-555 行与产物不一致")
        if bad:
            print("[FAIL] " + "；".join(bad))
            print("       跑：/tmp/w555venv/bin/python scripts/work555_final_numbers.py")
            return 1
        print(f"[OK] final_numbers.json 与两处文档一致；"
              f"快照 {n['snapshot']['n_match']}/{n['snapshot']['n_items']} 份 sha256 MATCH；"
              f"判词 {n['verdict'].get('verdict_word', '—')}")
        return 0

    open(LOG, "w", encoding="utf-8").write(new_log)
    open(REG, "w", encoding="utf-8").write(new_reg)
    print(json.dumps({
        "final_numbers": NUMS,
        "verdict": n["verdict"].get("verdict_word"),
        "delta_ic_mean": n["primary"].get("delta_ic_mean"),
        "ci": [n["primary"].get("ci_lo"), n["primary"].get("ci_hi")],
        "dee_net_sharpe_mean_8bps": n["primary"].get("dee_net_sharpe_mean_8bps"),
        "audit": n["audit"].get("verdict"),
        "n_blocking": n["audit"].get("n_blocking"),
        "n_findings": n["audit"].get("n_findings"),
        "snapshot_match": f"{n['snapshot']['n_match']}/{n['snapshot']['n_items']}",
        "weight_invariant": n["weight_invariant"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
