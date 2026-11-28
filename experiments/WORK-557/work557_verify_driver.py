"""核实运行驱动：阶段一（早停开）→ 若早停 ≤25 轮，阶段二（早停关，跑满 50 轮）。

墙钟纪律（派单 2026-11-28）：两段合计 ≤3 h。驱动自己在 2 h 50 min 处收手，
超时的那一段按「未完成」如实记录，不降轮数、不降窗。

由 schtasks 起（脱离 run 进程树），日志：
  experiments/WORK-557/verify/driver.log
结果：
  experiments/WORK-557/verify/verify_summary.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VER = os.path.join(REPO, "experiments", "WORK-557", "verify")
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
SCRIPT = os.path.join(REPO, "experiments", "WORK-557", "work557_verify_run.py")
T0 = time.time()
BUDGET_S = 3 * 3600
GUARD_S = int(2.83 * 3600)   # 2 h 50 min


def log(msg):
    line = f"[{time.time()-T0:8.1f}s] {msg}"
    print(line, flush=True)
    with open(os.path.join(VER, "driver.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def nvidia():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=60)
        return r.stdout.strip()
    except Exception as exc:
        return f"nvidia-smi failed: {exc}"


def run_phase(phase, timeout_s):
    cmd = [PY, "-u", SCRIPT, "--phase", phase, "--seed", "42", "--workers", "2",
           "--max-epochs", "50"]
    log(f"START {phase}: {' '.join(cmd)}")
    t = time.time()
    try:
        rc = subprocess.call(cmd, cwd=REPO, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        rc = -9
        log(f"TIMEOUT {phase} after {timeout_s}s -> killed")
    dt = time.time() - t
    log(f"END {phase}: rc={rc} in {dt:.0f}s ({dt/60:.1f} min)")
    j = os.path.join(VER, f"verify_{phase}_s42.json")
    data = None
    if os.path.exists(j):
        with open(j, encoding="utf-8") as fh:
            data = json.load(fh)
    return {"phase": phase, "rc": rc, "seconds": round(dt, 1), "result": data}


def main():
    os.makedirs(VER, exist_ok=True)
    log("nvidia-smi before: " + nvidia())
    summary = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "phases": []}

    remain = GUARD_S - (time.time() - T0)
    p1 = run_phase("early", max(600, int(min(remain, 5400))))
    summary["phases"].append(p1)

    r1 = p1.get("result") or {}
    ran = int(r1.get("n_epochs_ran") or 0)
    es_epoch = r1.get("early_stopping_fired_epoch")
    need_full = bool(ran and ran <= 25)
    log(f"phase early: ran={ran} early_stop_at={es_epoch} -> need_full50={need_full}")

    if need_full:
        remain = GUARD_S - (time.time() - T0)
        if remain < 3600:
            log(f"SKIP full50: remaining budget {remain:.0f}s < 3600s (would breach 3 h)")
            summary["full50_skipped_reason"] = f"剩余预算 {remain:.0f}s 不足 3600s，按 3 h 上限不启动"
        else:
            p2 = run_phase("full50", max(600, int(min(remain, 7200))))
            summary["phases"].append(p2)
    else:
        summary["full50_skipped_reason"] = (
            f"早停未在 25 轮内触发（实际 {ran} 轮），50 轮口径由本次同一 run 直接给出")

    summary["total_seconds"] = round(time.time() - T0, 1)
    summary["nvidia_after"] = nvidia()
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(os.path.join(VER, "verify_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    log(f"DONE total={summary['total_seconds']:.0f}s ({summary['total_seconds']/60:.1f} min)")
    log("nvidia-smi after: " + summary["nvidia_after"])


if __name__ == "__main__":
    main()
