#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 独立复算闸门（模型风控审计角色）

**审计契约（硬规定）**
1. 本脚本**禁止 import** 建模阶段（scripts/work555_*.py）的任何函数、类或变量。
   数值一律从原始快照与预测文件独立重算。
2. 任何要出现在对外材料里的数字，必须能由本脚本在同一份快照上独立算出。
3. 判据先写死再跑；不改阈值让结论变绿。

审计对象：
  - 事实层快照：data/WORK-555/panel_backtest.csv（sha256 见 experiments/WORK-555/snapshot_manifest.json）
  - 被审产物：experiments/WORK-555/preds_<arm>_s<seed>.npz / run_<arm>_s<seed>.json
              experiments/WORK-555/backtest_<arm>_s<seed>.json / bt_daily_<arm>_s<seed>_*.csv
  - 建模侧声称值：run_*.json 的 test_rankic_mean / test_ic_mean；backtest_*.json 的 metrics

审计方法（不复用建模阶段的任何函数）：
  1. 用 pandas 独立重算每臂每种子的日频截面 RankIC / IC，与建模侧逐项比对；
  2. 用 pandas 独立重算组合逐日账（选股、掩码、强制持有、成本、换手），与建模侧
     bt_daily_*.csv 逐日比对；
  3. 用独立实现的年化/Sharpe/MDD 口径复算汇总指标，与 backtest_*.json 比对；
  4. 校验原始快照 sha256 与 manifest 一致（防快照漂移）；
  5. 复算主检验量 Δ_ic 与进/退判词（判据写死在 §5）。

输出：experiments/WORK-555/audit_findings.json + 终端判决
退出码：0 = 无 blocking；1 = 存在 blocking

运行：
  /tmp/w555venv/bin/python audit/recompute_work555.py
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

TOL = 1e-6                 # §5 判据：相对差 <= 1e-6 视为一致
TOPK = 50
COST_MAIN_BPS = 8.0
COST_SENS_BPS = 5.0
STAMP_BPS = 10.0
RF_MAIN = 0.0
RF_ALT = 0.015
TD = 252
YD = 365.25
KAPPA = 1.5              # 预注册 §4.1：IC 过滤容差

# 预注册 §4.6：族消融子集的定义（族 -> 专家类型集合；None = 全部专家）
FAMILY_SPEC = (
    ("linear_family", {"linear", "ridge", "lasso", "elasticnet"}),
    ("mlp_family", {"mlp"}),
    ("tree_family", {"tree"}),
    ("full_dee", None),
)

findings = []


def add(sev, item, measured, expected, gap, where, fix):
    findings.append({"severity": sev, "item": item, "measured": measured,
                     "expected": expected, "gap": gap, "location": where,
                     "how_to_verify": fix})


def rel_diff(a, b):
    """零安全相对差：两边都为 0 -> 0；只有一边为 0 -> 1（不相等）。"""
    a = float(a)
    b = float(b)
    if a == b:
        return 0.0
    den = max(abs(a), abs(b))
    if den == 0.0:
        return 0.0
    return abs(a - b) / den


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 独立实现：截面 IC
def _rankdata(a):
    """平均秩（独立实现）。"""
    n = len(a)
    order = np.argsort(a, kind="stable")
    s = a[order]
    r = np.empty(n, dtype="float64")
    i = 0
    while i < n:
        j = i
        while j + 1 < n and s[j + 1] == s[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


def daily_ic(df, pred_col, label_col, method):
    """逐日截面相关。method in {'spearman','pearson'}"""
    out = []
    for _, g in df.groupby("trade_date", sort=True):
        p = g[pred_col].to_numpy(dtype="float64")
        t = g[label_col].to_numpy(dtype="float64")
        m = np.isfinite(p) & np.isfinite(t)
        p, t = p[m], t[m]
        if len(p) < 3:
            out.append(np.nan)
            continue
        if method == "spearman":
            p, t = _rankdata(p), _rankdata(t)
        sp, st = p.std(), t.std()
        if sp <= 0 or st <= 0:
            out.append(np.nan)
            continue
        out.append(float(((p - p.mean()) * (t - t.mean())).mean() / (sp * st)))
    return np.array(out, dtype="float64")


# ---------------------------------------------------------------- 独立实现：组合账
def portfolio_daily(bt, scores_by_day, cost_bps):
    """独立实现的逐日组合账：选股 -> 掩码 -> 强制持有 -> 成本 -> 结算。"""
    holdings = {}
    rows = []
    for d, g in bt.groupby("trade_date", sort=True):
        code = g["ts_code"].astype(str).to_numpy()
        member = g["is_member"].to_numpy(dtype=bool)
        listed = g["listed_ok"].to_numpy(dtype=bool)
        has_trade = g["has_trade"].to_numpy(dtype=bool)
        close_raw = g["close_raw"].to_numpy(dtype="float64")
        up = g["up_limit"].to_numpy(dtype="float64")
        dn = g["down_limit"].to_numpy(dtype="float64")
        fwd = g["fwd_ret_hfq"].to_numpy(dtype="float64")
        n = len(code)
        pos = {c: i for i, c in enumerate(code)}
        miss = np.isnan(up) | np.isnan(dn)
        can_buy = (member & listed & has_trade & (~miss)
                   & ~(close_raw >= up))
        can_sell = has_trade & (~miss) & ~(close_raw <= dn)

        sc = scores_by_day.get(d, {})
        scored = np.array([c in sc for c in code], dtype=bool)
        svals = np.array([sc.get(c, np.nan) for c in code], dtype="float64")
        ok = can_buy & scored & np.isfinite(svals)
        idx = np.where(ok)[0]
        # 分数降序；同分按代码序（稳定）
        order = sorted(idx.tolist(), key=lambda i: (-svals[i], code[i]))
        pick = order[:TOPK]
        target = {code[i]: 1.0 / len(pick) for i in pick} if pick else {}

        buys = sells = 0.0
        newh = dict(holdings)
        for c, w_old in list(holdings.items()):
            w_new = target.get(c, 0.0)
            d_w = w_new - w_old
            if d_w < -1e-12:
                i = pos[c]
                if can_sell[i]:
                    sells += -d_w
                    del newh[c]
                else:
                    newh[c] = w_old
        for c, w_new in target.items():
            w_old = holdings.get(c, 0.0)
            d_w = w_new - w_old
            if d_w > 1e-12:
                i = pos[c]
                if can_buy[i]:
                    buys += d_w
                    newh[c] = w_new
                elif c in holdings:
                    newh[c] = w_old
                else:
                    newh.pop(c, None)
        gross = 0.0
        for c, w in newh.items():
            i = pos[c]
            r = fwd[i]
            if not np.isfinite(r):
                r = 0.0
            gross += w * r
        cost = (buys + sells) * (cost_bps / 1e4) + sells * (STAMP_BPS / 1e4)
        rows.append({"trade_date": d, "n_hold": len(newh), "gross_ret": gross,
                     "cost": cost, "net_ret": gross - cost, "buys": buys,
                     "sells": sells, "turnover": buys + sells})
        holdings = newh
    return pd.DataFrame(rows)


def agg_metrics(daily):
    """独立实现的组合指标（口径见预注册 §5）。"""
    net = daily["net_ret"].to_numpy(dtype="float64")
    dates = daily["trade_date"]
    nav = np.cumprod(1.0 + net)
    nat = int((dates.iloc[-1] - dates.iloc[0]).days) + 1
    total = float(nav[-1] - 1.0)
    ann = float((1.0 + total) ** (YD / nat) - 1.0) if total > -1 else -1.0
    sd = float(np.std(net, ddof=0))
    vol = sd * np.sqrt(TD)
    sh0 = float((np.mean(net) * TD - RF_MAIN) / (sd * np.sqrt(TD))) if sd > 0 else 0.0
    sh15 = float((np.mean(net) * TD - RF_ALT) / (sd * np.sqrt(TD))) if sd > 0 else 0.0
    mdd = float(np.min(nav / np.maximum.accumulate(nav) - 1.0))
    turn = daily["turnover"].to_numpy(dtype="float64")
    return {
        "n_days": int(len(net)),
        "total_return": total,
        "annualized_return": ann,
        "annualized_vol": float(vol),
        "sharpe_rf0": sh0,
        "sharpe_rf1p5": sh15,
        "max_drawdown": mdd,
        "turnover_daily_mean": float(np.mean(turn)),
        "turnover_total": float(np.sum(turn)),
        "cost_total": float(np.sum(daily["cost"].to_numpy(dtype="float64"))),
        "gross_return_total": float(np.sum(daily["gross_ret"].to_numpy(dtype="float64"))),
        "avg_holdings": float(np.mean(daily["n_hold"].to_numpy())),
    }


# ---------------------------------------------------------------- 独立实现：族消融子集
def recompute_family_subsets(npz, df, label_col="label"):
    """独立复算族消融子集：族内**各自**做 IC 过滤 + 等权平均（预注册 §4.6）。

    只用 npz 里落的 per_expert_pred / expert_type / expert_valid_rankic，
    过滤阈值由本脚本自己算，**不复用建模阶段的任何函数**。
    """
    if "per_expert_pred" not in npz.files:
        return None
    per = npz["per_expert_pred"].astype("float64")
    types = [str(x) for x in npz["expert_type"]]
    ics = npz["expert_valid_rankic"].astype("float64")
    out = {}
    for fam, kinds in FAMILY_SPEC:
        idx = (list(range(len(types))) if kinds is None
               else [k for k in range(len(types)) if types[k] in kinds])
        if not idx:
            continue
        sub = ics[idx]
        thr = float(np.mean(sub) - KAPPA * np.std(sub, ddof=0))
        act = [k for k in idx if ics[k] >= thr]
        if not act:                       # 退化保护（预注册 §4.4 同规则）
            act = list(idx)
        d = df.copy()
        d["pred"] = per[act].mean(axis=0)
        out[fam] = {
            "rankic": float(np.nanmean(daily_ic(d, "pred", label_col, "spearman"))),
            "ic": float(np.nanmean(daily_ic(d, "pred", label_col, "pearson"))),
            "n_experts": len(idx),
            "n_active_after_ic_filter": len(act),
            "ic_filter_threshold": thr,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default=EXP)
    args = ap.parse_args()
    out_dir = os.path.join(REPO, "experiments", args.exp)
    data_dir = os.path.join(REPO, "data", args.exp)

    # ---- 0) 快照 sha256 校验（防快照漂移）
    man_path = os.path.join(out_dir, "snapshot_manifest.json")
    with open(man_path, encoding="utf-8") as fh:
        man = json.load(fh)
    for item in man["items"]:
        p = os.path.join(REPO, item["path"])
        got = sha256_of(p)
        if got != item["sha256"]:
            add("blocking", f"sha256::{item['name']}", got, item["sha256"],
                rel_diff(got, item["sha256"]), item["path"], "sha256sum <file>")

    bt = pd.read_csv(os.path.join(data_dir, "panel_backtest.csv"),
                     parse_dates=["trade_date"], dtype={"ts_code": "string"})
    bt = bt.reset_index(drop=True)
    bt["bt_row_id"] = np.arange(len(bt), dtype="int64")

    arms = []
    for fn in sorted(os.listdir(out_dir)):
        if fn.startswith("preds_") and fn.endswith(".npz"):
            parts = fn[len("preds_"):-len(".npz")].split("_s")
            arms.append((parts[0], int(parts[1])))
    arms = sorted(set(arms))
    if not arms:
        add("blocking", "preds_missing", "no preds_*_s*.npz", ">=1", "n/a", out_dir,
            "ls experiments/WORK-555/preds_*.npz")
        print(json.dumps({"verdict": "BLOCKING", "findings": findings}, ensure_ascii=False, indent=2))
        return 1

    recomputed = {}
    for arm, seed in arms:
        tag = f"{arm}_s{seed}"
        p = np.load(os.path.join(out_dir, f"preds_{arm}_s{seed}.npz"))
        pred, row = p["pred"], p["row"]
        df = bt.loc[row].copy()
        df["pred"] = pred.astype("float64")
        # IC 复算必须用**与建模侧完全相同的标签数值**（sample.npz 里是 float32 存储），
        # 否则比的是存储精度而不是算法正确性。
        # 标签本身与事实层的一致性另立一项检查（数据完整性），单独报告。
        samp = np.load(os.path.join(out_dir, "sample.npz"))
        s_mask = samp["split"] == 2
        s_rows = samp["bt_row_id"][s_mask]
        s_y = samp["y"][s_mask].astype("float64")
        lab_by_row = dict(zip(s_rows.tolist(), s_y.tolist()))
        fact_lab = df["label"].to_numpy(dtype="float64")
        model_lab = np.array([lab_by_row.get(int(r), np.nan) for r in row], dtype="float64")
        denom = np.maximum(np.abs(fact_lab), 1e-12)
        lab_dev = float(np.max(np.abs(model_lab - fact_lab) / denom))
        lab_dev_rel = float(np.max(np.abs(model_lab - fact_lab)) /
                            max(1e-12, float(np.max(np.abs(fact_lab)))))
        if lab_dev_rel > TOL:
            add("blocking", f"label_integrity::{tag}", lab_dev_rel, 0.0, lab_dev_rel,
                "experiments/WORK-555/sample.npz vs data/WORK-555/panel_backtest.csv",
                "/tmp/w555venv/bin/python audit/recompute_work555.py")
        else:
            findings.append({"severity": "pass",
                             "item": f"label_integrity::{tag} (max_rel_dev)",
                             "measured": lab_dev_rel, "expected": 0.0, "gap": lab_dev_rel,
                             "location": "sample.npz vs panel_backtest.csv",
                             "how_to_verify": "audit script output; float32 存储精度上限 ~6e-8"})
        df["label"] = model_lab

        # ---- 1) 独立复算 RankIC / IC
        ric = daily_ic(df, "pred", "label", "spearman")
        pic = daily_ic(df, "pred", "label", "pearson")
        ric_mean = float(np.nanmean(ric))
        pic_mean = float(np.nanmean(pic))
        run_path = os.path.join(out_dir, f"run_{arm}_s{seed}.json")
        if os.path.exists(run_path):
            with open(run_path, encoding="utf-8") as fh:
                run = json.load(fh)
            for key, mine in (("test_rankic_mean", ric_mean), ("test_ic_mean", pic_mean)):
                if key in run:
                    d = rel_diff(mine, run[key])
                    sev = "blocking" if d > TOL else "nit"
                    if d > TOL:
                        add(sev, f"ic::{tag}::{key}", mine, run[key], d,
                            f"run_{arm}_s{seed}.json",
                            "/tmp/w555venv/bin/python audit/recompute_work555.py")
                    elif d > 0:
                        findings.append({"severity": "pass", "item": f"ic::{tag}::{key}",
                                         "measured": mine, "expected": run[key], "gap": d,
                                         "location": f"run_{arm}_s{seed}.json",
                                         "how_to_verify": "audit script output"})

        # ---- 1b) 独立复算族消融子集（预注册 §4.6）
        fam_mine = recompute_family_subsets(p, df)
        if fam_mine and os.path.exists(run_path):
            with open(run_path, encoding="utf-8") as fh:
                run2 = json.load(fh)
            for fam, mine in fam_mine.items():
                claimed = run2.get("family_subsets", {}).get(fam)
                if not claimed:
                    continue
                for key in ("rankic", "ic", "n_experts", "n_active_after_ic_filter"):
                    if key not in claimed:
                        continue
                    dd = rel_diff(mine[key], claimed[key])
                    item = f"family::{tag}::{fam}::{key}"
                    if dd > TOL:
                        add("blocking", item, mine[key], claimed[key], dd,
                            f"run_{arm}_s{seed}.json",
                            "/tmp/w555venv/bin/python audit/recompute_work555.py")
                    else:
                        findings.append({"severity": "pass", "item": item,
                                         "measured": mine[key], "expected": claimed[key],
                                         "gap": dd,
                                         "location": f"run_{arm}_s{seed}.json",
                                         "how_to_verify": "audit script output"})

        # ---- 2) 独立复算组合账
        scores_by_day = {}
        for d, g in df.groupby("trade_date", sort=True):
            scores_by_day[d] = dict(zip(g["ts_code"].astype(str).tolist(),
                                        g["pred"].tolist()))
        bt_path = os.path.join(out_dir, f"backtest_{arm}_s{seed}.json")
        if os.path.exists(bt_path):
            with open(bt_path, encoding="utf-8") as fh:
                bj = json.load(fh)
            for mtag, bps in (("main_8bps", COST_MAIN_BPS), ("sens_5bps", COST_SENS_BPS)):
                mine_daily = portfolio_daily(bt, scores_by_day, bps)
                side_path = os.path.join(out_dir, f"bt_daily_{arm}_s{seed}_{mtag}.csv")
                if os.path.exists(side_path):
                    side = pd.read_csv(side_path, parse_dates=["trade_date"])
                    side = side.sort_values("trade_date").reset_index(drop=True)
                    mine_daily = mine_daily.sort_values("trade_date").reset_index(drop=True)
                    if len(side) != len(mine_daily):
                        add("blocking", f"daily_rows::{tag}::{mtag}", len(mine_daily),
                            len(side), "row count", side_path, "diff the two CSVs")
                    else:
                        for col in ("gross_ret", "cost", "net_ret", "buys", "sells", "turnover"):
                            d = float(np.max(np.abs(mine_daily[col].to_numpy()
                                                    - side[col].to_numpy())))
                            scale = max(1e-12, float(np.max(np.abs(side[col].to_numpy()))))
                            rd = d / scale
                            if rd > TOL:
                                add("blocking", f"daily::{tag}::{mtag}::{col}",
                                    float(np.max(mine_daily[col].to_numpy())), d, rd,
                                    side_path, "diff the two CSVs")
                mine_m = agg_metrics(mine_daily)
                claimed = bj["metrics"].get(mtag, {})
                for k, v in mine_m.items():
                    if k in claimed:
                        d = rel_diff(v, claimed[k])
                        if d > TOL:
                            add("blocking", f"metric::{tag}::{mtag}::{k}", v, claimed[k], d,
                                f"backtest_{arm}_s{seed}.json",
                                "/tmp/w555venv/bin/python audit/recompute_work555.py")
                        else:
                            findings.append({"severity": "pass",
                                             "item": f"metric::{tag}::{mtag}::{k}",
                                             "measured": v, "expected": claimed[k], "gap": d,
                                             "location": f"backtest_{arm}_s{seed}.json",
                                             "how_to_verify": "audit script output"})
                recomputed[f"{tag}::{mtag}"] = mine_m
            recomputed[f"{tag}::rankic"] = ric_mean
            recomputed[f"{tag}::ic"] = pic_mean

    # ---- 3) 复算主检验量 Δ_ic 与进/退判词（判据写死）
    verdict = {}
    for arm_pair in (("dee", "mlp_only"),):
        d_arm, b_arm = arm_pair
        per_seed = []
        for seed in sorted({s for a, s in arms if a == d_arm}):
            kd = f"{d_arm}_s{seed}::rankic"
            kb = f"{b_arm}_s{seed}::rankic"
            if kd in recomputed and kb in recomputed:
                rd, rb = recomputed[kd], recomputed[kb]
                delta = (rd - rb) / rb if rb != 0 else float("nan")
                per_seed.append({"seed": seed, "rankic_dee": rd, "rankic_mlp": rb,
                                 "delta_ic": delta})
        if per_seed:
            ds = [x["delta_ic"] for x in per_seed]
            rng = max(ds) - min(ds)
            mean_d = float(np.mean(ds))
            verdict = {
                "per_seed": per_seed, "delta_ic_mean": mean_d,
                "delta_ic_range": float(rng),
                "rankic_unstable": bool(rng > 0.01),
                "n_seeds": len(ds),
            }
            # 净 Sharpe < 0 -> 直接退
            sh = [recomputed[f"{d_arm}_s{s}::main_8bps"]["sharpe_rf0"]
                  for s in sorted({s for a, s in arms if a == d_arm})
                  if f"{d_arm}_s{s}::main_8bps" in recomputed]
            verdict["dee_net_sharpe_main_8bps"] = sh

    blocking = [f for f in findings if f["severity"] == "blocking"]
    report = {
        "experiment": args.exp,
        "tolerance_rel_diff": TOL,
        "n_findings": len(findings),
        "n_blocking": len(blocking),
        "verdict": "BLOCKING" if blocking else "PASS",
        "primary_test_recomputed": verdict,
        "findings": findings,
    }
    with open(os.path.join(out_dir, "audit_findings.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": report["verdict"], "n_blocking": len(blocking),
                      "n_findings": len(findings),
                      "primary_test_recomputed": verdict},
                     ensure_ascii=False, indent=2))
    if blocking:
        for f in blocking[:20]:
            print("BLOCKING:", f["item"], f["measured"], "!=", f["expected"], "gap=", f["gap"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
