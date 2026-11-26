#!/usr/bin/env python
"""WORK-503 · same-seed reproducibility evidence.

Compares a formal training run against an independent re-run of the *same*
arm with the *same* seed (the re-run writes to a ``_repro`` suffixed file so
the official artefact is never touched), and records:

  * the sha256 of both files (byte-level evidence)
  * a value-level comparison (exact equality per cell)

Why both: parquet byte hashes can differ for byte-identical frames when the
writer metadata differs, so the byte hash is reported as *evidence*, while the
value-level comparison is the *verdict*.  Both are written out — neither is
silently dropped.

  python scripts/work503_repro_check.py \
      --pairs no_vq_s42:experiments/WORK-503/preds/no_vq_s42.parquet:experiments/WORK-503/preds/no_vq_s42_repro.parquet

Output:
  experiments/WORK-503/reproducibility_check.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import numpy as np
import pandas as pd


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_frames(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Exact cell-by-cell comparison of two prediction frames."""
    out: dict = {
        "rows_official": int(len(a)),
        "rows_repro": int(len(b)),
        "columns_official": list(a.columns),
        "columns_repro": list(b.columns),
        "columns_match": sorted(a.columns) == sorted(b.columns),
    }
    if not out["columns_match"] or len(a) != len(b):
        out["n_mismatched_cells"] = None
        out["max_abs_diff"] = None
        out["identical"] = False
        return out

    a = a[sorted(a.columns)].reset_index(drop=True)
    b = b[sorted(b.columns)].reset_index(drop=True)

    per_col = {}
    n_mis = 0
    max_abs = 0.0
    for c in a.columns:
        ca, cb = a[c], b[c]
        if pd.api.types.is_numeric_dtype(ca) and pd.api.types.is_numeric_dtype(cb):
            va = ca.to_numpy(dtype="float64", na_value=np.nan)
            vb = cb.to_numpy(dtype="float64", na_value=np.nan)
            both_nan = np.isnan(va) & np.isnan(vb)
            diff = np.where(both_nan, 0.0, np.abs(va - vb))
            diff = np.where(np.isnan(diff), np.inf, diff)
            mis = int((diff > 0).sum())
            md = float(diff.max()) if len(diff) else 0.0
        else:
            sa = ca.astype(str).to_numpy()
            sb = cb.astype(str).to_numpy()
            mis = int((sa != sb).sum())
            md = 0.0 if mis == 0 else float("inf")
        n_mis += mis
        max_abs = max(max_abs, md)
        per_col[c] = {"n_mismatched_cells": mis, "max_abs_diff": md}

    out["per_column"] = per_col
    out["n_mismatched_cells"] = n_mis
    out["max_abs_diff"] = max_abs
    out["identical"] = bool(n_mis == 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pairs",
        nargs="+",
        required=True,
        help="name:official_parquet:repro_parquet (repeatable)",
    )
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    out_path = args.out or os.path.join(exp_dir, "reproducibility_check.json")

    results = []
    for spec in args.pairs:
        parts = spec.split(":")
        if len(parts) != 3:
            raise SystemExit(f"bad --pairs spec: {spec!r} (want name:official:repro)")
        name, off_p, rep_p = parts
        off_p = os.path.abspath(off_p)
        rep_p = os.path.abspath(rep_p)
        rec: dict = {"name": name, "official": off_p, "repro": rep_p}
        if not os.path.exists(off_p) or not os.path.exists(rep_p):
            rec["status"] = "missing_file"
            rec["official_exists"] = os.path.exists(off_p)
            rec["repro_exists"] = os.path.exists(rep_p)
            results.append(rec)
            print(f"{name}: MISSING (official={rec['official_exists']}, repro={rec['repro_exists']})")
            continue
        rec["official_sha256"] = sha256(off_p)
        rec["repro_sha256"] = sha256(rep_p)
        rec["sha256_equal"] = rec["official_sha256"] == rec["repro_sha256"]
        a = pd.read_parquet(off_p)
        b = pd.read_parquet(rep_p)
        rec.update(compare_frames(a, b))
        rec["status"] = "pass" if rec["identical"] else "fail"
        results.append(rec)
        print(
            f"{name}: {rec['status']}  cells_mismatch={rec['n_mismatched_cells']}  "
            f"max_abs_diff={rec['max_abs_diff']}  sha256_equal={rec['sha256_equal']}"
        )

    n_pass = sum(1 for r in results if r.get("status") == "pass")
    payload = {
        "tool": "scripts/work503_repro_check.py",
        "exp": args.exp,
        "method": (
            "同一 arm、同一 seed、同一 commit 的两次独立训练；正式产物不被覆盖"
            "（重跑写 _repro 后缀）。字节 sha256 与逐格精确比较同时记录。"
        ),
        "n_pairs": len(results),
        "n_pass": n_pass,
        "all_pass": bool(results) and n_pass == len(results),
        "results": results,
    }
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"-> {out_path}")
    if not payload["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
