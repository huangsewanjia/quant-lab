#!/usr/bin/env python3
"""WORK-637 / phase 2f (stage B, optional) — return-rank alignment (FT-DINO).

Implements equations (6)-(8) of arXiv:2611.23723v1: within a batch, the soft
target neighbourhood is built from cross-sectional percentile ranks of the
subsequent 5-day return, the model neighbourhood from cosine similarities, and
the loss is KL(p || q). Hyper-parameters are frozen in preregistration §10.

Executed only if stage A (including the independent recompute) is complete and
>= 3 h of the run remain; otherwise the verdict stays stage A and stage B is
reported as 未跑.

    ../.venv/bin/python -u scripts/work637_finetune.py --epochs 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C
import work637_encode as E

KAPPA_R = 0.05
KAPPA_Z = 0.10
LR = 3e-5
WD = 0.01
BATCH = 128
DATES_PER_BATCH = 4
WARMUP_FRAC = 0.05
VAL_SAMPLE = 2000


def rank_labels(panel, elig, lab, weeks):
    """u[i, j] = cross-sectional percentile rank of the realised 5-day return."""
    U = np.full(lab.shape, np.nan)
    for w in range(lab.shape[0]):
        m = elig["chartable"][w] & np.isfinite(lab[w])
        ix = np.nonzero(m)[0]
        if len(ix) < 30:
            continue
        order = np.argsort(lab[w, ix])
        rk = np.empty(len(ix))
        rk[order] = np.arange(len(ix))
        U[w, ix] = rk / max(len(ix) - 1, 1)
    return U


def kl_loss(emb, u, kappa_r, kappa_z):
    z = F.normalize(emb, dim=-1)
    s = z @ z.t()
    eye = torch.eye(len(z), dtype=torch.bool, device=z.device)
    target = torch.exp(-(u[:, None] - u[None, :]).abs() / kappa_r)
    target = target.masked_fill(eye, 0)
    p = target / target.sum(1, keepdim=True).clamp_min(1e-12)
    q = torch.exp(s / kappa_z).masked_fill(eye, 0)
    q = q / q.sum(1, keepdim=True).clamp_min(1e-12)
    return (p * (torch.log(p.clamp_min(1e-12)) - torch.log(q.clamp_min(1e-12)))).sum(1).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--steps", type=int, default=0, help="0 = full epoch")
    args = ap.parse_args()

    torch.set_num_threads(8)
    torch.manual_seed(args.seed)
    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    lab = C.label_matrix(panel, elig)
    weeks = pd.to_datetime(panel["days"][panel["week_idx"]])
    U = rank_labels(panel, elig, lab, weeks)

    idx = pd.read_parquet(os.path.join(C.DATA, "index_seed42.parquet"))
    code_ix = {c: j for j, c in enumerate(panel["codes"])}
    week_map = {d: w for w, d in enumerate(weeks.strftime("%Y-%m-%d"))}
    idx["week"] = idx["date"].map(week_map)
    idx["stock"] = idx["ts_code"].map(code_ix)
    idx["rank"] = U[idx["week"].to_numpy(), idx["stock"].to_numpy()]

    tr = idx[(weeks[idx["week"]].values >= np.datetime64("2014-01-02"))
             & (weeks[idx["week"]].values <= np.datetime64("2018-12-28"))
             & np.isfinite(idx["rank"].to_numpy())].reset_index(drop=True)
    va = idx[(weeks[idx["week"]].values >= np.datetime64("2019-01-02"))
             & (weeks[idx["week"]].values <= np.datetime64("2020-12-31"))
             & np.isfinite(idx["rank"].to_numpy())].reset_index(drop=True)
    print(f"train {len(tr)} charts / {tr['week'].nunique()} weeks; val {len(va)}")

    from transformers import Dinov2Model
    model = Dinov2Model.from_pretrained(C.MODEL_DIR).train()
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
    total = args.steps or (len(tr) // BATCH) * args.epochs
    warm = max(1, int(WARMUP_FRAC * total))

    def lr_at(t):
        if t < warm:
            return LR * (t + 1) / warm
        prog = (t - warm) / max(total - warm, 1)
        return LR * 0.5 * (1 + np.cos(np.pi * prog))

    rng = np.random.default_rng(args.seed)
    val_sample = va.sample(min(VAL_SAMPLE, len(va)), random_state=args.seed)
    ckpt_dir = os.path.join(C.DATA, "ft_ckpt")
    os.makedirs(ckpt_dir, exist_ok=True)

    def val_kl(m):
        m.eval()
        vals = []
        paths = [os.path.join(r.dir, r.file) for r in val_sample.itertuples()]
        uu = torch.tensor(val_sample["rank"].to_numpy(), dtype=torch.float32)
        with torch.inference_mode():
            for s in range(0, len(paths), BATCH):
                xb = E.load_paths(paths[s:s + BATCH], C.IMG_PX)
                h = m(pixel_values=xb).last_hidden_state[:, 0, :]
                if len(h) < 2:
                    continue
                z = F.normalize(h, dim=-1)
                sim = z @ z.t()
                eye = torch.eye(len(z), dtype=torch.bool)
                u = uu[s:s + len(h)]
                tg = torch.exp(-(u[:, None] - u[None, :]).abs() / KAPPA_R).masked_fill(eye, 0)
                p = tg / tg.sum(1, keepdim=True).clamp_min(1e-12)
                q = torch.exp(sim / KAPPA_Z).masked_fill(eye, 0)
                q = q / q.sum(1, keepdim=True).clamp_min(1e-12)
                vals.append(float((p * (torch.log(p.clamp_min(1e-12)) - torch.log(q.clamp_min(1e-12))))
                                  .sum(1).mean()))
        m.train()
        return float(np.mean(vals))

    log, best = [], (1e9, None)
    t0 = time.time()
    for step in range(total):
        for g in opt.param_groups:
            g["lr"] = lr_at(step)
        days = rng.choice(tr["week"].unique(), size=DATES_PER_BATCH, replace=False)
        picks = []
        for d in days:
            pool = tr.index[tr["week"] == d]
            picks.append(rng.choice(pool, size=BATCH // DATES_PER_BATCH, replace=False))
        b = tr.loc[np.concatenate(picks)]
        paths = [os.path.join(r.dir, r.file) for r in b.itertuples()]
        xb = E.load_paths(paths, C.IMG_PX)
        u = torch.tensor(b["rank"].to_numpy(), dtype=torch.float32)
        out = model(pixel_values=xb).last_hidden_state[:, 0, :]
        loss = kl_loss(out, u, KAPPA_R, KAPPA_Z)
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if step % 20 == 0:
            el = time.time() - t0
            print(f"  step {step}/{total} lr={lr_at(step):.2e} loss={float(loss):.4f} "
                  f"{el:.0f}s", flush=True)
        if (step + 1) % max(1, total // 10) == 0 or step == total - 1:
            v = val_kl(model)
            log.append({"step": step, "loss": float(loss), "val_kl": v, "lr": lr_at(step)})
            print(f"  checkpoint step={step} val_kl={v:.4f}", flush=True)
            torch.save(model.state_dict(), os.path.join(ckpt_dir, f"step{step:05d}.pt"))
            if v < best[0]:
                best = (v, step)
    meta = {"seed": args.seed, "steps": total, "batch": BATCH, "dates_per_batch": DATES_PER_BATCH,
            "kappa_r": KAPPA_R, "kappa_z": KAPPA_Z, "lr": LR, "wd": WD,
            "train_charts": int(len(tr)), "val_charts": int(len(va)),
            "val_sample": int(len(val_sample)), "checkpoints": log,
            "best_val_kl": best[0], "best_step": best[1],
            "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(C.EXP, "finetune_log.json"), "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: meta[k] for k in ("best_val_kl", "best_step", "seconds")}, indent=1))


if __name__ == "__main__":
    main()
