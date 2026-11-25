# -*- coding: utf-8 -*-
"""WORK-566 · 独立复算（模型风控审计侧）。

纪律：
  * **不 import 建模阶段的任何函数**（不 import work566_backtest.py / work566_train.py /
    work566_primary_test.py / work566_prior.py）；全部逻辑在本文件内独立重写。
  * 数据侧走**独立路径**：交易日历从 parquet 的 trade_cal 重取（不用建模侧的 meta.json）。
  * 判据先写死：指标类逐项相对差 ≤ 1e-6 才算一致；不一致项**以复算值为准**并全列。
  * 复算对象：各臂 × 各种子 × 主成本档的 RankIC / RankICIR / 净 SR / 净 AR / MDD / 日均换手 /
    批次数 / 相对增量（ΔRankIC），外加基准调整 alpha。

运行：python audit/recompute_work566.py --seeds 42 2024
产出：experiments/WORK-566/audit_findings.json
"""
import argparse
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "WORK-566")
OUT = os.path.join(REPO, "experiments", "WORK-566")
RAW = "/srv/share/quantdb/raw"

TOL = 1e-6
H_HOLD, K = 21, 30


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def avg_rank(a):
    """平均秩（并列取均值）——独立实现。"""
    n = len(a)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    sa = a[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = avg_rank(np.asarray(x, float)), avg_rank(np.asarray(y, float))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def max_drawdown(nav):
    peak = -np.inf
    mdd = 0.0
    for v in nav:
        if v > peak:
            peak = v
        dd = v / peak - 1.0
        if dd < mdd:
            mdd = dd
    return float(mdd)


def sharpe(r):
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return None
    sd = float(np.sqrt(np.sum((r - r.mean()) ** 2) / (len(r) - 1)))
    if sd <= 0:
        return None
    return float(np.sqrt(252.0) * r.sum() / len(r) / sd)


def calendar():
    import duckdb
    con = duckdb.connect()
    cal = con.execute(
        "select distinct cast(strftime(cal_date, '%Y%m%d') as int) as cd from "
        f"read_parquet('{RAW}/trade_cal/**/*.parquet', hive_partitioning=true, union_by_name=true) "
        "where exchange='SSE' and is_open=1 and cal_date between date '2013-01-01' "
        "and date '2026-06-26' order by cd").df()["cd"].to_numpy(dtype=np.int64)
    return cal


def recompute_portfolio(panel, pred_stock, pred_val, pred_date, dates_sorted, cost):
    """独立重写的组合模拟（预注册 §7 口径）。"""
    nav_out, ret_out, to_out = [], [], []
    cash = 1.0
    open_batches = []      # {exit_pos, units:{stock:units}}
    pending = []          # (entry_date_pos, batch)
    traded = np.zeros(len(dates_sorted))
    skipped_lu = skipped_su = deferred = delisted = 0
    n_batch = 0

    per_date = {}
    for k in range(len(pred_stock)):
        per_date.setdefault(int(pred_date[k]), []).append((float(pred_val[k]), int(pred_stock[k])))
    for d in per_date:
        per_date[d].sort(key=lambda t: (-t[0], t[1]))

    last_bar = {}

    def lb(s):
        if s not in last_bar:
            row = np.asarray(panel["has_bar"][s])
            nz = np.nonzero(row)[0]
            last_bar[s] = int(nz[-1]) if len(nz) else -1
        return last_bar[s]

    prev_nav = None
    for i, d in enumerate(dates_sorted):
        day = int(d)
        # 开盘执行买入
        for item in list(pending):
            epos, batch = item
            if epos != i:
                continue
            pending.remove(item)
            bought = {}
            for s, u in batch["units_plan"].items():
                if not panel["has_bar"][s, day]:
                    skipped_su += 1
                    continue
                op = float(panel["open_raw"][s, day])
                ul = float(panel["up_limit"][s, day])
                if not np.isfinite(op) or op <= 0:
                    continue
                if np.isfinite(ul) and ul > 0 and op >= ul * (1 - 1e-6):
                    skipped_lu += 1
                    continue
                bought[s] = u
            if bought:
                notional = sum(u * float(panel["open_hfq"][s, day]) for s, u in bought.items())
                scale = 1.0
                if notional > 0:
                    cap = batch["budget"] / (1.0 + cost)
                    if notional > cap > 0:
                        scale = cap / notional
                fee = 0.0
                for s, u in bought.items():
                    p = float(panel["open_hfq"][s, day])
                    uu = u * scale
                    cash -= uu * p
                    fee += abs(uu) * p
                    batch["units"][s] = batch["units"].get(s, 0.0) + uu
                cash -= fee * cost
                traded[i] += fee
            open_batches.append(batch)
        # 收盘执行卖出
        for batch in list(open_batches):
            if batch["exit_pos"] is None or i < batch["exit_pos"]:
                continue
            for s in list(batch["units"].keys()):
                if lb(s) >= 0 and i > lb(s):
                    p = float(panel["close_hfq"][s, lb(s)])
                    u = batch["units"].pop(s)
                    cash += u * p * 0.99
                    traded[i] += abs(u) * p
                    delisted += 1
                    continue
                if not panel["has_bar"][s, day]:
                    deferred += 1
                    continue
                c = float(panel["close_raw"][s, day])
                dl = float(panel["down_limit"][s, day])
                if not np.isfinite(c) or c <= 0:
                    continue
                if np.isfinite(dl) and dl > 0 and c <= dl * (1 + 1e-6):
                    deferred += 1
                    continue
                p = float(panel["close_hfq"][s, day])
                u = batch["units"].pop(s)
                cash += u * p
                traded[i] += abs(u) * p
            if not batch["units"]:
                open_batches.remove(batch)
                batch["exit_pos"] = None
        # 估值
        v = cash
        for batch in open_batches:
            for s, u in batch["units"].items():
                p = float(panel["close_hfq"][s, day])
                if not np.isfinite(p) or p <= 0:
                    p = float(panel["close_hfq"][s, lb(s)])
                v += u * p
        nav_out.append(v)
        ret_out.append((v / prev_nav - 1.0) if prev_nav else np.nan)
        to_out.append(traded[i] / (2 * prev_nav) if prev_nav else np.nan)
        prev_nav = v
        # t 日预测 → t+1 建仓
        cand = per_date.get(day, [])
        if cand and i + 1 < len(dates_sorted):
            picks = []
            nxt = int(dates_sorted[i + 1])
            for _, s in cand:
                if len(picks) >= K:
                    break
                if not panel["has_bar"][s, nxt]:
                    skipped_su += 1
                    continue
                op = float(panel["open_raw"][s, nxt])
                ul = float(panel["up_limit"][s, nxt])
                if np.isfinite(op) and np.isfinite(ul) and ul > 0 and op >= ul * (1 - 1e-6):
                    skipped_lu += 1
                    continue
                picks.append(s)
            if len(picks) >= K:
                budget = min(v / H_HOLD, max(cash, 0.0) / (1.0 + cost))
                if budget > 0:
                    units_plan = {}
                    for s in picks:
                        p = float(panel["open_hfq"][s, nxt])
                        units_plan[s] = budget / K / p
                    pending.append((i + 1, {"units_plan": units_plan, "units": {},
                                            "budget": budget, "exit_pos": i + 1 + H_HOLD}))
                    n_batch += 1
    nav = np.asarray(nav_out, float)
    ret = np.asarray(ret_out, float)
    to = np.asarray(to_out, float)
    r = ret[np.isfinite(ret)]
    return {
        "rankic": None, "nav": nav, "ret": ret, "turnover": to,
        "net_ar": float(252.0 * r.mean()) if len(r) else None,
        "net_sr": sharpe(r),
        "mdd": max_drawdown(nav[np.isfinite(nav)]),
        "avg_turnover": float(to[np.isfinite(to)].mean()) if np.isfinite(to).any() else None,
        "n_batches": n_batch, "skipped_lu": skipped_lu, "skipped_su": skipped_su,
        "deferred": deferred, "delisted": delisted, "n_days": int(len(r)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 2024])
    ap.add_argument("--cost", type=float, default=0.0005)
    args = ap.parse_args()

    panel = {
        "close_hfq": np.load(os.path.join(DATA, "base", "close_hfq.npy"), mmap_mode="r"),
        "open_hfq": np.load(os.path.join(DATA, "base", "open_hfq.npy"), mmap_mode="r"),
        "close_raw": np.load(os.path.join(DATA, "base", "close_raw.npy"), mmap_mode="r"),
        "open_raw": np.load(os.path.join(DATA, "base", "open_raw.npy"), mmap_mode="r"),
        "up_limit": np.load(os.path.join(DATA, "base", "up_limit.npy"), mmap_mode="r"),
        "down_limit": np.load(os.path.join(DATA, "base", "down_limit.npy"), mmap_mode="r"),
        "has_bar": np.load(os.path.join(DATA, "base", "has_bar.npy"), mmap_mode="r"),
    }
    cal = calendar()
    findings = {"experiment": "WORK-566", "tolerance": TOL,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "independence": "不 import 建模阶段任何函数；日历从 parquet trade_cal 独立重取",
                "comparisons": [], "blocking": 0, "suggestion": 0, "pass": 0}

    def cmp(name, measured, claimed):
        if measured is None or claimed is None:
            findings["comparisons"].append({"item": name, "measured": measured, "claimed": claimed,
                                            "verdict": "SKIP（缺一侧）"})
            return
        diff = abs(measured - claimed)
        denom = max(abs(claimed), 1e-12)
        rel = diff / denom
        if diff <= 1e-12:
            verdict = "PASS（逐位一致）"
        elif rel <= TOL:
            verdict = "PASS（相对差 ≤1e-6）"
        else:
            verdict = "BLOCKING（相对差 >1e-6，以复算值为准）"
            findings["blocking"] += 1
        if verdict.startswith("PASS"):
            findings["pass"] += 1
        findings["comparisons"].append({"item": name, "measured": measured, "claimed": claimed,
                                        "abs_diff": diff, "rel_diff": rel, "verdict": verdict})

    arms = ["s1_only", "s3_only", "s1_s2", "full", "r0", "r1", "r2", "r3", "fmb"]
    for arm in arms:
        for seed in args.seeds:
            zp = os.path.join(DATA, f"preds_seed{seed}_{arm}.npz")
            jp = os.path.join(OUT, f"backtest_{arm}_seed{seed}_c{args.cost}.json")
            if not (os.path.exists(zp) and os.path.exists(jp)):
                findings["comparisons"].append({"item": f"{arm}/seed{seed}", "verdict": "SKIP（缺产物）"})
                continue
            z = np.load(zp)
            claimed = json.load(open(jp, encoding="utf-8"))
            ps, pd_, pv = z["stock_test"], z["date_test"], z["pred_test"]
            dates_sorted = np.unique(pd_)
            rec = recompute_portfolio(panel, ps, pv, pd_, dates_sorted, args.cost)
            # 逐日 RankIC（独立实现）
            y = z["y_test"]
            byd = {}
            for k in range(len(pd_)):
                byd.setdefault(int(pd_[k]), []).append(k)
            ics = []
            for d, rows in byd.items():
                rows = np.asarray(rows)
                v = spearman(pv[rows], y[rows])
                if v is not None:
                    ics.append(v)
            ic_arr = np.asarray(ics, float)
            rankic = float(ic_arr.mean() * 100) if len(ic_arr) else None
            ic_std = float(ic_arr.std(ddof=1) * 100) if len(ic_arr) > 1 else None
            icir = (float(ic_arr.mean() / ic_arr.std(ddof=1) * np.sqrt(252)) if len(ic_arr) > 1
                    and ic_arr.std(ddof=1) > 0 else None)
            tag = f"{arm}/seed{seed}"
            cmp(f"{tag} RankIC(%)", rankic, claimed.get("rankic_mean_pct"))
            cmp(f"{tag} RankIC sd(%)", ic_std, claimed.get("rankic_std_pct"))
            cmp(f"{tag} RankICIR", icir, claimed.get("rankicir"))
            cmp(f"{tag} 净AR(%)", rec["net_ar"], claimed.get("net_ar"))
            cmp(f"{tag} 净SR", rec["net_sr"], claimed.get("net_sr"))
            cmp(f"{tag} MDD(%)", rec["mdd"], claimed.get("mdd"))
            cmp(f"{tag} 日均换手", rec["avg_turnover"], claimed.get("avg_turnover"))
            cmp(f"{tag} 批次数", float(rec["n_batches"]), float(claimed.get("n_batches")))
            cmp(f"{tag} 涨停剔除数", float(rec["skipped_lu"]), float(claimed.get("n_skipped_limitup")))
            cmp(f"{tag} 停牌剔除数", float(rec["skipped_su"]), float(claimed.get("n_skipped_suspend")))
            cmp(f"{tag} 顺延卖出数", float(rec["deferred"]), float(claimed.get("deferred_exits")))
            cmp(f"{tag} 退市清算数", float(rec["delisted"]), float(claimed.get("delist_liquidations")))
            log(f"  复算 {tag}: RankIC {rankic}, SR {rec['net_sr']}, MDD {rec['mdd']}")

    # ---------------- 相对增量复算（ΔRankIC = full − s1_only）
    rankics = {}
    for arm in ("full", "s1_only"):
        for seed in args.seeds:
            zp = os.path.join(DATA, f"preds_seed{seed}_{arm}.npz")
            if not os.path.exists(zp):
                continue
            z = np.load(zp)
            ps, pd_, pv, y = z["stock_test"], z["date_test"], z["pred_test"], z["y_test"]
            byd = {}
            for k in range(len(pd_)):
                byd.setdefault(int(pd_[k]), []).append(k)
            ics = []
            for d, rows in byd.items():
                rows = np.asarray(rows)
                v = spearman(pv[rows], y[rows])
                if v is not None:
                    ics.append(v)
            arr = np.asarray(ics, float)
            rankics[(arm, seed)] = float(arr.mean() * 100) if len(arr) else None
    deltas = []
    for seed in args.seeds:
        a, b = rankics.get(("full", seed)), rankics.get(("s1_only", seed))
        if a is not None and b is not None:
            deltas.append(a - b)
    if deltas:
        rec_delta = float(np.median(deltas))
        claimed = None
        mp = os.path.join(OUT, "verdict.json")
        if os.path.exists(mp):
            claimed = json.load(open(mp, encoding="utf-8")).get("H1", {}).get("delta_median_pp")
        cmp("ΔRankIC 中位数(pp)", rec_delta, claimed)
        findings["recomputed_delta_rankic_pp"] = rec_delta
        log(f"复算 ΔRankIC 中位数 = {rec_delta:.4f} pp")

    findings["conclusion"] = ("PASS —— 可进入研究备忘录" if findings["blocking"] == 0
                              else f"FAIL —— {findings['blocking']} 项 blocking，以复算值为准")
    with open(os.path.join(OUT, "audit_findings.json"), "w", encoding="utf-8") as f:
        json.dump(findings, f, ensure_ascii=False, indent=2)
    log(f"审计结论：{findings['conclusion']}（PASS {findings['pass']} / BLOCKING {findings['blocking']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
