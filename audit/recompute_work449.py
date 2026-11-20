#!/usr/bin/env python3
"""WORK-449 · stage 3 step 2 — independent recompute gate.

Contract (data_contracts + preregistration §10 gate 5): this script must NOT
import any function of the modelling stage. It re-reads the raw snapshot CSVs,
rebuilds the return series and the tradability mask with its own code, and
recomputes IC / Sharpe / annualised return / annualised volatility / max
drawdown / turnover, then compares against the modelling side item by item.

Only two modelling artifacts are consumed, because they cannot be recomputed
without retraining: the model predictions and the portfolio weights. The
factor-validity mask is likewise read from the factor artifact, and is
additionally spot-checked against an independent recomputation of two
representative factors on sampled cells.

Exit code 0 = no blocking finding.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime

import numpy as np

RF = 0.015
ARMS = ("no_mask", "halt_only", "full_mask")
TOL = 1e-6
MAIN_BOARD_LIMIT_PCT = 0.098
GROWTH_BOARD_LIMIT_PCT = 0.198
BSE_LIMIT_PCT = 0.298
ST_LIMIT_PCT = 0.048
CHINEXT_20PCT_START = "2020-08-24"


def rel_diff(a, b):
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return float("inf")
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


def board_of(code: str) -> str:
    digits = "".join(ch for ch in code if ch.isdigit())
    if code.startswith("bj") or digits[:1] in ("4", "8", "9"):
        return "bse"
    if digits.startswith(("688", "689")):
        return "star"
    if digits.startswith(("300", "301")):
        return "chinext"
    return "main"


class Rebuilt:
    """Independent re-derivation from the raw CSVs."""

    def __init__(self, repo: str, exp: str):
        self.data_dir = os.path.join(repo, "data", exp)
        self.exp_dir = os.path.join(repo, "experiments", exp)
        codes = sorted(f[:-4] for f in os.listdir(self.data_dir)
                       if f.endswith(".csv"))
        self.codes = codes
        rows = {}
        for code in codes:
            with open(os.path.join(self.data_dir, f"{code}.csv"),
                      encoding="utf-8") as fh:
                rows[code] = list(csv.DictReader(fh))
        self.rows = rows
        dates = sorted({r["date"] for code in codes for r in rows[code]})
        self.dates = dates
        self.pos = {d: i for i, d in enumerate(dates)}
        n, t = len(codes), len(dates)
        self.n, self.t = n, t
        self.close = np.full((t, n), np.nan)
        self.close_raw = np.full((t, n), np.nan)
        self.pct = np.full((t, n), np.nan)
        self.volume = np.full((t, n), np.nan)
        self.amount = np.full((t, n), np.nan)
        self.ts = np.zeros((t, n))
        self.st = np.zeros((t, n))
        for j, code in enumerate(codes):
            for r in rows[code]:
                i = self.pos[r["date"]]
                self.close[i, j] = _f(r["close"])
                self.close_raw[i, j] = _f(r["close_raw"])
                self.pct[i, j] = _f(r["pct_chg"])
                self.volume[i, j] = _f(r["volume"])
                self.amount[i, j] = _f(r["amount"])
                self.ts[i, j] = _f(r["tradestatus"])
                self.st[i, j] = _f(r["is_st"])
        self.has = ~np.isnan(self.close)
        self.suspended = (~self.has) | (self.ts == 0)
        # forward fill (field_dictionary §5)
        ff = self.close.copy()
        for j in range(n):
            last = math.nan
            col = ff[:, j]
            for i in range(t):
                if math.isnan(col[i]):
                    col[i] = last
                else:
                    last = col[i]
            ff[:, j] = col
        self.close_ff = ff
        # halt-spanning returns
        self.ret = np.full((t, n), math.nan)
        for i in range(1, t):
            self.ret[i, :] = ff[i, :] / ff[i - 1, :] - 1.0
        self.ret[~self.has] = math.nan
        # qfq corruption guard (prereg §4.3)
        with np.errstate(divide="ignore", invalid="ignore"):
            adj = self.close / self.close_raw
        adjff = np.full((t, n), np.nan)
        for j in range(n):
            last = math.nan
            for i in range(t):
                if math.isnan(adj[i, j]):
                    adjff[i, j] = last
                else:
                    last = adj[i, j]
                    adjff[i, j] = last
        imp = np.full((t, n), math.nan)
        imp[1:, :] = adjff[1:, :] / adjff[:-1, :] - 1.0
        self.band = self._band()
        thr = self.band * 1.02 + 0.01
        self.corrupt = np.zeros((t, n), dtype=bool)
        ok = ~np.isnan(imp)
        self.corrupt[1:, :] = ok[1:, :] & (np.abs(imp[1:, :]) > thr[1:, :])
        self.ret[self.corrupt] = np.where(
            np.isnan(self.pct[self.corrupt]), self.ret[self.corrupt],
            self.pct[self.corrupt] / 100.0)
        # masks
        self.listed = self._listed()
        self.listed_1y = self._dates()[:, None] > (
            self.listed[None, :] + np.timedelta64(365, "D"))
        self.at_limit = self._at_limit()
        self.m_exec = (self.has & ~self.suspended & ~self.at_limit
                       & self.listed_1y)
        self.m = {
            "no_mask": self.has,
            "halt_only": self.has & ~self.suspended & self.listed_1y,
            "full_mask": self.m_exec,
        }

    def _dates(self):
        return np.array(self.dates)

    def _band(self):
        band = np.empty((self.t, self.n))
        for j, code in enumerate(self.codes):
            b = board_of(code)
            base = {"bse": 0.30, "star": 0.20,
                    "chinext": 0.20}.get(b, 0.10)
            band[:, j] = base
        band[self.st == 1] = 0.05
        return band

    def _listed(self):
        out = np.empty(self.n, dtype="datetime64[D]")
        sb = os.path.join(self.exp_dir, "stock_basic.json")
        ipo = {}
        if os.path.exists(sb):
            ipo = {k: v.get("ipo_date") for k, v in json.load(open(sb)).items()}
        for j, code in enumerate(self.codes):
            raw = ipo.get(code)
            if not raw:
                raw = self.rows[code][0]["date"]
            out[j] = np.datetime64(raw, "D")
        return out

    def _at_limit(self):
        thr = np.empty((self.t, self.n))
        for j, code in enumerate(self.codes):
            b = board_of(code)
            base = {"bse": BSE_LIMIT_PCT, "star": GROWTH_BOARD_LIMIT_PCT,
                    "chinext": GROWTH_BOARD_LIMIT_PCT}.get(
                        b, MAIN_BOARD_LIMIT_PCT)
            thr[:, j] = base
        thr[self.st == 1] = ST_LIMIT_PCT
        d = self._dates()
        chinext = np.array([board_of(c) == "chinext" for c in self.codes])
        before = d < np.datetime64(CHINEXT_20PCT_START)
        thr[np.ix_(before, chinext)] = MAIN_BOARD_LIMIT_PCT
        with np.errstate(invalid="ignore"):
            at = np.abs(self.pct / 100.0) > thr
        return at & ~np.isnan(self.pct) & self.has


def _f(v):
    try:
        if v is None or v == "":
            return math.nan
        return float(v)
    except ValueError:
        return math.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-449")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    rb = Rebuilt(repo, args.exp)

    findings = []
    comparisons = []

    def add(level, name, measured, expected, note=""):
        d = rel_diff(measured, expected)
        rec = {"level": level, "item": name, "measured": measured,
               "expected": expected, "rel_diff": d, "note": note}
        comparisons.append(rec)
        if level == "blocking" and d > TOL:
            findings.append(rec)
        return d

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    # ---- 1. panel-level cross-checks (returns, mask) ----
    rng = np.random.default_rng(7)
    idx = rng.choice(rb.t * rb.n, size=min(4000, rb.t * rb.n), replace=False)
    pi = np.array([int(k) for k in idx])
    pj = np.array([int(k) for k in (idx % rb.n)])
    d_ret = 0.0
    for k in range(len(pi)):
        i, j = pi[k], pj[k]
        a, b = rb.ret[i, j], panel["r_eff"][i, j]
        if math.isnan(a) and math.isnan(b):
            continue
        d_ret = max(d_ret, rel_diff(a, b))
    add("blocking", "r_eff panel cross-check (4000 random cells)", d_ret, 0.0,
        "max relative difference between recomputed and stored returns")

    d_mask = float((rb.m_exec != panel["m_exec"]).mean())
    add("blocking", "M_exec mask equality", d_mask, 0.0,
        "fraction of cells that differ")
    for arm in ARMS:
        d = float((rb.m[arm] != panel[f"m_{arm}"]).mean())
        add("blocking", f"M_factor[{arm}] mask equality", d, 0.0,
            "fraction of cells that differ")

    # ---- 2. metric recomputation per arm ----
    for arm in ARMS:
        w_npz = np.load(os.path.join(exp_dir, f"weights_{arm}.npz"),
                        allow_pickle=True)
        p_npz = np.load(os.path.join(exp_dir, f"pred_{arm}.npz"),
                        allow_pickle=True)
        fac = np.load(os.path.join(exp_dir, f"factors_{arm}.npz"),
                      allow_pickle=True)
        weights = w_npz["weights"]
        pred = p_npz["pred"]
        model = json.load(open(os.path.join(exp_dir, f"metrics_{arm}.json")))
        dates = rb.dates
        test_sel = np.array([d >= "2022-01-01" and d <= "2024-12-31"
                             for d in dates])
        tidx = np.nonzero(test_sel)[0]
        valid = fac["masks"].all(axis=2)

        for cost_key in ("main", "sensitivity_5bps"):
            net, gross, turn = [], [], []
            for t in tidx:
                r = rb.ret[t]
                r = np.nan_to_num(r, nan=0.0)
                wp = weights[t - 1] if t > 0 else np.zeros(rb.n)
                dw = float(np.abs(weights[t] - wp).sum())
                g = float(wp @ r)
                c = model[cost_key]["cost_bps"] * 1e-4
                gross.append(g)
                net.append(g - c * dw)
                turn.append(dw)
            net = np.array(net)
            turn = np.array(turn)
            cum = np.cumprod(1.0 + net)
            mdd = float(np.min(cum / np.maximum.accumulate(cum) - 1.0))
            days = (np.datetime64(dates[tidx[-1]]) -
                    np.datetime64(dates[tidx[0]])).astype(int)
            total = float(cum[-1] - 1.0)
            ann = (1.0 + total) ** (365.25 / days) - 1.0
            vol = float(np.std(net, ddof=1) * np.sqrt(252))
            sharpe = (ann - RF) / vol if vol > 0 else None
            ics = []
            for t in tidx:
                ok = valid[t - 1] if t > 0 else np.zeros(rb.n, dtype=bool)
                ok = ok & np.isfinite(pred[t]) & ~np.isnan(rb.ret[t])
                if ok.sum() >= 10:
                    ics.append(float(np.corrcoef(pred[t][ok],
                                                rb.ret[t][ok])[0, 1]))
            ics = np.array(ics)
            add("blocking", f"[{arm}/{cost_key}] sharpe", sharpe,
                model[cost_key]["sharpe_rf1.5"])
            add("blocking", f"[{arm}/{cost_key}] ann_return", ann,
                model[cost_key]["ann_return"])
            add("blocking", f"[{arm}/{cost_key}] ann_vol", vol,
                model[cost_key]["ann_vol"])
            add("blocking", f"[{arm}/{cost_key}] max_drawdown", mdd,
                model[cost_key]["max_drawdown"])
            add("blocking", f"[{arm}/{cost_key}] turnover_two_sided",
                float(turn.sum()),
                model[cost_key]["turnover_two_sided_total"])
            add("blocking", f"[{arm}/{cost_key}] apparent_ic",
                float(ics.mean()) if len(ics) else None,
                model[cost_key]["apparent_ic_mean"])
            add("blocking", f"[{arm}/{cost_key}] total_return", total,
                model[cost_key]["total_return"])
            add("blocking", f"[{arm}/{cost_key}] avg_active_stocks",
                float(np.mean([(np.abs(weights[t]) > 1e-9).sum()
                               for t in tidx])),
                model[cost_key]["avg_active_stocks"])

        # ---- 3. factor spot-check (independent recomputation) ----
        names = list(fac["names"])
        vals = fac["values"]
        m_arm = rb.m[arm]
        for fname, win in (("ts_mean_ret_20", 20), ("ts_rank_close_60", 60)):
            if fname not in names:
                continue
            k = names.index(fname)
            cells = rng.choice(rb.t * rb.n, size=200, replace=False)
            worst = 0.0
            for cell in cells:
                i, j = int(cell) // rb.n, int(cell) % rb.n
                if not m_arm[i, j]:
                    continue
                if fname == "ts_mean_ret_20":
                    lo = max(0, i - win + 1)
                    seg = rb.ret[lo:i + 1, j]
                    ok = m_arm[lo:i + 1, j] & ~np.isnan(seg)
                    if ok.sum() == 0:
                        continue
                    expect = float(np.nanmean(np.where(ok, seg, np.nan)))
                else:
                    lo = max(0, i - win + 1)
                    seg = rb.close_ff[lo:i + 1, j]
                    ok = m_arm[lo:i + 1, j]
                    if ok.sum() < 2:
                        continue
                    last = seg[-1]
                    expect = float((seg[ok] <= last).sum() / ok.sum())
                got = float(vals[i, j, k])
                if not math.isnan(expect):
                    worst = max(worst, rel_diff(got, expect))
            add("suggestion", f"[{arm}] factor spot-check {fname}", worst, 0.0,
                "max relative difference on 200 sampled cells")

    blocking = [f for f in findings if f["level"] == "blocking"]
    out = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "script": "audit/recompute_work449.py",
        "independence": ("re-reads data/<exp>/*.csv and recomputes returns, "
                          "masks and all metrics with its own code; imports no "
                          "modelling-stage function"),
        "tolerance_rel_diff": TOL,
        "blocking": len(blocking),
        "suggestion": len([c for c in comparisons if c["level"] == "suggestion"
                           and c["rel_diff"] > TOL]),
        "comparisons": comparisons,
        "findings": blocking,
    }
    json.dump(out, open(os.path.join(exp_dir, "audit_findings.json"), "w"),
              indent=1)
    print(f"blocking={out['blocking']} suggestion={out['suggestion']} "
          f"comparisons={len(comparisons)}")
    for c in comparisons:
        if c["rel_diff"] > TOL:
            print(f"  [{c['level']}] {c['item']}: measured={c['measured']} "
                  f"expected={c['expected']} rel_diff={c['rel_diff']:.3e}")
    return 0 if not blocking else 1


if __name__ == "__main__":
    sys.exit(main())
