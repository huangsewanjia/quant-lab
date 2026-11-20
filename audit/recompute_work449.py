#!/usr/bin/env python3
"""WORK-449 · stage 3 step 2 — independent recompute gate.

Contract (data_contracts + preregistration §10 gate 5): this script must NOT
import any function of the modelling stage.  It re-reads the raw snapshot CSVs,
rebuilds the panel, the return series and the tradability masks with its own
code, and recomputes IC / Sharpe / annualised return / annualised volatility /
max drawdown / turnover, then compares against the modelling side item by item.

Only two modelling artifacts are consumed, because they cannot be recomputed
without retraining: the model predictions and the portfolio weights.  The
sample set (all 12 factors valid AND the bidirectional label check) is
re-derived here from the factor artifact and this script's own masks, and the
factor values themselves are spot-checked by an independent re-implementation
of the primitives plus the neutralisation.

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
TEST_START, TEST_END = "2022-01-01", "2024-12-31"


def rel_diff(a, b):
    """Relative difference with a zero-safe denominator.

    FYI-3: with denom = max(|a|,|b|,1e-12) a comparison whose *expected*
    value is 0 degenerates -- any non-zero measurement returns exactly 1.0
    regardless of how small it is, so the report showed "rel_diff = 1.0"
    for deviations that are actually ~1e-8.  When the expected value is 0
    the comparison is judged on the ABSOLUTE difference instead, which is
    both well defined and strictly more informative.

    `a` is the measured value, `b` the expected value.
    """
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return float("inf")
    if b == 0.0:
        return abs(a - b)
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


def board_of(code: str) -> str:
    num = code.split(".")[-1]
    if code.startswith("bj") or num.startswith(("4", "8", "92")):
        return "bse"
    if num.startswith(("688", "689")):
        return "star"
    if num.startswith(("300", "301")):
        return "chinext"
    return "main"


def _f(v):
    try:
        if v is None or v == "":
            return math.nan
        return float(v)
    except (TypeError, ValueError):
        return math.nan


class Rebuilt:
    """Independent re-derivation from the raw snapshot CSVs."""

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
        self.em_row = ~np.isnan(self.close)

        # ---- dense panel grid (protocol §5.2 + field_dictionary §5) ----
        first_i = np.full(n, -1, dtype=int)
        last_i = np.full(n, -1, dtype=int)
        for j, code in enumerate(codes):
            idx = [self.pos[r["date"]] for r in rows[code]]
            first_i[j] = min(idx)
            last_i[j] = max(idx)
        grid = np.arange(t)[:, None]
        self.has = (grid >= first_i[None, :]) & (grid <= last_i[None, :])
        self.suspended = self.has & ~self.em_row

        # ---- ST intervals (preregistration v1.1 §3.1) ----
        st_path = os.path.join(self.exp_dir, "st_intervals.json")
        if os.path.exists(st_path):
            st_map = json.load(open(st_path)).get("intervals", {})
            for j, code in enumerate(codes):
                for a, b in st_map.get(code, []):
                    sel = np.array([(x >= a and x <= b) for x in dates])
                    self.st[sel, j] = 1

        # ---- forward fill (field_dictionary §5) ----
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
        self.volume_f = np.where(self.em_row, self.volume, 0.0)
        self.amount_f = np.where(self.em_row, self.amount, 0.0)
        self.amount_f = np.where(np.isnan(self.amount_f),
                                 self.volume_f * ff, self.amount_f)

        # ---- halt-spanning returns ----
        self.ret = np.full((t, n), math.nan)
        for i in range(1, t):
            self.ret[i, :] = ff[i, :] / ff[i - 1, :] - 1.0
        self.ret[~self.has] = math.nan

        # ---- repaired adjusted-price rule (preregistration §4.3) ----
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

        # ---- repaired adjusted price level (compounded r_eff) ----
        self.adj_close = np.full((t, n), math.nan)
        acc = np.ones(n)
        lastv = np.full(n, math.nan)
        for i in range(t):
            r = self.ret[i]
            acc = np.where(np.isnan(r), acc, acc * (1.0 + np.where(np.isnan(r), 0, r)))
            lastv = np.where(np.isnan(r), lastv, acc)
            self.adj_close[i] = lastv

        # ---- masks (protocol §5.1 / §5.2) ----
        self.listed = self._listed()
        self.listed_1y = self._dates()[:, None] > (
            self.listed[None, :] + np.timedelta64(365, "D"))
        self.at_limit = self._at_limit()
        self.m_exec = (self.has & ~self.suspended & ~self.at_limit
                       & self.listed_1y)
        self.m = {
            "no_mask": self.has,
            "halt_only": self.has & ~self.suspended,
            "full_mask": self.m_exec,
        }

    def _dates(self):
        return np.array(self.dates, dtype="datetime64[D]")

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
            # the file also carries "_"-prefixed provenance fields that are
            # not per-stock records -- skip anything that is not a mapping
            ipo = {k: v.get("ipo_date")
                   for k, v in json.load(open(sb)).items()
                   if not k.startswith("_") and isinstance(v, dict)}
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
        return at & ~np.isnan(self.pct) & self.em_row


# ---------------------------------------------------------------------------
# independent factor re-implementation (naive, no reuse of modelling code)
# ---------------------------------------------------------------------------
def ind_ts_mean(series, mask, w):
    """Official masked_fill semantics: masked cells contribute 0, count = #mask."""
    T, N = series.shape
    out = np.full((T, N), np.nan)
    cnt = np.zeros((T, N))
    for i in range(w - 1, T):
        lo = i - w + 1
        seg = series[lo:i + 1]
        m = mask[lo:i + 1]
        filled = np.where(m, seg, 0.0)
        c = m.sum(axis=0)
        out[i] = filled.sum(axis=0) / np.maximum(c, 1.0)
        cnt[i] = c
    return out, cnt > 0


def ind_ts_rank(series, mask, w):
    """Rank of the last element inside the window over the in-mask cells."""
    T, N = series.shape
    out = np.full((T, N), np.nan)
    allv = np.zeros((T, N), dtype=bool)
    for i in range(w - 1, T):
        lo = i - w + 1
        seg = series[lo:i + 1]
        m = mask[lo:i + 1]
        neg = np.where(m, seg, -np.inf)
        last = neg[-1, :]
        rank = (neg <= last[None, :]).sum(axis=0).astype(np.float64)
        c = m.sum(axis=0)
        out[i] = np.where(np.all(m, axis=0), rank / np.maximum(c, 1.0), np.nan)
        allv[i] = np.all(m, axis=0)
    return out, allv


def ind_neutralize(values, valid):
    """MAD de-trend (5x) then z-score, cross-sectionally, within valid cells."""
    T, N = values.shape
    out = np.full((T, N), np.nan)
    for t in range(T):
        ok = valid[t] & np.isfinite(values[t])
        if ok.sum() < 3:
            continue
        x = values[t][ok]
        med = np.median(x)
        mad = np.median(np.abs(x - med))
        if mad > 0:
            xs = np.clip((x - med) / (1.4826 * mad), -5.0, 5.0)
        else:
            xs = x - med
        sd = xs.std()
        if sd <= 0:
            continue
        out[t, ok] = xs / sd
    return out


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
        return rec

    def add_exact(level, name, measured, note=""):
        """Comparison that must hold EXACTLY (mask / panel equality).

        These are not "relative" checks at all: the panel rows and the masks
        are rebuilt from the snapshot CSV and must match the stored arrays
        bit for bit.  Keeping them on the relative scale would silently
        loosen them, so they get their own exact-equality branch.
        """
        ok = (measured == 0.0)
        rec = {"level": level, "item": name, "measured": measured,
               "expected": 0.0, "rel_diff": 0.0 if ok else float("inf"),
               "note": note + " [exact equality required]"}
        comparisons.append(rec)
        if level == "blocking" and not ok:
            findings.append(rec)
        return rec

    panel = np.load(os.path.join(exp_dir, "panel.npz"), allow_pickle=True)
    # ---- 1. panel-level cross-checks (returns, masks) ----
    rng = np.random.default_rng(7)
    idx = rng.choice(rb.t * rb.n, size=min(4000, rb.t * rb.n), replace=False)
    # a flat cell index must be decomposed into (row, col) before indexing a
    # 2-D panel -- using the flat value as a row index runs off the array.
    pi = (idx // rb.n).astype(int)
    pj = (idx % rb.n).astype(int)
    d_ret = 0.0
    for k in range(len(pi)):
        i, j = pi[k], pj[k]
        a, b = rb.ret[i, j], panel["r_eff"][i, j]
        if math.isnan(a) and math.isnan(b):
            continue
        d_ret = max(d_ret, rel_diff(a, b))
    add_exact("blocking", "r_eff panel cross-check (4000 random cells)", d_ret,
              "max relative difference between recomputed and stored returns")
    d_mask = float((rb.m_exec != panel["m_exec"]).mean())
    add_exact("blocking", "M_exec mask equality", d_mask,
              "fraction of cells that differ")
    for arm in ARMS:
        # the rebuilt panel keys masks by arm name ("no_mask"); the stored
        # panel.npz keys them by the protocol's M_factor names (m_no/m_halt/
        # m_full).  Map explicitly instead of f-stringing the arm name.
        key = {"no_mask": "m_no", "halt_only": "m_halt",
               "full_mask": "m_full"}[arm]
        d = float((rb.m[arm] != panel[key]).mean())
        add_exact("blocking", f"M_factor[{arm}] mask equality", d,
                  "fraction of cells that differ")
    d_rows = float((rb.has != panel["has_row"]).mean())
    add_exact("blocking", "panel row grid equality", d_rows,
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
        test_sel = np.array([TEST_START <= d <= TEST_END for d in dates])
        tidx = np.nonzero(test_sel)[0]
        # sample set re-derived from the factor artifact and our own masks
        allv = fac["masks"].all(axis=2)
        m_arm = rb.m[arm]
        sample_ok = np.zeros((rb.t, rb.n), dtype=bool)
        sample_ok[:-1, :] = (allv[:-1, :] & m_arm[:-1, :] & m_arm[1:, :])

        for cost_key in ("main", "sensitivity_5bps"):
            net, gross, turn = [], [], []
            for t in tidx:
                r = np.nan_to_num(rb.ret[t], nan=0.0)
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
                if t + 1 >= rb.t:
                    continue
                p = pred[t]
                y = rb.ret[t + 1]
                ok = sample_ok[t] & np.isfinite(p) & np.isfinite(y)
                if ok.sum() >= 10:
                    ics.append(float(np.corrcoef(p[ok], y[ok])[0, 1]))
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
        names = [str(x) for x in fac["names"]]
        vals = fac["values"]
        specs = (("ts_mean_ret_20", "mean", 20),
                 ("ts_rank_close_60", "rank", 60))
        day_pool = np.nonzero(test_sel)[0]
        sample_days = rng.choice(day_pool, size=min(12, len(day_pool)),
                                 replace=False)
        for fname, kind, win in specs:
            if fname not in names:
                continue
            k = names.index(fname)
            if kind == "mean":
                raw, okm = ind_ts_mean(rb.ret, m_arm, win)
            else:
                raw, okm = ind_ts_rank(rb.adj_close, m_arm, win)
            neu = ind_neutralize(raw, okm)
            worst_val = 0.0
            worst_abs = 0.0
            worst_mask = 0
            for d in sample_days:
                d = int(d)
                if d < win - 1:
                    continue
                cells = okm[d] & np.isfinite(neu[d])
                if cells.sum() == 0:
                    continue
                stored = vals[d, cells, k].astype(np.float64)
                mine = neu[d, cells]
                denom = np.maximum(np.abs(stored), 1e-12)
                worst_val = max(worst_val, float(np.max(np.abs(stored - mine) /
                                                         denom)))
                worst_abs = max(worst_abs, float(np.max(np.abs(stored - mine))))
                worst_mask += int(np.sum(
                    (fac["masks"][d, cells, k]) != okm[d, cells]))
            # FYI-3: the factor values are z-scored, so the expected value is
            # 0 and the *relative* difference degenerates.  The comparison is
            # therefore made on the ABSOLUTE difference against the 1e-6
            # tolerance, and it is promoted to blocking (the deviation is
            # float32 storage precision, ~6e-8, well inside the tolerance).
            rec = add("blocking", f"[{arm}] factor value spot-check {fname}",
                      worst_abs, 0.0,
                      "max ABSOLUTE difference on 12 sampled test days "
                      "(independent re-implementation of the primitive + "
                      f"neutralisation); max relative difference = {worst_val:.3e}")
            rec["max_relative_difference"] = worst_val
            add_exact("suggestion", f"[{arm}] factor mask spot-check {fname}",
                      float(worst_mask),
                      "number of cells whose stored validity mask differs")

    blocking = [f for f in findings if f["level"] == "blocking"]
    out = {
        "experiment": args.exp,
        "generated_at": datetime.now().astimezone().isoformat(),
        "script": "audit/recompute_work449.py",
        "independence": ("re-reads data/<exp>/*.csv and recomputes returns, "
                          "masks, factor values and all metrics with its own "
                          "code; imports no modelling-stage function"),
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
