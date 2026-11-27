#!/usr/bin/env python3
"""WORK-637 / phase 2e — portfolio construction, costs, masks, metrics.

Preregistration §5-§7. Every run goes through ``_require_masks``: the engine
refuses to produce numbers with the tradability mask disabled, and refuses to
use a neighbour whose label was not yet realised (that check lives in
``work637_signal.py``; the provocation run is ``work637_assert_check.py``).

    ../.venv/bin/python -u scripts/work637_backtest.py --seed 42 --cost-bp 12
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C

COST_POINTS = (8, 12, 20)
CODES_REF = [None]


class MaskDisabled(RuntimeError):
    pass


def _require_masks(enabled: bool) -> None:
    if not enabled:
        raise MaskDisabled(
            "tradability mask disabled: refusing to produce a backtest. "
            "The mask (suspension / price-limit at entry and exit) is part of the "
            "pre-registered executable protocol (preregistration §6).")


def exit_prices(panel, elig) -> tuple[np.ndarray, np.ndarray]:
    """Exit price with the carry-forward rule of §6 when the exit day is untradeable."""
    ro = panel["ro"]        # raw: price-limit test only
    oq = panel["o"]         # qfq: the price actually used for returns
    n = len(panel["days"])
    W, S = elig["ext"].shape
    px = elig["ext"].copy()
    delayed = np.zeros((W, S), dtype=np.int32)
    forced = np.zeros((W, S), dtype=bool)
    for k in range(W):
        jx = elig["ext_ix"][k]
        if jx >= n:
            continue
        need = ~elig["ext_trade"][k]
        if not need.any():
            continue
        for j in np.nonzero(need)[0]:
            for d in range(1, 11):
                t = jx + d
                if t >= n:
                    break
                p = oq[t, j]
                lim = panel["dn"][t, j]
                praw = ro[t, j]
                if (not np.isnan(p)) and (not np.isnan(lim)) and (not np.isnan(praw)) \
                        and praw > lim + 1e-3:
                    px[k, j] = p
                    delayed[k, j] = d
                    break
            else:
                cc = panel["c"][min(jx + 10, n - 1), j]
                if np.isfinite(cc):
                    px[k, j] = cc
                    forced[k, j] = True
                    delayed[k, j] = 10
    return px, delayed, forced


def weekly_returns(panel, elig, px):
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where((elig["ent"] > 0) & np.isfinite(px), px / elig["ent"] - 1.0, np.nan)
    return r


def tie_salt(seed: int, code: str) -> float:
    """Seed-dependent but fully reproducible tie-break salt in [0, 1e-12).

    Reproducible on purpose: the independent recompute script re-derives the same
    salt, so a tie cannot make the two sides disagree for a reason that is not
    mathematical.
    """
    import hashlib
    h = hashlib.sha256(f"{seed}:{code}".encode()).digest()[:8]
    return int.from_bytes(h, "big") / 2 ** 64 * 1e-12


def top_decile(sig: np.ndarray, eligible: np.ndarray, seed: int, decile: int = 10,
               codes=None):
    n_w = len(sig)
    hold = np.zeros_like(eligible)
    short = np.zeros_like(eligible)
    for w in range(n_w):
        ix = np.nonzero(eligible[w] & np.isfinite(sig[w]))[0]
        if len(ix) < decile * 2:
            continue
        s = sig[w, ix]
        jitter = np.array([tie_salt(seed, codes[j]) for j in ix]) if codes is not None \
            else rng.random(len(ix)) * 1e-12
        order = np.argsort(-(s + jitter))
        k = max(1, len(ix) // decile)
        hold[w, ix[order[:k]]] = True
        short[w, ix[order[-k:]]] = True
    return hold, short


def run(sig, eligible, r, bench_mask, cost_bp, masks_on, seed):
    _require_masks(masks_on)
    W, S = sig.shape
    hold, short = top_decile(sig, eligible, seed, codes=CODES_REF[0])
    # H2 ablation can leave fewer names than a full decile; still EW on what is there.
    prev = np.zeros(S)
    rows = []
    nav_g = nav_n = nav_ls = 1.0
    for w in range(W):
        h = hold[w]
        if not h.any():
            rows.append(dict(week=w, n=0, gross=0.0, bench=0.0, ls=0.0, cost=0.0, turnover=0.0))
            continue
        wts = h.astype(float) / h.sum()
        rr = np.where(np.isfinite(r[w]), r[w], 0.0)
        gross = float(np.nansum(wts * np.where(np.isfinite(r[w]), r[w], 0.0)))
        bm = np.nonzero(bench_mask[w])[0]
        bench = float(np.nanmean(r[w, bm])) if len(bm) else 0.0
        sh = short[w]
        if sh.any():
            ws = sh.astype(float) / sh.sum()
            ls = gross - float(np.nansum(ws * np.where(np.isfinite(r[w]), r[w], 0.0)))
        else:
            ls = 0.0
        drift = np.where(h | (prev > 0), prev * (1 + rr), 0.0)
        denom = 1.0 + float(np.sum(prev * rr))
        drift = drift / denom if denom > 0 else drift
        turn = float(np.abs(wts - drift).sum())
        cost = turn * cost_bp / 1e4
        prev = wts.copy()
        rows.append(dict(week=w, n=int(h.sum()), gross=gross, bench=bench, ls=ls,
                         cost=cost, turnover=turn))
    df = pd.DataFrame(rows)
    df["excess_gross"] = df["gross"] - df["bench"]
    df["net"] = df["excess_gross"] - df["cost"]
    df["ls_net"] = df["ls"] - df["cost"]
    return df, hold, short


def stats(x: pd.Series, turnover: pd.Series | None = None) -> dict:
    x = x.astype(float).values
    n = len(x)
    mu, sd = float(np.mean(x)), float(np.std(x, ddof=1))
    sr = mu / sd * np.sqrt(52) if sd > 0 else float("nan")
    ar = float((np.prod(1 + x)) ** (52 / n) - 1) if np.all(1 + x > 0) else float("nan")
    if not np.isfinite(ar):
        ar = float((1 + mu) ** 52 - 1)
    nav = np.cumprod(1 + x)
    mdd = float(np.min(nav / np.maximum.accumulate(nav) - 1))
    out = dict(n_weeks=n, mean_weekly=mu, sd_weekly=sd, sharpe=sr, ann_return=ar,
               mdd=mdd, t_stat=mu / (sd / np.sqrt(n)) if sd > 0 else float("nan"))
    if turnover is not None:
        out.update(mean_turnover=float(turnover.mean()),
                   ann_turnover=float(turnover.mean() * 52))
    return out


def block_bootstrap_sr(x: np.ndarray, reps: int, block: int, seed: int):
    rng = np.random.default_rng(seed)
    n = len(x)
    nb = int(np.ceil(n / block))
    draws = np.empty(reps)
    for b in range(reps):
        starts = rng.integers(0, n, size=nb)
        idx = np.concatenate([(np.arange(s, s + block)) % n for s in starts])[:n]
        s = x[idx]
        sd = np.std(s, ddof=1)
        draws[b] = np.mean(s) / sd * np.sqrt(52) if sd > 0 else np.nan
    return {"reps": reps, "block": block,
            "ci95": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))],
            "p_le_0": float(np.mean(draws <= 0)), "draws_seed": seed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True, choices=list(C.SEEDS))
    ap.add_argument("--K", type=int, default=1000)
    ap.add_argument("--cost-bp", type=float, default=12)
    ap.add_argument("--signal-col", default="signal")
    ap.add_argument("--tag", default="")
    ap.add_argument("--masks", default="on", choices=["on", "off"])
    ap.add_argument("--bootstrap", type=int, default=1000)
    args = ap.parse_args()

    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    px, delayed, forced = exit_prices(panel, elig)
    r = weekly_returns(panel, elig, px)
    eligible = elig["eligible"] & np.isfinite(r)
    weeks = pd.to_datetime(panel["days"][panel["week_idx"]])
    in_test = (weeks >= "2021-01-04") & (weeks <= "2026-11-18")

    sp = os.path.join(C.EXP, "signals", f"seed{args.seed}_knn.csv")
    sig = pd.read_csv(sp)
    if args.signal_col == "same_stock":
        ss = pd.read_csv(os.path.join(C.EXP, "signals", f"seed{args.seed}_same_stock.csv"))
        wide = ss.set_index("week")
        S = wide.to_numpy(dtype=float)
    else:
        sub = sig[sig["K"] == args.K]
        wide = sub.pivot(index="week", columns="ts_code", values="signal")
        # weeks with an empty library produce no rows, so the pivot index must be
        # re-anchored on the full week range — otherwise every later week's signal
        # slides forward in time (look-ahead).
        wide = wide.reindex(index=pd.RangeIndex(len(weeks)), columns=panel["codes"])
        S = wide.to_numpy(dtype=float)
        assert S.shape[0] == len(weeks), (S.shape, len(weeks))

    CODES_REF[0] = panel["codes"]
    df, hold, short = run(S, eligible, r, eligible, args.cost_bp,
                          args.masks == "on", args.seed)

    t = in_test
    metrics = {}
    base = stats(df["net"][t], df["turnover"][t])
    metrics["net_long_only_excess"] = base
    metrics["gross_long_only_excess"] = stats(df["excess_gross"][t], df["turnover"][t])
    metrics["gross_long_short"] = stats(df["ls"][t])
    metrics["net_long_short"] = stats(df["ls_net"][t])
    x = df["net"][t].values
    metrics["bootstrap_net"] = block_bootstrap_sr(x, args.bootstrap, 5, args.seed)

    # RankIC over the test window
    ics = []
    for w in np.nonzero(t)[0]:
        m = eligible[w] & np.isfinite(S[w])
        if m.sum() < 30:
            continue
        rho, _ = spearmanr(S[w, m], r[w, m], nan_policy="omit")
        if np.isfinite(rho):
            ics.append(rho)
    ics = np.array(ics)
    metrics["rank_ic"] = {"mean": float(ics.mean()) if len(ics) else float("nan"),
                          "std": float(ics.std(ddof=1)) if len(ics) > 1 else float("nan"),
                          "icir": float(ics.mean() / ics.std(ddof=1)) if len(ics) > 1 else float("nan"),
                          "positive_share": float((ics > 0).mean()) if len(ics) else float("nan"),
                          "weeks": int(len(ics))}

    per_year = {}
    for y in sorted(set(weeks[t].year)):
        m = t & (weeks.year == y)
        per_year[int(y)] = stats(df["net"][m])
    metrics["per_year_net"] = per_year

    # cost sensitivity computed from the same holdings
    cost_rows = {}
    for bp in COST_POINTS:
        d2, _, _ = run(S, eligible, r, eligible, bp, args.masks == "on", args.seed)
        st = stats(d2["net"][t], d2["turnover"][t])
        cost_rows[bp] = st
        cost_rows[bp]["bootstrap_ci95"] = block_bootstrap_sr(
            d2["net"][t].values, args.bootstrap, 5, args.seed)["ci95"]
    metrics["cost_sensitivity"] = cost_rows

    meta = {"seed": args.seed, "K": args.K, "cost_bp": args.cost_bp,
            "signal_col": args.signal_col, "masks": args.masks,
            "test_window": ["2021-01-04", str(weeks[t][-1].date())],
            "test_weeks": int(t.sum()),
            "delayed_exit_share": float((delayed[t] > 0).mean()),
            "forced_exit_share": float(forced[t].mean()),
            "median_names": float(np.median(df["n"][t])),
            "signal_csv_sha256": C.sha256_file(sp),
            "metrics": metrics}
    tag = f"_{args.tag}" if args.tag else ""
    p = os.path.join(C.EXP, f"backtest_seed{args.seed}_K{args.K}_c{args.cost_bp:g}{tag}.json")
    with open(p, "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    df.to_csv(os.path.join(C.EXP, "signals", f"weekly_{os.path.basename(p).replace('.json','')}.csv"),
              index=False, float_format="%.10g")
    print(json.dumps({k: v for k, v in meta.items() if k != "metrics"}, indent=1, ensure_ascii=False))
    for k in ("net_long_only_excess", "gross_long_only_excess", "gross_long_short", "net_long_short"):
        m = metrics[k]
        print(f"{k:26s} SR={m['sharpe']:+.3f} AR={m['ann_return']:+.2%} MDD={m['mdd']:+.2%} "
              f"t={m['t_stat']:+.2f} turn={m.get('ann_turnover', float('nan')):.2f}")
    print("IC", json.dumps(metrics["rank_ic"]))
    print("bootstrap", metrics["bootstrap_net"]["ci95"])
    for bp, m in cost_rows.items():
        print(f"cost {bp:2d}bp SR={m['sharpe']:+.3f} CI={m['bootstrap_ci95']}")


if __name__ == "__main__":
    main()
