#!/usr/bin/env python3
"""EMMES（Energy Storage Europe / LCP Delta 欧洲储能市场监测）公开版本页的装机绝对值。

来源：https://energystorageeurope.eu/publication/emmes-10-june-2026/
用途：P0「全球储能装机与系统成本」的**欧洲装机腿**（新增 GW/GWh、累计 GW、2030 增速）。
      差距：公开页是新闻稿正文，不是表；系统成本（USD/kW·kWh）无公开源。

契约：
  * 单文件，仅标准库。
  * `--start/--end` 按版本日期（从页面标题解析，如 "EMMES 10.0 - June 2026" → 2026-06-01）。
  * 幂等：主键 (edition, metric)；重跑逐字节相同（页面正文缓存在 cache/，`--refresh` 可重抓）。
  * 同域名 >=1.05 s/次。
  * 一条数值都解析不到 → 退出码 1，并打印页面片段，便于判断是否改版。
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
EDITIONS = {
    "EMMES 10.0 - June 2026": "https://energystorageeurope.eu/publication/emmes-10-june-2026/",
}
FIELDS = ["edition", "edition_date", "metric", "value", "unit", "evidence", "source_url"]
KEY = ("edition", "metric")
_LAST: dict[str, float] = {}

PAT_GW_GWH = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*GW\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*GWh")
PAT_INSTALLED = re.compile(r"(?:surpassing|reach|hit|at)\s+([0-9]+(?:\.[0-9]+)?)\s*GW")
PAT_GROWTH = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s+growth")


def http_get(url: str, timeout: int = 60, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        wait = 1.05 - (time.time() - _LAST.get("energystorageeurope.eu", 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST["energystorageeurope.eu"] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def text_of(h: str) -> str:
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", h)


def sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[\.\!])\s+", t) if s.strip()]


def parse(edition: str, html: str, url: str) -> list[dict]:
    t = text_of(html)
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", edition)
    if m:
        month = ["January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December"].index(m.group(1)) + 1
        edate = f"{m.group(2)}-{month:02d}-01"
    else:
        edate = ""
    rows = []
    for sent in sentences(t):
        g = PAT_GW_GWH.search(sent)
        if g and "added" in sent.lower():
            rows.append(dict(edition=edition, edition_date=edate, metric="additions_gw",
                             value=g.group(1), unit="GW", evidence=sent[:240], source_url=url))
            rows.append(dict(edition=edition, edition_date=edate, metric="additions_gwh",
                             value=g.group(2), unit="GWh", evidence=sent[:240], source_url=url))
        i = PAT_INSTALLED.search(sent)
        if i and "installed" in sent.lower() and not any(r["metric"] == "installed_gw" for r in rows):
            rows.append(dict(edition=edition, edition_date=edate, metric="installed_gw",
                             value=i.group(1), unit="GW", evidence=sent[:240], source_url=url))
        gr = PAT_GROWTH.search(sent)
        if gr and not any(r["metric"] == "growth_to_2030_pct" for r in rows):
            rows.append(dict(edition=edition, edition_date=edate, metric="growth_to_2030_pct",
                             value=gr.group(1), unit="%", evidence=sent[:240], source_url=url))
    seen, uniq = set(), []
    for r in rows:
        if r["metric"] in seen:
            continue
        seen.add(r["metric"])
        uniq.append(r)
    return uniq


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
    rows = sorted(store.values(), key=lambda r: (r["edition_date"], r["edition"], r["metric"]))
    data = os.path.join(out, "data.csv")
    with open(data + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(data + ".tmp", data)
    raw = open(data, "rb").read()
    man = {"dataset": os.path.basename(out), "rows": len(rows), "fields": FIELDS,
           "primary_key": list(KEY), "csv_sha256": hashlib.sha256(raw).hexdigest(),
           "csv_bytes": len(raw), "sources": list(EDITIONS.values()),
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params,
           "notes": "数值来自新闻稿正文，非表格；每行 evidence 保留原句，便于复核"}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256")}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EMMES 欧洲储能公开版数值")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/emmes_storage"))
    a = ap.parse_args(argv)

    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    cache = os.path.join(a.out, "cache")
    os.makedirs(cache, exist_ok=True)
    ok = 0
    for edition, url in EDITIONS.items():
        slug = re.sub(r"[^A-Za-z0-9]+", "_", edition).strip("_")
        cpath = os.path.join(cache, slug + ".html")
        try:
            if os.path.exists(cpath) and not a.refresh:
                html = open(cpath, encoding="utf-8").read()
            else:
                html = http_get(url)
                with open(cpath, "w", encoding="utf-8") as f:
                    f.write(html)
        except Exception as e:  # noqa: BLE001
            print(f"WARN {edition} 抓取失败：{e}", file=sys.stderr)
            continue
        rows = parse(edition, html, url)
        if not rows:
            print(f"WARN {edition} 未解析到数值（页面可能改版）：{text_of(html)[:160]}", file=sys.stderr)
            continue
        ok += 1
        for r in rows:
            store[tuple(r[k] for k in KEY)] = r
        print(f"{edition}: {len(rows)} 个指标 " + ", ".join(f"{r['metric']}={r['value']}{r['unit']}" for r in rows))

    lo, hi = a.start, a.end
    keep = {k: v for k, v in store.items()
            if (not lo or v["edition_date"] >= lo) and (not hi or v["edition_date"] <= hi)}
    save(a.out, keep, {"start": lo, "end": hi, "editions_ok": ok})
    if not keep:
        print(f"FAIL 窗口 {lo}~{hi} 无版本页数据", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
