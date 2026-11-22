#!/usr/bin/env python3
"""WORK-503 stage 2 — bulk CSI300 constituent daily bars (Sina / 新浪财经).

Why Sina: the Tushare MCP endpoint (server-side) is verified working but bulk
pulls would have to pass through the agent context, which is not allowed by
the repo's data discipline; the East-Money interface (akshare
`stock_zh_a_hist_em`) is IP-rate-limited from this host; baostock got this
host's IP blacklisted mid-run.  Sina (`stock_zh_a_hist_sina`) is listed as an
equivalent source by the aktools interface registry and was verified to match
the Tushare MCP output field by field on 600519.SH, 2024-01-02:
    open/high/low/close identical, volume 3,215,644 股 == tushare 32,156.44 手,
    amount 5.440083e9 元 == tushare 5,440,082.548 千元.
Sina additionally delivers `outstanding_share` (流通股本) per day, which gives a
point-in-time float market cap for the size prior factor.

Universe: experiments/WORK-503/pool_membership.csv (point-in-time CSI300
roster, baostock query_hs300_stocks(date=...) sampled month by month).

Writes data/WORK-503/sina/<code>.csv (raw) and data/WORK-503/sina_hfq/<code>.csv
(后复权), plus experiments/WORK-503/snapshot_manifest_sina.json with per-item
ok/error, rows, date range and sha256.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import akshare as ak

START, END = "20120101", "20241231"
RAW_DIR, HFQ_DIR = "", ""
_LOCK = threading.Lock()


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_sina(code: str) -> str:
    """sh.600000 -> sh600000"""
    return code.replace(".", "")


def fetch_one(code: str):
    sym = to_sina(code)
    rec = {"code": code, "ok": False, "rows_raw": 0, "rows_hfq": 0,
           "date_min": None, "date_max": None,
           "sha256_raw": None, "sha256_hfq": None, "error": ""}
    frames = {}
    for adjust, key in (("", "raw"), ("hfq", "hfq")):
        last = ""
        df = None
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_hist_sina(symbol=sym, start_date=START,
                                             end_date=END, adjust=adjust)
                last = ""
                break
            except Exception as exc:                            # noqa: BLE001
                last = f"adjust={adjust or 'raw'}: {type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
        if last:
            rec["error"] = last
            return rec
        frames[key] = df
    try:
        raw, hfq = frames["raw"], frames["hfq"]
        p_raw = os.path.join(RAW_DIR, f"{code}.csv")
        p_hfq = os.path.join(HFQ_DIR, f"{code}.csv")
        raw.to_csv(p_raw, index=False)
        hfq.to_csv(p_hfq, index=False)
        dates = [str(d)[:10] for d in raw["date"].tolist()]
        rec.update({"ok": bool(dates), "rows_raw": len(raw),
                    "rows_hfq": len(hfq),
                    "date_min": dates[0] if dates else None,
                    "date_max": dates[-1] if dates else None,
                    "sha256_raw": sha256_of(p_raw), "sha256_hfq": sha256_of(p_hfq)})
    except Exception as exc:                                    # noqa: BLE001
        rec["error"] = f"write failed: {type(exc).__name__}: {exc}"
    return rec


def main():
    global RAW_DIR, HFQ_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    RAW_DIR = os.path.join(data_dir, "sina")
    HFQ_DIR = os.path.join(data_dir, "sina_hfq")
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(HFQ_DIR, exist_ok=True)

    codes = set()
    with open(os.path.join(exp_dir, "pool_membership.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            codes.add(r["code"])
    codes = sorted(codes)
    if args.limit:
        codes = codes[:args.limit]
    print(f"{len(codes)} unique PIT-universe codes", flush=True)

    mpath = os.path.join(exp_dir, "snapshot_manifest_sina.json")
    prev = {}
    if os.path.exists(mpath):
        for it in json.load(open(mpath)).get("items", []):
            prev[it["code"]] = it
    todo = [c for c in codes if not prev.get(c, {}).get("ok")]
    results = [prev[c] for c in codes if c in prev]
    print(f"resume: {len(prev)} done, {len(todo)} to fetch", flush=True)

    t0 = time.time()
    done = 0
    # Sequential on purpose: concurrent Sina sessions from one IP hang (the
    # request never returns) and Sina bans heavy crawlers.  724 codes x ~1.0 s
    # is ~12 min, which is cheaper than an IP ban.
    for rec in (fetch_one(c) for c in todo):
        results.append(rec)
        done += 1
        if done % 50 == 0 or done == len(todo):
            bad = sum(1 for r in results if not r["ok"])
            el = time.time() - t0
            print(f"{done}/{len(todo)} fail={bad} {el:.0f}s "
                  f"eta={el / done * (len(todo) - done):.0f}s", flush=True)
        time.sleep(args.sleep)

    results.sort(key=lambda r: r["code"])
    manifest = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "window": {"start": "2012-01-01", "end": "2024-12-31"},
        "paths_are": "repo-relative",
        "sources": {
            "C_akshare_sina_raw": ("akshare stock_zh_a_hist_sina adjust='' "
                                   "(新浪财经, 不复权)"),
            "C_akshare_sina_hfq": ("akshare stock_zh_a_hist_sina adjust='hfq' "
                                   "(新浪财经, 后复权)"),
        },
        "units": {"volume": "股", "amount": "元", "outstanding_share": "股",
                  "turnover": "小数"},
        "cross_check": ("600519.SH 2024-01-02 与 tushare MCP daily/daily_basic "
                        "逐字段一致（见 experiments/WORK-503/data_probe.json）"),
        "suspended_convention": ("新浪在停牌日无行；'无行 => 停牌'，价格前向填充、"
                                 "成交量置 0（field_dictionary.md §5）"),
        "pool": {"index": "000300.SH",
                 "source": "baostock query_hs300_stocks(date=...) 月度 PIT 快照",
                 "effective_lists": 145, "unique_codes": len(codes),
                 "membership_csv": f"experiments/{args.exp}/pool_membership.csv"},
        "items": results,
    }
    json.dump(manifest, open(mpath, "w"), ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["ok"])
    print(f"manifest -> {mpath}")
    print(f"ok={ok} fail={len(results) - ok} "
          f"rows={sum(r['rows_raw'] for r in results)} "
          f"elapsed={time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
