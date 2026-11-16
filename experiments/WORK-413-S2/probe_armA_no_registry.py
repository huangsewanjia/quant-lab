# -*- coding: utf-8 -*-
"""
第二篇文章复用验证 —— ARM A（无登记表：第一次做这类研究时的取数探测路线）

目的：复现「第一次做这类研究」的取数探测成本，作为前（before）基线。
说明：本 arm 是**路线复现**，不是盲测——执行者已知道登记表内容，因此臂内不再有
「猜源」的搜索成本；本臂测的是**真实发生的请求次数、失败模式与耗时**。

计量：
  - HTTP 请求数（monkeypatch requests 计数）
  - 每个源的探活耗时与错误原文
  - 本臂总耗时
"""
import json
import time

import requests

REQS = {"n": 0}
_orig = requests.Session.request


def _counted(self, method, url, *a, **kw):
    REQS["n"] += 1
    return _orig(self, method, url, *a, **kw)


requests.Session.request = _counted

T0 = time.time()
LOG = []


def probe(name, fn):
    r0, t0 = REQS["n"], time.time()
    try:
        info = fn()
        rec = {"source": name, "ok": True, "req": REQS["n"] - r0,
               "sec": round(time.time() - t0, 2), "info": info}
    except Exception as e:  # noqa: BLE001
        rec = {"source": name, "ok": False, "req": REQS["n"] - r0,
               "sec": round(time.time() - t0, 2),
               "err": f"{type(e).__name__}: {str(e)[:160]}"}
    LOG.append(rec)
    print(json.dumps(rec, ensure_ascii=False))
    return rec


# --- 朴素起点 1：akshare（A股数据最常见的入口，先猜它）
def akshare_a500():
    import akshare as ak
    df = ak.index_zh_a_hist(symbol="000510", period="daily",
                            start_date="20260501", end_date="20260605")
    return {"rows": int(len(df)), "cols": list(df.columns)}


# --- 朴素起点 2：akshare 的新浪后端
def akshare_sina():
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol="sh000510")
    return {"rows": int(len(df)), "cols": list(df.columns)[:8]}


# --- 朴素起点 3：腾讯原始接口（猜参数格式）
def tencent_raw():
    u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    r = requests.get(u, params={"param": "sh000510,day,20260501,20260605,640,qfq"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    js = r.json()
    node = (js.get("data") or {}).get("sh000510") or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise ValueError(f"empty kline keys={list(node.keys())}")
    return {"rows": len(rows), "first": rows[0][0], "last": rows[-1][0]}


# --- 朴素起点 4：东财 push2（猜接口）
def eastmoney():
    u = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    r = requests.get(u, params={"secid": "1.000510", "klt": "101", "fqt": "1",
                                 "beg": "20260501", "end": "20260605",
                                 "fields1": "f1,f2,f3", "fields2": "f51,f52,f53"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    js = r.json()
    kl = ((js.get("data") or {}).get("klines") or [])
    if not kl:
        raise ValueError(f"empty klines code={(js.get('data') or {}).get('code')}")
    return {"rows": len(kl)}


# --- 朴素起点 5：网易 CSV（猜 URL）
def netease():
    u = "https://quotes.money.163.com/service/chddata.html"
    r = requests.get(u, params={"code": "0000510", "start": "20260501", "end": "20260605"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    return {"bytes": len(r.content)}


# --- 朴素起点 6：乐咕赚钱效应（猜 akshare 接口名）
def legu():
    import akshare as ak
    df = ak.stock_market_activity_legu()
    return {"rows": int(len(df)), "cols": list(df.columns)}


probe("akshare.index_zh_a_hist(000510) [东财后端]", akshare_a500)
probe("akshare.stock_zh_index_daily(sh000510) [新浪后端]", akshare_sina)
probe("腾讯 fqkline 原始接口", tencent_raw)
probe("东财 push2", eastmoney)
probe("网易 chddata", netease)
probe("乐咕赚钱效应", legu)

T1 = time.time()
print("\n=== ARM A 汇总 ===")
print(json.dumps({"arm": "A", "http_requests": REQS["n"],
                  "elapsed_sec": round(T1 - T0, 2),
                  "probes": len(LOG),
                  "usable": [r["source"] for r in LOG if r["ok"]],
                  "failed": [r["source"] for r in LOG if not r["ok"]]},
                 ensure_ascii=False, indent=1))
with open(r"F:\001_install\103_multica\multica-upstream-20261105\data\workspaces\workspace-c8faa36a018a\work-413-e5dbb7589ffa\workdir\_armA.json", "w", encoding="utf-8") as f:
    json.dump({"arm": "A", "http_requests": REQS["n"], "elapsed_sec": round(T1 - T0, 2), "log": LOG}, f, ensure_ascii=False, indent=1)
