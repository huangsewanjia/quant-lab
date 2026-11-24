#!/usr/bin/env python3
"""WORK-557 段2 数据链路 runner：适配层 -> 面板 -> qlib 二进制 -> 训练集。

四步串行，每步：
  * stdout/stderr 落到 experiments/WORK-557/logs/<step>.log（完整日志留证）
  * 单独计时，打印 **一行摘要**（不把上千行进度刷进上下文）
  * 任一步非零退出即停止并报出失败步

用法：
    .venv\\Scripts\\python.exe scripts/work557_pipeline.py            # 全跑
    .venv\\Scripts\\python.exe scripts/work557_pipeline.py --only fetch_panel
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXP = "WORK-557"
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

STEPS = [
    ("fetch_panel", [PY, "-u", "scripts/work557_fetch_panel.py", "--exp", EXP]),
    ("build_panel", [PY, "-u", "scripts/work503_build_panel.py", "--exp", EXP]),
    ("dump_qlib", [PY, "-u", "scripts/work503_dump_qlib.py", "--exp", EXP]),
    ("build_dataset", [PY, "-u", "scripts/work557_build_dataset.py", "--exp", EXP]),
]

LOG_DIR = os.path.join(REPO, "experiments", EXP, "logs")


def tail(path, n=25):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:])
    except Exception as exc:
        return "<log read failed: %s>" % exc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)

    for name, cmd in STEPS:
        if args.only and name != args.only:
            continue
        log_path = os.path.join(LOG_DIR, "%s.log" % name)
        print("=== STEP %s ===  %s" % (name, " ".join(cmd[1:])), flush=True)
        t0 = time.time()
        env = dict(os.environ)
        # qlib 会 import mlflow，每次 import 都刷一遍 agent hint，纯噪声
        env["MLFLOW_DISABLE_AGENT_HINT"] = "1"
        with open(log_path, "w", encoding="utf-8") as lf:
            lf.write("# %s  %s\n" % (name, time.strftime("%Y-%m-%d %H:%M:%S")))
            lf.flush()
            rc = subprocess.call(cmd, cwd=REPO, stdout=lf, stderr=subprocess.STDOUT,
                                 env=env)
        dt = time.time() - t0
        print("--- %s rc=%d %.1fs (%.1f min) log=%s"
              % (name, rc, dt, dt / 60, os.path.relpath(log_path, REPO)), flush=True)
        if rc != 0:
            print("!!! FAILED at step %s (rc=%d); tail of log:" % (name, rc), flush=True)
            print(tail(log_path, 40), flush=True)
            return rc
        print(tail(log_path, 12), flush=True)
    print("=== pipeline ok ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
