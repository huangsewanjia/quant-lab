#!/usr/bin/env python3
"""WORK-449 · stage 2 step 3 — mask-aware factor library (12 factors).

Protocol: experiments/WORK-449/preregistration.md §5 (mask semantics), §6
(factor list), §5.4 (window assertion).

Primitive semantics follow the official implementation
(initial-d/ml-quant-trading, features/tensor_factors.py) as frozen in the
protocol §5.3:
  ts_mean  out_mask = count(mask) > 0
  ts_std   out_mask = count(mask) >= 2
  ts_corr  out_mask = count(mask) >= 2
  ts_rank  out_mask = all(mask) in window
  ts_min/max out_mask = any(mask) in window
  masked cells contribute 0 to the numerator and are excluded from the count,
  so an output value never depends on a masked input value.

Window assertion (protocol §5.4), operationalised on §5.3: a valid output cell
"consumed" a non-executable price iff its dependency window contains a cell
that the arm READ (arm mask = 1) but that is not executable (M_exec = 0).
  no_mask   reads limit/suspended cells -> must fire
  halt_only reads limit cells           -> must fire
  full_mask reads only M_exec = 1 cells  -> must not fire
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

FACTOR_SPEC = [
    # (name, kind, window, span_days)
    ("mom_5", "shift_ratio", 5, 6),
    ("mom_20", "shift_ratio", 20, 21),
    ("mom_60", "shift_ratio", 60, 61),
    ("ts_mean_ret_5", "ts_mean", 5, 5),
    ("ts_mean_ret_20", "ts_mean", 20, 20),
    ("ts_std_ret_20", "ts_std", 20, 20),
    ("ts_corr_close_vol_20", "ts_corr", 20, 20),
    ("ts_rank_close_60", "ts_rank", 60, 60),
    ("ts_max_ret_20", "ts_max", 20, 20),
    ("ts_min_ret_20", "ts_min", 20, 20),
    ("range_1", "range", 1, 1),
    ("amt_proxy_20", "ts_mean", 20, 20),
]
ARMS = ("no_mask", "halt_only", "full_mask")
CHUNK = 128


def _win(x, w):
    """(T-w+1, N, w) sliding windows along the time axis."""
    return sliding_window_view(x, w, axis=0)


def _roll(fn, *arrays, w, chunk=CHUNK):
    """Apply fn to sliding windows; out[t] uses rows [t-w+1, t]."""
    T = arrays[0].shape[0]
    out = np.full((T,) + arrays[0].shape[1:], np.nan)
    for s in range(0, T, chunk):
        e = min(s + chunk + w - 1, T)
        seg = [a[s:e] for a in arrays]
        if seg[0].shape[0] < w:
            break
        wins = [_win(a, w) for a in seg]
        res = fn(*wins)
        t0 = s + w - 1
        m = min(res.shape[0], T - t0)
        if m > 0:
            out[t0:t0 + m] = res[:m]
    return out


def ts_mean(x, m, w):
    xf = np.nan_to_num(x)
    mf = m.astype(np.float64)
    xm = np.nan_to_num(x) * mf

    def fn(xw, mw):
        n = mw.sum(axis=2)
        return np.where(n > 0, xw.sum(axis=2) / np.maximum(n, 1.0), np.nan)
    val = _roll(fn, xm, mf, w=w)
    cnt = _roll(lambda mw: mw.sum(axis=2), mf, w=w)
    return val, cnt > 0


def ts_std(x, m, w):
    xf = np.nan_to_num(x)
    mf = m.astype(np.float64)
    xm = xf * mf

    def fn(xw, mw):
        n = mw.sum(axis=2)
        mean = xw.sum(axis=2) / np.maximum(n, 1.0)
        var = (((xw - mean[:, :, None]) ** 2) * mw).sum(axis=2) / np.maximum(n - 1, 1)
        return np.where(n >= 2, np.sqrt(var), np.nan)
    val = _roll(fn, xm, mf, w=w)
    cnt = _roll(lambda mw: mw.sum(axis=2), mf, w=w)
    return val, cnt >= 2


def ts_corr(x, y, m, w):
    xf = np.nan_to_num(x)
    yf = np.nan_to_num(y)
    mf = m.astype(np.float64)
    xm = xf * mf
    ym = yf * mf

    def fn(xw, yw, mw):
        n = np.maximum(mw.sum(axis=2), 2.0)[:, :, None]
        mx = (xw * mw).sum(axis=2, keepdims=True) / n
        my = (yw * mw).sum(axis=2, keepdims=True) / n
        cx = (xw - mx) * mw
        cy = (yw - my) * mw
        cov = (cx * cy).sum(axis=2)
        sx = np.sqrt((cx ** 2).sum(axis=2))
        sy = np.sqrt((cy ** 2).sum(axis=2))
        return cov / np.maximum(sx * sy, 1e-12)
    val = _roll(fn, xm, ym, mf, w=w)
    cnt = _roll(lambda mw: mw.sum(axis=2), mf, w=w)
    return val, cnt >= 2


def ts_rank(x, m, w):
    xf = np.nan_to_num(x)
    mf = m.astype(np.float64)

    def fn(xw, mw):
        neg_inf = np.where(mw > 0, xw, -np.inf)
        last = neg_inf[:, -1:, :]
        rank = (neg_inf <= last).sum(axis=2).astype(np.float64)
        n = np.maximum(mw.sum(axis=2), 1.0)
        return np.where(np.all(mw > 0, axis=2), rank / n, np.nan)
    val = _roll(fn, xf, mf, w=w)
    allv = _roll(lambda mw: np.all(mw > 0, axis=2), mf, w=w) > 0.5
    return val, allv


def ts_min(x, m, w):
    xf = np.nan_to_num(x)
    mf = m.astype(np.float64)

    def fn(xw, mw):
        big = np.where(mw > 0, xw, np.inf)
        return np.where(mw.sum(axis=2) > 0, big.min(axis=2), np.nan)
    val = _roll(fn, xf, mf, w=w)
    anyv = _roll(lambda mw: mw.sum(axis=2) > 0, mf, w=w) > 0.5
    return val, anyv


def ts_max(x, m, w):
    xf = np.nan_to_num(x)
    mf = m.astype(np.float64)

    def fn(xw, mw):
        small = np.where(mw > 0, xw, -np.inf)
        return np.where(mw.sum(axis=2) > 0, small.max(axis=2), np.nan)
    val = _roll(fn, xf, mf, w=w)
    anyv = _roll(lambda mw: mw.sum(axis=2) > 0, mf, w=w) > 0.5
    return val, anyv


def shift_ratio(x, m, w):
    """x_t / x_{t-w} - 1, both endpoints must be in-mask."""
    T, N = x.shape
    xs = np.full((T, N), np.nan)
    ms = np.zeros((T, N), dtype=bool)
    xs[w:, :] = x[:-w, :]
    ms[w:, :] = m[:-w, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        val = x / xs - 1.0
    ok = m & ms & (w <= T - 1)
    return val, ok


def rolling_sum(x, w):
    """Rolling sum over the time axis, window w (positions < w -> NaN)."""
    T = x.shape[0]
    c = np.concatenate([np.zeros((1,) + x.shape[1:]), np.nancumsum(x, axis=0)], axis=0)
    out = np.full(x.shape, np.nan)
    if w <= T:
        out[w - 1:, :] = c[w:, :] - c[:-w, :]
    return out


def neutralize_day(values, valid):
    """MAD de-trend (5x) then z-score, cross-sectionally, within valid cells."""
    T, N = values.shape
    out = np.full((T, N), np.nan)
    for t in range(T):
        v = values[t]
        ok = valid[t] & np.isfinite(v)
        if ok.sum() < 3:
            continue
        x = v[ok]
        med = np.median(x)
        mad = np.median(np.abs(x - med))
        if mad > 0:
            xs = (x - med) / (1.4826 * mad)
            limit = 5.0
            xs = np.clip(xs, -limit, limit)
        else:
            xs = x - med
        sd = xs.std()
        if sd <= 0:
            continue
        out[t, ok] = xs / sd
    return out


ARM_KEY = {"no_mask": "m_no", "halt_only": "m_halt", "full_mask": "m_full"}


def build_arm(panel, arm):
    m = panel[ARM_KEY[arm]]
    m_exec = panel["m_exec"]
    adj_close = panel["adj_close"]
    r_eff = panel["r_eff"]
    volume = panel["volume"]
    amount = panel["amount"]
    high = panel["high_qfq"]
    low = panel["low_qfq"]
    corrupt = panel["corrupt"]

    factors, fmasks = [], []
    taint_counts = {}
    for name, kind, w, span in FACTOR_SPEC:
        if kind == "shift_ratio":
            val, ok = shift_ratio(adj_close, m, w)
        elif kind == "ts_mean":
            src = amount if name == "amt_proxy_20" else r_eff
            val, ok = ts_mean(src, m, w)
        elif kind == "ts_std":
            val, ok = ts_std(r_eff, m, w)
        elif kind == "ts_corr":
            val, ok = ts_corr(adj_close, volume, m, w)
        elif kind == "ts_rank":
            val, ok = ts_rank(adj_close, m, w)
        elif kind == "ts_max":
            val, ok = ts_max(r_eff, m, w)
        elif kind == "ts_min":
            val, ok = ts_min(r_eff, m, w)
        elif kind == "range":
            with np.errstate(divide="ignore", invalid="ignore"):
                val = (high - low) / adj_close
            ok = m & ~corrupt & np.isfinite(val)
            val = np.where(ok, val, np.nan)
        else:
            raise ValueError(kind)

        # window assertion: consumed a non-executable cell?
        consumed_bad = (m & ~m_exec).astype(np.float64)
        taint = rolling_sum(consumed_bad, span)
        fired = int(np.sum(ok & np.isfinite(taint) & (taint > 0)))
        taint_counts[name] = {"cells": fired,
                               "valid_cells": int(np.sum(ok & np.isfinite(val)))}

        neutral = neutralize_day(val, ok)
        factors.append(neutral)
        fmasks.append(ok & np.isfinite(neutral))
    return (np.stack(factors, axis=-1), np.stack(fmasks, axis=-1),
            taint_counts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    names = [s[0] for s in FACTOR_SPEC]
    summary = {}
    for arm in ARMS:
        vals, masks, taint = build_arm(panel, arm)
        np.savez_compressed(os.path.join(exp_dir, f"factors_{arm}.npz"),
                            values=vals.astype(np.float32),
                            masks=masks, names=np.array(names))
        total_fired = sum(v["cells"] for v in taint.values())
        summary[arm] = {
            "factor_count": len(names),
            "assertion_fired_cells": total_fired,
            "assertion_valid_cells": sum(v["valid_cells"] for v in taint.values()),
            "per_factor": taint,
            "factor_cell_counts": {n: int(masks[:, :, i].sum())
                                   for i, n in enumerate(names)},
        }
        print(f"{arm}: fired={total_fired} "
              f"valid={summary[arm]['assertion_valid_cells']}")
    json.dump(summary, open(os.path.join(exp_dir, "factor_stats.json"), "w"),
              indent=1)
    print(json.dumps({a: {"fired": summary[a]["assertion_fired_cells"],
                          "valid": summary[a]["assertion_valid_cells"]}
                      for a in ARMS}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
