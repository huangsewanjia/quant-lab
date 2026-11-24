#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 配图（论文式排版）

规格（工单「样式规格」）：
  - 图内文字一律用英文；中文解释放正文与图注
  - 面板用小写 a/b 标注
  - 强调色只用三支：主蓝 #0F4D92 / 次青 #42949E / 警示红 #B64342
  - 底 #ffffff、正文 #1a1a1a、分隔线 #d9d9d6、浅底块 #f7f7f5
  - 无阴影、无渐变、无圆角卡片堆叠

输出：
  figures/WORK-555/fig1_rankic_arms.png   三档 RankIC 对照 + Δ_ic 逐种子
  figures/WORK-555/fig2_daily_rankic.png  日频截面 RankIC 时序
  figures/WORK-555/fig3_net_equity.png    净净值曲线（含成本）
  figures/WORK-555/fig4_paper_compare.png 与论文 Table 1/2/3 对照

运行：
  /tmp/w555venv/bin/python scripts/work555_figures.py
"""
import json
import os
import sys

import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)
DATA = os.path.join(REPO, "data", EXP)
FIG = os.path.join(REPO, "figures", EXP)

C_BLUE = "#0F4D92"
C_TEAL = "#42949E"
C_RED = "#B64342"
C_INK = "#1a1a1a"
C_RULE = "#d9d9d6"
C_PANEL = "#f7f7f5"

ARM_LABEL = {"dee": "DEE (6-type, N=20)", "mlp_only": "MLP-only (N=20)",
             "xgb": "XGBoost"}
ARM_COLOR = {"dee": C_BLUE, "mlp_only": C_TEAL, "xgb": C_RED}

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica", "sans-serif"],
    "axes.unicode_minus": False,
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 9,
    "axes.edgecolor": C_RULE,
    "axes.labelcolor": C_INK,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "xtick.color": C_INK, "ytick.color": C_INK,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
    "grid.color": C_RULE, "grid.linewidth": 0.6,
})


def save(fig, stem):
    os.makedirs(FIG, exist_ok=True)
    p = os.path.join(FIG, f"{stem}.png")
    fig.savefig(p, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[fig] {p}")
    return p


def load_results():
    runs = {}
    for fn in sorted(os.listdir(OUT)):
        if fn.startswith("run_") and fn.endswith(".json"):
            tag = fn[len("run_"):-len(".json")]
            with open(os.path.join(OUT, fn), encoding="utf-8") as fh:
                runs[tag] = json.load(fh)
    bts = {}
    for fn in sorted(os.listdir(OUT)):
        if fn.startswith("backtest_") and fn.endswith(".json"):
            tag = fn[len("backtest_"):-len(".json")]
            with open(os.path.join(OUT, fn), encoding="utf-8") as fh:
                bts[tag] = json.load(fh)
    return runs, bts


def daily_series(arm, seed):
    """读组合逐日账（csv 模块，避免依赖 pandas：本机 system python 有 matplotlib 无 pandas）。"""
    p = os.path.join(OUT, f"bt_daily_{arm}_s{seed}_main_8bps.csv")
    if not os.path.exists(p):
        return None
    cols = {"trade_date": [], "net_ret": [], "gross_ret": [], "cost": [],
            "buys": [], "sells": [], "turnover": [], "n_hold": []}
    with open(p, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for k in cols:
                if k != "trade_date":
                    cols[k].append(float(row[k]))
                else:
                    cols[k].append(row[k])
    out = {k: np.asarray(v) for k, v in cols.items()}
    # **日期必须是真日期**：CSV 里的 trade_date 是字符串，若直接喂给 ax.plot()，
    # matplotlib 会当成**分类轴**，于是一个交易日一个刻度（974 个），
    # 974 个日期标签叠在一起糊成一条黑带（2026-11-24 自查发现，图 2/图 3 都有）。
    # 这里转成 datetime64[D]，配合显式的年份 locator/formatter。
    out["trade_date"] = np.asarray(cols["trade_date"], dtype="datetime64[D]")
    return out


_FACT_CACHE = {}


def _load_facts():
    """读事实层（磁盘缓存优先）。

    `panel_backtest.csv` 有 95 MB / 750,589 行。原实现用 `csv.reader` 每次重新解析，
    实测**单次 ~233 秒**，而 `daily_rankic()` 会被调用 9 次 → 整轮配图 ~30 分钟
    （实测有一次跑 29 分钟仍未产出 fig2）。

    现在改为：先读 `experiments/WORK-555/facts_cache.npz`
    （由 `scripts/work555_facts_cache.py` 用 pandas 一次性生成，实测 **1.3 秒**），
    缓存缺失时回退到 `csv.reader` 直读（慢，但保证脚本能独立跑通）。

    标签列对非测试段行可能是空串：缓存里一律记为 NaN，
    与原「只对测试段行做 float()」的语义一致（空标签不进任何统计）。
    """
    if "facts" in _FACT_CACHE:
        return _FACT_CACHE["facts"]
    cache = os.path.join(OUT, "facts_cache.npz")
    if os.path.exists(cache):
        d = np.load(cache)
        # trade_date 存的是字符串 -> 转 datetime64[D]（否则图 2 的 x 轴会退化成
        # 974 个分类刻度、标签叠成黑带，见 daily_series 处的同类注释）
        out = (d["ts_code"], np.asarray(d["trade_date"], dtype="datetime64[D]"), d["label"])
    else:
        bt_path = os.path.join(DATA, "panel_backtest.csv")
        print(f"[warn] 未找到 {cache}，回退到 csv.reader 直读（慢约 200 s/次）；"
              f"建议先跑 scripts/work555_facts_cache.py")
        ts, dt, lab = [], [], []
        with open(bt_path, encoding="utf-8") as fh:
            rd = csv.reader(fh)
            head = next(rd)
            i_ts, i_d, i_lab = head.index("ts_code"), head.index("trade_date"), head.index("label")
            for row in rd:
                ts.append(row[i_ts])
                dt.append(row[i_d])
                lab.append(float(row[i_lab]) if row[i_lab] else np.nan)
        out = (np.asarray(ts, dtype=str), np.asarray(dt, dtype="datetime64[D]"),
               np.asarray(lab, dtype="float64"))
    _FACT_CACHE["facts"] = out
    return out


def daily_rankic(arm, seed):
    """逐日截面 RankIC（建模侧口径，与 run_*.json 的均值对应）。

    事实层从缓存读（numpy 数组），预测值从 npz 读。
    """
    pr = np.load(os.path.join(OUT, f"preds_{arm}_s{seed}.npz"))
    ts_all, dt_all, lab_all = _load_facts()
    rid = pr["row"].astype("int64")
    labs = lab_all[rid]
    dts = dt_all[rid]
    preds = pr["pred"].astype("float64")
    per_day = {}
    for i in range(len(rid)):
        lst = per_day.get(dts[i])
        if lst is None:
            lst = per_day[dts[i]] = ([], [])
        lst[0].append(preds[i])
        lst[1].append(labs[i])
    out = []
    for d in sorted(per_day):
        p_arr, t_arr = per_day[d]
        p_arr = np.asarray(p_arr, dtype="float64")
        t_arr = np.asarray(t_arr, dtype="float64")
        m = np.isfinite(p_arr) & np.isfinite(t_arr)
        p_arr, t_arr = p_arr[m], t_arr[m]
        if len(p_arr) < 3:
            out.append((d, np.nan))
            continue
        rp, rt = _rank(p_arr), _rank(t_arr)
        out.append((d, float(np.corrcoef(rp, rt)[0, 1])))
    return out


def _rank(a):
    order = np.argsort(a, kind="stable")
    r = np.empty(len(a), dtype="float64")
    r[order] = np.arange(len(a), dtype="float64")
    s = a[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            r[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


# ---------------------------------------------------------------- 图1
def fig1(runs):
    arms = ["dee", "mlp_only", "xgb"]
    seeds = sorted({int(k.split("_s")[1]) for k in runs if k.startswith("dee_")})
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.0))

    ax = axes[0]
    xs, labels = [], []
    for i, arm in enumerate(arms):
        vals = [runs[f"{arm}_s{s}"]["test_rankic_mean"] for s in seeds
                if f"{arm}_s{s}" in runs]
        if not vals:
            continue
        x = i * 1.0
        ax.bar(x, np.mean(vals), width=0.46, color=ARM_COLOR[arm],
               edgecolor="white", linewidth=0.8, zorder=3)
        if len(vals) > 1:
            ax.plot([x - 0.23, x + 0.23], [min(vals), min(vals)],
                    color=C_INK, linewidth=1.0, zorder=4)
            ax.plot([x - 0.23, x + 0.23], [max(vals), max(vals)],
                    color=C_INK, linewidth=1.0, zorder=4)
        ax.text(x, np.mean(vals) + 0.0035,
                f"{np.mean(vals):.4f}\n±{(max(vals)-min(vals))/2:.4f}",
                ha="center", va="bottom", fontsize=6.6, color=C_INK, zorder=5)
        xs.append(x)
        labels.append(ARM_LABEL[arm])
    ax.set_xticks(xs)
    ax.set_xticklabels([l.replace(" (", "\n(") for l in labels], fontsize=6.6)
    ax.axhline(0, color=C_RULE, linewidth=0.8)
    ax.set_ylabel("Test-period RankIC (daily cross-section)")
    ax.set_title("a  Predictive strength by arm", loc="left", color=C_INK,
                 fontweight="bold")
    ax.grid(axis="y", zorder=0)
    ax.set_ylim(min(0, ax.get_ylim()[0]), ax.get_ylim()[1] * 1.28)

    ax = axes[1]
    deltas = []
    for s in seeds:
        kd, kb = f"dee_s{s}", f"mlp_only_s{s}"
        if kd in runs and kb in runs:
            rd = runs[kd]["test_rankic_mean"]
            rb = runs[kb]["test_rankic_mean"]
            deltas.append((s, (rd - rb) / rb if rb else np.nan))
    if deltas:
        xs = [d[0] for d in deltas]
        ys = [d[1] for d in deltas]
        ax.axhspan(0.02, 0.30, color=C_TEAL, alpha=0.10, zorder=1)
        ax.axhline(0, color=C_INK, linewidth=0.9, zorder=2)
        ax.plot(xs, ys, marker="o", markersize=5, color=C_BLUE, zorder=4,
                markeredgecolor="white", markeredgewidth=0.8)
        for x, y in zip(xs, ys):
            ax.text(x, y + 0.012, f"{y:+.2%}", ha="center", fontsize=6.8,
                    color=C_INK, zorder=5)
        ax.text(0.98, 0.30, "in-band  +2%..+30%", transform=ax.get_yaxis_transform(),
                ha="right", va="top", fontsize=6.4, color=C_TEAL)
        ax.set_xticks(xs)
        ax.set_xticklabels([f"seed {s}" for s in xs])
        ax.set_ylabel("Δ_ic = (RankIC_DEE − RankIC_MLP) / RankIC_MLP")
        ax.set_title("b  Primary test quantity per seed", loc="left", color=C_INK,
                     fontweight="bold")
        ax.grid(axis="y", zorder=0)
    fig.tight_layout()
    return save(fig, "fig1_rankic_arms")


# ---------------------------------------------------------------- 图2
def fig2():
    fig, axes = plt.subplots(2, 1, figsize=(7.4, 4.4), sharex=True,
                             gridspec_kw={"height_ratios": [1.25, 1]})
    ax = axes[0]
    for arm in ("dee", "mlp_only", "xgb"):
        for seed in (42, 43):
            d = daily_rankic(arm, seed)
            if not d:
                continue
            xs = [x[0] for x in d]
            ys = [x[1] for x in d]
            ls = "-" if seed == 42 else "--"
            lw = 1.0 if seed == 42 else 0.7
            ax.plot(xs, ys, ls, color=ARM_COLOR[arm],
                    linewidth=lw, alpha=1.0 if seed == 42 else 0.55, zorder=3)
    ax.axhline(0, color=C_RULE, linewidth=0.8)
    ax.set_ylabel("Daily cross-sectional RankIC")
    ax.set_title("a  Daily RankIC of the three arms (solid = seed 42, dashed = seed 43)",
                 loc="left", color=C_INK, fontweight="bold")
    ax.grid(axis="y", zorder=0)
    handles = [plt.Line2D([], [], color=ARM_COLOR[a], linewidth=1.6, label=ARM_LABEL[a])
               for a in ("dee", "mlp_only", "xgb")]
    ax.legend(handles=handles, loc="upper left", frameon=False, ncol=3)

    ax = axes[1]
    for arm in ("dee", "mlp_only", "xgb"):
        d = daily_rankic(arm, 42)
        if not d:
            continue
        xs = [x[0] for x in d]
        ys = np.cumsum([0.0 if not np.isfinite(x[1]) else x[1] for x in d])
        ax.plot(xs, ys, color=ARM_COLOR[arm], linewidth=1.2, zorder=3)
    ax.axhline(0, color=C_RULE, linewidth=0.8)
    ax.set_ylabel("Cumulative RankIC (sum of daily)")
    ax.set_xlabel("Test period 2017-01 .. 2020-12")
    ax.set_title("b  Cumulative predictive strength", loc="left", color=C_INK,
                 fontweight="bold")
    ax.grid(axis="y", zorder=0)
    # 显式年份刻度（否则 974 个交易日标签叠成黑带）
    for _ax in axes:
        _ax.xaxis.set_major_locator(mdates.YearLocator())
        _ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    return save(fig, "fig2_daily_rankic")


# ---------------------------------------------------------------- 图3
def fig3():
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.0))
    ax = axes[0]
    for arm in ("dee", "mlp_only", "xgb"):
        d = daily_series(arm, 42)
        if d is None:
            continue
        nav = np.cumprod(1.0 + d["net_ret"])
        ax.plot(d["trade_date"], nav, color=ARM_COLOR[arm], linewidth=1.2, zorder=3,
                label=ARM_LABEL[arm])
    ax.axhline(1.0, color=C_RULE, linewidth=0.8)
    ax.set_ylabel("Net NAV (1.0 at start)")
    ax.set_xlabel("2017 .. 2020")
    ax.set_title("a  Net equity, 8 bps + stamp (seed 42)",
                 loc="left", color=C_INK, fontweight="bold")
    ax.legend(loc="upper left", frameon=False)
    ax.grid(axis="y", zorder=0)
    # 显式年份刻度（否则 974 个交易日标签叠成黑带）
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    ax = axes[1]
    w = 0.26
    for i, arm in enumerate(("dee", "mlp_only", "xgb")):
        d = daily_series(arm, 42)
        if d is None:
            continue
        turn = float(np.mean(d["turnover"]))
        ax.bar(i, turn, width=w, color=ARM_COLOR[arm], edgecolor="white",
               linewidth=0.8, zorder=3)
        ax.text(i, turn + 0.02, f"{turn:.2f}", ha="center", fontsize=6.8,
                color=C_INK)
    ax.set_xticks(range(3))
    ax.set_xticklabels([ARM_LABEL[a].split(" (")[0] for a in
                        ("dee", "mlp_only", "xgb")], fontsize=7)
    ax.set_ylabel("Daily two-sided turnover")
    ax.set_title("b  Daily two-sided turnover, top-50 book",
                 loc="left", color=C_INK, fontweight="bold")
    ax.grid(axis="y", zorder=0)
    fig.tight_layout()
    return save(fig, "fig3_net_equity")


# ---------------------------------------------------------------- 图4
def fig4(runs, bts):
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.0))
    ax = axes[0]
    paper = {"Linear Reg.": 0.044, "MLP": 0.053, "XGBoost": 0.059, "DEE": 0.060}
    keys = list(paper.keys())
    ax.bar(np.arange(len(keys)) - 0.2, [paper[k] for k in keys], width=0.38,
           color=C_TEAL, edgecolor="white", linewidth=0.8, zorder=3,
           label="Paper Table 1 (Alpha158, CSI300)")
    ours = []
    for k, arm in zip(["Linear Reg.", "MLP", "XGBoost", "DEE"],
                      [None, "mlp_only", "xgb", "dee"]):
        if arm is None:
            ours.append(np.nan)
            continue
        vals = [runs[f"{arm}_s{s}"]["test_rankic_mean"] for s in (42, 43)
                if f"{arm}_s{s}" in runs]
        ours.append(np.mean(vals) if vals else np.nan)
    ax.bar(np.arange(len(keys)) + 0.2, ours, width=0.38, color=C_BLUE,
           edgecolor="white", linewidth=0.8, zorder=3,
           label="This study (48 factors, CSI300, net of nothing)")
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels(keys, fontsize=7)
    ax.set_ylabel("Test-period RankIC")
    ax.set_title("a  RankIC: paper vs this study", loc="left", color=C_INK,
                 fontweight="bold")
    ax.legend(loc="upper left", frameon=False, fontsize=6.4)
    ax.grid(axis="y", zorder=0)

    ax = axes[1]
    paper2 = {"Linear only": 0.051, "Tree only": 0.054, "MLP only": 0.055,
              "Full DEE": 0.060}
    fam = None
    for k in runs:
        if k.startswith("dee_") and "family_subsets" in runs[k]:
            fam = runs[k]["family_subsets"]
            break
    ks = list(paper2.keys())
    ax.bar(np.arange(len(ks)) - 0.2, [paper2[k] for k in ks], width=0.38,
           color=C_TEAL, edgecolor="white", linewidth=0.8, zorder=3,
           label="Paper Table 2 (N=50)")
    ours2 = []
    mapk = {"Linear only": "linear_family", "Tree only": "tree_family",
            "MLP only": "mlp_family", "Full DEE": "full_dee"}
    for k in ks:
        v = fam.get(mapk[k], {}).get("rankic") if fam else None
        ours2.append(v if v is not None else np.nan)
    ax.bar(np.arange(len(ks)) + 0.2, ours2, width=0.38, color=C_BLUE,
           edgecolor="white", linewidth=0.8, zorder=3, label="This study (N=20)")
    ax.set_xticks(range(len(ks)))
    ax.set_xticklabels(ks, fontsize=7)
    ax.set_ylabel("Test-period RankIC")
    ax.set_title("b  Family ablation: paper vs this study", loc="left",
                 color=C_INK, fontweight="bold")
    ax.legend(loc="upper left", frameon=False, fontsize=6.4)
    ax.grid(axis="y", zorder=0)
    fig.tight_layout()
    return save(fig, "fig4_paper_compare")


def main():
    runs, bts = load_results()
    if not runs:
        print("no run_*.json found — 先跑数")
        return 1
    fig1(runs)
    try:
        fig2()
    except Exception as e:
        print("[fig2] failed:", e)
    try:
        fig3()
    except Exception as e:
        print("[fig3] failed:", e)
    try:
        fig4(runs, bts)
    except Exception as e:
        print("[fig4] failed:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
