"""WORK-557 最小核实运行：把「50 轮 ≈ 1.89 h」的外推换成实测，并核实早停轮次。

派单（2026-11-28）要求：
  * 只跑一档：full / seed 42，workers=2，早停保留（patience=15, min_delta=1e-5, monitor=val_loss），上限 50 轮；
  * 若早停 ≤25 轮就停 → 同一 seed/同一数据、关掉早停再跑满 50 轮，拿「满 50 轮真实墙钟」；
  * 两段合计 ≤3 h；不改模型、不改 vendor、不降窗、不降轮数。

实现要点：
  * 与 `scripts/work557_run_arms.py` 的 stage-1 逐项相同（同 dataset/loader/FactorVQVAE/Trainer 参数）；
  * checkpoint 落 `experiments/WORK-557/verify/ckpt/`，**不碰** `vendor/PRISM-VQ/checkpoints/stage1_s42.pt`；
  * 每轮都写 `last.ckpt`（`save_last=True`），中断后 `--resume` 可从 last.ckpt 续跑；
  * `wandb.init(mode="disabled")`：上游每轮末 `wandb.log(wandb.Image(plt))`，不 init 会联网挂住；
  * 全程计时：每轮墙钟、valid loss、峰值显存、总墙钟，落 JSON + 人类可读 txt。

用法（schtasks 起，或前台）：
  .venv\\Scripts\\python.exe -u experiments\\WORK-557\\work557_verify_run.py --phase early
  .venv\\Scripts\\python.exe -u experiments\\WORK-557\\work557_verify_run.py --phase full50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import pandas as pd
import torch

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "vendor", "PRISM-VQ"))

import work557_run_arms as M  # noqa: E402

EXP = "WORK-557"
VER = os.path.join(REPO, "experiments", EXP, "verify")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["early", "full50"], default="early")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--arm", default="full")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-epochs", type=int, default=50)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--time-budget-s", type=float, default=9000.0,
                    help="本阶段的硬墙钟上限（默认 2.5 h），到点由 EarlyStopping 外的 watchdog 记录")
    args = ap.parse_args()

    os.environ.setdefault("WANDB_MODE", "disabled")
    os.makedirs(os.path.join(VER, "ckpt"), exist_ok=True)
    tag = f"{args.phase}_s{args.seed}"
    out_json = os.path.join(VER, f"verify_{tag}.json")
    out_txt = os.path.join(VER, f"verify_{tag}.log")
    T0 = time.time()

    def ev(tag_, fh=None):
        line = f"[{time.time()-T0:8.1f}s] {tag_}"
        print(line, flush=True)
        if fh:
            fh.write(line + "\n")
            fh.flush()

    fh = open(out_txt, "a", encoding="utf-8")
    ev(f"=== phase={args.phase} seed={args.seed} workers={args.workers} max_epochs={args.max_epochs} resume={args.resume} ===", fh)

    import pytorch_lightning as pl
    import wandb

    M.patch_persistent_workers()
    M.patch_sampler_init()
    M.patch_data_loader_collate()
    from dataset.dataset import init_data_loader

    ds_dir = os.path.join(REPO, "data", EXP, "ds")
    si = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    si["datetime"] = pd.to_datetime(si["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = si[si["segment"] == seg].sort_values(
            ["datetime", "instrument"], kind="mergesort")
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)

    torch.set_num_threads(8)
    from utils import seed_everything
    seed_everything(args.seed)
    pl.seed_everything(args.seed, workers=True)

    train_ds = M.Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    valid_ds = M.Work503Dataset(ds_dir, segs["valid"], True, cs_label=True)
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True,
                                               num_workers=args.workers)
    valid_loader, n_val = init_data_loader(valid_ds, shuffle=False, num_workers=0)
    ev(f"loaders: train {n_batches} batches/epoch, valid {n_val} batches", fh)

    try:
        wandb.init(mode="disabled", project="WORK-557-PRISM-VQ",
                   settings=wandb.Settings(silent=True))
    except Exception as exc:  # pragma: no cover
        ev(f"wandb.init failed ({exc}); stubbing wandb.log", fh)
        wandb.log = lambda *a, **k: None

    cfg = M.build_config(args.arm, args.seed, args.max_epochs, f"verify_{tag}.pt", EXP)
    from trainer.train_vqvae import FactorVQVAE
    model = FactorVQVAE(cfg, T_max=n_batches * args.max_epochs)

    ckdir = os.path.join(VER, "ckpt")
    ck = pl.callbacks.ModelCheckpoint(
        dirpath=ckdir, save_top_k=1, save_last=True, every_n_epochs=1,
        monitor="val_loss", mode="min", filename=f"ver_{tag}")
    callbacks = [ck, M.EpochTimer()]
    es = None
    if args.phase == "early":
        es = pl.callbacks.EarlyStopping(monitor="val_loss", mode="min",
                                        patience=15, min_delta=1e-5)
        callbacks.insert(1, es)

    tr = pl.Trainer(max_epochs=args.max_epochs, accelerator="gpu", devices=1,
                    precision=32, gradient_clip_val=1, logger=False,
                    callbacks=callbacks, enable_progress_bar=False,
                    enable_model_summary=False, deterministic=True)
    timer = callbacks[-1]
    ckpt = os.path.join(ckdir, "last.ckpt")
    resume_path = ckpt if (args.resume and os.path.exists(ckpt)) else None
    ev(f"fit() start; resume={'yes ' + ckpt if resume_path else 'no'}", fh)

    t_fit = time.time()
    tr.fit(model, train_dataloaders=train_loader, val_dataloaders=valid_loader,
           ckpt_path=resume_path)
    fit_s = time.time() - t_fit
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3

    hist = list(timer.history)
    ep = [round(h["seconds"], 1) for h in hist]
    vals = [round(h["val_loss"], 4) for h in hist]
    stopped_epoch = int(es.stopped_epoch) + 1 if (es and getattr(es, "stopped_epoch", 0)) else None
    out = {
        "phase": args.phase,
        "arm": args.arm,
        "seed": args.seed,
        "workers": args.workers,
        "max_epochs": args.max_epochs,
        "n_epochs_ran": len(ep),
        "resumed_from": resume_path,
        "n_batches_per_epoch": int(n_batches),
        "n_valid_batches": int(n_val),
        "fit_seconds": round(fit_s, 1),
        "epoch_seconds": ep,
        "val_loss": vals,
        "min_val_loss": min(vals) if vals else None,
        "min_val_loss_epoch": (vals.index(min(vals)) if vals else None),
        "early_stopping_fired_epoch": stopped_epoch,
        "median_steady_s_per_epoch": round(sorted(ep[1:])[len(ep[1:]) // 2], 1) if len(ep) > 1 else None,
        "mean_s_per_epoch": round(sum(ep) / len(ep), 1) if ep else None,
        "peak_vram_gb": round(peak, 3),
        "total_wall_s": round(time.time() - T0, 1),
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "wandb": "disabled",
        "note": "stage-1 (VQ-VAE) 单档核实运行；checkpoint 落 experiments/WORK-557/verify/ckpt/，"
                "不动 vendor/PRISM-VQ/checkpoints/stage1_s42.pt。",
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    ev(f"fit done: {len(ep)} epochs, {fit_s:.1f}s, early_stop={stopped_epoch}, peak={peak:.3f} GB", fh)
    ev("epoch_seconds=" + json.dumps(ep), fh)
    ev("val_loss=" + json.dumps(vals), fh)
    ev(f"-> {out_json}", fh)
    fh.close()
    print(json.dumps({k: v for k, v in out.items() if k not in ("epoch_seconds", "val_loss")},
                     ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
