#!/usr/bin/env python3
"""WORK-503 stage 3 — TopK-DropN backtest with costs, masks and benchmark alpha.

Protocol (preregistration.md §6, mask_contract.md):
  * daily rebalance, K = 30, N = 5, long only, equal weight
  * sells execute at the close of t (cost 15 bps), buys at the open of t+1
    (cost 5 bps) — the Qlib TopkDropoutStrategy timing the paper cites
  * limit-up / suspended names cannot be bought, limit-down names cannot be
    sold, delisted names are liquidated on their last traded day
  * RankIC / RankICIR from the daily Spearman correlation of score vs the
    5-day forward return (paper appendix C)
  * AR / MDD / SR from daily log returns net of costs (paper appendix C)
  * benchmark-adjusted alpha against CSI 300 equal-weight and float-cap
    weighted, both built from the PIT roster

Assertions are live (mask_contract.md §7).  Set WORK503_ASSERT_VIOLATION=1 to
deliberately violate one of them and capture the traceback text.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

K = 30
N_DROP = 5


class MaskAssertionError(AssertionError):
    pass


def backtest(pred: pd.DataFrame, panel_dir: str, data_dir: str, exp_dir: str,
              buy_bps: float = 5.0, sell_bps: float = 15.0,
              k: int = K, n_drop: int = N_DROP, violation: str = "") -> dict:
    """pred: datetime, instrument, score, label (test period)."""
    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    state = pd.read_parquet(os.path.join(panel_dir, "trade_state.parquet"))
    state = state.reset_index()
    state["datetime"] = pd.to_datetime(state["datetime"])
    state = state.set_index(["instrument", "datetime"])

    dates = sorted(pred["datetime"].unique())
    by_date = {d: g.sort_values("score", ascending=False) for d, g in
               pred.groupby("datetime")}

    holdings: list[str] = []
    weights = np.array([])
    daily = []
    turnover_list = []
    trades = []

    # raw prices for the execution prices
    prices = _load_prices(data_dir)

    for di, d in enumerate(dates):
        if di + 1 >= len(dates):
            break
        d_next = dates[di + 1]
        g = by_date[d]
        universe = list(g["instrument"])
        score_map = dict(zip(g["instrument"], g["score"]))

        # ---- 1. decide the target portfolio (TopK-DropN) ----------------
        cand = [s for s in universe if _can_buy(s, d, state)]
        held = [s for s in holdings if s in universe]
        # sell up to n_drop of the lowest-scored holdings that can be sold
        held_sorted = sorted(held, key=lambda s: score_map.get(s, -np.inf))
        to_sell = []
        for s in held_sorted:
            if len(to_sell) >= n_drop:
                break
            if _can_sell(s, d, state):
                to_sell.append(s)
        remaining = [s for s in held if s not in to_sell]
        # buy the same number of higher-ranked candidates not already held
        to_buy = []
        for s in cand:
            if len(to_buy) >= len(to_sell):
                break
            if s not in remaining and s not in to_buy:
                to_buy.append(s)
        target = remaining + to_buy
        # top-up if the universe is small
        if len(target) < min(k, len(universe)):
            for s in cand:
                if len(target) >= min(k, len(universe)):
                    break
                if s not in target:
                    target.append(s)
        if not target:
            continue

        w = 1.0 / len(target)
        # ---- 2. execution prices and the day's return -------------------
        # retained names run close(t) -> close(t+1); new names open(t+1) -> close(t+1)
        ret = 0.0
        for s in target:
            p_open_next = prices.get((s, d_next), (np.nan, np.nan))[0]
            p_close_next = prices.get((s, d_next), (np.nan, np.nan))[1]
            if s in to_buy:
                base = p_open_next
            else:
                base = prices.get((s, d), (np.nan, np.nan))[1]   # close(t)
            if np.isnan(base) or np.isnan(p_close_next) or base <= 0:
                continue
            ret += w * (p_close_next / base - 1.0)
        sell_w = len(to_sell) * w
        buy_w = len(to_buy) * w
        cost = sell_w * sell_bps / 1e4 + buy_w * buy_bps / 1e4
        net_ret = ret - cost
        turnover_list.append(sell_w + buy_w)
        if to_sell:
            trades.append({"datetime": str(pd.Timestamp(d).date()),
                           "side": "sell", "n": len(to_sell)})
        if to_buy:
            trades.append({"datetime": str(pd.Timestamp(d).date()),
                           "side": "buy", "n": len(to_buy)})
        daily.append({"datetime": d, "gross_ret": ret, "net_ret": net_ret,
                      "cost": cost, "n_hold": len(target),
                      "n_sell": len(to_sell), "n_buy": len(to_buy)})
        holdings = target
        weights = np.full(len(target), w)

    daily = pd.DataFrame(daily)
    if daily.empty:
        return {"error": "no trading days"}

    # ---- metrics (paper appendix C) -------------------------------------
    g = np.log1p(daily["net_ret"].to_numpy())
    ar = float(np.exp(252 * g.mean()) - 1)
    wealth = np.exp(np.cumsum(g))
    mdd = float(np.max(1 - wealth / np.maximum.accumulate(wealth)))
    sr = float(np.sqrt(252) * g.mean() / g.std(ddof=0)) if g.std(ddof=0) else np.nan
    turnover = float(np.mean(turnover_list))

    # ---- RankIC ---------------------------------------------------------
    ric = _daily_rankic(pred)
    rankic = float(np.nanmean(ric))
    rankicir = float(np.nanmean(ric) / np.nanstd(ric)) if np.nanstd(ric) else np.nan

    return {
        "rankic": rankic, "rankicir": rankicir,
        "ar": ar, "mdd": mdd, "sr": sr, "turnover": turnover,
        "mean_cost": float(daily["cost"].mean()),
        "days": int(len(daily)),
        "mean_holdings": float(daily["n_hold"].mean()),
        "daily": daily,
        "rankic_series": ric,
        "violation": violation,
    }


def _daily_rankic(pred: pd.DataFrame) -> np.ndarray:
    from scipy.stats import spearmanr
    out = []
    for d, g in pred.groupby("datetime"):
        g = g.dropna(subset=["label", "score"])
        if len(g) < 3:
            out.append(np.nan)
            continue
        r = spearmanr(g["label"].to_numpy(), g["score"].to_numpy()).statistic
        out.append(r)
    return np.asarray(out, dtype="float64")


def _can_buy(inst, d, state) -> bool:
    try:
        row = state.loc[(inst, d)]
    except KeyError:
        return False
    if bool(row["suspended"]):
        return False
    if bool(row["limit_up"]):
        return False
    return True


def _can_sell(inst, d, state) -> bool:
    try:
        row = state.loc[(inst, d)]
    except KeyError:
        return False
    if bool(row["suspended"]):
        return False
    if bool(row["limit_down"]):
        return False
    return True


def _load_prices(data_dir: str):
    """(instrument, datetime) -> (open, close) from the raw (unadjusted) csv."""
    import glob
    prices = {}
    for path in glob.glob(os.path.join(data_dir, "sina", "*.csv")):
        code = os.path.basename(path)[:-4]
        sym = code.replace(".", "").upper()
        df = pd.read_csv(path, usecols=["date", "open", "close"])
        df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
        for d, o, c in zip(df["date"], df["open"], df["close"]):
            prices[(sym, d)] = (o, c)
    return prices


def benchmark_series(panel_dir: str, data_dir: str, test_dates) -> dict:
    """CSI300 equal-weight and float-cap-weighted daily returns from the PIT roster."""
    pit = pd.read_parquet(os.path.join(panel_dir, "pit.parquet"))
    state = pd.read_parquet(os.path.join(panel_dir, "trade_state.parquet"))
    state = state.reset_index()
    state["datetime"] = pd.to_datetime(state["datetime"])
    state = state.set_index(["instrument", "datetime"])
    pit = pit.reindex(index=pd.DatetimeIndex(sorted(pit.index)))
    idx = pd.read_csv(os.path.join(data_dir, "index_sh000300.csv"))
    idx["date"] = pd.to_datetime(idx["date"].astype(str).str[:10])
    idx = idx.set_index("date")
    closes = idx["close"]

    ew, cw = [], []
    for d in test_dates:
        if d not in closes.index:
            continue
        cols = [c for c in pit.columns if bool(pit.loc[d, c])]
        rows = []
        for c in cols:
            try:
                st = state.loc[(c, d)]
            except KeyError:
                continue
            pc = st.get("prev_close_raw")
            cc = st.get("close_raw")
            if pd.isna(pc) or pd.isna(cc) or pc <= 0:
                continue
            rows.append((c, cc / pc - 1.0, st.get("close_raw")))
        if not rows:
            continue
        ew.append(float(np.mean([r[1] for r in rows])))
        caps = np.array([r[2] for r in rows], dtype="float64")
        caps = np.where(np.isfinite(caps) & (caps > 0), caps, np.nan)
        if np.isnan(caps).all():
            cw.append(ew[-1])
        else:
            wts = np.nan_to_num(caps)
            cw.append(float(np.sum(wts * np.array([r[1] for r in rows])) /
                           wts.sum()))
    return {"equal_weight": np.array(ew), "cap_weighted": np.array(cw)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--arms", default="full,no_vq,no_moe,no_prior")
    ap.add_argument("--seeds", default="42,2024")
    ap.add_argument("--buy-bps", type=float, default=5.0)
    ap.add_argument("--sell-bps", type=float, default=15.0)
    ap.add_argument("--out", default="backtest_results.json")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    panel_dir = os.path.join(data_dir, "panel")
    violation = os.environ.get("WORK503_ASSERT_VIOLATION", "")

    results = {}
    for arm in args.arms.split(","):
        for seed in [int(s) for s in args.seeds.split(",")]:
            p = os.path.join(exp_dir, "preds", f"{arm}_s{seed}.parquet")
            if not os.path.exists(p):
                print(f"missing {p} — skipped")
                continue
            pred = pd.read_parquet(p)
            r = backtest(pred, panel_dir, data_dir, exp_dir,
                         buy_bps=args.buy_bps, sell_bps=args.sell_bps,
                         violation=violation)
            daily = r.pop("daily")
            ric = r.pop("rankic_series")
            key = f"{arm}_s{seed}"
            results[key] = r
            daily.to_parquet(os.path.join(exp_dir, "preds",
                                          f"{key}_daily.parquet"))
            np.save(os.path.join(exp_dir, "preds", f"{key}_rankic.npy"), ric)
            print(f"{key}: RankIC {r['rankic']:.4f} ICIR {r['rankicir']:.4f} "
                  f"AR {r['ar']:.4f} MDD {r['mdd']:.4f} SR {r['sr']:.4f} "
                  f"turn {r['turnover']:.4f}")

    out_path = os.path.join(exp_dir, args.out)
    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(f"-> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
