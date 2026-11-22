#!/usr/bin/env python3
"""WORK-505 · snapshot verification: compare the landed WORK-449 snapshot files
against the frozen per-stock sha256 list in experiments/WORK-449/snapshot_manifest.json.

Output:
  experiments/WORK-505/snapshot_verification.json  (full 680-row record)
  experiments/WORK-505/snapshot_verification.csv    (680 rows, flat)

Each row records: expected sha256 (frozen manifest), actual sha256 of the landed
file (null when the file is absent), byte size, row count, date range and the
PASS/FAIL/MISSING verdict.  Nothing is estimated: a file that could not be
recovered is reported as MISSING, never as a value.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def sha256_of(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def csv_shape(path: str):
    """(rows, date_min, date_max) read from the landed file, or (None, None, None)."""
    if not os.path.exists(path):
        return None, None, None
    n, dmin, dmax = 0, None, None
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        rdr = csv.reader(fh)
        header = next(rdr, None)
        if header is None or not header or header[0] != "date":
            return None, None, None
        for row in rdr:
            if not row:
                continue
            n += 1
            if dmin is None:
                dmin = row[0]
            dmax = row[0]
    return n, dmin, dmax


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-505")
    ap.add_argument("--source-exp", default="WORK-449")
    ap.add_argument("--data-dir", default=r"F:\quant_work505\data\WORK-449")
    args = ap.parse_args()

    exp_dir = os.path.join(REPO, "experiments", args.exp)
    os.makedirs(exp_dir, exist_ok=True)
    man_path = os.path.join(REPO, "experiments", args.source_exp,
                            "snapshot_manifest.json")
    manifest = json.load(open(man_path, encoding="utf-8"))
    items = manifest["items"]

    rows, n_pass, n_fail, n_missing = [], 0, 0, 0
    for it in items:
        code = it["code"]
        path = os.path.join(args.data_dir, f"{code}.csv")
        actual = sha256_of(path)
        n, dmin, dmax = csv_shape(path)
        if actual is None:
            verdict, n_missing = "MISSING", n_missing + 1
        elif actual == it["sha256"]:
            verdict, n_pass = "PASS", n_pass + 1
        else:
            verdict, n_fail = "FAIL", n_fail + 1
        rows.append({
            "code": code,
            "verdict": verdict,
            "expected_sha256": it["sha256"],
            "actual_sha256": actual,
            "expected_rows": it["rows"],
            "actual_rows": n,
            "expected_date_min": it["date_min"],
            "actual_date_min": dmin,
            "expected_date_max": it["date_max"],
            "actual_date_max": dmax,
            "size_bytes": os.path.getsize(path) if actual else None,
            "local_path": path if actual else None,
        })

    out = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_experiment": args.source_exp,
        "source_manifest": os.path.relpath(man_path, REPO).replace("\\", "/"),
        "source_manifest_generated_at": manifest.get("generated_at"),
        "data_dir": args.data_dir,
        "expected_sha256_algorithm": "sha256(file bytes) — same convention as "
                                     "scripts/work449_fetch.py:sha256_of()",
        "counts": {
            "manifest_items": len(items),
            "pass": n_pass,
            "fail": n_fail,
            "missing": n_missing,
        },
        "landed_bytes": sum(r["size_bytes"] or 0 for r in rows),
        "rows": rows,
    }
    jp = os.path.join(exp_dir, "snapshot_verification.json")
    json.dump(out, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    cp = os.path.join(exp_dir, "snapshot_verification.csv")
    cols = list(rows[0].keys())
    with open(cp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"manifest items   : {len(items)}")
    print(f"PASS (sha256 ==) : {n_pass}")
    print(f"FAIL (sha !=)    : {n_fail}")
    print(f"MISSING (no file): {n_missing}")
    print(f"landed bytes     : {out['landed_bytes']}")
    print("->", jp)
    print("->", cp)
    if n_missing:
        print("\nMISSING codes (first 40):")
        print(" ".join(r["code"] for r in rows if r["verdict"] == "MISSING")[:2000])


if __name__ == "__main__":
    main()
