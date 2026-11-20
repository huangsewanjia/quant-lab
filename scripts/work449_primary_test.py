#!/usr/bin/env python3
"""WORK-449 · stage 3 step 1 — primary test: DeltaSharpe + block bootstrap.

Protocol: experiments/WORK-449/preregistration.md §9 (main test quantity,
bootstrap settings, verdict).

  DeltaSharpe = Sharpe(no_mask) - Sharpe(full_mask)      (primary)
  DeltaIC     = IC(no_mask) - IC(full_mask)              (corroboration)

  reproduced  <=> DeltaSharpe < 0 AND the 95% CI of DeltaSharpe excludes 0
  not reproduced <=> otherwise ("未复现" is still a delivered result)

Bootstrap: 1000 paired resamples of the daily net return series, block length
20 trading days, seed 42, 95% percentile interval.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

RF = 0.015
N_BOOT = 1000
BLOCK = 20
SEED = 42


def sharpe(net: np.ndarray, days: int) -> float:
    cum = np.cumprod(1.0 + net)
    total = float(cum[-1] - 1.0)
    ann = (1.0 + total) ** (365.25 / days) - 1.0
    vol = float(np.std(net, ddof=1) * np.sqrt(252))
    if vol <= 0:
        return float("nan")
    return (ann - RF) / vol


def block_indices(n: int, block: int, nb: int, rng) -> np.ndarray:
    starts = rng.integers(0, nb, size=nb)
    return np.concatenate([np.arange(s * block, (s + 1) * block) for s in starts])


def calendar_days(start: str, end: str) -> int:
    """Calendar days spanned by the test window.

    The backtest annualises the compounded return with 365.25 / *calendar*
    days (scripts/work449_backtest.py::metrics) while the volatility uses
    sqrt(252) trading days.  The primary test MUST use the same denominator,
    otherwise delta_sharpe would not equal
    sharpe(no_mask) - sharpe(full_mask) as reported by the arms.
    """
    return int((np.datetime64(end) - np.datetime64(start)).astype(int))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    arms = {}
    for arm in ("no_mask", "halt_only", "full_mask"):
        m = json.load(open(os.path.join(exp_dir, f"metrics_{arm}.json")))
        arms[arm] = m

    def run(cost_key: str):
        a = np.array(arms["no_mask"][cost_key]["net_returns"])
        b = np.array(arms["full_mask"][cost_key]["net_returns"])
        h = np.array(arms["halt_only"][cost_key]["net_returns"])
        meta = arms["no_mask"][cost_key]
        n_trading = int(meta["test_days"])
        cal_days = calendar_days(meta["start"], meta["end"])
        d_sharpe = float(sharpe(a, cal_days) - sharpe(b, cal_days))
        d_ic = (arms["no_mask"][cost_key]["apparent_ic_mean"] -
                arms["full_mask"][cost_key]["apparent_ic_mean"])
        rng = np.random.default_rng(SEED)
        nb = len(a) // BLOCK
        stats = np.empty(N_BOOT)
        for i in range(N_BOOT):
            sel = block_indices(len(a), BLOCK, nb, rng)
            # a resampled path covers len(sel) trading days; scale the calendar
            # span proportionally so the annualisation stays comparable.
            boot_days = cal_days * len(sel) / n_trading
            stats[i] = sharpe(a[sel], boot_days) - sharpe(b[sel], boot_days)
        lo, hi = np.percentile(stats, [2.5, 97.5])
        excludes_zero = bool(lo > 0 or hi < 0)
        reproduced = bool(d_sharpe < 0 and excludes_zero)
        # consistency: delta_sharpe must equal the difference of the two
        # Sharpes the arms themselves report.
        rep_delta = (float(arms["no_mask"][cost_key]["sharpe_rf1.5"]) -
                     float(arms["full_mask"][cost_key]["sharpe_rf1.5"]))
        return {
            "cost_bps": arms["no_mask"][cost_key]["cost_bps"],
            "delta_sharpe": d_sharpe,
            "delta_sharpe_ci95": [float(lo), float(hi)],
            "ci_excludes_zero": excludes_zero,
            "delta_ic": float(d_ic),
            "sharpe_no_mask": arms["no_mask"][cost_key]["sharpe_rf1.5"],
            "sharpe_halt_only": arms["halt_only"][cost_key]["sharpe_rf1.5"],
            "sharpe_full_mask": arms["full_mask"][cost_key]["sharpe_rf1.5"],
            "delta_sharpe_vs_reported_sharpes": rep_delta,
            "delta_sharpe_consistency_abs_err": abs(d_sharpe - rep_delta),
            "annualisation": {
                "calendar_days": cal_days,
                "trading_days": n_trading,
                "return_annualiser": "365.25 / calendar_days",
                "vol_annualiser": "sqrt(252) trading days",
            },
            "apparent_ic_no_mask": arms["no_mask"][cost_key]["apparent_ic_mean"],
            "apparent_ic_halt_only":
                arms["halt_only"][cost_key]["apparent_ic_mean"],
            "apparent_ic_full_mask":
                arms["full_mask"][cost_key]["apparent_ic_mean"],
            "verdict": "复现成立" if reproduced else "未复现",
            "bootstrap": {"n": N_BOOT, "block": BLOCK, "seed": SEED,
                          "paired": True},
        }

    out = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "primary_cost_bps": 8.0,
        "main": run("main"),
        "sensitivity_5bps": run("sensitivity_5bps"),
    }
    m = out["main"]
    out["verdict"] = m["verdict"]
    out["divergence_complete"] = bool(m["delta_ic"] > 0)
    json.dump(out, open(os.path.join(exp_dir, "primary_test.json"), "w"),
              indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    sys.exit(main())
