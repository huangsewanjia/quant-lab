#!/usr/bin/env python3
"""WORK-503 stage 2 — train one PRISM-VQ ablation arm with one seed.

Reuses the official implementation verbatim:
  vendor/PRISM-VQ/trainer/train_vqvae.py        (FactorVQVAE, stage 1)
  vendor/PRISM-VQ/trainer/train_ypred.py        (GenerateReturn, stage 2 full)
  vendor/PRISM-VQ/trainer/train_ypred_wo_moe.py (GenerateReturn, no_moe)
  vendor/PRISM-VQ/trainer/train_ypred_wo_prior.py (GenerateReturn, no_prior)
  vendor/PRISM-VQ/trainer/train_ypred_wo_stage1.py (GenerateReturn, no_vq)
  vendor/PRISM-VQ/module/**                      (model code)
  vendor/PRISM-VQ/utils/test.py                  (run_inference, RankIC)

Only the data path is ours (preregistration.md §1.1 / CHANGES-vs-upstream.md):
the dataset returns the same (T=20, 158 feature ∥ 6 prior ∥ 10 label) layout
that qlib's TSDataSampler produces, so the official modules are untouched.

Usage:
  python scripts/work503_run_arms.py --arm full --seed 42 --epochs 8
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

import numpy as np
import pandas as pd
import torch

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR = os.path.join(REPO, "vendor", "PRISM-VQ")
sys.path.insert(0, VENDOR)

N_FEATURES = 158
N_PRIOR = 6
N_LABEL = 10
T = 20


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------
class Work503Dataset:
    """qlib-TSDataSampler-compatible: get_index() + __getitem__ -> (T, 174)."""

    def __init__(self, ds_dir, index_df, use_prior=True, cs_label=True,
                 dropna_label=True):
        self.ds_dir = ds_dir
        self.use_prior = use_prior
        self.cs_label = cs_label
        # The per-instrument arrays are indexed by the GLOBAL trading calendar
        # that work503_build_dataset.py wrote next to them.  Building the
        # position map from the *segment* calendar instead would silently read
        # another day's features (preregistration_v1.2.md §B).
        with open(os.path.join(ds_dir, "calendar.txt"), encoding="utf-8") as fh:
            self.cal = pd.DatetimeIndex(
                [pd.Timestamp(line.strip()) for line in fh if line.strip()])
        self.cal_pos = {d: i for i, d in enumerate(self.cal)}
        self._feat, self._prior, self._label = {}, {}, {}

        df = index_df.reset_index()
        df = df.sort_values(["datetime", "instrument"], kind="mergesort")
        rows = list(zip(df["instrument"].to_numpy(), df["datetime"].to_numpy()))
        if dropna_label:
            keep = []
            for i, (inst, dt) in enumerate(rows):
                lab = self._get_label(inst, dt)
                if lab is not None and not np.isnan(lab).any():
                    keep.append(i)
            rows = [rows[i] for i in keep]
            df = df.iloc[keep]
        self.rows = rows
        self.index = df.set_index(["datetime", "instrument"])

    # -- lazy per-instrument arrays --------------------------------------
    def _load(self, inst):
        if inst not in self._feat:
            # memory-mapped: the feature matrices are ~2 MB per instrument and
            # DataLoader workers would otherwise each hold a private copy.
            self._feat[inst] = np.load(os.path.join(
                self.ds_dir, "feat", f"{inst}.npy"), mmap_mode="r")
            self._prior[inst] = np.load(os.path.join(
                self.ds_dir, "prior", f"{inst}.npy"), mmap_mode="r")
            sub = "label_cs" if self.cs_label else "label"
            self._label[inst] = np.load(os.path.join(
                self.ds_dir, sub, f"{inst}.npy"), mmap_mode="r")
        return self._feat[inst], self._prior[inst], self._label[inst]

    def _get_label(self, inst, dt):
        lab = self._label.get(inst)
        if lab is None:
            _, _, lab = self._load(inst)
        t = self.cal_pos.get(dt)
        if t is None:
            return None
        return np.asarray(lab[t])

    def get_index(self):
        # qlib-compatible: a MultiIndex ordered (datetime, instrument) so that
        # the official DailyBatchSamplerRandom groups by date and
        # run_inference's sortlevel(0) keeps the batch order.
        return self.index.index

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        inst, dt = self.rows[i]
        F, P, L = self._load(inst)
        t = self.cal_pos[dt]
        lo = max(0, t - T + 1)
        win = np.asarray(F[lo:t + 1], dtype="float32")
        if win.shape[0] < T:
            pad = np.full((T - win.shape[0], win.shape[1]), np.nan, dtype="float32")
            win = np.concatenate([pad, win], axis=0)
        # qlib fillna_type='ffill+bfill' along the time axis
        win = _ffill_bfill(win)
        out = np.zeros((T, N_FEATURES + N_PRIOR + N_LABEL), dtype="float32")
        out[:, :N_FEATURES] = win
        pr = np.asarray(P[t]) if self.use_prior else np.zeros(N_PRIOR, dtype="float32")
        out[T - 1, N_FEATURES:N_FEATURES + N_PRIOR] = pr
        out[T - 1, N_FEATURES + N_PRIOR:] = np.asarray(L[t])
        return out


def _ffill_bfill(x: np.ndarray) -> np.ndarray:
    x = x.astype("float64")
    # forward fill along axis 0
    mask = np.isnan(x)
    idx = np.where(~mask, np.arange(x.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    x = np.take_along_axis(x, idx, axis=0)
    x[mask] = np.nan
    # backward fill
    mask = np.isnan(x)
    rev = x[::-1]
    m2 = np.isnan(rev)
    idx = np.where(~m2, np.arange(x.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    rev = np.take_along_axis(rev, idx, axis=0)
    rev[m2] = np.nan
    x = rev[::-1]
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return x.astype("float32")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def build_config(arm: str, seed: int, epochs: int, ckpt_name: str) -> dict:
    import yaml
    with open(os.path.join(VENDOR, "configs", "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["vqvae"]["num_prior_factors"] = N_PRIOR
    cfg["vqvae"]["num_features"] = N_FEATURES
    cfg["vqvae"]["seq_len"] = T
    cfg["vqvae"]["predictor"]["pred_len"] = N_LABEL
    cfg["predictor"]["pred_len"] = 20
    cfg["predictor"]["target_day"] = 5
    cfg["predictor"]["num_features"] = N_FEATURES
    cfg["predictor"]["n_expert"] = 2          # CSI 300 setting (paper §5.1.4)
    cfg["predictor"]["k"] = 1                # half of n_expert (top-1)
    cfg["predictor"]["transformer"]["num_heads"] = 2   # CSI 300
    cfg["predictor"]["aux_weight"] = 0.01    # CSI 300
    cfg["predictor"]["aux_imp"] = 3
    cfg["predictor"]["use_prior"] = True
    cfg["predictor"]["saved_model"] = ckpt_name
    cfg["data"]["universe"] = "csi300"
    cfg["data"]["data_path"] = os.path.join(REPO, "data", "WORK-503", "ds")
    cfg["train"]["seed"] = seed
    cfg["train"]["device"] = "cpu"
    cfg["train"]["accelerator"] = "cpu"
    cfg["train"]["gpu_counts"] = 1
    cfg["train"]["num_workers"] = 1
    cfg["train"]["num_epochs"] = epochs
    cfg["train"]["precision"] = 32
    cfg["train"]["gradient_clip_val"] = 1
    cfg["train"]["early_stopping"]["patience"] = 5
    cfg["train"]["early_stopping"]["monitor"] = "val_loss"
    cfg["train"]["save_dir"] = "checkpoints"
    cfg["train"]["save_res"] = "res_work503"
    cfg["train"]["project_name"] = "WORK-503-PRISM-VQ"
    return cfg


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True,
                    choices=["full", "no_vq", "no_moe", "no_prior"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    os.environ.setdefault("WANDB_MODE", "disabled")
    os.environ.setdefault("HYDRA_FULL_ERROR", "1")

    # The upstream trainers log the codebook utilisation with
    # `wandb.log({"Codebook Utilization": wandb.Image(plt)})` at the end of
    # every epoch (trainer/train_vqvae.py:98).  Upstream runs them under a
    # WandbLogger, which calls wandb.init() implicitly; our runner disables
    # the logger, so the initialisation is done here instead (no network:
    # WANDB_MODE=disabled).  Without it the stage-1 run dies at the end of
    # the first epoch with "You must call wandb.init() before log()".
    import wandb
    try:
        wandb.init(mode="disabled", project="WORK-503-PRISM-VQ",
                   dir=os.path.join(REPO, "data", args.exp, "logs"))
    except Exception as exc:                       # pragma: no cover
        print(f"wandb.init failed ({exc}); stubbing wandb.log")
        wandb.log = lambda *a, **k: None
    os.chdir(VENDOR)          # get_root_dir() / checkpoint paths

    exp_dir = os.path.join(REPO, "experiments", args.exp)
    ds_dir = os.path.join(REPO, "data", args.exp, "ds")
    out_dir = os.path.join(exp_dir, "preds")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(VENDOR, "checkpoints"), exist_ok=True)

    from dataset.dataset import init_data_loader
    import pytorch_lightning as pl

    # The seed has to initialise the global RNGs BEFORE the model is built and
    # before the per-day batch order is drawn.  Upstream stage2.py does the
    # same (`seed_everything(cfg.train.seed)` + `pl.seed_everything(...,
    # workers=True)`); without it the "seed" would only label an unseeded
    # random draw and the run could not be reproduced from the seed.
    from utils import seed_everything
    seed_everything(args.seed)
    pl.seed_everything(args.seed, workers=True)

    sample_index = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    sample_index["datetime"] = pd.to_datetime(sample_index["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = sample_index[sample_index["segment"] == seg].copy()
        sub = sub.sort_values(["datetime", "instrument"], kind="mergesort")
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)
        print(f"segment {seg}: {len(sub)} samples")

    use_prior = args.arm != "no_prior"
    train_ds = Work503Dataset(ds_dir, segs["train"], use_prior, cs_label=True)
    valid_ds = Work503Dataset(ds_dir, segs["valid"], use_prior, cs_label=True)
    test_ds = Work503Dataset(ds_dir, segs["test"], use_prior, cs_label=False,
                              dropna_label=False)

    train_loader, n_batches = init_data_loader(train_ds, shuffle=True,
                                                num_workers=args.workers)
    valid_loader, _ = init_data_loader(valid_ds, shuffle=False, num_workers=0)
    test_loader, _ = init_data_loader(test_ds, shuffle=False, num_workers=0)
    print(f"train batches/epoch: {n_batches}")

    # The stage-1 VQ-VAE is identical for the arms that use it (full /
    # no_moe / no_prior all load the same pre-trained stage-1 checkpoint,
    # exactly as the upstream ablation scripts do), so it is trained once
    # per seed and shared.
    ckpt_name = f"stage1_s{args.seed}.pt"
    ckpt_path = os.path.join(VENDOR, "checkpoints", ckpt_name)
    cfg = build_config(args.arm, args.seed, args.epochs, ckpt_name)

    t0 = time.time()
    log = {"arm": args.arm, "seed": args.seed, "epochs": args.epochs,
           "n_train": len(train_ds), "n_valid": len(valid_ds),
           "n_test": len(test_ds), "batches": n_batches}

    # ---- stage 1 -------------------------------------------------------
    if args.arm != "no_vq" and not os.path.exists(ckpt_path):
        from trainer.train_vqvae import FactorVQVAE
        m1 = FactorVQVAE(cfg, T_max=n_batches * args.epochs)
        ck = pl.callbacks.ModelCheckpoint(
            dirpath=os.path.join(VENDOR, "checkpoints"), save_top_k=1,
            monitor="val_loss", mode="min",
            filename=f"stage1_s{args.seed}")
        es = pl.callbacks.EarlyStopping(monitor="val_loss", mode="min",
                                        patience=3, min_delta=1e-5)
        tr = pl.Trainer(max_epochs=args.epochs, accelerator="cpu", devices=1,
                        precision=32, gradient_clip_val=1, logger=False,
                        callbacks=[ck, es], enable_progress_bar=False,
                        enable_model_summary=False, deterministic=True)
        tr.fit(m1, train_dataloaders=train_loader, val_dataloaders=valid_loader)
        best = ck.best_model_path or os.path.join(
            VENDOR, "checkpoints", f"stage1_s{args.seed}.ckpt")
        state = torch.load(best, map_location="cpu")
        torch.save({"state_dict": state["state_dict"] if "state_dict" in state
                    else state}, ckpt_path)
        log["stage1_best"] = str(best)
        log["stage1_seconds"] = round(time.time() - t0, 1)
        print(f"stage1 done in {log['stage1_seconds']}s", flush=True)
    elif args.arm != "no_vq":
        log["stage1_reused"] = ckpt_path
        print(f"stage1 reused: {ckpt_path}")

    # ---- stage 2 -------------------------------------------------------
    if args.arm == "full":
        from trainer.train_ypred import GenerateReturn
    elif args.arm == "no_moe":
        from trainer.train_ypred_wo_moe import GenerateReturn
    elif args.arm == "no_prior":
        from trainer.train_ypred_wo_prior import GenerateReturn
    else:
        from trainer.train_ypred_wo_stage1 import GenerateReturn

    m2 = GenerateReturn(cfg, T_max=n_batches * args.epochs)
    ck2 = pl.callbacks.ModelCheckpoint(
        dirpath=os.path.join(VENDOR, "checkpoints"), save_top_k=1,
        monitor="val_loss", mode="min",
        filename=f"stage2_{args.arm}_s{args.seed}")
    es2 = pl.callbacks.EarlyStopping(monitor="val_loss", mode="min",
                                     patience=3, min_delta=1e-5)
    tr2 = pl.Trainer(max_epochs=args.epochs, accelerator="cpu", devices=1,
                     precision=32, gradient_clip_val=1, logger=False,
                     callbacks=[ck2, es2], enable_progress_bar=False,
                     enable_model_summary=False, deterministic=True)
    tr2.fit(m2, train_dataloaders=train_loader, val_dataloaders=valid_loader)
    best2 = ck2.best_model_path
    if best2:
        m2 = type(m2).load_from_checkpoint(best2, config=cfg,
                                           T_max=n_batches * args.epochs)
    # upstream stage2.py freezes the VQ-VAE before the inference
    if hasattr(m2, "freeze_vqvae"):
        m2.freeze_vqvae()
    m2.eval()
    log["stage2_seconds"] = round(time.time() - t0, 1)

    # ---- inference ------------------------------------------------------
    from utils.test import run_inference
    df, rankic, icir = run_inference(m2, test_loader, cfg)
    df = df.reset_index()
    df.columns = ["datetime", "instrument", "score", "label"]
    out_path = os.path.join(out_dir, f"{args.arm}_s{args.seed}.parquet")
    df.to_parquet(out_path)
    log["metrics"] = {k: float(v) for k, v in icir.items()}
    log["preds"] = out_path
    log["total_seconds"] = round(time.time() - t0, 1)
    json.dump(log, open(os.path.join(out_dir, f"{args.arm}_s{args.seed}.json"),
                        "w"), ensure_ascii=False, indent=1)
    print(json.dumps(log, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
