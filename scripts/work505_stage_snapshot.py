#!/usr/bin/env python3
"""WORK-505 · stage a sha256-verified snapshot into the panel-builder input directory.

Why this exists
---------------
``scripts/work449_build_panel.py`` (WORK-449, frozen, not modified by WORK-505)
resolves its input directory as ``<repo>/data/<exp>`` -- hard-coded, no
``--data-dir`` option.  The preregistration's step-0 verification command
(``work505_verify_snapshot.py --data-dir F:\\quant_work505\\data\\WORK-449``)
points at a *different* directory.  Copying the 680 verified CSV files into
``<repo>/data/<exp>`` is therefore the missing bridge that makes the frozen
command table runnable end to end (preregistration errata E8).

What this script does -- and does not do
-----------------------------------------
* It only copies a file when the file's sha256 **equals** the sha256 frozen in
  ``experiments/WORK-449/snapshot_manifest.json``.  A mismatching file is never
  copied and never overwritten; it is reported as FAIL.
* A file that is absent from the source directory is reported as MISSING.  No
  placeholder, no estimate, no synthetic row.
* It never writes into the source directory and never touches
  ``experiments/WORK-449/``.
* ``<repo>/data/*`` is git-ignored, so the staged CSVs are local run inputs and
  are not part of the committed tree.

Output
------
  experiments/WORK-505/snapshot_staging.json

Exit codes
----------
  0  every file found in the source directory passed the frozen sha256 check
  2  at least one present file failed the sha256 check (corrupt source)
  3  bad usage (source dir missing)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage a sha256-verified snapshot into <repo>/data/<exp>/")
    ap.add_argument("--exp", default="WORK-505",
                    help="experiment id; the staged dir is <repo>/data/<exp>")
    ap.add_argument("--source-dir", default=r"F:\quant_work505\data\WORK-449",
                    help="directory holding the CSVs verified by "
                         "work505_verify_snapshot.py")
    ap.add_argument("--source-exp", default="WORK-449",
                    help="experiment whose snapshot_manifest.json holds the "
                         "frozen per-stock sha256 list")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be copied, copy nothing")
    ap.add_argument("--out", default=None,
                    help="output JSON path (default "
                         "experiments/<exp>/snapshot_staging.json)")
    args = ap.parse_args()

    src_dir = os.path.abspath(args.source_dir)
    if not os.path.isdir(src_dir):
        print(f"[work505_stage_snapshot] source dir not found: {src_dir}")
        return 3

    man_path = os.path.join(REPO, "experiments", args.source_exp,
                            "snapshot_manifest.json")
    if not os.path.exists(man_path):
        print(f"[work505_stage_snapshot] manifest not found: {man_path}")
        return 3
    manifest = json.load(open(man_path, encoding="utf-8"))
    items = manifest["items"]

    dest_dir = os.path.join(REPO, "data", args.exp)
    out_path = args.out or os.path.join(REPO, "experiments", args.exp,
                                        "snapshot_staging.json")

    rows, n_copied, n_pass, n_fail, n_missing, n_already = [], 0, 0, 0, 0, 0
    if not args.dry_run:
        os.makedirs(dest_dir, exist_ok=True)

    for it in items:
        code = it["code"]
        exp_sha = it["sha256"]
        src = os.path.join(src_dir, f"{code}.csv")
        dst = os.path.join(dest_dir, f"{code}.csv")

        if not os.path.exists(src):
            verdict = "MISSING"
            n_missing += 1
            rows.append({"code": code, "verdict": verdict,
                         "expected_sha256": exp_sha, "actual_sha256": None,
                         "source_path": src, "staged_path": None})
            continue

        actual = sha256_of(src)
        if actual != exp_sha:
            verdict = "FAIL"
            n_fail += 1
            rows.append({"code": code, "verdict": verdict,
                         "expected_sha256": exp_sha, "actual_sha256": actual,
                         "source_path": src, "staged_path": None})
            continue

        n_pass += 1
        dst_sha = sha256_of(dst) if os.path.exists(dst) else None
        if dst_sha == exp_sha:
            verdict = "ALREADY_STAGED"
            n_already += 1
        elif args.dry_run:
            verdict = "WOULD_COPY"
        else:
            tmp = dst + ".tmp"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            verdict = "COPIED"
            n_copied += 1

        rows.append({"code": code, "verdict": verdict,
                     "expected_sha256": exp_sha, "actual_sha256": actual,
                     "source_path": src, "staged_path": dst})

    out = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_dir": src_dir,
        "dest_dir": dest_dir,
        "source_manifest": os.path.relpath(man_path, REPO).replace("\\", "/"),
        "source_manifest_generated_at": manifest.get("generated_at"),
        "dry_run": bool(args.dry_run),
        "n_expected": len(items),
        "n_pass": n_pass,
        "n_copied": n_copied,
        "n_already_staged": n_already,
        "n_fail": n_fail,
        "n_missing": n_missing,
        "complete": (n_fail == 0 and n_missing == 0 and n_pass == len(items)),
        "note": ("complete=true means all 680 frozen files were present and "
                 "passed their sha256 check; the panel builder may then be "
                 "run on <repo>/data/<exp>."),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(f"[work505_stage_snapshot] expected={len(items)} pass={n_pass} "
          f"copied={n_copied} already={n_already} fail={n_fail} "
          f"missing={n_missing} complete={out['complete']}")
    print(f"[work505_stage_snapshot] dest={dest_dir}")
    print(f"[work505_stage_snapshot] out={out_path}")
    if n_fail:
        print("[work505_stage_snapshot] ABORT: at least one present file "
              "failed the frozen sha256 check; it was not copied.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
