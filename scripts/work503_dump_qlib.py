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

Calendar alignment (preregistration_v1.2.md §A)
------------------------------------------------
Qlib's `.day.bin` format is *dense over the calendar*: the j-th float32 after
the start index belongs to calendar day `start_index + j`.  Writing only the
days a stock actually traded (i.e. packing them) shifts every later price
forward by the number of suspended days in between -- 724 stocks, 608 of them
with holes, 54,600 calendar days in total.  This script therefore writes one
value per calendar day and fills the suspended days exactly as
mask_contract.md §2.2 prescribes: prices forward-filled to the previous close,
`volume = 0`, `amount = 0`.

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
    n_holes = 0
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
        # dense on the exchange calendar: missing calendar days are suspended
        raw = raw.set_index("date").reindex(cal)
        hfq = hfq.set_index("date").reindex(cal)
        if raw["volume"].isna().all():
            fail.append({"code": code, "error": "no rows on calendar"})
            continue

        volume = raw["volume"].to_numpy("float64")
        traded = np.isfinite(volume) & (volume > 0)
        if not traded.any():
            fail.append({"code": code, "error": "no traded day"})
            continue
        first_i = int(np.argmax(traded))
        last_i = int(len(traded) - 1 - np.argmax(traded[::-1]))
        n_holes += int((~traded[first_i:last_i + 1]).sum())

        # ---- mask_contract.md §2.2: suspended days keep the row, prices are
        # forward-filled to the previous close, volume = 0, amount = 0.
        close_raw = raw["close"].to_numpy("float64")
        close_hfq = hfq["close"].to_numpy("float64")
        close_raw_f = pd.Series(close_raw).where(traded).ffill().to_numpy("float64")
        close_hfq_f = pd.Series(close_hfq).where(traded).ffill().to_numpy("float64")

        def px(df, col, fallback):
            v = df[col].to_numpy("float64")
            ok = traded & np.isfinite(v)
            return np.where(ok, v, fallback)

        open_h = px(hfq, "open", close_hfq_f)
        close_h = close_hfq_f
        high_h = px(hfq, "high", close_hfq_f)
        low_h = px(hfq, "low", close_hfq_f)
        volume_out = np.where(traded, volume, 0.0)
        amount = np.where(traded, raw["amount"].to_numpy("float64"), 0.0)
        factor = close_hfq_f / close_raw_f
        denom = np.where(traded & (volume > 0), volume, 1.0)
        vwap = np.where(traded & (volume > 0), (amount / denom) * factor, close_hfq_f)

        series = {
            "open": open_h, "close": close_h, "high": high_h, "low": low_h,
            "volume": volume_out, "factor": factor, "vwap": vwap, "amount": amount,
        }
        n = last_i - first_i + 1
        for name, vals in series.items():
            arr = vals[first_i:last_i + 1].astype("float64")
            write_bin(os.path.join(out_dir, "features", sym.lower(),
                                   f"{name}.day.bin"), first_i, arr)
        items.append({"symbol": sym, "code": code,
                      "start": cal[first_i], "end": cal[last_i],
                      "calendar_days": int(n),
                      "traded_days": int(traded[first_i:last_i + 1].sum()),
                      "suspended_days": int(n - traded[first_i:last_i + 1].sum()),
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
        "calendar_alignment": "dense per calendar day (qlib .day.bin semantics); "
                              "suspended days forward-filled to previous close, "
                              "volume = 0, amount = 0 (mask_contract.md §2.2)",
        "suspended_days_total": int(n_holes),
        "items": items,
    }
    mpath = os.path.join(exp_dir, "snapshot_manifest_qlib.json")
    json.dump(manifest, open(mpath, "w"), ensure_ascii=False, indent=1)
    print(f"qlib dataset -> {out_dir}  instruments={ok} failed={len(fail)}")
    print(f"manifest -> {mpath}")


if __name__ == "__main__":
    sys.exit(main())
