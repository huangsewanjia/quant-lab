#!/usr/bin/env python3
"""WORK-503 stage 2 — fetch CSI300 constituent daily bars (Baostock).

Protocol: experiments/WORK-503/preregistration.md §2 (data source, window),
mask_contract.md (suspension / delisting conventions).

Source: Baostock (www.baostock.com), one login per worker process.
Two calls per stock:
  adjustflag="1"  后复权 (hfq) — prices for features and portfolio returns
  adjustflag="3"  不复权 (raw)  — preclose for limit-price (涨跌停) detection

Field semantics follow data_contracts/field_dictionary.md §1/§5:
  baostock volume = 股, amount = 元, turn = 换手率(%), tradestatus = 1 当日有交易
  isST = 1 时 ST (limit band 5% / 20%), 0 时非 ST

Writes data/WORK-503/<code>.csv (hfq) and data/WORK-503/raw/<code>.csv (raw),
plus experiments/WORK-503/snapshot_manifest.json with per-item ok/error,
rows, date range and sha256. Failed entries keep the original error text.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from calendar import monthrange
from datetime import date, datetime
import multiprocessing as mp

FIELDS = ("date,open,high,low,close,preclose,volume,amount,turn,"
          "tradestatus,pctChg,isST,peTTM,pbMRQ,psTTM")
OUT_COLS = ("date", "open", "high", "low", "close", "preclose", "volume",
            "amount", "turn", "tradestatus", "pct_chg", "is_st",
            "pe_ttm", "pb_mrq", "ps_ttm")
START, END = "2012-01-01", "2024-12-31"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _login():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")


def query_series(code_bs: str, adjustflag: str):
    """Return list of row-lists for one code, or raise."""
    import baostock as bs
    rs = bs.query_history_k_data_plus(
        code_bs, FIELDS, start_date=START, end_date=END,
        frequency="d", adjustflag=adjustflag)
    if rs.error_code != "0":
        raise RuntimeError(f"query error {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rows


def write_csv(path: str, rows) -> int:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(OUT_COLS)
        for r in rows:
            w.writerow(r)
    return len(rows)


def fetch_one(code: str):
    """code like sh.600519."""
    out_dir, raw_dir = OUT_DIR, RAW_DIR
    t0 = time.time()
    try:
        hfq = query_series(code, "1")
        raw = query_series(code, "3")
    except Exception as exc:                                    # noqa: BLE001
        return {"code": code, "ok": False, "rows": 0, "date_min": None,
                "date_max": None, "sha256": None, "sha256_raw": None,
                "error": f"{type(exc).__name__}: {exc}", "elapsed_s": 0.0}
    try:
        p1 = os.path.join(out_dir, f"{code}.csv")
        p2 = os.path.join(raw_dir, f"{code}.csv")
        n1 = write_csv(p1, hfq)
        n2 = write_csv(p2, raw)
        dates = [r[0] for r in hfq] if hfq else []
        return {"code": code, "ok": bool(n1), "rows": n1, "rows_raw": n2,
                "date_min": dates[0] if dates else None,
                "date_max": dates[-1] if dates else None,
                "sha256": sha256_of(p1) if n1 else None,
                "sha256_raw": sha256_of(p2) if n2 else None,
                "error": "", "elapsed_s": round(time.time() - t0, 2)}
    except Exception as exc:                                    # noqa: BLE001
        return {"code": code, "ok": False, "rows": 0, "date_min": None,
                "date_max": None, "sha256": None, "sha256_raw": None,
                "error": f"write failed: {type(exc).__name__}: {exc}",
                "elapsed_s": round(time.time() - t0, 2)}


def fetch_index(code_bs: str):
    """Baostock index k-data wants YYYY-MM-DD (not YYYYMMDD)."""
    import baostock as bs
    rs = bs.query_history_k_data_plus(
        code_bs, "date,open,high,low,close,preclose,volume,amount,pctChg",
        start_date="2012-01-01", end_date="2024-12-31", frequency="d")
    if rs.error_code != "0":
        raise RuntimeError(f"index {code_bs}: {rs.error_code} {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rows


def fetch_trade_dates():
    import baostock as bs
    rs = bs.query_trade_dates(start_date="2012-01-01", end_date="2024-12-31")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rows


OUT_DIR = ""
RAW_DIR = ""


def init_worker(out_dir, raw_dir):
    global OUT_DIR, RAW_DIR
    OUT_DIR, RAW_DIR = out_dir, raw_dir
    _login()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    raw_dir = os.path.join(data_dir, "raw")
    os.makedirs(exp_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)

    _login()
    import baostock as bs

    # ---- PIT membership -------------------------------------------------
    # baostock query_hs300_stocks(date="YYYY-MM-DD") returns the 300 names
    # that were IN the index on that date, together with the effective date
    # of the constituent list it belongs to.  Sampling month by month and
    # keeping the list's own effective date gives a point-in-time roster
    # (never back-filled with today's names).
    mem_path = os.path.join(exp_dir, "pool_membership.csv")
    if not os.path.exists(mem_path):
        by_eff = {}
        probe_days = []
        y, m = 2012, 1
        while (y, m) <= (2024, 12):
            probe_days.append(date(y, m, monthrange(y, m)[1]).isoformat())
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        for pd_ in probe_days:
            rs = bs.query_hs300_stocks(date=pd_)
            if rs.error_code != "0":
                raise RuntimeError(f"hs300 {pd_}: {rs.error_code} {rs.error_msg}")
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue
            eff = rows[0][0]
            by_eff.setdefault(eff, set()).update(
                (r[1], r[2]) for r in rows)
        eff_dates = sorted(by_eff)
        with open(mem_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["effective_date", "code", "name"])
            for eff in eff_dates:
                for code, name in sorted(by_eff[eff]):
                    w.writerow([eff, code, name])
        json.dump({"probe_dates": len(probe_days),
                   "effective_lists": len(eff_dates),
                   "effective_dates": eff_dates,
                   "rows": sum(len(v) for v in by_eff.values())},
                  open(os.path.join(exp_dir, "pool_membership_meta.json"), "w"),
                  ensure_ascii=False, indent=1)
        print(f"membership: {len(eff_dates)} effective lists, "
              f"{sum(len(v) for v in by_eff.values())} rows", flush=True)

    lists = {}
    with open(mem_path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            lists.setdefault(r["effective_date"], []).append(
                (r["code"], r["name"]))
    codes = sorted({c for members in lists.values() for c, _ in members})
    if args.limit:
        codes = codes[:args.limit]
    print(f"{len(codes)} unique codes from {len(lists)} effective lists",
          flush=True)

    # ---- trading calendar + CSI300 index --------------------------------
    cal_path = os.path.join(exp_dir, "trade_calendar.csv")
    if not os.path.exists(cal_path):
        with open(cal_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["calendar_date", "is_trading_day"])
            for r in fetch_trade_dates():
                w.writerow([r[0], r[1]])
        print("calendar written", flush=True)

    idx_path = os.path.join(data_dir, "index_sh000300.csv")
    if not os.path.exists(idx_path):
        rows = fetch_index("sh.000300")
        with open(idx_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "open", "high", "low", "close", "preclose",
                        "volume", "amount", "pct_chg"])
            for r in rows:
                w.writerow(r)
        print(f"index rows={len(rows)}", flush=True)

    # ---- bars ------------------------------------------------------------
    mpath = os.path.join(exp_dir, "snapshot_manifest.json")
    prev = {}
    if os.path.exists(mpath):
        for it in json.load(open(mpath)).get("items", []):
            prev[it["code"]] = it
    todo = [c for c in codes if not prev.get(c, {}).get("ok")]
    results = [prev[c] for c in codes if c in prev]
    print(f"resume: {len(prev)} done, {len(todo)} to fetch", flush=True)

    t0 = time.time()
    # baostock keeps a global socket; a forked child inherits a broken copy
    # (query_history_k_data_plus returns None).  "spawn" gives every worker a
    # fresh interpreter, so init_worker's login() owns its own connection.
    ctx = mp.get_context("spawn")
    with ctx.Pool(args.workers, initializer=init_worker,
                  initargs=(data_dir, raw_dir)) as pool:
        for k, res in enumerate(pool.imap_unordered(fetch_one, todo), 1):
            results.append(res)
            if k % 50 == 0 or k == len(todo):
                bad = sum(1 for r in results
                          if not r["ok"] and r["code"] in set(todo))
                el = time.time() - t0
                print(f"{k}/{len(todo)} fail={bad} {el:.0f}s "
                      f"eta={el / k * (len(todo) - k):.0f}s", flush=True)

    results.sort(key=lambda r: r["code"])
    manifest = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "window": {"start": "2012-01-01", "end": "2024-12-31"},
        "paths_are": "repo-relative (relative to repository root)",
        "sources": {
            "B_baostock_hfq": "baostock query_history_k_data_plus adjustflag='1' (后复权)",
            "B_baostock_raw": "baostock query_history_k_data_plus adjustflag='3' (不复权)",
        },
        "units": {"volume": "股", "amount": "元", "turn": "%",
                  "pct_chg": "%", "pe_ttm": "倍", "pb_mrq": "倍"},
        "suspended_convention": (
            "baostock returns no row for a suspended day; 'no row => suspended', "
            "price forward-filled and volume zeroed per field_dictionary.md §5"),
        "pool": {"index": "000300.SH",
                 "source": "baostock query_hs300_stocks (effective_date = PIT)",
                 "effective_lists": len(lists), "unique_codes": len(codes),
                 "membership_csv": f"experiments/{args.exp}/pool_membership.csv"},
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
