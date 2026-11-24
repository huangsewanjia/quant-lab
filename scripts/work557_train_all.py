#!/usr/bin/env python3
"""WORK-557 段2c：四档消融 × 2 seed + GRU 基线的训练驱动（GPU）。

* 每个 arm/seed 单独起一个进程（跑完显存完全释放，不串味）；
* **顺序**按 seed 外层、arm 内层，且 `full` 先跑 —— stage-1 的 VQ-VAE 权重
  按上游消融脚本的做法「同一个 seed 的三档共用」，`work557_run_arms.py` 会复用
  `vendor/PRISM-VQ/checkpoints/stage1_s<seed>.pt`；
* **断点续跑**：若 `experiments/WORK-557/preds/<arm>_s<seed>.json` 已存在就跳过；
* 每次跑之前检查 GPU 显存占用（派单卡：本单不得与 WORK-538 并行占 GPU）；
* 汇总每个 run 的 s/epoch、峰值显存、早停轮次、训练总秒数到
  `experiments/WORK-557/training_log.json`。

用法：
  .venv\\Scripts\\python scripts/work557_train_all.py --smoke          # 1 轮冒烟（对齐设备）
  .venv\\Scripts\\python scripts/work557_train_all.py --epochs 50       # 正式
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = "WORK-557"
ARMS = ["full", "no_vq", "no_moe", "no_prior"]
SEEDS = [42, 2024]
VENV_PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PY):
    VENV_PY = sys.executable

# 派单卡：本单不得与 WORK-538 并行占 GPU；显存占用 > 3 GB 就等。
GPU_FREE_MB = 3000


def gpu_used_mb():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=60)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0


def wait_for_gpu(timeout_s=3600, label=""):
    t0 = time.time()
    while True:
        used = gpu_used_mb()
        if used <= GPU_FREE_MB:
            print("[gpu] %d MiB in use -> start %s" % (used, label), flush=True)
            return used
        wait = 60
        print("[gpu] %d MiB in use (>%d) -> waiting %ds %s"
              % (used, GPU_FREE_MB, wait, label), flush=True)
        if time.time() - t0 > timeout_s:
            raise SystemExit("[gpu] waited %ds and the GPU is still busy (%d MiB)"
                             % (time.time() - t0, used))
        time.sleep(wait)


def run_one(cmd, label, log_path, timeout_s=None):
    print("\n=== %s ===" % label, flush=True)
    print(" ".join(cmd), flush=True)
    t0 = time.time()
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write("\n\n===== %s  (%s) =====\n" % (label, time.strftime("%Y-%m-%d %H:%M:%S")))
        lf.flush()
        try:
            p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=REPO,
                               timeout=timeout_s)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = -9
            lf.write("\n### TIMEOUT after %.0fs -> killed ###\n" % (timeout_s or 0))
            lf.flush()
            print("!!! %s exceeded %ss -> KILLED (protocol stop condition)"
                  % (label, timeout_s), flush=True)
    dt = time.time() - t0
    print("=== %s done rc=%d in %.1fs (%.1f min) ==="
          % (label, rc, dt, dt / 60), flush=True)
    return rc, dt


def collect(exp_dir):
    """把每个 run 的日志文件收成 training_log.json。"""
    pd_dir = os.path.join(exp_dir, "preds")
    runs = {}
    if os.path.isdir(pd_dir):
        for fn in sorted(os.listdir(pd_dir)):
            if not fn.endswith(".json") or "_s" not in fn:
                continue
            try:
                r = json.load(open(os.path.join(pd_dir, fn), encoding="utf-8"))
            except Exception:
                continue
            if "epoch_history" not in r and "valid_loss" not in r:
                continue
            key = fn[:-5]
            hist = r.get("epoch_history") or []
            secs = [h.get("seconds") for h in hist if h.get("seconds")]
            runs[key] = {
                "arm": r.get("arm", key.rsplit("_s", 1)[0]),
                "seed": r.get("seed", key.rsplit("_s", 1)[-1]),
                "epochs_ran": r.get("epochs_ran"),
                "stage1_epochs_ran": r.get("stage1_epochs_ran"),
                "early_stopped_epoch": r.get("early_stopped_epoch"),
                "stage1_early_stopped_epoch": r.get("stage1_early_stopped_epoch"),
                "mean_seconds_per_epoch": (sum(secs) / len(secs)) if secs else None,
                "total_seconds": r.get("total_seconds") or r.get("seconds"),
                "stage1_seconds": r.get("stage1_seconds"),
                "stage2_seconds": r.get("stage2_seconds"),
                "peak_vram_gb": r.get("peak_vram_gb"),
                "device": r.get("device"),
                "rankic": (r.get("metrics") or {}).get("rankic"),
                "rankicir": (r.get("metrics") or {}).get("rankicir"),
                "valid_loss": r.get("valid_loss"),
                "epoch_history": hist,
            }
    out = {"experiment": EXP,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "gpu": {}, "runs": runs}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                            "--format=csv,noheader"], capture_output=True, text=True,
                           timeout=60)
        out["gpu"]["spec"] = r.stdout.strip()
    except Exception:
        pass
    try:
        import torch
        out["gpu"]["torch"] = torch.__version__
        out["gpu"]["cuda"] = torch.version.cuda
        out["gpu"]["device"] = (torch.cuda.get_device_name(0)
                                if torch.cuda.is_available() else None)
    except Exception:
        pass
    path = os.path.join(exp_dir, "training_log.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default=EXP)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--smoke", action="store_true",
                    help="1 epoch per run: device-alignment + throughput probe")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    ap.add_argument("--skip-gru", action="store_true")
    ap.add_argument("--no-wait-gpu", action="store_true")
    ap.add_argument("--gru-first", action="store_true",
                    help="先跑 GRU 基线再跑四档：H2 必需且最便宜，先保底")
    ap.add_argument("--max-run-seconds", type=float, default=7200.0,
                    help="单档训练墙钟上限（秒）。派单停手条件③：单档 >2h 即停并回报实测；"
                         "超时会杀掉子进程并停止后续档位。0 = 不限")
    args = ap.parse_args()

    repo = REPO
    exp_dir = os.path.join(repo, "experiments", args.exp)
    os.makedirs(os.path.join(exp_dir, "preds"), exist_ok=True)
    log_path = os.path.join(exp_dir, "training_log.txt")
    epochs = 1 if args.smoke else args.epochs
    arms = [a for a in args.arms.split(",") if a]
    seeds = [int(s) for s in args.seeds.split(",") if s]
    suffix = "_smoke" if args.smoke else ""

    t0 = time.time()
    failures = []
    stopped = None          # 触发停手条件时的说明
    cap = args.max_run_seconds if args.max_run_seconds and args.max_run_seconds > 0 else None

    def run_arm(arm, seed):
        nonlocal stopped
        out_json = os.path.join(exp_dir, "preds", "%s_s%s%s.json"
                                % (arm, seed, suffix))
        if os.path.exists(out_json) and not args.smoke:
            print("skip (exists): %s_s%s" % (arm, seed), flush=True)
            return
        if not args.no_wait_gpu:
            wait_for_gpu(label="%s seed %s" % (arm, seed))
        cmd = [VENV_PY, "-u", os.path.join("scripts", "work557_run_arms.py"),
               "--arm", arm, "--seed", str(seed), "--epochs", str(epochs),
               "--exp", args.exp, "--workers", str(args.workers),
               "--out-suffix", suffix]
        rc, dt = run_one(cmd, "%s seed %s (%d epochs)%s"
                         % (arm, seed, epochs, " SMOKE" if args.smoke else ""),
                         log_path, timeout_s=cap)
        if rc == -9:
            stopped = ("stop condition 3 (single run > %.0fs): %s seed %s killed"
                       % (cap or 0, arm, seed))
            failures.append({"arm": arm, "seed": seed, "rc": rc, "reason": "timeout"})
        elif rc != 0:
            failures.append({"arm": arm, "seed": seed, "rc": rc})
            print("!! %s seed %s FAILED rc=%d" % (arm, seed, rc), flush=True)

    def run_gru(seed):
        nonlocal stopped
        out_json = os.path.join(exp_dir, "preds", "gru_s%s%s.json"
                                % (seed, suffix))
        if os.path.exists(out_json) and not args.smoke:
            print("skip (exists): gru seed %s" % seed, flush=True)
            return
        if not args.no_wait_gpu:
            wait_for_gpu(label="gru seed %s" % seed)
        cmd = [VENV_PY, "-u", os.path.join("scripts", "work557_baseline.py"),
               "--kind", "gru", "--seed", str(seed), "--epochs", str(epochs),
               "--exp", args.exp]
        rc, dt = run_one(cmd, "gru seed %s (%d epochs)%s"
                         % (seed, epochs, " SMOKE" if args.smoke else ""),
                         log_path, timeout_s=cap)
        if rc == -9:
            stopped = ("stop condition 3 (single run > %.0fs): gru seed %s killed"
                       % (cap or 0, seed))
            failures.append({"arm": "gru", "seed": seed, "rc": rc, "reason": "timeout"})
        elif rc != 0:
            failures.append({"arm": "gru", "seed": seed, "rc": rc})

    def arms_loop():
        for seed in seeds:
            for arm in arms:
                run_arm(arm, seed)
                if stopped:
                    return

    def gru_loop():
        for seed in seeds:
            run_gru(seed)
            if stopped:
                return

    # 顺序：默认「四档 -> GRU」；--gru-first 则「GRU -> 四档」
    # （GRU 最便宜且 H2 必需，先跑可在被停手时保住 H2 的基线）
    if args.gru_first and not args.skip_gru:
        gru_loop()
    if not stopped:
        arms_loop()
    if not args.skip_gru and not args.gru_first and not stopped:
        gru_loop()

    out = collect(exp_dir)
    out["failures"] = failures
    out["stopped"] = stopped
    out["max_run_seconds"] = args.max_run_seconds
    out["wall_seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(exp_dir, "training_log.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\n=== training driver done in %.1f min; failures=%s; stopped=%s ==="
          % (out["wall_seconds"] / 60, failures, stopped), flush=True)
    for k, r in sorted(out["runs"].items()):
        print("  %-18s epochs=%-4s s/epoch=%-7s peakVRAM=%-6s rankic=%s"
              % (k, r["epochs_ran"],
                 ("%.1f" % r["mean_seconds_per_epoch"])
                 if r["mean_seconds_per_epoch"] else "-",
                 r["peak_vram_gb"], r["rankic"]), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
