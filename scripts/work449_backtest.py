#!/usr/bin/env python3
"""WORK-449 · stage 2 step 5 — daily rebalanced Markowitz backtest for one arm.

Protocol: experiments/WORK-449/preregistration.md §8 (portfolio, costs),
§5.5 (execution constraint).

Pipeline per test day t:
  mu      = model prediction at t (only cells the arm may read)
  Sigma   = Ledoit-Wolf covariance of the last 120 trading days of returns,
            projected to PSD (eigenvalue floor 1e-10)
  QP      max mu'w - alpha*w'Sigma w  s.t. 1'w = 1, 0 <= w <= 3%
           solved by projected gradient (exact projection onto the box+simplex
           polytope); cross-checked against scipy SLSQP on sampled days
  exec    stocks whose M_exec = 0 keep yesterday's weight (cannot trade);
           stocks that left the pool are sold when tradable

Net return: r_net[t] = w_{t-1}'r[t] - c*||w[t]-w[t-1]||_1,  c = 8 bps (main),
5 bps (sensitivity).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

ALPHA = 10.0
W_MAX = 0.03
COV_LOOKBACK = 120
COST_MAIN_BPS = 8.0
COST_SENS_BPS = 5.0
RF = 0.015
MAX_ITER = 4000
TOL = 1e-12
# bisection steps for the water-level projection: the bracket is halved
# 2**52 times, i.e. shrunk far beyond double precision -> exact projection.
BISECT_STEPS = 52


def ledoit_wolf(X: np.ndarray):
    """Ledoit-Wolf (2004) shrinkage covariance — identical to sklearn's
    ``LedoitWolf`` (which is what the official implementation uses).

    X: [n_series, n_obs], already demeaned along the observation axis.
    The estimator treats the n_series series as observations of an
    n_series-dimensional variable over n_obs periods, so the sums run over
    periods.  Shrinkage:  S_hat = (1-delta)*S + delta*mu*I,  mu = tr(S)/n.
    """
    n, t = X.shape
    if n < 2 or t < 3:
        return np.eye(n) * 1e-8, 0.0
    S = (X @ X.T) / t
    diag = np.einsum("ij,ij->j", X, X) / t      # diagonal of S, shape (n,)
    mu = float(diag.sum()) / n
    # beta_ = sum_i (sum_t X2[t, i])^2 = t^2 * sum_i diag_i^2
    beta_ = t * t * float(np.sum(diag ** 2))
    # delta_ = (1/t^2) * sum_{s,t} (x_s . x_t)^2 = ||S||_F^2
    delta_ = float(np.sum(S ** 2))
    beta = 1.0 / (n * t) * (beta_ / t - delta_)
    delta = delta_ - 2.0 * mu * float(diag.sum()) + n * mu * mu
    delta /= n
    beta = min(beta, delta)
    shrink = 0.0 if beta == 0 else beta / delta
    shrunk = (1.0 - shrink) * S + shrink * mu * np.eye(n)
    return shrunk, shrink


def psd_clip(S: np.ndarray, floor: float = 1e-10):
    w, V = np.linalg.eigh(S)
    w = np.maximum(w, floor)
    return (V * w) @ V.T


def project_box_simplex(v: np.ndarray, c: float, target: float) -> np.ndarray:
    """Euclidean projection onto {0 <= w <= c, sum(w) = target}.

    Bisection on the water level lambda (protocol §8: "等式-箱约束的欧氏投影用
    二分水位法精确求").  The bracket [min(v)-c, max(v)] is halved BISECT_STEPS
    times, i.e. shrunk by 2**52, which is far beyond double precision, so the
    projected point is exact to machine precision.
    """
    n = len(v)
    if target <= 0:
        return np.zeros(n)
    lo = float(np.min(v) - c)
    hi = float(np.max(v))
    for _ in range(BISECT_STEPS):
        mid = 0.5 * (lo + hi)
        s = np.clip(v - mid, 0.0, c).sum()
        if s > target:
            lo = mid
        else:
            hi = mid
    lam = 0.5 * (lo + hi)
    return np.clip(v - lam, 0.0, c)


def solve_qp(mu: np.ndarray, Sigma: np.ndarray, w_fixed: np.ndarray,
             free: np.ndarray, target: float, alpha: float = ALPHA,
             w_max: float = W_MAX, warm: np.ndarray | None = None):
    """Projected-gradient QP over the free block; w_fixed holds the rest.

    `warm` is an optional warm start for the free block (the previous day's
    solution, rescaled to `target`); it only changes the iteration path, not
    the optimum (the objective is strictly convex on the free block).
    """
    nf = int(free.sum())
    if nf == 0 or target <= 0:
        return np.zeros_like(mu)
    muF = mu[free]
    SigFF = Sigma[np.ix_(free, free)]
    SigFB = Sigma[np.ix_(free, ~free)] @ w_fixed[~free]
    L = 2.0 * alpha * max(float(np.linalg.eigvalsh(SigFF).max()), 1e-12)
    step = 1.0 / L
    x = np.full(nf, target / nf)
    if warm is not None and len(warm) == nf:
        wsum = float(np.sum(warm))
        if wsum > 1e-12:
            cand = warm * (target / wsum)
            x = project_box_simplex(cand, w_max, target)
    y = x.copy()
    tk = 1.0
    z_prev = x.copy()
    for _ in range(MAX_ITER):
        g = -muF + 2.0 * alpha * (SigFF @ y + SigFB)
        x_new = project_box_simplex(y - step * g, w_max, target)
        # convergence: projected gradient norm
        g2 = -muF + 2.0 * alpha * (SigFF @ x_new + SigFB)
        x_new = project_box_simplex(x_new - step * g2, w_max, target)
        if np.max(np.abs(x_new - x)) < TOL:
            x = x_new
            break
        tk_new = 0.5 * (1.0 + np.sqrt(1.0 + 4.0 * tk * tk))
        y = x_new + ((tk - 1.0) / tk_new) * (x_new - z_prev)
        z_prev = x_new
        x = x_new
        tk = tk_new
    out = w_fixed.copy()
    out[free] = x
    return out


def slsqp_qp(mu, Sigma, w_fixed, free, target, alpha=ALPHA, w_max=W_MAX):
    """Independent reference solver (scipy SLSQP) for the cross-check.

    The gradient is the exact analytic gradient of the objective; the box
    constraints are handed to scipy through `bounds` (scipy handles the
    active-set logic itself).  Masking the gradient with (x > 0) would
    wrongly freeze variables sitting at the lower bound whose gradient
    points upwards, and the cross-check would then compare two different
    optima instead of the same one.
    """
    from scipy.optimize import minimize
    nf = int(free.sum())
    if nf == 0 or target <= 0:
        return np.zeros_like(mu)
    muF = mu[free]
    SigFF = Sigma[np.ix_(free, free)]
    SigFB = Sigma[np.ix_(free, ~free)] @ w_fixed[~free]

    def f(x):
        wF = np.clip(x, 0.0, w_max)
        w = w_fixed.copy()
        w[free] = wF
        return float(alpha * w @ Sigma @ w - mu @ w)

    def grad(x):
        wF = np.clip(x, 0.0, w_max)
        return 2.0 * alpha * (SigFF @ wF + SigFB) - muF

    x0 = np.full(nf, min(target / nf, w_max))
    cons = [{"type": "eq", "fun": lambda x: x.sum() - target,
             "jac": lambda x: np.ones_like(x)}]
    bnds = [(0.0, w_max)] * nf
    res = minimize(f, x0, jac=grad, bounds=bnds, constraints=cons,
                   method="SLSQP", options={"maxiter": 1000, "ftol": 1e-12})
    out = w_fixed.copy()
    out[free] = np.clip(res.x, 0.0, w_max)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    ap.add_argument("--arm", required=True,
                    choices=["no_mask", "halt_only", "full_mask"])
    ap.add_argument("--solver-check-days", type=int, default=5)
    ap.add_argument("--tag", default="",
                    help="suffix for the output file names, e.g. '_s43'; "
                         "empty keeps the protocol-frozen file names")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    pred_npz = np.load(os.path.join(exp_dir, f"pred_{args.arm}{args.tag}.npz"),
                       allow_pickle=True)
    dates = panel["dates"]
    codes = panel["codes"]
    pred = pred_npz["pred"]
    sample_ok = pred_npz["sample_ok"]
    r_eff = panel["r_eff"]
    m_exec = panel["m_exec"]
    has_row = panel["has_row"]

    # pool membership by effective date
    lists = json.load(open(os.path.join(exp_dir, "pool_membership.json")))
    eff = np.array(sorted(lists))
    members = {d: {c for c, _ in lists[d]} for d in lists}
    code_idx = {c: i for i, c in enumerate(codes)}
    in_pool = np.zeros((len(dates), len(codes)), dtype=bool)
    cur = set()
    for t, d in enumerate(dates):
        key = str(d)
        if key in members:
            cur = members[key]
        else:
            past = [e for e in eff if e <= key]
            cur = members[past[-1]] if past else set()
        in_pool[t] = [c in cur for c in codes]

    test_sel = (dates >= np.datetime64("2022-01-01")) & \
               (dates <= np.datetime64("2024-12-31"))
    tidx = np.nonzero(test_sel)[0]

    weights = np.zeros((len(dates), len(codes)))
    w_prev = np.zeros(len(codes))
    solver_check = []
    rng = np.random.default_rng(42)
    check_days = set(rng.choice(tidx[1:],
                                size=min(args.solver_check_days,
                                         len(tidx) - 1),
                                replace=False).tolist())

    for t in tidx:
        mu = np.nan_to_num(pred[t], nan=0.0)
        pool = in_pool[t]
        prev_pool = in_pool[t - 1] if t > 0 else np.zeros_like(pool)
        left = prev_pool & ~pool
        tradable = pool | left
        blocked = tradable & ~m_exec[t]
        forced_zero = left & m_exec[t]
        frozen = blocked | (left & ~m_exec[t])
        free = tradable & m_exec[t] & ~left
        # stocks never held and not in the pool cannot be bought
        free &= (w_prev > 0) | pool
        w_fixed = np.where(frozen, w_prev, 0.0)
        w_fixed[forced_zero] = 0.0
        target = 1.0 - w_fixed.sum()
        if target < 0:
            target = 0.0
            w_fixed = w_fixed * 0.0
        # covariance from the last COV_LOOKBACK days of returns
        lo = max(0, t - COV_LOOKBACK)
        live = has_row[lo:t, :].any(axis=0)
        idx = np.nonzero(live)[0]
        Sigma = np.zeros((len(codes), len(codes)))
        if len(idx) > 5:
            # halted days carry a 0 return (halt-spanning convention); days
            # before the listing are zero as well.
            X = np.nan_to_num(r_eff[lo:t, idx].T, nan=0.0)
            if X.shape[0] > 5 and X.shape[1] > 10:
                Xc = X - X.mean(axis=1, keepdims=True)
                S, inten = ledoit_wolf(Xc)
                S = psd_clip(S)
                Sigma[np.ix_(idx, idx)] = S
        else:
            Sigma = np.eye(len(codes)) * 1e-6

        w = solve_qp(mu, Sigma, w_fixed, free, target,
                     warm=w_prev[free] if free.any() else None)
        if t in check_days:
            w2 = slsqp_qp(mu, Sigma, w_fixed, free, target)
            denom = max(np.abs(w2).max(), 1e-12)
            obj = lambda v: float(ALPHA * v @ Sigma @ v - mu @ v)
            o1, o2 = obj(w), obj(w2)
            solver_check.append({
                "date": str(dates[t]),
                "max_abs_diff": float(np.max(np.abs(w - w2))),
                "max_rel_diff": float(np.max(np.abs(w - w2)) / denom),
                "obj_pgp": o1,
                "obj_slsqp": o2,
                "obj_rel_diff": float(abs(o1 - o2) / max(abs(o2), 1e-16)),
                "sum_w": float(w.sum()), "sum_w_slsqp": float(w2.sum()),
            })
        weights[t] = w
        w_prev = w

    # ---- metrics ----
    def metrics(cost_bps: float):
        net, gross, turn = [], [], []
        for k, t in enumerate(tidx):
            rp = r_eff[t]
            rp = np.nan_to_num(rp, nan=0.0)
            wp = weights[t - 1] if t > 0 else np.zeros(len(codes))
            dw = np.abs(weights[t] - wp).sum()
            g = float(wp @ rp)
            n = g - (cost_bps * 1e-4) * dw
            gross.append(g)
            net.append(n)
            turn.append(dw)
        net = np.array(net)
        gross = np.array(gross)
        turn = np.array(turn)
        cum = np.cumprod(1.0 + net)
        mdd = float(np.min(cum / np.maximum.accumulate(cum) - 1.0))
        days = (np.datetime64(str(dates[tidx[-1]])) -
                np.datetime64(str(dates[tidx[0]]))).astype(int)
        total = float(cum[-1] - 1.0)
        ann = (1.0 + total) ** (365.25 / days) - 1.0
        vol = float(np.std(net, ddof=1) * np.sqrt(252))
        sharpe = (ann - RF) / vol if vol > 0 else None
        # daily apparent IC (protocol §9.2: corr(yhat_t, r_{t+1}) on the
        # arm's valid stock set)
        ics = []
        for t in tidx:
            if t + 1 >= len(dates):
                continue
            p = pred[t]
            y = r_eff[t + 1]
            ok = sample_ok[t].copy()
            ok &= np.isfinite(p) & np.isfinite(y)
            if ok.sum() >= 10:
                ics.append(float(np.corrcoef(p[ok], y[ok])[0, 1]))
        ics = np.array(ics)
        return {
            "cost_bps": cost_bps,
            "test_days": int(len(tidx)),
            "start": str(dates[tidx[0]]), "end": str(dates[tidx[-1]]),
            "total_return": total,
            "ann_return": float(ann),
            "ann_vol": vol,
            "sharpe_rf1.5": sharpe,
            "max_drawdown": mdd,
            "turnover_two_sided_total": float(turn.sum()),
            "turnover_one_sided_total": float(turn.sum() / 2.0),
            "turnover_daily_mean_two_sided": float(turn.mean()),
            "apparent_ic_mean": float(ics.mean()) if len(ics) else None,
            "apparent_ic_std": float(ics.std(ddof=1)) if len(ics) > 1 else None,
            "apparent_ic_t": (float(ics.mean() / (ics.std(ddof=1) /
                                                   np.sqrt(len(ics))))
                              if len(ics) > 1 and ics.std(ddof=1) > 0 else None),
            "ic_days": int(len(ics)),
            "avg_active_stocks": float(
                np.mean([(np.abs(weights[t]) > 1e-9).sum() for t in tidx])),
            "net_returns": net.tolist(),
            "gross_returns": gross.tolist(),
        }

    main_m = metrics(COST_MAIN_BPS)
    sens_m = metrics(COST_SENS_BPS)
    out = {
        "arm": args.arm,
        "generated_at": datetime.now().astimezone().isoformat(),
        "alpha": ALPHA, "w_max": W_MAX, "cov_lookback": COV_LOOKBACK,
        "cost_bps_main": COST_MAIN_BPS, "cost_bps_sensitivity": COST_SENS_BPS,
        "solver_cross_check": solver_check,
        "solver_check_max_rel_diff": (max(c["max_rel_diff"]
                                           for c in solver_check)
                                       if solver_check else None),
        "solver_check_max_obj_rel_diff": (max(c["obj_rel_diff"]
                                               for c in solver_check)
                                           if solver_check else None),
        "main": main_m, "sensitivity_5bps": sens_m,
    }
    json.dump(out, open(os.path.join(exp_dir, f"metrics_{args.arm}{args.tag}.json"), "w"),
              indent=1)
    np.savez_compressed(os.path.join(exp_dir, f"weights_{args.arm}{args.tag}.npz"),
                        weights=weights, dates=dates, codes=codes)
    print(json.dumps({k: v for k, v in main_m.items()
                      if k not in ("net_returns", "gross_returns")}, indent=1))
    print("solver check max rel diff:",
          out["solver_check_max_rel_diff"])


if __name__ == "__main__":
    sys.exit(main())
