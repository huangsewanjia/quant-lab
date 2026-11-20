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
        days = arms["no_mask"][cost_key]["test_days"]
        d_sharpe = float(sharpe(a, days) - sharpe(b, days))
        d_ic = (arms["no_mask"][cost_key]["apparent_ic_mean"] -
                arms["full_mask"][cost_key]["apparent_ic_mean"])
        rng = np.random.default_rng(SEED)
        nb = len(a) // BLOCK
        stats = np.empty(N_BOOT)
        for i in range(N_BOOT):
            sel = block_indices(len(a), BLOCK, nb, rng)
            stats[i] = sharpe(a[sel], days) - sharpe(b[sel])
        lo, hi = np.percentile(stats, [2.5, 97.5])
        excludes_zero = bool(lo > 0 or hi < 0)
        reproduced = bool(d_sharpe < 0 and excludes_zero)
        return {
            "cost_bps": arms["no_mask"][cost_key]["cost_bps"],
            "delta_sharpe": d_sharpe,
            "delta_sharpe_ci95": [float(lo), float(hi)],
            "ci_excludes_zero": excludes_zero,
            "delta_ic": float(d_ic),
            "sharpe_no_mask": arms["no_mask"][cost_key]["sharpe_rf1.5"],
            "sharpe_halt_only": arms["halt_only"][cost_key]["sharpe_rf1.5"],
            "sharpe_full_mask": arms["full_mask"][cost_key]["sharpe_rf1.5"],
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
