#!/usr/bin/env python3
"""WORK-449 · stage 2 step 4 — train the MLP with AdjMSE for one mask arm.

Protocol: experiments/WORK-449/preregistration.md §3 (splits), §7 (model and
training). The three arms differ ONLY in M_factor; hyper-parameters, seed and
split are identical.

Indexing convention
  pred[t, i]      forecast of r_{t+1} made from the factors observed at t
                   (NaN where the arm's sample set does not contain the cell)
  label[t, i]     = r_eff[t+1, i]
  sample[t, i]    = all 12 factors valid at t AND the bidirectional label
                   check m[t] & m[t+1] holds (paper §3.5.1)
  daily IC at t   = corr(pred[t], label[t]) across the valid cross-section
                   (protocol §9.2: corr(yhat_t, r_{t+1}))
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

TRAIN = ("2015-01-01", "2020-12-31")
VAL = ("2021-01-01", "2021-12-31")
TEST = ("2022-01-01", "2024-12-31")
SEED = 42
EPOCHS = 60
BATCH = 4096
LR = 1e-3
WD = 1e-4
HIDDEN = 128
DROPOUT = 0.1
GAMMA = 0.1
ARM_KEY = {"no_mask": "m_no", "halt_only": "m_halt", "full_mask": "m_full"}


def seed_all(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)
    # CPU BLAS/torch oversubscription is catastrophic on this host (8 threads
    # -> 21x slower per step than 3 threads; the same effect was measured for
    # OpenBLAS eigh).  Pin the thread count so that the three arms are
    # numerically comparable and the run is reproducible.
    torch.set_num_threads(int(os.environ.get("WORK449_TORCH_THREADS", "2")))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass          # already initialised; harmless


def adj_mse(pred, target, gamma: float = GAMMA):
    """AdjMSE (paper §3.5.2): wrong-sign errors get (1+gamma)/gamma = 11x weight."""
    sq = (pred - target) ** 2
    same = (torch.sign(pred) * torch.sign(target)) > 0
    w = torch.where(same, torch.full_like(sq, gamma),
                    torch.full_like(sq, 1.0 + gamma))
    return (w * sq).mean()


class MLP(nn.Module):
    """Paper §3.5.3: two hidden layers, 128 units, GELU, dropout 0.1."""

    def __init__(self, d_in: int, hidden: int = HIDDEN, dropout: float = DROPOUT):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_in, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


def avg_rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=np.float64)
    ranks[order] = np.arange(1, len(x) + 1)
    s = x[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def daily_rank_ic(pred_from, label, valid, tidx) -> float:
    vals = []
    for t in tidx:
        ok = valid[t] & np.isfinite(pred_from[t]) & np.isfinite(label[t])
        if ok.sum() < 10:
            continue
        rp = avg_rank(pred_from[t][ok])
        ry = avg_rank(label[t][ok])
        if rp.std() == 0 or ry.std() == 0:
            continue
        vals.append(float(np.corrcoef(rp, ry)[0, 1]))
    return float(np.mean(vals)) if vals else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    ap.add_argument("--arm", required=True,
                    choices=["no_mask", "halt_only", "full_mask"])
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    seed_all(SEED)

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    fac = np.load(os.path.join(exp_dir, f"factors_{args.arm}.npz"),
                  allow_pickle=True)
    dates = panel["dates"]
    values = fac["values"].astype(np.float32)
    fmasks = fac["masks"]
    r_eff = panel["r_eff"]
    m_arm = panel[ARM_KEY[args.arm]]

    T, N, K = values.shape
    all_valid = fmasks.all(axis=2)
    label_ok = m_arm[:-1, :] & m_arm[1:, :]          # bidirectional check
    sample_ok = all_valid[:-1, :] & label_ok          # [T-1, N]
    label = r_eff[1:, :]                              # [T-1, N]
    # full-grid version of the sample set: sample_full[t] = sample_ok[t] for
    # t <= T-2; the last day has no t+1 label and is never a sample.
    sample_full = np.zeros((T, N), dtype=bool)
    sample_full[:-1, :] = sample_ok

    def days(a, b):
        return np.nonzero((dates[:-1] >= np.datetime64(a)) &
                          (dates[:-1] <= np.datetime64(b)))[0]

    tr_days = days(*TRAIN)
    va_days = days(*VAL)
    te_days = days(*TEST)

    # ---- normalisation from the TRAIN segment only ----
    mu = np.zeros(K, dtype=np.float64)
    sd = np.ones(K, dtype=np.float64)
    tr_mask = np.zeros((T - 1, N), dtype=bool)
    tr_mask[tr_days, :] = True
    tr_mask &= sample_ok
    for k in range(K):
        v = values[:-1, :, k][tr_mask]
        v = v[np.isfinite(v)]
        if v.size > 10:
            mu[k] = float(v.mean())
            s = float(v.std())
            sd[k] = s if s > 1e-12 else 1.0
    norm = ((values.astype(np.float64) - mu[None, None, :]) /
            sd[None, None, :]).astype(np.float32)

    idx = np.nonzero(sample_ok)
    x_all = norm[idx[0], idx[1], :]
    y_all = label[idx[0], idx[1]].astype(np.float32)
    is_tr = np.isin(idx[0], tr_days)
    is_va = np.isin(idx[0], va_days)
    is_te = np.isin(idx[0], te_days)
    print(f"{args.arm}: train={int(is_tr.sum())} val={int(is_va.sum())} "
          f"test={int(is_te.sum())}")

    Xtr = torch.from_numpy(x_all[is_tr])
    Ytr = torch.from_numpy(y_all[is_tr])
    model = MLP(K)
    opt = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WD)

    log = {"arm": args.arm, "seed": SEED, "epochs": EPOCHS, "batch": BATCH,
           "lr": LR, "weight_decay": WD, "gamma": GAMMA, "hidden": HIDDEN,
           "dropout": DROPOUT, "n_train": int(is_tr.sum()),
           "n_val": int(is_va.sum()), "n_test": int(is_te.sum()),
           "history": []}
    best = {"rankic": -np.inf, "epoch": -1, "state": None}
    gen = torch.Generator().manual_seed(SEED)
    Xva = torch.from_numpy(x_all[is_va])
    for ep in range(EPOCHS):
        model.train()
        perm = torch.randperm(len(Xtr), generator=gen)
        tot, nb = 0.0, 0
        for i in range(0, len(Xtr), BATCH):
            b = perm[i:i + BATCH]
            opt.zero_grad()
            loss = adj_mse(model(Xtr[b]), Ytr[b])
            loss.backward()
            opt.step()
            tot += float(loss)
            nb += 1
        model.eval()
        with torch.no_grad():
            pv = model(Xva).numpy()
        pgrid = np.full((T - 1, N), np.nan)
        pgrid[idx[0][is_va], idx[1][is_va]] = pv
        ric = daily_rank_ic(pgrid, label, sample_ok, va_days)
        log["history"].append({"epoch": ep, "train_loss": tot / max(nb, 1),
                                "val_rankic": ric})
        if ric == ric and ric > best["rankic"]:
            best = {"rankic": ric, "epoch": ep,
                    "state": {k: v.detach().clone()
                              for k, v in model.state_dict().items()}}
        if ep % 5 == 0 or ep == EPOCHS - 1:
            print(f"  ep{ep:02d} loss={tot / max(nb, 1):.6f} "
                  f"valRankIC={ric:.4f}")

    if best["state"] is None:                     # no usable val signal
        best = {"rankic": None, "epoch": EPOCHS - 1,
                "state": {k: v.detach().clone()
                          for k, v in model.state_dict().items()}}
    model.load_state_dict(best["state"])
    model.eval()
    with torch.no_grad():
        p_all = model(torch.from_numpy(x_all)).numpy()
    pred_from = np.full((T, N), np.nan, dtype=np.float32)
    # the model output for the sample whose factors sit at t0 is the forecast
    # of r_{t0+1}; it is therefore stored at t0 (not t0+1) so that the
    # portfolio formed at t uses the signal available at t.
    pred_from[idx[0], idx[1]] = p_all.astype(np.float32)
    print(f"best epoch {best['epoch']} valRankIC {best['rankic']}")

    np.savez_compressed(os.path.join(exp_dir, f"pred_{args.arm}.npz"),
                        pred=pred_from, sample_ok=sample_full,
                        dates=dates, codes=panel["codes"])
    torch.save({"state_dict": model.state_dict(), "mu": mu, "sd": sd,
                "n_features": K, "best_epoch": best["epoch"],
                "val_rankic": best["rankic"]},
               os.path.join(exp_dir, f"model_{args.arm}.pt"))
    log["best_epoch"] = best["epoch"]
    log["val_rankic"] = best["rankic"]
    log["finished_at"] = datetime.now().astimezone().isoformat()
    json.dump(log, open(os.path.join(exp_dir, f"train_log_{args.arm}.json"), "w"),
              indent=1)
    print(f"saved model_{args.arm}.pt / pred_{args.arm}.npz")


if __name__ == "__main__":
    sys.exit(main())
