#!/usr/bin/env python3
"""World Bank WDI 公开 API：全球实际 GDP（2015 美元）与贸易占 GDP 比重。

来源：https://api.worldbank.org/v2/country/{code}/indicator/{ind}?format=json&per_page=1000
用途：P0「全球实际 GDP 增速与贸易量增速」的年度序列。
      差距：WDI 给水平值（自算增速），无「贸易量指数」；IMF WEO 口径取不到（403）。

契约：
  * 单文件，仅标准库。
  * `--start/--end` 按年度；默认取 WLD/CHN/USA/EMU 四个口径。
  * 幂等：主键 (indicator, country, year)；重跑逐字节相同。
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
import urllib.request

UA = "quantdb-alt/1.0 (+research)"
BASE = "https://api.worldbank.org/v2/country/%s/indicator/%s?format=json&per_page=1000"
INDICATORS = {
    "NY.GDP.MKTP.KD": ("real_gdp_constant_2015_usd", "current US$ (2015 base)"),
    "NY.GDP.MKTP.KD.ZG": ("real_gdp_growth", "%"),
    "TG.VAL.TOTL.GD.ZS": ("trade_pct_gdp", "% of GDP"),
}
COUNTRIES = {"WLD": "World", "CHN": "China", "USA": "United States", "EMU": "Euro area"}
FIELDS = ["indicator", "indicator_name", "country", "country_name", "year", "value", "unit", "source_url"]
KEY = ("indicator", "country", "year")
_LAST: dict[str, float] = {}


def http_get_json(url: str, timeout: int = 60, retries: int = 2) -> list:
    last = None
    for i in range(retries + 1):
        wait = 1.05 - (time.time() - _LAST.get("api.worldbank.org", 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST["api.worldbank.org"] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                js = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(js, list) and len(js) >= 2 and isinstance(js[1], list):
                return js[1]
            return []
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


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
    rows = sorted(store.values(), key=lambda r: (r["indicator"], r["country"], int(r["year"])))
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
           "csv_bytes": len(raw),
           "sources": [BASE % ("{country}", "{indicator}")],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params, "years": [years[0], years[-1]] if years else []}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "years")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="World Bank WDI GDP/贸易")
    this_year = dt.date.today().year
    ap.add_argument("--start", default=str(this_year - 10))
    ap.add_argument("--end", default=str(this_year - 1))
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/worldbank_wdi"))
    a = ap.parse_args(argv)

    y0, y1 = int(a.start[:4]), int(a.end[:4])
    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    got, fails = 0, []
    for code, (name, unit) in INDICATORS.items():
        for c in COUNTRIES:
            url = BASE % (c, code)
            try:
                pts = http_get_json(url)
            except Exception as e:  # noqa: BLE001
                fails.append(f"{code}/{c}: {e}")
                continue
            n = 0
            for p in pts:
                try:
                    y = int(p["date"])
                except (KeyError, ValueError):
                    continue
                if not (y0 <= y <= y1) or p.get("value") is None:
                    continue
                r = {"indicator": name, "indicator_name": code, "country": c,
                     "country_name": COUNTRIES[c], "year": str(y),
                     "value": f"{p['value']}", "unit": unit, "source_url": url}
                store[tuple(r[k] for k in KEY)] = r
                n += 1
            got += n
            print(f"{code} {c}: {n} 行")
    keep = {k: v for k, v in store.items() if y0 <= int(v["year"]) <= y1}
    save(a.out, keep, {"start": a.start, "end": a.end, "failures": fails})
    if not keep:
        print("FAIL 无数据：" + "; ".join(fails), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
