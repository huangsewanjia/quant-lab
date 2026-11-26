#!/usr/bin/env python3
"""Benchmark Mineral Intelligence 公开价格评估指数（锂/碳酸锂/氢氧化锂/钴/镍/石墨/负极/正极/电芯）。

来源：https://www.benchmarkminerals.com/price-assessments
性质：付费商自有公开页。公开面只有**相对指数点**（基期归一），不是美元/吨绝对价——
      这是 P0「全球锂盐与镍钴价格」「全球电芯价格」无法升级为 A 的结构性原因。

契约：
  * 单文件，仅标准库。
  * 指数页无评估日标注（页面只有 ticker，无 as-of 日期）→ snapshot_date 取抓取日，
    manifest.notes 里写明「快照日=抓取日，非源站评估日」，不得当作评估日引用。
  * `--start/--end` 筛选已累积快照日期。
  * 幂等：主键 (snapshot_date, index_name)，同日重跑 data.csv 逐字节相同。
  * 同域名 >=1.05 s/次。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
URL = "https://www.benchmarkminerals.com/price-assessments"
FIELDS = ["snapshot_date", "index_name", "index_value", "change_pct", "unit", "source_url"]
KEY = ("snapshot_date", "index_name")
_LAST: dict[str, float] = {}


def http_get(url: str, timeout: int = 45, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        wait = 1.05 - (time.time() - _LAST.get(urllib.parse.urlsplit(url).netloc, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST[urllib.parse.urlsplit(url).netloc] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def strip_html(h: str) -> str:
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", h)


def parse(text: str, snapshot: str) -> list[dict]:
    t = strip_html(text)
    rows, seen = [], set()
    pat = re.compile(r"([A-Z][A-Za-z0-9 \-\/&\.]{2,48}?Index)\s+([0-9][0-9,]*\.?[0-9]*)\s+(-?[0-9]+\.?[0-9]*)\s*%")
    for m in pat.finditer(t):
        name = m.group(1).strip()
        if name in seen:
            continue
        seen.add(name)
        rows.append({"snapshot_date": snapshot, "index_name": name,
                     "index_value": m.group(2).replace(",", ""),
                     "change_pct": m.group(3), "unit": "index", "source_url": URL})
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


def save(out: str, store: dict[tuple, dict], params: dict) -> dict:
    os.makedirs(out, exist_ok=True)
    rows = sorted(store.values(), key=lambda r: tuple(r[k] for k in KEY))
    data = os.path.join(out, "data.csv")
    with open(data + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(data + ".tmp", data)
    raw = open(data, "rb").read()
    man = {"dataset": os.path.basename(out), "rows": len(rows), "fields": FIELDS,
           "primary_key": list(KEY), "csv_sha256": hashlib.sha256(raw).hexdigest(),
           "csv_bytes": len(raw), "sources": [URL],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params,
           "notes": "snapshot_date=抓取日（源站公开页未标注评估日）；index 为相对指数点，非 USD/吨",
           "date_range": [rows[0]["snapshot_date"], rows[-1]["snapshot_date"]] if rows else []}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "date_range")},
                     ensure_ascii=False))
    return man


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark 公开价格评估指数快照")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/benchmark_public_index"))
    a = ap.parse_args(argv)

    snapshot = dt.date.today().isoformat()
    try:
        rows = parse(http_get(URL), snapshot)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL 抓取失败：{e}", file=sys.stderr)
        return 1
    if len(rows) < 5:
        print(f"FAIL 仅解析到 {len(rows)} 个指数（页面结构可能改版）", file=sys.stderr)
        return 1
    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    for r in rows:
        store[tuple(r[k] for k in KEY)] = r
    lo, hi = a.start, a.end
    keep = {k: v for k, v in store.items()
            if (not lo or k[0] >= lo) and (not hi or k[0] <= hi)}
    print(f"本次 {len(rows)} 个指数，快照日 {snapshot}；累积 {len(store)}，窗口内 {len(keep)}")
    save(a.out, keep, {"start": lo, "end": hi, "fetched": len(rows)})
    if not keep:
        print(f"FAIL 窗口 {lo}~{hi} 无已累积快照", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
