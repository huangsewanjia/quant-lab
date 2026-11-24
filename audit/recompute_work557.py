#!/usr/bin/env python3
"""WORK-557 stage 3 — INDEPENDENT recompute of the headline metrics.

Contract (audit/README.md): this script must NOT import any function from the
modelling stage.  It re-derives, from the snapshot CSVs and the prediction
parquets only:
    RankIC, RankICIR, net SR, AR, MDD, mean daily turnover
and compares them item by item with the modelling-side numbers
(experiments/WORK-557/backtest_results.json).  Agreement requires a relative
difference <= 1e-6 on every item; every mismatch is listed and the RECOMPUTED
value is authoritative (preregistration.md §9 failure criterion 5).

The portfolio is rebuilt here through a **different code path** than
scripts/work503_backtest.py:

  * prices live in per-instrument dictionaries keyed by date, the forward
    fill is carried by an explicit loop (no pandas matrices);
  * the portfolio is a ledger of positions with an explicit order book, the
    target set is built with set operations instead of list surgery;
  * the draw-down is measured with a running-peak loop instead of
    `np.maximum.accumulate`;
  * the standard deviation is computed from the centred sum of squares.

A coding error on either side therefore shows up as a mismatch instead of
being silently duplicated.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-557")
DATA = os.path.join(REPO, "data", "WORK-557")
PANEL = os.path.join(DATA, "panel")
TOL = 1e-6
K = 30
N_DROP = 5
BUY_BPS = 5.0
SELL_BPS = 15.0


# ---------------------------------------------------------------------------
# price book (dictionary based, explicit forward fill)
# ---------------------------------------------------------------------------
class PriceBook:
    def __init__(self, data_dir: str):
        self.opens: dict[str, dict] = {}
        self.closes: dict[str, dict] = {}
        self.shares: dict[str, dict] = {}
        self.close_ff: dict[str, dict] = {}
        self.prev_ff: dict[str, dict] = {}
        self.last_close: dict[str, float] = {}
        calendar = [l.strip() for l in open(
            os.path.join(data_dir, "qlib_cn", "calendars", "day.txt"),
            encoding="utf-8") if l.strip()]
        for path in sorted(glob.glob(os.path.join(data_dir, "sina", "*.csv"))):
            sym = os.path.basename(path)[:-4].replace(".", "")
            df = pd.read_csv(path, usecols=["date", "open", "close",
                                            "outstanding_share"])
            df["date"] = df["date"].astype(str).str[:10]
            o, c, sh = {}, {}, {}
            for d, ro, rc, rs in zip(df["date"], df["open"], df["close"],
                                     df["outstanding_share"]):
                o[d] = float(ro)
                c[d] = float(rc)
                sh[d] = float(rs)
            # explicit forward fill over the whole calendar
            ff, last, prev = {}, float("nan"), {}
            for i, d in enumerate(calendar):
                prev[d] = last
                if d in c:
                    last = c[d]
                ff[d] = last
            self.opens[sym] = o
            self.closes[sym] = c
            self.shares[sym] = sh
            self.close_ff[sym] = ff
            self.prev_ff[sym] = prev
            self.last_close[sym] = last

    def close_on(self, sym: str, d: str) -> float:
        ff = self.close_ff.get(sym, {})
        return ff.get(d, np.nan)

    def open_on(self, sym: str, d: str) -> float:
        return self.opens.get(sym, {}).get(d, np.nan)

    def close_exact(self, sym: str, d: str) -> float:
        return self.closes.get(sym, {}).get(d, np.nan)

    def prev_close(self, sym: str, d: str) -> float:
        return self.prev_ff.get(sym, {}).get(d, np.nan)

    def locked_limit_up_at_open(self, sym: str, d: str) -> bool:
        """The name opens locked at the upper limit -> it cannot be bought.

        Knowable at the open (no look-ahead into the close).
        """
        pc = self.prev_close(sym, d)
        op = self.open_on(sym, d)
        if not np.isfinite(pc) or not np.isfinite(op) or pc <= 0:
            return False
        for b in band_for(sym):
            if op >= pc * (1.0 + b) - 0.005:
                return True
        return False


def load_panel():
    pit = pd.read_parquet(os.path.join(PANEL, "pit.parquet"))
    state = pd.read_parquet(os.path.join(PANEL, "trade_state.parquet"))
    state = state.reset_index()
    state["datetime"] = pd.to_datetime(state["datetime"])
    state["date"] = state["datetime"].dt.strftime("%Y-%m-%d")
    state = state.set_index(["instrument", "date"])
    return pit, state


def band_for(sym: str):
    num = sym[-6:]
    if num.startswith("688") or num.startswith("689"):
        return (0.20, 0.05)
    if num.startswith("30"):
        return (0.20, 0.05)
    if num.startswith("8") or num.startswith("4"):
        return (0.30, 0.05)
    return (0.10, 0.05)


# ---------------------------------------------------------------------------
# the independent portfolio ledger
# ---------------------------------------------------------------------------
def recompute(pred: pd.DataFrame, pit: pd.DataFrame, state: pd.DataFrame,
              book: PriceBook) -> dict:
    pred = pred.copy()
    pred["datetime"] = pd.to_datetime(pred["datetime"])
    pred["date"] = pred["datetime"].dt.strftime("%Y-%m-%d")
    day_list = sorted(pred["date"].unique())
    pit_dates = set(pd.to_datetime(pit.index).strftime("%Y-%m-%d"))

    buy_cost = BUY_BPS / 1e4
    sell_cost = SELL_BPS / 1e4

    cash = 1.0
    position: dict[str, float] = {}          # instrument -> shares
    book_orders: list[tuple[str, float, str]] = []   # (inst, amount, decision day)
    order_meta: dict[str, float] = {}        # instrument -> decision value
    values: list[float] = []
    value_days: list[str] = []
    trace: list[dict] = []
    turnover_by_day: dict[str, float] = {}
    sold_weight: dict[str, float] = {}
    bought_weight: dict[str, float] = {}

    for i, day in enumerate(day_list):
        # ---- execute the open orders of this morning --------------------
        for inst, amount, dec_day in list(book_orders):
            if inst in position:
                book_orders.remove((inst, amount, dec_day))
                continue
            row = state.loc[(inst, day)] if (inst, day) in state.index else None
            tradable = (row is not None and not bool(row["suspended"])
                        and not bool(row["limit_up"])
                        and not book.locked_limit_up_at_open(inst, day))
            px = book.open_on(inst, day)
            if not tradable or not np.isfinite(px):
                book_orders.remove((inst, amount, dec_day))
                continue
            spend = min(amount, cash / (1.0 + buy_cost))
            if spend <= 0:
                break
            shares = spend / (px * (1.0 + buy_cost))
            position[inst] = shares
            cash -= shares * px * (1.0 + buy_cost)
            bought_weight[dec_day] = bought_weight.get(dec_day, 0.0) + \
                spend / order_meta.get(inst, 1.0)
            book_orders.remove((inst, amount, dec_day))
        book_orders = []
        order_meta = {}

        # ---- mark to market at the close ---------------------------------
        value = cash
        for inst, sh in position.items():
            value += sh * book.close_on(inst, day)
        values.append(value)
        value_days.append(day)
        trace.append({"date": day, "value": value, "cash": cash,
                      "n_pos": len(position),
                      "positions": "|".join(sorted(position))})

        if i == len(day_list) - 1:
            break

        # ---- the rebalance decided at this close ------------------------
        block = pred[pred["date"] == day]
        scores = dict(zip(block["instrument"], block["score"].to_numpy()))
        universe = list(scores)
        members = set()
        if day in pit_dates:
            row = pit.loc[pd.Timestamp(day)]
            members = {c for c in pit.columns if bool(row[c])}
            universe = [s for s in universe if s in members]
        if not universe:
            continue

        # names that left the roster are liquidated
        for inst in list(position):
            if inst in members:
                continue
            row = state.loc[(inst, day)] if (inst, day) in state.index else None
            sellable = (row is not None and not bool(row["suspended"])
                        and not bool(row["limit_down"])
                        and np.isfinite(book.close_exact(inst, day)))
            if sellable:
                px = book.close_exact(inst, day)
            elif not np.isfinite(book.close_exact(inst, day)):
                # delisted with no price left: liquidate at the last close
                px = book.close_on(inst, day)
                if not np.isfinite(px):
                    continue
            else:
                continue        # suspended -> the sale is deferred
            proceeds = position[inst] * px * (1.0 - sell_cost)
            cash += proceeds
            sold_weight[day] = sold_weight.get(day, 0.0) + \
                position[inst] * px / max(value, 1e-12)
            del position[inst]

        held = set(position)
        # the drop list: the lowest-scored sellable holdings, up to N_DROP
        ordered = sorted(held, key=lambda s: (scores.get(s, -np.inf), s))
        drop_set = set()
        for inst in ordered:
            if len(drop_set) >= N_DROP:
                break
            row = state.loc[(inst, day)] if (inst, day) in state.index else None
            if row is None or bool(row["suspended"]) or bool(row["limit_down"]):
                continue
            if not np.isfinite(book.close_exact(inst, day)):
                continue
            drop_set.add(inst)
        survivors = held - drop_set

        # the target set: the survivors plus the best new candidates
        ranked = sorted(universe, key=lambda s: (-scores.get(s, -np.inf), s))
        target = list(survivors)
        for inst in ranked:
            if len(target) >= min(K, len(universe)):
                break
            if inst in held:
                continue
            row = state.loc[(inst, day)] if (inst, day) in state.index else None
            if row is None or bool(row["suspended"]) or bool(row["limit_up"]):
                continue          # mask_contract.md §3.4: not a candidate
            target.append(inst)
        if not target:
            continue
        weight = 1.0 / len(target)

        # sell the dropped names at the close
        for inst in drop_set:
            if inst not in position:
                continue
            px = book.close_exact(inst, day)
            if not np.isfinite(px):
                continue
            amount = position[inst] * px
            cash += amount * (1.0 - sell_cost)
            sold_weight[day] = sold_weight.get(day, 0.0) + amount / max(value, 1e-12)
            del position[inst]

        # trim the over-weight survivors back to the equal weight
        target_value = weight * value
        for inst in list(position):
            cur = position[inst] * book.close_exact(inst, day)
            if not np.isfinite(cur) or cur <= target_value + 1e-12:
                continue
            row = state.loc[(inst, day)] if (inst, day) in state.index else None
            if row is None or bool(row["suspended"]) or bool(row["limit_down"]):
                continue
            px = book.close_exact(inst, day)
            excess = cur - target_value
            n_sh = excess / px
            position[inst] -= n_sh
            cash += n_sh * px * (1.0 - sell_cost)
            sold_weight[day] = sold_weight.get(day, 0.0) + excess / max(value, 1e-12)

        # queue the buys for the next open
        for inst in target:
            if inst in position:
                continue
            book_orders.append((inst, target_value, day))
            order_meta[inst] = value
        for inst in list(position):
            cur = position[inst] * book.close_exact(inst, day)
            gap = target_value - cur
            if gap > 1e-12:
                book_orders.append((inst, gap, day))
                order_meta[inst] = value
        book_orders.sort(key=lambda x: (-scores.get(x[0], -np.inf), x[0]))

    # ---- daily returns ---------------------------------------------------
    arr = np.asarray(values[1:], dtype="float64")
    prev = np.asarray(values[:-1], dtype="float64")
    rets = arr / prev - 1.0
    days = value_days[1:]

    # ---- metrics ---------------------------------------------------------
    logs = []
    for r in rets:
        logs.append(np.log1p(r))
    mean_log = sum(logs) / len(logs)
    ar = float(np.exp(252.0 * mean_log) - 1.0)
    # draw-down with an explicit running peak
    wealth = 1.0
    peak = 1.0
    mdd = 0.0
    for lg in logs:
        wealth *= np.exp(lg)
        if wealth > peak:
            peak = wealth
        dd = 1.0 - wealth / peak
        if dd > mdd:
            mdd = dd
    var = sum((x - mean_log) ** 2 for x in logs) / len(logs)
    sd = float(np.sqrt(var))
    sr = float(np.sqrt(252.0) * mean_log / sd) if sd > 0 else np.nan

    # ---- turnover ---------------------------------------------------------
    turn = []
    for d in days:
        turn.append((sold_weight.get(d, 0.0) + bought_weight.get(d, 0.0)) / 2.0)
    turnover = float(sum(turn) / len(turn)) if turn else 0.0

    # ---- RankIC ------------------------------------------------------------
    ric = []
    for d, g in pred.groupby("date"):
        g = g.dropna(subset=["label", "score"])
        if len(g) < 3:
            ric.append(np.nan)
            continue
        ric.append(spearmanr(g["label"].to_numpy(),
                              g["score"].to_numpy()).statistic)
    ric = np.asarray(ric, dtype="float64")
    rankic = float(np.nanmean(ric))
    sd_ric = float(np.nanstd(ric))
    rankicir = float(rankic / sd_ric) if sd_ric > 0 else np.nan

    return {"rankic": rankic, "rankicir": rankicir, "ar": ar, "mdd": float(mdd),
            "sr": sr, "turnover": turnover, "days": int(len(rets)),
            "_trace": trace}


def independence_selfcheck() -> dict:
    """Check that this audit script does not reuse the modelling stage.

    audit/README.md rule 2 forbids the audit script from importing any
    function of the modelling stage; the check is made mechanical here so the
    artefact carries the evidence instead of the claim.

    The README also asks for a standard-library-only implementation.  That
    part is **not** satisfied and is disclosed rather than hidden: the
    prediction snapshots are parquet, and a parquet reader does not exist in
    the standard library.  numpy / pandas / scipy are used for I/O and for
    the rank correlation only; every portfolio number is recomputed by the
    dictionary/ledger code path below, which shares no function with
    scripts/work503_backtest.py.
    """
    import ast
    path = os.path.abspath(__file__)
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
            elif node.level:                      # relative import
                imported.add("." * node.level)
    forbidden = sorted(m for m in imported
                       if m.startswith("work503") or m.startswith("scripts")
                       or m.startswith("."))
    return {
        "imported_modules": sorted(imported),
        "modeling_imports": forbidden,
        "modeling_imports_found": len(forbidden),
        "stdlib_only": False,
        "third_party_deps": ["numpy", "pandas", "scipy"],
        "deviation_note": (
            "audit/README.md 规则 2 的「只允许标准库」一条未满足：预测快照是 "
            "parquet，标准库没有 parquet 读取器。第三方库仅用于读快照与算 "
            "spearman；组合数值全部由本脚本自己的字典/账本实现重算，"
            "与 scripts/work503_backtest.py 不共用任何函数。"),
    }


def main():
    pit, state = load_panel()
    book = PriceBook(DATA)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="backtest_results.json")
    ap.add_argument("--out", default="audit_recompute.json")
    cli = ap.parse_args()
    model_path = os.path.join(EXP, cli.model)
    if not os.path.exists(model_path):
        print("no backtest_results.json — nothing to compare")
        return 1
    model = json.load(open(model_path))

    out, mismatches = {}, []
    for key, mv in model.items():
        p = os.path.join(EXP, "preds", f"{key}.parquet")
        if not os.path.exists(p):
            continue
        pred = pd.read_parquet(p)
        rv = recompute(pred, pit, state, book)
        trace = rv.pop("_trace", None)
        if trace is not None:
            pd.DataFrame(trace).to_parquet(
                os.path.join(EXP, "preds", f"{key}_audit_trace.parquet"))
        out[key] = rv
        for field in ("rankic", "rankicir", "ar", "mdd", "sr", "turnover"):
            a, b = mv.get(field), rv.get(field)
            if a is None or b is None:
                continue
            denom = max(abs(a), abs(b), 1e-12)
            rel = abs(a - b) / denom
            if rel > TOL:
                mismatches.append({"key": key, "field": field,
                                   "model_side": a, "recompute": b,
                                   "rel_diff": rel})
    report = {"tolerance": TOL, "items": out, "mismatches": mismatches,
              "n_mismatches": len(mismatches),
              "independence": independence_selfcheck()}
    json.dump(report, open(os.path.join(EXP, cli.out), "w"),
              ensure_ascii=False, indent=1)
    for k, v in out.items():
        print(f"{k}: recompute RankIC {v['rankic']:.6f} SR {v['sr']:.6f} "
              f"AR {v['ar']:.6f} MDD {v['mdd']:.6f} turn {v['turnover']:.6f}")
    print(f"mismatches: {len(mismatches)}")
    for m in mismatches:
        print("  ", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
