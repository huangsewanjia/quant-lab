#!/usr/bin/env python3
"""WORK-557 环境搭建（一次性脚本，幂等）。

三件事：
  1. 建 py3.13 venv（--system-site-packages，继承 ds 环境的 torch 2.11+cu128）
     -> .venv/ ；qlib 的纯 python 依赖装在这里，不污染 ds 环境（WORK-538 在用）。
  2. 建 py3.9 venv（从 moblieagent 解释器）-> .venv39/ ；装 pyqlib 的 cp39 轮子
     （它的 _libs/*.pyd 是 cp39 ABI，是 _libs 语义层面的「参考实现」）。
  3. 把 pyqlib 的 cp312 轮子解到 .tmp_qlib312/，供 py3.13 用。

不做：不改 ds 环境任何已装的包；不写 Z:\\quantdb。
"""
import os
import pathlib
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
DS_PY = r"F:\001_install\020_Anaconda\envs\ds\python.exe"
PY39 = r"F:\001_install\020_Anaconda\envs\moblieagent\python.exe"
VENV313 = ROOT / ".venv"
VENV39 = ROOT / ".venv39"
QLIB312 = ROOT / ".tmp_qlib312"
WHL = ROOT / ".tmp_pip" / "pyqlib-0.9.7-cp312-cp312-win_amd64.whl"


def run(cmd, **kw):
    print(">>>", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kw)


def main():
    # ---- 1. py3.13 venv (inherits torch from ds) --------------------------
    if not (VENV313 / "Scripts" / "python.exe").exists():
        r = run([DS_PY, "-m", "venv", "--system-site-packages", str(VENV313)])
        if r.returncode:
            sys.exit("venv313 failed")
    py313 = str(VENV313 / "Scripts" / "python.exe")

    # qlib 的纯 python 依赖（--no-deps 逐个装，避免动 numpy/pandas/torch）
    light = [
        "ruamel.yaml", "loguru", "fire", "dill", "filelock>=3.16.0",
        "python-redis-lock", "redis", "pymongo", "tqdm", "joblib",
        "scikit-learn", "lightgbm", "cvxpy", "mlflow", "pyarrow",
    ]
    r = run([py313, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
            + light)
    print("light deps rc =", r.returncode, flush=True)

    # ---- 2. py3.9 venv (reference _libs) ---------------------------------
    if not (VENV39 / "Scripts" / "python.exe").exists():
        r = run([PY39, "-m", "venv", str(VENV39)])
        if r.returncode:
            sys.exit("venv39 failed")
    py39 = str(VENV39 / "Scripts" / "python.exe")
    r = run([py39, "-m", "pip", "install", "-q", "--disable-pip-version-check",
             "numpy==1.26.4", "pandas==1.5.3", "scipy", "scikit-learn",
             "pyyaml", "requests", "loguru", "fire", "dill", "filelock",
             "ruamel.yaml", "tqdm", "joblib", "pyarrow"])
    print("py39 base rc =", r.returncode, flush=True)
    r = run([py39, "-m", "pip", "install", "-q", "--disable-pip-version-check",
             "--no-deps", str(ROOT / ".tmp_pip" / "pyqlib-0.9.7-cp39-cp39-win_amd64.whl")])
    print("pyqlib cp39 rc =", r.returncode, flush=True)

    # ---- 3. unpack cp312 wheel for py3.13 --------------------------------
    QLIB312.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(WHL) as z:
        z.extractall(QLIB312)
    print("unpacked cp312 ->", QLIB312)
    print("py313 =", py313)
    print("py39  =", py39)


if __name__ == "__main__":
    main()
