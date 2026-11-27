#!/usr/bin/env python3
"""WORK-637 / phase 2a — data snapshot manifest.

Runs each quantdb endpoint separately, records ``ok`` / ``error`` verbatim,
row count, date range and the sha256 of a canonical serialisation, plus the
sha256 of the derived panel file. Missing endpoints keep ``ok: false`` and are
never deleted from the manifest (``field_dictionary.md`` §6).

    ../.venv/bin/python -u scripts/work637_snapshot.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work637_common as C


def canon_sha(df: pd.DataFrame) -> str:
    d = df.copy()
    for col in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[col]):
            d[col] = d[col].dt.strftime("%Y-%m-%d")
    d = d.sort_index(axis=1).sort_values(d.columns.tolist()).reset_index(drop=True)
    return C.sha256_bytes(d.to_csv(index=False).encode("utf-8"))


ENDPOINTS = {
    "trade_cal_sse": (
        "select cal_date, is_open from trade_cal where exchange='SSE' "
        f"and cal_date between date '{C.CAL_START}' and date '{C.CAL_END}'"),
    "index_weight_csi300": (
        "select index_code, con_code, trade_date, weight from index_weight "
        f"where index_code='{C.INDEX_CODE}'"),
    "v_daily_qfq": (
        "select ts_code, trade_date, open_qfq, high_qfq, low_qfq, close_qfq, vol, amount "
        "from v_daily_qfq where trade_date between date '2013-01-01' and date '2026-11-26' "
        f"and ts_code in (select distinct con_code from index_weight where index_code='{C.INDEX_CODE}')"),
    "daily_raw_open_close": (
        "select ts_code, trade_date, open, close, pre_close, pct_chg from daily "
        "where trade_date between date '2013-01-01' and date '2026-11-26' "
        f"and ts_code in (select distinct con_code from index_weight where index_code='{C.INDEX_CODE}')"),
    "stk_limit": (
        "select ts_code, trade_date, up_limit, down_limit from stk_limit "
        "where trade_date between date '2013-01-01' and date '2026-11-26' "
        f"and ts_code in (select distinct con_code from index_weight where index_code='{C.INDEX_CODE}')"),
    "suspend_d": (
        "select ts_code, trade_date, suspend_type from suspend_d "
        "where trade_date between date '2013-01-01' and date '2026-11-26'"),
    "index_daily_csi300": (
        "select trade_date, close from index_daily where ts_code='000300.SH' "
        "and trade_date between date '2020-12-01' and date '2026-11-26'"),
    "stock_basic": "select ts_code, name, list_date, delist_date from stock_basic",
}


def main():
    os.makedirs(C.EXP, exist_ok=True)
    con = C.connect()
    out = {"generated_at": pd.Timestamp.utcnow().isoformat(), "db": C.DB,
           "data_cutoff": C.CAL_END, "endpoints": {}}
    for name, sql in ENDPOINTS.items():
        rec = {"sql": sql, "ok": False}
        try:
            df = con.execute(sql).df()
            rec.update(ok=True, rows=int(len(df)), cols=list(df.columns),
                       sha256=canon_sha(df))
            tcol = "trade_date" if "trade_date" in df.columns else (
                "cal_date" if "cal_date" in df.columns else None)
            if tcol is not None and len(df):
                d = pd.to_datetime(df[tcol])
                rec["date_range"] = [str(d.min().date()), str(d.max().date())]
                rec["n_dates"] = int(d.nunique())
        except Exception as exc:                      # noqa: BLE001 - verbatim record
            rec["error"] = f"{type(exc).__name__}: {exc}"
        out["endpoints"][name] = rec
        print(f"{name:24s} ok={rec['ok']} rows={rec.get('rows')} {rec.get('error','')}")
    con.close()

    panel_path = os.path.join(C.DATA, "panel.npz")
    if os.path.exists(panel_path):
        out["panel"] = {"path": os.path.relpath(panel_path, C.REPO),
                        "bytes": os.path.getsize(panel_path),
                        "sha256": C.sha256_file(panel_path)}
    else:
        out["panel"] = {"path": os.path.relpath(panel_path, C.REPO), "ok": False,
                        "error": "panel.npz missing — run scripts/work637_common.py"}

    panel = C.load_panel_cached()
    elig = C.eligibility(panel)
    weeks = pd.to_datetime(panel["days"][panel["week_idx"]])
    lab = C.label_matrix(panel, elig)
    chartable = elig["chartable"]
    splits = {"train": ("2014-01-02", "2018-12-28"), "val": ("2019-01-02", "2020-12-31"),
              "test": ("2021-01-04", "2026-11-18")}
    sp = {}
    for k, (lo, hi) in splits.items():
        m = (weeks >= lo) & (weeks <= hi)
        sp[k] = {"start": lo, "end": hi, "weeks": int(m.sum()),
                 "chart_images": int(chartable[m].sum()),
                 "query_eligible_mean": float(elig["eligible"][m].sum(1).mean()),
                 "labels_finite": int(np.isfinite(lab[m]).sum())}
    out["splits"] = sp
    out["grid"] = {"weeks": int(len(weeks)), "first": str(weeks[0].date()),
                   "last": str(weeks[-1].date()),
                   "chartable_total": int(chartable.sum())}
    out["pool"] = {"index": C.INDEX_CODE, "snapshots": int(panel["snapshots"]),
                   "codes_ever": len(panel["codes"]),
                   "pool_obs": int(panel["pool_mask"].sum())}
    out["panel_rows"] = panel["n_rows"]
    p = os.path.join(C.EXP, "snapshot_manifest.json")
    with open(p, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("wrote", p)
    print(json.dumps(out["splits"], indent=1, ensure_ascii=False))
    print(json.dumps(out["grid"], ensure_ascii=False))


if __name__ == "__main__":
    main()
