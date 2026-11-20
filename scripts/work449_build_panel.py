#!/usr/bin/env python3
"""WORK-449 · stage 2 step 2 — build the price panel and the three mask arms.

Protocol: experiments/WORK-449/preregistration.md §4 (data source), §4.3
(repaired-adjusted-price rule), §5 (mask definitions, "mask before rolling
window"), §2.2 (power check).

Panel construction (protocol §5.2 + data_contracts/field_dictionary.md §5)
--------------------------------------------------------------------------
The panel is a DENSE grid over the union calendar. A panel row exists for a
stock from its first East-Money row to its last East-Money row:

  * inside that window the stock is *suspended* on days with no source row:
    the price is forward-filled to the last close and volume/amount are zeroed
    (field_dictionary §5).  A suspended day is a data fact, not a mask, so the
    ``no_mask`` arm reads it (protocol §5.2);
  * outside that window the stock was not listed (or is delisted): no row at
    all, price NaN.

Price series are the hfq (backward-adjusted) series of AKShare
``stock_zh_a_hist_em``; EM's qfq is unusable for early years because its
2-decimal precision collapses once the adjusted price drops below ~1 yuan
(preregistration_v1.2.md).

Mask arms (protocol §5.1 / §5.2, one sentence each, order fixed)
---------------------------------------------------------------
  no_mask    : no mask at all — every panel cell is readable
  halt_only  : only the suspension mask — limit-up/limit-down days stay readable
  full_mask  : suspension + limit mask, i.e. exactly M_exec
  M_exec     : not suspended AND not at limit AND listed for more than 1 year
               (shared by all three arms; it is an execution fact)

Outputs
  experiments/<exp>/panel.npz         price/return arrays, calendar, codes
  experiments/<exp>/panel_stats.json  per-arm mask rates, power-check decision
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

TRAIN_START, TRAIN_END = "2015-01-01", "2020-12-31"
VAL_START, VAL_END = "2021-01-01", "2021-12-31"
TEST_START, TEST_END = "2022-01-01", "2024-12-31"
CHINEXT_20PCT_START = np.datetime64("2020-08-24")

BAND_MAIN, BAND_ST, BAND_GROWTH, BAND_BSE = 0.10, 0.05, 0.20, 0.30
PROXY_MAIN, PROXY_ST, PROXY_GROWTH, PROXY_BSE = 0.098, 0.048, 0.198, 0.298


def board_of(code: str) -> str:
    num = code.split(".")[-1]
    if code.startswith("bj") or num.startswith(("4", "8", "92")):
        return "bse"
    if num.startswith(("688", "689")):
        return "star"
    if num.startswith(("300", "301")):
        return "chinext"
    return "main"


def limit_pct_matrix(dates, codes, is_st):
    """[T, N] proxy limit thresholds (protocol §0 table, v2.0 clarification 1)."""
    per_stock = np.empty(len(codes))
    for j, c in enumerate(codes):
        b = board_of(c)
        per_stock[j] = {"bse": PROXY_BSE, "star": PROXY_GROWTH,
                        "chinext": PROXY_GROWTH}.get(b, PROXY_MAIN)
    out = np.broadcast_to(per_stock, (len(dates), len(codes))).copy()
    out[is_st] = PROXY_ST
    chinext = np.array([board_of(c) == "chinext" for c in codes])
    before = dates < CHINEXT_20PCT_START
    out[np.ix_(before, chinext)] = PROXY_MAIN
    return out


def band_matrix(dates, codes, is_st):
    per_stock = np.empty(len(codes))
    for j, c in enumerate(codes):
        b = board_of(c)
        per_stock[j] = {"bse": BAND_BSE, "star": BAND_GROWTH,
                        "chinext": BAND_GROWTH}.get(b, BAND_MAIN)
    out = np.broadcast_to(per_stock, (len(dates), len(codes))).copy()
    out[is_st] = BAND_ST
    return out


def load_repo(repo, exp):
    data_dir = os.path.join(repo, "data", exp)
    codes = sorted(f[:-4] for f in os.listdir(data_dir) if f.endswith(".csv"))
    frames = {}
    for code in codes:
        with open(os.path.join(data_dir, f"{code}.csv"), encoding="utf-8") as fh:
            df = pd.read_csv(fh, dtype={"date": str})
        frames[code] = df.set_index("date")
    return codes, frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)

    codes, frames = load_repo(repo, args.exp)
    print(f"loaded {len(codes)} codes")
    all_dates = sorted(set().union(*[set(df.index) for df in frames.values()]))
    di = {d: i for i, d in enumerate(all_dates)}
    T, N = len(all_dates), len(codes)
    dates = np.array(all_dates, dtype="datetime64[D]")
    print(f"calendar {T} days {all_dates[0]}..{all_dates[-1]}")

    def stack(col):
        arr = np.full((T, N), np.nan)
        for j, code in enumerate(codes):
            df = frames[code]
            vals = pd.to_numeric(df[col].reindex(all_dates).values,
                                 errors="coerce")
            arr[:, j] = vals
        return arr

    close_h = stack("close")          # hfq close
    high_h = stack("high")
    low_h = stack("low")
    close_r = stack("close_raw")
    preclose = stack("preclose")
    volume = stack("volume")
    amount = stack("amount")
    pct_chg = stack("pct_chg")
    ts = stack("tradestatus")
    st = stack("is_st")
    em_row = ~np.isnan(close_h)      # a source row exists for this cell

    # ---- dense panel grid: first -> last source row of each stock ----
    first_i = np.full(N, -1, dtype=int)
    last_i = np.full(N, -1, dtype=int)
    for j, code in enumerate(codes):
        idx = [di[d] for d in frames[code].index]
        first_i[j] = min(idx)
        last_i[j] = max(idx)
    grid = np.arange(T)[:, None]
    has_row = (grid >= first_i[None, :]) & (grid <= last_i[None, :])
    suspended = has_row & ~em_row
    print(f"dense rows {int(has_row.sum())} | suspended(in-window) "
          f"{int(suspended.sum())} | pre-IPO/delisted {int((~has_row).sum())}")

    # ---- listed date (IPO) ----
    ipo_map = {}
    sb_path = os.path.join(exp_dir, "stock_basic.json")
    if os.path.exists(sb_path):
        for code, meta in json.load(open(sb_path)).items():
            if meta.get("ipo_date"):
                ipo_map[code] = meta["ipo_date"]
    listed = np.empty(N, dtype="datetime64[D]")
    for j, code in enumerate(codes):
        raw = ipo_map.get(code) or frames[code].index[0]
        listed[j] = np.datetime64(raw, "D")
    listed_1y = dates[:, None] > (listed[None, :] + np.timedelta64(365, "D"))

    # ---- ST intervals (preregistration v1.1 §3.1): Tushare namechange map ----
    st_path = os.path.join(exp_dir, "st_intervals.json")
    st_map = json.load(open(st_path))["intervals"] if os.path.exists(st_path) else {}
    st_days = 0
    for j, code in enumerate(codes):
        for a, b in st_map.get(code, []):
            sel = (dates >= np.datetime64(a)) & (dates <= np.datetime64(b))
            st[sel, j] = 1
            st_days += int(sel.sum())
    print(f"ST-marked cells from st_intervals: {st_days}")

    # ---- forward fill prices on suspended / missing days (field_dict §5) ----
    close_ff = pd.DataFrame(close_h).ffill(axis=0).to_numpy()
    volume_f = np.where(em_row, volume, 0.0)
    amount_f = np.where(em_row, amount, 0.0)
    amount_f = np.where(np.isnan(amount_f), volume_f * close_ff, amount_f)

    # ---- repaired adjusted-price rule (protocol §4.3) ----
    with np.errstate(divide="ignore", invalid="ignore"):
        adj = close_h / close_r
    adj_ff = pd.DataFrame(adj).ffill(axis=0).to_numpy()
    imp = np.full((T, N), np.nan)
    imp[1:, :] = adj_ff[1:, :] / adj_ff[:-1, :] - 1.0
    bands = band_matrix(dates, codes, st == 1)
    thr = bands * 1.02 + 0.01
    corrupt = np.zeros((T, N), dtype=bool)
    valid_imp = ~np.isnan(imp)
    corrupt[1:, :] = valid_imp[1:, :] & (np.abs(imp[1:, :]) > thr[1:, :])

    # ---- returns ----
    r_h = np.full((T, N), np.nan)
    r_h[1:, :] = close_ff[1:, :] / close_ff[:-1, :] - 1.0   # halt-spanning
    r_raw = pct_chg / 100.0
    r_eff = r_h.copy()
    r_eff[corrupt] = np.where(np.isnan(r_raw[corrupt]), r_h[corrupt],
                               r_raw[corrupt])
    r_eff[~has_row] = np.nan          # not listed -> no cell, no return

    # repaired adjusted price level = compounded r_eff (forward filled on
    # suspended days to the last close, never to a stale value)
    adj_close = np.full((T, N), np.nan)
    acc = np.ones(N)
    last = np.full(N, np.nan)
    for t in range(T):
        r = r_eff[t]
        acc = np.where(np.isnan(r), acc, acc * (1.0 + np.where(np.isnan(r), 0, r)))
        last = np.where(np.isnan(r), last, acc)
        adj_close[t] = last

    # ---- masks ----
    lp = limit_pct_matrix(dates, codes, st == 1)
    with np.errstate(invalid="ignore"):
        at_limit = np.abs(r_raw) > lp
    at_limit = at_limit & ~np.isnan(r_raw) & em_row

    m_exec = has_row & ~suspended & ~at_limit & listed_1y
    m_no = has_row                                   # no filtering at all
    m_halt = has_row & ~suspended                    # suspension mask only
    m_full = m_exec                                  # suspension + limit mask

    np.savez_compressed(
        os.path.join(exp_dir, "panel.npz"),
        dates=dates, codes=np.array(codes),
        close_hfq=close_h, close_hfq_ff=close_ff, close_raw=close_r,
        high_hfq=high_h, low_hfq=low_h,
        adj_close=adj_close, r_eff=r_eff, r_raw=r_raw, pct_chg=pct_chg,
        volume=volume_f, amount=amount_f, has_row=has_row, em_row=em_row,
        suspended=suspended, at_limit=at_limit, listed_1y=listed_1y,
        corrupt=corrupt, limit_pct=lp, band=bands,
        m_exec=m_exec, m_no=m_no, m_halt=m_halt, m_full=m_full,
        listed=np.array([str(x) for x in listed]),
    )

    # ---- power check (protocol §2.2) ----
    test_sel = (dates >= np.datetime64(TEST_START)) & \
               (dates <= np.datetime64(TEST_END))
    valid = has_row[test_sel]
    mask_rate = float((~m_exec[test_sel] & valid).sum() / max(valid.sum(), 1))
    decision = "CSI300" if mask_rate >= 0.005 else "CSI300+CSI500"

    main_board = np.array([board_of(c) == "main" for c in codes])
    st_band_cells = int((main_board[None, :] & (st == 1) &
                         (np.abs(r_raw) > 0.048) & (np.abs(r_raw) <= 0.052) &
                         test_sel[:, None]).sum())
    stats = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "st_diagnostic": {
            "st_marked_cells_all": st_days,
            "st_marked_cells_test": int((st == 1)[test_sel].sum()),
            "possible_missed_st_limit_cells_test": st_band_cells,
            "note": ("upper bound on ST limit events that the 10% proxy band "
                     "would miss (preregistration v1.1 §3.1)"),
        },
        "dates": {"n": int(T), "first": str(dates[0]), "last": str(dates[-1])},
        "codes": int(N),
        "segments": {"train": [TRAIN_START, TRAIN_END],
                     "val": [VAL_START, VAL_END],
                     "test": [TEST_START, TEST_END]},
        "power_check": {
            "window": [TEST_START, TEST_END],
            "full_mask_rate_test_window": mask_rate,
            "threshold": 0.005,
            "locked_pool": decision,
            "note": ("decided from data coverage only, before any model run "
                     "(protocol §2.2)"),
        },
        "counts": {
            "source_rows": int(em_row.sum()),
            "dense_panel_rows": int(has_row.sum()),
            "suspended_in_window_cells": int(suspended.sum()),
            "not_listed_cells": int((~has_row).sum()),
            "at_limit_cells": int(at_limit.sum()),
            "corrupt_adj_cells": int(corrupt.sum()),
            "not_listed_1y_cells": int((~listed_1y).sum()),
        },
        "arms": {},
    }
    for arm, m in (("no_mask", m_no), ("halt_only", m_halt),
                   ("full_mask", m_full)):
        lm = m[:-1, :] & m[1:, :]
        stats["arms"][arm] = {
            "factor_cells_test": int((m & test_sel[:, None]).sum()),
            "label_cells_test": int((lm & test_sel[:-1][:, None]).sum()),
            "mask_rate_test": float(m[test_sel].sum() /
                                    max(has_row[test_sel].sum(), 1)),
            "at_limit_cells_test": int((at_limit & test_sel[:, None]).sum()),
            "suspended_cells_test": int((suspended & test_sel[:, None]).sum()),
        }
    json.dump(stats, open(os.path.join(exp_dir, "panel_stats.json"), "w"),
              indent=1)
    print(json.dumps(stats["power_check"], indent=1))
    print(json.dumps(stats["counts"], indent=1))
    print(json.dumps(stats["arms"], indent=1))


if __name__ == "__main__":
    sys.exit(main())
