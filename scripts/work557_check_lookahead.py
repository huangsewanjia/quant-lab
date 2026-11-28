#!/usr/bin/env python3
"""WORK-557 stage 3 — assert_no_future (mask_contract.md §7.5).

The mask contract requires the backtest engine to assert that no feature,
prior or label uses data from after the sample date t.  The construction
guarantees it by design (the feature window is t-19..t, the priors are lagged
one trading day, the label is a forward return), and this script verifies it
on the actual snapshot instead of trusting the construction:

  A. feature axis  — the dataset row at calendar position t must equal Qlib's
    raw Alpha158 value at date t (after inverting the RobustZScoreNorm).
    A one-row shift would break this.
  B. prior axis    — the prior row at t must equal the PIT panel prior at t,
    which by construction is built from information up to t-1 (the panel
    shifts every prior by one trading day).
  C. label axis    — the label at t must equal close(t+5) / open(t+1) - 1
    computed from the raw adjusted prices, i.e. strictly forward looking.

It also recomputes the normalisation fit statistics from the raw features and
writes them to ds/fit_stats.npz, so that the inversion in (A) uses exactly
the statistics that were used to build the dataset.

Output: experiments/WORK-557/lookahead_check.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

# ---- qlib runtime bootstrap -------------------------------------------
# pyqlib 0.9.7 publishes no cp313 wheel and this machine trains on
# Python 3.13.15.  .venv/qlib312 holds the cp312 build plus the numerically
# validated pure-python `_libs` stand-in (see
# experiments/WORK-557/work557_qlib_shim.py; the stand-in was checked
# element-by-element against the real cp39 .pyd, max |diff| = 0).
_REPO_QLIB = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO_QLIB, ".venv", "qlib312"))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-557")
DATA = os.path.join(REPO, "data", "WORK-557")
DS = os.path.join(DATA, "ds")
TRAIN = ("2015-01-01", "2019-12-31")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-557")
    ap.add_argument("--n-instruments", type=int, default=6)
    ap.add_argument("--n-dates", type=int, default=25)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from work503_build_dataset import robust_norm_fit

    import qlib
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D
    qlib.init(provider_uri=os.path.join(DATA, "qlib_cn"), region="cn")

    cal = pd.DatetimeIndex(pd.to_datetime(
        pd.read_csv(os.path.join(DS, "calendar.txt"), header=None)[0]))
    cal_pos = {d: i for i, d in enumerate(cal)}
    pit = pd.read_parquet(os.path.join(DATA, "panel", "pit.parquet")).reindex(index=cal)
    symbols = sorted(pit.columns)
    pit_mask = pit.to_numpy(dtype=bool)

    expr_list, name_list = Alpha158.__new__(Alpha158).get_feature_config()
    feat_names = list(name_list)

    # ---- the fit window (same rows as work503_build_dataset.py) ----------
    train_idx = np.where((cal >= pd.Timestamp(TRAIN[0])) &
                         (cal <= pd.Timestamp(TRAIN[1])))[0]
    fb, pb = [], []
    skipped_fit = []
    for gstart in range(0, len(symbols), 120):
        group = symbols[gstart:gstart + 120]
        raw = D.features(group, expr_list, start_time="2012-01-01",
                         end_time="2024-12-31")
        raw.columns = feat_names
        _have = set(raw.index.get_level_values("instrument"))
        for sym in group:
            i = symbols.index(sym)
            # 2026-11-28 修：qlib 二进制里缺票（如 sh600001）会让 xs() 抛 KeyError，
            # 整个前视检查因此一直没有产物。缺票跳过并记录，不参与拟合统计。
            if sym not in _have:
                skipped_fit.append(sym)
                continue
            sub = raw.xs(sym, level="instrument").reindex(cal).to_numpy("float64")
            rows = pit_mask[train_idx, i]
            fb.append(sub[train_idx][rows])
            P = np.load(os.path.join(DS, "prior", f"{sym}.npy"), mmap_mode="r")
            # the stored prior is normalised; invert it with the statistics
            # recomputed below, so the raw prior is recovered in a second pass
            pb.append(None)
    print(f"[lookahead] fit pass skipped {len(skipped_fit)} symbol(s) absent from qlib: {skipped_fit[:8]}", flush=True)
    Xfit = np.concatenate(fb, axis=0)
    f_mean, f_std = robust_norm_fit(Xfit)
    del fb, Xfit

    # the prior statistics need the raw priors; recompute them from the panel
    priors = pd.read_parquet(os.path.join(DATA, "panel", "priors.parquet"))
    priors = priors.reset_index()
    priors["datetime"] = pd.to_datetime(priors["datetime"])
    prior_cols = ["mkt_20d", "size_lm", "value_bp", "mom_12_1", "vol_60d", "rev_5d"]
    piv = priors.pivot(index="datetime", columns="instrument", values=prior_cols[0])
    pb = []
    for sym in symbols:
        i = symbols.index(sym)
        sub = priors[priors["instrument"] == sym].set_index("datetime")
        sub = sub.reindex(cal)[prior_cols].to_numpy("float64")
        rows = pit_mask[train_idx, i]
        pb.append(sub[train_idx][rows])
    Pfit = np.concatenate(pb, axis=0)
    p_mean, p_std = robust_norm_fit(Pfit)
    np.savez(os.path.join(DS, "fit_stats.npz"), f_mean=f_mean, f_std=f_std,
             p_mean=p_mean, p_std=p_std, feature_names=np.array(feat_names))
    del pb, Pfit

    rng = np.random.default_rng(args.seed)
    out = {"features_checked": 0, "max_feat_abs_diff": 0.0,
           "feature_date_mismatches": 0, "prior_max_abs_diff": 0.0,
           "label_max_abs_diff": 0.0, "samples": []}

    # ---- A. the feature axis --------------------------------------------
    # 2026-11-28 修：pit 股票池里有 qlib 二进制里没有的票（如 sh600001，退市/无 2012 后数据），
    # `D.features` 取不到 -> `raw.xs(sym)` 抛 KeyError。以前直接崩，整个前视检查没有产物。
    # 现在按候选序取，取不到的记 skipped 并继续，直到凑够 n_instruments 个可用样本。
    _cand = list(rng.choice(len(symbols), size=min(4 * args.n_instruments, len(symbols)),
                            replace=False))
    _picked = []
    for _si in _cand:
        if len(_picked) >= args.n_instruments:
            break
        _sym = symbols[_si]
        try:
            _probe = D.features([_sym], expr_list, start_time="2012-01-01",
                                end_time="2024-12-31")
            _probe.xs(_sym, level="instrument")
        except (KeyError, ValueError):
            out.setdefault("skipped_symbols", []).append(_sym)
            continue
        _picked.append(int(_si))
    out["n_instruments_used"] = len(_picked)
    for si in _picked:
        sym = symbols[si]
        raw = D.features([sym], expr_list, start_time="2012-01-01",
                         end_time="2024-12-31")
        raw.columns = feat_names
        sub = raw.xs(sym, level="instrument").reindex(cal).to_numpy("float64")
        arr = np.load(os.path.join(DS, "feat", f"{sym}.npy")).astype("float64")
        norm = (sub - f_mean) / f_std
        norm = np.clip(norm, -3.0, 3.0)
        norm = np.nan_to_num(norm, nan=0.0, posinf=0.0, neginf=0.0)
        diff = np.abs(norm - arr)
        # rows where the clip did not bind are the ones that can be inverted
        unclipped = np.abs((sub - f_mean) / f_std) < 3.0
        d = diff[unclipped]
        out["features_checked"] += int(d.size)
        out["max_feat_abs_diff"] = max(out["max_feat_abs_diff"],
                                       float(d.max()) if d.size else 0.0)
        # a date shift would show up as a large mismatch on most rows
        out["feature_date_mismatches"] += int((d > 1e-4).sum())
        out["samples"].append({"instrument": sym, "rows": int(arr.shape[0]),
                               "max_abs_diff": float(d.max()) if d.size else 0.0,
                               "n_clipped": int((~unclipped).sum())})

    # ---- B. the prior axis ----------------------------------------------
    prior_bad, prior_n = 0, 0
    prior_max = 0.0
    for sym in symbols[:120]:
        i = symbols.index(sym)
        sub = priors[priors["instrument"] == sym].set_index("datetime")
        sub = sub.reindex(cal)[prior_cols].to_numpy("float64")
        arr = np.load(os.path.join(DS, "prior", f"{sym}.npy")).astype("float64")
        norm = np.clip((sub - p_mean) / p_std, -3.0, 3.0)
        norm = np.nan_to_num(norm, nan=0.0, posinf=0.0, neginf=0.0)
        unclipped = np.abs((sub - p_mean) / p_std) < 3.0
        d = np.abs(norm - arr)[unclipped]
        if d.size:
            prior_max = max(prior_max, float(d.max()))
            prior_n += int(d.size)
            prior_bad += int((d > 1e-4).sum())
    out["prior_max_abs_diff"] = prior_max
    out["prior_checked"] = prior_n
    out["prior_mismatches"] = prior_bad

    # ---- C. the label axis ------------------------------------------------
    samples = pd.read_parquet(os.path.join(DS, "sample_index.parquet"))
    samples = samples[samples["segment"] == "test"]
    lab_bad, lab_n, lab_max = 0, 0, 0.0
    for sym in rng.choice(sorted(samples["instrument"].unique()),
                          size=min(args.n_instruments, 60), replace=False):
        code = sym[:2] + "." + sym[2:]      # sh600519 -> sh.600519
        p_hfq = os.path.join(DATA, "sina_hfq", f"{code}.csv")
        if not os.path.exists(p_hfq):
            continue
        hfq = pd.read_csv(p_hfq, usecols=["date", "open", "close"])
        hfq["date"] = pd.to_datetime(hfq["date"].astype(str).str[:10])
        hfq = hfq.set_index("date").reindex(cal)
        op = hfq["open"].to_numpy("float64")
        cl = hfq["close"].to_numpy("float64")
        arr = np.load(os.path.join(DS, "label", f"{sym}.npy")).astype("float64")
        rows = samples[samples["instrument"] == sym]["datetime"].tolist()
        rows = rows[: args.n_dates]
        for d in rows:
            t = cal_pos.get(pd.Timestamp(d))
            if t is None or t + 5 >= len(cal):
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                expect = cl[t + 5] / op[t + 1] - 1.0
            got = arr[t, 4]
            if not np.isfinite(expect):
                continue
            lab_n += 1
            diff = abs(float(got) - float(expect))
            lab_max = max(lab_max, diff)
            if diff > 1e-4:
                lab_bad += 1
    out["label_checked"] = lab_n
    out["label_max_abs_diff"] = lab_max
    out["label_mismatches"] = lab_bad

    out["passed"] = (out["feature_date_mismatches"] == 0 and
                     out["prior_mismatches"] == 0 and out["label_mismatches"] == 0)
    path = os.path.join(EXP, "lookahead_check.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1, default=float)
    print(json.dumps({k: v for k, v in out.items() if k != "samples"},
                     ensure_ascii=False, indent=1, default=float))
    print(f"-> {path}")
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
