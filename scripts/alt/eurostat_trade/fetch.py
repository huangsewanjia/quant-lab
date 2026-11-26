#!/usr/bin/env python3
"""Eurostat 公开 API：欧洲对外贸易依赖度（SITC 大类）+ 有色金属制造业增加值（nama_10_a64 C24）。

来源：https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ext_lt_intratrd
      https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nama_10_a64
用途：P0「欧洲半导体设备与材料进口依赖度」「欧洲锂矿开采与进口依赖度」「欧洲原铝产量与电力成本」
      的贸易/产值腿。口径是 SITC 大类（SITC2_4 原料、SITC5 化工、SITC6 制材、SITC7 机械与运输设备），
      **不是** HS 8486 / 253090 单品类——单品类需 COMEXT ds-045409，本脚本不覆盖（见台账）。

契约：
  * 单文件，仅标准库。
  * `--start/--end` 按年度（YYYY 或 YYYY-MM-DD，取前 4 位）。
  * 幂等：主键 (dataset, geo, partner, flow, time)；重跑逐字节相同。
  * 同域名 >=1.05 s/次。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; quantdb-alt/1.0)"
BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
FIELDS = ["dataset", "geo", "partner", "flow", "sitc", "nace_r2", "unit",
          "time", "value", "source_url"]
KEY = ("dataset", "geo", "partner", "flow", "sitc", "nace_r2", "time")
_LAST: dict[str, float] = {}


def http_get_json(url: str, timeout: int = 60, retries: int = 2) -> dict:
    last = None
    for i in range(retries + 1):
        host = urllib.parse.urlsplit(url).netloc
        wait = 1.05 - (time.time() - _LAST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST[host] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def decode_jsonstat2(js: dict, dataset: str, url: str) -> list[dict]:
    """JSON-stat 2.0：value 的扁平下标按 dims 的 size 行优先展开。"""
    dims = js["id"]
    sizes = js["size"]
    cats = {d: (js["dimension"][d]["category"]["index"] if "index" in js["dimension"][d]["category"]
                else {k: i for i, k in enumerate(js["dimension"][d]["category"]["label"])})
            for d in dims}
    inv = {d: {v: k for k, v in cats[d].items()} for d in dims}
    values = js.get("value") or {}
    rows = []
    for flat, val in values.items():
        idx = int(flat)
        coords = {}
        for d, size in zip(reversed(dims), reversed(sizes)):
            coords[d] = idx % size
            idx //= size
        r = {"dataset": dataset, "geo": "", "partner": "", "flow": "", "sitc": "",
             "nace_r2": "", "unit": "", "time": "", "value": f"{val:g}" if val is not None else "",
             "source_url": url}
        for d in dims:
            code = inv[d].get(coords[d], "")
            if d == "geo":
                r["geo"] = code
            elif d == "partner":
                r["partner"] = code
            elif d in ("indic_et", "indic_na", "na_item"):
                r["flow"] = code
            elif d == "sitc06":
                r["sitc"] = code
            elif d == "nace_r2":
                r["nace_r2"] = code
            elif d == "unit":
                r["unit"] = code
            elif d == "time":
                r["time"] = code
        rows.append(r)
    return rows


def fetch_trade(years: list[str]) -> list[dict]:
    rows = []
    for sitc in ("SITC2_4", "SITC5", "SITC6", "SITC7"):
        q = [("format", "JSON"), ("geo", "EU27_2020"), ("partner", "EXT_EU27_2020"),
             ("sitc06", sitc)] + [("time", y) for y in years]
        url = BASE + "ext_lt_intratrd?" + urllib.parse.urlencode(q)
        js = http_get_json(url)
        got = decode_jsonstat2(js, "ext_lt_intratrd", url)
        rows += got
        print(f"  ext_lt_intratrd {sitc}: {len(got)} 点")
    return rows


def fetch_nama(years: list[str]) -> list[dict]:
    q = [("format", "JSON"), ("geo", "EU27_2020"), ("nace_r2", "C24"),
         ("na_item", "B1G"), ("unit", "CP_MEUR")] + [("time", y) for y in years]
    url = BASE + "nama_10_a64?" + urllib.parse.urlencode(q)
    try:
        js = http_get_json(url)
    except Exception as e:  # noqa: BLE001
        print(f"WARN nama_10_a64 失败（不阻塞贸易腿）：{e}", file=sys.stderr)
        return []
    got = decode_jsonstat2(js, "nama_10_a64", url)
    print(f"  nama_10_a64 C24 B1G: {len(got)} 点")
    return got


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
    rows = sorted(store.values(), key=lambda r: tuple(r[k] for k in KEY))
    data = os.path.join(out, "data.csv")
    with open(data + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(data + ".tmp", data)
    raw = open(data, "rb").read()
    years = sorted({r["time"] for r in rows})
    man = {"dataset": os.path.basename(out), "rows": len(rows), "fields": FIELDS,
           "primary_key": list(KEY), "csv_sha256": hashlib.sha256(raw).hexdigest(),
           "csv_bytes": len(raw), "sources": [BASE + "ext_lt_intratrd", BASE + "nama_10_a64"],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params, "years": [years[0], years[-1]] if years else []}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "years")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Eurostat 贸易依赖度 + C24 增加值")
    this_year = dt.date.today().year
    ap.add_argument("--start", default=str(this_year - 5))
    ap.add_argument("--end", default=str(this_year - 1))
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/eurostat_trade"))
    a = ap.parse_args(argv)

    y0, y1 = int(a.start[:4]), int(a.end[:4])
    if y1 < y0:
        print("FAIL --end 早于 --start", file=sys.stderr)
        return 1
    years = [str(y) for y in range(y0, y1 + 1)]
    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    errors = []
    try:
        rows = fetch_trade(years)
    except Exception as e:  # noqa: BLE001
        errors.append(f"ext_lt_intratrd: {e}")
        rows = []
    rows += fetch_nama(years)
    for r in rows:
        store[tuple(r[k] for k in KEY)] = r
    if not rows:
        print("FAIL 未取到任何 Eurostat 数据点：" + "; ".join(errors), file=sys.stderr)
        return 1
    keep = {k: v for k, v in store.items() if y0 <= int(v["time"] or 0) <= y1}
    save(a.out, keep, {"start": a.start, "end": a.end, "years": years})
    if not keep:
        print(f"FAIL 窗口 {years[0]}~{years[-1]} 无数据点", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
