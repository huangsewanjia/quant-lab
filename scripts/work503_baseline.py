#!/usr/bin/env python3
"""WORK-503 stage 2 — GRU baseline (and a linear baseline) on the same data.

The baseline model class is Qlib's own GRU implementation
(`qlib.contrib.model.pytorch_gru_ts.GRU`) — the same class the paper's
baseline table is built on.  Only the training loop is ours (the paper does
not release the baseline training code); it uses exactly the protocol fixed
in preregistration_v1.1.md: same features, same PIT universe, same splits,
same seeds, same batch-per-day layout, same loss (MSE on the CSRankNorm'd
5-day forward return).

Outputs the same parquet shape as the PRISM-VQ arms so that the backtest
and the H2 bootstrap can consume them directly.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))

N_FEATURES = 158
N_PRIOR = 6
N_LABEL = 10
T = 20
TARGET = 4          # RET_5D (0-based)


class LinearHead(nn.Module):
    def __init__(self, d_in):
        super().__init__()
        self.fc = nn.Linear(d_in, 1)

    def forward(self, x):          # (B, T, F) -> (B, 1)
        return self.fc(x[:, -1, :])


def build_model(kind: str):
    if kind == "gru":
        from qlib.contrib.model.pytorch_gru_ts import GRU
        return GRU(d_feat=N_FEATURES, d_model=64, n_layers=1, dropout=0.1)
    return LinearHead(N_FEATURES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="gru", choices=["gru", "linear"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)

    from work503_run_arms import Work503Dataset, _ffill_bfill

    exp_dir = os.path.join(REPO, "experiments", args.exp)
    ds_dir = os.path.join(REPO, "data", args.exp, "ds")
    out_dir = os.path.join(exp_dir, "preds")
    os.makedirs(out_dir, exist_ok=True)

    sample_index = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    sample_index["datetime"] = pd.to_datetime(sample_index["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = sample_index[sample_index["segment"] == seg].copy()
        sub = sub.sort_values(["datetime", "instrument"], kind="mergesort")
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)

    train_ds = Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    valid_ds = Work503Dataset(ds_dir, segs["valid"], True, cs_label=True)
    test_ds = Work503Dataset(ds_dir, segs["test"], True, cs_label=False,
                              dropna_label=False)

    model = build_model(args.kind)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=max(1, args.epochs))

    def batches(ds, shuffle):
        idx = ds.index.reset_index()
        idx["date"] = idx["datetime"]
        groups = list(idx.groupby("date", sort=True).groups.values())
        if shuffle:
            rng = np.random.default_rng(args.seed)
            rng.shuffle(groups)
        for g in groups:
            rows = [i for i in g]
            batch = np.stack([ds.rows[i] for i in rows])
            yield torch.from_numpy(batch), rows

    best_val, best_state = np.inf, None
    t0 = time.time()
    for ep in range(args.epochs):
        model.train()
        tot, nb = 0.0, 0
        for batch, _ in batches(train_ds, True):
            x = batch[:, :, :N_FEATURES].float()
            y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()
            pred = model(x).squeeze(-1)
            loss = nn.functional.mse_loss(pred, y)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss)
            nb += 1
        sched.step()
        model.eval()
        vs, vn = 0.0, 0
        with torch.no_grad():
            for batch, _ in batches(valid_ds, False):
                x = batch[:, :, :N_FEATURES].float()
                y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float()
                pred = model(x).squeeze(-1)
                vs += float(nn.functional.mse_loss(pred, y))
                vn += 1
        vloss = vs / max(1, vn)
        print(f"epoch {ep}: train {tot / max(1, nb):.4f} valid {vloss:.4f} "
              f"({time.time() - t0:.0f}s)", flush=True)
        if vloss < best_val:
            best_val = vloss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    # ---- inference on the test set --------------------------------------
    model.eval()
    rows, scores = [], []
    with torch.no_grad():
        for batch, idx in batches(test_ds, False):
            x = batch[:, :, :N_FEATURES].float()
            pred = model(x).squeeze(-1).numpy()
            scores.append(pred)
            rows.extend(idx)
    df = pd.DataFrame({"instrument": [test_ds.rows[i][0] for i in rows],
                       "datetime": [test_ds.rows[i][1] for i in rows],
                       "score": np.concatenate(scores)})
    label = np.stack([test_ds._load(r[0])[2][test_ds.cal_pos[r[1]]]
                       for r in rows])
    df["label"] = label[:, TARGET]
    df = df.sort_values(["datetime", "instrument"])
    out = os.path.join(out_dir, f"{args.kind}_s{args.seed}.parquet")
    df.to_parquet(out)

    # RankIC on the test set (paper appendix C)
    from scipy.stats import spearmanr
    ric = []
    for d, g in df.dropna(subset=["label"]).groupby("datetime"):
        if len(g) < 3:
            continue
        ric.append(spearmanr(g["label"], g["score"]).statistic)
    ric = np.asarray(ric, dtype="float64")
    metrics = {"rankic": float(np.nanmean(ric)),
               "rankicir": float(np.nanmean(ric) / np.nanstd(ric))}
    log = {"kind": args.kind, "seed": args.seed, "epochs": args.epochs,
           "valid_loss": best_val, "metrics": metrics, "preds": out,
           "seconds": round(time.time() - t0, 1)}
    json.dump(log, open(os.path.join(out_dir,
                                     f"{args.kind}_s{args.seed}.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(log, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
