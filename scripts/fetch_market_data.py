# -*- coding: utf-8 -*-
"""
取数脚本：宽基/风格指数日线快照 → data/<EXP>/*.csv + experiments/<EXP>/snapshot_manifest.json

来源与可用性结论见 data_contracts/data_source_registry.md；字段口径见
data_contracts/field_dictionary.md。本脚本只负责「按已知可用性取数 + 落快照 + 记校验值」，
不做指标计算（那是 build_analysis.py），不做结论（那是 experiments/registry.md）。

已知坑（不要重试，详见登记表 §3）：
  - 腾讯源 param 的日期必须是 YYYY-MM-DD，带连字符；用 YYYYMMDD 会返回 40 字节错误体。
  - 东财 push2 对本机出口 IP 限流 → 先做 1 次 preflight，失败则整段跳过（记为「未取到」），
    不空跑 12×45s 重试。成交额因此改用代理口径 volume×close（只做趋势）。
  - 腾讯无中证2000 代码（sh932000）→ 该指数记为未取到，画图用国证2000 替代。
  - 网易 502、乐咕仅当日截面：均不作为本脚本的数据源。

口径：日线收盘；指数不复权；时区 Asia/Shanghai；交易日历 = 沪深交易所日历
窗口：见下方 START / END 常量（每个实验一套；换实验请同时换 --exp）

输出：
  data/<EXP>/A_tencent_<code>.csv      原始层，只追加
  data/<EXP>/B_eastmoney_<code>.csv    同上（东财可用时）
  experiments/<EXP>/snapshot_manifest.json  快照清单（含 sha256，路径为仓库相对）

运行：
  python scripts/fetch_market_data.py --exp WORK-404
"""
import argparse
import hashlib
import json
import os
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- 路径（仓库相对，无写死的任务目录）
EXP = os.environ.get("QUANTLAB_EXP", "WORK-404")
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

# ---------------------------------------------------------------- 窗口（每个实验一套）
START, END = "20250101", "20261114"
START_D, END_D = "2025-01-01", "2026-11-14"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# (显示名, 腾讯代码, 东财 secid, 风格标签)
UNIVERSE = [
    ("上证指数", "sh000001", "1.000001", "全市场"),
    ("深证成指", "sz399001", "0.399001", "全市场"),
    ("上证50", "sh000016", "1.000016", "大盘价值"),
    ("沪深300", "sh000300", "1.000300", "大盘"),
    ("中证500", "sh000905", "1.000905", "中盘"),
    ("中证1000", "sh000852", "1.000852", "小盘"),
    ("中证2000", "sh932000", "1.932000", "超小盘"),
    ("国证2000", "sz399303", "0.399303", "超小盘"),
    ("创业板指", "sz399006", "0.399006", "科技成长"),
    ("科创50", "sh000688", "1.000688", "科技成长"),
    ("中证全指", "sh000985", "1.000985", "全市场"),
    ("中证红利", "sh000922", "1.000922", "防御"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_rel(path):
    """把绝对路径转成仓库相对路径（正斜杠），写进 manifest —— 仓库可移植、快照可复核。"""
    return os.path.relpath(path, REPO).replace("\\", "/")


def fetch_tencent(name, code):
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    # 日期必须带连字符（YYYY-MM-DD）；用 YYYYMMDD 会返回 40 字节错误体
    params = {"param": f"{code},day,{START_D},{END_D},640,qfq"}
    r = requests.get(url, params=params, headers=H, timeout=30)
    r.raise_for_status()
    js = r.json()
    if js.get("code") not in (0, "0", None):
        raise ValueError(f"tencent code={js.get('code')} msg={js.get('msg')}")
    node = (js.get("data") or {}).get(code) or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise ValueError(f"empty kline for {code}: keys={list(node.keys())}")
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = df["date"].astype(str).str.slice(0, 10)
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df["index_name"] = name
    df["source"] = "A_tencent"
    return df


def fetch_eastmoney(name, secid):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid, "klt": "101", "fqt": "1", "beg": START, "end": END,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    r = requests.get(url, params=params, headers=H, timeout=30)
    r.raise_for_status()
    js = r.json()
    data = js.get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        raise ValueError(f"empty klines secid={secid} code={data.get('code')}")
    cols = ["date", "open", "close", "high", "low", "volume", "amount", "amplitude", "pct_chg", "change"]
    recs = []
    for k in klines:
        p = k.split(",")
        recs.append(p[:10] if len(p) >= 10 else p + [None] * (10 - len(p)))
    df = pd.DataFrame(recs, columns=cols)
    df["date"] = df["date"].astype(str).str.slice(0, 10)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df["index_name"] = name
    df["source"] = "B_eastmoney"
    return df


def main():
    ap = argparse.ArgumentParser(description="取数 → 行情快照 + 快照清单")
    ap.add_argument("--exp", default=EXP, help="实验 ID（决定 data/<EXP> 与 experiments/<EXP>）")
    args = ap.parse_args()
    exp = args.exp

    data_dir = os.path.join(REPO, "data", exp)
    out_dir = os.path.join(REPO, "experiments", exp)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    manifest = {
        "experiment": exp,
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "window": {"start": START, "end": END},
        "paths_are": "repo-relative (relative to repository root)",
        "sources": {
            "A_tencent": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get (param=code,day,YYYY-MM-DD,YYYY-MM-DD,640,qfq)",
            "B_eastmoney": "https://push2his.eastmoney.com/api/qt/stock/kline/get (secid,klt=101,fqt=1) —— 限流时整段跳过",
            "C_tushare_mcp": "MCP tushare index_daily（服务端，独立交叉校验；数值见 build_analysis.py 的 TUSHARE 常量）",
        },
        "items": [],
    }

    print(f"== 源A 腾讯：全历史收盘/成交量（exp={exp}）==", flush=True)
    for name, code, _sec, tag in UNIVERSE:
        try:
            df = fetch_tencent(name, code)
            p = os.path.join(data_dir, f"A_tencent_{code}.csv")
            df.to_csv(p, index=False, encoding="utf-8")
            manifest["items"].append({
                "source": "A_tencent", "name": name, "code": code, "style_tag": tag, "ok": True,
                "rows": int(len(df)), "date_min": str(df["date"].iloc[0]), "date_max": str(df["date"].iloc[-1]),
                "path": repo_rel(p), "sha256": sha256(p),
            })
            print(f"[OK]   {name:<8} {code:<10} rows={len(df):<5} {df['date'].iloc[0]}..{df['date'].iloc[-1]} sha={sha256(p)[:12]}", flush=True)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"[:180]
            manifest["items"].append({"source": "A_tencent", "name": name, "code": code, "ok": False, "error": msg})
            print(f"[FAIL] {name:<8} {code:<10} {msg}", flush=True)
        time.sleep(1.0)

    print("\n== 源B 东财 preflight（不通则整段跳过，不空跑重试）==", flush=True)
    em_ok = False
    try:
        t = fetch_eastmoney("中证全指", "1.000985")
        em_ok = len(t) > 100
        print(f"preflight rows={len(t)} span={t['date'].iloc[0]}..{t['date'].iloc[-1]}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"preflight FAIL: {type(e).__name__}: {e}"[:200], flush=True)

    if em_ok:
        for name, code, secid, tag in UNIVERSE:
            try:
                df = fetch_eastmoney(name, secid)
                p = os.path.join(data_dir, f"B_eastmoney_{code}.csv")
                df.to_csv(p, index=False, encoding="utf-8")
                manifest["items"].append({
                    "source": "B_eastmoney", "name": name, "code": code, "style_tag": tag, "ok": True,
                    "rows": int(len(df)), "date_min": str(df["date"].iloc[0]), "date_max": str(df["date"].iloc[-1]),
                    "path": repo_rel(p), "sha256": sha256(p),
                })
                print(f"[OK]   {name:<8} {secid:<10} rows={len(df):<5} sha={sha256(p)[:12]}", flush=True)
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"[:180]
                manifest["items"].append({"source": "B_eastmoney", "name": name, "code": code, "ok": False, "error": msg})
                print(f"[FAIL] {name:<8} {secid:<10} {msg}", flush=True)
            time.sleep(3.0)
    else:
        for name, code, _sec, tag in UNIVERSE:
            manifest["items"].append({"source": "B_eastmoney", "name": name, "code": code, "style_tag": tag,
                                      "ok": False, "error": "skipped: eastmoney push2 对本机 IP 限流（preflight 失败），成交额改用代理口径"})
        print("跳过源B：成交额将以 volume×close 代理口径计算，并在数据契约中标注", flush=True)

    mpath = os.path.join(out_dir, "snapshot_manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    okA = sum(1 for i in manifest["items"] if i["source"] == "A_tencent" and i.get("ok"))
    okB = sum(1 for i in manifest["items"] if i["source"] == "B_eastmoney" and i.get("ok"))
    print(f"\n源A 成功 {okA}/{len(UNIVERSE)}，源B 成功 {okB}/{len(UNIVERSE)}；manifest -> {repo_rel(mpath)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
