#!/usr/bin/env python3
"""WORK-637 / phase 2c — encode the image library with frozen DINOv2-S.

Emits ``data/WORK-637/emb_seed<seed>.npy`` (float32, L2-normalised CLS token,
row order identical to ``index_seed<seed>.parquet``) plus a meta json with the
weight sha256, resolution and measured throughput.

    ../.venv/bin/python -u scripts/work637_encode.py --seed 42
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
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
WEIGHT_SHA256 = "ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1"


def load_paths(paths, px):
    arr = np.empty((len(paths), px, px, 3), dtype=np.uint8)
    for k, p in enumerate(paths):
        with Image.open(p) as im:
            a = np.asarray(im.convert("RGB"))
        if a.shape[0] != px or a.shape[1] != px:
            raise RuntimeError(f"unexpected image size {a.shape} at {p}")
        arr[k] = a
    x = arr.astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    return torch.from_numpy(x.transpose(0, 3, 1, 2)).contiguous()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True, choices=list(C.SEEDS))
    ap.add_argument("--batch", type=int, default=128)
    args = ap.parse_args()

    torch.set_num_threads(8)
    from transformers import Dinov2Model
    model = Dinov2Model.from_pretrained(C.MODEL_DIR).eval()

    idx = pd.read_parquet(os.path.join(C.DATA, f"index_seed{args.seed}.parquet"))
    paths = [os.path.join(r.dir, r.file) for r in idx.itertuples()]
    n = len(paths)
    out = np.empty((n, 384), dtype=np.float32)
    t0 = time.time()
    # shard checkpoints: a killed run loses at most one shard, not the whole pass
    shard = 20000
    shard_dir = os.path.join(C.DATA, f"shards_seed{args.seed}")
    os.makedirs(shard_dir, exist_ok=True)
    with torch.inference_mode():
        for s in range(0, n, args.batch):
            xb = load_paths(paths[s:s + args.batch], C.IMG_PX)
            h = model(pixel_values=xb).last_hidden_state[:, 0, :]
            h = h / h.norm(dim=-1, keepdim=True).clamp_min(1e-12)
            out[s:s + len(xb)] = h.numpy()
            if (s + len(xb)) % shard == 0 or s + len(xb) == n:
                np.save(os.path.join(shard_dir, f"{s + len(xb):06d}.npy"),
                        out[:s + len(xb)])
            if (s // args.batch) % 200 == 0:
                el = time.time() - t0
                done = s + len(xb)
                print(f"  {done}/{n} {el:.0f}s ({done / max(el, 1e-9):.1f}/s)", flush=True)
    el = time.time() - t0
    p = os.path.join(C.DATA, f"emb_seed{args.seed}.npy")
    np.save(p, out)
    meta = {"seed": args.seed, "n": int(n), "dim": int(out.shape[1]), "px": C.IMG_PX,
            "weights": "facebook/dinov2-small model.safetensors",
            "weights_sha256": WEIGHT_SHA256,
            "norms": {"min": float(np.linalg.norm(out, axis=1).min()),
                      "max": float(np.linalg.norm(out, axis=1).max())},
            "seconds": round(el, 1), "images_per_s": round(n / el, 2),
            "sha256": C.sha256_file(p), "bytes": os.path.getsize(p)}
    with open(os.path.join(C.EXP, f"embedding_meta_seed{args.seed}.json"), "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    print(json.dumps(meta, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
