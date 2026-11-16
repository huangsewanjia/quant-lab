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

命令行：
  python probe_armA_no_registry.py                       # 结果写到 experiments/WORK-413-S2/rerun/armA_result.json
  python probe_armA_no_registry.py --out D:\\tmp\\a.json  # 自定输出路径
  python probe_armA_no_registry.py --no-write            # 只打印，不写文件

> 输出路径**已参数化**，脚本内不含任何仓库外的绝对路径，可开箱即跑。
> 默认写到 `rerun/` 子目录，**不覆盖**已入库的实测证据 `armA_result.json`。
"""
import argparse
import json
import os
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))          # <repo>/experiments/WORK-413-S2 → <repo>
DEFAULT_OUT = os.path.join(REPO, "experiments", "WORK-413-S2", "rerun", "armA_result.json")

_AP = argparse.ArgumentParser(description="ARM A（无登记表）：第一次做这类研究时的取数探测路线")
_AP.add_argument("--out", default=DEFAULT_OUT,
                 help="结果 JSON 输出路径（默认：experiments/WORK-413-S2/rerun/armA_result.json，不覆盖已入库证据）")
_AP.add_argument("--no-write", action="store_true", help="只打印结果，不写文件")
ARGS = _AP.parse_args()

# 本文件的出处说明：写清楚「这个 JSON 是谁、哪一次跑批产出的、能不能重跑」
PROVENANCE = {
    "script": "experiments/WORK-413-S2/probe_armA_no_registry.py",
    "arm": "A —— 无登记表路线复现（前）",
    "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "elapsed_sec_scope": "本次跑批的墙钟耗时：6 次 HTTP 请求（4 次注定失败）+ 逐条探活耗时",
    "note": "ARM A 是**路线复现**不是盲测：执行者已知部分答案，臂内不含「猜源」的搜索成本，"
            "因此本臂测的是真实发生的请求次数、失败模式与耗时——是真实成本的**下界**，不是上界。",
    "note_rerun": "本臂的失败数取决于跑批当时各源的可用性（东财限流、网易 502、腾讯日期格式坑）。"
                  "重跑不构成同一次实验，结果可能不同；复跑请用 --out 另存，不要覆盖本文件。",
}

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
if ARGS.no_write:
    print("\n[no-write] 未写文件（--no-write）")
else:
    out_path = os.path.abspath(ARGS.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"arm": "A", "http_requests": REQS["n"], "elapsed_sec": round(T1 - T0, 2),
                   "log": LOG, "provenance": PROVENANCE}, f, ensure_ascii=False, indent=1)
    print(f"\n[written] {out_path}")
