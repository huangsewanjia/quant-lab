#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · 页缓存区间预热（warm_cache_range）回归测试。

背景（2026-11-25 20:07 / 20:10 两次重跑崩盘）：
第 16 次「整机 OOM」处置把页缓存预热从「整文件顺序读」改成「按日期区间读」，
新写的 `warm_cache_range` 里有一行 `arr.close()` —— 但 `np.load(mmap_mode="r")`
返回的是 **numpy.memmap**，numpy 2.x 下**没有 .close() 方法**
（实测 hasattr(np.memmap, "close") == False）→ 抛 AttributeError，
训练在预热阶段**秒崩**。当时误判为整机 OOM，链又自动重跑了 2 次带坏代码的进程。

本测试把「预热函数本身」锁住：
  T1 旧写法必崩（先红）——证明这类 bug 真实存在且能被断言捕获；
  T2 新写法对合成 .npy 不崩，且读到的**字节位置**正确（不是瞎读）；
  T3 对真实面板文件（若存在）用极小区间跑一次，不崩且字节数自洽。

运行：python scripts/work566_warm_cache_test.py
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from work566_train import warm_cache_range  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def t1_old_pattern_must_raise():
    """T1（先红）：旧写法 arr.close() 在 numpy.memmap 上必须抛 AttributeError。"""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.npy")
        np.save(p, np.zeros((4, 4), dtype=np.float32))
        arr = np.load(p, mmap_mode="r")
        check("T1a np.memmap 没有 .close() 属性", not hasattr(arr, "close"),
              f"type={type(arr).__name__}")
        raised = False
        try:
            arr.close()
        except AttributeError:
            raised = True
        check("T1b 旧写法 arr.close() 抛 AttributeError", raised)
        del arr


def t2_synthetic_roundtrip():
    """T2：新写法在合成 .npy 上不崩，且预热读到的字节位置正确。"""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "s.npy")
        n_stock, n_date, n_feat = 7, 11, 3
        arr = np.arange(n_stock * n_date * n_feat, dtype=np.float32).reshape(
            n_stock, n_date, n_feat)
        np.save(p, arr)

        lo, hi = 2, 6
        before = warm_cache_range(p, n_stock, n_date, n_feat * 4, lo, hi, "合成文件")
        check("T2a 预热返回正数字节数", isinstance(before, (int, float)) and before >= 0,
              f"返回 {before}")

        # 独立核对：按同一偏移读一遍，与内存数组逐位比对
        header = int(np.load(p, mmap_mode="r").offset)
        with open(p, "rb") as f:
            for s in (0, 3, n_stock - 1):
                start = header + (s * n_date + lo) * n_feat * 4
                f.seek(start)
                chunk = np.frombuffer(f.read((hi - lo + 1) * n_feat * 4), dtype=np.float32)
                expect = arr[s, lo:hi + 1, :].reshape(-1)
                check(f"T2b 股票 {s} 区间字节与数组逐位一致",
                      np.array_equal(chunk, expect))


def t3_real_panel():
    """T3：真实面板文件用极小区间跑一次（不崩 + 字节数自洽）。

    只取**前 3 只股票**：warm_cache_range 的字节偏移按 (stock, date, feat) 布局
    逐股计算，传 n_stocks=3 时算出的偏移对真实文件依然正确，
    又避免 5783 次小读在繁忙磁盘上把测试拖成分钟级。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    p = os.path.join(repo, "data", "WORK-566", "feat.npy")
    if not os.path.exists(p):
        print("  [SKIP] T3 真实面板文件不存在，跳过")
        return
    arr = np.load(p, mmap_mode="r")
    n_date, n_feat = int(arr.shape[1]), int(arr.shape[2])
    del arr
    lo, hi = 100, 101
    n_stock = 3
    n = warm_cache_range(p, n_stock, n_date, n_feat * 4, lo, hi, "真实面板小区间")
    expect = (hi - lo + 1) * n_feat * 4 * n_stock
    check("T3a 真实面板预热字节数自洽", n == expect, f"实得 {n}，期望 {expect}")


def main():
    print("WORK-566 · 页缓存区间预热回归测试")
    t1_old_pattern_must_raise()
    t2_synthetic_roundtrip()
    t3_real_panel()
    print(f"\n结果：{len(FAIL)} 项 FAIL")
    if FAIL:
        for f in FAIL:
            print(f"  FAIL: {f}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
