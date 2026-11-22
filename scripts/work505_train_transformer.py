#!/usr/bin/env python3
"""WORK-505 · stage 2 step 4 — train the cross-section Transformer with AdjMSE
for one mask arm.

Protocol: experiments/WORK-505/preregistration.md §1 (模型与训练替换条款),
§0 (可比性), §2.1 (两 seed 合并口径).  Everything except the model/training
loop is inherited verbatim from WORK-449:

  * the sample set (sample_ok) is the SAME array the MLP used —
    ``all_valid[:-1] & m_arm[:-1] & m_arm[1:]`` — so the two models are trained
    on exactly the same (t, i) pairs;
  * the factor normalisation (train-segment mean/std) is computed by the SAME
    code path as scripts/work449_train.py, so mu/sd are bit-identical;
  * the AdjMSE loss (gamma = 0.1) is the SAME function;
  * the prediction file ``pred_<arm>_<seed>.npz`` has the SAME fields, shapes
    and dtypes as WORK-449's, so scripts/work449_backtest.py consumes it
    without a single change.

What differs from the MLP: the training unit is a **trading day** (the whole
cross-section of up to 680 stocks is one sequence) instead of a single
stock-day, and the model is a permutation-equivariant Transformer over the
cross-section (no positional embedding).

Indexing convention (identical to WORK-449):
  pred[t, i]   forecast of r_{t+1} made from the factors observed at t
  label[t, i]  = r_eff[t+1, i]
  sample[t, i] all 12 factors valid at t AND the bidirectional label check
                m[t] & m[t+1] holds
  daily IC at t = corr(pred[t], label[t]) across the valid cross-section
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

TRAIN = ("2015-01-01", "2020-12-31")
VAL = ("2021-01-01", "2021-12-31")
TEST = ("2022-01-01", "2024-12-31")
SEED = 42
EPOCHS = 60
# --- training protocol: identical to WORK-449 v1.3 (paper §3.5.2) ----------
BATCH_DAYS = 12          # 12 trading days ~ 8,160 stock-day ~ WORK-449's 8192
LR = 5e-4
WD = 1e-5
GRAD_CLIP = 1.0
# --- architecture: WORK-505 preregistration §1.1 M1-M6 ---------------------
HIDDEN = 128            # M3  d = 128
NHEAD = 4               # M4  4 heads
NLAYERS = 2             # M4  2 encoder layers
FFN = 4 * HIDDEN        # M4  FFN = 512
DROPOUT = 0.1           # M6
GAMMA = 0.1             # AdjMSE gamma (inherited)
ARM_KEY = {"no_mask": "m_no", "halt_only": "m_halt", "full_mask": "m_full"}


def seed_all(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(int(os.environ.get("WORK505_TORCH_THREADS", "2")))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


def adj_mse(pred, target, gamma: float = GAMMA):
    """AdjMSE (paper §3.5.2) — verbatim from scripts/work449_train.py:70-76."""
    sq = (pred - target) ** 2
    same = (torch.sign(pred) * torch.sign(target)) > 0
    w = torch.where(same, torch.full_like(sq, gamma),
                    torch.full_like(sq, 1.0 + gamma))
    return (w * sq).mean()


class EncoderLayer(nn.Module):
    """Pre-LayerNorm encoder block; identical to WORK-500's CrossSectionTransformer."""

    def __init__(self, d: int, nhead: int, ff: int, p: float):
        super().__init__()
        self.attn = nn.MultiheadAttention(d, nhead, dropout=0.0, batch_first=True)
        self.ln1 = nn.LayerNorm(d)
        self.ln2 = nn.LayerNorm(d)
        self.ff = nn.Sequential(nn.Linear(d, ff), nn.GELU(), nn.Linear(ff, d))
        self.do1 = nn.Dropout(p)
        self.do2 = nn.Dropout(p)

    def forward(self, x, key_padding_mask):
        h = self.ln1(x)
        a, _ = self.attn(h, h, h, need_weights=False,
                         key_padding_mask=key_padding_mask)
        x = x + self.do1(a)
        h = self.ln2(x)
        h = self.ff(h)
        return x + self.do2(h)


class CrossSectionTransformer(nn.Module):
    """x: (B, S, F) -> (B, S). Permutation-equivariant over stocks (no
    positional embedding) — the correct inductive bias for a cross-sectional
    model.  WORK-505 preregistration §1.1 M1-M9."""

    def __init__(self, d_in: int, d: int = HIDDEN, nhead: int = NHEAD,
                 nlayers: int = NLAYERS, ff: int = FFN, p: float = DROPOUT):
        super().__init__()
        self.proj = nn.Linear(d_in, d)
        self.ln0 = nn.LayerNorm(d)
        self.do0 = nn.Dropout(p)
        self.layers = nn.ModuleList([EncoderLayer(d, nhead, ff, p)
                                     for _ in range(nlayers)])
        self.head = nn.Linear(d, 1)

    def forward(self, x, key_padding_mask=None):
        h = self.ln0(self.proj(x))
        h = self.do0(h)
        for lyr in self.layers:
            h = lyr(h, key_padding_mask)
        return self.head(h).squeeze(-1)


def avg_rank(x: np.ndarray) -> np.ndarray:
    """Average ranks for ties — verbatim from scripts/work449_train.py:94-108."""
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
    """Daily cross-sectional RankIC — identical semantics to
    scripts/work449_train.py:110-121 (mean over the given days)."""
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
    ap.add_argument("--exp", default="WORK-505")
    ap.add_argument("--arm", required=True,
                    choices=["no_mask", "halt_only", "full_mask"])
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--tag", default="")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    seed_all(args.seed)

    dev = torch.device(args.device if (args.device == "cpu"
                                       or torch.cuda.is_available()) else "cpu")
    if dev.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False

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
    sample_full = np.zeros((T, N), dtype=bool)
    sample_full[:-1, :] = sample_ok

    def days(a, b):
        return np.nonzero((dates[:-1] >= np.datetime64(a)) &
                          (dates[:-1] <= np.datetime64(b)))[0]

    tr_days = days(*TRAIN)
    va_days = days(*VAL)
    te_days = days(*TEST)

    # ---- normalisation from the TRAIN segment only (same code path as MLP) --
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

    # ---- per-day design tensors -------------------------------------------
    # X_day[t] : (N, K) factor matrix at t (NaN where invalid)
    # Y_day[t] : (N,)   label r_{t+1} (NaN where invalid)
    # V_day[t] : (N,)   validity mask of the training sample
    X_day = np.nan_to_num(norm, nan=0.0)
    Y_day = np.full((T - 1, N), np.nan, dtype=np.float32)
    Y_day[:] = label
    V_day = sample_ok

    day_ok = V_day.any(axis=1) & np.isfinite(Y_day).any(axis=1)
    tr_idx = np.array([t for t in tr_days if day_ok[t]], dtype=np.int64)
    va_idx = np.array([t for t in va_days if day_ok[t]], dtype=np.int64)
    te_idx = np.array([t for t in te_days if day_ok[t]], dtype=np.int64)
    print(f"{args.arm}: train_days={len(tr_idx)} val_days={len(va_idx)} "
          f"test_days={len(te_idx)} | stocks={N} features={K}", flush=True)

    model = CrossSectionTransformer(K).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
    gen = torch.Generator().manual_seed(args.seed)

    log = {"arm": args.arm, "seed": args.seed, "epochs": args.epochs,
           "batch_days": BATCH_DAYS, "lr": LR, "weight_decay": WD,
           "grad_clip": GRAD_CLIP, "gamma": GAMMA, "hidden": HIDDEN,
           "nhead": NHEAD, "n_layers": NLAYERS, "ffn": FFN,
           "dropout": DROPOUT, "optimizer": "AdamW",
           "model": "CrossSectionTransformer",
           "device": str(dev), "torch_version": torch.__version__,
           "torch_threads": torch.get_num_threads(),
           "n_params": int(sum(p.numel() for p in model.parameters())),
           "n_train_days": int(len(tr_idx)), "n_val_days": int(len(va_idx)),
           "n_test_days": int(len(te_idx)), "n_stocks": int(N),
           "n_features": int(K), "history": []}

    def make_batch(days_idx):
        xb = torch.from_numpy(X_day[days_idx])                  # (B, N, K)
        yb = torch.from_numpy(np.nan_to_num(Y_day[days_idx],
                                             nan=0.0))         # (B, N)
        vb = torch.from_numpy(V_day[days_idx])                   # (B, N)
        return xb, yb, vb

    best = {"rankic": -np.inf, "epoch": -1, "state": None}
    for ep in range(args.epochs):
        t_ep = time.time()
        model.train()
        perm = torch.randperm(len(tr_idx), generator=gen)
        tot, nb = 0.0, 0
        for i in range(0, len(perm), BATCH_DAYS):
            sel = perm[i:i + BATCH_DAYS]
            days_b = tr_idx[sel.numpy()]
            xb, yb, vb = make_batch(days_b)
            xb = xb.to(dev, non_blocking=True)
            yb = yb.to(dev, non_blocking=True)
            vb = vb.to(dev, non_blocking=True)
            # key_padding_mask: True = ignore.  A valid query attends to every
            # valid stock; invalid stocks are excluded from the keys and their
            # outputs are dropped from the loss (prereg §1.1 M9).
            pred = model(xb, key_padding_mask=~vb)
            same = (torch.sign(pred) * torch.sign(yb)) > 0
            sq = (pred - yb) ** 2
            w = torch.where(same, torch.full_like(sq, GAMMA),
                            torch.full_like(sq, 1.0 + GAMMA))
            sel_mask = vb
            denom = sel_mask.sum().clamp(min=1)
            loss = (w * sq * sel_mask).sum() / denom
            opt.zero_grad(set_to_none=True)
            loss.backward()
            if GRAD_CLIP:
                nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP)
            opt.step()
            tot += float(loss)
            nb += 1
        t_train = time.time() - t_ep

        t_ep = time.time()
        model.eval()
        pgrid = np.full((T - 1, N), np.nan)
        with torch.no_grad():
            for i in range(0, len(va_idx), BATCH_DAYS):
                days_b = va_idx[i:i + BATCH_DAYS]
                xb, _yb, vb = make_batch(days_b)
                pv = model(xb.to(dev), key_padding_mask=~vb.to(dev)).cpu().numpy()
                for j, t in enumerate(days_b):
                    good = vb.numpy()[j]
                    row = np.full(N, np.nan)
                    row[good] = pv[j][good]
                    pgrid[t] = row
        ric = daily_rank_ic(pgrid, label, sample_ok, va_idx)
        t_val = time.time() - t_ep

        log["history"].append({"epoch": ep, "train_loss": tot / max(nb, 1),
                                "val_rankic": ric,
                                "sec_train": round(t_train, 3),
                                "sec_val": round(t_val, 3)})
        if ric == ric and ric > best["rankic"]:
            best = {"rankic": ric, "epoch": ep,
                    "state": {k: v.detach().clone()
                              for k, v in model.state_dict().items()}}
        if ep % 5 == 0 or ep == args.epochs - 1:
            print(f"  ep{ep:02d} loss={tot / max(nb, 1):.6f} "
                  f"valRankIC={ric:.4f} train={t_train:.1f}s val={t_val:.1f}s",
                  flush=True)
        json.dump({"arm": args.arm, "seed": args.seed, "epoch": ep,
                   "epochs": args.epochs, "sec_train": round(t_train, 3),
                   "sec_val": round(t_val, 3), "val_rankic": ric,
                   "best_epoch": best["epoch"], "best_rankic": best["rankic"],
                   "updated_at": datetime.now().astimezone().isoformat()},
                  open(os.path.join(exp_dir,
                                    f"train_progress_{args.arm}{args.tag}.json"), "w"),
                  indent=1)

    if best["state"] is None:
        best = {"rankic": None, "epoch": args.epochs - 1,
                "state": {k: v.detach().clone()
                          for k, v in model.state_dict().items()}}
    model.load_state_dict(best["state"])
    model.eval()

    # ---- full-grid prediction (same layout as WORK-449) -------------------
    pred_from = np.full((T, N), np.nan, dtype=np.float32)
    with torch.no_grad():
        for i in range(0, len(tr_idx), BATCH_DAYS):
            days_b = tr_idx[i:i + BATCH_DAYS]
            xb, _yb, vb = make_batch(days_b)
            pv = model(xb.to(dev), key_padding_mask=~vb.to(dev)).cpu().numpy()
            for j, t in enumerate(days_b):
                good = vb.numpy()[j]
                row = np.full(N, np.nan)
                row[good] = pv[j][good]
                pred_from[t] = row
        for group, idx in (("val", va_idx), ("test", te_idx)):
            for i in range(0, len(idx), BATCH_DAYS):
                days_b = idx[i:i + BATCH_DAYS]
                xb, _yb, vb = make_batch(days_b)
                pv = model(xb.to(dev), key_padding_mask=~vb.to(dev)).cpu().numpy()
                for j, t in enumerate(days_b):
                    good = vb.numpy()[j]
                    row = np.full(N, np.nan)
                    row[good] = pv[j][good]
                    pred_from[t] = row

    print(f"best epoch {best['epoch']} valRankIC {best['rankic']}")

    np.savez_compressed(os.path.join(exp_dir, f"pred_{args.arm}{args.tag}.npz"),
                        pred=pred_from, sample_ok=sample_full,
                        dates=dates, codes=panel["codes"])
    torch.save({"state_dict": model.state_dict(), "mu": mu, "sd": sd,
                "n_features": K, "best_epoch": best["epoch"],
                "val_rankic": best["rankic"],
                "config": {"hidden": HIDDEN, "nhead": NHEAD,
                           "n_layers": NLAYERS, "ffn": FFN,
                           "dropout": DROPOUT}},
               os.path.join(exp_dir, f"model_{args.arm}{args.tag}.pt"))
    log["best_epoch"] = best["epoch"]
    log["val_rankic"] = best["rankic"]
    log["finished_at"] = datetime.now().astimezone().isoformat()
    if dev.type == "cuda":
        log["peak_vram_mb"] = torch.cuda.max_memory_allocated(dev) / 1024 ** 2
        log["peak_vram_reserved_mb"] = torch.cuda.max_memory_reserved(dev) / 1024 ** 2
    json.dump(log, open(os.path.join(exp_dir,
                                     f"train_log_{args.arm}{args.tag}.json"), "w"),
              indent=1)
    print(f"saved model_{args.arm}{args.tag}.pt / pred_{args.arm}{args.tag}.npz")


if __name__ == "__main__":
    sys.exit(main())
