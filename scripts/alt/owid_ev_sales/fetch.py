#!/usr/bin/env python3
"""OWID（IEA Global EV Outlook 派生）全球电动车销量与新车渗透率。

来源：https://ourworldindata.org/grapher/electric-car-sales.csv
      https://ourworldindata.org/grapher/electric-car-sales-share.csv
用途：P0「全球 EV 销量与中国渗透率」「全球 EV 销量与新车渗透率」「美国 EV 销量与新车渗透率」
      「拉美与澳洲 EV 渗透率」「东南亚 EV 与两轮车电动化」的国家/地区年度序列。
      元数据标注来源为 IEA GEO，公开代理相对一手源滞后约一版（6–12 个月）。

契约：
  * 单文件，仅标准库。
  * `--start/--end` 按年度；默认全量取回后落盘，窗口只影响输出行。
  * 幂等：主键 (indicator, entity, year)；重跑逐字节相同。
  * 同域名 >=1.05 s/次。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "quantdb-alt/1.0 (+research)"
SOURCES = {
    "ev_sales_units": "https://ourworldindata.org/grapher/electric-car-sales.csv",
    "ev_sales_share": "https://ourworldindata.org/grapher/electric-car-sales-share.csv",
}
FIELDS = ["indicator", "entity", "code", "year", "value", "unit", "source_url"]
KEY = ("indicator", "entity", "year")
_LAST: dict[str, float] = {}


def fetch_csv(url: str, timeout: int = 60, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        wait = 1.05 - (time.time() - _LAST.get("ourworldindata.org", 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST["ourworldindata.org"] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def parse(text: str, indicator: str, url: str, y0: int, y1: int) -> list[dict]:
    rd = csv.DictReader(io.StringIO(text))
    val_col = rd.fieldnames[-1]
    unit = "vehicles" if indicator == "ev_sales_units" else "%"
    rows = []
    for r in rd:
        try:
            y = int(r["Year"])
            v = r[val_col]
            if v in ("", None) or not (y0 <= y <= y1):
                continue
            float(v)
        except (ValueError, TypeError):
            continue
        rows.append({"indicator": indicator, "entity": r["Entity"], "code": r.get("Code", ""),
                     "year": str(y), "value": v, "unit": unit, "source_url": url})
    return rows


def load_rows(path: str) -> dict[tuple, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if not all(k in (rd.fieldnames or []) for k in KEY):
            print(f"WARN {path} 主键列不匹配（旧 schema），按空库处理", file=sys.stderr)
            return {}
        return {tuple(r[k] for k in KEY): r for r in rd}


def save(out: str, store: dict[tuple, dict], params: dict) -> None:
    os.makedirs(out, exist_ok=True)
    rows = sorted(store.values(), key=lambda r: (r["indicator"], r["entity"], int(r["year"])))
    data = os.path.join(out, "data.csv")
    with open(data + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(data + ".tmp", data)
    raw = open(data, "rb").read()
    years = sorted({int(r["year"]) for r in rows})
    man = {"dataset": os.path.basename(out), "rows": len(rows), "fields": FIELDS,
           "primary_key": list(KEY), "csv_sha256": hashlib.sha256(raw).hexdigest(),
           "csv_bytes": len(raw), "sources": list(SOURCES.values()),
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params, "years": [years[0], years[-1]] if years else [],
           "notes": "OWID 派生自 IEA Global EV Outlook（公开版本滞后一手源约一版）"}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "years")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OWID EV 销量/渗透率")
    this_year = dt.date.today().year
    ap.add_argument("--start", default=str(this_year - 10))
    ap.add_argument("--end", default=str(this_year - 1))
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/owid_ev_sales"))
    a = ap.parse_args(argv)

    y0, y1 = int(a.start[:4]), int(a.end[:4])
    if y1 < y0:
        print("FAIL --end 早于 --start", file=sys.stderr)
        return 1
    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    ok = 0
    for indicator, url in SOURCES.items():
        try:
            rows = parse(fetch_csv(url), indicator, url, y0, y1)
        except Exception as e:  # noqa: BLE001
            print(f"WARN {indicator} 失败：{e}", file=sys.stderr)
            continue
        ok += 1
        for r in rows:
            store[tuple(r[k] for k in KEY)] = r
        print(f"{indicator}: {len(rows)} 行")
    if not ok:
        print("FAIL 两个 OWID 序列都未取到", file=sys.stderr)
        return 1
    keep = {k: v for k, v in store.items() if y0 <= int(v["year"]) <= y1}
    save(a.out, keep, {"start": a.start, "end": a.end})
    if not keep:
        print(f"FAIL 窗口 {y0}~{y1} 无数据", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
