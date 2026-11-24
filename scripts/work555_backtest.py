#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 组合回测引擎（量化研究员阶段）

严格照抄 experiments/WORK-555/preregistration.md v1.0 的 §5：
  - top-50 等权，日频调仓；t 日收盘打分 -> 持有 t -> t+1
  - 买入受限：t 日停牌 或 close_raw(t) >= up_limit -> 当日不可买入
  - 卖出受限：t 日停牌 或 close_raw(t) <= down_limit -> 当日不可卖出（强制持有）
  - stk_limit 缺失当日按不可买入、不可卖出处理（保守，显式豁免，不触发断言）
  - 成本：cost_t = buys*c + sells*c + sells*stamp；主口径 c=8bps、stamp=10bps；敏感性 5bps
  - 年化用自然日折算 (1+R)^(365.25/自然日数)-1；波动用交易日折算 std*sqrt(252)
  - Sharpe 主口径 rf=0（另给 rf=1.5% 对照列）；MDD 按日频净净值曲线

**可成交断言**：任何用「不可成交价」做买卖决策或结算的路径都抛 AssertionError，
错误信息含 ts_code / trade_date / 违规类型。`--no-mask` 刻意关闭掩码时断言必须触发。

输出：
  experiments/WORK-555/bt_daily_<arm>_s<seed>.csv   逐日组合账（审计复算的事实层）
  experiments/WORK-555/backtest_<arm>_s<seed>.json  汇总指标

运行：
  /tmp/w555venv/bin/python scripts/work555_backtest.py --arm dee --seed 42
  /tmp/w555venv/bin/python scripts/work555_backtest.py --arm dee --seed 42 --no-mask   # 断言演示
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

TOPK = 50
COST_MAIN_BPS = 8.0          # §5 主口径单边佣金+冲击
COST_SENS_BPS = 5.0          # §5 敏感性档
STAMP_BPS = 10.0             # §5 卖出印花税（2017-2020 实际税率）
RF_MAIN = 0.0                # §5 主口径 rf=0
RF_ALT = 0.015               # §5 对照列 rf=1.5%
TRADING_DAYS = 252           # §5 波动折算
YEAR_DAYS = 365.25           # §5 年化自然日折算


class UntradablePriceRead(AssertionError):
    """读取了不可成交价 —— 掩码/断言被绕过时抛此错。"""


class TradabilityGuard:
    """可成交判定与断言（§5.5）。

    enforce=False 时只记录不抛错 —— 仅用于「刻意关闭掩码」的断言演示。
    """

    def __init__(self, enforce=True, mask_enabled=True):
        self.enforce = enforce          # 断言是否抛错（始终 True）
        self.mask_enabled = mask_enabled  # 可成交掩码是否生效（--no-mask 关掉）
        self.violations = []

    def check(self, ts_code, trade_date, reason, purpose):
        msg = (f"UNTRADABLE_PRICE_READ ts_code={ts_code} trade_date={trade_date} "
               f"reason={reason} purpose={purpose}")
        self.violations.append({"ts_code": str(ts_code), "trade_date": str(trade_date),
                                 "reason": reason, "purpose": purpose})
        if self.enforce:
            raise UntradablePriceRead(msg)
        return msg


def load_facts():
    bt = pd.read_csv(
        os.path.join(DATA, "panel_backtest.csv"),
        parse_dates=["trade_date"], dtype={"ts_code": "string"},
    )
    # **不要重排序**：CSV 的行序就是 features 阶段分配的 bt_row_id 序，重排会让映射错位
    return bt.reset_index(drop=True)


def build_day_tables(bt):
    """把事实层转成逐日的 numpy 字典（回测主循环用）。"""
    days = []
    for d, g in bt.groupby("trade_date", sort=True):
        g = g.reset_index(drop=True)
        days.append({
            "date": d,
            "code": g["ts_code"].astype(str).to_numpy(),
            "member": g["is_member"].to_numpy(dtype=bool),
            "listed": g["listed_ok"].to_numpy(dtype=bool),
            "has_trade": g["has_trade"].to_numpy(dtype=bool),
            "suspended": g["is_suspended"].to_numpy(dtype=bool),
            "close_raw": g["close_raw"].to_numpy(dtype="float64"),
            "close_hfq": g["close_hfq"].to_numpy(dtype="float64"),
            "up_limit": g["up_limit"].to_numpy(dtype="float64"),
            "down_limit": g["down_limit"].to_numpy(dtype="float64"),
            "fwd_ret": g["fwd_ret_hfq"].to_numpy(dtype="float64"),
        })
    return days


def _tradable(i, day, side):
    """**独立于掩码表达式**的可成交复核（断言用）。

    掩码 buy_ok/sell_ok 决定「能不能交易」；本函数只回答「这笔交易到底该不该发生」，
    两者分开写，掩码写错时本函数能独立抓出来。
    返回 (是否可成交, 违规类型)。
    """
    if not day["has_trade"][i]:
        return False, "suspended"
    up = day["up_limit"][i]
    dn = day["down_limit"][i]
    if np.isnan(up) or np.isnan(dn):
        return False, "limit_missing"       # 预注册 §5：缺 limit 保守处理，显式豁免
    cr = day["close_raw"][i]
    if side == "buy" and cr >= up:
        return False, "limit_up"            # 涨停买不到
    if side == "sell" and cr <= dn:
        return False, "limit_down"          # 跌停卖不掉
    return True, ""


def run_backtest(days, scores_by_day, cost_bps, guard, topk=TOPK):
    """逐日：选股 -> 目标权重 -> 与上日实际权重之差 -> 成交可行性 -> 收益结算。

    scores_by_day: {date: {code: score}}（缺失/NaN 的不入选）
    """
    holdings = {}          # code -> weight（实际持仓）
    rows = []
    for day in days:
        d = day["date"]
        codes = day["code"]
        sc = scores_by_day.get(d, {})
        idx_of = {c: i for i, c in enumerate(codes)}

        up = day["up_limit"]
        dn = day["down_limit"]
        cr = day["close_raw"]
        limit_missing = np.isnan(up) | np.isnan(dn)
        limit_up = (~limit_missing) & (cr >= up)
        limit_down = (~limit_missing) & (cr <= dn)

        # ---- 掩码（§5）：买入可行 / 卖出可行
        buy_ok = (day["has_trade"] & day["listed"] & day["member"]
                  & (~limit_missing) & (~limit_up))
        sell_ok = day["has_trade"] & (~limit_missing) & (~limit_down)
        if not guard.mask_enabled:
            # --no-mask：刻意关掉掩码，用于证明断言真的在跑
            buy_ok = day["listed"] & day["member"]
            sell_ok = np.ones(len(codes), dtype=bool)

        # ---- 1) 目标组合：在**可买入**且有分数的股票里取分数最高 top-K
        cand = [(sc[c], i) for i, c in enumerate(codes)
                if buy_ok[i] and c in sc and np.isfinite(sc[c])]
        cand.sort(key=lambda t: (-t[0], t[1]))
        sel = [i for _, i in cand[:topk]]
        target = {codes[i]: 1.0 / len(sel) for i in sel} if sel else {}

        # ---- 2) 目标权重 vs 实际持仓 -> 买卖
        buys = 0.0
        sells = 0.0
        new_holdings = dict(holdings)
        for c, w_old in holdings.items():
            i = idx_of.get(c)
            w_new = target.get(c, 0.0)
            d_w = w_new - w_old
            if d_w < -1e-12:
                if i is not None and sell_ok[i]:
                    ok, reason = _tradable(i, day, "sell")
                    if not ok and reason != "limit_missing":
                        guard.check(c, d, reason, "sell_fill")
                    sells += -d_w
                    new_holdings.pop(c, None)
                else:
                    # 卖不掉（停牌/跌停/缺 limit）-> 强制持有，这是合法结果，不是违规
                    new_holdings[c] = w_old
        for c, w_new in target.items():
            w_old = holdings.get(c, 0.0)
            d_w = w_new - w_old
            if d_w > 1e-12:
                i = idx_of.get(c)
                if i is not None and buy_ok[i]:
                    ok, reason = _tradable(i, day, "buy")
                    if not ok and reason != "limit_missing":
                        guard.check(c, d, reason, "buy_fill")
                    buys += d_w
                    new_holdings[c] = w_new
                else:
                    # 买不进 -> 维持原权重（原本没有则不进组合），合法结果
                    if c in holdings:
                        new_holdings[c] = w_old
                    else:
                        new_holdings.pop(c, None)

        # ---- 3) 结算收益（按实际成交后的持仓）
        gross = 0.0
        for c, w in new_holdings.items():
            i = idx_of.get(c)
            if i is None:
                continue
            if day["suspended"][i] and c in holdings and abs(
                    new_holdings.get(c, 0.0) - holdings.get(c, 0.0)) > 1e-12:
                # 停牌股权重发生变化 = 引擎在不可成交价上做了交易 -> 断言
                guard.check(c, d, "suspended", "settle")
            r = day["fwd_ret"][i]
            if not np.isfinite(r):
                r = 0.0
            gross += w * r
        cost = (buys + sells) * (cost_bps / 1e4) + sells * (STAMP_BPS / 1e4)
        net = gross - cost

        rows.append({
            "trade_date": d, "n_hold": len(new_holdings), "n_target": len(target),
            "gross_ret": gross, "cost": cost, "net_ret": net,
            "buys": buys, "sells": sells, "turnover": buys + sells,
        })
        holdings = new_holdings
    return pd.DataFrame(rows)


def metrics(daily, rf=RF_MAIN):
    """§5 口径的组合指标。"""
    net = daily["net_ret"].to_numpy(dtype="float64")
    dates = daily["trade_date"]
    nav = np.cumprod(1.0 + net)
    n = len(net)
    total = nav[-1] - 1.0 if n else 0.0
    # 年化：自然日折算
    nat_days = int((dates.iloc[-1] - dates.iloc[0]).days) + 1 if n else 0
    ann = (1.0 + total) ** (YEAR_DAYS / nat_days) - 1.0 if nat_days > 0 and total > -1 else -1.0
    vol = float(np.std(net, ddof=0) * np.sqrt(TRADING_DAYS))
    # Sharpe = (年化日均收益 - rf) / 年化波动；rf=0 为主口径（§5）
    sharpe = float((np.mean(net) * TRADING_DAYS - rf) / (np.std(net, ddof=0) * np.sqrt(TRADING_DAYS))) \
        if np.std(net, ddof=0) > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    mdd = float(np.min(nav / peak - 1.0)) if n else 0.0
    turn = daily["turnover"].to_numpy(dtype="float64")
    return {
        "n_days": int(n),
        "total_return": float(total),
        "annualized_return": float(ann),
        "annualized_vol": vol,
        "sharpe_rf0": sharpe,
        "sharpe_rf1p5": float((np.mean(net) * TRADING_DAYS - RF_ALT) / (np.std(net, ddof=0) * np.sqrt(TRADING_DAYS)))
        if np.std(net, ddof=0) > 0 else 0.0,
        "max_drawdown": mdd,
        "turnover_daily_mean": float(np.mean(turn)) if n else 0.0,
        "turnover_total": float(np.sum(turn)) if n else 0.0,
        "cost_total": float(np.sum(daily["cost"].to_numpy(dtype="float64"))) if n else 0.0,
        "gross_return_total": float(np.sum(daily["gross_ret"].to_numpy(dtype="float64"))) if n else 0.0,
        "avg_holdings": float(np.mean(daily["n_hold"].to_numpy())) if n else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--no-mask", action="store_true",
                    help="刻意关闭可成交掩码（用于演示断言真的在跑）")
    args = ap.parse_args()

    t0 = time.time()
    bt = load_facts()
    days = build_day_tables(bt)

    preds = np.load(os.path.join(OUT, f"preds_{args.arm}_s{args.seed}.npz"))
    pred, row, date_idx = preds["pred"], preds["row"], preds["date_idx"]
    # row 是 panel_backtest.csv 的行号；preds 按 sample 行序给出 -> 需回连到 bt 行
    bt = bt.reset_index(drop=True)
    bt["bt_row_id"] = np.arange(len(bt), dtype="int64")
    scores = pd.DataFrame({"bt_row_id": row, "pred": pred})
    merged = bt.merge(scores, on="bt_row_id", how="inner")
    scores_by_day = {}
    for d, g in merged.groupby("trade_date", sort=True):
        scores_by_day[d] = dict(zip(g["ts_code"].astype(str).tolist(),
                                    g["pred"].astype("float64").tolist()))

    # 断言**始终开启**：--no-mask 只关掩码，关掉掩码后断言必须立刻报警
    guard = TradabilityGuard(enforce=True, mask_enabled=not args.no_mask)
    results = {}
    daily_frames = {}
    for tag, bps in (("main_8bps", COST_MAIN_BPS), ("sens_5bps", COST_SENS_BPS)):
        daily = run_backtest(days, scores_by_day, bps, guard)
        results[tag] = metrics(daily)
        daily_frames[tag] = daily
        if guard.violations:
            break

    out = {
        "arm": args.arm, "seed": args.seed,
        "protocol": {
            "topk": TOPK, "cost_main_bps": COST_MAIN_BPS, "cost_sens_bps": COST_SENS_BPS,
            "stamp_bps": STAMP_BPS, "rf_main": RF_MAIN, "rf_alt": RF_ALT,
            "annualization_days": YEAR_DAYS, "vol_trading_days": TRADING_DAYS,
            "mask_enabled": not args.no_mask,
            "prereg": "experiments/WORK-555/preregistration.md v1.0 (commit 9a85409)",
        },
        "metrics": results,
        "guard_violations": guard.violations[:50],
        "n_guard_violations": len(guard.violations),
    }
    if not args.no_mask:
        for tag, daily in daily_frames.items():
            daily.to_csv(os.path.join(OUT, f"bt_daily_{args.arm}_s{args.seed}_{tag}.csv"),
                         index=False)
        with open(os.path.join(OUT, f"backtest_{args.arm}_s{args.seed}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"arm": args.arm, "seed": args.seed,
                      "no_mask": args.no_mask,
                      "metrics": results,
                      "n_guard_violations": len(guard.violations),
                      "elapsed_sec": round(time.time() - t0, 1)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
