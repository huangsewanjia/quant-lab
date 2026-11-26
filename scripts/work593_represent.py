# -*- coding: utf-8 -*-
"""WORK-593 第 2 段 · 三套锚定表示 + 字段契约校验 + vwap 验收

输入：data/WORK-593/panel_base.parquet + anchors.csv（scripts/work593_fetch.py 产出）
输出：data/WORK-593/panel_E.parquet / panel_C.parquet / panel_P.parquet
      experiments/WORK-593/field_contract.json
      experiments/WORK-593/vwap_check.json
      experiments/WORK-593/representation.json（三份快照 sha256）

表示构造式见 experiments/WORK-593/preregistration.md §1.2：
    p_t = x_t * f_t / f_i^(A) ; vwap_t = vwap_raw_t * f_t / f_i^(A) ; v_t = vol_t * f_i^(A) / f_t

运行：/home/moyongkai/.venvs/quantdb/bin/python scripts/work593_represent.py
"""
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-593"
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)
QLIB = os.path.expanduser("~/.qlib/qlib_data")

PRICE_COLS = ["open", "high", "low", "close", "vwap"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p):
    return os.path.relpath(p, REPO).replace("\\", "/")


def derive(base, anchors, rep):
    """统一变换式。返回新面板（不改入参）。"""
    f = anchors.set_index("ts_code")["f_" + rep]
    af = base["ts_code"].map(f).astype("float64").to_numpy()
    f_t = base["adj_factor"].to_numpy(dtype="float64")
    k = f_t / af                       # 价格乘子
    out = base[["ts_code", "trade_date"]].copy()
    out["adj_scale"] = af
    for c in ["open", "high", "low"]:
        out[c] = base[c].to_numpy(dtype="float64") * k
    out["close"] = base["close"].to_numpy(dtype="float64") * k
    out["vwap"] = base["vwap_raw"].to_numpy(dtype="float64") * k
    out["vol"] = base["vol"].to_numpy(dtype="float64") / k
    out["amount"] = base["amount"].to_numpy(dtype="float64")
    out["pct_chg"] = base["pct_chg"].to_numpy(dtype="float64")
    return out


def contract_c1(panel, name):
    """C1: adjusted vwap ∈ [low, high]。

    v1.1 口径（容差按字段分辨率推导：1e-6 + 0.5/amount_yuan + 0.5/vol_shares），
    v1.0 的严格 1e-9 口径同时报告，两者并列披露。
    """
    ok_rows = panel.dropna(subset=["vwap", "low", "high"])
    lo = ok_rows["low"].to_numpy()
    hi = ok_rows["high"].to_numpy()
    w = ok_rows["vwap"].to_numpy()
    amt_yuan = np.abs(ok_rows["amount"].to_numpy() * 1000.0)
    shares = np.abs(ok_rows["vol"].to_numpy() * 100.0)
    tol_rel = 1e-6 + 0.5 / amt_yuan + 0.5 / shares
    bad = (w < lo - tol_rel * np.abs(lo)) | (w > hi + tol_rel * np.abs(hi))
    bad_strict = (w < lo - 1e-9 * np.abs(lo)) | (w > hi + 1e-9 * np.abs(hi))
    return {
        "representation": name,
        "rows_checked": int(len(ok_rows)),
        "violations_v1_1": int(bad.sum()),
        "violation_rate_v1_1": float(bad.sum() / max(len(ok_rows), 1)),
        "violations_v1_0_strict_1e_9": int(bad_strict.sum()),
        "violation_rate_v1_0_strict_1e_9": float(bad_strict.sum() / max(len(ok_rows), 1)),
        "tolerance": "v1.1: 1e-6 + 0.5/(amount*1000) + 0.5/(vol*100); v1.0: 1e-9*|bound|",
    }


def contract_c2(panel, base, name):
    """C2: vwap * vol == vwap_raw * vol_raw（相对差 ≤1e-12，违规数须为 0）。"""
    lhs = panel["vwap"].to_numpy() * panel["vol"].to_numpy()
    rhs = base["vwap_raw"].to_numpy() * base["vol"].to_numpy()
    m = np.isfinite(lhs) & np.isfinite(rhs) & (np.abs(rhs) > 0)
    rel_diff = np.abs(lhs[m] - rhs[m]) / np.abs(rhs[m])
    return {
        "representation": name,
        "rows_checked": int(m.sum()),
        "violations_1e_12": int((rel_diff > 1e-12).sum()),
        "max_rel_diff": float(rel_diff.max()) if len(rel_diff) else 0.0,
    }


def contract_c3():
    """C3: 成分表不得含指数自身（命中即报异常，不静默丢弃）。"""
    iw = pd.read_parquet(os.path.join(DATA, "raw", "index_weight.parquet"))
    hit = iw[iw["index_code"] == iw["con_code"]]
    return {
        "check": "index self-membership",
        "rows_scanned": int(len(iw)),
        "hits": int(len(hit)),
        "hit_rows": hit.head(5).assign(trade_date=lambda d: d["trade_date"].astype(str)).to_dict("records"),
        "verdict": "pass (0 hits)" if len(hit) == 0 else "ANOMALY - must be reported, not dropped",
    }


def contract_c4(panel, exdates, name):
    """C4: 除权日事件校验 —— 调整后收益 vs 交易所 pct_chg，|差| ≤ 1e-3。"""
    p = panel[["ts_code", "trade_date", "close", "pct_chg"]].copy()
    p = p.sort_values(["ts_code", "trade_date"])
    p["ret"] = p.groupby("ts_code", sort=False)["close"].pct_change()
    ex = exdates[["ts_code", "ex_date"]].drop_duplicates()
    ex["trade_date"] = pd.to_datetime(ex["ex_date"])
    m = ex.merge(p, on=["ts_code", "trade_date"], how="inner").dropna(subset=["ret", "pct_chg"])
    diff = (m["ret"] - m["pct_chg"] / 100.0).abs()
    return {
        "representation": name,
        "ex_events_matched": int(len(m)),
        "pass_1e_3": int((diff <= 1e-3).sum()),
        "pass_rate": float((diff <= 1e-3).mean()) if len(m) else None,
        "median_abs_diff": float(diff.median()) if len(m) else None,
        "p99_abs_diff": float(diff.quantile(0.99)) if len(m) else None,
        "worst": m.assign(diff=diff).nlargest(3, "diff")[["ts_code", "trade_date", "ret", "pct_chg", "diff"]].assign(trade_date=lambda d: d["trade_date"].astype(str)).to_dict("records"),
    }


def read_qlib(code):
    cal = [l.strip() for l in open(os.path.join(QLIB, "calendars", "day.txt"), encoding="utf-8") if l.strip()]
    feats = {}
    for f in ["close", "volume", "factor"]:
        path = os.path.join(QLIB, "features", code, f"{f}.day.bin")
        if not os.path.exists(path):
            return None
        arr = np.fromfile(path, dtype="<f4")
        start = int(arr[0])
        vals = arr[1:]
        idx = pd.to_datetime(cal[start:start + len(vals)])
        feats[f] = pd.Series(vals, index=idx)
    return pd.DataFrame(feats)


def vwap_checks(panel_E, base):
    """V1 Qlib 交叉核对（可得字段）/ V2 区间命中率 / V3 实现双路径。"""
    res = {"qlib_vwap_available": False, "qlib_evidence": {}, "V1": None, "V2": None, "V3": None}

    feat_dir = os.path.join(QLIB, "features")
    probe = os.path.join(feat_dir, "sh600000")
    listing = sorted(os.listdir(probe)) if os.path.isdir(probe) else []
    res["qlib_evidence"] = {
        "bundle": QLIB,
        "probe_dir": probe,
        "files": listing,
        "has_vwap_day_bin": os.path.exists(os.path.join(probe, "vwap.day.bin")),
        "calendar_last": [l.strip() for l in open(os.path.join(QLIB, "calendars", "day.txt"), encoding="utf-8")][-1],
        "feature_count": len(os.listdir(feat_dir)),
    }
    res["qlib_vwap_available"] = res["qlib_evidence"]["has_vwap_day_bin"]

    # ---- V1: close 收益 + volume 水平（Qlib 只有这两个可对） ----
    rows = []
    for code in ["sh600000", "sh600519", "sz000001"]:
        q = read_qlib(code)
        if q is None:
            rows.append({"code": code, "ok": False, "error": "qlib features missing"})
            continue
        ts_code = code[2:].upper() + (".SH" if code.startswith("sh") else ".SZ")
        ours = panel_E[panel_E["ts_code"] == ts_code]
        if ours.empty:
            rows.append({"code": code, "ok": False, "error": "local panel missing"})
            continue
        o = ours.set_index("trade_date").sort_index()
        j = q.join(o[["close", "vol"]], how="inner", lsuffix="_q")
        j = j.dropna(subset=["close", "close_q"])
        if len(j) < 100:
            rows.append({"code": code, "ok": False, "error": f"overlap too short: {len(j)}"})
            continue
        rq = j["close"].pct_change()
        ro = j["close_q"].pct_change()
        m = rq.notna() & ro.notna()
        ret_diff = (rq[m] - ro[m]).abs()
        # qlib volume = 原始股数；本地 vol = 手 -> 股
        vr = j["volume"] / (j["vol"] * 100.0)
        rows.append({
            "code": code, "ok": True,
            "overlap_days": int(len(j)),
            "overlap_start": str(j.index.min().date()), "overlap_end": str(j.index.max().date()),
            "close_return_max_abs_diff": float(ret_diff.max()),
            "close_return_share_le_1e_4": float((ret_diff <= 1e-4).mean()),
            "volume_ratio_median": float(vr.median()),
            "volume_ratio_cv": float(vr.std() / vr.mean()) if vr.mean() else None,
            "note": "qlib close = 调整价（锚定在 qlib 最后一日），故比收益不比水平；qlib volume = 原始股数，故比水平",
        })
    res["V1"] = rows

    # ---- V2: 区间命中率（全体股票-日） ----
    p = panel_E.dropna(subset=["vwap", "low", "high"])
    tol = (1e-6 + 0.5 / (p["amount"].abs() * 1000.0) + 0.5 / (p["vol"].abs() * 100.0))
    inside = ((p["vwap"] >= p["low"] - tol * p["low"].abs())
              & (p["vwap"] <= p["high"] + tol * p["high"].abs()))
    inside_strict = ((p["vwap"] >= p["low"] - 1e-9 * p["low"].abs())
                     & (p["vwap"] <= p["high"] + 1e-9 * p["high"].abs()))
    res["V2"] = {"rows": int(len(p)), "inside_v1_1": int(inside.sum()),
                 "rate_v1_1": float(inside.mean()),
                 "inside_v1_0_strict_1e_9": int(inside_strict.sum()),
                 "rate_v1_0_strict_1e_9": float(inside_strict.mean())}

    # ---- V3: 实现双路径 ----
    v1 = (base["amount"].to_numpy() * 10.0) / base["vol"].to_numpy()
    v2 = np.multiply(base["amount"].to_numpy(), 10.0) * np.power(base["vol"].to_numpy(), -1.0)
    finite = np.isfinite(v1) & np.isfinite(v2)
    res["V3"] = {"rows": int(finite.sum()),
                 "max_abs_diff": float(np.abs(v1[finite] - v2[finite]).max()) if finite.any() else None}
    return res


def main():
    base = pd.read_parquet(os.path.join(DATA, "panel_base.parquet"))
    anchors = pd.read_csv(os.path.join(DATA, "anchors.csv"))
    exdates = pd.read_parquet(os.path.join(DATA, "exdates.parquet"))
    print(f"base rows={len(base):,}  anchors={len(anchors)}")

    reps = {}
    rep_meta = {}
    for rep in ["E", "C", "P"]:
        panel = derive(base, anchors, rep)
        path = os.path.join(DATA, f"panel_{rep}.parquet")
        panel.to_parquet(path, index=False)
        reps[rep] = panel
        rep_meta[rep] = {
            "path": rel(path), "rows": int(len(panel)), "sha256": sha256(path),
            "anchor": {"E": "2026-11-13", "C": "2020-12-31", "P": "first available"}[rep],
            "cols": list(panel.columns),
        }
        print(f"[{rep}] {len(panel):,} rows -> {rel(path)}  sha256={rep_meta[rep]['sha256'][:16]}…")

    fc = {
        "experiment": EXP,
        "prereg": "experiments/WORK-593/preregistration.md",
        "C1": [contract_c1(reps[r], r) for r in ["E", "C", "P"]],
        "C2": [contract_c2(reps[r], base, r) for r in ["E", "C", "P"]],
        "C3": contract_c3(),
        "C4": [contract_c4(reps[r], exdates, r) for r in ["E", "C", "P"]],
    }
    with open(os.path.join(OUT, "field_contract.json"), "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2, default=str)

    vc = vwap_checks(reps["E"], base)
    with open(os.path.join(OUT, "vwap_check.json"), "w", encoding="utf-8") as f:
        json.dump(vc, f, ensure_ascii=False, indent=2, default=str)

    with open(os.path.join(OUT, "representation.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": EXP,
                   "base": {"path": rel(os.path.join(DATA, "panel_base.parquet")),
                            "sha256": sha256(os.path.join(DATA, "panel_base.parquet"))},
                   "representations": rep_meta}, f, ensure_ascii=False, indent=2)

    print("\n-- C1 (vwap in [low,high], tol 1e-9) --")
    for r in fc["C1"]:
        print(f"  {r['representation']}: rows={r['rows_checked']:,} viol_v1.1={r['violations_v1_1']} rate={r['violation_rate_v1_1']:.3e} | strict1e-9={r['violations_v1_0_strict_1e_9']} ({r['violation_rate_v1_0_strict_1e_9']:.3e})")
    print("-- C2 (turnover identity) --")
    for r in fc["C2"]:
        print(f"  {r['representation']}: viol={r['violations_1e_12']} max_rel={r['max_rel_diff']:.3e}")
    print(f"-- C3 self-membership hits: {fc['C3']['hits']} --")
    print("-- C4 (ex-date event check) --")
    for r in fc["C4"]:
        print(f"  {r['representation']}: matched={r['ex_events_matched']} pass_rate={r['pass_rate']} p99={r['p99_abs_diff']}")
    print("-- vwap --")
    print(f"  qlib has vwap.day.bin: {vc['qlib_vwap_available']}  calendar_last={vc['qlib_evidence']['calendar_last']}")
    for r in vc["V1"]:
        print(f"  V1 {r['code']}: {r}")
    print(f"  V2 inside_v1.1={vc['V2']['inside_v1_1']:,}/{vc['V2']['rows']:,} = {vc['V2']['rate_v1_1']:.6f} | strict={vc['V2']['rate_v1_0_strict_1e_9']:.6f}")
    print(f"  V3 max_abs_diff={vc['V3']['max_abs_diff']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
