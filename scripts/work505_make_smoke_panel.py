#!/usr/bin/env python3
"""WORK-505 · synthetic smoke panel.

Builds a MINIMAL panel.npz / factors_<arm>.npz / pool_membership.json whose
schema is byte-compatible with WORK-449's, so that the whole WORK-505 chain
(transformer training -> WORK-449's backtest -> metrics json) can be executed
end-to-end on this box WITHOUT the frozen snapshot.

This is a SMOKE TEST ONLY.  The panel is synthetic (random prices with a
learnable signal injected); no number produced from it is a research result.
"""
from __future__ import annotations

import json
import os

import numpy as np

EXP = "WORK-505-SMOKE"
N_STOCKS = 40
N_FEAT = 12
SEED = 20261122
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP_DIR = os.path.join(REPO, "experiments", EXP)
os.makedirs(EXP_DIR, exist_ok=True)

rng = np.random.default_rng(SEED)
try:                                            # numpy < 3
    _bd = np.busday_range("2014-01-02", "2022-06-30")
except AttributeError:                          # numpy >= 3 dropped busday_range
    import pandas as pd
    _bd = pd.bdate_range("2014-01-02", "2022-06-30").values
dates = np.array(_bd, dtype="datetime64[D]")
T, N = len(dates), N_STOCKS
codes = np.array([f"smk.{i:06d}" for i in range(N)])

# ---- synthetic factor panel (T, N, K) with a learnable signal ---------------
w_true = rng.normal(size=N_FEAT).astype(np.float32)
raw = rng.normal(size=(T, N, N_FEAT)).astype(np.float32)
values = raw + 0.9 * np.tanh(raw @ w_true)[..., None]

# ---- synthetic price / return panel ---------------------------------------
r_eff = (0.02 * np.tanh(values @ w_true)
         + rng.normal(scale=0.012, size=(T, N))).astype(np.float64)
r_eff[0, :] = 0.0
has_row = np.ones((T, N), dtype=bool)
em_row = has_row.copy()
suspended = rng.random((T, N)) < 0.02
at_limit = rng.random((T, N)) < 0.01
listed = np.array(["2010-01-01"] * N)
listed_1y = (dates[:, None] > (np.datetime64("2011-01-01") +
                               np.timedelta64(0, "D")))

m_exec = has_row & ~suspended & ~at_limit & listed_1y
m_no = has_row
m_halt = has_row & ~suspended
m_full = m_exec

close_hfq = np.cumprod(1 + r_eff, axis=0) * 10.0
close_raw = close_hfq / rng.uniform(0.5, 1.5, size=N)
preclose = np.vstack([np.full((1, N), np.nan), close_hfq[:-1]])
volume = np.abs(rng.normal(1e6, 2e5, size=(T, N)))
amount = volume * close_hfq
pct_chg = r_eff * 100.0

np.savez_compressed(
    os.path.join(EXP_DIR, "panel.npz"),
    dates=dates, codes=codes,
    close_hfq=close_hfq, close_hfq_ff=close_hfq, close_raw=close_raw,
    high_hfq=close_hfq * 1.01, low_hfq=close_hfq * 0.99,
    adj_close=close_hfq, r_eff=r_eff, r_raw=r_eff, pct_chg=pct_chg,
    volume=volume, amount=amount, has_row=has_row, em_row=em_row,
    suspended=suspended, at_limit=at_limit, listed_1y=listed_1y,
    corrupt=np.zeros((T, N), dtype=bool),
    limit_pct=np.full((T, N), 0.10), band=np.full((T, N), 0.10),
    m_exec=m_exec, m_no=m_no, m_halt=m_halt, m_full=m_full,
    listed=listed.astype(str),
)

# ---- per-arm factors (values differ slightly per arm: the mask matters) ---
for arm in ("no_mask", "halt_only", "full_mask"):
    m = {"no_mask": m_no, "halt_only": m_halt, "full_mask": m_full}[arm]
    vals = values.copy()
    masks = np.repeat(m[:, :, None], N_FEAT, axis=2).copy()
    # a stock that is masked out has no valid factors -> NaN values there
    vals[~m, :] = np.nan
    np.savez_compressed(os.path.join(EXP_DIR, f"factors_{arm}.npz"),
                        values=vals.astype(np.float32),
                        masks=masks,
                        names=np.array([f"f{k}" for k in range(N_FEAT)]))

# ---- pool membership ------------------------------------------------------
lists = {"2014-01-02": [[str(c), "smoke"] for c in codes]}
json.dump(lists, open(os.path.join(EXP_DIR, "pool_membership.json"), "w"),
          indent=1)

print(f"smoke panel written to {EXP_DIR}")
print(f"  dates {dates[0]}..{dates[-1]} (T={T})  codes={N}  features={N_FEAT}")
print(f"  pre-training days (before 2015-01-01): "
      f"{int(((dates < np.datetime64('2015-01-01'))).sum())}")
print(f"  test-window days (2022-01-01..2024-12-31): "
      f"{int(((dates >= np.datetime64('2022-01-01'))).sum())}")
print(f"  mask rates: no={m_no.mean():.4f} halt={m_halt.mean():.4f} "
      f"full={m_full.mean():.4f}")
