#!/usr/bin/env python3
"""WORK-449 · parity test: our Ledoit-Wolf vs sklearn's LedoitWolf.

The official implementation (initial-d/ml-quant-trading, MIT,
src/mlquant/portfolio/markowitz.py) estimates the covariance with
``sklearn.covariance.LedoitWolf().fit(history).covariance_``.  This test
pins our numpy implementation to that reference, because the two shrinkage
formulations (Ledoit-Wolf 2003's b-bar^2/d-bar^2 vs 2004's beta/delta) differ
by an order of magnitude in the shrinkage intensity and silently turn the
covariance into a scaled identity.

It also re-checks the QP solver against scipy SLSQP on synthetic problems.

Exit code 0 = all checks pass.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOL_LW = 1e-12
TOL_QP = 1e-6


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    bt = _load("work449_backtest", os.path.join(HERE, "work449_backtest.py"))
    try:
        from sklearn.covariance import LedoitWolf
    except ImportError:
        print("SKIP: scikit-learn not installed")
        return 0

    failures = []
    rng = np.random.default_rng(20261120)
    print("Ledoit-Wolf vs sklearn.covariance.LedoitWolf")
    for (n, t) in [(297, 120), (300, 120), (50, 200), (680, 120),
                   (300, 2000), (20, 60), (137, 121)]:
        f = rng.normal(size=(t, 1)) * 0.015
        X = (f + rng.normal(size=(t, n)) * 0.012).T          # (series, obs)
        Xc = X - X.mean(axis=1, keepdims=True)
        S_mine, delta_mine = bt.ledoit_wolf(Xc)
        lw = LedoitWolf().fit(Xc.T)                           # sklearn: (obs, series)
        S_ref = lw.covariance_
        rel = float(np.max(np.abs(S_mine - S_ref)) /
                    max(np.abs(S_ref).max(), 1e-18))
        d_ok = abs(delta_mine - lw.shrinkage_) <= 1e-12
        ok = (rel <= TOL_LW) and d_ok
        print(f"  n={n:4d} t={t:5d}  delta {delta_mine:.8f} vs "
              f"{lw.shrinkage_:.8f}  max_rel_diff={rel:.3e}  "
              f"{'OK' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"ledoit_wolf n={n} t={t} rel={rel:.3e}")

    print("\nQP (projected gradient) vs scipy SLSQP")
    for (n, t) in [(300, 120), (120, 200)]:
        f = rng.normal(size=(t, 1)) * 0.015
        X = (f + rng.normal(size=(t, n)) * 0.012).T
        Xc = X - X.mean(axis=1, keepdims=True)
        S, _ = bt.ledoit_wolf(Xc)
        S = bt.psd_clip(S)
        mu = rng.normal(size=n) * 0.01
        w_fixed = np.zeros(n)
        free = np.ones(n, dtype=bool)
        w1 = bt.solve_qp(mu, S, w_fixed, free, 1.0)
        w2 = bt.slsqp_qp(mu, S, w_fixed, free, 1.0)
        obj = lambda v: float(bt.ALPHA * v @ S @ v - mu @ v)
        o1, o2 = obj(w1), obj(w2)
        obj_rel = abs(o1 - o2) / max(abs(o2), 1e-16)
        w_rel = float(np.max(np.abs(w1 - w2)) / max(np.abs(w2).max(), 1e-12))
        ok = obj_rel <= TOL_QP
        print(f"  n={n:4d} t={t:5d}  obj_rel_diff={obj_rel:.3e}  "
              f"weight_rel_diff={w_rel:.3e}  {'OK' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"qp n={n} obj_rel={obj_rel:.3e}")

    print(f"\nfailures={len(failures)}")
    for x in failures:
        print("  FAIL", x)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
