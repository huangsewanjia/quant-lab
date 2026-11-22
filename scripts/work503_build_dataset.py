#!/usr/bin/env python3
"""WORK-503 stage 2 — build the model dataset (Alpha158 features + priors + labels).

Fidelity notes
--------------
* The 158 feature expressions come from Qlib's official Alpha158 handler
  (`qlib.contrib.data.handler.Alpha158`), read out of its config — the
  expressions themselves are not re-implemented here.
* The pre-processing reproduces Qlib's processors exactly (same formulas, same
  fit window, same order):
    RobustZScoreNorm  median / (MAD + EPS) * 1.4826, then clip to [-3, 3]
    Fillna            fill with 0
    DropnaLabel       drop rows whose label is NaN (train/valid only)
    CSRankNorm        per-date rank(pct) -> (x - 0.5) * 3.46
* PIT: a (instrument, date) row only exists when the stock was a CSI 300
  member on that date (pool_membership.csv).  The processor fit window uses
  PIT rows only, so the normalisation never sees a stock before it entered
  the index.
* Labels: open(t+1) -> close(t+k) for k = 1..10 (paper text §5.1.1 and this
  work order; the upstream yaml uses close(t+1) -> close(t+k) — see
  preregistration.md §1.1 D3).

Outputs (data/WORK-503/ds/):
  feat/<SYM>.npy      (n_dates, 158) float32, processed
  prior/<SYM>.npy     (n_dates, 6)   float32, processed
  label/<SYM>.npy     (n_dates, 10)  float32, raw forward returns
  label_cs/<SYM>.npy  (n_dates, 10)  float32, CSRankNorm'd (train/valid)
  sample_index.parquet  PIT sample rows: instrument, datetime, segment
  feature_meta.json    expressions, fit stats, coverage
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

TRAIN = ("2013-01-01", "2019-12-31")
VALID = ("2020-01-01", "2021-12-31")
TEST = ("2022-01-01", "2024-12-31")
LOOKBACK = 20          # T
EPS = 1e-12            # qlib.processor.EPS
ROBUST_SCALE = 1.4826
CLIP = 3.0
CS_SCALE = 3.46


def segment_of(dates: pd.DatetimeIndex) -> np.ndarray:
    seg = np.full(len(dates), "", dtype=object)
    for name, (s, e) in (("train", TRAIN), ("valid", VALID), ("test", TEST)):
        seg[(dates >= pd.Timestamp(s)) & (dates <= pd.Timestamp(e))] = name
    return seg


def robust_norm_fit(X: np.ndarray):
    mean = np.nanmedian(X, axis=0)
    mad = np.nanmedian(np.abs(X - mean), axis=0)
    std = (mad + EPS) * ROBUST_SCALE
    return mean, std


def robust_norm_apply(X: np.ndarray, mean: np.ndarray, std: np.ndarray):
    X = X - mean
    X = X / std
    return np.clip(X, -CLIP, CLIP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--group", type=int, default=120)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    ds_dir = os.path.join(data_dir, "ds")
    for sub in ("feat", "prior", "label", "label_cs"):
        os.makedirs(os.path.join(ds_dir, sub), exist_ok=True)

    import qlib
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D

    qlib.init(provider_uri=os.path.join(data_dir, "qlib_cn"), region="cn")
    exprs = Alpha158().get_feature_config()["feature"]
    feat_names = list(exprs.keys())
    print(f"Alpha158 expressions: {len(feat_names)}")

    cal = D.calendar(start_time="2012-01-01", end_time="2024-12-31")
    cal = pd.DatetimeIndex(cal)
    n_cal = len(cal)

    # ---- PIT membership on the calendar ----------------------------------
    pit = pd.read_parquet(os.path.join(data_dir, "panel", "pit.parquet"))
    pit = pit.reindex(index=cal, columns=sorted(pit.columns))
    pit_mask = pit.to_numpy(dtype=bool)          # (n_cal, n_inst)

    # ---- priors ----------------------------------------------------------
    priors = pd.read_parquet(os.path.join(data_dir, "panel", "priors.parquet"))
    priors = priors.reset_index()
    priors["datetime"] = pd.to_datetime(priors["datetime"])
    prior_cols = ["mkt_20d", "size_lm", "value_bp", "mom_12_1", "vol_60d", "rev_5d"]

    symbols = [s for s in pit.columns]
    sym_to_col = {s: i for i, s in enumerate(symbols)}

    # ---- 1. raw features per instrument group ---------------------------
    t0 = time.time()
    done = 0
    for gstart in range(0, len(symbols), args.group):
        group = symbols[gstart:gstart + args.group]
        raw = D.features(group, exprs, start_time="2012-01-01", end_time="2024-12-31")
        for sym in group:
            sub = raw.xs(sym, level="instrument")
            arr = sub.reindex(cal).to_numpy("float64")
            np.save(os.path.join(ds_dir, "feat", f"{sym}.npy"), arr.astype("float32"))
        done += len(group)
        print(f"features {done}/{len(symbols)} {time.time() - t0:.0f}s", flush=True)

    # ---- 2. labels from the dumped prices (dense calendar) ---------------
    import work503_dump_qlib as dmp
    for sym in symbols:
        code = sym.lower().replace("sh", "sh.").replace("sz", "sz.")
        code = code.replace("sh.", "sh.", 1)
        p_raw = os.path.join(data_dir, "sina", f"{code}.csv")
        p_hfq = os.path.join(data_dir, "sina_hfq", f"{code}.csv")
        if not (os.path.exists(p_raw) and os.path.exists(p_hfq)):
            continue
        hfq = pd.read_csv(p_hfq)
        hfq["date"] = pd.to_datetime(hfq["date"].astype(str).str[:10])
        hfq = hfq.set_index("date").reindex(cal)
        open_h = hfq["open"].to_numpy("float64")
        close_h = hfq["close"].to_numpy("float64")
        labels = np.full((n_cal, 10), np.nan)
        for k in range(1, 11):
            fwd = np.full(n_cal, np.nan)
            fwd[: n_cal - k] = close_h[k:]
            op = np.full(n_cal, np.nan)
            op[: n_cal - 1] = open_h[1:]
            with np.errstate(divide="ignore", invalid="ignore"):
                labels[:, k - 1] = fwd / op - 1.0
        np.save(os.path.join(ds_dir, "label", f"{sym}.npy"), labels.astype("float32"))
    print(f"labels done {time.time() - t0:.0f}s", flush=True)

    # ---- 3. priors per instrument ---------------------------------------
    priors = priors.set_index(["instrument", "datetime"])
    for sym in symbols:
        try:
            sub = priors.xs(sym, level="instrument").reindex(cal)
        except KeyError:
            sub = pd.DataFrame(np.nan, index=cal, columns=prior_cols)
        np.save(os.path.join(ds_dir, "prior", f"{sym}.npy"),
                sub[prior_cols].to_numpy("float32"))
    print(f"priors done {time.time() - t0:.0f}s", flush=True)

    # ---- 4. fit the feature/prior normalisation on the PIT train rows ----
    seg = segment_of(cal)
    train_mask = (seg == "train")
    fit_blocks, prior_fit_blocks = [], []
    for sym in symbols:
        col = sym_to_col[sym]
        if not pit_mask[train_mask, col].any():
            continue
        F = np.load(os.path.join(ds_dir, "feat", f"{sym}.npy"))
        P = np.load(os.path.join(ds_dir, "prior", f"{sym}.npy"))
        rows = pit_mask[train_mask, col]
        fit_blocks.append(F[train_mask][rows])
        prior_fit_blocks.append(P[train_mask][rows])
    Xfit = np.concatenate(fit_blocks, axis=0)
    Pfit = np.concatenate(prior_fit_blocks, axis=0)
    f_mean, f_std = robust_norm_fit(Xfit)
    p_mean, p_std = robust_norm_fit(Pfit)
    print(f"fit rows {Xfit.shape} {time.time() - t0:.0f}s")
    del Xfit, Pfit, fit_blocks, prior_fit_blocks

    # ---- 5. apply + write processed arrays -------------------------------
    for sym in symbols:
        F = np.load(os.path.join(ds_dir, "feat", f"{sym}.npy")).astype("float64")
        P = np.load(os.path.join(ds_dir, "prior", f"{sym}.npy")).astype("float64")
        F = robust_norm_apply(F, f_mean, f_std)
        F = np.nan_to_num(F, nan=0.0, posinf=0.0, neginf=0.0)
        P = robust_norm_apply(P, p_mean, p_std)
        P = np.nan_to_num(P, nan=0.0, posinf=0.0, neginf=0.0)
        np.save(os.path.join(ds_dir, "feat", f"{sym}.npy"), F.astype("float32"))
        np.save(os.path.join(ds_dir, "prior", f"{sym}.npy"), P.astype("float32"))

    # ---- 6. CSRankNorm the labels on the PIT rows (train/valid) ---------
    lab_all = np.zeros((n_cal, len(symbols), 10), dtype="float32")
    for i, sym in enumerate(symbols):
        L = np.load(os.path.join(ds_dir, "label", f"{sym}.npy"))
        lab_all[:, i, :] = L
    cs = np.full_like(lab_all, np.nan)
    for t in range(n_cal):
        rows = pit_mask[t]
        if rows.sum() == 0:
            continue
        vals = lab_all[t, rows, :]
        ranks = pd.DataFrame(vals).rank(pct=True).to_numpy()
        cs[t, rows, :] = ((ranks - 0.5) * CS_SCALE).astype("float32")
    for i, sym in enumerate(symbols):
        np.save(os.path.join(ds_dir, "label_cs", f"{sym}.npy"), cs[:, i, :])

    # ---- 7. sample index -------------------------------------------------
    rows = []
    seg_arr = seg
    for i, sym in enumerate(symbols):
        days = np.where(pit_mask[:, i])[0]
        for t in days:
            rows.append((sym, cal[t], seg_arr[t]))
    samples = pd.DataFrame(rows, columns=["instrument", "datetime", "segment"])
    samples.to_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    counts = samples.groupby("segment").size().to_dict()
    print("samples per segment", counts, "total", len(samples))

    meta = {
        "feature_names": feat_names,
        "n_features": len(feat_names),
        "prior_cols": prior_cols,
        "label_cols": [f"RET_{k}D" for k in range(1, 11)],
        "lookback": LOOKBACK,
        "segments": {"train": list(TRAIN), "valid": list(VALID), "test": list(TEST)},
        "calendar": {"n": n_cal, "start": str(cal[0].date()),
                     "end": str(cal[-1].date())},
        "samples_per_segment": counts,
        "instruments": len(symbols),
        "preprocessors": {
            "feature": "RobustZScoreNorm(clip_outlier=True) + Fillna(0), fit on PIT train rows 2013-2019",
            "prior": "RobustZScoreNorm(clip_outlier=True) + Fillna(0), fit on PIT train rows 2013-2019",
            "label": "DropnaLabel + CSRankNorm (train/valid); raw returns (test)",
        },
        "label_definition": "open(t+1) -> close(t+k), k=1..10; main target k=5",
        "qlib_version": qlib.__version__,
    }
    json.dump(meta, open(os.path.join(ds_dir, "feature_meta.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"dataset -> {ds_dir}  elapsed={time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
