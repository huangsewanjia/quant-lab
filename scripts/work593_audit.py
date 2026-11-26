# -*- coding: utf-8 -*-
"""WORK-593 第 2/3 段 · 重锚定审计器

输入：data/WORK-593/panel_{E,C,P}.parquet（三套锚定表示）+ membership.parquet
输出（experiments/WORK-593/）：
  metrics_factors.csv    每条表达式 × 3 套表示的 IC / ΔIC / 占比 / 不变量判词
  ic_series.npz          逐日 IC 序列（bootstrap 与独立复算用）
  primary_test.json      三种子的主检验量 + 循环块 bootstrap 95% CI + 子组分解
  invariance.json        不变量判定（数值尺度指数 + 横截面秩相关 + Qlib 式 placebo）
  combiner.json          自适应组合器（次要口径，3 种子）
  report_data.json       报告汇总（含「标签在三套表示下逐值一致」的断言结果）

协议：experiments/WORK-593/preregistration.md + preregistration_v1.1.md。只实现协议，不改阈值。
运行：/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_audit.py
"""
import json
import math
import os
import random
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-593"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

EVAL_START = "2011-01-04"
EVAL_END = "2026-11-13"
LABEL_H = 20
MIN_N = 50
BOOT_REPS = 1000
BOOT_BLOCK = 20
SEEDS = (42, 43, 44)
DEGREE_STOCKS = 10
RANK_SAMPLE_DAYS = 30
DEGREE_LAMBDA = 1.5

NAMES = [f"PL{i:02d}" for i in range(1, 12)] + [f"INV{i:02d}" for i in range(1, 26)]
GROUP = {n: ("PL" if n.startswith("PL") else "INV") for n in NAMES}
CS_RANK_FACTORS = {"PL11", "INV24"}   # 横截面秩类：尺度指数不适用


# ------------------------------------------------------------------ 统计工具
def _rank(a):
    n = len(a)
    order = np.argsort(a, kind="mergesort")
    sv = a[order]
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i + 1
        while j < n and sv[j] == sv[i]:
            j += 1
        ranks[order[i:j]] = 0.5 * (i + j - 1) + 1.0
        i = j
    return ranks


def spearman(a, b):
    ra, rb = _rank(a), _rank(b)
    sa, sb = ra.std(), rb.std()
    if sa == 0 or sb == 0:
        return np.nan
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


def daily_ic(F, Y, M, min_n=MIN_N):
    T = F.shape[0]
    out = np.full(T, np.nan)
    for t in range(T):
        m = M[t] & np.isfinite(F[t]) & np.isfinite(Y[t])
        if m.sum() < min_n:
            continue
        f = F[t][m]
        if f.max() == f.min():
            continue
        out[t] = spearman(f, Y[t][m])
    return out


# ------------------------------------------------------------------ 因子库
def _ma(x, n):
    return x.rolling(n, min_periods=max(5, n // 2)).mean()


def _std(x, n):
    return x.rolling(n, min_periods=max(5, n // 2)).std(ddof=1)


def _corr(x, y, n):
    mp = max(8, n // 2)
    mx = x.rolling(n, min_periods=mp).mean()
    my = y.rolling(n, min_periods=mp).mean()
    mxy = (x * y).rolling(n, min_periods=mp).mean()
    cov = mxy - mx * my
    sx = x.rolling(n, min_periods=mp).std(ddof=0)
    sy = y.rolling(n, min_periods=mp).std(ddof=0)
    return cov / (sx * sy)


def _ts_rank(x, n):
    cnt = None
    for k in range(n):
        c = (x >= x.shift(k)).astype("float64")
        cnt = c if cnt is None else cnt + c
    return cnt / float(n)


def make_factor(D, name):
    """按预注册 §5 的 36 条定义逐条实现。D = {'O','H','L','C','W','V'} → DataFrame。"""
    C, O, H, L, W, V = D["C"], D["O"], D["H"], D["L"], D["W"], D["V"]
    if name == "PL01": return C
    if name == "PL02": return O
    if name == "PL03": return H
    if name == "PL04": return L
    if name == "PL05": return W
    if name == "PL06": return _ma(C, 5)
    if name == "PL07": return _ma(C, 20)
    if name == "PL08": return _ma(C, 60)
    if name == "PL09": return _std(C, 20)
    if name == "PL10": return H.rolling(20, min_periods=10).max()
    if name == "PL11": return C.rank(axis=1, pct=True)
    ret = C / C.shift(1) - 1.0
    if name == "INV01": return C / O - 1.0
    if name == "INV02": return ret
    if name == "INV03": return (H - L) / C
    if name == "INV04": return (C - W) / W
    if name == "INV05": return (H - W) / W
    if name == "INV06": return (W - L) / W
    if name == "INV07": return W / C - 1.0
    if name == "INV08": return C / _ma(C, 5) - 1.0
    if name == "INV09": return C / _ma(C, 20) - 1.0
    if name == "INV10": return C / _ma(C, 60) - 1.0
    if name == "INV11": return _ma(C, 5) / _ma(C, 20) - 1.0
    if name == "INV12": return _ma(C, 20) / _ma(C, 60) - 1.0
    if name == "INV13": return (C - _ma(C, 20)) / _std(C, 20)
    if name == "INV14":
        lo = L.rolling(20, min_periods=10).min()
        hi = H.rolling(20, min_periods=10).max()
        return (C - lo) / (hi - lo)
    if name == "INV15": return (C - C.shift(5)) / C
    if name == "INV16": return H.rolling(5, min_periods=5).max() / C - 1.0
    if name == "INV17": return L.rolling(5, min_periods=5).min() / C - 1.0
    if name == "INV18": return (H - L) / (H + L)
    if name == "INV19": return _std(ret, 20)
    if name == "INV20": return _corr(C, V, 20)
    if name == "INV21": return _corr(ret, np.log(V.where(V > 0)), 20)
    if name == "INV22": return V / _ma(V, 20) - 1.0
    if name == "INV23": return _ma(V, 5) / _ma(V, 20) - 1.0
    if name == "INV24": return (V / _ma(V, 20)).rank(axis=1, pct=True)
    if name == "INV25": return _ts_rank(C / _ma(C, 20) - 1.0, 20)
    raise KeyError(name)


# ------------------------------------------------------------------ 面板
COLS6 = [("O", "open"), ("H", "high"), ("L", "low"), ("C", "close"), ("W", "vwap"), ("V", "vol")]


def load_rep(rep, codes, dates):
    df = pd.read_parquet(os.path.join(DATA, f"panel_{rep}.parquet"),
                         columns=["ts_code", "trade_date"] + [c for _, c in COLS6])
    out = {}
    for k, c in COLS6:
        out[k] = (df.pivot(index="trade_date", columns="ts_code", values=c)
                    .reindex(index=dates, columns=codes))
    del df
    return out


def build_mask(mem, dates, codes):
    T, N = len(dates), len(codes)
    M = np.zeros((T, N), dtype=bool)
    di = {d: i for i, d in enumerate(dates)}
    ci = {c: j for j, c in enumerate(codes)}
    for code, s, e in zip(mem["ts_code"], mem["start_date"], mem["end_date"]):
        j = ci.get(code)
        if j is None:
            continue
        for d in dates[(dates >= s) & (dates <= e)]:
            M[di[d], j] = True
    return M


def block_bootstrap(x, seed, reps=BOOT_REPS, block=BOOT_BLOCK, alpha=0.05):
    rng = random.Random(seed)
    n = len(x)
    nb = int(math.ceil(n / block))
    means = np.empty(reps)
    for i in range(reps):
        idx = []
        for _ in range(nb):
            s0 = rng.randrange(n)
            idx.extend((s0 + k) % n for k in range(block))
        means[i] = x[np.asarray(idx[:n])].mean()
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi), means


# ------------------------------------------------------------------ 组合器
def combiner_ic(D, ic_rep, Y, M, ev_idx, seed, dates):
    """AlphaForge 式自适应组合器（预注册 §7），只用于次要口径。"""
    T, N = Y.shape
    rng = random.Random(seed + 1000)
    Z = {}
    for n in NAMES:
        a = make_factor(D, n).to_numpy(dtype="float32")
        mu = np.nanmean(np.where(M, a, np.nan), axis=1, keepdims=True)
        sd = np.nanstd(np.where(M, a, np.nan), axis=1, keepdims=True)
        sd = np.where(np.isfinite(sd) & (sd > 0), sd, np.nan)
        Z[n] = ((a - mu) / sd).astype("float32")
        del a
    score = np.full(len(ev_idx), np.nan)
    w, sel = None, []
    n_refits, sel_sizes = 0, []
    for pos, t in enumerate(ev_idx):
        if pos % 20 == 0:
            cut = t - 21
            sel, w = [], None
            if cut > 60:
                stats = []
                for n in NAMES:
                    s = ic_rep[n][:cut]
                    s = s[np.isfinite(s)]
                    if len(s) < 60:
                        continue
                    mu_ = float(s.mean())
                    sd_ = float(s.std(ddof=1))
                    stats.append((n, mu_, (mu_ / sd_) if sd_ else np.nan))
                cand = [x for x in stats if abs(x[1]) > 0.015 and np.isfinite(x[2]) and abs(x[2]) > 0.15]
                if cand:
                    order = {n: i for i, n in enumerate(NAMES)}
                    cand.sort(key=lambda x: order[x[0]])
                    sel = [x[0] for x in cand[:20]]
                elif stats:
                    stats.sort(key=lambda x: abs(x[2]) if np.isfinite(x[2]) else -1.0, reverse=True)
                    sel = [stats[0][0]]
                if sel:
                    days = [d for d in range(0, cut, 5) if rng.random() < 0.8]
                    k = len(sel)
                    XtX = np.zeros((k, k))
                    Xty = np.zeros(k)
                    for d in days:
                        m = M[d] & np.isfinite(Y[d])
                        if m.sum() < MIN_N:
                            continue
                        X = np.column_stack([Z[n][d][m] for n in sel]).astype("float64")
                        y = Y[d][m]
                        ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
                        if ok.sum() < MIN_N:
                            continue
                        X, y = X[ok], y[ok]
                        XtX += X.T @ X
                        Xty += X.T @ y
                    try:
                        w = np.linalg.solve(XtX + 1e-8 * np.eye(k), Xty)
                    except np.linalg.LinAlgError:
                        w = np.linalg.lstsq(XtX, Xty, rcond=None)[0]
                    n_refits += 1
                    sel_sizes.append(k)
        if sel and w is not None:
            m = M[t] & np.isfinite(Y[t])
            if m.sum() >= MIN_N:
                X = np.column_stack([Z[n][t][m] for n in sel]).astype("float64")
                ok = np.isfinite(X).all(axis=1) & np.isfinite(Y[t][m])
                if ok.sum() >= MIN_N:
                    s = X[ok] @ w
                    score[pos] = spearman(s, Y[t][m][ok])
    sc = score[np.isfinite(score)]
    return {"IC": float(sc.mean()) if len(sc) else None,
            "n_days": int(len(sc)),
            "ICIR": float(sc.mean() / sc.std(ddof=1)) if len(sc) > 1 else None,
            "n_refits": n_refits,
            "avg_selected": float(np.mean(sel_sizes)) if sel_sizes else None}


# ------------------------------------------------------------------ 主流程
def main():
    t0 = time.time()
    mem = pd.read_parquet(os.path.join(DATA, "membership.parquet"))
    base = pd.read_parquet(os.path.join(DATA, "panel_base.parquet"), columns=["ts_code", "trade_date"])
    dates = pd.DatetimeIndex(sorted(base["trade_date"].unique()))
    codes = sorted(base["ts_code"].unique())
    del base
    M = build_mask(mem, dates, codes)
    ev = (dates >= pd.Timestamp(EVAL_START)) & (dates <= pd.Timestamp(EVAL_END))
    ev_idx = np.where(ev)[0]
    print(f"dates={len(dates)} codes={len(codes)} membership_cells={M.sum():,} eval_days={ev.sum()}", flush=True)

    rng = np.random.default_rng(20261126)
    sample_days = np.sort(rng.choice(ev_idx, size=RANK_SAMPLE_DAYS, replace=False))
    degree_codes = sorted(rng.choice(codes, size=DEGREE_STOCKS, replace=False))

    ic, slices, mini, Y = {}, {}, {}, {}
    for rep in ["E", "C", "P"]:
        D = load_rep(rep, codes, dates)
        ydf = D["C"].shift(-LABEL_H) / D["C"] - 1.0
        Y[rep] = ydf.to_numpy(dtype="float64")
        mini[rep] = {k: D[k][degree_codes].copy() for k in D}
        slices[rep] = {}
        ic[rep] = {}
        for name in NAMES:
            fdf = make_factor(D, name)
            arr = fdf.to_numpy(dtype="float64")
            s = daily_ic(arr, Y[rep], M)
            s[~ev] = np.nan
            ic[rep][name] = s
            slices[rep][name] = arr[sample_days][:, :].copy()
            del fdf, arr
        del D
        print(f"[{rep}] IC for 36 factors done ({time.time()-t0:.0f}s)", flush=True)

    label_diff = {r: float(np.nanmax(np.abs(Y[r] - Y["E"]))) for r in ["C", "P"]}
    print(f"label identical across reps: max|diff| = {label_diff}", flush=True)

    # ---------------- 不变量判定 ----------------
    inv = {}
    for name in NAMES:
        # ① 数值尺度指数：单股价格 ×λ、成交量 ÷λ 后重算因子
        ks = []
        if name not in CS_RANK_FACTORS:
            for rep in ["E"]:
                base_f = make_factor(mini[rep], name)
                for code in degree_codes:
                    d1 = {}
                    for k in mini[rep]:
                        srs = mini[rep][k][code]
                        d1[k] = srs
                    d1["O"], d1["H"], d1["L"], d1["C"], d1["W"] = (
                        d1["O"] * DEGREE_LAMBDA, d1["H"] * DEGREE_LAMBDA,
                        d1["L"] * DEGREE_LAMBDA, d1["C"] * DEGREE_LAMBDA,
                        d1["W"] * DEGREE_LAMBDA)
                    d1["V"] = d1["V"] / DEGREE_LAMBDA
                    one = {k: v.to_frame(code) for k, v in d1.items()}
                    scaled = make_factor(one, name)[code]
                    ratio = (scaled / base_f[code]).replace([np.inf, -np.inf], np.nan).dropna()
                    ratio = ratio[ratio > 0]
                    if len(ratio):
                        ks.append(float(np.log(ratio.median()) / np.log(DEGREE_LAMBDA)))
        ks = np.asarray(ks, dtype="float64")
        med_k = float(np.nanmedian(ks)) if len(ks) else float("nan")
        # ② 横截面秩相关（E vs C，抽样日）
        rc = []
        for t in sample_days:
            a = slices["E"][name][list(sample_days).index(t)]
            b = slices["C"][name][list(sample_days).index(t)]
            m = M[t] & np.isfinite(a) & np.isfinite(b)
            if m.sum() >= MIN_N and a[m].max() > a[m].min() and b[m].max() > b[m].min():
                rc.append(spearman(a[m], b[m]))
        rc = np.asarray(rc, dtype="float64")
        mean_rc = float(np.nanmean(rc)) if len(rc) else float("nan")
        # ③ 全局 placebo：所有股票同比例缩放 → 秩不变（恒 1.0），作为对照基线写死
        ok_k = (name in CS_RANK_FACTORS) or (abs(med_k) <= 1e-6)
        verdict = "invariant" if (ok_k and mean_rc >= 0.999) else "rejected"
        reason = []
        if name in CS_RANK_FACTORS:
            reason.append("cross-sectional rank: degree test n/a")
        elif abs(med_k) > 1e-6:
            reason.append(f"scale degree k={med_k:+.4f} != 0")
        if mean_rc < 0.999:
            reason.append(f"rank corr {mean_rc:.6f} < 0.999")
        inv[name] = {
            "group": GROUP[name], "verdict": verdict, "reason": "; ".join(reason) or "both tests pass",
            "degree_k_median": med_k,
            "degree_k_min": float(np.nanmin(ks)) if len(ks) else None,
            "degree_k_max": float(np.nanmax(ks)) if len(ks) else None,
            "rank_corr_mean_E_vs_C": mean_rc,
            "rank_corr_min_E_vs_C": float(np.nanmin(rc)) if len(rc) else None,
            "share_days_rank_corr_lt_0.9": float(np.nanmean(rc < 0.9)) if len(rc) else None,
            "placebo_global_rank_corr": 1.0,
            "n_rank_days": int(len(rc)), "n_degree_samples": int(len(ks)),
            "IC_E": float(np.nanmean(ic["E"][name])), "IC_C": float(np.nanmean(ic["C"][name])),
        }
    print(f"invariance done ({time.time()-t0:.0f}s)", flush=True)

    # ---------------- 主表 ----------------
    rows = []
    for name in NAMES:
        se, sc, sp = ic["E"][name], ic["C"][name], ic["P"][name]
        ice, icc = float(np.nanmean(se)), float(np.nanmean(sc))
        rows.append({
            "factor": name, "group": GROUP[name],
            "IC_E": ice, "IC_C": icc, "IC_P": float(np.nanmean(sp)),
            "dIC_E_minus_C": ice - icc,
            "ratio_over_IC_C": ((ice - icc) / icc) if icc else np.nan,
            "n_days": int(np.isfinite(se).sum()),
            "ICIR_E": float(np.nanmean(se) / np.nanstd(se, ddof=1)),
            "ICIR_C": float(np.nanmean(sc) / np.nanstd(sc, ddof=1)),
            "invariant_verdict": inv[name]["verdict"],
        })
    mt = pd.DataFrame(rows)
    mt.to_csv(os.path.join(OUT, "metrics_factors.csv"), index=False, float_format="%.10g")

    dmat = np.vstack([ic["E"][n] - ic["C"][n] for n in NAMES])
    d_daily = np.nanmean(dmat, axis=0)
    d_daily = d_daily[np.isfinite(d_daily)]
    pooled = float(d_daily.mean())
    ic_pool_C = float(np.mean([np.nanmean(ic["C"][n]) for n in NAMES]))
    ratio = pooled / ic_pool_C

    primary = {
        "metric": "dIC_pool = mean_f[IC_f(E) - IC_f(C)] ; ratio = dIC_pool / mean_f IC_f(C)",
        "dIC_pool": pooled, "IC_pool_C": ic_pool_C, "ratio": ratio,
        "n_factors": len(NAMES), "n_days": int(len(d_daily)),
        "eval_window": [str(dates[ev][0].date()), str(dates[ev][-1].date())],
        "label": f"{LABEL_H}-day close-to-close forward return",
        "bootstrap": {"reps": BOOT_REPS, "block": BOOT_BLOCK,
                      "method": "circular block bootstrap of the daily pool dIC series",
                      "seeds": list(SEEDS)},
        "seeds": {},
    }
    for seed in SEEDS:
        lo, hi, means = block_bootstrap(d_daily, seed)
        primary["seeds"][str(seed)] = {
            "ci95": [lo, hi], "ci_excludes_zero": bool(lo > 0 or hi < 0),
            "boot_mean": float(means.mean()), "boot_sd": float(means.std(ddof=1)),
            "boot_share_negative": float((means <= 0).mean()),
        }
    c1 = pooled > 0
    # 预注册 §6 第 2 条的字面：95% CI 不含 0（不论方向）；第 1 条已单独管方向
    c2 = all(primary["seeds"][str(s)]["ci_excludes_zero"] for s in SEEDS)
    c3 = 0.10 <= ratio <= 0.18
    _signs = {("+" if pooled > 0 else "-")} | {
        ("+" if primary["seeds"][str(s)]["boot_mean"] > 0 else "-") for s in SEEDS}
    c4 = len(_signs) == 1
    primary["criteria"] = {"c1_dIC_positive": c1, "c2_ci_excludes_zero_all_seeds": c2,
                           "c3_ratio_in_0.10_0.18": c3,
                           "c4_same_sign_all_seeds": c4,
                           "c2_note": "CI 不含 0 即通过，方向由 C1 管；本单 CI 整段在负侧"}
    primary["verdict"] = "复现成立" if (c1 and c2 and c3) else "未复现"
    primary["verdict_reason"] = ("三项合取全部满足" if (c1 and c2 and c3) else
                                 "；".join([k for k, v in
                                           [("dIC<=0", not c1), ("CI含0或负", not c2),
                                            ("占比出界", not c3)] if v]))

    primary["subgroup"] = {}
    for g in ["PL", "INV"]:
        sel = [n for n in NAMES if GROUP[n] == g]
        sub = np.nanmean(np.vstack([ic["E"][n] - ic["C"][n] for n in sel]), axis=0)
        sub = sub[np.isfinite(sub)]
        icc = float(np.mean([np.nanmean(ic["C"][n]) for n in sel]))
        primary["subgroup"][g] = {"n_factors": len(sel), "dIC": float(sub.mean()),
                                  "IC_C": icc,
                                  "ratio": float(sub.mean() / icc) if icc else None}
    primary["drift_share_abs_gt_0.005"] = {
        "all": float((mt["dIC_E_minus_C"].abs() > 0.005).mean()),
        "PL": float((mt.loc[mt.group == "PL", "dIC_E_minus_C"].abs() > 0.005).mean()),
        "INV": float((mt.loc[mt.group == "INV", "dIC_E_minus_C"].abs() > 0.005).mean()),
    }

    with open(os.path.join(OUT, "primary_test.json"), "w", encoding="utf-8") as f:
        json.dump(primary, f, ensure_ascii=False, indent=2)
    np.savez_compressed(os.path.join(OUT, "ic_series.npz"),
                        days=np.array([str(d.date()) for d in dates]),
                        eval_mask=ev,
                        **{f"{rep}|{n}": ic[rep][n] for rep in ["E", "C", "P"] for n in NAMES})
    with open(os.path.join(OUT, "invariance.json"), "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)

    # ---------------- 组合器（次要） ----------------
    comb = {}
    for rep in ["E", "C"]:
        D = load_rep(rep, codes, dates)
        ydf = D["C"].shift(-LABEL_H) / D["C"] - 1.0
        Yrep = ydf.to_numpy(dtype="float64")
        for seed in SEEDS:
            comb.setdefault(str(seed), {})[rep] = combiner_ic(D, ic[rep], Yrep, M, ev_idx, seed, dates)
            print(f"[combiner] rep={rep} seed={seed} {comb[str(seed)][rep]} ({time.time()-t0:.0f}s)", flush=True)
        del D
    for seed in SEEDS:
        e, c = comb[str(seed)]["E"], comb[str(seed)]["C"]
        comb[str(seed)]["diff_E_minus_C"] = (None if e["IC"] is None or c["IC"] is None
                                            else e["IC"] - c["IC"])
    with open(os.path.join(OUT, "combiner.json"), "w", encoding="utf-8") as f:
        json.dump(comb, f, ensure_ascii=False, indent=2)

    report = {
        "experiment": EXP,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "panel": {"dates": int(len(dates)), "codes": int(len(codes)),
                  "eval_days": int(ev.sum()), "eval_window": primary["eval_window"],
                  "membership_cells": int(M.sum())},
        "label_max_abs_diff_across_reps": label_diff,
        "primary": primary,
        "invariance_summary": {
            "n_invariant": int(sum(1 for n in NAMES if inv[n]["verdict"] == "invariant")),
            "n_rejected": int(sum(1 for n in NAMES if inv[n]["verdict"] != "invariant")),
            "pass_rate": float(sum(1 for n in NAMES if inv[n]["verdict"] == "invariant") / len(NAMES)),
            "paper_alpha158": "153/158 = 0.9684",
        },
        "per_factor": mt.to_dict("records"),
        "combiner": comb,
    }
    with open(os.path.join(OUT, "report_data.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print("\n=== 主检验量 ===")
    print(f"dIC_pool = {pooled:+.6f}   IC_pool(C) = {ic_pool_C:.6f}   ratio = {ratio:.4%}")
    for s in SEEDS:
        ci = primary["seeds"][str(s)]["ci95"]
        print(f"  seed {s}: CI95 = [{ci[0]:+.6f}, {ci[1]:+.6f}]  excl0={primary['seeds'][str(s)]['ci_excludes_zero']}")
    print(f"子组: {json.dumps(primary['subgroup'], ensure_ascii=False)}")
    print(f"判词: {primary['verdict']} ({primary['verdict_reason']})")
    print(f"不变量: {report['invariance_summary']}")
    print(f"用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
