"""WORK-557 只做推理：从已训好的 stage-2 checkpoint 产出 preds/<arm>_s<seed>.parquet。

为什么需要它：2026-11-28 17:13 那次 `run_arms` 的 `full s42` 跑完了 stage-1（18:17）与 stage-2，
却在收尾阶段退出（`exit 18:58:38`）且**没有落 preds**；那次是用 pythonw 起的，stdout 全丢，
看不到报错。两个阶段的 checkpoint 都在，所以不必重训，直接补推理。

与 `scripts/work557_run_arms.py` 的推理段逐行等价：
    m2 = type(m2).load_from_checkpoint(best2, config=cfg, T_max=n_batches*epochs)
    m2.freeze_vqvae(); m2.eval()
    df, rankic, icir = run_inference(m2, test_loader, cfg)

用法：
  .venv\\Scripts\\python.exe -u experiments\\WORK-557\\work557_infer_only.py --arm full --seed 42
"""
from __future__ import annotations

import argparse
import glob
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
VENDOR = os.path.join(REPO, "vendor", "PRISM-VQ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["full", "no_vq", "no_moe", "no_prior"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--exp", default=EXP)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    T0 = time.time()

    def ev(m):
        print(f"[{time.time()-T0:7.1f}s] {m}", flush=True)

    import pytorch_lightning as pl
    import wandb
    M.patch_persistent_workers()
    M.patch_sampler_init()
    M.patch_data_loader_collate()
    from dataset.dataset import init_data_loader

    os.environ.setdefault("WANDB_MODE", "disabled")
    ds_dir = os.path.join(REPO, "data", args.exp, "ds")
    si = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    si["datetime"] = pd.to_datetime(si["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = si[si["segment"] == seg].sort_values(["datetime", "instrument"], kind="mergesort")
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)

    torch.set_num_threads(8)
    from utils import seed_everything
    seed_everything(args.seed)
    pl.seed_everything(args.seed, workers=True)
    try:
        wandb.init(mode="disabled", project="WORK-557-PRISM-VQ",
                   settings=wandb.Settings(silent=True))
    except Exception:
        wandb.log = lambda *a, **k: None

    train_ds = M.Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    test_ds = M.Work503Dataset(ds_dir, segs["test"], True, cs_label=False, dropna_label=False)
    train_loader, n_batches = init_data_loader(train_ds, shuffle=True, num_workers=args.workers)
    test_loader, n_test = init_data_loader(test_ds, shuffle=False, num_workers=0)
    ev(f"loaders: train {n_batches}, test {n_test}")

    ckpt_name = f"stage1_s{args.seed}.pt"
    cfg = M.build_config(args.arm, args.seed, 50, ckpt_name, args.exp)

    if args.arm == "full":
        from trainer.train_ypred import GenerateReturn
    elif args.arm == "no_moe":
        from trainer.train_ypred_wo_moe import GenerateReturn
    elif args.arm == "no_prior":
        from trainer.train_ypred_wo_prior import GenerateReturn
    else:
        from trainer.train_ypred_wo_stage1 import GenerateReturn

    pat = os.path.join(VENDOR, "checkpoints", f"stage2_{args.arm}_s{args.seed}*.ckpt")
    cands = sorted(glob.glob(pat), key=os.path.getmtime)
    if not cands:
        raise SystemExit(f"no stage-2 checkpoint matches {pat}")
    best2 = cands[-1]
    ev(f"stage-2 ckpt: {best2}")
    m2 = GenerateReturn.load_from_checkpoint(best2, config=cfg, T_max=n_batches * 50)
    if hasattr(m2, "freeze_vqvae"):
        m2.freeze_vqvae()
    m2.eval()

    from utils.test import run_inference
    df, rankic, icir = run_inference(m2, test_loader, cfg)
    df = df.reset_index()
    df.columns = ["datetime", "instrument", "score", "label"]
    out_dir = os.path.join(REPO, "experiments", args.exp, "preds")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{args.arm}_s{args.seed}.parquet")
    df.to_parquet(out_path)
    log = {
        "arm": args.arm, "seed": args.seed, "mode": "infer_only",
        "preds": out_path, "n_rows": int(len(df)),
        "stage2_ckpt": best2,
        "metrics": {k: float(v) for k, v in icir.items()},
        "seconds": round(time.time() - T0, 1),
    }
    with open(os.path.join(out_dir, f"{args.arm}_s{args.seed}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=1)
    ev(json.dumps(log, ensure_ascii=False, indent=1))
    ev("done")


if __name__ == "__main__":
    main()
