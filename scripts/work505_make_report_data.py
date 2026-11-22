#!/usr/bin/env python3
"""WORK-505 · rebuild report_data.json — the single machine-written source of
every number the report prints.

Nothing in this file is transcribed by hand: the environment-retest numbers
come from the raw per-run JSON of the WORK-500 C/D benchmark re-run, the
WORK-500 reference numbers come from its bench_medians.csv, and the snapshot
numbers come from the verification artifact.  The report generator only
formats what this file contains.

Usage (PowerShell):
  & 'F:\\quant_work505\\venv\\Scripts\\python.exe' scripts\\work505_make_report_data.py `
      --bench-dir 'F:\\quant_work500\\bench' `
      --w500-medians '<path to>\\bench_medians.csv' `
      --data-dir 'F:\\quant_work505\\data\\WORK-449'
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = "WORK-505"
EXP_DIR = os.path.join(REPO, "experiments", EXP)
BAND = (0.13 * 0.8, 0.13 * 1.2)          # 0.13 s +/- 20 %
FIGS = {
    "fig1": "figures/fig1_env_retest_C.png",
    "fig2": "figures/fig2_env_retest_D.png",
    "fig3": "figures/fig3_snapshot_recovery.png",
    "fig4": "figures/fig4_transport_channels.png",
    "fig5": "figures/fig5_execution_status.png",
    "fig6": "figures/fig6_w500_baseline.png",
}


def steady_median(epoch_seconds):
    """Per-run s/epoch: median of the steady-state epochs (warm-up dropped)."""
    tail = list(epoch_seconds[1:4]) if len(epoch_seconds) >= 4 else list(epoch_seconds)
    return statistics.median(tail)


def read_run(path):
    d = json.load(open(path, encoding="utf-8"))
    ep = d["epoch_seconds"]
    name = os.path.basename(path)
    seed = int(name.split("_s")[1].split("_")[0])
    group = "C" if name.startswith("C_") else "D"
    phase = "confirm" if "_c" in name else "first"
    return {
        "file": name,
        "epoch_seconds": ep,
        "steady_median": steady_median(ep),
        "peak_vram_mb": d.get("peak_vram_mb"),
        "n_params": d.get("n_params"),
        "torch_version": d.get("torch_version"),
        "group": group,
        "seed": seed,
        "phase": phase,
    }


def med(vals):
    return statistics.median(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-dir", default=r"F:\quant_work500\bench")
    ap.add_argument("--w500-medians", required=True,
                    help="WORK-500 bench_medians.csv (the reference table)")
    ap.add_argument("--data-dir", default=r"F:\quant_work505\data\WORK-449")
    args = ap.parse_args()

    runs = []
    for folder, phase in ((os.path.join(args.bench_dir, "raw_work505"), None),
                           (os.path.join(args.bench_dir, "raw_work505_confirm"), None)):
        for p in sorted(glob.glob(os.path.join(folder, "*.json"))):
            if os.path.basename(p) in ("retest_rows.json", "retest_summary.json",
                                       "confirm_rows.json"):
                continue
            r = read_run(p)
            r["phase"] = "confirm" if "confirm" in folder else "first"
            runs.append(r)

    c_runs = [r for r in runs if r["group"] == "C"]
    d_runs = [r for r in runs if r["group"] == "D"]
    c_first = [r for r in c_runs if r["phase"] == "first"]
    c_conf = [r for r in c_runs if r["phase"] == "confirm"]

    with open(args.w500_medians, encoding="utf-8-sig", newline="") as fh:
        w500 = [row for row in csv.DictReader(fh)]

    def ref(group, seed):
        for row in w500:
            if row["group"] == group and row["seed"] == str(seed):
                return float(row["sec_per_epoch_median"])
        return None

    def per_seed(rs):
        out = {}
        for s in sorted({r["seed"] for r in rs}):
            out[str(s)] = med([r["steady_median"] for r in rs if r["seed"] == s])
        return out

    c_all = [r["steady_median"] for r in c_runs]
    d_all = [r["steady_median"] for r in d_runs]
    c_med_all = med(c_all)
    c_med_conf = med([r["steady_median"] for r in c_conf])
    c_med_first = med([r["steady_median"] for r in c_first])
    d_med = med(d_all)
    d_ps = per_seed(d_runs)

    c_ref42, c_ref43 = ref("C", 42), ref("C", 43)
    d_ref42, d_ref43 = ref("D", 42), ref("D", 43)

    in_band = {k: bool(BAND[0] <= v <= BAND[1]) for k, v in
               (("all16", c_med_all), ("confirm10", c_med_conf),
                ("first6", c_med_first))}

    summary = {
        "C": {
            "n": len(c_all),
            "median": c_med_all,
            "min": min(c_all),
            "max": max(c_all),
            "per_seed": per_seed(c_runs),
            "confirm_median": c_med_conf,
            "confirm_n": len(c_conf),
            "first_median": c_med_first,
            "first_n": len(c_first),
            "work500_ref": {"42": c_ref42, "43": c_ref43},
            "band": [BAND[0], BAND[1]],
            "verdict_in_band": in_band["all16"],
            "verdict_in_band_by_aggregate": in_band,
            "median_definition": ("per run: median of the steady-state epochs "
                                   "(warm-up epoch dropped); per aggregate: "
                                   "median over the runs"),
        },
        "D": {
            "n": len(d_all),
            "median": d_med,
            "min": min(d_all),
            "max": max(d_all),
            "per_seed": d_ps,
            "work500_ref": {"42": d_ref42, "43": d_ref43},
            "rel_diff_pct_vs_ref": {
                "overall": (d_med - d_ref42) / d_ref42 * 100.0,
                **{str(s): (d_ps[str(s)] - (d_ref42 if s == 42 else d_ref43))
                   / (d_ref42 if s == 42 else d_ref43) * 100.0
                   for s in sorted(d_ps)},
            },
            "median_definition": ("per run: median of the steady-state epochs; "
                                   "per aggregate: median over the runs"),
        },
    }

    ver_path = os.path.join(EXP_DIR, "snapshot_verification.json")
    ver = json.load(open(ver_path, encoding="utf-8"))
    rows = ver["rows"]
    pass_rows = [r for r in rows if r.get("verdict") == "PASS"]
    missing = [r["code"] for r in rows if r.get("verdict") == "MISSING"]
    failed = [r["code"] for r in rows if r.get("verdict") == "FAIL"]

    landed = 0
    for r in pass_rows:
        p = os.path.join(args.data_dir, f"{r['code']}.csv")
        if os.path.exists(p):
            landed += os.path.getsize(p)

    out = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "generator": "scripts/work505_make_report_data.py",
        "w500_bench_medians": w500,
        "retest_runs": runs,
        "retest_summary": summary,
        "snapshot": {
            "counts": ver["counts"],
            "landed_bytes": landed,
            "landed_bytes_declared": ver["landed_bytes"],
            "data_dir": args.data_dir,
            "missing_codes": missing,
            "failed_codes": failed,
            "pass_rows": pass_rows,
            "sha256_convention": ver.get("expected_sha256_algorithm"),
        },
    }
    out.update(FIGS)

    path = os.path.join(EXP_DIR, "report_data.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("written:", path, os.path.getsize(path), "bytes")
    print("C  n=%d  all=%.4f  confirm10=%.4f  first6=%.4f  band=[%.4f, %.4f]"
          % (len(c_all), c_med_all, c_med_conf, c_med_first, BAND[0], BAND[1]))
    print("   in band by aggregate:", in_band)
    print("D  n=%d  median=%.4f  per_seed=%s  ref=%.4f  rel_diff=%.2f%%"
          % (len(d_all), d_med, {k: round(v, 4) for k, v in d_ps.items()},
             d_ref42, summary["D"]["rel_diff_pct_vs_ref"]["overall"]))
    print("snapshot pass=%d fail=%d missing=%d landed_bytes=%d"
          % (len(pass_rows), len(failed), len(missing), landed))


if __name__ == "__main__":
    main()
