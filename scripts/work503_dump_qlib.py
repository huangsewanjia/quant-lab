#!/usr/bin/env python3
"""WORK-503 stage 2 — dump the Sina panel into Qlib binary format.

Why: the paper (arXiv:2605.13407) builds its dataset with Qlib's
`Alpha158` handler and `Ref($close,-k)` label expressions, and its backtest
uses Qlib's `TopkDropoutStrategy`.  Reusing those official components needs
the data in Qlib's on-disk layout, which this script writes:

  <out>/calendars/day.txt            one trading day per line
  <out>/instruments/all.txt          SYMBOL<TAB>start<TAB>end
  <out>/features/<sym>/<field>.day.bin
        float32 little-endian; first float32 = start index into the
        calendar, then one float32 per calendar day from that index on
        (Qlib's FileStorage format, qlib/data/storage/file_storage.py).

Prices written are 后复权 (back-adjusted) so that `$close` is an adjusted
price and dividend drops do not show up as returns; `factor` holds the
adjustment ratio hfq/raw.  `vwap` = (amount / volume) * factor, the field
Qlib's Alpha158 "price" group needs.

Raw (unadjusted) prices stay in data/WORK-503/sina/ and are used for the
涨跌停 mask and for the tradability rules (mask_contract.md).

Point-in-time membership (pool_membership.csv) is NOT used to build the
feature files; it is applied when the sample index is built, so that a stock
is only a sample while it is actually in CSI 300.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

FIELDS = ("open", "close", "high", "low", "volume", "factor", "vwap", "amount")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_qlib_symbol(code: str) -> str:
    """sh.600000 -> SH600000 ; sz.300750 -> SZ300750"""
    mkt, num = code.split(".")
    return f"{mkt.upper()}{num}"


def write_bin(path: str, start_index: int, values: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        np.hstack([start_index, values]).astype("<f").tofile(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--out", default="qlib_cn")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    out_dir = os.path.join(data_dir, args.out)
    os.makedirs(os.path.join(out_dir, "calendars"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "instruments"), exist_ok=True)

    # ---- calendar: the CSI300 index series is the exchange trading calendar
    idx = pd.read_csv(os.path.join(data_dir, "index_sh000300.csv"))
    cal = sorted({str(d)[:10] for d in idx["date"]})
    with open(os.path.join(out_dir, "calendars", "day.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(cal) + "\n")
    cal_index = {d: i for i, d in enumerate(cal)}
    print(f"calendar: {len(cal)} days {cal[0]} .. {cal[-1]}", flush=True)

    # ---- instruments
    codes = set()
    with open(os.path.join(exp_dir, "pool_membership.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            codes.add(r["code"])
    codes = sorted(codes)

    raw_dir = os.path.join(data_dir, "sina")
    hfq_dir = os.path.join(data_dir, "sina_hfq")
    items, ok, fail = [], 0, []
    for code in codes:
        sym = to_qlib_symbol(code)
        p_raw = os.path.join(raw_dir, f"{code}.csv")
        p_hfq = os.path.join(hfq_dir, f"{code}.csv")
        if not (os.path.exists(p_raw) and os.path.exists(p_hfq)):
            fail.append({"code": code, "error": "missing sina csv"})
            continue
        raw = pd.read_csv(p_raw)
        hfq = pd.read_csv(p_hfq)
        raw["date"] = raw["date"].astype(str).str[:10]
        hfq["date"] = hfq["date"].astype(str).str[:10]
        df = raw.merge(hfq, on="date", suffixes=("_raw", "_hfq"))
        df = df[df["date"].isin(cal_index)].sort_values("date").reset_index(drop=True)
        if df.empty:
            fail.append({"code": code, "error": "no rows on calendar"})
            continue
        start_index = cal_index[df["date"].iloc[0]]
        n_cal = len(cal) - start_index
        factor = (df["close_hfq"] / df["close_raw"]).astype("float64")
        vwap = (df["amount_raw"] / df["volume_raw"].replace(0, np.nan)) * factor
        series = {
            "open": df["open_hfq"].to_numpy("float64"),
            "close": df["close_hfq"].to_numpy("float64"),
            "high": df["high_hfq"].to_numpy("float64"),
            "low": df["low_hfq"].to_numpy("float64"),
            "volume": df["volume_raw"].to_numpy("float64"),
            "factor": factor.to_numpy("float64"),
            "vwap": vwap.to_numpy("float64"),
            "amount": df["amount_raw"].to_numpy("float64"),
        }
        for name, vals in series.items():
            arr = np.full(n_cal, np.nan, dtype="float64")
            arr[: len(vals)] = vals
            write_bin(os.path.join(out_dir, "features", sym.lower(),
                                   f"{name}.day.bin"), start_index, arr)
        items.append({"symbol": sym, "code": code,
                      "start": df["date"].iloc[0], "end": df["date"].iloc[-1],
                      "rows": int(len(df)),
                      "factors": [f"features/{sym.lower()}/{f}.day.bin"
                                  for f in FIELDS]})
        ok += 1
        if ok % 100 == 0:
            print(f"dumped {ok}/{len(codes)}", flush=True)

    with open(os.path.join(out_dir, "instruments", "all.txt"), "w",
              encoding="utf-8") as fh:
        for it in items:
            fh.write(f"{it['symbol']}\t{it['start']}\t{it['end']}\n")
    manifest = {
        "experiment": args.exp,
        "out": f"data/{args.exp}/{args.out}",
        "calendar_days": len(cal),
        "calendar_start": cal[0], "calendar_end": cal[-1],
        "instruments_written": ok, "instruments_failed": len(fail),
        "failures": fail,
        "fields": list(FIELDS),
        "price_basis": "后复权 (hfq) for open/close/high/low; factor = hfq/raw",
        "source": "akshare stock_zh_a_hist_sina (新浪财经), raw + hfq",
        "items": items,
    }
    mpath = os.path.join(exp_dir, "snapshot_manifest_qlib.json")
    json.dump(manifest, open(mpath, "w"), ensure_ascii=False, indent=1)
    print(f"qlib dataset -> {out_dir}  instruments={ok} failed={len(fail)}")
    print(f"manifest -> {mpath}")


if __name__ == "__main__":
    sys.exit(main())
