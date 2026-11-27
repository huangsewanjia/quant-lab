#!/usr/bin/env python3
"""WORK-637 / phase 3 — INDEPENDENT recompute of every published metric.

Contract (audit/README.md): this file must NOT import anything from the
modelling stage (``work637_common`` / ``work637_backtest`` / ...). It re-derives
prices, pool, calendar, portfolio and costs from quantdb with its own SQL and
its own data structures, takes only the *signals* from the dumped CSV, and
compares each item with the modelling-side JSON. Agreement requires a relative
difference <= 1e-6; every mismatch is listed and the RECOMPUTED value is
authoritative.

Independent code paths on purpose:
  * prices live in per-stock dicts keyed by date (no pandas matrices);
  * the weekly grid is rebuilt from the SSE calendar with an ISO-week loop;
  * the decile cut is a sorted list slice, not numpy argsort;
  * draw-down is a running-peak loop, standard deviation is the centred
    sum of squares divided by n-1;
  * turnover is accumulated as a ledger of per-name weight changes.

    ../.venv/bin/python -u audit/recompute_work637.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections import defaultdict

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-637")
DB = "/srv/share/quantdb/db/quantdb.duckdb"

INDEX_CODE = "000300.SH"
TEST_START = "2021-01-04"
TEST_END = "2026-11-18"
MIN_LIST = 250
LOOKBACK = 20
MA_WIN = 20
WIDTH = LOOKBACK + MA_WIN - 1
DECILES = 10
SEED = 42
COST_BPS = (8, 12, 20)
TOL = 1e-6


def tie_salt(seed: int, code: str) -> float:
    h = hashlib.sha256(f"{seed}:{code}".encode()).digest()[:8]
    return int.from_bytes(h, "big") / 2 ** 64 * 1e-12


def load():
    con = duckdb.connect(DB, read_only=True)
    cal = [r[0] for r in con.execute(
        "select cal_date from trade_cal where exchange='SSE' and is_open=1 "
        "and cal_date between date '2013-01-01' and date '2026-11-26' order by 1").fetchall()]
    codes = [r[0] for r in con.execute(
        f"select distinct con_code from index_weight where index_code='{INDEX_CODE}' order by 1").fetchall()]
    rows = con.execute(
        "select q.ts_code, q.trade_date, q.open_qfq, q.close_qfq, d.open, "
        "       l.up_limit, l.down_limit "
        "from v_daily_qfq q "
        "join daily d on d.ts_code=q.ts_code and d.trade_date=q.trade_date "
        "left join stk_limit l on l.ts_code=q.ts_code and l.trade_date=q.trade_date "
        "where q.trade_date between date '2013-01-01' and date '2026-11-26' "
        f"and q.ts_code in (select distinct con_code from index_weight where index_code='{INDEX_CODE}')"
    ).fetchall()
    con.close()
    px = defaultdict(dict)
    for code, d, oq, cq, o, up, dn in rows:
        px[code][d] = (oq, cq, o, up, dn)
    return cal, codes, px


def build_membership():
    con = duckdb.connect(DB, read_only=True)
    iw = pd.read_sql(f"select trade_date, con_code from index_weight where index_code='{INDEX_CODE}'",
                     con)
    con.close()
    snaps = sorted(set(pd.to_datetime(iw["trade_date"]).dt.date))
    by_snap = defaultdict(set)
    for t, c in zip(pd.to_datetime(iw["trade_date"]).dt.date, iw["con_code"]):
        by_snap[t].add(c)
    return snaps, by_snap


def main():
    cal, codes, px = load()
    cal_ix = {d: i for i, d in enumerate(cal)}
    snaps, by_snap = build_membership()
    list_date = {}
    con = duckdb.connect(DB, read_only=True)
    for c, ld in con.execute("select ts_code, list_date from stock_basic").fetchall():
        list_date[c] = ld
    con.close()

    # ---- weekly grid: last SSE trading day of every ISO week ----
    weeks = []
    cur = None
    for d in cal:
        iso = d.isocalendar()[:2]
        if cur is None or cur[0] != iso:
            if cur is not None:
                weeks.append(cur[1])
            cur = (iso, d)
        else:
            cur = (iso, d)
    if cur:
        weeks.append(cur[1])
    weeks = [d for d in weeks if TEST_START <= str(d) <= TEST_END or str(d) <= TEST_END]
    weeks = [d for d in weeks if str(d) >= "2014-01-02"]

    sig = pd.read_csv(os.path.join(EXP, "signals", f"seed{SEED}_knn.csv"))
    sig = sig[sig["K"] == 1000].pivot(index="date", columns="ts_code", values="signal")

    # ---- per-stock windows and returns ----
    order_codes = sorted(codes)
    wd = []
    for w in weeks:
        wi = cal_ix.get(w)
        if wi is None or wi + 6 >= len(cal):
            wd.append(None)
            continue
        wd.append(wi)

    def has_window(code, wi):
        d0 = cal[wi - WIDTH + 1]
        for k in range(wi - WIDTH + 1, wi + 1):
            if cal[k] not in px[code]:
                return False
        return True

    def open_at(code, d):
        v = px[code].get(d)
        return None if v is None else v[0]

    def open_raw(code, d):
        v = px[code].get(d)
        return None if v is None else v[2]

    def limit(code, d):
        v = px[code].get(d)
        return (None, None) if v is None else (v[3], v[4])

    listed = {}
    for c in order_codes:
        ld = list_date.get(c)
        listed[c] = (sum(1 for d in cal if ld is not None and d < ld) - 1) if ld else -10 ** 9

    recs = []
    for w, wi in zip(weeks, wd):
        if wi is None:
            recs.append(None)
            continue
        mem = None
        for s0 in snaps:
            if s0 <= w:
                mem = by_snap[s0]
            else:
                break
        mem = mem or set()
        ent_d, ext_d = cal[wi + 1], cal[wi + 6]
        # "rets" covers every instrument with prices (the modelling side's label
        # matrix does not depend on pool membership); "names" is the investable
        # subset. Weight drift of a name that leaves the index needs the former.
        week = {"date": str(w), "names": [], "rets": {}}
        for c in order_codes:
            if c not in px:
                continue
            o_e = open_at(c, ent_d)
            if o_e is None or o_e <= 0:
                continue
            o_x = open_at(c, ext_d)
            _, dn = limit(c, ext_d)
            orx = open_raw(c, ext_d)
            p_exit, delayed = None, 0
            if o_x is not None and dn is not None and orx is not None and orx > dn + 1e-3:
                p_exit = o_x
            else:
                for dk in range(1, 11):
                    if wi + 6 + dk >= len(cal):
                        break
                    dd = cal[wi + 6 + dk]
                    pp = open_at(c, dd)
                    _, dnl = limit(c, dd)
                    prx = open_raw(c, dd)
                    if pp is not None and dnl is not None and prx is not None and prx > dnl + 1e-3:
                        p_exit, delayed = pp, dk
                        break
            if p_exit is None:
                dd = cal[min(wi + 16, len(cal) - 1)]
                v = px[c].get(dd)
                if v is None or not (v[1] > 0):
                    continue
                p_exit = v[1]
            ret = p_exit / o_e - 1.0
            week["rets"][c] = ret
            # investable: in the index, complete window, seasoned, tradeable entry
            if c not in mem:
                continue
            if not has_window(c, wi):
                continue
            ld = list_date.get(c)
            if ld is None:
                continue
            if sum(1 for k in range(0, wi + 1) if cal[k] >= ld) < MIN_LIST:
                continue
            up, _ = limit(c, ent_d)
            orx_e = open_raw(c, ent_d)
            if up is None or orx_e is None or orx_e >= up - 1e-3:
                continue
            week["names"].append((c, ret))
        recs.append(week)

    # ---- portfolio ----
    prev = {}
    prev_rec = None
    daily_rows = []
    for rec in recs:
        if rec is None or len(rec["names"]) < 2 * DECILES:
            daily_rows.append(None)
            continue
        srow = sig.loc[rec["date"]] if rec["date"] in sig.index else None
        cand = [(c, r) for c, r in rec["names"] if srow is not None and c in srow.index
                and np.isfinite(srow[c])]
        if len(cand) < 2 * DECILES:
            daily_rows.append(None)
            continue
        cand.sort(key=lambda x: (-(x and srow[x[0]] + tie_salt(SEED, x[0])), x[0]))
        k = max(1, len(cand) // DECILES)
        hold = cand[:k]
        short = cand[-k:]
        wts = {c: 1.0 / k for c, _ in hold}
        gross = sum(wts[c] * rr for c, rr in hold)
        bench = sum(rr for _, rr in rec["names"]) / len(rec["names"])
        ws = 1.0 / len(short)
        ls = gross - sum(ws * rr for _, rr in short)
        drift = {}
        denom = 1.0
        # weights held into this week drifted with the return realised BEFORE
        # this rebalance (the previous week's label), never with this week's
        prev_rets = prev_rec["rets"] if prev_rec is not None else {}
        for c, pw in prev.items():
            rr = prev_rets.get(c, 0.0)
            d = pw * (1 + rr)
            drift[c] = d
            denom += pw * rr
        if denom <= 0:
            denom = 1.0
        drift = {c: v / denom for c, v in drift.items()}
        turn = 0.0
        for c in set(list(wts) + list(drift)):
            turn += abs(wts.get(c, 0.0) - drift.get(c, 0.0))
        prev = dict(wts)
        prev_rec = rec
        daily_rows.append({"date": rec["date"], "gross": gross, "bench": bench,
                           "ls": ls, "turnover": turn, "n": k})

    # the published metrics are the TEST-WINDOW metrics only (preregistration §1)
    rows = [r for r in daily_rows
            if r is not None and TEST_START <= r["date"] <= TEST_END]
    pd.DataFrame(rows).to_csv(os.path.join(EXP, "audit_weekly.csv"), index=False,
                              float_format="%.12g")
    test_recs = [r for r in recs if r is not None and TEST_START <= r["date"] <= TEST_END]
    net = np.array([r["gross"] - r["bench"] - r["turnover"] * 12 / 1e4 for r in rows])
    gross_ex = np.array([r["gross"] - r["bench"] for r in rows])
    turn = np.array([r["turnover"] for r in rows])

    def sharpe(x):
        n = len(x)
        m = float(sum(x) / n)
        ss = float(sum((v - m) ** 2 for v in x))
        sd = math.sqrt(ss / (n - 1))
        return m / sd * math.sqrt(52), m, sd

    sr_net, mu, sd = sharpe(net)
    nav, peak, mdd = 1.0, 1.0, 0.0
    for v in net:
        nav *= 1 + v
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    ar = nav ** (52 / len(net)) - 1
    sr_gross, _, _ = sharpe(gross_ex)

    # rank IC
    ics = []
    for rec in test_recs:
        if rec is None or rec["date"] not in sig.index:
            continue
        srow = sig.loc[rec["date"]]
        cs, rr = [], []
        for c, r in rec["names"]:
            if c in srow.index and np.isfinite(srow[c]):
                cs.append(float(srow[c]))
                rr.append(r)
        if len(cs) >= 30:
            rho = spearmanr(cs, rr).correlation
            if np.isfinite(rho):
                ics.append(float(rho))

    cost_rows = {}
    for bp in COST_BPS:
        xn = np.array([r["gross"] - r["bench"] - r["turnover"] * bp / 1e4 for r in rows])
        cost_rows[bp] = sharpe(xn)[0]

    mine = {"weeks": len(rows), "test_start": rows[0]["date"], "test_end": rows[-1]["date"],
            "net_sharpe": sr_net, "gross_sharpe": sr_gross,
            "ann_return_net": ar, "mdd_net": mdd,
            "mean_turnover": float(turn.mean()), "ann_turnover": float(turn.mean() * 52),
            "mean_weekly_net": mu, "sd_weekly_net": sd,
            "rank_ic_mean": float(np.mean(ics)) if ics else float("nan"),
            "rank_ic_weeks": len(ics),
            "cost_sharpe": cost_rows}

    # ---- compare with modelling side ----
    bt = json.load(open(os.path.join(EXP, "backtest_seed42_K1000_c12.json")))
    mm = bt["metrics"]
    model = {"weeks": mm["net_long_only_excess"]["n_weeks"],
             "net_sharpe": mm["net_long_only_excess"]["sharpe"],
             "gross_sharpe": mm["gross_long_only_excess"]["sharpe"],
             "ann_return_net": mm["net_long_only_excess"]["ann_return"],
             "mdd_net": mm["net_long_only_excess"]["mdd"],
             "mean_turnover": mm["net_long_only_excess"]["mean_turnover"],
             "ann_turnover": mm["net_long_only_excess"]["ann_turnover"],
             "mean_weekly_net": mm["net_long_only_excess"]["mean_weekly"],
             "sd_weekly_net": mm["net_long_only_excess"]["sd_weekly"],
             "rank_ic_mean": mm["rank_ic"]["mean"],
             "cost_sharpe": {int(k): v["sharpe"] for k, v in mm["cost_sensitivity"].items()}}

    checks = []

    def cmp_item(name, a, b):
        if a is None or b is None or (isinstance(a, float) and not np.isfinite(a)) \
                or (isinstance(b, float) and not np.isfinite(b)):
            checks.append({"item": name, "recomputed": a, "modelling": b, "ok": False,
                           "note": "non-finite"})
            return
        rel = abs(a - b) / max(abs(b), 1e-12)
        checks.append({"item": name, "recomputed": a, "modelling": b,
                       "abs_diff": abs(a - b), "rel_diff": rel, "ok": rel <= TOL})

    for k in ("weeks", "net_sharpe", "gross_sharpe", "ann_return_net", "mdd_net",
              "mean_turnover", "ann_turnover", "mean_weekly_net", "sd_weekly_net",
              "rank_ic_mean"):
        cmp_item(k, mine[k], model[k])
    for bp in COST_BPS:
        cmp_item(f"cost_sharpe_{bp}bp", mine["cost_sharpe"][bp], model["cost_sharpe"][bp])

    out = {"audit_object": {"signals": f"experiments/WORK-637/signals/seed{SEED}_knn.csv",
                            "modelling_json": "experiments/WORK-637/backtest_seed42_K1000_c12.json",
                            "snapshot": "experiments/WORK-637/snapshot_manifest.json"},
           "tolerance": TOL, "recomputed": mine, "modelling": model, "checks": checks,
           "n_checks": len(checks), "n_ok": sum(1 for c in checks if c["ok"]),
           "n_fail": sum(1 for c in checks if not c["ok"]),
           "authoritative": "recomputed values where a check fails"}
    p = os.path.join(EXP, "audit_recompute.json")
    with open(p, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False, default=bool)
    for c in checks:
        flag = "OK " if c["ok"] else "FAIL"
        print(f"{flag} {c['item']:20s} recomputed={c['recomputed']!r:>24} "
              f"modelling={c['modelling']!r:>24} rel={c.get('rel_diff')}")
    print(f"\n{out['n_ok']}/{out['n_checks']} within {TOL:g} -> {p}")
    return 0 if out["n_fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
