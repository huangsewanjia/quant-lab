#!/usr/bin/env python3
"""WORK-449 · parity test: numpy factor primitives vs the official torch ones.

The official implementation (initial-d/ml-quant-trading, MIT,
src/mlquant/features/tensor_factors.py) is transcribed here VERBATIM into
torch (float64) and compared cell-by-cell with the numpy port used by
scripts/work449_build_factors.py.

Why this test exists
--------------------
The rolling-window layout is the single most error-prone part of the port:
torch's ``x.unfold(0, w, 1)`` yields [T-w+1, N, w] and the official code
permutes it to [T-w+1, w, N], while numpy's sliding_window_view natively
yields [T-w+1, N, w].  A wrong axis silently produces plausible-looking
numbers, so the port is pinned to the reference implementation here.

Exit code 0 = all primitives agree within tolerance.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import numpy as np
import torch

TOL = 1e-10
# The official ts_rank casts the rank to float32 (``rank = (xw <= last).sum(dim=1).float()``),
# so the reference itself carries ~1e-7 relative rounding. The numpy port keeps
# float64; the parity tolerance for that primitive is therefore 1e-6, which is
# also the tolerance of the preregistration gate (§10.5).
TOL_TS_RANK = 1e-6
HERE = os.path.dirname(os.path.abspath(__file__))


def _load_mod():
    spec = importlib.util.spec_from_file_location(
        "work449_build_factors", os.path.join(HERE, "work449_build_factors.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- official torch
def _unfold_t(x, window):
    return x.unfold(0, window, 1).permute(0, 2, 1)


def _unfold_mask(mask, window):
    return mask.unfold(0, window, 1).permute(0, 2, 1)


def _pad_front(out, window, value=0.0):
    pad = torch.full((window - 1, *out.shape[1:]), value, dtype=out.dtype)
    return torch.cat([pad, out], dim=0)


def ts_mean(x, mask, window):
    xw = _unfold_t(x.masked_fill(~mask, 0.0), window)
    mw = _unfold_mask(mask, window).float()
    n = mw.sum(dim=1).clamp_min(1.0)
    out = xw.sum(dim=1) / n
    out_mask = (mw.sum(dim=1) > 0)
    return _pad_front(out, window), _pad_front(out_mask.bool(), window, 0).bool()


def ts_std(x, mask, window):
    xw = _unfold_t(x.masked_fill(~mask, 0.0), window)
    mw = _unfold_mask(mask, window).float()
    n = mw.sum(dim=1).clamp_min(2.0)
    mean = xw.sum(dim=1, keepdim=True) / n.unsqueeze(1)
    var = (((xw - mean) ** 2) * mw).sum(dim=1) / (n - 1)
    out_mask = (mw.sum(dim=1) >= 2)
    return (_pad_front(var.sqrt(), window),
            _pad_front(out_mask.bool(), window, 0).bool())


def ts_corr(x, y, mask, window):
    xw = _unfold_t(x.masked_fill(~mask, 0.0), window)
    yw = _unfold_t(y.masked_fill(~mask, 0.0), window)
    mw = _unfold_mask(mask, window).float()
    n = mw.sum(dim=1, keepdim=True).clamp_min(2.0)
    mx = (xw * mw).sum(dim=1, keepdim=True) / n
    my = (yw * mw).sum(dim=1, keepdim=True) / n
    cx = (xw - mx) * mw
    cy = (yw - my) * mw
    cov = (cx * cy).sum(dim=1)
    sx = (cx ** 2).sum(dim=1).sqrt()
    sy = (cy ** 2).sum(dim=1).sqrt()
    out = cov / (sx * sy).clamp_min(1e-12)
    out_mask = (mw.sum(dim=1) >= 2)
    return (_pad_front(out, window),
            _pad_front(out_mask.bool(), window, 0).bool())


def ts_rank(x, mask, window):
    xw = _unfold_t(x.masked_fill(~mask, float("-inf")), window)
    last = xw[:, -1:, :]
    rank = (xw <= last).sum(dim=1).float()
    n = _unfold_mask(mask, window).float().sum(dim=1).clamp_min(1.0)
    out = rank / n
    out_mask = _unfold_mask(mask, window).all(dim=1)
    return _pad_front(out, window), _pad_front(out_mask.bool(), window, 0).bool()


def ts_min(x, mask, window):
    xw = _unfold_t(x.masked_fill(~mask, float("inf")), window)
    mw = _unfold_mask(mask, window)
    out = xw.min(dim=1).values
    out_mask = mw.any(dim=1)
    return _pad_front(out, window), _pad_front(out_mask.bool(), window, 0).bool()


def ts_max(x, mask, window):
    xw = _unfold_t(x.masked_fill(~mask, float("-inf")), window)
    mw = _unfold_mask(mask, window)
    out = xw.max(dim=1).values
    out_mask = mw.any(dim=1)
    return _pad_front(out, window), _pad_front(out_mask.bool(), window, 0).bool()


# ---------------------------------------------------------------------- helpers
def compare(name, got_v, got_m, ref_v, ref_m, w, tol=None):
    """Compare masks on every row, values on rows >= w-1 (official pads front)."""
    tol = TOL if tol is None else tol
    got_m = np.asarray(got_m, dtype=bool)
    ref_m = np.asarray(ref_m, dtype=bool)
    mask_mismatch = int((got_m != ref_m).sum())
    rv = np.asarray(ref_v, dtype=np.float64)
    gv = np.asarray(got_v, dtype=np.float64)
    same = ((np.isnan(gv) & np.isnan(rv)) |
            (np.isposinf(gv) & np.isposinf(rv)) |
            (np.isneginf(gv) & np.isneginf(rv)))
    diff = np.where(same, 0.0, np.abs(gv - rv))
    diff = np.where(np.isnan(diff), np.inf, diff)          # NaN vs number
    tail = diff[w - 1:]
    max_abs = float(np.max(tail)) if tail.size else 0.0
    denom = np.maximum(np.abs(rv[w - 1:]), 1e-12)
    finite = np.isfinite(rv[w - 1:])
    max_rel = float(np.max((diff[w - 1:][finite]) / denom[finite])) \
        if finite.any() else 0.0
    ok = (mask_mismatch == 0) and (max_rel <= tol)
    print(f"  {name:14s} mask_mismatch={mask_mismatch:>6d} "
          f"max_abs={max_abs:.3e} max_rel={max_rel:.3e} tol={tol:.0e} "
          f"{'OK' if ok else 'FAIL'}")
    return ok


def main():
    bf = _load_mod()
    rng = np.random.default_rng(20261120)
    T, N = 300, 7
    failures = []

    for trial in range(6):
        x = rng.normal(size=(T, N))
        y = rng.normal(size=(T, N))
        # sprinkle NaN at masked positions (like the panel) and some unmasked
        mask = rng.random((T, N)) > 0.25
        x[rng.random((T, N)) < 0.05] = np.nan
        y[rng.random((T, N)) < 0.05] = np.nan
        xt = torch.tensor(x, dtype=torch.float64)
        yt = torch.tensor(y, dtype=torch.float64)
        mt = torch.tensor(mask)
        print(f"trial {trial}")
        for w in (5, 20, 60):
            if w > T // 2:
                continue
            for fname, ref, npfn in (
                ("ts_mean", ts_mean, bf.ts_mean),
                ("ts_std", ts_std, bf.ts_std),
                ("ts_corr", ts_corr, bf.ts_corr),
                ("ts_rank", ts_rank, bf.ts_rank),
                ("ts_min", ts_min, bf.ts_min),
                ("ts_max", ts_max, bf.ts_max),
            ):
                if fname == "ts_corr":
                    rv, rm = ref(xt, yt, mt, w)
                    gv, gm = npfn(x, y, mask, w)
                else:
                    rv, rm = ref(xt, mt, w)
                    gv, gm = npfn(x, mask, w)
                tol = TOL_TS_RANK if fname == "ts_rank" else TOL
                if not compare(f"{fname} w={w}", gv, gm,
                               rv.numpy(), rm.numpy(), w, tol):
                    failures.append(f"trial{trial} {fname} w={w}")

    print(f"\nTOL={TOL} (ts_rank {TOL_TS_RANK}) failures={len(failures)}")
    for f in failures:
        print("  FAIL", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
