"""WORK-557 段2 真实循环标定：走 PL Trainer（与 work557_run_arms.py 完全相同的
model / Trainer / callback 设定），只把 `limit_train_batches` 卡到 N 步。

为什么不能只用 work557_throughput_bench.py 的微基准：微基准绕过了 Lightning 的
training_step —— 上游每一步还要 `self.log(..., on_step=True)` 四次、
`epoch_vq_codes.append(encoding_indices.cpu().numpy())` 一次 CPU 同步，以及
`deterministic=True`。2026-11-26 15:42 的 1-epoch 真实跑在 2400 s 内**没有跑完第 1 轮**
（2675 batch/epoch），说明真实循环远比微基准的 263 ms/batch 慢。这里直接量真实循环。

输出：s/step、s/epoch(×2675)、stage-1 跑满 50 轮的小时数、峰值显存。
"""
from __future__ import annotations

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
SEED = 42


def main():
    n_steps = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8

    os.makedirs(os.path.join(REPO, "vendor", "PRISM-VQ", "checkpoints"), exist_ok=True)
    import pytorch_lightning as pl

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
    seed_everything(SEED)
    pl.seed_everything(SEED, workers=True)

    train_ds = M.Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    valid_ds = M.Work503Dataset(ds_dir, segs["valid"], True, cs_label=True)
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True, num_workers=workers)
    valid_loader, _ = init_data_loader(valid_ds, shuffle=False, num_workers=0)

    cfg = M.build_config("full", SEED, 50, "stage1_bench.pt", EXP)
    from trainer.train_vqvae import FactorVQVAE
    model = FactorVQVAE(cfg, T_max=n_batches * 50)

    timer = M.EpochTimer()
    tr = pl.Trainer(max_epochs=1, accelerator="gpu", devices=1, precision=32,
                    gradient_clip_val=1, logger=False, callbacks=[timer],
                    enable_progress_bar=False, enable_model_summary=False,
                    deterministic=True, limit_train_batches=n_steps,
                    limit_val_batches=0)
    t0 = time.time()
    tr.fit(model, train_dataloaders=train_loader, val_dataloaders=valid_loader)
    torch.cuda.synchronize()
    dt = time.time() - t0
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    s_step = dt / n_steps
    s_epoch = s_step * n_batches
    out = {
        "n_steps_timed": n_steps,
        "workers": workers,
        "wall_seconds": round(dt, 1),
        "s_per_step": round(s_step, 3),
        "n_batches_per_epoch": int(n_batches),
        "s_per_epoch": round(s_epoch, 1),
        "stage1_50_epochs_hours": round(s_epoch * 50 / 3600, 2),
        "peak_vram_gb": round(peak, 3),
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "note": "真实 PL 循环（含 upstream 的 self.log×4/step 与 epoch_vq_codes CPU 同步、"
                "deterministic=True）；valid 段被 limit_val_batches=0 关掉，故真实 s/epoch "
                "还要再加一次 valid 前向（valid 145,800 样本 ≈ 0.18×train 步数）。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    p = os.path.join(REPO, "experiments", EXP, "throughput_bench_pl.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("->", p, flush=True)


if __name__ == "__main__":
    main()
