"""WORK-557 单档真实循环计时（给「这一档到底要多久」定价的唯一可信数）。

与 `scripts/work557_run_arms.py` 的 stage-1 **逐项相同**：同 dataset、同 loader、
同 FactorVQVAE、同 Trainer 参数、同 callbacks（早停 patience=15 / min_delta=1e-5）、
同 `wandb.init(mode="disabled")`。差别只有三处（都不改数值口径）：
  1) `checkpoints_bench/` 落盘，不碰官方 `vendor/PRISM-VQ/checkpoints/stage1_s<seed>.pt`，
     以免把 N 轮的半成品当成正式 stage-1 复用；
  2) 可传 `--epochs` 只跑前 N 轮（用来定价，不是正式训练）；
  3) 额外记录：worker spawn 时间、每轮墙钟、峰值显存、valid 段耗时。

产物：experiments/WORK-557/throughput_bench_pl.json
用法：
  .venv\\Scripts\\python.exe -u experiments\\WORK-557\\work557_epoch_timing.py --epochs 10 --seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import time

import pandas as pd
import torch

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys_path_done = False
import sys  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "vendor", "PRISM-VQ"))

import work557_run_arms as M  # noqa: E402

EXP = "WORK-557"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--arm", default="full")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--train-batches", type=int, default=-1,
                    help="-1 表示 2675 个 batch/轮（真实口径）；给小数用于 A/B 对照")
    ap.add_argument("--val-batches", type=int, default=-1,
                    help="-1 表示跑完整个 valid 段（真实口径）")
    args = ap.parse_args()

    os.environ.setdefault("WANDB_MODE", "disabled")
    if os.environ.get("PYDUMP"):
        import faulthandler
        faulthandler.dump_traceback_later(int(os.environ["PYDUMP"]), repeat=True)
    T0 = time.time()

    def ev(tag, t=None):
        print(f"[{time.time()-T0:8.1f}s] {tag}", flush=True)

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
    ev(f"loaders built: train batches/epoch={n_batches}, valid batches={n_val}")

    # 上游在 on_train_epoch_end 里 wandb.log(wandb.Image(plt))；不 init 会联网挂住。
    try:
        wandb.init(mode="disabled", project="WORK-557-PRISM-VQ",
                   settings=wandb.Settings(silent=True))
    except Exception as exc:  # pragma: no cover
        print(f"wandb.init failed ({exc}); stubbing wandb.log", flush=True)
        wandb.log = lambda *a, **k: None

    cfg = M.build_config(args.arm, args.seed, args.epochs, "stage1_bench.pt", EXP)
    from trainer.train_vqvae import FactorVQVAE
    model = FactorVQVAE(cfg, T_max=n_batches * args.epochs)

    ckdir = os.path.join(EXP_pkg(), "checkpoints_bench")
    os.makedirs(ckdir, exist_ok=True)
    ck = pl.callbacks.ModelCheckpoint(dirpath=ckdir, save_top_k=1,
                                      monitor="val_loss", mode="min",
                                      filename=f"stage1_bench_s{args.seed}")
    es = pl.callbacks.EarlyStopping(monitor="val_loss", mode="min",
                                    patience=15, min_delta=1e-5)
    timer = M.EpochTimer()
    tr = pl.Trainer(max_epochs=args.epochs, accelerator="gpu", devices=1,
                    precision=32, gradient_clip_val=1, logger=False,
                    callbacks=[ck, es, timer], enable_progress_bar=False,
                    enable_model_summary=False, deterministic=True,
                    limit_train_batches=(None if args.train_batches < 0 else args.train_batches),
                    limit_val_batches=(None if args.val_batches < 0 else args.val_batches))
    ev("trainer built; fit() start (含 8 worker spawn)")
    t_fit = time.time()
    tr.fit(model, train_dataloaders=train_loader, val_dataloaders=valid_loader)
    fit_s = time.time() - t_fit
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    ev(f"fit returned in {fit_s:.1f}s")

    hist = list(timer.history)
    ep = [h["seconds"] for h in hist]
    # 首轮含 worker spawn 与 cuda 预热，稳态取第 2 轮起的中位数
    warm = ep[0] if ep else None
    steady = sorted(ep[1:])[len(ep[1:]) // 2] if len(ep) > 1 else None
    out = {
        "arm": args.arm,
        "seed": args.seed,
        "n_epochs_requested": args.epochs,
        "n_epochs_ran": len(ep),
        "n_batches_per_epoch": int(n_batches),
        "n_valid_batches": int(n_val),
        "num_workers": args.workers,
        "val_batches": args.val_batches,
        "train_batches": args.train_batches,
        "fit_seconds": round(fit_s, 1),
        "epoch_seconds": ep,
        "epoch_1_seconds_with_spawn": warm,
        "steady_state_median_s_per_epoch": steady,
        "s_per_batch_steady": round(steady / n_batches, 3) if steady else None,
        "s_per_batch_timestamps": round(fit_s / max(1, sum(ep)), 3) if ep else None,
        "peak_vram_gb": round(peak, 3),
        "stage1_50_epochs_hours_steady": round(steady * 50 / 3600, 2) if steady else None,
        "stage1_50_epochs_hours_with_first": round((sum(ep) / max(1, len(ep))) * 50 / 3600, 2) if ep else None,
        "epoch_history": hist,
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "wandb": "disabled",
        "note": "真实 PL 循环 + 真实 valid 段（valid 用 num_workers=0，与生产一致）；"
                "checkpoint 落 checkpoints_bench/，不动官方 stage1_s<seed>.pt。",
    }
    print(json.dumps({k: v for k, v in out.items() if k != "epoch_history"},
                     ensure_ascii=False, indent=1), flush=True)
    p = os.path.join(EXP_pkg(), "throughput_bench_pl.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    ev(f"-> {p}")


def EXP_pkg():
    return os.path.join(REPO, "experiments", EXP)


if __name__ == "__main__":
    main()
