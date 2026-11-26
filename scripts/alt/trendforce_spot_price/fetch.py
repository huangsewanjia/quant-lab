#!/usr/bin/env python3
"""TrendForce 公开现货价表（DRAM / NAND Flash）日快照。

来源：https://www.trendforce.cn/price/dram/dram_spot
      https://www.trendforce.cn/price/flash/flash_spot
性质：付费商自有公开页（现货价公开、合约价需 Gold+ 会员）。因此本脚本只取现货价，
      合约价不在此脚本覆盖范围内（见 docs/alt_gap_ledger.csv 第 10 行）。

契约：
  * 单文件，仅标准库（urllib + html.parser）。
  * `--start/--end`（YYYY-MM-DD）筛选**已累积**的快照日期；站方无历史接口，
    每次运行只追加当日快照，历史靠日更累积（manifest 记录缺口）。
  * 幂等：以 (snapshot_date, market, item) 为主键合并，重复运行不产生重复行，
    且同一交易日两次运行的 data.csv 逐字节相同（行内不含抓取时间）。
  * 同域名请求间隔 >= 1.05 s（模块级 _LAST[host]）。
  * 失败退出码非 0 并打印原因；部分品种缺失时打印 WARN 但退出码 0。

输出：<本目录>/data.csv、<本目录>/manifest.json（rows / sha256 / sources / fetched_at）。
默认落盘目录可用环境变量 ALT_RAW_DIR 覆盖（测试用），生产为
/srv/share/quantdb/raw/industry_alt/trendforce_spot_price。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html.parser
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
URLS = {
    "DRAM": "https://www.trendforce.cn/price/dram/dram_spot",
    "NAND Flash": "https://www.trendforce.cn/price/flash/flash_spot",
}
FIELDS = ["snapshot_date", "market", "item", "high", "low", "avg_usd",
          "change_pct", "unit", "source_url"]
KEY = ("snapshot_date", "market", "item")

_LAST: dict[str, float] = {}


def _throttle(host: str) -> None:
    wait = 1.05 - (time.time() - _LAST.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _LAST[host] = time.time()


def http_get(url: str, timeout: int = 40, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        _throttle(urllib.parse.urlsplit(url).netloc)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < retries:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} failed after {retries + 1} attempts: {last}")


class _Tables(html.parser.HTMLParser):
    """收集 <tr> 内 <td>/<th> 的纯文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c for c in self._row):
                self.rows.append(self._row)
            self._row = None


def parse_page(text: str, market: str, url: str) -> tuple[str, list[dict]]:
    m = re.search(r"更新日期\s*(\d{4}-\d{2}-\d{2})", text)
    snapshot = m.group(1) if m else dt.date.today().isoformat()
    p = _Tables()
    p.feed(text)
    out = []
    for cells in p.rows:
        if len(cells) < 7:
            continue
        item = cells[0]
        if "规格" in item or item.lower() in ("item", ""):
            continue
        nums = cells[1:7]
        try:
            high, low, _ph, _pl, avg = (float(x.replace(",", "")) for x in nums[:5])
        except ValueError:
            continue
        chg = nums[5].replace("%", "").replace("\u2014", "").strip() or "0"
        try:
            chg = float(chg)
        except ValueError:
            chg = 0.0
        out.append({
            "snapshot_date": snapshot, "market": market, "item": item,
            "high": f"{high:g}", "low": f"{low:g}", "avg_usd": f"{avg:g}",
            "change_pct": f"{chg:g}", "unit": "USD", "source_url": url,
        })
    return snapshot, out


def load_rows(path: str) -> dict[tuple, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if not all(k in (rd.fieldnames or []) for k in KEY):
            print(f"WARN {path} 主键列不匹配（旧 schema），按空库处理", file=sys.stderr)
            return {}
        return {tuple(r[k] for k in KEY): r for r in rd}


def save(paths: dict, store: dict[tuple, dict], sources: list[str], params: dict) -> None:
    os.makedirs(paths["out"], exist_ok=True)
    rows = sorted(store.values(), key=lambda r: tuple(r[k] for k in KEY))
    tmp = paths["data"] + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, paths["data"])
    raw = open(paths["data"], "rb").read()
    man = {
        "dataset": os.path.basename(paths["out"]),
        "rows": len(rows),
        "fields": FIELDS,
        "primary_key": list(KEY),
        "csv_sha256": hashlib.sha256(raw).hexdigest(),
        "csv_bytes": len(raw),
        "sources": sources,
        "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "params": params,
        "date_range": [rows[0]["snapshot_date"], rows[-1]["snapshot_date"]] if rows else [],
    }
    with open(paths["manifest"], "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "date_range")},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TrendForce 公开现货价日快照")
    ap.add_argument("--start", default="", help="起始快照日 YYYY-MM-DD（含）")
    ap.add_argument("--end", default="", help="结束快照日 YYYY-MM-DD（含）")
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/trendforce_spot_price"))
    a = ap.parse_args(argv)

    paths = {"out": a.out, "data": os.path.join(a.out, "data.csv"),
             "manifest": os.path.join(a.out, "manifest.json")}
    store = load_rows(paths["data"])
    fetched, errors = 0, []
    for market, url in URLS.items():
        try:
            snapshot, rows = parse_page(http_get(url), market, url)
        except Exception as e:  # noqa: BLE001 - 记录原因后继续取另一品种
            errors.append(f"{market}: {e}")
            print(f"WARN {market} 抓取失败：{e}", file=sys.stderr)
            continue
        if not rows:
            errors.append(f"{market}: 页面无可解析表格行（可能改版）")
            print(f"WARN {market} 无可解析行（可能改版）", file=sys.stderr)
            continue
        fetched += len(rows)
        for r in rows:
            store[tuple(r[k] for k in KEY)] = r
        print(f"{market}: {len(rows)} 行，快照日 {snapshot}")

    if not store:
        print("FAIL 未取到任何行，未落盘", file=sys.stderr)
        return 1

    lo, hi = a.start, a.end
    keep = {k: v for k, v in store.items()
            if (not lo or k[0] >= lo) and (not hi or k[0] <= hi)}
    if lo or hi:
        print(f"窗口 {lo or '-'} ~ {hi or '-'}：{len(keep)} 行（累积 {len(store)} 行）")
    save(paths, keep, list(URLS.values()), {"start": lo, "end": hi, "fetched_rows": fetched})
    if not keep:
        print(f"FAIL 窗口 {lo}~{hi} 内没有已累积快照（源站无历史接口）", file=sys.stderr)
        return 1
    if errors and fetched == 0:
        print("FAIL 全部品种抓取失败：" + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
