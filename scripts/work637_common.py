"""WORK-637 shared loaders and constants.

Single source of truth for: PIT CSI300 pool, weekly signal grid, price panel
(qfq OHLC for rendering / returns, raw open + limit prices for tradability
masks), and the image-library layout.

All definitions follow ``experiments/WORK-637/preregistration.md`` §2-§3.
"""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pandas as pd

DB = "/srv/share/quantdb/db/quantdb.duckdb"
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXP = os.path.join(REPO, "experiments", "WORK-637")
DATA = os.path.join(REPO, "data", "WORK-637")
IMAGELIB_ROOT = "/srv/share/quantdb/code/WORK-637/imagelib"
MODEL_DIR = os.path.join(os.path.dirname(REPO), "models")

INDEX_CODE = "000300.SH"
GRID_START = "2014-01-02"
GRID_END = "2026-11-18"          # last week whose t+6 label is realised by 2026-11-26
CAL_START = "2013-01-01"
CAL_END = "2026-11-26"

LOOKBACK = 20                     # D20 candlestick window
MA_WIN = 20                       # MA20 needs 19 extra days -> 39-day window
MIN_LIST_DAYS = 250
IMG_PX = 140
SEEDS = (42, 2024)
PAD_BY_SEED = {42: 0.020, 2024: 0.060}

UP_COLOR = "#C8322B"
DOWN_COLOR = "#2E7D32"
MA_COLOR = "#1a1a1a"


def imagelib_dir(seed: int) -> str:
    return os.path.join(IMAGELIB_ROOT, f"seed{seed}")


def connect():
    import duckdb
    return duckdb.connect(DB, read_only=True)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def trading_days(con=None) -> np.ndarray:
    own = con is None
    con = con or connect()
    df = con.execute(
        "select cal_date from trade_cal where exchange='SSE' and is_open=1 "
        f"and cal_date between date '{CAL_START}' and date '{CAL_END}' order by 1"
    ).df()
    if own:
        con.close()
    return pd.to_datetime(df["cal_date"]).values.astype("datetime64[D]")


def weekly_signal_days(days: np.ndarray) -> np.ndarray:
    """Last trading day of each ISO week inside [GRID_START, GRID_END]."""
    s = pd.Series(pd.to_datetime(days))
    iso = s.dt.isocalendar()
    df = pd.DataFrame({"d": s, "y": iso["year"].values, "w": iso["week"].values})
    wk = df.groupby(["y", "w"])["d"].max().sort_values().values
    lo = np.datetime64(GRID_START)
    hi = np.datetime64(GRID_END)
    return np.array([d for d in wk if lo <= np.datetime64(d) <= hi])


def pool_snapshot(con=None) -> pd.DataFrame:
    own = con is None
    con = con or connect()
    iw = con.execute(
        "select trade_date, con_code, weight from index_weight "
        f"where index_code='{INDEX_CODE}' order by trade_date"
    ).df()
    if own:
        con.close()
    iw["trade_date"] = pd.to_datetime(iw["trade_date"])
    return iw


def day_index_map(days: np.ndarray) -> dict:
    return {np.datetime64(d): i for i, d in enumerate(days)}


def _pivot(df: pd.DataFrame, value: str, days: np.ndarray, codes: list[str]) -> np.ndarray:
    p = df.pivot(index="trade_date", columns="ts_code", values=value)
    p = p.reindex(index=pd.to_datetime(days), columns=codes)
    return p.to_numpy(dtype="float64")


def build_panel(verbose: bool = True) -> dict:
    """Build the aligned panel. Returns dict of arrays + metadata (caller saves)."""
    con = connect()
    days = trading_days(con)
    weekdays = weekly_signal_days(days)
    iw = pool_snapshot(con)
    codes = sorted(iw["con_code"].unique().tolist())
    code_ix = {c: i for i, c in enumerate(codes)}

    q = con.execute(
        "select ts_code, trade_date, open_qfq, high_qfq, low_qfq, close_qfq, vol "
        "from v_daily_qfq where trade_date between date '2013-01-01' and date '2026-11-26' "
        "and ts_code in (select distinct con_code from index_weight "
        f"where index_code='{INDEX_CODE}') order by ts_code, trade_date"
    ).df()
    q["trade_date"] = pd.to_datetime(q["trade_date"])
    raw = con.execute(
        "select ts_code, trade_date, open, close from daily "
        "where trade_date between date '2013-01-01' and date '2026-11-26' "
        "and ts_code in (select distinct con_code from index_weight "
        f"where index_code='{INDEX_CODE}') order by ts_code, trade_date"
    ).df()
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    lim = con.execute(
        "select ts_code, trade_date, up_limit, down_limit from stk_limit "
        "where trade_date between date '2013-01-01' and date '2026-11-26' "
        "and ts_code in (select distinct con_code from index_weight "
        f"where index_code='{INDEX_CODE}')"
    ).df()
    lim["trade_date"] = pd.to_datetime(lim["trade_date"])
    sb = con.execute("select ts_code, list_date from stock_basic").df()
    sb["list_date"] = pd.to_datetime(sb["list_date"])
    cal_all = con.execute(
        "select cal_date from trade_cal where exchange='SSE' and is_open=1 order by 1"
    ).df()
    con.close()
    cal_all["cal_date"] = pd.to_datetime(cal_all["cal_date"])

    o = _pivot(q, "open_qfq", days, codes)
    h = _pivot(q, "high_qfq", days, codes)
    lo = _pivot(q, "low_qfq", days, codes)
    c = _pivot(q, "close_qfq", days, codes)
    v = _pivot(q, "vol", days, codes)
    ro = _pivot(raw, "open", days, codes)
    up = _pivot(lim, "up_limit", days, codes)
    dn = _pivot(lim, "down_limit", days, codes)

    present = ~np.isnan(c)
    # listing days: trading days on/before date since list_date, counted on the SSE calendar
    cal_ix = {d: i for i, d in enumerate(cal_all["cal_date"].values)}
    list_days = np.full(len(codes), -10**9, dtype=np.int64)
    for j, code in enumerate(codes):
        ld = sb.loc[sb["ts_code"] == code, "list_date"]
        if len(ld) and pd.notna(ld.iloc[0]):
            prev = cal_all["cal_date"].values[cal_all["cal_date"].values < np.datetime64(ld.iloc[0])]
            list_days[j] = len(prev) - 1 if len(prev) else -1
    # listed_days[i, j] = number of open days before day i minus listing offset
    day_pos = np.arange(len(days), dtype=np.int64)
    cal_pos = np.array([cal_ix.get(np.datetime64(d), -1) for d in days], dtype=np.int64)
    listed_days = cal_pos[:, None] - list_days[None, :]

    # window completeness: 39 consecutive present rows ending at each day
    win_ok = np.zeros_like(present)
    for j in range(len(codes)):
        p = present[:, j].astype(np.int32)
        if p.sum() < LOOKBACK + MA_WIN - 1:
            continue
        cs = np.concatenate([[0], np.cumsum(p)])
        width = MA_WIN + LOOKBACK - 1
        ok = (cs[width:] - cs[:-width]) == width
        win_ok[width - 1:, j] = ok

    ma = pd.DataFrame(c).rolling(MA_WIN, min_periods=MA_WIN).mean().to_numpy()

    dmap = day_index_map(days)
    week_idx = np.array([dmap[np.datetime64(d)] for d in weekdays], dtype=np.int64)

    # PIT membership per week
    snap_dates = np.sort(iw["trade_date"].unique())
    member_sets = {d: set(g["con_code"]) for d, g in iw.groupby("trade_date")}
    pool_mask = np.zeros((len(week_idx), len(codes)), dtype=bool)
    for w, wi in enumerate(week_idx):
        cand = snap_dates[snap_dates <= days[wi]]
        if len(cand) == 0:
            continue
        for code in member_sets[cand[-1]]:
            if code in code_ix:
                pool_mask[w, code_ix[code]] = True

    if verbose:
        print(f"panel: {len(days)} days x {len(codes)} codes, weeks={len(week_idx)}, "
              f"pool obs={int(pool_mask.sum())}, win_ok at weeks={int(win_ok[week_idx].sum())}")

    return dict(days=days, week_idx=week_idx, codes=codes, o=o, h=h, lo=lo, c=c, v=v,
                ro=ro, up=up, dn=dn, present=present, win_ok=win_ok, ma=ma,
                listed_days=listed_days, pool_mask=pool_mask,
                n_rows=dict(qfq=len(q), raw=len(raw), lim=len(lim), iw=len(iw)),
                ranges=dict(days=(str(days[0]), str(days[-1]))),
                snapshots=int(len(snap_dates)))


def eligibility(panel: dict) -> dict:
    """Per (week, stock): chartable, pool-member, entry-tradeable, exit info."""
    wk = panel["week_idx"]
    present = panel["present"]
    ro, up, dn = panel["ro"], panel["up"], panel["dn"]
    n = len(panel["days"])
    W, S = len(wk), len(panel["codes"])

    has_chart = panel["win_ok"][wk, :]                      # (W,S)
    in_pool = panel["pool_mask"]
    listed = panel["listed_days"][wk, :] >= MIN_LIST_DAYS

    ent_ix = wk + 1
    ext_ix = wk + 6
    ok_e = ent_ix < n
    ok_x = ext_ix < n
    ent = np.zeros((W, S)); ext = np.zeros((W, S))
    ent_trade = np.zeros((W, S), dtype=bool)
    ext_trade = np.zeros((W, S), dtype=bool)
    for k in range(W):
        i, jx = ent_ix[k], ext_ix[k]
        if i < n:
            ent[k] = ro[i]
            ent_trade[k] = present[i] & ~np.isnan(ro[i]) & ~(ro[i] >= up[i] - 1e-3) & ~np.isnan(up[i])
        if jx < n:
            ext[k] = ro[jx]
            ext_trade[k] = present[jx] & ~np.isnan(ro[jx]) & ~(ro[jx] <= dn[jx] + 1e-3) & ~np.isnan(dn[jx])
    chartable = has_chart & in_pool & listed
    eligible = chartable & ok_e[:, None] & ent_trade
    return dict(eligible=eligible, chartable=chartable, has_chart=has_chart,
                in_pool=in_pool, listed=listed,
                ent=ent, ext=ext, ent_trade=ent_trade, ext_trade=ext_trade,
                ent_ix=ent_ix, ext_ix=ext_ix)


def label_matrix(panel: dict, elig: dict) -> np.ndarray:
    """r(j,tau) = open_qfq(tau+6)/open_qfq(tau+1) - 1.

    Availability-based only: a chart whose label is computable belongs to the
    library even if the stock could not be traded that week (preregistration §4).
    """
    oq = panel["o"]
    W, S = elig["ent"].shape
    fwd = np.full((W, S), np.nan)
    n = len(panel["days"])
    for k in range(W):
        i, jx = elig["ent_ix"][k], elig["ext_ix"][k]
        if i < n and jx < n:
            a, b = oq[i], oq[jx]
            with np.errstate(invalid="ignore", divide="ignore"):
                fwd[k] = np.where((a > 0) & (b > 0), b / a - 1.0, np.nan)
    return fwd


def load_panel_cached() -> dict:
    p = os.path.join(DATA, "panel.npz")
    meta = os.path.join(DATA, "panel_meta.json")
    if os.path.exists(p):
        z = np.load(p, allow_pickle=True)
        d = {k: z[k] for k in z.files}
        with open(meta) as f:
            m = json.load(f)
        d["codes"] = [str(x) for x in d["codes"]]
        d.update(m)
        return d
    return panel_from_build()


def panel_from_build() -> dict:
    panel = build_panel()
    os.makedirs(DATA, exist_ok=True)
    np.savez_compressed(
        os.path.join(DATA, "panel.npz"), **panel)
    meta = {k: panel[k] for k in ("n_rows", "ranges", "snapshots")}
    with open(os.path.join(DATA, "panel_meta.json"), "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False, default=str)
    return panel


if __name__ == "__main__":
    p = build_panel()
    panel_from_build()
    e = eligibility(p)
    print("chartable per week: mean %.1f total %d" % (
        e["chartable"].sum(1).mean(), e["chartable"].sum()))
    print("eligible per week: mean %.1f min %d max %d" % (
        e["eligible"].sum(1).mean(), e["eligible"].sum(1).min(), e["eligible"].sum(1).max()))
    lab = label_matrix(p, e)
    print("labels finite:", int(np.isfinite(lab).sum()), "of", lab.size)
