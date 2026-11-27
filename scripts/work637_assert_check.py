#!/usr/bin/env python3
"""WORK-637 — provocation runs proving the engine assertions are live.

Three artifacts, each holding the verbatim exception text:

* ``assertion_violation_mask.txt``  — backtest refuses to run with masks disabled
* ``assertion_violation_lookahead.txt`` — signal engine refuses a library entry
  whose label is not yet realised (produced by injecting the entry)
* ``assertion_violation_window.txt`` — renderer refuses an incomplete window

    ../.venv/bin/python -u scripts/work637_assert_check.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/scripts")
import work637_common as C
import work637_backtest as B

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def record(name: str, text: str) -> None:
    p = os.path.join(C.EXP, name)
    with open(p, "w") as f:
        f.write(text.rstrip() + "\n")
    print(f"wrote {p}")


def main():
    os.makedirs(C.EXP, exist_ok=True)
    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    px, _, _ = B.exit_prices(panel, elig)
    r = B.weekly_returns(panel, elig, px)
    S = np.zeros_like(r)
    try:
        B.run(S, elig["eligible"], r, elig["eligible"], 12.0, masks_on=False, seed=42)
        raise SystemExit("mask assertion did not fire")
    except B.MaskDisabled as exc:
        record("assertion_violation_mask.txt", f"MaskDisabled: {exc}")

    # look-ahead: the signal engine is invoked with a library frontier pushed
    # 20 rows past the legal point; it must raise.
    r1 = subprocess.run([PY, os.path.join(HERE, "work637_signal.py"), "--seed", "42",
                         "--frontier-offset", "20", "--smoke-weeks", "8"],
                        capture_output=True, text=True)
    txt = (r1.stderr or "") + (r1.stdout or "")
    if "look-ahead violation" not in txt:
        raise SystemExit("look-ahead assertion did not fire:\n" + txt[-2000:])
    lines = [ln for ln in txt.splitlines() if "look-ahead violation" in ln]
    record("assertion_violation_lookahead.txt",
           f"frontier-offset=20 injection\n{r1.returncode}\n" + "\n".join(lines))

    # renderer window check
    import work637_render as R
    R._worker_init(panel, C.PAD_BY_SEED[42], 42)
    try:
        # a week whose window is incomplete for every stock does not exist in the
        # chartable set, so provoke the same guard with a hole-punched window
        p = dict(panel)
        p["ma"] = panel["ma"].copy()
        p["ma"][-1, 0] = np.nan
        R._G["panel"] = p
        w = len(panel["week_idx"]) - 1
        R._render((w, 0, "/tmp/_w637_probe.png"))
        raise SystemExit("window assertion did not fire")
    except RuntimeError as exc:
        record("assertion_violation_window.txt", f"RuntimeError: {exc}")


if __name__ == "__main__":
    main()
