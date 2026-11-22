#!/usr/bin/env python3
"""WORK-505 · end-to-end smoke grid on the synthetic panel.

Runs the FULL protocol shape (3 arms x 2 seeds x 2 models) plus the two-seed
merged primary test, so that the delivered chain -- train -> WORK-449's
backtest -> primary test -- is executed for every cell the preregistration
asks for.

Nothing produced here is a research result: the panel is synthetic.

Usage (must run with the venv interpreter, see failure_log §3.2):
  & 'F:\\quant_work505\\venv\\Scripts\\python.exe' scripts\\work505_run_smoke_grid.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PY = sys.executable
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = "WORK-505-SMOKE"
ARMS = ("no_mask", "halt_only", "full_mask")
SEEDS = (42, 43)
OUT = os.path.join(REPO, "experiments", EXP, "smoke_grid_log.json")
STEPS = []


def run(label, args):
    t = time.time()
    p = subprocess.run([PY] + args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    dt = time.time() - t
    rec = {"label": label, "args": args, "returncode": p.returncode,
           "seconds": round(dt, 1)}
    STEPS.append(rec)
    print(f"[{p.returncode}] {dt:6.1f}s  {label}", flush=True)
    if p.returncode != 0:
        print("--- stdout tail ---")
        print(p.stdout[-3000:])
        print("--- stderr tail ---")
        print(p.stderr[-4000:])
        json.dump(STEPS, open(OUT, "w"), indent=1)
        raise SystemExit(f"FAILED: {label}")
    tail = [ln for ln in p.stdout.strip().splitlines() if ln.strip()][-3:]
    for ln in tail:
        print("      " + ln[:200], flush=True)
    return p.stdout


# 0) panel (starts 2014-01-02, i.e. it has a pre-training segment)
run("panel", [os.path.join("scripts", "work505_make_smoke_panel.py")])

# 1) Transformer: 3 arms x 2 seeds (--tag defaults to _<seed>)
for arm in ARMS:
    for seed in SEEDS:
        run(f"transformer/{arm}/s{seed}",
            [os.path.join("scripts", "work505_train_transformer.py"),
             "--exp", EXP, "--arm", arm, "--seed", str(seed),
             "--epochs", "3", "--device", "cuda"])
        run(f"backtest/{arm}/s{seed}",
            [os.path.join("scripts", "work449_backtest.py"),
             "--exp", EXP, "--arm", arm, "--tag", f"_{seed}"])

# 2) MLP reference (WORK-449's own trainer, untouched): 3 arms x 2 seeds
for arm in ARMS:
    for seed in SEEDS:
        run(f"mlp/{arm}/s{seed}",
            [os.path.join("scripts", "work449_train.py"),
             "--exp", EXP, "--arm", arm, "--seed", str(seed),
             "--tag", f"_mlp{seed}"])
        run(f"backtest_mlp/{arm}/s{seed}",
            [os.path.join("scripts", "work449_backtest.py"),
             "--exp", EXP, "--arm", arm, "--tag", f"_mlp{seed}"])

# 3) primary test (two-seed merged verdict)
run("primary_test",
    [os.path.join("scripts", "work505_primary_test.py"), "--exp", EXP,
     "--seeds", "42,43"])

json.dump(STEPS, open(OUT, "w"), indent=1)
print("ALL SMOKE STEPS OK ->", OUT)
