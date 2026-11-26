#!/usr/bin/env python3
"""WORK-503 — cost sensitivity table (pre-registered cost points).

Pre-registration §5.2 froze three cost points:
  * main      : buy 5 bps + sell 15 bps
  * 单边 8 bps : buy 8 bps + sell 8 bps
  * 双边 30 bps: buy 15 bps + sell 15 bps
A zero-cost run is also kept, clearly labelled as a **post-hoc diagnostic**
(not part of the pre-registered protocol) because it is the only way to
separate "the signal has no edge" from "the edge does not survive the
turnover cost".

This script only assembles the already-produced backtest json files into
one table; it never recomputes anything.

    ../env/.venv/bin/python -u scripts/work503_cost_sensitivity.py
"""
from __future__ import annotations

import json
import os

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-503")

RUNS = [
    ("main_5_15bps", "backtest_results.json", "预注册主口径（买 5 / 卖 15 bps）", True),
    ("single_8bps", "backtest_cost_8bps.json", "预注册敏感性（单边 8 bps）", True),
    ("roundtrip_30bps", "backtest_cost_30bps.json", "预注册敏感性（双边 30 bps）", True),
    ("gross_0bps", "backtest_gross_diagnostic.json", "**预注册外诊断**（零成本）", False),
]


def main():
    rows = []
    for label, fname, desc, prereg in RUNS:
        p = os.path.join(EXP, fname)
        if not os.path.exists(p):
            print(f"missing {fname} — skipped")
            continue
        d = json.load(open(p))
        for key, v in d.items():
            if not key.startswith("full_"):
                continue
            rows.append({
                "cost_point": label,
                "description": desc,
                "pre_registered": prereg,
                "run": key,
                "rankic": v.get("rankic"),
                "sr_net": v.get("sr"),
                "ar_net": v.get("ar"),
                "mdd": v.get("mdd"),
                "turnover_1side": v.get("turnover"),
                "alpha_index_annualized": (v.get("alpha_index") or {}).get(
                    "excess_annualized"),
                "alpha_ew_annualized": (v.get("alpha_equal_weight") or {}).get(
                    "excess_annualized"),
                "alpha_totalcap_annualized": (v.get("alpha_cap_weighted") or {}).get(
                    "excess_annualized"),
            })
    if not rows:
        print("no cost-sensitivity runs found")
        return 1
    tab = pd.DataFrame(rows)
    out_csv = os.path.join(EXP, "cost_sensitivity.csv")
    tab.to_csv(out_csv, index=False)
    json.dump({"rows": rows}, open(os.path.join(EXP, "cost_sensitivity.json"), "w"),
              ensure_ascii=False, indent=1, default=float)
    print(tab.to_string(index=False))
    print(f"-> {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
