#!/usr/bin/env python3
"""SNE Research 公开新闻稿：全球动力电池装机、ESS 出货、材料消耗（绝对值 + 同比）。

来源：https://www.sneresearch.com/en/insight/release/（付费商自有公开列表页）
性质：付费数据库的「定期新闻稿摘要」，不是可查询时间序列库；每条稿件给出当期
      绝对值与同比，能拼出粗粒度季度/月度序列。

契约：
  * 单文件，仅标准库。
  * `--start/--end` 按稿件 Registration Date 过滤；默认只增量抓列表里未入过库的稿件，
    因此重跑不会重复请求（`--refresh` 强制定点重抓已入库稿件）。
  * 幂等：主键 (release_id, metric)；同日重跑逐字节相同。
  * 同域名 >=1.05 s/次；单页失败只记 failed 行，不中断整批。
  * 退出码：一条稿件都没取到 → 1。

抽取规则（写死、可复核）：稿件正文里
  "(?P<val>[0-9][0-9,]*\\.?[0-9]*)\\s?(?P<unit>GWh|MWh|GW|GWh)" 取指标值与单位，
  标题里的 "(?P<pct>-?[0-9.]+)%" 取同比；只保留带绝对值的句子作为 key_figure 证据。
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
INDEX = "https://www.sneresearch.com/en/insight/release/"
FIELDS = ["release_id", "publish_date", "title", "metric", "value", "unit",
          "yoy_pct", "period", "evidence", "source_url", "status"]
KEY = ("release_id",)
_LAST: dict[str, float] = {}


def http_get(url: str, timeout: int = 60, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        host = urllib.parse.urlsplit(url).netloc
        wait = 1.05 - (time.time() - _LAST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST[host] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9"})
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


def list_releases(html: str) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r'href="[^"]*release_view/(\d+)/page/0[^"]*"[^>]*>(.*?)</a>', html, flags=re.S):
        rid = m.group(1)
        title = text_of(m.group(2)).strip()
        if rid in seen or len(title) < 12:
            continue
        seen.add(rid)
        out.append({"release_id": rid, "title": title[:200],
                    "url": f"https://www.sneresearch.com/en/insight/release_view/{rid}/page/0"})
    return out


METRIC_PAT = re.compile(
    r"([0-9][0-9,]*\.?[0-9]*)\s*(K|M)?\s*(GWh|MWh|TWh|GW|MWh|tons?|Units?|million|billion)",
    re.I)
CATEGORY_RULES = [
    ("Material consumption", re.compile(r"(cathode|anode|electrolyte|separator|material)", re.I)),
    ("ESS shipments", re.compile(r"\bESS\b|energy storage", re.I)),
    ("EV sales", re.compile(r"\bEV Sales\b|Global EV Sales|FCEV Sales", re.I)),
    ("EV battery installation", re.compile(r"battery (usage|installation)|EV battery", re.I)),
]
PERIOD_PAT = re.compile(r"(Jan[-\u2013]?\s?(?:Sep|Aug|Jun|Mar)|[1-4]Q\d{2}|\d{4})", re.I)


def clean_title(t: str) -> str:
    t = (t.replace("&quot;", "").replace("&#x27;", "").replace("&amp;", "&")
          .replace("\u201c", "").replace("\u201d", "").strip())
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(?<=\d)\s+(?=\d{3}(?:\D|$))", "", t)  # "2 261K" -> "2261K"
    t = re.split(r"\s+(?:Up|Down)\s+[-0-9.,]+\s*%", t, flags=re.I)[0]
    t = re.sub(r"\s*20\d{2}\.\d{2}\.\d{2}\s*$", "", t)
    return t.strip(" |\"")


def metric_name(title: str) -> str:
    for name, pat in CATEGORY_RULES:
        if pat.search(title):
            return name
    return "other"


def parse_release(rid: str, raw_title: str, html: str, url: str) -> list[dict]:
    """一条稿件一行：头条绝对值 + 同比 + 期间。正文只用于取证据句。"""
    t = text_of(html)
    dm = re.search(r"Registration Date\s*(\d{4}-\d{2}-\d{2})", t)
    date = dm.group(1) if dm else ""
    title = clean_title(raw_title)
    yoy = re.search(r"(Up|Down|up|down)\s+(-?[0-9]+(?:\.[0-9]+)?)\s*%", raw_title)
    yoy_pct = yoy.group(2) if yoy else ""
    per = PERIOD_PAT.search(title)
    period = per.group(1) if per else ""
    m = METRIC_PAT.search(title)
    row = {"release_id": rid, "publish_date": date, "title": title[:200],
           "metric": metric_name(title), "value": "", "unit": "", "yoy_pct": yoy_pct,
           "period": period, "evidence": "", "source_url": url, "status": "OK"}
    if m:
        prefix = (m.group(2) or "").upper()
        unit = m.group(3)
        row["value"] = m.group(1).replace(",", "")
        row["unit"] = (prefix + unit) if prefix in ("K", "M") and unit.lower().startswith(("wh", "ton", "unit")) else unit
        # 体证据：正文里含该绝对值的前一句
        needle = m.group(1).replace(",", "")
        for sent in re.split(r"(?<=[\.\!])\s+", t):
            if needle in sent.replace(",", ""):
                row["evidence"] = sent.strip()[:240]
                break
        if not row["evidence"]:
            row["evidence"] = title[:240]
    else:
        row["status"] = "NO_METRIC"
        row["evidence"] = title[:240]
    if row["value"] and row["value"] not in title.replace(",", ""):
        # 数字必须出现在标题里（标题是该稿的头条口径）；正文里的其他数值不算本稿指标
        row["value"], row["unit"], row["status"] = "", "", "NO_METRIC"
    if not row["value"]:
        row["status"] = "NO_METRIC"
    return [row]


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
    rows = sorted(store.values(), key=lambda r: (r["publish_date"], r["release_id"], r["metric"]))
    data = os.path.join(out, "data.csv")
    with open(data + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(data + ".tmp", data)
    raw = open(data, "rb").read()
    man = {"dataset": os.path.basename(out), "rows": len(rows), "fields": FIELDS,
           "primary_key": list(KEY), "csv_sha256": hashlib.sha256(raw).hexdigest(),
           "csv_bytes": len(raw), "sources": [INDEX],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params,
           "date_range": [rows[0]["publish_date"], rows[-1]["publish_date"]] if rows else []}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "date_range")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SNE Research 公开新闻稿绝对值抽取")
    ap.add_argument("--start", default="", help="稿件发布日期下限 YYYY-MM-DD")
    ap.add_argument("--end", default="", help="稿件发布日期上限 YYYY-MM-DD")
    ap.add_argument("--max-pages", type=int, default=2, help="列表页页数上限（每页约 12 条）")
    ap.add_argument("--max-fetch", type=int, default=20, help="单次最多抓取的稿件数（预算闸）")
    ap.add_argument("--refresh", action="store_true", help="重抓已入库稿件")
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/sne_research_releases"))
    a = ap.parse_args(argv)

    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    known = {k[0] for k in store}
    items: list[dict] = []
    try:
        for page in range(a.max_pages):
            html = http_get(INDEX if page == 0 else f"{INDEX}?page={page}")
            items += list_releases(html)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL 列表页抓取失败：{e}", file=sys.stderr)
        return 1
    if not items:
        print("FAIL 列表页未解析到 release_view 链接（可能改版）", file=sys.stderr)
        return 1

    todo = [it for it in items if a.refresh or it["release_id"] not in known]
    todo = todo[:a.max_fetch]
    print(f"列表 {len(items)} 条，待抓 {len(todo)} 条")
    cache_dir = os.path.join(a.out, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    fetched, failed = 0, []
    for it in todo:
        cpath = os.path.join(cache_dir, it["release_id"] + ".html")
        try:
            if os.path.exists(cpath) and not a.refresh:
                html = open(cpath, encoding="utf-8").read()
            else:
                html = http_get(it["url"])
                with open(cpath, "w", encoding="utf-8") as f:
                    f.write(html)
            rows = parse_release(it["release_id"], it["title"], html, it["url"])
        except Exception as e:  # noqa: BLE001
            failed.append(f"{it['release_id']}: {e}")
            print(f"WARN {it['release_id']} 抓取失败：{e}", file=sys.stderr)
            continue
        fetched += 1
        for r in rows:
            store[tuple(r[k] for k in KEY)] = r
        print(f"  {it['release_id']} {rows[0]['publish_date']} {rows[0]['title'][:60]} -> {len(rows)} 指标")

    lo, hi = a.start, a.end
    keep = {k: v for k, v in store.items()
            if (not lo or v["publish_date"] >= lo) and (not hi or v["publish_date"] <= hi)}
    save(a.out, keep, {"start": lo, "end": hi, "listed": len(items),
                       "fetched_releases": fetched, "failed": failed})
    if not keep:
        print(f"FAIL 窗口 {lo}~{hi} 无稿件", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
