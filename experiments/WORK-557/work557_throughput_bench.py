"""WORK-557 吞吐标定：把「单档 50 轮要多久」从猜测变成实测。

对齐 GRU 的吞吐（73 s/epoch）用的是同一套计时口径：真实 train_loader 出 batch，
真实 FactorVQVAE 前向+反向+优化器步。分两段计时，回答「慢在数据还是慢在模型」：

  A. 纯取数：只 iter(loader)，取 N 个 batch，不碰 GPU
  B. 纯模型：取到的 batch 做 forward/backward/step（含 H2D 拷贝）

输出 s/batch，再乘 train batches/epoch（2675）得到 s/epoch 与 50 轮总时长。
N 取 20，够稳定又不烧时间（若单 batch 真到 ~1 s，20 个约 20 s）。
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
import torch

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "vendor", "PRISM-VQ"))

import work557_run_arms as M  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
SEED = 42
EXP = "WORK-557"

def main():
    ds_dir = os.path.join(REPO, "data", EXP, "ds")
    os.makedirs(os.path.join(REPO, "vendor", "PRISM-VQ", "checkpoints"), exist_ok=True)

    import pytorch_lightning as pl  # noqa: E402
    M.patch_persistent_workers()
    M.patch_sampler_init()
    M.patch_data_loader_collate()
    from dataset.dataset import init_data_loader  # noqa: E402

    si = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    si["datetime"] = pd.to_datetime(si["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = si[si["segment"] == seg].sort_values(["datetime", "instrument"], kind="mergesort")
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)
    train_ds = M.Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True, num_workers=WORKERS)
    print("batches/epoch:", n_batches, "workers:", WORKERS, flush=True)

    torch.set_num_threads(8)
    dev = "cuda"
    cfg = M.build_config("full", SEED, 50, "stage1_s42.pt", EXP)
    from trainer.train_vqvae import FactorVQVAE  # noqa: E402
    model = FactorVQVAE(cfg, T_max=n_batches * 50).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["train"]["learning_rate"])
    model.train()

    # ---- A: pure data fetch -------------------------------------------------
    it = iter(train_loader)
    t0 = time.time()
    batch0 = None
    for i in range(N):
        b = next(it)
        if i == 0:
            batch0 = b
    t_data = time.time() - t0
    print(f"A fetch {N} batches: {t_data:.2f}s -> {t_data / N * 1000:.1f} ms/batch", flush=True)
    print("batch shape:", tuple(batch0.shape), "dtype:", batch0.dtype, flush=True)

    # ---- B: model step -----------------------------------------------------
    torch.cuda.reset_peak_memory_stats()
    it2 = iter(train_loader)
    t0 = time.time()
    losses = []
    for i in range(N):
        b = next(it2).to(dev, non_blocking=True)
        feat, prior, fut = model._get_data(b, i)
        recon, vq, pred, total, z_q, _aux = model(feat, prior, fut)
        opt.zero_grad(set_to_none=True)
        total.backward()
        opt.step()
        losses.append(float(total))
    torch.cuda.synchronize()
    t_model = time.time() - t0
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    print(f"B model {N} steps: {t_model:.2f}s -> {t_model / N * 1000:.1f} ms/batch; "
          f"loss {losses[-1]:.4f}; peak_vram {peak:.2f} GB", flush=True)

    per_epoch = (t_data + t_model) / N * n_batches
    out = {
        "n_batches_per_epoch": int(n_batches),
        "workers": WORKERS,
        "n_timed": N,
        "data_ms_per_batch": round(t_data / N * 1000, 1),
        "model_ms_per_batch": round(t_model / N * 1000, 1),
        "combined_s_per_epoch": round(per_epoch, 1),
        "stage1_50_epochs_hours": round(per_epoch * 50 / 3600, 2),
        "peak_vram_gb": round(peak, 2),
        "batch_shape": list(batch0.shape),
        "torch": torch.__version__,
        "device": torch.cuda.get_device_name(0),
        "note": "stage-1 only (VQ-VAE). 阶段2 (GenerateReturn) 未计时；stage-1 权重复用后"
                "每个 seed 的 full/no_moe/no_prior 三档仍各要重训 stage2。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    p = os.path.join(REPO, "experiments", EXP, "throughput_bench.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("->", p, flush=True)


if __name__ == "__main__":
    main()
