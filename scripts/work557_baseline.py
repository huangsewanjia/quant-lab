#!/usr/bin/env python3
"""WORK-557 stage 2 — GRU baseline (and a linear baseline) on the same data.

The baseline model class is Qlib's own GRU implementation
(`qlib.contrib.model.pytorch_gru_ts.GRU`) — the same class the paper's
baseline table is built on.  Only the training loop is ours (the paper does
not release the baseline training code); it uses exactly the protocol fixed
in preregistration_v1.1.md: same features, same PIT universe, same splits,
same seeds, same batch-per-day layout, same loss (MSE on the CSRankNorm'd
5-day forward return).

Outputs the same parquet shape as the PRISM-VQ arms so that the backtest
and the H2 bootstrap can consume them directly.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

# ---- qlib runtime bootstrap -------------------------------------------
# pyqlib 0.9.7 publishes no cp313 wheel and this machine trains on
# Python 3.13.15.  .venv/qlib312 holds the cp312 build plus the numerically
# validated pure-python `_libs` stand-in (see
# experiments/WORK-557/work557_qlib_shim.py; the stand-in was checked
# element-by-element against the real cp39 .pyd, max |diff| = 0).
_REPO_QLIB = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO_QLIB, ".venv", "qlib312"))
import torch
import torch.nn as nn

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))

N_FEATURES = 158
N_PRIOR = 6
N_LABEL = 10
T = 20
TARGET = 4          # RET_5D (0-based)


class LinearHead(nn.Module):
    def __init__(self, d_in):
        super().__init__()
        self.fc = nn.Linear(d_in, 1)

    def forward(self, x):          # (B, T, F) -> (B, 1)
        return self.fc(x[:, -1, :])


def build_model(kind: str, seed: int, epochs: int):
    """Build the baseline model.

    NOTE (bug fix, 2026-11-22): qlib's ``GRU`` is a *wrapper* class
    (``qlib.contrib.model.pytorch_gru_ts.GRU``), not an ``nn.Module`` — the
    actual torch module lives in ``self.GRU_model``.  The previous call
    ``GRU(d_feat=..., d_model=64, n_layers=1, dropout=0.1)`` had two defects:

    1. ``d_model`` / ``n_layers`` are **not** parameters of qlib's ``GRU``
       (its signature is ``d_feat / hidden_size / num_layers / dropout / ...``),
       so they were swallowed by ``**kwargs`` and silently ignored — the model
       actually ran with ``num_layers=2, dropout=0.0``.
    2. The wrapper exposes no ``.parameters()`` / ``.state_dict()``, so the
       training loop crashed with
       ``AttributeError: 'GRU' object has no attribute 'parameters'``.

    Both are plumbing bugs in *our* script, not protocol choices: the
    pre-registration only names the class
    (``qlib.contrib.model.pytorch_gru_ts.GRU``), so the hyper-parameters the
    script always intended (``hidden_size=64``, ``num_layers=1``,
    ``dropout=0.1``) are now actually applied, and ``seed=`` is forwarded so
    qlib seeds the RNG before the module is constructed.
    """
    if kind == "gru":
        from qlib.contrib.model.pytorch_gru_ts import GRU
        return GRU(d_feat=N_FEATURES, hidden_size=64, num_layers=1,
                   dropout=0.1, n_epochs=epochs, lr=1e-3, seed=seed)
    return LinearHead(N_FEATURES)


def core_module(model):
    """Return the torch module that actually holds the parameters."""
    return getattr(model, "GRU_model", model)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="gru", choices=["gru", "linear"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--exp", default="WORK-557")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out-suffix", default="",
                    help="产物文件名后缀（探针跑用 _probe，避免覆盖正式结果）")
    ap.add_argument(
        "--limit-days", type=int, default=0,
        help="smoke mode: keep only the first N trading days of every "
             "segment.  0 = all.  Used to exercise the whole path (train -> "
             "inference -> parquet) in ~1 minute before a full run.")
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)

    from work557_run_arms import Work503Dataset, _ffill_bfill

    exp_dir = os.path.join(REPO, "experiments", args.exp)
    ds_dir = os.path.join(REPO, "data", args.exp, "ds")
    out_dir = os.path.join(exp_dir, "preds")
    os.makedirs(out_dir, exist_ok=True)

    sample_index = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    sample_index["datetime"] = pd.to_datetime(sample_index["datetime"])
    segs = {}
    for seg in ("train", "valid", "test"):
        sub = sample_index[sample_index["segment"] == seg].copy()
        sub = sub.sort_values(["datetime", "instrument"], kind="mergesort")
        if args.limit_days:
            keep_days = sorted(sub["datetime"].unique())[: args.limit_days]
            sub = sub[sub["datetime"].isin(keep_days)]
        segs[seg] = sub.set_index(["datetime", "instrument"], drop=True)
        print(f"segment {seg}: {len(sub)} samples", flush=True)

    train_ds = Work503Dataset(ds_dir, segs["train"], True, cs_label=True)
    valid_ds = Work503Dataset(ds_dir, segs["valid"], True, cs_label=True)
    test_ds = Work503Dataset(ds_dir, segs["test"], True, cs_label=False,
                              dropna_label=False)

    model = build_model(args.kind, args.seed, args.epochs)
    core = core_module(model)
    device = torch.device(args.device)
    core = core.to(device)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    opt = torch.optim.AdamW(core.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=max(1, args.epochs))

    # ONE generator for the whole run.  Re-seeding it inside `batches()`
    # would make every epoch see exactly the same day order — reproducible,
    # but the shuffling degenerates and the baseline trains worse than it
    # should.  With a single generator the run is still bit-reproducible from
    # the seed (the state advances deterministically).
    rng = np.random.default_rng(args.seed)

    # 逐日分组只算一次（按 dataset 缓存）：原来每个 epoch 都对 802,406 行做一次
    # reset_index + groupby（纯开销，且与口径无关）。每轮仍从**排序后的同一份列表**
    # 开始洗牌，与原实现的日序语义完全一致。
    _groups_cache = {}

    def _groups_of(ds):
        key = id(ds)
        if key not in _groups_cache:
            _idx = ds.index.reset_index()
            _idx["date"] = _idx["datetime"]
            _groups_cache[key] = list(_idx.groupby("date", sort=True).groups.values())
        return _groups_cache[key]

    # 逐样本取数**保持串行**：实测线程池反而更慢（executor.map 每样本要过一次
    # 队列 + 锁，而每样本的真实 numpy 工作量只有 ~30 us，GIL 争用把省下的
    # ffill 时间又吃回去了 —— 实测 60 ms/batch，串行约 42 ms/batch）。
    # 取数本身已通过 ffill 等价快实现从 ~77 us/窗口降到 ~15.7 us/窗口
    # （逐位一致，见 experiments/WORK-557/ffill_equivalence.json）。

    def batches(ds, shuffle):
        groups = list(_groups_of(ds))
        if shuffle:
            rng.shuffle(groups)
        for g in groups:
            rows = [i for i in g]
            # `ds.rows[i]` is only the (instrument, datetime) key pair — the
            # actual (T, 174) sample must come from `ds[i]`.  Stacking the key
            # pairs produced an object array that torch refuses to convert
            # (TypeError: can't convert np.ndarray of type numpy.object_).
            batch = np.stack([ds[i] for i in rows])
            yield torch.from_numpy(batch), rows

    best_val, best_state = np.inf, None
    epoch_seconds = []
    t0 = time.time()
    for ep in range(args.epochs):
        core.train()
        tot, nb = 0.0, 0
        for batch, _ in batches(train_ds, True):
            x = batch[:, :, :N_FEATURES].float().to(device, non_blocking=True)
            y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float().to(device)
            pred = core(x).reshape(-1)
            loss = nn.functional.mse_loss(pred, y)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(core.parameters(), 1.0)
            opt.step()
            tot += float(loss)
            nb += 1
        sched.step()
        core.eval()
        vs, vn = 0.0, 0
        with torch.no_grad():
            for batch, _ in batches(valid_ds, False):
                x = batch[:, :, :N_FEATURES].float().to(device, non_blocking=True)
                y = batch[:, -1, N_FEATURES + N_PRIOR + TARGET].float().to(device)
                pred = core(x).reshape(-1)
                vs += float(nn.functional.mse_loss(pred, y))
                vn += 1
        vloss = vs / max(1, vn)
        epoch_seconds.append(round(time.time() - t0, 1))
        print(f"epoch {ep}: train {tot / max(1, nb):.4f} valid {vloss:.4f} "
              f"({time.time() - t0:.0f}s)", flush=True)
        if vloss < best_val:
            best_val = vloss
            best_state = {k: v.clone() for k, v in core.state_dict().items()}

    if best_state is not None:
        core.load_state_dict(best_state)

    # ---- inference on the test set --------------------------------------
    core.eval()
    rows, scores = [], []
    with torch.no_grad():
        for batch, idx in batches(test_ds, False):
            x = batch[:, :, :N_FEATURES].float().to(device)
            pred = core(x).reshape(-1).cpu().numpy()
            scores.append(pred)
            rows.extend(idx)
    df = pd.DataFrame({"instrument": [test_ds.rows[i][0] for i in rows],
                       "datetime": [test_ds.rows[i][1] for i in rows],
                       "score": np.concatenate(scores)})
    # `rows` holds *indices* into the dataset, not (instrument, datetime) key
    # pairs — the key pair has to be looked up through `test_ds.rows[i]`.
    # (Bug fix 2026-11-22: this line used `r[0]`/`r[1]` on an int and crashed
    # with "TypeError: 'int' object is not subscriptable" *after* the whole
    # training had finished.)
    label = np.stack([test_ds._load(test_ds.rows[r][0])[2][
        test_ds.cal_pos[test_ds.rows[r][1]]] for r in rows])
    df["label"] = label[:, TARGET]
    df = df.sort_values(["datetime", "instrument"])
    out = os.path.join(out_dir, f"{args.kind}_s{args.seed}.parquet")
    df.to_parquet(out)

    # RankIC on the test set (paper appendix C)
    from scipy.stats import spearmanr
    ric = []
    for d, g in df.dropna(subset=["label"]).groupby("datetime"):
        if len(g) < 3:
            continue
        ric.append(spearmanr(g["label"], g["score"]).statistic)
    ric = np.asarray(ric, dtype="float64")
    metrics = {"rankic": float(np.nanmean(ric)),
               "rankicir": float(np.nanmean(ric) / np.nanstd(ric))}
    log = {"kind": args.kind, "seed": args.seed, "epochs": args.epochs,
           "valid_loss": best_val, "metrics": metrics, "preds": out,
           "seconds": round(time.time() - t0, 1),
           "epoch_seconds": epoch_seconds,
           "epochs_ran": len(epoch_seconds),
           "device": str(device),
           "peak_vram_gb": (round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3)
                           if torch.cuda.is_available() else None)}
    json.dump(log, open(os.path.join(out_dir,
                                     f"{args.kind}_s{args.seed}{args.out_suffix}.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(log, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
