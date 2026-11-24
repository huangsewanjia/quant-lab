#!/usr/bin/env python3
"""WORK-557：定位训练取数的真实瓶颈（微基准）。

要回答的问题：每样本 191 us 花在哪？
  (a) ds[i] 全流程（当前实现）
  (b) 只做 memmap 切片
  (c) 只做 _ffill_bfill
  (d) 只做 out 数组分配与拷贝
  (e) 线程池版本

用法：.venv\\Scripts\\python.exe experiments/WORK-557/work557_bench_loader.py
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
os.chdir(REPO)

import work557_run_arms as R  # noqa: E402


def main():
    ds_dir = os.path.join(REPO, "data", "WORK-557", "ds")
    idx = pd.read_parquet(os.path.join(ds_dir, "sample_index.parquet"))
    tr = idx[idx["segment"] == "train"].set_index(["datetime", "instrument"], drop=True)
    t0 = time.time()
    ds = R.Work503Dataset(ds_dir, tr, True, cs_label=True)
    print("dataset init: %.1fs, rows=%d" % (time.time() - t0, len(ds)), flush=True)

    rng = np.random.default_rng(0)
    rows = rng.choice(len(ds.rows), 4000, replace=False)
    rows = [int(r) for r in rows]

    # (a) 全流程（串行）
    t0 = time.time()
    for i in rows:
        ds[i]
    t_full = (time.time() - t0) / len(rows) * 1e6

    # (b) 只做 memmap 切片
    t0 = time.time()
    for i in rows:
        inst, dt = ds.rows[i]
        F, P, L = ds._load(inst)
        t = ds.cal_pos[dt]
        lo = max(0, t - R.T + 1)
        win = np.asarray(F[lo:t + 1], dtype="float32")
    t_slice = (time.time() - t0) / len(rows) * 1e6

    # (c) 只做 ffill（已换成快速版）
    inst, dt = ds.rows[rows[0]]
    F, P, L = ds._load(inst)
    t = ds.cal_pos[dt]
    lo = max(0, t - R.T + 1)
    win = np.asarray(F[lo:t + 1], dtype="float32")
    t0 = time.time()
    for _ in range(4000):
        R._ffill_bfill(win)
    t_ffill = (time.time() - t0) / 4000 * 1e6

    # (d) 只做 out 分配 + 拷贝
    t0 = time.time()
    for i in rows:
        inst, dt = ds.rows[i]
        F, P, L = ds._load(inst)
        t = ds.cal_pos[dt]
        lo = max(0, t - R.T + 1)
        win = np.asarray(F[lo:t + 1], dtype="float32")
        out = np.zeros((R.T, R.N_FEATURES + R.N_PRIOR + R.N_LABEL), dtype="float32")
        out[:, :R.N_FEATURES] = win
        pr = np.asarray(P[t])
        out[R.T - 1, R.N_FEATURES:R.N_FEATURES + R.N_PRIOR] = pr
        lab = np.asarray(L[t])
        out[R.T - 1, R.N_FEATURES + R.N_PRIOR:] = lab
    t_out = (time.time() - t0) / len(rows) * 1e6

    # (e) 线程池
    for nw in (4, 8, 12):
        pool = ThreadPoolExecutor(max_workers=nw)
        t0 = time.time()
        for i in range(0, len(rows), 300):
            list(pool.map(ds.__getitem__, rows[i:i + 300]))
        t_pool = (time.time() - t0) / len(rows) * 1e6
        pool.shutdown()
        print("  threads=%-3d %.1f us/sample" % (nw, t_pool), flush=True)

    print("\n=== 每样本耗时拆解 ===")
    print("  (a) ds[i] 全流程（串行）      %8.1f us" % t_full)
    print("  (b) 其中 memmap 切片         %8.1f us" % t_slice)
    print("  (c) 其中 _ffill_bfill         %8.1f us" % t_ffill)
    print("  (d) 其中 切片+out 分配拷贝    %8.1f us" % t_out)
    print("\n  全窗单轮估计（train 802,406 + valid 145,800 = 948,206 样本）")
    for nm, v in (("串行 ds[i]", t_full), ("线程池 8", None)):
        pass
    print("    串行: %.0f s/轮" % (t_full * 948206 / 1e6))


if __name__ == "__main__":
    main()
