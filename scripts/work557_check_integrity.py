#!/usr/bin/env python3
"""WORK-557 stage 3 — data integrity assertions (WORK-557 preregistration.md §1).

Runs the five assertions I1-I5 that must pass before any training starts and
writes the measured values to experiments/WORK-557/integrity_check.json.

  I1  qlib binary calendar alignment: for a random sample of stocks, every
      quoted close read through `D.features` equals the source CSV close
      (relative error < 1e-6).
  I2  no date shift: the quoted series is dense between the stock's first and
      last quoted day (suspended days are forward-filled, not packed).
  I3  the dataset arrays are indexed by the same calendar the qlib dump used
      (ds/calendar.txt == qlib_cn/calendars/day.txt, line by line).
  I4  mask_contract.md §5 (new listing): no sample whose first quoted day is
      less than 19 calendar days before the sample date.
  I5  failure criterion 1: test-period PIT coverage and tradable share.

Usage: python scripts/work503_check_integrity.py [--n-stocks 20]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys

import numpy as np
import pandas as pd

# ---- qlib runtime bootstrap -------------------------------------------
# pyqlib 0.9.7 publishes no cp313 wheel and this machine trains on
# Python 3.13.15.  .venv/qlib312 holds the cp312 build plus the numerically
# validated pure-python `_libs` stand-in (see
# experiments/WORK-557/work557_qlib_shim.py; the stand-in was checked
# element-by-element against the real cp39 .pyd, max |diff| = 0).
_REPO_QLIB = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO_QLIB, ".venv", "qlib312"))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-557")
DATA = os.path.join(REPO, "data", "WORK-557")
REL_TOL = 1e-6


def to_qlib_symbol(code: str) -> str:
    mkt, num = code.split(".")
    return f"{mkt.upper()}{num}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-557")
    ap.add_argument("--n-stocks", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    import qlib
    from qlib.data import D
    qlib.init(provider_uri=os.path.join(DATA, "qlib_cn"), region="cn")

    cal = pd.DatetimeIndex(D.calendar(start_time="2012-01-01", end_time="2024-12-31"))
    out = {"experiment": args.exp, "calendar_days": len(cal),
           "calendar_start": str(cal[0].date()), "calendar_end": str(cal[-1].date())}

    # ---- I1 / I2 -------------------------------------------------------
    codes = sorted({r["code"] for r in csv.DictReader(
        open(os.path.join(EXP, "pool_membership.csv"), encoding="utf-8"))})
    rng = random.Random(args.seed)
    pick = rng.sample(codes, min(args.n_stocks, len(codes)))
    mismatches, max_rel, n_quotes, n_nan_inside = [], 0.0, 0, 0
    per_stock = []
    for code in pick:
        sym = to_qlib_symbol(code)
        df = D.features([sym], ["$close"], start_time="2012-01-01",
                        end_time="2024-12-31")
        s = df["$close"]
        s.index = df.index.get_level_values("datetime")
        hfq = pd.read_csv(os.path.join(DATA, "sina_hfq", f"{code}.csv"))
        hfq["date"] = hfq["date"].astype(str).str[:10]
        hfq = hfq.set_index("date")["close"]
        stock_bad, stock_rel, stock_quotes = 0, 0.0, 0
        for d, v in hfq.items():
            if d not in cal:
                continue
            stock_quotes += 1
            n_quotes += 1
            if pd.Timestamp(d) not in s.index:
                mismatches.append({"code": code, "date": d, "error": "row_missing"})
                stock_bad += 1
                continue
            q = float(s.loc[pd.Timestamp(d)])
            if not np.isfinite(q):
                mismatches.append({"code": code, "date": d, "error": "value_nan"})
                stock_bad += 1
                continue
            rel = abs(q - v) / abs(v) if v else abs(q - v)
            stock_rel = max(stock_rel, rel)
            if rel > REL_TOL:
                mismatches.append({"code": code, "date": d, "error": "value_diff",
                                    "qlib": q, "csv": v, "rel": rel})
                stock_bad += 1
        max_rel = max(max_rel, stock_rel)
        fin = np.isfinite(s.to_numpy())
        nan_inside = int((~fin).sum())
        n_nan_inside += nan_inside
        per_stock.append({"code": code, "quotes": stock_quotes,
                          "max_rel_err": stock_rel, "mismatches": stock_bad,
                          "rows": int(len(fin)), "nan_rows": nan_inside})
    out["I1_alignment"] = {
        "stocks_checked": len(pick), "quotes_checked": n_quotes,
        "max_rel_err": max_rel, "tolerance": REL_TOL,
        "mismatches": len(mismatches), "first_mismatches": mismatches[:5],
        "passed": len(mismatches) == 0 and max_rel < REL_TOL,
        "per_stock": per_stock,
    }
    out["I2_no_date_shift"] = {
        "nan_rows_inside_range": n_nan_inside,
        "passed": n_nan_inside == 0,
        "note": "suspended days are forward-filled (mask_contract.md §2.2), "
                "so the series must be dense between first and last quote",
    }

    # ---- I3 ------------------------------------------------------------
    p_cal = os.path.join(DATA, "ds", "calendar.txt")
    p_qlib = os.path.join(DATA, "qlib_cn", "calendars", "day.txt")
    a = [l.strip() for l in open(p_cal, encoding="utf-8") if l.strip()]
    b = [l.strip() for l in open(p_qlib, encoding="utf-8") if l.strip()]
    # 2026-11-28 修：两份日历**日期完全相同、只是字符串格式不同**
    # （ds 写 '2006-01-04'，qlib 写 '20060104'），逐字面比较会得到 identical=False
    # 这种假阴性。这里按解析后的日期比较，同时把两种原始格式都记进产物。
    _da = pd.to_datetime(pd.Series(a), errors="coerce")
    _db = pd.to_datetime(pd.Series(b), errors="coerce")
    _identical_parsed = bool(len(_da) == len(_db) and (_da.values == _db.values).all())
    _identical_literal = a == b
    out["I3_calendar_identity"] = {
        "ds_calendar": p_cal, "qlib_calendar": p_qlib,
        "n_ds": len(a), "n_qlib": len(b),
        "identical": _identical_parsed,
        "identical_literal": _identical_literal,
        "format_ds": a[0] if a else None, "format_qlib": b[0] if b else None,
        "note": "按解析后的日期比较；两种文件只是格式不同（YYYY-MM-DD vs YYYYMMDD）",
        # 2026-11-28 修：日历文件覆盖 2006–2024（4616 天），而本检查的窗口 cal 是
        # 2012-01-04 起的 3157 天，拿 len(a)==len(cal) 判必然为假。改为「窗口内每一天
        # 都在两份日历里」。
        "n_window_days": len(cal),
        "window_covered": bool(set(pd.DatetimeIndex(cal)).issubset(set(_da))),
        "passed": _identical_parsed and bool(set(pd.DatetimeIndex(cal)).issubset(set(_da))),
    }

    # ---- I4 ------------------------------------------------------------
    samples = pd.read_parquet(os.path.join(DATA, "ds", "sample_index.parquet"))
    samples["datetime"] = pd.to_datetime(samples["datetime"])
    state = pd.read_parquet(os.path.join(DATA, "panel", "trade_state.parquet"))
    state = state.reset_index()
    state["datetime"] = pd.to_datetime(state["datetime"])
    susp = state.pivot(index="datetime", columns="instrument",
                        values="suspended").reindex(index=cal)
    cal_pos = {d: i for i, d in enumerate(cal)}
    viol, checked = [], 0
    for sym, grp in samples.groupby("instrument"):
        traded = (~susp[sym].to_numpy(dtype=bool))
        if not traded.any():
            continue
        first_i = int(np.argmax(traded))
        cum = np.cumsum(traded)
        for d in grp["datetime"]:
            t = cal_pos.get(d)
            if t is None:
                continue
            checked += 1
            if t - first_i + 1 < 20 or cum[t] < 20:
                viol.append({"instrument": sym, "datetime": str(d.date()),
                             "first_quoted": str(cal[first_i].date())})
    out["I4_new_listing_rule"] = {
        "samples_checked": checked, "violations": len(viol),
        "first_violations": viol[:5],
        "rule": "t - first_quoted_day + 1 >= 20 AND cum_traded_days(t) >= 20",
        "passed": len(viol) == 0,
    }

    # ---- I5 ------------------------------------------------------------
    pit = pd.read_parquet(os.path.join(DATA, "panel", "pit.parquet"))
    pit = pit.reindex(index=cal)
    test_mask = (cal >= pd.Timestamp("2022-01-01")) & (cal <= pd.Timestamp("2024-12-31"))
    pit_test = pit.loc[test_mask].to_numpy(dtype=bool)
    state_s = state.set_index(["instrument", "datetime"])
    trad = state["tradable"].to_numpy(dtype=bool)
    st = state_s.reindex(pd.MultiIndex.from_product(
        [pit.columns, cal[test_mask]], names=["instrument", "datetime"]))
    trad_test = st["tradable"].to_numpy(dtype=bool)
    in_pit = pit_test.T.reshape(-1)
    rows_with_bar = float(np.mean(~st["close_raw"].isna().to_numpy()[in_pit]))
    tradable = float(np.mean(trad_test[in_pit]))

    # PIT 成分覆盖率（预注册 v1.0 §9 失败判据 1 的另一半；口径见 v1.3 §6）：
    # 测试段内「按 PIT 名单应持有的 (交易日, 股票) 对」中，**同时**存在于样本
    # 索引与行情快照（当天确实有 bar）的比例。名字进了名单却既没有样本行、
    # 又没有行情，就说明这块数据不可用。
    samples = pd.read_parquet(os.path.join(DATA, "ds", "sample_index.parquet"))
    samples["datetime"] = pd.to_datetime(samples["datetime"])
    test_samples = samples[samples["segment"] == "test"]
    n_days, n_inst = len(cal[test_mask]), len(pit.columns)
    sample_idx = pd.MultiIndex.from_frame(
        test_samples[["datetime", "instrument"]].drop_duplicates())
    pit_idx = pd.MultiIndex.from_product([cal[test_mask], pit.columns],
                                         names=["datetime", "instrument"])
    in_sample = np.asarray(pit_idx.isin(sample_idx),
                           dtype=bool).reshape(n_days, n_inst)
    has_bar = (~st["close_raw"].isna().to_numpy()
               .reshape(n_inst, n_days).T)
    # 预注册 v1.3 §6 的定义：「按 PIT 名单应持有的 (交易日, 股票) 对」中，
    # **在样本索引与行情快照中同时存在**的比例 —— 分母是 PIT 对的个数，
    # 不是「全部 (日期 × 全部 724 只股票) 的笛卡尔积」。
    # (Bug fix 2026-11-22: 早先写成 np.mean(...)，分母是 726×724 = 526,104
    #  的全体格子，算出来 0.414 是「宇宙里的 PIT 格子占比」，不是覆盖率。)
    n_pit_pairs = int(pit_test.sum())
    n_cov = int(np.sum(in_sample & has_bar & pit_test))
    pit_cov = float(n_cov / n_pit_pairs) if n_pit_pairs else float("nan")
    out["I5_test_coverage"] = {
        "test_days": int(test_mask.sum()),
        "pit_pairs": n_pit_pairs,
        "pit_pairs_covered": n_cov,
        "rows_with_bar": rows_with_bar, "tradable_share": tradable,
        "pit_coverage": pit_cov,
        "pit_coverage_threshold": 0.90,
        "threshold": 0.85,
        "pit_coverage_definition": "测试段内 PIT (交易日, 股票) 对中，"
                                   "同时存在于样本索引与行情快照的比例"
                                   "（分母 = PIT 对总数，预注册 v1.3 §6）",
        "passed": bool(tradable >= 0.85 and rows_with_bar >= 0.85
                       and pit_cov >= 0.90),
    }

    out["all_passed"] = all(v.get("passed", False) for k, v in out.items()
                             if isinstance(v, dict) and "passed" in v)
    path = os.path.join(EXP, "integrity_check.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    summary = {k: (v.get("passed") if isinstance(v, dict) else v)
               for k, v in out.items()}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"-> {path}")
    return 0 if out["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
