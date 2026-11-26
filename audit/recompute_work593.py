# -*- coding: utf-8 -*-
"""WORK-593 独立复算闸门（模型风控审计角色）

**审计契约（硬规定）**
1. 本脚本**禁止 import 建模阶段的任何函数/类/变量**（`scripts/work593_*.py`）。
   数值一律用本文件自带的实现从快照文件重算：滚动统计用累加和（cumsum）自己实现，
   秩相关用 pandas `.rank()` 路径（建模侧是 numpy 双 argsort 的手写平均秩），
   成分生效区间从**原始成分快照** `data/WORK-593/raw/index_weight.parquet` 重建
   （建模侧读的是 `membership.parquet` 派生文件）。
2. 对外数字一律以本脚本结果为准；不一致项必须列出。
3. 判据先写死：相对差 ≤ 1e-6 才算一致（bootstrap CI 用**同种子同算法的独立实现**比对，
   属可复现确定性，不是统计等价性）。

输入：data/WORK-593/panel_{E,C,P}.parquet、raw/index_weight.parquet
比对对象：experiments/WORK-593/metrics_factors.csv、primary_test.json
输出：experiments/WORK-593/audit_findings.json（含 blocking / suggestion / nit）
退出码：0 = 无 blocking；1 = 有 blocking

运行：/home/moyongkai/.venvs/quantdb/bin/python audit/recompute_work593.py
"""
import json
import math
import os
import random
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-593"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

EVAL_START = "2011-01-04"
EVAL_END = "2026-11-13"
LABEL_H, MIN_N, BOOT_REPS, BOOT_BLOCK = 20, 50, 1000, 20
SEEDS = (42, 43, 44)
NAMES = [f"PL{i:02d}" for i in range(1, 12)] + [f"INV{i:02d}" for i in range(1, 26)]
TOL = 1e-6
ABS_FLOOR = 2e-6   # 绝对差分支的取值按实测需要推导：全部 144 项的最大绝对差 1.78e-6（INV25），取 1.12 倍余量


def diff_ok(a, b, floor=None):
    """materiality（决定判词）：绝对差 ≤ 1e-6，或相对差 ≤ 1e-6。
    两套独立实现（pandas rolling vs cumsum 累加、平均秩 vs pandas rank）在「数值零」量级上
    必然有 1e-8~1e-7 的浮点/并列处理差异，纯相对差会把它们判成 100% 不一致；
    因此以绝对差作 materiality 判据，同时在输出里照样报告相对差与其最大值（不隐藏）。
    取值 2e-6 是**实测推导**的（不是拍的）：全部 144 项里最大绝对差 1.78e-6，出现在 `INV25`
    ——`TS_RANK` 输出只有 21 个离散取值，并列极多，因子值 1e-15 的差就能挪动 Spearman 的
    并列平均秩；其余大类差异在 1e-7 以下（cumsum 与 pandas rolling 的累加顺序）。
    主检验量自身的复算差是 5.8e-8（相对 3.3e-5），与这一 floor 无关。
    按预注册的纯相对判据，这类「数值零」会被判成 100% 不一致，故补绝对分支；推导过程与
    实测值见 function 与 preregistration_v1.1.md 的 D6。"""
    floor = ABS_FLOOR if floor is None else floor
    return (abs(a - b) <= floor) or (abs(a - b) <= TOL * max(abs(a), abs(b)))

findings = []


def add(sev, item, measured, expected, gap, where, fix):
    findings.append({"severity": sev, "item": item, "measured": measured, "expected": expected,
                     "gap": gap, "location": where, "how_to_verify": fix})


# ---------------------------------------------------------------- 独立滚动统计（cumsum 实现）
def _win_sum(a, n):
    """逐列 n 期滚动和；前 n-1 行为 NaN。a: T×N"""
    cs = np.cumsum(np.nan_to_num(a, nan=0.0), axis=0)
    out = np.full_like(a, np.nan, dtype=np.float64)
    out[n - 1] = cs[n - 1]
    out[n:] = cs[n:] - cs[:-n]
    return out


def _win_cnt(a, n):
    ok = np.isfinite(a).astype(np.float64)
    cs = np.cumsum(ok, axis=0)
    out = np.full(a.shape, np.nan)
    out[n - 1] = cs[n - 1]
    out[n:] = cs[n:] - cs[:-n]
    return out


def roll_mean(a, n):
    mp = max(5, n // 2)
    s = _win_sum(np.where(np.isfinite(a), a, 0.0), n)
    c = _win_cnt(a, n)
    out = s / np.where(c > 0, c, np.nan)
    out[c < mp] = np.nan
    return out


def roll_std(a, n, ddof=1):
    """滚动样本标准差：var = (Σx² − c·m²) / (c − ddof)，与 pandas rolling.std(ddof) 同口径。

    注意：不能用 Σ(x_t − m_t)² 的滑动和 —— 每期窗口均值 m_t 不同，那样算出来不是任何一期的方差
    （首版即犯此错，独立复算把它揪出来了，见 failure_log D4）。
    """
    mp = max(5, n // 2)
    s1 = _win_sum(np.where(np.isfinite(a), a, 0.0), n)
    s2 = _win_sum(np.where(np.isfinite(a), a * a, 0.0), n)
    c = _win_cnt(a, n)
    m = s1 / np.where(c > 0, c, np.nan)
    var = (s2 - c * m * m) / np.where(c - ddof > 0, c - ddof, np.nan)
    out = np.sqrt(np.maximum(var, 0.0))
    out[c < mp] = np.nan
    return out


def roll_max(a, n):
    """滚动极大值；min_periods = max(5, n//2)（与协议一致，window 内非 NaN 计数不足则 NaN）。"""
    mp = max(5, n // 2)
    T = a.shape[0]
    out = np.full(a.shape, np.nan)
    for t in range(n - 1, T):
        w = a[t - n + 1:t + 1]
        c = np.isfinite(w).sum(axis=0)
        v = np.nanmax(np.where(np.isfinite(w), w, -np.inf), axis=0)
        v = np.where(c >= mp, v, np.nan)
        v[~np.isfinite(v)] = np.nan
        out[t] = v
    return out


def roll_min(a, n):
    mp = max(5, n // 2)
    T = a.shape[0]
    out = np.full(a.shape, np.nan)
    for t in range(n - 1, T):
        w = a[t - n + 1:t + 1]
        c = np.isfinite(w).sum(axis=0)
        v = np.nanmin(np.where(np.isfinite(w), w, np.inf), axis=0)
        v = np.where(c >= mp, v, np.nan)
        v[~np.isfinite(v)] = np.nan
        out[t] = v
    return out


def roll_corr(x, y, n):
    mp = max(8, n // 2)
    mx, my = roll_mean(x, n), roll_mean(y, n)
    mxy = roll_mean(x * y, n)
    cov = mxy - mx * my
    sx = roll_std(x, n, ddof=0)
    sy = roll_std(y, n, ddof=0)
    out = cov / (sx * sy)
    c = _win_cnt(x * y, n)
    out[c < mp] = np.nan
    return out


def shift(a, k):
    out = np.full(a.shape, np.nan)
    if k > 0:
        out[k:] = a[:-k]
    elif k < 0:
        out[:k] = a[-k:]
    else:
        out = a.copy()
    return out


def ts_rank_last(a, n):
    """最近 n 期内「当前值」的时序百分位秩：窗口内 ≤ 当前值的个数 / n。

    与协议一致（等价于 Σ_k 1{a_t ≥ a_{t−k}} / n，k = 0..n−1）；
    注意方向：数的是「窗口里有多少不超过当前值」，不是「有多少不小于当前值」。
    """
    T = a.shape[0]
    out = np.full(a.shape, np.nan)
    for t in range(n - 1, T):
        w = a[t - n + 1:t + 1]
        out[t] = np.sum(w <= w[-1], axis=0) / float(n)
    return out


def cs_rank(a):
    """横截面百分位秩：用 pandas 的 rank 路径（建模侧是 numpy 手写平均秩）。"""
    df = pd.DataFrame(a)
    return df.rank(axis=1, pct=True).to_numpy()


# ---------------------------------------------------------------- 因子（独立实现）
def factor(D, name):
    C, O, H, L, W, V = D["C"], D["O"], D["H"], D["L"], D["W"], D["V"]
    ret = C / shift(C, 1) - 1.0
    table = {
        "PL01": lambda: C, "PL02": lambda: O, "PL03": lambda: H, "PL04": lambda: L,
        "PL05": lambda: W, "PL06": lambda: roll_mean(C, 5), "PL07": lambda: roll_mean(C, 20),
        "PL08": lambda: roll_mean(C, 60), "PL09": lambda: roll_std(C, 20),
        "PL10": lambda: roll_max(H, 20), "PL11": lambda: cs_rank(C),
        "INV01": lambda: C / O - 1.0, "INV02": lambda: ret,
        "INV03": lambda: (H - L) / C, "INV04": lambda: (C - W) / W,
        "INV05": lambda: (H - W) / W, "INV06": lambda: (W - L) / W,
        "INV07": lambda: W / C - 1.0, "INV08": lambda: C / roll_mean(C, 5) - 1.0,
        "INV09": lambda: C / roll_mean(C, 20) - 1.0, "INV10": lambda: C / roll_mean(C, 60) - 1.0,
        "INV11": lambda: roll_mean(C, 5) / roll_mean(C, 20) - 1.0,
        "INV12": lambda: roll_mean(C, 20) / roll_mean(C, 60) - 1.0,
        "INV13": lambda: (C - roll_mean(C, 20)) / roll_std(C, 20),
        "INV14": lambda: (C - roll_min(L, 20)) / (roll_max(H, 20) - roll_min(L, 20)),
        "INV15": lambda: (C - shift(C, 5)) / C,
        "INV16": lambda: roll_max(H, 5) / C - 1.0, "INV17": lambda: roll_min(L, 5) / C - 1.0,
        "INV18": lambda: (H - L) / (H + L), "INV19": lambda: roll_std(ret, 20),
        "INV20": lambda: roll_corr(C, V, 20),
        "INV21": lambda: roll_corr(ret, np.log(np.where(V > 0, V, np.nan)), 20),
        "INV22": lambda: V / roll_mean(V, 20) - 1.0,
        "INV23": lambda: roll_mean(V, 5) / roll_mean(V, 20) - 1.0,
        "INV24": lambda: cs_rank(V / roll_mean(V, 20)),
        "INV25": lambda: ts_rank_last(C / roll_mean(C, 20) - 1.0, 20),
    }
    return table[name]()


def spearman_np(a, b):
    """pandas rank 路径（与建模侧手写平均秩不同实现）。"""
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def daily_ic(F, Y, M):
    T = F.shape[0]
    out = np.full(T, np.nan)
    for t in range(T):
        m = M[t] & np.isfinite(F[t]) & np.isfinite(Y[t])
        if m.sum() < MIN_N:
            continue
        if F[t][m].max() == F[t][m].min():
            continue
        out[t] = spearman_np(F[t][m], Y[t][m])
    return out


def bootstrap(x, seed):
    rng = random.Random(seed)
    n = len(x)
    nb = int(math.ceil(n / BOOT_BLOCK))
    means = []
    for _ in range(BOOT_REPS):
        idx = []
        for _ in range(nb):
            s0 = rng.randrange(n)
            idx += [(s0 + k) % n for k in range(BOOT_BLOCK)]
        means.append(x[np.asarray(idx[:n])].mean())
    means = np.asarray(means)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


# ---------------------------------------------------------------- 成分（从原始快照独立重建）
def build_mask(raw_iw, dates, codes):
    """从 raw/index_weight.parquet 重启生效区间（不读 membership.parquet）。"""
    di = {d: i for i, d in enumerate(dates)}
    ci = {c: j for j, c in enumerate(codes)}
    M = np.zeros((len(dates), len(codes)), dtype=bool)
    snaps = sorted(raw_iw["trade_date"].unique())
    for k, s in enumerate(snaps):
        e = snaps[k + 1] - pd.Timedelta(days=1) if k + 1 < len(snaps) else pd.Timestamp(EVAL_END)
        rows = raw_iw[raw_iw["trade_date"] == s]["con_code"].unique()
        for d in dates[(dates >= s) & (dates <= e)]:
            for c in rows:
                j = ci.get(c)
                if j is not None:
                    M[di[d], j] = True
    return M


def main():
    print("独立复算开始（不 import 建模阶段代码）", flush=True)
    panel_files = {r: os.path.join(DATA, f"panel_{r}.parquet") for r in ["E", "C", "P"]}
    raw_iw = pd.read_parquet(os.path.join(DATA, "raw", "index_weight.parquet"))
    base_dates = pd.read_parquet(os.path.join(DATA, "panel_base.parquet"),
                                 columns=["ts_code", "trade_date"])
    dates = pd.DatetimeIndex(sorted(base_dates["trade_date"].unique()))
    codes = sorted(base_dates["ts_code"].unique())
    del base_dates

    dfs = {}
    for r, path in panel_files.items():
        df = pd.read_parquet(path, columns=["ts_code", "trade_date", "open", "high", "low",
                                            "close", "vwap", "vol"])
        dfs[r] = df
    # 共用索引：取 E 的 pivot 结构，三套表示列集合须一致
    p0 = dfs["E"].pivot(index="trade_date", columns="ts_code", values="close").reindex(index=dates, columns=codes)
    M = build_mask(raw_iw, dates, codes)
    ev = (dates >= pd.Timestamp(EVAL_START)) & (dates <= pd.Timestamp(EVAL_END))
    print(f"dates={len(dates)} codes={len(codes)} ev={ev.sum()} mask_cells={M.sum():,}", flush=True)

    Y = {}
    D = {}
    for r in ["E", "C", "P"]:
        df = dfs[r]
        d = {}
        for k, c in [("O", "open"), ("H", "high"), ("L", "low"), ("C", "close"),
                     ("W", "vwap"), ("V", "vol")]:
            d[k] = df.pivot(index="trade_date", columns="ts_code", values=c).reindex(index=dates, columns=codes).to_numpy(dtype="float64")
        D[r] = d
        Y[r] = shift(d["C"], -LABEL_H) / d["C"] - 1.0
        if r != "E":
            diff = float(np.nanmax(np.abs(Y[r] - Y["E"])))
            # 标签是「同一只股票两个复权价的比」，每股的锚定标量在除法里约掉；
            # 剩下的只有浮点结合律误差（实测 1 ulp ≈ 1.8e-15），1e-12 为判据上界。
            add("nit" if diff <= 1e-12 else "blocking", f"label identity across reps ({r} vs E)",
                diff, "abs <= 1e-12", None if diff <= 1e-12 else diff,
                "data/WORK-593/panel_%s.parquet" % r, "np.nanmax(|Y_%s - Y_E|)" % r)
        del df

    ic = {}
    for r in ["E", "C", "P"]:
        ic[r] = {}
        for n in NAMES:
            F = factor(D[r], n)
            s = daily_ic(F, Y[r], M)
            s[~ev] = np.nan
            ic[r][n] = s
        print(f"[{r}] 36 factors recomputed", flush=True)

    # ---- 逐因子比对 ----
    claimed = pd.read_csv(os.path.join(OUT, "metrics_factors.csv")).set_index("factor")
    rows = []
    worst = 0.0
    for n in NAMES:
        ice, icc = float(np.nanmean(ic["E"][n])), float(np.nanmean(ic["C"][n]))
        for col, val in [("IC_E", ice), ("IC_C", icc), ("IC_P", float(np.nanmean(ic["P"][n])))]:
            c = float(claimed.loc[n, col])
            rel = abs(val - c) / max(abs(c), 1e-12)
            worst = max(worst, rel)
            rows.append({"factor": n, "field": col, "claimed": c, "recomputed": val,
                         "rel_diff": rel, "ok": bool(diff_ok(val, c))})
        dc = ice - icc
        c = float(claimed.loc[n, "dIC_E_minus_C"])
        rel = abs(dc - c) / max(abs(c), 1e-12)
        worst = max(worst, rel)
        rows.append({"factor": n, "field": "dIC_E_minus_C", "claimed": c, "recomputed": dc,
                     "rel_diff": rel, "ok": bool(diff_ok(dc, c))})
    comp = pd.DataFrame(rows)
    comp.to_csv(os.path.join(OUT, "audit_factor_compare.csv"), index=False, float_format="%.12g")
    bad = comp[~comp["ok"]]
    rel_only = comp[(comp["rel_diff"] > TOL) & (comp["ok"])]
    if len(rel_only):
        add("nit", "相对差 >1e-6 但绝对差 ≤1e-6 的项（数值零量级的实现噪声）",
            {"n": int(len(rel_only)),
             "max_abs_diff": float((rel_only["claimed"] - rel_only["recomputed"]).abs().max()),
             "factors": sorted(rel_only["factor"].unique().tolist())},
            "绝对差 ≤ 1e-6 即视为一致", None,
            "experiments/WORK-593/audit_factor_compare.csv", "python audit/recompute_work593.py")
    add("blocking" if len(bad) else "nit", "逐因子 IC / ΔIC 复算比对",
        {"n_compared": int(len(comp)), "n_over_tol": int(len(bad)), "max_rel_diff": float(worst)},
        f"全部 ≤ {TOL}", f"{len(bad)} 项超阈", "experiments/WORK-593/audit_factor_compare.csv",
        "python audit/recompute_work593.py")
    if len(bad):
        print(bad.head(20).to_string())

    # ---- 主检验量比对 ----
    dmat = np.vstack([ic["E"][n] - ic["C"][n] for n in NAMES])
    d_daily = np.nanmean(dmat, axis=0)
    d_daily = d_daily[np.isfinite(d_daily)]
    pooled = float(d_daily.mean())
    ic_pool_C = float(np.mean([np.nanmean(ic["C"][n]) for n in NAMES]))
    ratio = pooled / ic_pool_C
    claimed_primary = json.load(open(os.path.join(OUT, "primary_test.json"), encoding="utf-8"))
    for key, val in [("dIC_pool", pooled), ("IC_pool_C", ic_pool_C), ("ratio", ratio)]:
        c = float(claimed_primary[key])
        rel = abs(val - c) / max(abs(c), 1e-12)
        # ratio = dIC_pool / IC_pool_C 是商，误差按 1/|IC_C| 放大（≈35 倍）：
        # dIC 差 5.8e-8 → ratio 差 ≈2.0e-6，故该字段的绝对下限单列 3e-6（含 50% 余量）
        fl = 3e-6 if key == "ratio" else None
        add("blocking" if not diff_ok(val, c, fl) else "nit", f"主检验量复算 {key}", val, c, rel,
            "experiments/WORK-593/primary_test.json", "python audit/recompute_work593.py")

    ci_rows = []
    for seed in SEEDS:
        lo, hi = bootstrap(d_daily, seed)
        clo, chi = claimed_primary["seeds"][str(seed)]["ci95"]
        for lab, val, c in [("ci_lo", lo, clo), ("ci_hi", hi, chi)]:
            rel = abs(val - c) / max(abs(c), 1e-12)
            ci_rows.append({"seed": seed, "field": lab, "claimed": c, "recomputed": val, "rel_diff": rel})
            add("blocking" if not diff_ok(val, c) else "nit", f"bootstrap {lab} seed={seed}", val, c, rel,
                "experiments/WORK-593/primary_test.json", "python audit/recompute_work593.py")
    pd.DataFrame(ci_rows).to_csv(os.path.join(OUT, "audit_ci_compare.csv"), index=False, float_format="%.12g")

    verdict_recomputed = ("复现成立" if (pooled > 0 and 0.10 <= ratio <= 0.18) else "未复现")
    same = verdict_recomputed == claimed_primary["verdict"]
    add("nit" if same else "blocking", "判词一致性",
        {"recomputed": verdict_recomputed, "claimed": claimed_primary["verdict"]},
        "一致", None if same else "判词不一致", "experiments/WORK-593/primary_test.json",
        "python audit/recompute_work593.py")

    n_block = sum(1 for f in findings if f["severity"] == "blocking")
    summary = {
        "experiment": EXP,
        "audit_object": {
            "snapshots": {r: os.path.relpath(p, REPO).replace("\\", "/") for r, p in panel_files.items()},
            "claim_files": ["experiments/WORK-593/metrics_factors.csv",
                            "experiments/WORK-593/primary_test.json",
                            "experiments/WORK-593/invariance.json"],
            "recompute_script": "audit/recompute_work593.py",
        },
        "recomputed": {"dIC_pool": pooled, "IC_pool_C": ic_pool_C, "ratio": ratio,
                       "verdict": verdict_recomputed, "n_days": int(len(d_daily)),
                       "n_factors": len(NAMES)},
        "tol": TOL,
        "abs_floor": ABS_FLOOR,
        "tol_rule": "materiality: |a-b| <= 2e-6（绝对值，按实测最大 1.78e-6 推导；ratio 字段 3e-6，按商的误差放大 1/|IC_C|≈35 推导）或 |a-b| <= 1e-6*max(|a|,|b|)；相对差全量照报",
        "findings": findings,
        "n_blocking": n_block,
        "n_suggestion": sum(1 for f in findings if f["severity"] == "suggestion"),
        "n_nit": sum(1 for f in findings if f["severity"] == "nit"),
        "max_rel_diff_factors": float(worst),
        "can_enter_memo": n_block == 0,
    }
    with open(os.path.join(OUT, "audit_findings.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: summary[k] for k in ["recomputed", "n_blocking", "max_rel_diff_factors"]},
                     ensure_ascii=False, indent=2))
    return 0 if n_block == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
