#!/usr/bin/env python3
"""WORK-503 stage 3 — INDEPENDENT recompute of the headline metrics.

Contract (audit/README.md): this script must NOT import any function from the
modelling stage.  It re-derives, from the snapshot CSVs and the prediction
parquets only:
    RankIC, RankICIR, net SR, AR, MDD, mean daily turnover
and compares them item by item with the modelling-side numbers
(experiments/WORK-503/backtest_results.json).  Agreement requires a relative
difference <= 1e-6 on every item; every mismatch is listed and the RECOMPUTED
value is authoritative.

The portfolio is rebuilt here with a different code path than
scripts/work503_backtest.py (vectorised per-date ranking + a holdings state
machine instead of the day-by-day loop), so a coding error in either one shows
up as a mismatch rather than being silently duplicated.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-503")
DATA = os.path.join(REPO, "data", "WORK-503")
PANEL = os.path.join(DATA, "panel")
TOL = 1e-6
K = 30
N_DROP = 5


def load_panel():
    pit = pd.read_parquet(os.path.join(PANEL, "pit.parquet"))
    state = pd.read_parquet(os.path.join(PANEL, "trade_state.parquet"))
    state = state.reset_index()
    state["datetime"] = pd.to_datetime(state["datetime"])
    state = state.set_index(["instrument", "datetime"])
    return pit, state


def load_prices():
    """(instrument, datetime) -> (open, close) raw prices."""
    out = {}
    for path in glob.glob(os.path.join(DATA, "sina", "*.csv")):
        code = os.path.basename(path)[:-4]
        sym = code.replace(".", "").upper()
        df = pd.read_csv(path, usecols=["date", "open", "close"])
        df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
        for d, o, c in zip(df["date"], df["open"], df["close"]):
            out[(sym, d)] = (float(o), float(c))
    return out


def recompute(pred: pd.DataFrame, pit: pd.DataFrame, state: pd.DataFrame,
              prices: dict, buy_bps=5.0, sell_bps=15.0) -> dict:
    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    pred = pred.sort_values(["datetime", "instrument"])

    dates = list(pred["datetime"].unique())
    pit_dates = pit.index
    holdings = []
    rets, turnovers, costs = [], [], []

    for i in range(len(dates) - 1):
        d, d_next = dates[i], dates[i + 1]
        block = pred[pred["datetime"] == d]
        if block.empty:
            continue
        members = [c for c in pit.columns if bool(pit.loc[d, c])] \
            if d in pit_dates else []
        mset = set(members)
        block = block[block["instrument"].isin(mset)]
        if block.empty:
            continue
        block = block.sort_values("score", ascending=False)
        ranked = list(block["instrument"])
        score_of = dict(zip(block["instrument"], block["score"]))

        tradable = {}
        for s in ranked:
            key = (s, d)
            if key not in state.index:
                tradable[s] = False
                continue
            row = state.loc[key]
            tradable[s] = (not bool(row["suspended"])
                           and not bool(row["limit_up"]))

        held = [s for s in holdings if s in mset]
        held_sorted = sorted(held, key=lambda s: score_of.get(s, -np.inf))
        sellable = []
        for s in held_sorted:
            if len(sellable) >= N_DROP:
                break
            key = (s, d)
            if key in state.index:
                row = state.loc[key]
                if not bool(row["suspended"]) and not bool(row["limit_down"]):
                    sellable.append(s)
        keep = [s for s in held if s not in sellable]
        buy = []
        for s in ranked:
            if len(buy) >= len(sellable):
                break
            if tradable.get(s, False) and s not in keep and s not in buy:
                buy.append(s)
        target = keep + buy
        if not target:
            continue
        w = 1.0 / len(target)

        r = 0.0
        for s in target:
            p_next = prices.get((s, d_next))
            if p_next is None:
                continue
            open_next, close_next = p_next
            if s in buy:
                base = open_next
            else:
                p_now = prices.get((s, d))
                base = p_now[1] if p_now else np.nan
            if not np.isfinite(base) or base <= 0 or not np.isfinite(close_next):
                continue
            r += w * (close_next / base - 1.0)
        cost = len(sellable) * w * sell_bps / 1e4 + len(buy) * w * buy_bps / 1e4
        rets.append(r - cost)
        costs.append(cost)
        turnovers.append((len(sellable) + len(buy)) * w)
        holdings = target

    rets = np.asarray(rets, dtype="float64")
    g = np.log1p(rets)
    wealth = np.exp(np.cumsum(g))
    ar = float(np.exp(252 * g.mean()) - 1)
    mdd = float((1 - wealth / np.maximum.accumulate(wealth)).max())
    sr = float(np.sqrt(252) * g.mean() / g.std(ddof=0))

    ric = []
    for d, gdf in pred.groupby("datetime"):
        gdf = gdf.dropna(subset=["label", "score"])
        if len(gdf) < 3:
            ric.append(np.nan)
            continue
        ric.append(spearmanr(gdf["label"].to_numpy(),
                              gdf["score"].to_numpy()).statistic)
    ric = np.asarray(ric, dtype="float64")
    rankic = float(np.nanmean(ric))
    rankicir = float(np.nanmean(ric) / np.nanstd(ric))

    return {"rankic": rankic, "rankicir": rankicir, "ar": ar, "mdd": mdd,
            "sr": sr, "turnover": float(np.mean(turnovers)),
            "days": int(len(rets)), "mean_cost": float(np.mean(costs))}


def main():
    pit, state = load_panel()
    prices = load_prices()
    model_path = os.path.join(EXP, "backtest_results.json")
    if not os.path.exists(model_path):
        print("no backtest_results.json — nothing to compare")
        return 1
    model = json.load(open(model_path))

    out, mismatches = {}, []
    for key, mv in model.items():
        p = os.path.join(EXP, "preds", f"{key}.parquet")
        if not os.path.exists(p):
            continue
        pred = pd.read_parquet(p)
        rv = recompute(pred, pit, state, prices)
        out[key] = rv
        for field in ("rankic", "rankicir", "ar", "mdd", "sr", "turnover"):
            a, b = mv.get(field), rv.get(field)
            if a is None or b is None:
                continue
            denom = max(abs(a), abs(b), 1e-12)
            rel = abs(a - b) / denom
            if rel > TOL:
                mismatches.append({"key": key, "field": field,
                                   "model_side": a, "recompute": b,
                                   "rel_diff": rel})
    report = {"tolerance": TOL, "items": out, "mismatches": mismatches,
              "n_mismatches": len(mismatches)}
    json.dump(report, open(os.path.join(EXP, "audit_recompute.json"), "w"),
              ensure_ascii=False, indent=1)
    for k, v in out.items():
        print(f"{k}: recompute RankIC {v['rankic']:.6f} SR {v['sr']:.6f} "
              f"AR {v['ar']:.6f} MDD {v['mdd']:.6f} turn {v['turnover']:.6f}")
    print(f"mismatches: {len(mismatches)}")
    for m in mismatches:
        print("  ", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
