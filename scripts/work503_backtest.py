#!/usr/bin/env python3
"""WORK-503 stage 3 — TopK-DropN backtest with costs, masks and benchmark alpha.

Protocol (preregistration.md §6, preregistration_v1.1/v1.2, mask_contract.md):

  * daily rebalance, K = 30, N = 5, long only, equal weight (1/K each)
  * sells execute at the close of t (cost 15 bps), buys at the open of t+1
    (cost 5 bps) — the Qlib TopkDropoutStrategy timing the paper cites
  * limit-up / suspended names cannot be bought, limit-down names cannot be
    sold, names that leave the PIT roster are liquidated at their last close
  * RankIC / RankICIR from the daily Spearman correlation of score vs the
    5-day forward return (paper appendix C)
  * AR / MDD / SR from daily log returns net of costs (paper appendix C)
  * benchmark-adjusted alpha against CSI 300 equal-weight and float-cap
    weighted, both built from the PIT roster

Accounting
----------
The portfolio is simulated with **shares and cash**, which reproduces the
preregistration's return convention exactly:

    value(t)  = cash(t) + sum_i shares_i(t) * close_i(t)

  * a name held from before earns close(t-1) -> close(t);
  * a name bought at open(t) earns open(t) -> close(t);
  * a name sold at close(t-1) earns nothing over (t-1, t];
  * cash earns nothing (the proceeds of the t-1 sales wait for the t open).

The daily log return is log(value(t) / value(t-1)) and already contains both
the market move and the two cost legs.

Assertions are live (mask_contract.md §7).  Set WORK503_ASSERT_VIOLATION=1 to
deliberately violate one of them and capture the traceback text.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

K = 30
N_DROP = 5


class MaskAssertionError(AssertionError):
    """Raised by the mask_contract.md §7 assertions."""


def assert_not_limit_down(inst: str, t: int, market: "Market", side: str):
    if bool(market.limit_down[t, market.inst_pos[inst]]):
        raise MaskAssertionError(
            f"assert_not_limit_down: {side} {inst} on "
            f"{market.cal[t].date()} while limit_down is True")


def assert_not_suspended(inst: str, t: int, market: "Market", side: str):
    if bool(market.suspended[t, market.inst_pos[inst]]):
        raise MaskAssertionError(
            f"assert_not_suspended: {side} {inst} on "
            f"{market.cal[t].date()} while the stock is suspended")


def assert_not_limit_up(inst: str, t: int, market: "Market", side: str):
    if bool(market.limit_up[t, market.inst_pos[inst]]):
        raise MaskAssertionError(
            f"assert_not_limit_up: {side} {inst} on "
            f"{market.cal[t].date()} while limit_up is True")


def assert_price_available(inst: str, t: int, market: "Market", px: float,
                           side: str):
    if not np.isfinite(px) or px <= 0:
        raise MaskAssertionError(
            f"assert_price_available: {side} {inst} on "
            f"{market.cal[t].date()} used a non tradable price ({px})")


# ---------------------------------------------------------------------------
# price / state tables
# ---------------------------------------------------------------------------
def _band_for(symbol: str) -> list[float]:
    """Candidate daily price-limit bands for a stock (percent).

    Same rule as scripts/work503_build_panel.py: the board limit plus the
    conservative ST 5% band (mask_contract.md §3.2).
    """
    num = symbol[-6:]
    if num.startswith("688") or num.startswith("689"):
        return [0.20, 0.05]
    if num.startswith("30"):
        return [0.20, 0.05]
    if num.startswith("8") or num.startswith("4"):
        return [0.30, 0.05]
    return [0.10, 0.05]


class Market:
    """Dense calendar x instrument tables for prices, shares and mask flags."""

    def __init__(self, data_dir: str, panel_dir: str):
        cal = [l.strip() for l in open(
            os.path.join(data_dir, "qlib_cn", "calendars", "day.txt"),
            encoding="utf-8") if l.strip()]
        self.cal = pd.DatetimeIndex(cal)
        self.cal_pos = {d: i for i, d in enumerate(self.cal)}

        state = pd.read_parquet(os.path.join(panel_dir, "trade_state.parquet"))
        state = state.reset_index()
        state["datetime"] = pd.to_datetime(state["datetime"])
        self.instruments = sorted(state["instrument"].unique())
        self.inst_pos = {s: i for i, s in enumerate(self.instruments)}
        n_d, n_i = len(self.cal), len(self.instruments)

        def mat(col, dtype):
            m = np.zeros((n_d, n_i), dtype=dtype)
            m[:] = np.nan if dtype is np.float64 else False
            d_idx = state["datetime"].map(self.cal_pos)
            i_idx = state["instrument"].map(self.inst_pos)
            ok = d_idx.notna() & i_idx.notna()
            m[d_idx[ok].to_numpy(), i_idx[ok].to_numpy()] = \
                state.loc[ok, col].to_numpy()
            return m

        self.suspended = mat("suspended", bool)
        self.limit_up = mat("limit_up", bool)
        self.limit_down = mat("limit_down", bool)
        self.tradable = mat("tradable", bool)
        self.prev_close_raw = mat("prev_close_raw", np.float64)

        # ---- dense price table from the raw (unadjusted) Sina csvs --------
        n = len(self.instruments)
        self.open_m = np.full((n_d, n), np.nan)
        self.close_m = np.full((n_d, n), np.nan)
        self.shares_m = np.full((n_d, n), np.nan)
        for path in sorted(glob.glob(os.path.join(data_dir, "sina", "*.csv"))):
            code = os.path.basename(path)[:-4]
            sym = code.replace(".", "")          # sh.600436 -> sh600436
            if sym not in self.inst_pos:
                continue
            df = pd.read_csv(path, usecols=["date", "open", "close",
                                            "outstanding_share"])
            df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
            df = df.set_index("date").reindex(self.cal)
            j = self.inst_pos[sym]
            self.open_m[:, j] = df["open"].to_numpy("float64")
            self.close_m[:, j] = df["close"].to_numpy("float64")
            self.shares_m[:, j] = df["outstanding_share"].to_numpy("float64")
        # forward fill: suspended days keep the last close (mask_contract §2.2)
        self.close_ff = pd.DataFrame(self.close_m).ffill().to_numpy()
        self.shares_ff = pd.DataFrame(self.shares_m).ffill().to_numpy()
        # the previous *traded* close (preregistration_v1.2.md §D)
        traded = np.isfinite(self.close_m)
        self.traded = traded
        self.prev_close_ff = pd.DataFrame(
            np.where(traded, self.close_m, np.nan)).ffill().shift(1).to_numpy()

    # -- helpers ---------------------------------------------------------
    def close(self, inst: str, t: int) -> float:
        return float(self.close_ff[t, self.inst_pos[inst]])

    def open_price(self, inst: str, t: int) -> float:
        return float(self.open_m[t, self.inst_pos[inst]])

    def limit_up_at_open(self, inst: str, t: int) -> bool:
        """The stock opens locked at the upper limit -> it cannot be bought.

        Known at the open, so this is an execution constraint, not a signal
        input (no look-ahead into the close).
        """
        pc = self.prev_close_ff[t, self.inst_pos[inst]]
        op = self.open_m[t, self.inst_pos[inst]]
        if not np.isfinite(pc) or not np.isfinite(op) or pc <= 0:
            return False
        for b in _band_for(inst):
            if op >= pc * (1.0 + b) - 0.005:
                return True
        return False

    def can_sell_at_close(self, inst: str, t: int) -> bool:
        j = self.inst_pos[inst]
        if bool(self.suspended[t, j]):
            return False
        if bool(self.limit_down[t, j]):
            return False
        return np.isfinite(self.close_m[t, j])

    def can_buy_on_close(self, inst: str, t: int) -> bool:
        """Buyable on the decision day (mask_contract.md §3.4).

        A name that is suspended or closed limit-up on day t is removed from
        the Top-K candidate list, so that the slot is taken by the next best
        tradable name instead of by cash.
        """
        j = self.inst_pos[inst]
        if bool(self.suspended[t, j]):
            return False
        if bool(self.limit_up[t, j]):
            return False
        return True

    def can_buy_at_open(self, inst: str, t: int) -> bool:
        """Buyable at the open of day t.

        Two gates, both conservative (they can only remove trades):
          * the mask_contract.md §3.4 flag `limit_up` on the execution day;
          * the name opening locked at the upper limit (knowable at the open).
        """
        j = self.inst_pos[inst]
        if bool(self.suspended[t, j]):
            return False
        if bool(self.limit_up[t, j]):
            return False
        if self.limit_up_at_open(inst, t):
            return False
        return np.isfinite(self.open_m[t, j])


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------
def backtest(pred: pd.DataFrame, market: Market, buy_bps: float = 5.0,
             sell_bps: float = 15.0, k: int = K, n_drop: int = N_DROP,
             violation: str = "") -> dict:
    """pred: columns datetime, instrument, score, label (test period)."""
    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    dates = sorted(pred["datetime"].unique())
    by_date = {d: g.sort_values("score", ascending=False)
               for d, g in pred.groupby("datetime")}

    buy_cost = buy_bps / 1e4
    sell_cost = sell_bps / 1e4

    cash = 1.0
    shares: dict[str, float] = {}
    pending: list[tuple[str, float]] = []      # (instrument, target value)
    daily = []
    trades = []
    violations = []

    prev_value = None
    violation_fired = False
    for di, d in enumerate(dates):
        t = market.cal_pos.get(pd.Timestamp(d))
        if t is None:
            continue

        # --- mask_contract.md §7: the assertions have to fire for real.
        # WORK503_ASSERT_VIOLATION=1 makes the engine attempt one trade the
        # mask forbids; the assertion must then raise.
        # WORK503_ASSERT_VIOLATION=weight removes the cash cap of one buy so
        # that the weight/ledger invariant (assertion 4) can be triggered.
        if violation and not violation_fired and violation != "weight":
            g0 = by_date[d]
            for cand in g0["instrument"]:
                j = market.inst_pos[cand]
                if bool(market.suspended[t, j]) or bool(market.limit_up[t, j]):
                    violation_fired = True
                    assert_not_suspended(cand, t, market, "buy")
                    assert_not_limit_up(cand, t, market, "buy")
                    break

        # ---- 1. execute the orders decided at the previous close ---------
        for inst, tgt, dec_date, dec_value in pending:
            if inst in shares:
                continue
            if not market.can_buy_at_open(inst, t):
                continue
            px = market.open_price(inst, t)
            # --- mask_contract.md §7: the assertions must really be live ---
            assert_not_suspended(inst, t, market, "buy")
            assert_not_limit_up(inst, t, market, "buy")
            assert_price_available(inst, t, market, px, "buy")
            amount = min(tgt, cash / (1.0 + buy_cost))
            if violation == "weight" and not violation_fired:
                # deliberately break the cash cap so that the weight/ledger
                # invariant below fires (mask_contract §7 assertion 4)
                violation_fired = True
                amount = tgt
            if amount <= 0:
                break
            n_sh = amount / (px * (1.0 + buy_cost))
            shares[inst] = n_sh
            cash -= n_sh * px * (1.0 + buy_cost)
            trades.append({"datetime": str(dec_date), "exec_date":
                           str(pd.Timestamp(d).date()),
                           "side": "buy", "instrument": inst,
                           "price": px, "amount": amount,
                           "weight": amount / max(dec_value, 1e-12)})
        pending = []

        # ---- 2. mark to market at the close -------------------------------
        pos_value = sum(sh * market.close(inst, t) for inst, sh in shares.items())
        value = cash + pos_value
        # --- mask_contract.md §7 assertion 4 (the real version) -----------
        # The literal form `sum([1/n]*n) == 1` is a tautology: it can never
        # fail, so it detects nothing.  What is actually checkable is the
        # ledger: the invested weight must not exceed 1 (no leverage / no
        # negative cash) and the cash share must stay non-negative.  A buggy
        # order sizing (e.g. buying more than the cash allows) breaks it.
        if value > 0:
            invested = pos_value / value
            if invested > 1.0 + 1e-9:
                raise MaskAssertionError(
                    f"assert_weight_sum: invested weight {invested!r} > 1 on "
                    f"{pd.Timestamp(d).date()} (positions {pos_value!r} vs "
                    f"value {value!r})")
            if cash < -1e-9 * max(value, 1.0):
                raise MaskAssertionError(
                    f"assert_weight_sum: negative cash {cash!r} on "
                    f"{pd.Timestamp(d).date()} (value {value!r})")
        if prev_value is not None and prev_value > 0:
            r = value / prev_value - 1.0
            daily.append({"datetime": d, "value": value, "ret": r,
                          "n_hold": len(shares), "cash_weight": cash / value
                          if value > 0 else np.nan,
                          "cash": cash,
                          "positions": "|".join(sorted(shares))})
        prev_value = value

        if di == len(dates) - 1:
            break

        # ---- 3. decide the rebalance at the close ------------------------
        g = by_date[d]
        universe = list(g["instrument"])
        score_map = dict(zip(g["instrument"], g["score"].to_numpy()))
        held = [s for s in shares]

        # names that left the PIT roster are liquidated (mask_contract §4.3)
        for inst in list(held):
            if inst in universe:
                continue
            if market.can_sell_at_close(inst, t):
                px = market.close(inst, t)
                proceeds = shares[inst] * px * (1.0 - sell_cost)
                cash += proceeds
                trades.append({"datetime": str(pd.Timestamp(d).date()),
                               "side": "liquidate", "instrument": inst,
                               "price": px, "amount": shares[inst] * px,
                               "weight": shares[inst] * px / max(value, 1e-12)})
                del shares[inst]
            elif not np.isfinite(market.close_m[t, market.inst_pos[inst]]):
                # delisted with no price left: liquidate at the last known close
                px = float(market.close_ff[t, market.inst_pos[inst]])
                if np.isfinite(px):
                    proceeds = shares[inst] * px * (1.0 - sell_cost)
                    cash += proceeds
                    trades.append({"datetime": str(pd.Timestamp(d).date()),
                                   "side": "liquidate_delisted",
                                   "instrument": inst, "price": px,
                                   "amount": shares[inst] * px,
                                   "weight": shares[inst] * px / max(value, 1e-12)})
                    del shares[inst]
            # else: suspended -> the sale is deferred to the next tradable day

        held = [s for s in shares]
        # sell up to n_drop of the lowest-scored holdings that can be sold
        held_sorted = sorted(held, key=lambda s: (score_map.get(s, -np.inf), s))
        dropped = []
        for s in held_sorted:
            if len(dropped) >= n_drop:
                break
            if market.can_sell_at_close(s, t):
                dropped.append(s)
        remaining = [s for s in held if s not in dropped]

        # candidates: the highest-scored names of the day, not held.  The
        # dropped names are still in `shares` here, so they cannot be picked
        # again the same day (no sell-then-rebuy round trip).
        cand = sorted([s for s in universe if s not in shares
                       and market.can_buy_on_close(s, t)],
                      key=lambda s: (-score_map.get(s, -np.inf), s))
        target = list(remaining)
        for s in cand:
            if len(target) >= min(k, len(universe)):
                break
            target.append(s)
        if not target:
            continue
        w = 1.0 / len(target)
        # mask_contract.md §7 断言 4 的**目标构造**侧检查（可失败的版本）：
        # 目标名单必须只由「存续股 + 当日可买候选」构成、规模不超过 K、
        # 且不重复；权重之和由 w = 1/len(target) 构造性保证，此处只做防守性
        # 复核。真正的账本侧检查在每日收盘盯市处（invested ≤ 1、现金非负）。
        if len(set(target)) != len(target):
            raise MaskAssertionError(
                f"assert_weight_sum: duplicated name in the target set on "
                f"{pd.Timestamp(d).date()}")
        if len(target) > min(k, len(universe)):
            raise MaskAssertionError(
                f"assert_weight_sum: target holds {len(target)} names > K on "
                f"{pd.Timestamp(d).date()}")
        bad_target = [s for s in target
                      if s not in universe and s not in shares]
        if bad_target:
            raise MaskAssertionError(
                f"assert_weight_sum: target names {bad_target[:3]} are neither "
                f"in the PIT universe nor held on {pd.Timestamp(d).date()}")
        if abs(sum([w] * len(target)) - 1.0) >= 1e-9:
            raise MaskAssertionError(
                f"assert_weight_sum: weights sum to "
                f"{sum([w] * len(target))!r} on {d}, expected 1 within 1e-9")

        # ---- 4. the weight changes: sells at close(d), buys at open(d+1) --
        # the target value of every name is fixed at the close of d
        tgt_value = {s: w * value for s in target}
        for s in dropped:
            px = market.close(s, t)
            if not np.isfinite(px):
                continue
            assert_not_suspended(s, t, market, "sell")
            assert_not_limit_down(s, t, market, "sell")
            assert_price_available(s, t, market, px, "sell")
            amt = shares[s] * px
            cash += amt * (1.0 - sell_cost)
            trades.append({"datetime": str(pd.Timestamp(d).date()),
                           "side": "sell", "instrument": s, "price": px,
                           "amount": amt, "weight": amt / max(value, 1e-12)})
            del shares[s]
        # reduce the over-weight survivors to their target weight
        for s in list(shares):
            if s not in tgt_value:
                continue
            cur = shares[s] * market.close(s, t)
            if cur <= tgt_value[s] + 1e-12:
                continue
            px = market.close(s, t)
            if not market.can_sell_at_close(s, t):
                continue
            amt = cur - tgt_value[s]
            n_sh = amt / px
            shares[s] -= n_sh
            cash += n_sh * px * (1.0 - sell_cost)
            trades.append({"datetime": str(pd.Timestamp(d).date()),
                           "side": "rebalance_sell", "instrument": s,
                           "price": px, "amount": amt,
                           "weight": amt / max(value, 1e-12)})
        # the buys happen at the next open
        for s in target:
            if s in shares:
                continue
            pending.append((s, tgt_value[s] - 0.0,
                            str(pd.Timestamp(d).date()), value))
        # under-weight survivors are topped up at the next open as well
        for s in list(shares):
            cur = shares[s] * market.close(s, t)
            gap = tgt_value[s] - cur
            if gap > 1e-12:
                pending.append((s, gap, str(pd.Timestamp(d).date()), value))
        pending.sort(key=lambda x: (-score_map.get(x[0], -np.inf), x[0]))

    daily = pd.DataFrame(daily)
    if daily.empty:
        return {"error": "no trading days"}

    # ---- metrics (paper appendix C) -------------------------------------
    g_log = np.log1p(daily["ret"].to_numpy())
    ar = float(np.exp(252 * g_log.mean()) - 1.0)
    wealth = np.exp(np.cumsum(g_log))
    mdd = float(np.max(1.0 - wealth / np.maximum.accumulate(wealth)))
    std = float(g_log.std(ddof=0))
    sr = float(np.sqrt(252) * g_log.mean() / std) if std > 0 else np.nan
    # turnover: daily single-side turnover (prereg §5.2) = (buy + sell) / 2
    td = pd.DataFrame(trades)
    all_dates = pd.Index(daily["datetime"].dt.strftime("%Y-%m-%d"))
    if not td.empty:
        per_day = td.groupby("datetime")["weight"].sum() / 2.0
        per_day = per_day.reindex(all_dates).fillna(0.0)
        turnover = float(per_day.mean())
        turnover_sum_side = float(per_day.mean() * 2.0)
    else:
        turnover, turnover_sum_side = 0.0, 0.0

    ric = _daily_rankic(pred)
    rankic = float(np.nanmean(ric))
    ric_std = float(np.nanstd(ric))
    rankicir = float(rankic / ric_std) if ric_std > 0 else np.nan

    return {
        "rankic": rankic, "rankicir": rankicir,
        "ar": ar, "mdd": mdd, "sr": sr,
        "turnover": turnover, "turnover_both_sides": turnover_sum_side,
        "days": int(len(daily)),
        "mean_holdings": float(daily["n_hold"].mean()),
        "mean_cash_weight": float(daily["cash_weight"].mean()),
        "n_trades": int(len(td)) if not td.empty else 0,
        "daily": daily, "rankic_series": ric,
        "trades": td, "violation": violation,
    }


def _daily_rankic(pred: pd.DataFrame) -> np.ndarray:
    from scipy.stats import spearmanr
    out = []
    for d, g in pred.groupby("datetime"):
        g = g.dropna(subset=["label", "score"])
        if len(g) < 3:
            out.append(np.nan)
            continue
        r = spearmanr(g["label"].to_numpy(), g["score"].to_numpy()).statistic
        out.append(r)
    return np.asarray(out, dtype="float64")


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------
def benchmark_series(market: Market, pit: pd.DataFrame, test_dates) -> dict:
    """CSI300 equal-weight and float-cap-weighted daily returns (PIT roster)."""
    pit = pit.reindex(index=market.cal)
    ew, cw = [], []
    for d in test_dates:
        t = market.cal_pos.get(pd.Timestamp(d))
        if t is None:
            continue
        cols = [c for c in pit.columns if bool(pit.iloc[t][c])]
        rets, caps = [], []
        for c in cols:
            j = market.inst_pos[c]
            pc = market.prev_close_ff[t, j]
            cc = market.close_ff[t, j]
            if not np.isfinite(pc) or not np.isfinite(cc) or pc <= 0:
                continue
            rets.append(cc / pc - 1.0)
            cap = market.shares_ff[t, j] * cc
            caps.append(cap if np.isfinite(cap) and cap > 0 else np.nan)
        if not rets:
            continue
        ew.append(float(np.mean(rets)))
        caps = np.asarray(caps, dtype="float64")
        if np.isnan(caps).all():
            cw.append(ew[-1])
        else:
            caps = np.nan_to_num(caps)
            cw.append(float(np.sum(caps * np.asarray(rets)) / caps.sum()))
    return {"equal_weight": np.asarray(ew), "cap_weighted": np.asarray(cw)}


def benchmark_stats(port: np.ndarray, bench: np.ndarray) -> dict:
    """Excess return / IR / tracking error between a portfolio and a benchmark."""
    n = min(len(port), len(bench))
    p, b = port[:n], bench[:n]
    ex = p - b
    te = float(np.std(ex, ddof=1)) if n > 1 else np.nan
    ir = float(np.mean(ex) / te) if te and te > 0 else np.nan
    ann_ex = float(np.mean(ex) * 252)
    return {"n_days": int(n), "excess_annualized": ann_ex,
            "information_ratio": ir, "tracking_error": te,
            "bench_annualized": float(np.mean(b) * 252),
            "port_annualized": float(np.mean(p) * 252)}


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-503")
    ap.add_argument("--arms", default="full,no_vq,no_moe,no_prior,gru")
    ap.add_argument("--seeds", default="42,2024")
    ap.add_argument("--buy-bps", type=float, default=5.0)
    ap.add_argument("--sell-bps", type=float, default=15.0)
    ap.add_argument("--out", default="backtest_results.json")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    data_dir = os.path.join(repo, "data", args.exp)
    panel_dir = os.path.join(data_dir, "panel")
    violation = os.environ.get("WORK503_ASSERT_VIOLATION", "")

    market = Market(data_dir, panel_dir)
    pit = pd.read_parquet(os.path.join(panel_dir, "pit.parquet"))
    pit.index = pd.to_datetime(pit.index)

    results = {}
    for arm in args.arms.split(","):
        for seed in [int(s) for s in args.seeds.split(",")]:
            p = os.path.join(exp_dir, "preds", f"{arm}_s{seed}.parquet")
            if not os.path.exists(p):
                print(f"missing {p} — skipped")
                continue
            pred = pd.read_parquet(p)
            r = backtest(pred, market, buy_bps=args.buy_bps,
                         sell_bps=args.sell_bps, violation=violation)
            if "error" in r:
                print(f"{arm}_s{seed}: {r['error']}")
                continue
            daily = r.pop("daily")
            ric = r.pop("rankic_series")
            td = r.pop("trades")
            key = f"{arm}_s{seed}"
            daily["net_log_ret"] = np.log1p(daily["ret"].to_numpy())
            daily.to_parquet(os.path.join(exp_dir, "preds", f"{key}_daily.parquet"))
            np.save(os.path.join(exp_dir, "preds", f"{key}_rankic.npy"), ric)
            if not td.empty:
                td.to_parquet(os.path.join(exp_dir, "preds", f"{key}_trades.parquet"))
            # benchmark-adjusted alpha
            bm = benchmark_series(market, pit, sorted(daily["datetime"].unique()))
            for nm, arr in bm.items():
                r[f"alpha_{nm}"] = benchmark_stats(
                    daily["ret"].to_numpy(), arr)
            pd.DataFrame({"datetime": sorted(daily["datetime"].unique()),
                          "equal_weight": bm["equal_weight"],
                          "cap_weighted": bm["cap_weighted"]}).to_parquet(
                os.path.join(exp_dir, "preds", f"{key}_bench.parquet"))
            results[key] = r
            print(f"{key}: RankIC {r['rankic']:.4f} ICIR {r['rankicir']:.4f} "
                  f"AR {r['ar']:.4f} MDD {r['mdd']:.4f} SR {r['sr']:.4f} "
                  f"turn {r['turnover']:.4f} hold {r['mean_holdings']:.1f}")

    out_path = os.path.join(exp_dir, args.out)
    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(f"-> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
