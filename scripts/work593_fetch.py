# -*- coding: utf-8 -*-
"""WORK-593 第 2 段 · 取数：本机 quantdb（DuckDB，只读）→ 基础面板快照 + 锚点 + manifest

口径见 experiments/WORK-593/preregistration.md §1 / §4，字段字典见
data_contracts/field_dictionary.md。**同一底层数据只取一次**，三套表示由
scripts/work593_represent.py 从本快照派生。

产物（全部落 data/WORK-593/，该目录按 .gitignore 不入库；manifest 入库）：
  raw/<endpoint>.parquet      逐端点原始抽取（只追加）
  panel_base.parquet          股票×日 基础面板（不复权价 + vol/amount + adj_factor + vwap_raw）
  anchors.csv                 每股三组锚点因子 f_E / f_C / f_P 与 fallback 标记
  membership.parquet          成分生效区间（时点口径）
  exdates.parquet             除权日（C4 事件校验用）
  experiments/WORK-593/snapshot_manifest.json   端点 ok/error 原文 + 行数 + sha256

运行：
  /home/moyongkai/.venvs/quantdb/bin/python scripts/work593_fetch.py
"""
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime, timezone

import duckdb
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-593"
DATA = os.path.join(REPO, "data", EXP)
RAW = os.path.join(DATA, "raw")
OUT = os.path.join(REPO, "experiments", EXP)
DB = "/srv/share/quantdb/db/quantdb.duckdb"

START, END = "2010-01-04", "2026-11-13"   # 取数窗口（预注册 §4）
CUT = "2020-12-31"                        # 截止锚定 T_C（预注册 §1.2）
INDICES = ("000300.SH", "000905.SH")

items = []


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p):
    return os.path.relpath(p, REPO).replace("\\", "/")


def record(name, fn, **meta):
    """跑一个端点：成功写 ok:true + 行数 + sha256；失败写 ok:false + error 原文，条目不删。"""
    entry = {"endpoint": name, "start": START, "end": END, **meta}
    try:
        df = fn()
        path = os.path.join(RAW, f"{name}.parquet")
        df.to_parquet(path, index=False)
        entry.update({
            "ok": True,
            "rows": int(len(df)),
            "path": rel(path),
            "sha256": sha256(path),
        })
        if "trade_date" in df.columns and len(df):
            entry["date_min"] = str(pd.to_datetime(df["trade_date"]).min().date())
            entry["date_max"] = str(pd.to_datetime(df["trade_date"]).max().date())
        print(f"[ok] {name}: {len(df):,} rows")
        return df
    except Exception as e:  # noqa: BLE001 - 错误原文入 manifest，不重试
        entry.update({"ok": False, "error": f"{type(e).__name__}: {e}",
                      "traceback_tail": traceback.format_exc().splitlines()[-3:]})
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        return None
    finally:
        items.append(entry)


def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    con = duckdb.connect(DB, read_only=True)
    con.execute("PRAGMA threads=4")

    def q(sql):
        return con.execute(sql).fetchdf()

    # ---------------- E1 成分快照 ----------------
    iw = record("index_weight", lambda: q(f"""
        select index_code, con_code, trade_date, weight
        from index_weight
        where index_code in {INDICES}
          and trade_date between DATE '{START}' and DATE '{END}'
        order by trade_date, index_code, con_code
    """))
    if iw is None:
        raise SystemExit("成分端点失败，停（预注册 §8）")

    union = sorted(iw["con_code"].unique())
    print(f"universe union = {len(union)}")
    in_list = ",".join("'%s'" % c for c in union)

    # ---------------- E2 日线 ----------------
    dl = record("daily", lambda: q(f"""
        select ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
        from daily
        where ts_code in ({in_list})
          and trade_date between DATE '{START}' and DATE '{END}'
        order by ts_code, trade_date
    """))

    # ---------------- E3 复权因子 ----------------
    af = record("adj_factor", lambda: q(f"""
        select ts_code, trade_date, adj_factor
        from adj_factor
        where ts_code in ({in_list})
          and trade_date between DATE '{START}' and DATE '{END}'
        order by ts_code, trade_date
    """))

    # ---------------- E4 除权日 ----------------
    ex = record("dividend", lambda: q(f"""
        select ts_code, end_date, ex_date, cash_div, stk_div
        from dividend
        where ts_code in ({in_list})
          and ex_date between DATE '{START}' and DATE '{END}'
        order by ts_code, ex_date
    """))

    # ---------------- E5 交易日历 ----------------
    cal = record("trade_cal", lambda: q(f"""
        select cal_date, is_open from trade_cal
        where exchange='SSE' and cal_date between DATE '{START}' and DATE '{END}'
        order by cal_date
    """))

    # ---------------- E6 股票主档（退市/上市识别用） ----------------
    sb = record("stock_basic", lambda: q(f"""
        select ts_code, name, industry, market, list_date from stock_basic
        where ts_code in ({in_list}) order by ts_code
    """))

    if dl is None or af is None:
        raise SystemExit("日线或复权因子端点失败，停（预注册 §8）")

    # ---------------- 基础面板 ----------------
    base = dl.merge(af, on=["ts_code", "trade_date"], how="left")
    n_missing_f = int(base["adj_factor"].isna().sum())
    # vwap 自建口径（预注册 §3）：amount 千元、vol 手 → 元/股
    base["vwap_raw"] = (base["amount"] * 10.0) / base["vol"]
    base.loc[base["vol"] <= 0, "vwap_raw"] = float("nan")
    base = base.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    pb = os.path.join(DATA, "panel_base.parquet")
    base.to_parquet(pb, index=False)

    # ---------------- 三组锚点 ----------------
    rows = []
    asof_end = pd.Timestamp(END)
    asof_cut = pd.Timestamp(CUT)
    for code, g in base.groupby("ts_code", sort=True):
        g = g.dropna(subset=["adj_factor"])
        if g.empty:
            rows.append({"ts_code": code, "f_E": float("nan"), "f_C": float("nan"),
                         "f_P": float("nan"), "fallback_first_C": True, "note": "no_adj_factor"})
            continue
        ge = g[g["trade_date"] <= asof_end]
        gc = g[g["trade_date"] <= asof_cut]
        fallback = False
        if ge.empty:
            f_E = float("nan")
        else:
            f_E = float(ge.iloc[-1]["adj_factor"])
        if gc.empty:
            f_C = float(g.iloc[0]["adj_factor"])
            fallback = True
        else:
            f_C = float(gc.iloc[-1]["adj_factor"])
        f_P = float(g.iloc[0]["adj_factor"])
        rows.append({
            "ts_code": code,
            "f_E": f_E, "f_C": f_C, "f_P": f_P,
            "fallback_first_C": fallback,
            "T_E": str(ge.iloc[-1]["trade_date"].date()) if not ge.empty else "",
            "T_C": str(gc.iloc[-1]["trade_date"].date()) if not gc.empty else "",
            "T_P": str(g.iloc[0]["trade_date"].date()),
        })
    anchors = pd.DataFrame(rows).sort_values("ts_code").reset_index(drop=True)
    pa = os.path.join(DATA, "anchors.csv")
    anchors.to_csv(pa, index=False)
    n_fallback = int(anchors["fallback_first_C"].sum())

    # ---------------- 成分生效区间（时点口径） ----------------
    spells = []
    for idx, g in iw.groupby("index_code"):
        snaps = sorted(g["trade_date"].unique())
        next_snap = {pd.Timestamp(s): pd.Timestamp(n) for s, n in zip(snaps[:-1], snaps[1:])}
        last = pd.Timestamp(snaps[-1])
        for s in snaps:
            s = pd.Timestamp(s)
            end = next_snap.get(s, pd.Timestamp(END) + pd.Timedelta(days=1)) - pd.Timedelta(days=1)
            if s == last:
                end = asof_end
            members = g.loc[g["trade_date"] == s, "con_code"].unique()
            for c in members:
                spells.append((c, s, min(end, asof_end)))
    mem = pd.DataFrame(spells, columns=["ts_code", "start_date", "end_date"])
    mem = (mem.groupby("ts_code", as_index=False)
              .agg(start_date=("start_date", "min"), end_date=("end_date", "max")))
    # 逐段合并（同股票多次进出成分时合并成连续区间，防止漏日）
    merged = []
    for code, g in mem.sort_values(["ts_code", "start_date"]).groupby("ts_code"):
        cur_s, cur_e = None, None
        for s, e in zip(g["start_date"], g["end_date"]):
            if cur_s is None:
                cur_s, cur_e = s, e
            elif s <= cur_e + pd.Timedelta(days=1):
                cur_e = max(cur_e, e)
            else:
                merged.append((code, cur_s, cur_e))
                cur_s, cur_e = s, e
        merged.append((code, cur_s, cur_e))
    memf = pd.DataFrame(merged, columns=["ts_code", "start_date", "end_date"])
    pm = os.path.join(DATA, "membership.parquet")
    memf.to_parquet(pm, index=False)

    # ---------------- 除权日 ----------------
    if ex is not None and len(ex):
        pe = os.path.join(DATA, "exdates.parquet")
        ex.to_parquet(pe, index=False)
        n_ex = int(ex["ex_date"].nunique())
    else:
        pe, n_ex = "", 0

    manifest = {
        "experiment": EXP,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "paper": "arXiv:2611.07751v2",
        "data_source": {
            "engine": "DuckDB read_only",
            "db": DB,
            "tables": ["daily", "adj_factor", "index_weight", "dividend", "trade_cal", "stock_basic"],
            "why_local_first": "工作区数据底座优先；本单不新接外部数据源（派单卡「不许为这事新接数据源」）",
        },
        "window": {"start": START, "end": END, "cutoff_anchor": CUT},
        "paths_are": "repo-relative (relative to repository root)",
        "universe": {
            "indices": list(INDICES),
            "union_stocks": len(union),
            "membership_rows": int(len(memf)),
            "paper_union": 1730,
            "gap": len(union) - 1730,
        },
        "base_panel": {
            "path": rel(pb), "rows": int(len(base)), "sha256": sha256(pb),
            "columns": list(base.columns),
            "missing_adj_factor_rows": n_missing_f,
            "vwap_formula": "amount * 10 / vol   (amount 千元, vol 手 -> 元/股)",
        },
        "anchors": {
            "path": rel(pa), "rows": int(len(anchors)), "sha256": sha256(pa),
            "fallback_first_C_rows": n_fallback,
            "definition": {
                "E": "last adj_factor on or before 2026-11-13",
                "C": "last adj_factor on or before 2020-12-31; fallback = first available",
                "P": "first adj_factor on or after 2010-01-04",
            },
        },
        "membership": {"path": rel(pm), "rows": int(len(memf)), "sha256": sha256(pm),
                       "granularity": "monthly index_weight snapshots, effective until next snapshot"},
        "exdates": {"path": rel(pe), "rows": int(len(ex)) if ex is not None else 0,
                    "distinct_ex_dates": n_ex,
                    "sha256": sha256(pe) if pe else None},
        "items": items,
    }
    mp = os.path.join(OUT, "snapshot_manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nmanifest -> {rel(mp)}")
    print(f"base rows = {len(base):,}  missing adj_factor rows = {n_missing_f:,}")
    print(f"anchors rows = {len(anchors)}  fallback_C = {n_fallback}")
    print(f"universe = {len(union)}  membership spells = {len(memf)}  ex-dates = {n_ex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
