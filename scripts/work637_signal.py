#!/usr/bin/env python3
"""WORK-637 / phase 2d — retrieval signal from the recursively expanding library.

For every (stock, week) query, exact top-K cosine neighbours are taken from the
library of charts whose 5-day label is already realised (preregistration §4),
and the signal is the mean realised label of those neighbours (§5).

Also produces the same-stock-only signal for the H2 ablation.

    ../.venv/bin/python -u scripts/work637_signal.py --seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import faiss
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C

KS = (100, 1000, 3000)
KMAX = max(KS)


def tiebreak_order(dists: np.ndarray, ids: np.ndarray, salt: np.ndarray) -> np.ndarray:
    """Stable sort that breaks exact ties by a seed-dependent salt (§4)."""
    return np.lexsort((salt[ids], -dists))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True, choices=list(C.SEEDS))
    ap.add_argument("--emb-seed", type=int, default=None,
                    help="reuse another seed's frozen embedding pass (used for the reduced "
                         "seed check when a second render+encode pass is not affordable); "
                         "the seed still drives tie-breaks and the bootstrap")
    ap.add_argument("--frontier-offset", type=int, default=0,
                    help="DIAGNOSTIC ONLY: push the library frontier past the legal "
                         "point to prove the look-ahead assertion fires")
    ap.add_argument("--smoke-weeks", type=int, default=0,
                    help="DIAGNOSTIC ONLY: stop after N weeks")
    args = ap.parse_args()

    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    lab = C.label_matrix(panel, elig)
    code_ix = {c: j for j, c in enumerate(panel["codes"])}

    emb_seed = args.emb_seed if args.emb_seed is not None else args.seed
    idx = pd.read_parquet(os.path.join(C.DATA, f"index_seed{emb_seed}.parquet"))
    emb = np.load(os.path.join(C.DATA, f"emb_seed{emb_seed}.npy"))
    assert len(idx) == len(emb)

    # row -> (week, stock) and label
    week_of = idx["date"].map({d: w for w, d in
                               zip(range(len(panel["week_idx"])),
                                   pd.to_datetime(panel["days"][panel["week_idx"]]).strftime("%Y-%m-%d"))}).to_numpy()
    stock_of = idx["ts_code"].map(code_ix).to_numpy()
    if np.isnan(week_of.astype(float)).any():
        raise RuntimeError("index row not mapped to a week")
    week_of = week_of.astype(int)
    row_label = lab[week_of, stock_of]

    # deterministic seed salt for tie-breaking
    rng = np.random.default_rng(args.seed)
    salt = rng.permutation(len(idx)).astype(np.int64)

    order = np.argsort(week_of, kind="stable")
    wk_sorted = week_of[order]
    row_start = np.searchsorted(wk_sorted, np.arange(len(panel["week_idx"])), side="left")
    row_end = np.searchsorted(wk_sorted, np.arange(len(panel["week_idx"])), side="right")

    index = faiss.IndexFlatIP(emb.shape[1])
    frontier = 0                      # rows [0, frontier) are in the library
    added_through = -1
    out_rows = []
    t0 = time.time()
    n_violation = 0

    for w in range(len(panel["week_idx"])):
        qidx = panel["week_idx"][w]
        # library = rows with week_of[row] + 6 <= qidx  (label realised)
        while frontier < len(order) and panel["week_idx"][week_of[order[frontier]]] + 6 <= qidx:
            frontier += 1
        if args.frontier_offset:
            frontier = min(len(order), frontier + args.frontier_offset)
        new = order[added_through + 1:frontier]
        if len(new):
            index.add(np.ascontiguousarray(emb[new]))
            added_through = frontier - 1

        qs = order[row_start[w]:row_end[w]]
        if len(qs) == 0 or index.ntotal == 0:
            continue
        D, I = index.search(np.ascontiguousarray(emb[qs]), min(KMAX, index.ntotal))
        # map faiss local ids back to global row ids: ids are insertion order == order[:frontier]
        G = order[:frontier][I]
        # look-ahead assertion: every used neighbour's label must already be realised
        bad = panel["week_idx"][week_of[G]] + 6 > qidx
        if bad.any():
            n_violation += int(bad.sum())
            raise RuntimeError(f"look-ahead violation at week {w}: {int(bad.sum())} neighbours "
                               f"with unrealised labels")
        lb = row_label[G]
        finite = np.isfinite(lb)
        for k in KS:
            kk = min(k, lb.shape[1])
            sub = np.where(finite[:, :kk], lb[:, :kk], np.nan)
            with np.errstate(invalid="ignore"):
                s = np.nanmean(sub, axis=1)
            cnt = finite[:, :kk].sum(1)
            s[cnt == 0] = np.nan
            out_rows.append(pd.DataFrame({
                "week": w, "date": pd.to_datetime(panel["days"][qidx]).strftime("%Y-%m-%d"),
                "ts_code": idx["ts_code"].to_numpy()[qs], "K": k,
                "signal": s, "n_finite": cnt}))
        if args.smoke_weeks and w >= args.smoke_weeks:
            break
        if w % 50 == 0:
            el = time.time() - t0
            print(f"  week {w}/{len(panel['week_idx'])} lib={frontier} q={len(qs)} {el:.0f}s", flush=True)

    sig = pd.concat(out_rows, ignore_index=True)
    os.makedirs(os.path.join(C.EXP, "signals"), exist_ok=True)
    sp = os.path.join(C.EXP, "signals", f"seed{args.seed}_knn.csv")
    sig.to_csv(sp, index=False, float_format="%.10g")

    # ---- H2 ablation: same-stock-only library ----
    same = np.full((len(panel["week_idx"]), len(panel["codes"])), np.nan)
    by_stock = {}
    for r in range(len(idx)):
        by_stock.setdefault(stock_of[r], []).append(r)
    for j, rows in by_stock.items():
        rows = np.array(sorted(rows, key=lambda r: week_of[r]))
        rw = week_of[rows]
        E = emb[rows]
        for w in np.unique(rw):
            qs = rows[rw == w]
            qidx = panel["week_idx"][w]
            usable = rows[panel["week_idx"][rw] + 6 <= qidx]
            if len(usable) == 0:
                continue
            Euse = emb[usable]
            for q in qs:
                sim = Euse @ emb[q]
                top = np.argsort(-sim)[:min(1000, len(usable))]
                lb = lab[week_of[usable[top]], j]
                lb = lb[np.isfinite(lb)]
                if len(lb):
                    same[w, j] = lb.mean()
    same_df = pd.DataFrame(same, columns=panel["codes"])
    same_df.insert(0, "week", np.arange(len(panel["week_idx"])))
    same_df.to_csv(os.path.join(C.EXP, "signals", f"seed{args.seed}_same_stock.csv"),
                   index=False, float_format="%.10g")

    meta = {"seed": args.seed, "emb_seed": emb_seed, "k_list": list(KS), "rows": int(len(sig)),
            "weeks": int(len(panel["week_idx"])), "lookahead_violations": n_violation,
            "signal_csv": os.path.relpath(sp, C.REPO),
            "signal_csv_sha256": C.sha256_file(sp),
            "same_stock_csv_sha256": C.sha256_file(
                os.path.join(C.EXP, "signals", f"seed{args.seed}_same_stock.csv")),
            "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(C.EXP, f"signal_meta_seed{args.seed}.json"), "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    print(json.dumps(meta, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
