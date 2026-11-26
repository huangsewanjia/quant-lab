#!/usr/bin/env python3
"""IRENA PxWeb 公开 API：按区域/技术/年份的可再生能源与储能装机容量（MW）与发电量（GWh）。

来源：https://pxweb.irena.org/api/v1/en/IRENASTAT/Power%20Capacity%20and%20Generation/
      Region_ELECSTAT_2026_H2_PX.px   （JSON-stat 2.0，GET + query 参数）
用途：P0「全球可再生能源装机与产能」「中东与东南亚新能源与电网规划」的装机腿。
      差距：IRENA 只分「抽水蓄能」一种储能技术，电化学储能不在表内；「在建/规划」状态也没有。

契约：
  * 单文件，仅标准库；先取 metadata 拿维度编码，再按区域×技术×年份组查询。
  * `--start/--end` 按年度。
  * 幂等：主键 (region, technology, data_type, year)；重跑逐字节相同。
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

UA = "quantdb-alt/1.0 (+research)"
TABLE = ("https://pxweb.irena.org/api/v1/en/IRENASTAT/Power%20Capacity%20and%20Generation/"
         "Region_ELECSTAT_2026_H2_PX.px")
FIELDS = ["region", "region_name", "technology", "technology_name", "data_type",
          "year", "value", "unit", "source_url"]
KEY = ("region", "technology", "data_type", "year")
_LAST: dict[str, float] = {}
UNIT = {"Electrical Installed Capacity (MW)": "MW", "Electricity Generation (GWh)": "GWh"}


def num(v) -> str:
    """数值落盘统一：整数不带小数点，非整数最多 6 位小数（避免 1.88318e+06 这种科学计数）。"""
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:.6f}".rstrip("0").rstrip(".")


def http_json(url: str, timeout: int = 90, retries: int = 2, body: bytes | None = None):
    last = None
    for i in range(retries + 1):
        wait = 1.05 - (time.time() - _LAST.get("pxweb.irena.org", 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST["pxweb.irena.org"] = time.time()
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def get_meta() -> dict:
    return http_json(TABLE)


def query(meta: dict, region: str, techs: list[str], dtype: str, years: list[str]) -> list[dict]:
    dims = {v["code"]: v for v in meta["variables"]}
    names = {k: dict(zip(v["values"], v.get("valueTexts") or v["values"])) for k, v in dims.items()}
    yr_codes = []
    inv = {t: c for c, t in names["Year"].items()}
    for y in years:
        if y in inv:
            yr_codes.append(inv[y])
    if not yr_codes:
        return []
    q = {"query": [
        {"code": "Region", "selection": {"filter": "item", "values": [region]}},
        {"code": "Technology", "selection": {"filter": "item", "values": techs}},
        {"code": "Data Type", "selection": {"filter": "item", "values": [dtype]}},
        {"code": "Year", "selection": {"filter": "item", "values": yr_codes}},
    ], "response": {"format": "json-stat2"}}
    # PxWeb 的 GET + query 参数会被忽略（只回 metadata）；必须 POST 同一 URL 带 JSON body
    js = http_json(TABLE, body=json.dumps(q).encode("utf-8"))
    if "value" not in js:
        return []
    # json-stat2 的 value 既可能是 {"0": v} 也可能是稠密数组 [v0, v1, ...]（含 null）
    if isinstance(js["value"], list):
        dense = {str(i): v for i, v in enumerate(js["value"]) if v is not None}
    else:
        dense = js["value"]
    dim_list = js["id"]
    sizes = js["size"]
    cats = {d: js["dimension"][d]["category"]["index"] for d in dim_list}
    inv_c = {d: {v: k for k, v in cats[d].items()} for d in dim_list}
    rows = []
    for flat, val in dense.items():
        idx = int(flat)
        coords = {}
        for d, size in zip(reversed(dim_list), reversed(sizes)):
            coords[d] = idx % size
            idx //= size
        code_region = inv_c["Region"][coords["Region"]]
        code_tech = inv_c["Technology"][coords["Technology"]]
        code_dt = inv_c["Data Type"][coords["Data Type"]]
        code_year = inv_c["Year"][coords["Year"]]
        dtype_name = names["Data Type"].get(code_dt, code_dt)
        rows.append({"region": code_region, "region_name": names["Region"].get(code_region, ""),
                     "technology": code_tech, "technology_name": names["Technology"].get(code_tech, ""),
                     "data_type": dtype_name, "year": names["Year"].get(code_year, code_year),
                     "value": num(val), "unit": UNIT.get(dtype_name, ""), "source_url": TABLE})
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
    rows = sorted(store.values(), key=lambda r: (r["region"], r["technology"], r["data_type"], int(r["year"])))
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
           "csv_bytes": len(raw), "sources": [TABLE],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params, "years": [years[0], years[-1]] if years else [],
           "notes": "储能仅覆盖抽水蓄能（IRENA 技术分类所限）"}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "years")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IRENA PxWeb 装机容量/发电量")
    this_year = dt.date.today().year
    ap.add_argument("--start", default=str(this_year - 6))
    ap.add_argument("--end", default=str(this_year - 1))
    ap.add_argument("--regions", default="GLO,CHN,IND,USA,RER,RME,RAF,RAS,RSA")
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/irena_re_capacity"))
    a = ap.parse_args(argv)

    y0, y1 = int(a.start[:4]), int(a.end[:4])
    if y1 < y0:
        print("FAIL --end 早于 --start", file=sys.stderr)
        return 1
    years = [str(y) for y in range(y0, y1 + 1)]
    try:
        meta = get_meta()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL 取 metadata 失败：{e}", file=sys.stderr)
        return 1
    dims = {v["code"]: v for v in meta["variables"]}
    if "Region" not in dims or "Technology" not in dims:
        print(f"FAIL 维度不符（可能换了表）：{[v['code'] for v in meta['variables']]}", file=sys.stderr)
        return 1
    techs = dims["Technology"]["values"]
    dtypes = dims["Data Type"]["values"]
    regions = [r for r in a.regions.split(",") if r]
    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    got, fails = 0, []
    for region in regions:
        for dtype in dtypes:
            try:
                rows = query(meta, region, techs, dtype, years)
            except Exception as e:  # noqa: BLE001
                fails.append(f"{region}/{dtype}: {e}")
                continue
            for r in rows:
                store[tuple(r[k] for k in KEY)] = r
            got += len(rows)
        print(f"{region}: 累计 {got} 行")
    keep = {k: v for k, v in store.items() if y0 <= int(v["year"]) <= y1}
    save(a.out, keep, {"start": a.start, "end": a.end, "regions": regions,
                       "technologies": len(techs), "failures": fails[:20]})
    if not keep:
        print("FAIL 无数据：" + "; ".join(fails[:5]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
