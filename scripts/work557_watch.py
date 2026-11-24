#!/usr/bin/env python3
"""WORK-557 训练监控：sleep N 秒后打印一行状态，供后台轮询用。

只打印**一行**摘要，避免把训练日志刷进上下文。

用法：
    .venv\\Scripts\\python.exe scripts/work557_watch.py 600
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXP = "WORK-557"
EXP_DIR = os.path.join(REPO, "experiments", EXP)


def main():
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    time.sleep(wait)
    preds = os.path.join(EXP_DIR, "preds")
    done = sorted(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(preds, "*.json")))
    # 正在跑的：看 training_log.txt 最后一个 "=== xxx ===" 标记
    running = ""
    log = os.path.join(EXP_DIR, "training_log.txt")
    if os.path.exists(log):
        try:
            with open(log, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            for line in reversed(txt.splitlines()):
                if line.startswith("=== ") and "done rc=" not in line:
                    running = line.strip()[:90]
                    break
        except Exception:
            pass
    last = ""
    lj = os.path.join(EXP_DIR, "training_log.json")
    if os.path.exists(lj):
        try:
            d = json.load(open(lj, encoding="utf-8"))
            runs = d.get("runs", {})
            if runs:
                k = sorted(runs)[-1]
                r = runs[k]
                last = "%s: epochs=%s s/epoch=%s peakVRAM=%s" % (
                    k, r.get("epochs_ran"), r.get("mean_seconds_per_epoch"),
                    r.get("peak_vram_gb"))
        except Exception:
            pass
    print("[watch] done=%d %s | running=%s | last=%s"
          % (len(done), ",".join(done), running or "-", last or "-"), flush=True)


if __name__ == "__main__":
    main()
