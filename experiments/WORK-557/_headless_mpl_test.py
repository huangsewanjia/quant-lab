"""回归测试：WORK-557 训练入口必须锁死无 GUI 的 matplotlib 后端。

2026-11-27 故障：`MPLBACKEND` 未设时，本机 matplotlib 解析成 QtAgg
（ds 环境装了 PyQt6 6.9.1，但没有 `PyQt6/Qt6` 平台插件目录）。上游
`vendor/PRISM-VQ/trainer/train_vqvae.py` 在每轮结束时调用 `plt.figure()`，
于是训练进程弹出一个模态 Qt 对话框
  "This application failed to start because no Qt platform plugin could be
   initialized."
并卡住，epoch 汇总与 `throughput_bench_pl.json` 都不落盘。

本测试在子进程里把 `MPLBACKEND` 置为空串（matplotlib 视同未设，旧代码的
`setdefault` 会因此失效）再按真实入口顺序 import `scripts/work557_run_arms.py`，
要求它自己把后端钉成 Agg；后端不是 Agg 就直接失败退出，**不创建 Qt 窗口**。

用法（无 Qt、无 GPU 计算，约 5 s）：
  .venv\\Scripts\\python.exe experiments\\WORK-557\\_headless_mpl_test.py
退出码 0 = 通过；1 = 回归。
"""
from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CHILD = """
import sys
sys.path.insert(0, {scripts!r})
sys.path.insert(0, {vendor!r})
import pandas, torch  # noqa: F401  -- real entry-point import order
import work557_run_arms  # noqa: F401  -- the fix lives in this module
from trainer.train_vqvae import FactorVQVAE  # noqa: F401  -- upstream pyplot import
import matplotlib
backend = matplotlib.get_backend()
print("backend=" + backend)
if backend.lower() != "agg":
    raise SystemExit("FAIL: expected Agg, got " + backend)
import matplotlib.pyplot as plt
fig = plt.figure()
plt.close(fig)
print("figure_ok=True")
"""


def main():
    env = dict(os.environ)
    env["MPLBACKEND"] = ""  # empty == unset for matplotlib; the pin must override
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [sys.executable, "-c", CHILD.format(
            scripts=os.path.join(REPO, "scripts"),
            vendor=os.path.join(REPO, "vendor", "PRISM-VQ"))],
        env=env, capture_output=True, text=True)
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip())
        print("FAIL: child exited %d" % proc.returncode)
        return 1
    ok = "backend=agg" in proc.stdout.lower() and "figure_ok=True" in proc.stdout
    print("PASS" if ok else "FAIL: backend/figure check failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
