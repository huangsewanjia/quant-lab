#!/usr/bin/env python3
"""OWID（IEA / Energy Institute 派生）全球能源消费结构与 CO2 排放。

来源：https://ourworldindata.org/grapher/annual-co2-emissions-per-country.csv
      https://ourworldindata.org/grapher/primary-energy-consumption-by-source.csv
用途：P0「全球能源需求与二氧化碳排放」的区域年度序列（总量 CO2 + 分品种一次能源）。
      注意：OWID 总量 CO2 含工业过程排放，与 IEA World Energy Balances 的「能源部门 CO2」口径不同。

契约：
  * 单文件，仅标准库；宽表（按能源品种分列）转长表落盘。
  * `--start/--end` 按年度。
  * 幂等：主键 (indicator, entity, year, series)；重跑逐字节相同。
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
import urllib.request

UA = "quantdb-alt/1.0 (+research)"
CO2 = "https://ourworldindata.org/grapher/annual-co2-emissions-per-country.csv"
ENERGY = "https://ourworldindata.org/grapher/primary-energy-consumption-by-source.csv"
FIELDS = ["indicator", "entity", "code", "year", "series", "value", "unit", "source_url"]
KEY = ("indicator", "entity", "year", "series")
_LAST: dict[str, float] = {}


def fetch_csv(url: str, timeout: int = 90, retries: int = 2) -> str:
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


def parse_long(text: str, indicator: str, url: str, y0: int, y1: int, unit: str) -> list[dict]:
    rd = csv.DictReader(io.StringIO(text))
    val_col = rd.fieldnames[-1]
    rows = []
    for r in rd:
        try:
            y = int(r["Year"])
            float(r[val_col])
        except (ValueError, TypeError, KeyError):
            continue
        if not (y0 <= y <= y1):
            continue
        rows.append({"indicator": indicator, "entity": r["Entity"], "code": r.get("Code", ""),
                     "year": str(y), "series": val_col, "value": r[val_col],
                     "unit": unit, "source_url": url})
    return rows


def parse_wide(text: str, indicator: str, url: str, y0: int, y1: int) -> list[dict]:
    """Energy 表是宽表：Entity,Code,Year,<品种1>,<品种2>…；转长表。"""
    rd = csv.DictReader(io.StringIO(text))
    cols = [c for c in rd.fieldnames if c not in ("Entity", "Code", "Year")]
    rows = []
    for r in rd:
        try:
            y = int(r["Year"])
        except (ValueError, KeyError):
            continue
        if not (y0 <= y <= y1):
            continue
        for c in cols:
            v = r.get(c, "")
            if v in ("", None):
                continue
            try:
                float(v)
            except ValueError:
                continue
            rows.append({"indicator": indicator, "entity": r["Entity"], "code": r.get("Code", ""),
                         "year": str(y), "series": c, "value": v, "unit": "TWh",
                         "source_url": url})
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
    rows = sorted(store.values(), key=lambda r: (r["indicator"], r["entity"], int(r["year"]), r["series"]))
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
           "csv_bytes": len(raw), "sources": [CO2, ENERGY],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params, "years": [years[0], years[-1]] if years else [],
           "notes": "CO2 为总量（含工业过程）；一次能源单位为 OWID 原始 TWh"}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "years")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OWID 能源消费与 CO2")
    this_year = dt.date.today().year
    ap.add_argument("--start", default=str(this_year - 10))
    ap.add_argument("--end", default=str(this_year - 1))
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/owid_energy_co2"))
    a = ap.parse_args(argv)

    y0, y1 = int(a.start[:4]), int(a.end[:4])
    if y1 < y0:
        print("FAIL --end 早于 --start", file=sys.stderr)
        return 1
    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    ok = 0
    try:
        rows = parse_long(fetch_csv(CO2), "co2_annual", CO2, y0, y1, "tonnes")
        ok += 1
        for r in rows:
            store[tuple(r[k] for k in KEY)] = r
        print(f"co2_annual: {len(rows)} 行")
    except Exception as e:  # noqa: BLE001
        print(f"WARN co2 失败：{e}", file=sys.stderr)
    try:
        rows = parse_wide(fetch_csv(ENERGY), "primary_energy_by_source", ENERGY, y0, y1)
        ok += 1
        for r in rows:
            store[tuple(r[k] for k in KEY)] = r
        print(f"primary_energy_by_source: {len(rows)} 行")
    except Exception as e:  # noqa: BLE001
        print(f"WARN energy 失败：{e}", file=sys.stderr)
    if not ok:
        print("FAIL 两个序列都未取到", file=sys.stderr)
        return 1
    keep = {k: v for k, v in store.items() if y0 <= int(v["year"]) <= y1}
    save(a.out, keep, {"start": a.start, "end": a.end})
    if not keep:
        print(f"FAIL 窗口 {y0}~{y1} 无数据", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
