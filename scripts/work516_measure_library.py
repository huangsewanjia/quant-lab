#!/usr/bin/env python3
"""WORK-516 · measure the quantdb raw library against the ML-Quant Panel spec.

Produces every quantitative claim used in
`docs/mlquant_alignment.md` and `docs/mlquant_export_spec.md` as a single
JSON artefact, so the numbers in those documents are reproducible rather than
transcribed.

This script is a **measurement** tool: it reads `/srv/share/quantdb/raw` and
writes nothing back to it.

Usage
-----
    python scripts/work516_measure_library.py \
        --raw /srv/share/quantdb/raw \
        --out experiments/WORK-516/library_measurements.json

Dependencies: pyarrow, numpy.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pyarrow.parquet as pq

# Window: 1 year of warm-up before the paper's 2015 training start, through the
# end of the paper's 2022-2024 test window.
LO, HI = "20140101", "20241231"
# Paper window and its three segments.
PAPER_LO, PAPER_HI = "20150101", "20241231"
SEGMENTS = {
    "train_2015_2020": ("20150101", "20201231"),
    "val_2021": ("20210101", "20211231"),
    "test_2022_2024": ("20220101", "20241231"),
}
# Dates used for the point-in-time checks (proxy vs exchange band, units, etc).
SAMPLE_DATES = [
    "20150105", "20160104", "20170103", "20180102", "20190102",
    "20200102", "20210104", "20220104", "20230103", "20240102",
]
UNIT_CHECK_DATES = ["20150105", "20200102", "20240226"]
ADJ_LIMIT_DATES = ["20150105", "20170103", "20190102", "20210104", "20230103", "20241231"]
BASE_DATE = "20241231"


def partitions(raw, name, lo=LO, hi=HI):
    root = os.path.join(raw, name)
    out = []
    for p in os.listdir(root):
        key = p.split("=", 1)[1] if "=" in p else ""
        if len(key) == 8 and key.isdigit() and lo <= key <= hi:
            out.append((key, os.path.join(root, p, "part.parquet")))
    return sorted(out)


def table(raw, name, date, cols=None):
    path = os.path.join(raw, name, f"trade_date={date}", "part.parquet")
    return pq.read_table(path, columns=cols).to_pydict()


def file_sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def row_counts(raw):
    res = {}
    for name in ("daily", "adj_factor", "daily_basic", "stk_limit",
                 "suspend_d", "index_daily"):
        ps = partitions(raw, name)
        rows = sum(pq.ParquetFile(f).metadata.num_rows for _, f in ps)
        res[name] = {"partitions": len(ps), "rows": rows}
    # segments
    segs = {}
    for label, (lo, hi) in SEGMENTS.items():
        ps = partitions(raw, "daily", lo, hi)
        rows = sum(pq.ParquetFile(f).metadata.num_rows for _, f in ps)
        segs[label] = {"partitions": len(ps), "rows": rows,
                       "from": lo, "to": hi}
    res["_segments"] = segs
    # full library
    ps = partitions(raw, "daily", "19900101", "20991231")
    rows = sum(pq.ParquetFile(f).metadata.num_rows for _, f in ps)
    res["_full_library"] = {"partitions": len(ps), "rows": rows}
    return res


def universe(raw):
    codes = set()
    for _, f in partitions(raw, "daily", PAPER_LO, PAPER_HI):
        codes.update(pq.read_table(f, columns=["ts_code"])["ts_code"].to_pylist())
    sb = {}
    for p in ("D", "L", "P"):
        path = os.path.join(raw, "stock_basic", f"partition={p}", "part.parquet")
        if not os.path.exists(path):
            continue
        d = pq.read_table(path, columns=["ts_code", "list_date", "name"]).to_pydict()
        for i, c in enumerate(d["ts_code"]):
            sb[c] = {"list_status": p, "list_date": str(d["list_date"][i]),
                     "name": d["name"][i]}
    st = Counter(v["list_status"] for v in sb.values())
    delisted = sorted(c for c in codes if sb.get(c, {}).get("list_status") == "D")
    unknown = sorted(c for c in codes if c not in sb)
    ipo_in_window = sorted(c for c in codes
                           if c in sb and sb[c]["list_date"] >= PAPER_LO)
    return {
        "distinct_codes_in_daily_2015_2024": len(codes),
        "stock_basic_status_counts": dict(st),
        "codes_in_daily_marked_delisted": len(delisted),
        "codes_in_daily_absent_from_stock_basic": len(unknown),
        "codes_listed_on_or_after_2015_01_01": len(ipo_in_window),
        "stock_basic_partition_rows": {
            p: pq.ParquetFile(os.path.join(raw, "stock_basic", f"partition={p}",
                                           "part.parquet")).metadata.num_rows
            for p in ("D", "L", "P")
            if os.path.exists(os.path.join(raw, "stock_basic", f"partition={p}",
                                           "part.parquet"))
        },
    }


def adj_factor_stats(raw):
    mn, mx, total, n = 1e18, -1e18, 0, 0
    first = {}
    for key, f in partitions(raw, "adj_factor", "19900101", "20991231"):
        d = pq.read_table(f, columns=["ts_code", "adj_factor"]).to_pydict()
        a = np.array(d["adj_factor"], dtype=float)
        mn = min(mn, float(np.nanmin(a)))
        mx = max(mx, float(np.nanmax(a)))
        total += len(a)
        n += 1
        for c, v in zip(d["ts_code"], d["adj_factor"]):
            first.setdefault(c, v)
    fv = np.array(list(first.values()), dtype=float)
    have_base = set(pq.read_table(
        os.path.join(raw, "adj_factor", f"trade_date={BASE_DATE}", "part.parquet"),
        columns=["ts_code"])["ts_code"].to_pylist())
    # Window-scoped view: the export window only needs these codes.
    win_codes = set()
    for _, f in partitions(raw, "daily", PAPER_LO, PAPER_HI):
        win_codes.update(pq.read_table(f, columns=["ts_code"])["ts_code"].to_pylist())
    # Export-window adj_factor statistics (this is what the export sees).
    wmn, wmx, wtot, wn = 1e18, -1e18, 0, 0
    for _, f in partitions(raw, "adj_factor", LO, HI):
        a = np.array(pq.read_table(f, columns=["adj_factor"])["adj_factor"],
                     dtype=float)
        wmn = min(wmn, float(np.nanmin(a)))
        wmx = max(wmx, float(np.nanmax(a)))
        wtot += len(a)
        wn += 1
    return {
        "rows_full_library": total,
        "partitions_full_library": n,
        "min": mn,
        "max": mx,
        "min_scaled_one_cent_gap": mn * 0.01,
        "eps_1e_3_is_safe_for_hfq": bool(mn * 0.01 > 1e-3),
        "per_stock_first_observed_min": float(fv.min()),
        "per_stock_first_observed_median": float(np.median(fv)),
        "per_stock_first_observed_max": float(fv.max()),
        "share_stocks_with_first_factor_exactly_1": float(
            np.mean(np.isclose(fv, 1.0, atol=1e-9))),
        "note_first_observed_is_listing_for_full_library": (
            "adj_factor is normalised to 1.0 at a stock's first trading day, "
            "so price * adj_factor is the standard 后复权 (hfq) series."),
        "stocks_missing_base_date_factor_full_library": len(set(first) - have_base),
        "stocks_missing_base_date_factor_in_export_window": len(
            win_codes - have_base),
        "export_window_stats": {
            "rows": wtot, "partitions": wn, "min": wmn, "max": wmx,
            "min_scaled_one_cent_gap": wmn * 0.01,
            "eps_1e_3_is_safe_for_hfq": bool(wmn * 0.01 > 1e-3),
        },
        "base_date": BASE_DATE,
    }


def unit_check(raw):
    """vwap = amount(1000CNY) * 10 / vol(lots)  vs  (H+L+C)/3."""
    out = []
    for k in UNIT_CHECK_DATES:
        d = table(raw, "daily", k)
        close = np.array(d["close"], dtype=float)
        high = np.array(d["high"], dtype=float)
        low = np.array(d["low"], dtype=float)
        vol = np.array(d["vol"], dtype=float)
        amt = np.array(d["amount"], dtype=float)
        ok = vol > 0
        vwap = np.zeros_like(close)
        vwap[ok] = amt[ok] * 10.0 / vol[ok]
        proxy = (high + low + close) / 3.0
        rel = np.abs(vwap[ok] - proxy[ok]) / np.maximum(proxy[ok], 1e-9)
        out.append({
            "date": k, "rows": int(len(close)),
            "median_rel_dev_vs_HLC3": float(np.median(rel)),
            "p90_rel_dev_vs_HLC3": float(np.percentile(rel, 90)),
            "frac_rel_dev_gt_5pct": float(np.mean(rel > 0.05)),
            "zero_vol_rows": int((~ok).sum()),
        })
    return out


def proxy_vs_exchange_band(raw):
    """Is |close/pre_close-1| > 0.098 equivalent to the exchange limit band?"""
    tot = Counter()
    per_date = []
    for k in SAMPLE_DATES:
        d = table(raw, "daily", k)
        codes = d["ts_code"]
        close = np.array(d["close"], dtype=float)
        pre = np.array(d["pre_close"], dtype=float)
        s = table(raw, "stk_limit", k)
        up = dict(zip(s["ts_code"], s["up_limit"]))
        dn = dict(zip(s["ts_code"], s["down_limit"]))
        lim = np.array([up.get(c, np.nan) for c in codes], dtype=float)
        dlim = np.array([dn.get(c, np.nan) for c in codes], dtype=float)
        have = np.isfinite(lim) & np.isfinite(dlim)
        band = have & ((close >= lim - 1e-3) | (close <= dlim + 1e-3))
        proxy = (np.abs(close / pre - 1.0) > 0.098) & have
        tot["both"] += int((band & proxy).sum())
        tot["proxy_only"] += int((proxy & ~band).sum())
        tot["band_only"] += int((band & ~proxy).sum())
        tot["neither"] += int((~band & ~proxy & have).sum())
        per_date.append({
            "date": k, "rows": int(len(codes)),
            "rows_with_band": int(have.sum()),
            "band_events": int(band.sum()), "proxy_events": int(proxy.sum()),
            "proxy_only": int((proxy & ~band).sum()),
            "band_only": int((band & ~proxy).sum()),
            "band_coverage": float(have.mean()),
        })
    checked = sum(tot.values())
    return {
        "per_date": per_date,
        "totals": dict(tot),
        "checked_rows": checked,
        "proxy_false_positive_rate": tot["proxy_only"] / checked,
        "proxy_false_negative_rate": tot["band_only"] / checked,
        "limit_event_share_of_rows": (tot["both"] + tot["proxy_only"]
                                       + tot["band_only"]) / checked,
    }


def adjusted_limit_fidelity(raw):
    """Does scaling close and the limit band by the same factor change the
    'at limit' set at eps=1e-3?  (qfq base = BASE_DATE, for comparison.)"""
    out = []
    for k in ADJ_LIMIT_DATES:
        d = table(raw, "daily", k)
        codes = d["ts_code"]
        close = np.array(d["close"], dtype=float)
        a = table(raw, "adj_factor", k, ["ts_code", "adj_factor"])
        amap = dict(zip(a["ts_code"], a["adj_factor"]))
        ab = table(raw, "adj_factor", BASE_DATE, ["ts_code", "adj_factor"])
        abmap = dict(zip(ab["ts_code"], ab["adj_factor"]))
        s = table(raw, "stk_limit", k)
        lmap = dict(zip(s["ts_code"], s["up_limit"]))
        adj = np.array([amap.get(c, np.nan) for c in codes], dtype=float)
        adjb = np.array([abmap.get(c, np.nan) for c in codes], dtype=float)
        lim = np.array([lmap.get(c, np.nan) for c in codes], dtype=float)
        f = adj / adjb
        have = np.isfinite(lim) & np.isfinite(f)
        raw_at = have & (close >= lim - 1e-3)
        adj_at = have & ((close * f) >= (lim * f) - 1e-3)
        out.append({
            "date": k,
            "qfq_factor_min": float(np.nanmin(f)),
            "qfq_factor_max": float(np.nanmax(f)),
            "raw_at_limit": int(raw_at.sum()),
            "adj_at_limit_eps_1e_3": int(adj_at.sum()),
            "agreement": float(np.mean(raw_at[have] == adj_at[have])),
        })
    return out


def adj_factor_row_coverage(raw):
    """How many (date, code) rows in `daily` have no matching `adj_factor` row?

    Computed as a per-date set difference, so it is exact without holding the
    full 10M-row cross product in memory.
    """
    total = 0
    missing = 0
    dates_with_missing = 0
    worst = []
    for key, f in partitions(raw, "daily", LO, HI):
        dcodes = set(pq.read_table(f, columns=["ts_code"])["ts_code"].to_pylist())
        apath = os.path.join(raw, "adj_factor", f"trade_date={key}", "part.parquet")
        acodes = set(pq.read_table(apath, columns=["ts_code"])["ts_code"].to_pylist())
        gap = dcodes - acodes
        total += len(dcodes)
        missing += len(gap)
        if gap:
            dates_with_missing += 1
            worst.append({"date": key, "missing": len(gap), "daily_rows": len(dcodes)})
    worst.sort(key=lambda x: -x["missing"])
    return {
        "daily_rows": total,
        "rows_without_adj_factor": missing,
        "share_without_adj_factor": missing / total if total else 0.0,
        "dates_with_at_least_one_missing": dates_with_missing,
        "worst_dates": worst[:5],
        "expected_export_rows": total - missing,
    }


def model_parameter_counts():
    """Analytic parameter counts for the official model classes.

    Shapes follow torch.nn: Linear, MultiheadAttention, TransformerEncoderLayer.
    """
    def mlp(in_dim, h):
        return in_dim * h + h + h * h + h + h + 1

    def transformer(in_dim, d=64, depth=2, ff=None):
        ff = 4 * d if ff is None else ff
        mha = 3 * d * d + 3 * d + d * d + d
        layer = mha + (d * ff + ff) + (ff * d + d) + 2 * (2 * d)
        total = layer * depth + (1 * d + d) + in_dim * d + d + (d + 1)
        return total, layer

    return {
        "mlp_in213_hidden128": mlp(213, 128),
        "mlp_in213_hidden256": mlp(213, 256),
        "mlp_in12_hidden128": mlp(12, 128),
        "transformer_in213_d64_depth2": transformer(213, 64, 2)[0],
        "transformer_in213_d64_depth3": transformer(213, 64, 3)[0],
        "transformer_in213_d64_depth4": transformer(213, 64, 4)[0],
        "transformer_per_layer_d64": transformer(213, 64, 1)[1],
        "paper_claims_mlp_params_approx": 44000,
        "paper_claims_transformer_params_approx": 220000,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/srv/share/quantdb/raw")
    ap.add_argument("--out", default="experiments/WORK-516/library_measurements.json")
    args = ap.parse_args()

    if not os.path.isdir(args.raw):
        print(f"raw library not found: {args.raw}", file=sys.stderr)
        return 2

    art = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_root": args.raw,
        "window": {"lo": LO, "hi": HI},
        "paper_window": {"lo": PAPER_LO, "hi": PAPER_HI},
        "row_counts": row_counts(args.raw),
        "universe": universe(args.raw),
        "adj_factor": adj_factor_stats(args.raw),
        "unit_check_vwap": unit_check(args.raw),
        "limit_proxy_vs_exchange_band": proxy_vs_exchange_band(args.raw),
        "adjusted_limit_fidelity": adjusted_limit_fidelity(args.raw),
        "adj_factor_row_coverage": adj_factor_row_coverage(args.raw),
        "model_parameter_counts": model_parameter_counts(),
    }

    # Freeze the identity of the inputs we measured: sha256 of the schema
    # files we actually read (one representative partition per dataset).
    snap = {}
    for name in ("daily", "adj_factor", "stk_limit", "suspend_d",
                 "daily_basic", "stock_basic"):
        cands = sorted(glob.glob(os.path.join(args.raw, name, "**", "part.parquet"),
                                  recursive=True))
        if cands:
            snap[name] = {"file": os.path.relpath(cands[len(cands) // 2], args.raw),
                          "sha256": file_sha256(cands[len(cands) // 2])}
    art["snapshot_sha256"] = snap

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(art, fh, ensure_ascii=False, indent=1)
    print(f"wrote {args.out}")
    print(json.dumps({
        "daily_rows_2014_2024": art["row_counts"]["daily"]["rows"],
        "distinct_codes_2015_2024": art["universe"]["distinct_codes_in_daily_2015_2024"],
        "test_window_partitions": art["row_counts"]["_segments"]["test_2022_2024"]["partitions"],
        "proxy_false_positive_rate": art["limit_proxy_vs_exchange_band"]["proxy_false_positive_rate"],
        "proxy_false_negative_rate": art["limit_proxy_vs_exchange_band"]["proxy_false_negative_rate"],
        "hfq_eps_safe": art["adj_factor"]["eps_1e_3_is_safe_for_hfq"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
