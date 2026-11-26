#!/usr/bin/env python3
"""国内期货交易所公开日行情：广期所（碳酸锂/多晶硅/工业硅…）+ 上期所（铜/铝/铅/锌/镍/锡）。

来源：
  SHFE  https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{YYYYMMDD}.dat  （一天一个 JSON）
  GFEX  POST http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList            （trade_date=YYYYMMDD&trade_type=0）

用途：P0「全球交易所库存与注册仓单」的**持仓量/成交**公开面、P0「铜/铝/铅/锌现货价与升贴水」
      的期货腿（升贴水 = 现货 - 期货，现货腿见 alt_gap_ledger 说明）。
      注册仓单（warrant）明细需交易所会员后台，本脚本不覆盖。

契约：
  * 单文件，仅标准库。
  * `--start/--end`（YYYY-MM-DD）为交易日区间；已抓齐的日期跳过（幂等增量）。
    「抓齐」= 该日 SHFE 与 GFEX 都成功（GFEX 碳酸锂 20230721 上市，此前只要求 SHFE）。
  * 幂等：主键 (trade_date, exchange, contract)；同日重跑 data.csv 逐字节相同。
  * 同域名 >=1.05 s/次；单日单所失败记 failed.json，不写占位行。
  * 交易日历：周一至周五；节假日源站返回 404/空 → 记为 holiday 跳过，不算失败。
  * 退出码：区间内一行都没取到 → 1。
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

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
SHFE = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx%s.dat"
GFEX = "http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList"
SHFE_PRODUCTS = ("CU_F", "AL_F", "ZN_F", "PB_F", "NI_F", "SN_F")
GFEX_LISTING = dt.date(2023, 7, 21)
FIELDS = ["trade_date", "exchange", "product", "contract", "close", "settle",
          "volume", "open_interest", "source_url"]
KEY = ("trade_date", "exchange", "contract")
_LAST: dict[str, float] = {}


def _throttle(host: str) -> None:
    wait = 1.05 - (time.time() - _LAST.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _LAST[host] = time.time()


def fetch(url: str, data: bytes | None = None, timeout: int = 40) -> bytes:
    host = urllib.parse.urlsplit(url).netloc
    _throttle(host)
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_shfe(day: str) -> list[dict] | None:
    """返回 None 表示非交易日（404）。"""
    url = SHFE % day
    try:
        raw = fetch(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    j = json.loads(raw.decode("utf-8", "replace"))
    rows = []
    for k, v in j.items():
        if not isinstance(v, list) or not v or not isinstance(v[0], dict):
            continue
        for it in v:
            prod = (it.get("PRODUCTID") or it.get("VARNAME") or "").upper()
            if prod not in SHFE_PRODUCTS:
                continue
            rows.append({"trade_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                         "exchange": "SHFE", "product": prod,
                         "contract": prod + str(it.get("DELIVERYMONTH") or ""),
                         "close": str(it.get("CLOSEPRICE") or ""),
                         "settle": str(it.get("SETTLEMENTPRICE") or ""),
                         "volume": str(it.get("VOLUME") or ""),
                         "open_interest": str(it.get("OPENINTEREST") or ""),
                         "source_url": url})
    return rows


def parse_gfex(day: str) -> list[dict]:
    body = f"trade_date={day}&trade_type=0".encode()
    j = json.loads(fetch(GFEX, data=body).decode("utf-8", "replace"))
    if j.get("code") != "0":
        raise RuntimeError(f"GFEX 返回 code={j.get('code')} msg={j.get('msg')}")
    rows = []
    for it in j.get("data") or []:
        contract = (it.get("variety") or "") + (it.get("delivMonth") or "")
        if not it.get("delivMonth"):
            continue  # 「总计」行
        rows.append({"trade_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                     "exchange": "GFEX", "product": it.get("varietyEn") or it.get("variety") or "",
                     "contract": contract,
                     "close": str(it.get("close") if it.get("close") is not None else ""),
                     "settle": str(it.get("clearPrice") if it.get("clearPrice") is not None else ""),
                     "volume": str(it.get("volumn") if it.get("volumn") is not None else ""),
                     "open_interest": str(it.get("openInterest") if it.get("openInterest") is not None else ""),
                     "source_url": GFEX})
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


def save(out: str, store: dict[tuple, dict], params: dict, failed: list[dict]) -> None:
    os.makedirs(out, exist_ok=True)
    rows = sorted(store.values(), key=lambda r: (r["trade_date"], r["exchange"], r["contract"]))
    data = os.path.join(out, "data.csv")
    with open(data + ".tmp", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(data + ".tmp", data)
    raw = open(data, "rb").read()
    man = {"dataset": os.path.basename(out), "rows": len(rows), "fields": FIELDS,
           "primary_key": list(KEY), "csv_sha256": hashlib.sha256(raw).hexdigest(),
           "csv_bytes": len(raw), "sources": [SHFE % "{YYYYMMDD}", GFEX],
           "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "params": params,
           "date_range": [rows[0]["trade_date"], rows[-1]["trade_date"]] if rows else []}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "failed.json"), "w", encoding="utf-8") as f:
        json.dump(failed, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: man[k] for k in ("dataset", "rows", "csv_sha256", "date_range")},
                     ensure_ascii=False))


def daterange(lo: dt.date, hi: dt.date):
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SHFE/GFEX 日行情增量抓取")
    today = dt.date.today()
    ap.add_argument("--start", default=(today - dt.timedelta(days=10)).isoformat())
    ap.add_argument("--end", default=today.isoformat())
    ap.add_argument("--no-resume", action="store_true", help="不理已有数据，重抓区间")
    ap.add_argument("--out", default=os.environ.get(
        "ALT_RAW_DIR", "/srv/share/quantdb/raw/industry_alt/shfe_gfex_quotes"))
    a = ap.parse_args(argv)

    lo = dt.date.fromisoformat(a.start)
    hi = dt.date.fromisoformat(a.end)
    if hi < lo:
        print("FAIL --end 早于 --start", file=sys.stderr)
        return 1

    data = os.path.join(a.out, "data.csv")
    store = load_rows(data)
    have = {(k[0], k[1]) for k in store}
    failed, days, skipped = [], 0, 0
    holidays = 0
    for d in daterange(lo, hi):
        day = d.strftime("%Y%m%d")
        ds = d.isoformat()
        need_shfe = d >= dt.date(2002, 1, 1) and (a.no_resume or (ds, "SHFE") not in have)
        need_gfex = d >= GFEX_LISTING and (a.no_resume or (ds, "GFEX") not in have)
        if not need_shfe and not need_gfex:
            skipped += 1
            continue
        days += 1
        if need_shfe:
            try:
                rows = parse_shfe(day)
                if rows is None:
                    holidays += 1
                else:
                    for r in rows:
                        store[tuple(r[k] for k in KEY)] = r
            except Exception as e:  # noqa: BLE001
                failed.append({"trade_date": ds, "source": "SHFE", "error": str(e)[:200]})
        if need_gfex:
            try:
                rows = parse_gfex(day)
                if not rows:
                    failed.append({"trade_date": ds, "source": "GFEX", "error": "empty data"})
                for r in rows:
                    store[tuple(r[k] for k in KEY)] = r
            except Exception as e:  # noqa: BLE001
                failed.append({"trade_date": ds, "source": "GFEX", "error": str(e)[:200]})
        print(f"{ds} ok" if not failed or failed[-1]["trade_date"] != ds else f"{ds} FAIL")

    keep = {k: v for k, v in store.items() if lo.isoformat() <= k[0] <= hi.isoformat()}
    save(a.out, keep, {"start": a.start, "end": a.end, "days_attempted": days,
                       "days_skipped": skipped, "non_trading": holidays,
                       "failed_count": len(failed)}, failed)
    if not keep:
        print(f"FAIL 区间 {a.start}~{a.end} 一行未取到（失败 {len(failed)} 条，见 failed.json）",
              file=sys.stderr)
        return 1
    if failed:
        print(f"WARN {len(failed)} 个「日×所」失败，见 failed.json（不影响已取到的行）",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
