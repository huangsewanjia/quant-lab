#!/usr/bin/env python3
"""WORK-557：验证 _ffill_bfill 的**等价快速实现**（逐位一致）。

背景
----
原实现（work557_run_arms.py::_ffill_bfill）：
    x = x.astype("float64")
    mask = np.isnan(x)
    idx = np.where(~mask, np.arange(T)[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    x = np.take_along_axis(x, idx, axis=0)
    x[mask] = np.nan          # <- 把刚填上的值又还原成 NaN
    ... 同结构的 backward fill ...
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return x.astype("float32")

分析：两次 fill 的结果都被紧随其后的 `x[mask] = nan` 还原 —— 原 NaN 位置仍 NaN、
非 NaN 位置取值未变，故整个函数等价于
    np.nan_to_num(x.astype("float64"), nan=0.0, posinf=0.0, neginf=0.0).astype("float32")
但代价是每样本要做两次 take_along_axis + maximum.accumulate（实测 ~200 µs/样本，
80 万样本/轮 → 单轮 ~160 s，是训练的主瓶颈）。

本脚本用**真实数据**（ds/feat 里的实际窗口，含 NaN/inf/全 NaN 列等边界情形）
逐位比对两个实现，max_abs_diff 必须为 0.0 才允许替换。

用法：.venv\\Scripts\\python.exe experiments/WORK-557/work557_ffill_equiv.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))


def orig(x: np.ndarray) -> np.ndarray:
    x = x.astype("float64")
    mask = np.isnan(x)
    idx = np.where(~mask, np.arange(x.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    x = np.take_along_axis(x, idx, axis=0)
    x[mask] = np.nan
    mask = np.isnan(x)
    rev = x[::-1]
    m2 = np.isnan(rev)
    idx = np.where(~m2, np.arange(x.shape[0])[:, None], 0)
    np.maximum.accumulate(idx, axis=0, out=idx)
    rev = np.take_along_axis(rev, idx, axis=0)
    rev[m2] = np.nan
    x = rev[::-1]
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return x.astype("float32")


def fast(x: np.ndarray) -> np.ndarray:
    return np.nan_to_num(x.astype("float64"), nan=0.0, posinf=0.0,
                         neginf=0.0).astype("float32")


def main():
    ds = os.path.join(REPO, "data", "WORK-557", "ds")
    feat_dir = os.path.join(ds, "feat")
    files = sorted(f for f in os.listdir(feat_dir) if f.endswith(".npy"))
    rng = np.random.default_rng(0)
    n_checked = 0
    max_diff = 0.0
    n_nan = n_inf = 0
    T = 20
    for fn in files[::37]:                      # 抽样 ~1/37 的标的
        inst = fn[:-4]
        arr = np.load(os.path.join(feat_dir, fn), mmap_mode="r")
        n_rows = arr.shape[0]
        # 取若干随机位置的真实窗口（沿时间轴），并覆盖行首（bfill 边界）
        starts = [0, 1, 2] + list(rng.integers(0, max(n_rows - T, 1), 12))
        for st in starts:
            lo = max(0, int(st))
            win = np.asarray(arr[lo:lo + T], dtype="float32")
            if win.shape[0] < T:                # 行首不足 T 行 -> 补 NaN
                pad = np.full((T - win.shape[0], win.shape[1]), np.nan,
                              dtype="float32")
                win = np.concatenate([pad, win], axis=0)
            n_nan += int(np.isnan(win).sum())
            n_inf += int(np.isinf(win).sum())
            a = orig(win)
            b = fast(win)
            d = float(np.max(np.abs(a.astype("float64") - b.astype("float64")))) \
                if a.size else 0.0
            max_diff = max(max_diff, d)
            n_checked += 1
    # 额外边界：全 NaN 列、含 ±inf、单行
    edge = [
        np.full((T, 5), np.nan, dtype="float32"),
        np.array([[np.inf, -np.inf, 0.0, np.nan, 1.0]] * T, dtype="float32"),
        np.zeros((T, 3), dtype="float32"),
    ]
    for e in edge:
        a, b = orig(e), fast(e)
        max_diff = max(max_diff, float(np.max(np.abs(a.astype("float64")
                                                   - b.astype("float64")))))
        n_checked += 1

    # 计时
    import time
    sample = np.asarray(np.load(os.path.join(feat_dir, files[0]), mmap_mode="r")[:T],
                        dtype="float32")
    t0 = time.time()
    for _ in range(2000):
        orig(sample)
    t_orig = (time.time() - t0) / 2000 * 1e6
    t0 = time.time()
    for _ in range(2000):
        fast(sample)
    t_fast = (time.time() - t0) / 2000 * 1e6

    out = {"windows_checked": n_checked, "max_abs_diff": max_diff,
           "nan_values": n_nan, "inf_values": n_inf,
           "us_per_window_orig": t_orig, "us_per_window_fast": t_fast,
           "speedup": t_orig / max(t_fast, 1e-9),
           "equiv": max_diff == 0.0}
    path = os.path.join(HERE, "ffill_equivalence.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("wrote -> %s" % path)
    return 0 if out["equiv"] else 1


if __name__ == "__main__":
    sys.exit(main())
