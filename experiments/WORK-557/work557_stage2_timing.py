"""WORK-557 stage-2 单步/单轮实测（2026-11-28 派单 §4，≤1 h）。

背景：整队报价里 72–83% 是 stage-2 的「×2 结构推断」，而 stage-2 至今 0 次进入。
本脚本只做一件事：让 `GenerateReturn`（MoE + Transformer 预测器）真正跑起来，
量出 s/step、s/epoch、峰值显存，不改 vendor/、不起整队。

上游约束（读代码得到，不是猜的）：
  * `GenerateReturn.__init__` 第 111–113 行会 `load_pretrained_vqvae('checkpoints/<predictor.saved_model>')`
    然后 `freeze_vqvae()` —— 所以 stage-2 必须有一份 stage-1 权重，且路径是 **cwd 相对**；
  * 本脚本把 2026-11-28 核实运行产出的 stage-1 权重复制到 `./checkpoints/stage2_timing_s42.pt`
    （REPO 根下的新目录，**不在 vendor/ 里**），再把它写进 `cfg['predictor']['saved_model']`。

用法：
  .venv\\Scripts\\python.exe -u experiments\\WORK-557\\work557_stage2_timing.py --steps 50
  .venv\\Scripts\\python.exe -u experiments\\WORK-557\\work557_stage2_timing.py --full-epoch
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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
SRC_CKPT = os.path.join(VER, "ckpt", "ver_full50_s42.ckpt")
CKPT_DIR = os.path.join(REPO, "checkpoints")          # cwd 相对目录：GenerateReturn 的硬要求
CKPT_NAME = "stage2_timing_s42.pt"


def prepare_stage1_ckpt():
    os.makedirs(CKPT_DIR, exist_ok=True)
    dst = os.path.join(CKPT_DIR, CKPT_NAME)
    if not os.path.exists(dst):
        ck = torch.load(SRC_CKPT, map_location="cpu")
        sd = ck["state_dict"] if "state_dict" in ck else ck
        torch.save({"state_dict": sd}, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--full-epoch", action="store_true")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    os.environ.setdefault("WANDB_MODE", "disabled")
    os.makedirs(VER, exist_ok=True)
    T0 = time.time()

    def ev(msg):
        print(f"[{time.time()-T0:8.1f}s] {msg}", flush=True)

    import pytorch_lightning as pl
    import wandb

    M.patch_persistent_workers()
    M.patch_sampler_init()
    M.patch_data_loader_collate()
    from dataset.dataset import init_data_loader

    dst = prepare_stage1_ckpt()
    ev(f"stage-1 权重就绪: {dst} ({os.path.getsize(dst)/1e6:.1f} MB)")

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
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True, num_workers=args.workers)
    valid_loader, n_val = init_data_loader(valid_ds, shuffle=False, num_workers=0)
    ev(f"loaders: train {n_batches} batches, valid {n_val} batches")

    try:
        wandb.init(mode="disabled", project="WORK-557-PRISM-VQ",
                   settings=wandb.Settings(silent=True))
    except Exception as exc:  # pragma: no cover
        ev(f"wandb.init failed ({exc}); stubbing wandb.log")
        wandb.log = lambda *a, **k: None

    cfg = M.build_config("full", args.seed, 50, CKPT_NAME, EXP)
    from trainer.train_ypred import GenerateReturn
    m2 = GenerateReturn(cfg, T_max=n_batches * 50)
    n_par = sum(p.numel() for p in m2.parameters() if p.requires_grad)
    ev(f"GenerateReturn built; trainable params = {n_par/1e6:.2f} M")

    limit_train = None if args.full_epoch else args.steps
    limit_val = None if args.full_epoch else 0
    timer = M.EpochTimer()
    tr = pl.Trainer(max_epochs=args.epochs, accelerator="gpu", devices=1, precision=32,
                    gradient_clip_val=1, logger=False, callbacks=[timer],
                    enable_progress_bar=False, enable_model_summary=False,
                    deterministic=True, limit_train_batches=limit_train,
                    limit_val_batches=limit_val)
    ev("fit() start (stage-2)")
    t_fit = time.time()
    tr.fit(m2, train_dataloaders=train_loader, val_dataloaders=valid_loader)
    fit_s = time.time() - t_fit
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    hist = list(timer.history)
    ep = [round(h["seconds"], 1) for h in hist]
    vals = [round(h["val_loss"], 5) for h in hist]

    n_done = limit_train if limit_train else n_batches
    s_step = fit_s / max(1, n_done)
    proj_epoch = s_step * n_batches
    out = {
        "stage": 2,
        "arm": "full",
        "seed": args.seed,
        "workers": args.workers,
        "mode": "full_epoch" if args.full_epoch else f"{args.steps}_steps",
        "steps_done": n_done,
        "n_batches_per_epoch": int(n_batches),
        "n_valid_batches": int(n_val),
        "fit_seconds": round(fit_s, 1),
        "s_per_step": round(s_step, 4),
        "projected_s_per_epoch": round(proj_epoch, 1),
        "projected_s_per_epoch_note": "含 worker spawn 与进程启动的一次性开销摊在本段步骤里"
                                      if not args.full_epoch else "实测整轮（含 valid 段）",
        "epoch_history_seconds": ep,
        "epoch_val_loss": vals,
        "peak_vram_gb": round(peak, 3),
        "trainable_params_m": round(n_par / 1e6, 2),
        "stage1_ckpt_used": dst,
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "note": "stage-2 = GenerateReturn（VQ-VAE 已 freeze，MoE + Transformer 预测器）；"
                "未改 vendor/PRISM-VQ 任何文件；stage-1 权重从 verify/ckpt 复制到 ./checkpoints/。",
    }
    tag = "full_epoch" if args.full_epoch else f"{args.steps}steps"
    p = os.path.join(VER, f"stage2_timing_{tag}.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    ev(json.dumps(out, ensure_ascii=False, indent=1))
    ev(f"-> {p}")


if __name__ == "__main__":
    main()
