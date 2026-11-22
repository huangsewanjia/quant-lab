#!/usr/bin/env python3
"""WORK-503 stage 2 — fetch quarterly book value per share for the PIT universe.

Why: the paper's expert prior factors are the 13 JKP global factors, which
have no A-share per-stock equivalent.  The pre-registration replaces them
with a self-built A-share prior set (market / size / value / momentum /
volatility / reversal).  The VALUE leg needs a point-in-time fundamental.

Source: akshare `stock_financial_analysis_indicator` (新浪财经·财务指标),
quarterly 每股净资产_调整后 (BVPS, yuan, as reported, i.e. unadjusted terms
comparable with the raw close).

Point-in-time rule: a report period becomes usable 120 calendar days after
the period end (conservative assumption for the announcement date, which the
interface does not publish).  The effective date is written into the file so
the panel builder never needs to guess it.

Writes data/WORK-503/fundamentals/<code>.csv with columns
date, period_end, bvps, effective_date.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta

import akshare as ak

LAG_DAYS = 120
BVPS_COL = "每股净资产_调整后(元)"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_one(code: str, out_dir: str):
    sym = code.split(".")[-1]
    rec = {"code": code, "ok": False, "rows": 0, "error": "",
           "date_min": None, "date_max": None, "sha256": None}
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_financial_analysis_indicator(symbol=sym, start_year="2011")
            break
        except Exception as exc:                                # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"
            time.sleep(2.0 * (attempt + 1))
    if df is None or df.empty:
        if not rec["error"]:
            rec["error"] = "empty result"
        return rec
    if BVPS_COL not in df.columns:
        rec["error"] = f"column {BVPS_COL} missing"
        return rec
    sub = df[["日期", BVPS_COL]].copy()
    sub.columns = ["period_end", "bvps"]
    sub["period_end"] = pd_to_date(sub["period_end"])
    sub = sub.dropna(subset=["bvps"])
    sub = sub[sub["period_end"] <= "2024-12-31"]
    if sub.empty:
        rec["error"] = "no rows <= 2024-12-31"
        return rec
    sub["effective_date"] = sub["period_end"] + timedelta(days=LAG_DAYS)
    path = os.path.join(out_dir, f"{code}.csv")
    sub.to_csv(path, index=False)
    rec.update({"ok": True, "rows": int(len(sub)),
                "date_min": str(sub["period_end"].min().date()),
                "date_max": str(sub["period_end"].max().date()),
                "sha256": sha256_of(path), "error": ""})
    return rec


def pd_to_date(s):
    import pandas as pd
    return pd.to_datetime(s, errors="coerce")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    out_dir = os.path.join(data_dir, "fundamentals")
    os.makedirs(out_dir, exist_ok=True)

    codes = set()
    with open(os.path.join(exp_dir, "pool_membership.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            codes.add(r["code"])
    codes = sorted(codes)
    if args.limit:
        codes = codes[:args.limit]

    mpath = os.path.join(exp_dir, "snapshot_manifest_fundamentals.json")
    prev = {}
    if os.path.exists(mpath):
        for it in json.load(open(mpath)).get("items", []):
            prev[it["code"]] = it
    todo = [c for c in codes if not prev.get(c, {}).get("ok")]
    results = [prev[c] for c in codes if c in prev]
    print(f"{len(codes)} codes, {len(todo)} to fetch", flush=True)

    t0 = time.time()
    for i, code in enumerate(todo, 1):
        results.append(fetch_one(code, out_dir))
        if i % 50 == 0 or i == len(todo):
            bad = sum(1 for r in results if not r["ok"])
            el = time.time() - t0
            print(f"{i}/{len(todo)} fail={bad} {el:.0f}s "
                  f"eta={el / i * (len(todo) - i):.0f}s", flush=True)
        time.sleep(args.sleep)

    results.sort(key=lambda r: r["code"])
    manifest = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": "akshare stock_financial_analysis_indicator (新浪财经·财务指标)",
        "field": BVPS_COL,
        "units": {"bvps": "元"},
        "point_in_time_rule": (f"report period becomes usable {LAG_DAYS} "
                               "calendar days after the period end"),
        "items": results,
    }
    json.dump(manifest, open(mpath, "w"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["ok"])
    print(f"manifest -> {mpath}")
    print(f"ok={ok} fail={len(results) - ok} "
          f"rows={sum(r['rows'] for r in results)} elapsed={time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
