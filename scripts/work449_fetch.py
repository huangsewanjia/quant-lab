#!/usr/bin/env python3
"""WORK-449 · stage 2 step 1 — fetch CSI300 constituent daily data (AKShare).

Protocol: experiments/WORK-449/preregistration.md §4 (data source), §4.3 (qfq
repair rule), preregistration_v1.1.md (data source change and its reason).

Source: AKShare `stock_zh_a_hist_em` (东方财富·行情), two calls per code:
  adjust=""    raw prices + 涨跌幅 (the true ex-rights daily return) + 成交额
  adjust="hfq" backward-adjusted prices (factor inputs). EM's qfq is
         unusable for early years: its 2-decimal precision collapses once the
         adjusted price drops below ~1 yuan (see preregistration_v1.2.md).

Suspended days produce no row; they are reconstructed as
"no row => suspended" exactly as data_contracts/field_dictionary.md §5
requires (row kept, price forward-filled, volume zeroed).

Writes data/<exp>/<code>.csv and experiments/<exp>/snapshot_manifest.json.
Failed entries keep their original error text.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime

import akshare as ak

START, END = "20140101", "20241231"
OUT_COLS = ("date", "code", "open", "high", "low", "close", "close_raw",
            "preclose", "volume", "amount", "tradestatus", "is_st", "pct_chg")
COLMAP = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
          "最低": "low", "成交量": "volume", "成交额": "amount",
          "涨跌幅": "pct_chg", "涨跌额": "chg"}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_frame(adjust: str, symbol: str):
    df = ak.stock_zh_a_hist_em(symbol=symbol, period="daily",
                                start_date=START, end_date=END, adjust=adjust)
    if df is None or df.empty:
        return {}
    df = df.rename(columns={k: v for k, v in COLMAP.items() if k in df.columns})
    return {str(r["date"])[:10]: r for _, r in df.iterrows()}


def fetch_one(code: str):
    """code like sh.600519 -> symbol 600519."""
    symbol = code.split(".")[-1]
    err = ""
    raw, hfq = {}, {}
    for adjust, bucket in (("", raw), ("hfq", hfq)):
        last = ""
        for attempt in range(3):
            try:
                bucket.update(to_frame(adjust, symbol))
                last = ""
                break
            except Exception as exc:               # noqa: BLE001
                last = f"adjust={adjust or 'raw'}: {type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
        if last:
            err = last
    return code, raw, hfq, err


def write_csv(code, raw, hfq, out_dir):
    path = os.path.join(out_dir, f"{code}.csv")
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(OUT_COLS)
        for d in sorted(set(raw) | set(hfq)):
            r = raw.get(d)
            q = hfq.get(d)
            src = r if r is not None else q
            if src is None:
                continue
            preclose = ""
            if r is not None:
                try:
                    preclose = f"{float(r['close']) - float(r['chg']):.4f}"
                except (KeyError, TypeError, ValueError):
                    preclose = ""
            w.writerow([
                d, code,
                q["open"] if q is not None else "",
                q["high"] if q is not None else "",
                q["low"] if q is not None else "",
                q["close"] if q is not None else "",
                r["close"] if r is not None else "",
                preclose,
                (f"{float(src['volume']) * 100:.0f}"
                 if src.get("volume") is not None else ""),
                src.get("amount", ""),
                1,          # a row exists -> tradable status flag
                0,          # is_st placeholder (see is_st map file)
                src.get("pct_chg", ""),
            ])
            n += 1
    return path, n


def worker(args):
    idx, code, out_dir = args
    try:
        code, raw, hfq, err = fetch_one(code)
    except Exception as exc:                       # noqa: BLE001
        return {"code": code, "ok": False,
                "error": f"{type(exc).__name__}: {exc}", "rows": 0,
                "date_min": None, "date_max": None, "sha256": None,
                "path": None}
    try:
        path, rows = write_csv(code, raw, hfq, out_dir)
    except Exception as exc:                       # noqa: BLE001
        return {"code": code, "ok": False,
                "error": f"write failed: {type(exc).__name__}: {exc}",
                "rows": 0, "date_min": None, "date_max": None,
                "sha256": None, "path": None}
    dates = sorted(set(raw) | set(hfq))
    return {
        "code": code,
        "ok": bool(rows),
        "error": err,
        "rows": rows,
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "sha256": sha256_of(path) if rows else None,
        "path": f"data/{os.path.basename(os.path.dirname(out_dir))}/"
                f"{os.path.basename(path)}",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    os.makedirs(exp_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    lists = json.load(open(os.path.join(exp_dir, "pool_membership.json")))
    codes = sorted({c for members in lists.values() for c, _ in members})
    if args.limit:
        codes = codes[:args.limit]
    print(f"{len(codes)} unique codes from {len(lists)} effective lists",
          flush=True)

    with open(os.path.join(exp_dir, "pool_membership.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["effective_date", "code", "name"])
        for eff in sorted(lists):
            for code, name in lists[eff]:
                w.writerow([eff, code, name])

    mpath = os.path.join(exp_dir, "snapshot_manifest.json")
    prev = {}
    if args.resume and os.path.exists(mpath):
        for it in json.load(open(mpath)).get("items", []):
            prev[it["code"]] = it
        todo = [c for c in codes if not prev.get(c, {}).get("ok")]
        results = [prev[c] for c in codes if c in prev]
        print(f"resume: {len(prev)} previous, {len(todo)} to fetch", flush=True)
    else:
        todo = list(codes)
        results = []

    import multiprocessing as mp
    t0 = time.time()
    items = [(i, c, data_dir) for i, c in enumerate(todo)]
    with mp.Pool(args.workers) as pool:
        for k, res in enumerate(pool.imap_unordered(worker, items), 1):
            results.append(res)
            if k % 25 == 0 or k == len(items):
                el = time.time() - t0
                bad = sum(1 for r in results
                          if not r["ok"] and r["code"] in set(todo))
                print(f"{k}/{len(items)} fail={bad} {el:.0f}s "
                      f"eta={el / k * (len(items) - k):.0f}s", flush=True)

    results = [r for r in results if r["code"] in set(codes)]
    results.sort(key=lambda r: r["code"])
    manifest = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "window": {"start": "2014-01-01", "end": "2024-12-31"},
        "paths_are": "repo-relative (relative to repository root)",
        "sources": {
            "A_akshare_em_hfq": ("akshare stock_zh_a_hist_em adjust='hfq' "
                                 "(东方财富·行情, 后复权)"),
            "A_akshare_em_raw": ("akshare stock_zh_a_hist_em adjust='' "
                                 "(东方财富·行情)"),
        },
        "adjust_basis": ("hfq basis = range start 2014-01-01 (per stock); "
                          "hfq is used instead of qfq because EM's qfq loses "
                          "precision for early years (preregistration_v1.2.md)"),
        "suspended_convention": ("no row => suspended; price forward-filled, "
                                 "volume zeroed (field_dictionary.md §5)"),
        "pool": {
            "index": "000300.SH",
            "source": "baostock query_hs300_stocks, dedup by updateDate",
            "effective_lists": len(lists),
            "unique_codes": len(codes),
            "membership_csv": f"experiments/{args.exp}/pool_membership.csv",
        },
        "items": results,
    }
    json.dump(manifest, open(mpath, "w"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["ok"])
    print(f"manifest -> {mpath}")
    print(f"ok={ok} fail={len(results) - ok} "
          f"rows={sum(r['rows'] for r in results)} "
          f"elapsed={time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
