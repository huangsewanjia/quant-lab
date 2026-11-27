"""插桩：数一数 DataLoader 到底被创建/迭代了几次、每次 spawn 多少 worker。

只读诊断，不改任何生产代码。用法：
  python experiments/WORK-557/_diag_spawn.py [epochs] [limit_batches] [workers]
"""
from __future__ import annotations

import os
import sys
import time

import pandas as pd
import torch

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "vendor", "PRISM-VQ"))

import work557_run_arms as M  # noqa: E402

T0 = time.time()
EVENTS = []


def ev(tag):
    dt = time.time() - T0
    EVENTS.append((round(dt, 1), tag))
    print(f"[{dt:7.1f}s] {tag}", flush=True)


def main():
    n_epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 8

    import pytorch_lightning as pl

    M.patch_persistent_workers()
    M.patch_sampler_init()
    M.patch_data_loader_collate()
    from dataset.dataset import init_data_loader
    from torch.utils.data import DataLoader as _DL  # 真类（dataset.DataLoader 已被 _dl 包装）

    orig_iter = _DL.__iter__
    orig_len = _DL.__len__

    def _iter(self):
        ev(f"DataLoader.__iter__ workers={self.num_workers} persistent={self.persistent_workers}")
        return orig_iter(self)

    def _len(self):
        ev("DataLoader.__len__")
        return orig_len(self)

    _DL.__iter__ = _iter
    _DL.__len__ = _len

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
    ev("dataset built; constructing loader")
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True, num_workers=workers)
    ev(f"loader constructed (batches/epoch={n_batches})")

    cfg = M.build_config("full", 42, 50, "stage1_diag.pt", EXP)
    from trainer.train_vqvae import FactorVQVAE
    model = FactorVQVAE(cfg, T_max=n_batches * 50)

    timer = M.EpochTimer()
    tr = pl.Trainer(max_epochs=n_epochs, accelerator="gpu", devices=1, precision=32,
                    gradient_clip_val=1, logger=False, callbacks=[timer],
                    enable_progress_bar=False, enable_model_summary=False,
                    deterministic=True, limit_train_batches=limit,
                    limit_val_batches=0)
    ev("trainer built; calling fit")
    t_fit = time.time()
    tr.fit(model, train_dataloaders=train_loader)
    t_fit_done = time.time()
    ev(f"fit RETURNED in {t_fit_done-t_fit:.1f}s")
    ev(f"epochs={len(timer.history)} per-epoch={[h['seconds'] for h in timer.history]}")
    ev("entering teardown watch (no work)")
    time.sleep(8)
    ev("teardown watch done; script about to exit")
    import atexit

    def _bye():
        ev("atexit reached")

    atexit.register(_bye)


if __name__ == "__main__":
    main()
