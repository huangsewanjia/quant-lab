#!/usr/bin/env python3
"""WORK-637 — rendering-jitter sensitivity diagnostic.

The pre-registered seed 2024 was meant to re-render the whole library with
``pad = 6%`` instead of ``2%`` (preregistration §3.1). Under the measured machine
load a second full pass (185,729 renders + 185,729 encodes) was not affordable,
so this diagnostic measures *what that perturbation does to retrieval* on a
sample instead: 2,000 queries are re-rendered at 6%, encoded, and their
top-1000 neighbourhoods and signals are compared with the 2% versions.

    ../.venv/bin/python -u scripts/work637_jitter_check.py --n 2000
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C
import work637_encode as E
import work637_render as R

ALT_PAD = 0.060
BASE_PAD = 0.020


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--k", type=int, default=1000)
    args = ap.parse_args()

    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    lab = C.label_matrix(panel, elig)
    idx = pd.read_parquet(os.path.join(C.DATA, "index_seed42.parquet"))
    emb = np.load(os.path.join(C.DATA, "emb_seed42.npy"))
    weeks = pd.to_datetime(panel["days"][panel["week_idx"]])
    wmap = {d: w for w, d in enumerate(weeks.strftime("%Y-%m-%d"))}
    w_of = idx["date"].map(wmap).to_numpy()
    j_of = idx["ts_code"].map({c: j for j, c in enumerate(panel["codes"])}).to_numpy()

    rng = np.random.default_rng(20241127)
    pick = np.sort(rng.choice(len(idx), size=min(args.n, len(idx)), replace=False))

    tmp = os.path.join(C.DATA, "jitter_tmp")
    os.makedirs(tmp, exist_ok=True)
    import torch
    torch.set_num_threads(8)
    from transformers import Dinov2Model
    model = Dinov2Model.from_pretrained(C.MODEL_DIR).eval()

    R._worker_init(panel, ALT_PAD, 42)
    new_emb = np.empty((len(pick), emb.shape[1]), dtype=np.float32)
    for s in range(0, len(pick), 128):
        paths = []
        for r in pick[s:s + 128]:
            p = os.path.join(tmp, f"{r}.png")
            R._render((int(w_of[r]), int(j_of[r]), p))
            paths.append(p)
        with torch.inference_mode():
            h = model(pixel_values=E.load_paths(paths, C.IMG_PX)).last_hidden_state[:, 0, :]
        new_emb[s:s + len(h)] = (h / h.norm(dim=-1, keepdim=True)).numpy()
        for p in paths:
            os.remove(p)

    # library legality identical to the signal engine
    res = {"n": int(len(pick)), "k": args.k, "alt_pad": ALT_PAD, "base_pad": BASE_PAD,
           "cosine_self": float(np.mean(np.sum(emb[pick] * new_emb, axis=1)))}
    jac, sig_corr, base_sig, alt_sig = [], [], [], []
    for t, r in enumerate(pick):
        q_idx = panel["week_idx"][w_of[r]]
        legal = np.nonzero(panel["week_idx"][w_of] + 6 <= q_idx)[0]
        if len(legal) < args.k + 1:
            continue
        for tag, q in (("base", emb[r]), ("alt", new_emb[t])):
            sim = emb[legal] @ q
            top = np.argpartition(-sim, args.k)[:args.k]
            lbl = lab[w_of[legal[top]], j_of[legal[top]]]
            (base_sig if tag == "base" else alt_sig).append(np.nanmean(lbl))
            if tag == "alt":
                jac.append(len(set(top) & set(prev_top)) / len(set(top) | set(prev_top)))
            else:
                prev_top = top
    res["topk_jaccard_mean"] = float(np.mean(jac))
    res["topk_jaccard_p10"] = float(np.percentile(jac, 10))
    a, b = np.array(base_sig), np.array(alt_sig)
    m = np.isfinite(a) & np.isfinite(b)
    res["signal_corr"] = float(np.corrcoef(a[m], b[m])[0, 1])
    res["signal_mean_abs_diff"] = float(np.mean(np.abs(a[m] - b[m])))
    res["queries_used"] = int(m.sum())
    p = os.path.join(C.EXP, "jitter_check.json")
    with open(p, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
