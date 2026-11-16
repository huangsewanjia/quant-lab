# -*- coding: utf-8 -*-
"""
ARM B 修正与补测：
 1. 腾讯 sh000510 用 YYYY-MM-DD 复验（Arm A 用 YYYYMMDD 触发了登记表记录的日期格式坑）
 2. 全口径网格：数清「盲试」需要试几套口径才能对上原文数字
 3. 跨源校验修正：新浪 sh000852 vs 腾讯 sh000852（同指数）
"""
import json
import time

import pandas as pd
import requests

REQS = {"n": 0}
_orig = requests.Session.request


def _counted(self, method, url, *a, **kw):
    REQS["n"] += 1
    return _orig(self, method, url, *a, **kw)


requests.Session.request = _counted
T0 = time.time()


def tencent(code, d0="2026-05-01", d1="2026-11-06"):
    u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    r = requests.get(u, params={"param": f"{code},day,{d0},{d1},640,qfq"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    node = (r.json().get("data") or {}).get(code) or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise ValueError(f"empty kline keys={list(node.keys())}")
    df = pd.DataFrame([x[:6] for x in rows], columns=["date", "open", "close", "high", "low", "volume"])
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"].astype(str).str.slice(0, 10))
    return df.set_index("date").sort_index()


def sina(code):
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=code).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


print("--- 1. 腾讯中证A500 复验（YYYY-MM-DD 格式）---")
try:
    df = tencent("sh000510")
    print(json.dumps({"ok": True, "rows": len(df), "first": str(df.index[0].date()),
                      "last": str(df.index[-1].date())}, ensure_ascii=False))
except Exception as e:  # noqa: BLE001
    print(json.dumps({"ok": False, "err": f"{type(e).__name__}: {e}"[:120]}, ensure_ascii=False))

print("\n--- 1b. 复现 YYYYMMDD 格式坑（Arm A 的失败）---")
try:
    u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    r = requests.get(u, params={"param": "sh000510,day,20260501,20260605,640,qfq"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    body = r.text
    print(json.dumps({"status": r.status_code, "body_len": len(body), "body_head": body[:120]}, ensure_ascii=False))
except Exception as e:  # noqa: BLE001
    print(json.dumps({"ok": False, "err": f"{type(e).__name__}: {e}"[:120]}, ensure_ascii=False))

print("\n--- 2. 取数（登记表口径）---")
df1000 = tencent("sh000852")
dfA500 = tencent("sh000510")
s1000 = sina("sh000852")
print(json.dumps({"tencent_1000": len(df1000), "tencent_A500": len(dfA500), "sina_1000": len(s1000)}, ensure_ascii=False))


def close_on(df, ymd):
    s = df[df.index <= pd.Timestamp(ymd)]
    return None if s.empty else (float(s["close"].iloc[-1]), s.index[-1].strftime("%Y-%m-%d"))


def open_on(df, ymd):
    s = df[df.index <= pd.Timestamp(ymd)]
    return None if s.empty else (float(s["open"].iloc[-1]), s.index[-1].strftime("%Y-%m-%d"))


def mdd_close(df, d0, d1):
    s = df[(df.index >= pd.Timestamp(d0)) & (df.index <= pd.Timestamp(d1))]["close"]
    return float((s / s.cummax() - 1).min())


def mdd_low(df, d0, d1):
    s = df[(df.index >= pd.Timestamp(d0)) & (df.index <= pd.Timestamp(d1))]
    low = s["low"]
    return float((low / low.cummax() - 1).min())


def mdd_high_to_low(df, d0, d1):
    """日内口径：先按最高价定峰值，再按最低价算最深回撤"""
    s = df[(df.index >= pd.Timestamp(d0)) & (df.index <= pd.Timestamp(d1))]
    return float((s["low"] / s["high"].cummax() - 1).min())


# ===== 盲试网格：Arm A 没有 metric_definitions 时要试的全套口径 =====
CLAIMS = [
    {"id": "D1", "text": "中证1000 区间跌幅 25.4%", "code": "sh000852", "df": df1000,
     "d0": "2026-05-19", "d1": "2026-11-06", "claimed": -0.254, "kind": "ret"},
    {"id": "D2", "text": "中证1000 期间最大跌幅 31.39%", "code": "sh000852", "df": df1000,
     "d0": "2026-05-19", "d1": "2026-11-06", "claimed": -0.3139, "kind": "mdd"},
    {"id": "D3", "text": "中证A500 区间跌幅 20.22%", "code": "sh000510", "df": dfA500,
     "d0": "2026-05-28", "d1": "2026-11-06", "claimed": -0.2022, "kind": "ret"},
]

ENDPOINT_VARIANTS = {
    "E1 成立日收盘→11-06收盘": ("close", "close", "d0", "d1"),
    "E2 成立日次日收盘→11-06收盘": ("close_next", "close", "d0", "d1"),
    "E3 成立日开盘→11-06收盘": ("open", "close", "d0", "d1"),
    "E4 成立日收盘→11-06开盘": ("close", "open", "d0", "d1"),
    "E5 成立日收盘→11-05收盘": ("close", "close", "d0", "d1_minus1"),
    "E6 成立日收盘→11-06收盘(新浪源)": ("close_sina", "close_sina", "d0", "d1"),
}
MDD_VARIANTS = {
    "M1 区间内收盘 cummax": mdd_close,
    "M2 区间内日内最低 cummax": mdd_low,
    "M3 区间内 最高→最低": mdd_high_to_low,
    "M4 YTD起点收盘 cummax": lambda df, a, b: mdd_close(df, "2025-12-31", b),
}

print("\n--- 3. 盲试网格（Arm A：无 metric_definitions）---")
blind_hits = {}
grid_rows = 0
for cl in CLAIMS:
    df = cl["df"]
    hits = []
    for ename, (es, ee, ds, de) in ENDPOINT_VARIANTS.items():
        grid_rows += 1
        if es == "close_next":
            a = close_on(df, (pd.Timestamp(cl["d0"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))[0]
        elif es == "open":
            a = open_on(df, cl["d0"])[0]
        elif es == "close_sina":
            a = close_on(s1000, cl["d0"])[0]
        else:
            a = close_on(df, cl["d0"])[0]
        if ee == "open":
            b = open_on(df, cl["d1"])[0]
        elif ee == "close_sina":
            b = close_on(s1000, cl["d1"])[0]
        elif de == "d1_minus1":
            b = close_on(df, "2026-11-05")[0]
        else:
            b = close_on(df, cl["d1"])[0]
        r = b / a - 1
        gap = (r - cl["claimed"]) * 100
        if abs(gap) <= 1.0:
            hits.append({"variant": ename, "measured": round(r, 6), "gap_pp": round(gap, 3)})
        if cl["kind"] == "mdd":
            for mname, mfn in MDD_VARIANTS.items():
                grid_rows += 1
                m = mfn(df, cl["d0"], cl["d1"])
                mgap = (m - cl["claimed"]) * 100
                if abs(mgap) <= 1.0:
                    hits.append({"variant": f"{ename} × {mname}", "measured": round(m, 6), "gap_pp": round(mgap, 3)})
    blind_hits[cl["id"]] = {"claim": cl["text"], "claimed": cl["claimed"], "hits_within_1pp": hits}
    print(f"{cl['id']} {cl['text']}  命中数(≤1pp)={len(hits)}")
    for h in hits:
        print(f"    {h['variant']}: {h['measured']:.4f}  gap={h['gap_pp']:+.3f}pp")

print("\n--- 4. 登记表口径（Arm B：一次命中）---")
reg = {}
for cl in CLAIMS:
    df = cl["df"]
    if cl["kind"] == "ret":
        c0, u0 = close_on(df, cl["d0"])
        c1, u1 = close_on(df, cl["d1"])
        val, gap = c1 / c0 - 1, (c1 / c0 - 1 - cl["claimed"]) * 100
        reg[cl["id"]] = {"text": cl["text"], "claimed": cl["claimed"], "measured": round(val, 6),
                         "gap_pp": round(gap, 3), "endpoints": [u0, u1], "rounds_needed": 1}
    else:
        c0, u0 = close_on(df, cl["d0"])
        c1, u1 = close_on(df, cl["d1"])
        since = mdd_close(df, cl["d0"], cl["d1"])
        ytd = mdd_close(df, "2025-12-31", cl["d1"])
        reg[cl["id"]] = {"text": cl["text"], "claimed": cl["claimed"],
                         "成立以来区间MDD": round(since, 6), "gap_since_pp": round((since - cl["claimed"]) * 100, 3),
                         "YTD起点MDD": round(ytd, 6), "gap_ytd_pp": round((ytd - cl["claimed"]) * 100, 3),
                         "endpoints": [u0, u1], "rounds_needed": 1,
                         "口径分叉": "原文「期间最大跌幅」= 成立以来区间；metric_definitions §4 默认 YTD 起点 → 两套都报"}
    print(json.dumps({"id": cl["id"], **reg[cl["id"]]}, ensure_ascii=False))

print("\n--- 5. 跨源校验（修正：新浪 sh000852 vs 腾讯 sh000852）---")
common = df1000.index.intersection(s1000.index)
d = (s1000.loc[common, "close"] - df1000.loc[common, "close"]).abs()
xc = {"common_days": int(len(common)), "max_abs_diff": float(d.max()), "mean_abs_diff": float(d.mean()),
      "within_0.005": bool(d.max() <= 0.005)}
print(json.dumps(xc, ensure_ascii=False))

OUT = {"http_requests": REQS["n"], "elapsed_sec": round(time.time() - T0, 2),
       "blind_grid_rows": grid_rows, "blind_hits": blind_hits, "registry_path": reg, "cross_source": xc}
print("\n=== 汇总 ===")
print(json.dumps({k: v for k, v in OUT.items() if k not in ("blind_hits", "registry_path")}, ensure_ascii=False, indent=1))
with open(r"F:\001_install\103_multica\multica-upstream-20261105\data\workspaces\workspace-c8faa36a018a\work-413-e5dbb7589ffa\workdir\_armB2.json", "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=1)
