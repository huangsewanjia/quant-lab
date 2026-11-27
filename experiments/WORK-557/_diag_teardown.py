"""诊断：fit 结束后进程为什么挂着不退出。

复刻 work557_throughput_bench_pl.py 的 setup（同 model / 同 Trainer / 同 loader），
只跑 3 步，然后立刻 dump 主线程栈 + 所有线程栈（faulthandler），定位死锁位置。
"""
from __future__ import annotations

import faulthandler
import os
import sys
import time

import pandas as pd
import torch

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "vendor", "PRISM-VQ"))

import work557_run_arms as M  # noqa: E402


def main():
    faulthandler.dump_traceback_later(75, repeat=False)
    import pytorch_lightning as pl

    M.patch_persistent_workers()
    M.patch_sampler_init()
    M.patch_data_loader_collate()
    from dataset.dataset import init_data_loader

    EXP = "WORK-557"
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
    seed_everything(42)
    pl.seed_everything(42, workers=True)

    train_ds = M.Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True, num_workers=8)
    cfg = M.build_config("full", 42, 50, "stage1_diag.pt", EXP)
    from trainer.train_vqvae import FactorVQVAE
    model = FactorVQVAE(cfg, T_max=n_batches * 50)

    tr = pl.Trainer(max_epochs=1, accelerator="gpu", devices=1, precision=32,
                    gradient_clip_val=1, logger=False, callbacks=[M.EpochTimer()],
                    enable_progress_bar=False, enable_model_summary=False,
                    deterministic=True, limit_train_batches=3, limit_val_batches=0)
    t0 = time.time()
    tr.fit(model, train_dataloaders=train_loader)
    print(f"[fit returned] {time.time()-t0:.1f}s", flush=True)
    print("[now doing nothing — hang happens here if it hangs]", flush=True)
    time.sleep(150)
    print("[clean exit]", flush=True)


if __name__ == "__main__":
    main()
