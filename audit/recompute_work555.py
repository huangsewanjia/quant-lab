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
from datetime import datetime

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
    """独立实现的逐日组合账：选股 -> 掩码 -> **现金账本调仓** -> 成本 -> 结算。

    口径与建模脚本 scripts/work555_backtest.py 一致，但**代码路径独立**（本脚本零 import 建模函数）：
      1) 目标组合：在「可买入」且有有效分数的股票里取分数最高 TOPK，等权；
      2) 卖出：能卖则卖到目标权重；卖不掉（停牌 / 跌停 / 缺 limit）则**强制保留原权重**；
      3) 买入：买入额受**可用现金**约束；cash = 1 - Σw（上日未投出部分），
         卖不掉的部分不能凭空变成买入额度；
      4) 结算：按实际成交后的持仓加权 fwd_ret；成本 = (buys+sells)*c + sells*stamp。

    不变量：任意一天 Σw ≤ 1 且 Σw + cash ≡ 1（无隐含杠杆）。
    （早期实现把「强制持有的旧权重」与「新买入的 1/K」直接叠加，Σw 最高到 1.44，
      隐含杠杆 44% —— 本函数记录 sum_w / cash 供主流程做闸门检查。）
    """
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
        pos = {c: i for i, c in enumerate(code)}
        miss = np.isnan(up) | np.isnan(dn)
        can_buy = (member & listed & has_trade & (~miss)
                   & ~(close_raw >= up))
        can_sell = has_trade & (~miss) & ~(close_raw <= dn)

        sc = scores_by_day.get(d, {})
        svals = np.array([sc.get(c, np.nan) for c in code], dtype="float64")
        ok = can_buy & np.array([c in sc for c in code], dtype=bool) & np.isfinite(svals)
        idx = np.where(ok)[0]
        # 分数降序；同分按代码序（事实层日内 ts_code 已是字典序，等价于建模侧的索引序）
        order = sorted(idx.tolist(), key=lambda i: (-svals[i], code[i]))
        pick = order[:TOPK]
        target = {code[i]: 1.0 / len(pick) for i in pick} if pick else {}

        buys = sells = 0.0
        cash = 1.0 - float(sum(holdings.values()))      # 期初现金（上日未投出部分）
        newh = {}
        # ---- 卖出阶段：能卖则卖到目标权重；卖不掉则强制持有原权重
        for c, w_old in holdings.items():
            w_new = target.get(c, 0.0)
            i = pos.get(c)
            # i 为 None = 该股当日已无面板行（事实层非稠密，最早只出现 25/974 天）
            #   -> 视为不可成交 -> 强制保留原权重（与建模侧 idx_of.get(c) 语义一致）
            if w_new < w_old - 1e-12 and i is not None and can_sell[i]:
                sold = w_old - w_new
                sells += sold
                cash += sold
                if w_new > 1e-15:
                    newh[c] = w_new
            else:
                newh[c] = w_old
        # ---- 买入阶段：按分数序买入，受可用现金约束
        for c, w_new in target.items():
            w_cur = newh.get(c, 0.0)
            need = w_new - w_cur
            if need <= 1e-12:
                continue
            i = pos.get(c)
            if i is None or not can_buy[i]:
                continue                       # 买不进 -> 不买（合法结果）
            amt = need if need <= cash else cash
            if amt <= 1e-15:
                break                          # 现金耗尽，后续买不进
            buys += amt
            cash -= amt
            newh[c] = w_cur + amt

        gross = 0.0
        for c, w in newh.items():
            i = pos.get(c)
            if i is None:
                continue                       # 当日无面板行 -> 无收益可结算
            r = fwd[i]
            if not np.isfinite(r):
                r = 0.0
            gross += w * r
        cost = (buys + sells) * (cost_bps / 1e4) + sells * (STAMP_BPS / 1e4)
        sum_w = float(sum(newh.values()))
        rows.append({"trade_date": d, "n_hold": len(newh), "gross_ret": gross,
                     "cost": cost, "net_ret": gross - cost, "buys": buys,
                     "sells": sells, "turnover": buys + sells,
                     "sum_w": sum_w, "cash": cash})
        holdings = newh
    return pd.DataFrame(rows)


def weight_invariants(daily):
    """组合权重不变量检查（现金账本口径的闸门）。

    返回 dict：
      max_sum_w        —— 每日 Σw 的最大值（必须 ≤ 1 + 1e-9，否则存在隐含杠杆）
      n_days_over_1   —— Σw > 1 + 1e-9 的天数（必须为 0）
      max_ledger_dev   —— max |Σw + cash - 1|（账本恒等式，必须 ≈ 0）
    """
    sw = daily["sum_w"].to_numpy(dtype="float64")
    cash = daily["cash"].to_numpy(dtype="float64")
    return {
        "max_sum_w": float(np.max(sw)) if len(sw) else 0.0,
        "n_days_over_1": int(np.sum(sw > 1.0 + 1e-9)),
        "max_ledger_dev": float(np.max(np.abs(sw + cash - 1.0))) if len(sw) else 0.0,
    }


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
            # 注意：sha256 是十六进制串，**不能**送进 rel_diff（那里做 float()），
            # 否则一旦真的漂移，这里会抛 ValueError 而不是报出 blocking。
            add("blocking", f"sha256::{item['name']}", got, item["sha256"],
                "sha256 不一致", item["path"], "sha256sum <file>")
        else:
            findings.append({"severity": "pass", "item": f"sha256::{item['name']}",
                             "measured": got, "expected": item["sha256"], "gap": 0.0,
                             "location": item["path"],
                             "how_to_verify": "sha256sum <file>"})

    # ---- 0b) 产物清单 sha256 校验（把**审计的直接输入**也纳入 sha256 覆盖）
    # 第二轮审查指出：preds_*.npz / bt_daily_*.csv / sample.npz 这些审计输入
    # 既不入库、也不在任何 sha256 覆盖内 -> 第三方无法确认「复算用的是同一份输入」。
    am_path = os.path.join(out_dir, "artifact_manifest.json")
    if os.path.exists(am_path):
        with open(am_path, encoding="utf-8") as fh:
            aman = json.load(fh)
        for item in aman.get("items", []):
            p = os.path.join(REPO, item["path"])
            if not os.path.exists(p):
                add("blocking", f"artifact_sha256::{item['name']}", "缺失",
                    item["sha256"], "文件不存在", item["path"], "sha256sum <file>")
                continue
            got = sha256_of(p)
            if got != item["sha256"]:
                add("blocking", f"artifact_sha256::{item['name']}", got, item["sha256"],
                    "sha256 不一致", item["path"], "sha256sum <file>")
            else:
                findings.append({"severity": "pass",
                                 "item": f"artifact_sha256::{item['name']}",
                                 "measured": got, "expected": item["sha256"], "gap": 0.0,
                                 "location": item["path"],
                                 "how_to_verify": "sha256sum <file>"})
    else:
        add("suggestion", "artifact_manifest_missing", "缺 artifact_manifest.json",
            "存在", "审计的直接输入未被 sha256 覆盖",
            "experiments/WORK-555/artifact_manifest.json",
            "/tmp/w555venv/bin/python scripts/work555_artifact_manifest.py --build")

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
            # 键名按臂取：xgbfull（诊断臂）的报告字段是 test_rankic_mean_best_iter /
            # test_ic_mean_best_iter；其余臂是 test_rankic_mean / test_ic_mean。
            # 第四轮审查 B4-1 实测发现：原先只查 test_rankic_mean，xgbfull 的 IC
            # 被「if key in run」静默跳过 —— 该臂在审计里零覆盖，秩口径分歧也没人挡。
            for cands, mine in ((("test_rankic_mean", "test_rankic_mean_best_iter"), ric_mean),
                                (("test_ic_mean", "test_ic_mean_best_iter"), pic_mean)):
                key = next((k for k in cands if k in run), None)
                if key is None:
                    continue
                d = rel_diff(mine, run[key])
                sev = "blocking" if d > TOL else "nit"
                if d > TOL:
                    add(sev, f"ic::{tag}::{key}", mine, run[key], d,
                        f"run_{arm}_s{seed}.json",
                        "/tmp/w555venv/bin/python audit/recompute_work555.py")
                else:
                    # 第五轮处置：原先只在 gap > 0 时记 pass，导致「检查项数」随数值
                    # 恰好相等而漂移（xgbfull 口径修正后由 320 回到 318），
                    # 覆盖度不可信。这里对**每一个被检查的量**都留一条记录，
                    # gap 恰好为 0 时也记（gap=0.0），项数于是只取决于覆盖、不取决于数值运气。
                    # 比较口径与阈值（TOL）未动。
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
                        for col in ("gross_ret", "cost", "net_ret", "buys", "sells",
                                   "turnover", "sum_w", "cash"):
                            if col not in side.columns:
                                continue
                            a_mine = mine_daily[col].to_numpy(dtype="float64")
                            a_side = side[col].to_numpy(dtype="float64")
                            d = float(np.max(np.abs(a_mine - a_side)))
                            scale = max(1e-12, float(np.max(np.abs(a_side))))
                            if scale <= 1e-9:
                                # **近零列**（如 cash 多数交易日为 0）：相对差判据会把
                                # 1e-16 的浮点尾差放大成假 blocking，改用绝对差判据。
                                rd, ok = d, d <= 1e-12
                            else:
                                rd = d / scale
                                ok = rd <= TOL
                            if not ok:
                                add("blocking", f"daily::{tag}::{mtag}::{col}",
                                    float(np.max(a_mine)), d, rd,
                                    side_path, "diff the two CSVs")
                # ---- 2b) 组合权重不变量（现金账本闸门：Σw ≤ 1，Σw + cash ≡ 1）
                inv = weight_invariants(mine_daily)
                inv["item"] = f"weight_invariant::{tag}::{mtag}"
                if inv["n_days_over_1"] > 0 or inv["max_sum_w"] > 1.0 + 1e-9:
                    add("blocking", inv["item"], inv["max_sum_w"], 1.0,
                        inv["max_sum_w"] - 1.0, side_path,
                        "/tmp/w555venv/bin/python audit/recompute_work555.py")
                elif inv["max_ledger_dev"] > 1e-9:
                    add("blocking", inv["item"] + "::ledger", inv["max_ledger_dev"], 0.0,
                        inv["max_ledger_dev"], side_path,
                        "/tmp/w555venv/bin/python audit/recompute_work555.py")
                else:
                    findings.append({"severity": "pass", "item": inv["item"],
                                     "measured": inv["max_sum_w"], "expected": 1.0,
                                     "gap": abs(inv["max_sum_w"] - 1.0),
                                     "ledger_max_dev": inv["max_ledger_dev"],
                                     "n_days_over_1": inv["n_days_over_1"],
                                     "location": side_path,
                                     "how_to_verify": "audit script output; sum_w+cash==1"})
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
            # 预注册 §6.2：「两种子 max − min > 0.01（**RankIC 绝对量**）」判不稳定。
            # 早期实现误把 **Δ_ic** 的极差填进 rankic_unstable（字段语义错，nit 项），
            # 现在拆成两组量：Δ_ic 极差 与 各臂 RankIC 绝对量极差。
            ric_d = [x["rankic_dee"] for x in per_seed]
            ric_b = [x["rankic_mlp"] for x in per_seed]
            rng_d = float(max(ric_d) - min(ric_d))
            rng_b = float(max(ric_b) - min(ric_b))
            verdict = {
                "per_seed": per_seed, "delta_ic_mean": mean_d,
                "delta_ic_range": float(rng),
                "rankic_range_dee": rng_d,
                "rankic_range_mlp_baseline": rng_b,
                "rankic_unstable": bool(rng_d > 0.01 or rng_b > 0.01),
                "rankic_unstable_detail": {
                    "dee": bool(rng_d > 0.01), "mlp_baseline": bool(rng_b > 0.01),
                    "threshold": 0.01, "metric": "RankIC 绝对量 max-min（预注册 §6.2）",
                },
                "n_seeds": len(ds),
            }
            # 净 Sharpe < 0 -> 直接退
            sh = [recomputed[f"{d_arm}_s{s}::main_8bps"]["sharpe_rf0"]
                  for s in sorted({s for a, s in arms if a == d_arm})
                  if f"{d_arm}_s{s}::main_8bps" in recomputed]
            verdict["dee_net_sharpe_main_8bps"] = sh

    blocking = [f for f in findings if f["severity"] == "blocking"]
    # **审计新鲜度**：把本次审计所依据的产物清单 sha256 与生成时间写进输出。
    # 产物（清单）之后又变过而审计没重跑 -> 消费方（final_numbers --check / 报告）可据此判定审计陈旧。
    am_path = os.path.join(out_dir, "artifact_manifest.json")
    am_sha = sha256_of(am_path) if os.path.exists(am_path) else None
    report = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "artifact_manifest_sha256": am_sha,
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
