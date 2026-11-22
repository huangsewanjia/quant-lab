#!/usr/bin/env python3
"""WORK-503 stage 2 — build the point-in-time panel.

Produces, for every (instrument, trading day) in the PIT CSI300 universe:

  priors        6 self-built A-share prior factors, all lagged one trading
                day (information available up to t-1, as in the paper)
                  mkt_20d    CSI300 20-day return          (global)
                  size_lm    log(float market cap)          (per stock)
                  value_bp   book-to-price = BVPS / close   (per stock)
                  mom_12_1   12-1 momentum                   (per stock)
                  vol_60d    60-day annualised volatility    (per stock)
                  rev_5d     5-day reversal                  (per stock)
  trade_state   tradability flags used by mask_contract.md
                  suspended / limit_up / limit_down / tradable
  pit           True when the stock is a CSI300 member on that day

PIT rules (data_contracts/field_dictionary.md §5):
  * membership comes from the effective-date roster, never the current list
  * a delisted stock leaves the pool on the day after its last trade
  * suspended days keep the row, price forward-filled, volume zeroed
  * 涨跌停 days are not tradable

Outputs (data/WORK-503/panel/): priors.parquet, trade_state.parquet,
pit.parquet and a summary json.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np
import pandas as pd

MKT = 0.42949e9  # placeholder, overwritten below (not used)


def sha256_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def band_for(code: str) -> list[float]:
    """Candidate daily price-limit bands for a stock (percent)."""
    num = code.split(".")[-1]
    if num.startswith("688") or num.startswith("689"):
        return [0.20, 0.05]          # 科创板 20%, ST 5% is not applicable -> kept conservative
    if num.startswith("30"):
        return [0.20, 0.05]          # 创业板 20%
    if num.startswith("8") or num.startswith("4"):
        return [0.30, 0.05]          # 北交所 30%
    return [0.10, 0.05]              # 主板 10%, ST 5%


def load_membership(exp_dir: str):
    rows = []
    with open(os.path.join(exp_dir, "pool_membership.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((r["effective_date"], r["code"]))
    df = pd.DataFrame(rows, columns=["effective_date", "code"])
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    panel_dir = os.path.join(data_dir, "panel")
    os.makedirs(panel_dir, exist_ok=True)

    # ---- calendar + benchmark -------------------------------------------
    idx = pd.read_csv(os.path.join(data_dir, "index_sh000300.csv"))
    idx["date"] = idx["date"].astype(str).str[:10]
    idx = idx.sort_values("date").reset_index(drop=True)
    cal = pd.DatetimeIndex(idx["date"]).sort_values()
    cal_pos = {d: i for i, d in enumerate(cal)}
    idx_close = idx["close"].to_numpy("float64")
    print(f"calendar {len(cal)} days {cal[0]}..{cal[-1]}")

    # ---- PIT membership --------------------------------------------------
    mem = load_membership(exp_dir)
    eff_dates = sorted(pd.to_datetime(mem["effective_date"].unique()))
    # daily membership: forward-fill the latest effective list
    mem["effective_date"] = pd.to_datetime(mem["effective_date"])
    members_by_eff = {d: set(g["code"]) for d, g in mem.groupby("effective_date")}
    pit = np.zeros((len(cal), 0), dtype=bool)   # filled below
    codes = sorted(mem["code"].unique())          # "sh.600000" (file names)
    syms = [c.replace(".", "") for c in codes]    # "sh600000" (index keys)
    pit = np.zeros((len(cal), len(codes)), dtype=bool)
    col_of = {c: i for i, c in enumerate(codes)}
    eff_idx = {d: i for i, d in enumerate(eff_dates)}
    cur = eff_idx[eff_dates[0]]
    for t, d in enumerate(cal):
        while cur + 1 < len(eff_dates) and eff_dates[cur + 1] <= d:
            cur += 1
        for c in members_by_eff[eff_dates[cur]]:
            pit[t, col_of[c]] = True
    pit_df = pd.DataFrame(pit, index=pd.Index(cal, name="date"),
                          columns=pd.Index(syms, name="instrument"))
    print(f"PIT membership: {len(eff_dates)} effective lists, "
          f"{pit.sum()} (stock, day) pairs, "
          f"mean constituents/day {pit.sum(axis=1).mean():.1f}")

    # ---- per-stock series ------------------------------------------------
    priors_rows, state_rows = [], []
    mkt_prior = np.full(len(cal), np.nan)
    mkt_close = pd.Series(idx_close, index=pd.Index(cal, name="date"))
    mkt_20d = mkt_close / mkt_close.shift(20) - 1.0
    mkt_prior = mkt_20d.to_numpy()

    for code, sym in zip(codes, syms):
        p_raw = os.path.join(data_dir, "sina", f"{code}.csv")
        p_hfq = os.path.join(data_dir, "sina_hfq", f"{code}.csv")
        if not (os.path.exists(p_raw) and os.path.exists(p_hfq)):
            continue
        raw = pd.read_csv(p_raw)
        hfq = pd.read_csv(p_hfq)
        raw["date"] = pd.to_datetime(raw["date"].astype(str).str[:10])
        hfq["date"] = pd.to_datetime(hfq["date"].astype(str).str[:10])
        raw = raw.set_index("date").reindex(cal)
        hfq = hfq.set_index("date").reindex(cal)

        close_raw = raw["close"].to_numpy("float64")
        open_raw = raw["open"].to_numpy("float64")
        volume = raw["volume"].to_numpy("float64")
        shares = raw["outstanding_share"].to_numpy("float64")
        close_hfq = hfq["close"].to_numpy("float64")

        # ---- tradability state
        traded = raw["volume"].notna().to_numpy() & (volume > 0)
        # preregistration_v1.2.md §D: the daily price limit is measured against
        # the previous *traded* close.  Taking the previous calendar day's close
        # instead would leave it undefined on the first day back from a
        # suspension and silently skip the limit test there (漏掩码), which
        # contradicts mask_contract.md §3.3 ("宁可多掩码，不可漏掩码").
        close_raw_f = pd.Series(close_raw, index=cal).where(traded).ffill().to_numpy()
        prev_close = pd.Series(close_raw_f, index=cal).shift(1).to_numpy()
        bands = band_for(code)
        pct = np.full(len(cal), np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            pct = close_raw / prev_close - 1.0
        limit_up = np.zeros(len(cal), dtype=bool)
        limit_down = np.zeros(len(cal), dtype=bool)
        for b in bands:
            lu = prev_close * (1.0 + b)
            ld = prev_close * (1.0 - b)
            with np.errstate(invalid="ignore"):
                limit_up |= (close_raw >= lu - 0.005) & np.isfinite(lu) & (prev_close > 0)
                limit_down |= (close_raw <= ld + 0.005) & np.isfinite(ld) & (prev_close > 0)
        suspended = (~traded) & raw["close"].isna().to_numpy()
        # a row present but with zero volume is a suspended day as well
        suspended |= (~traded) & raw["close"].notna().to_numpy() & (volume == 0)
        # tradable = there is a bar and it is not at a limit
        tradable = traded & ~limit_up & ~limit_down

        # ---- priors (computed on data up to t, then lagged by one day)
        close_s = pd.Series(close_hfq, index=cal)
        mom = (close_s.shift(21) / close_s.shift(252) - 1.0)
        ret1 = close_s.pct_change()
        vol60 = ret1.rolling(60).std() * np.sqrt(252)
        rev5 = close_s.shift(1) / close_s.shift(6) - 1.0
        # suspended days: market cap uses the last available close and the last
        # known share count (mask_contract.md §2.2), otherwise the size prior
        # would go missing on every suspended day.
        shares_f = pd.Series(shares, index=cal).ffill().to_numpy("float64")
        float_mcap = pd.Series(shares_f * close_raw_f, index=cal)
        size_lm = np.log(float_mcap.where(float_mcap > 0))
        # value: BVPS from the fundamentals snapshot, usable from effective_date
        p_fund = os.path.join(data_dir, "fundamentals", f"{code}.csv")
        value_bp = pd.Series(np.nan, index=cal)
        if os.path.exists(p_fund):
            f = pd.read_csv(p_fund)
            f["effective_date"] = pd.to_datetime(f["effective_date"])
            f = f.sort_values("effective_date")
            series = pd.Series(f["bvps"].to_numpy(),
                                index=pd.DatetimeIndex(f["effective_date"]))
            # for each calendar day take the latest BVPS that is already effective
            idx_eff = series.index
            vals = series.to_numpy()
            pos = np.searchsorted(idx_eff.to_numpy(), pd.DatetimeIndex(cal),
                                  side="right") - 1
            ok = pos >= 0
            bp = np.full(len(cal), np.nan)
            bp[ok] = vals[pos[ok]]
            value_bp = pd.Series(bp, index=cal) / close_s.where(close_s > 0)

        pr = pd.DataFrame({
            "mkt_20d": mkt_prior,
            "size_lm": size_lm.to_numpy(),
            "value_bp": value_bp.to_numpy(),
            "mom_12_1": mom.to_numpy(),
            "vol_60d": vol60.to_numpy(),
            "rev_5d": rev5.to_numpy(),
        }, index=pd.Index(cal, name="date")).shift(1)     # lag one trading day

        priors_rows.append(pd.DataFrame(
            pr.to_numpy(), index=pd.MultiIndex.from_product([[sym], cal],
                                                             names=["instrument", "datetime"]),
            columns=pr.columns))
        state_rows.append(pd.DataFrame({
            "suspended": suspended, "limit_up": limit_up,
            "limit_down": limit_down, "tradable": tradable,
            "close_raw": close_raw, "open_raw": open_raw,
            "prev_close_raw": prev_close, "pct_chg": pct,
        }, index=pd.MultiIndex.from_product([[sym], cal],
                                             names=["instrument", "datetime"])))

    priors = pd.concat(priors_rows).sort_index()
    state = pd.concat(state_rows).sort_index()
    priors.to_parquet(os.path.join(panel_dir, "priors.parquet"))
    state.to_parquet(os.path.join(panel_dir, "trade_state.parquet"))
    pit_df.to_parquet(os.path.join(panel_dir, "pit.parquet"))

    # ---- summary ---------------------------------------------------------
    pit_s = pit_df.stack()                       # index: (date, instrument)
    pit_s.index = pit_s.index.swaplevel(0, 1)    # -> (instrument, date)
    in_pit = (pit_s.reindex(state.index).fillna(False)
              .to_numpy().astype(bool))
    summ = {
        "calendar": {"days": len(cal),
                     "start": str(cal[0].date()), "end": str(cal[-1].date())},
        "instruments": len(codes),
        "pit": {"effective_lists": len(eff_dates),
                "effective_dates": [str(pd.Timestamp(d).date()) for d in eff_dates],
                "stock_day_pairs": int(pit.sum()),
                "mean_constituents_per_day": float(pit.sum(axis=1).mean())},
        "coverage_in_pit": {
            "rows_with_bar": float(np.mean(state["tradable"].notna().to_numpy()[in_pit])),
            "suspended_share": float(state["suspended"].to_numpy()[in_pit].mean()),
            "limit_up_share": float(state["limit_up"].to_numpy()[in_pit].mean()),
            "limit_down_share": float(state["limit_down"].to_numpy()[in_pit].mean()),
            "tradable_share": float(state["tradable"].to_numpy()[in_pit].mean()),
        },
        "prior_completeness_in_pit": {
            c: float(np.mean(priors[c].notna().to_numpy()[in_pit]))
            for c in priors.columns
        },
    }
    with open(os.path.join(panel_dir, "panel_summary.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summ, fh, ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
