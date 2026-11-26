#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-537 · 外资研报公开层渠道可抓性实测。

口径（来自 docs/WORK-537/01-hypothesis.md）：
  - 只打公开页：一次 GET 一个入口 URL，不做全站爬。
  - 不绕过登录 / 付费墙 / 验证码 / Cloudflare：403 就记 403。
  - 同域名 >= 1.05s 间隔；单轮总请求受 --max-requests 限制（默认 200）。
  - 遵守 robots.txt：Disallow 的路径不抓正文，只记人工看。

子命令：
  probe    探测全部（或 --only 指定的）渠道，写 probe_log.json + 02-data.csv + foreign_research_channels.csv
  sample   对指定渠道，从已保存的列表页 HTML 里挑一篇文章抓正文，落 raw/foreign_research/<ch>/<date>/<slug>.md + meta.json
  verify   重新抓样例 URL，比对标题是否一致（验收抽查用）

依赖：仅 Python 标准库（本机无 requests/bs4/pandas；30 个静态入口页不需要爬虫框架）。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape
from urllib.parse import urlparse, urljoin

CST = timezone(timedelta(hours=8))
UA_BROWSER = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
# 少数站点（SEC）在 robots/服务条款里要求声明式 UA（含联系邮箱），不是绕过手段，是其公开的使用要求。
UA_DECLARED = "Multica-QuantLab/1.0 (research@quant-lab.local)"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 跟随脚本所在仓库（worktree 安全）
RAW_ROOT = "/srv/share/quantdb/raw/foreign_research"
PROBE_DIR = os.path.join(RAW_ROOT, "_probe")
CSV_DATA = os.path.join(REPO, "docs", "WORK-537", "02-data.csv")
CSV_DELIVER = os.path.join(REPO, "docs", "foreign_research_channels.csv")

_last_by_domain: dict[str, float] = {}
REQ_COUNT = {"n": 0}
MAX_REQUESTS = 200
DOMAIN_GAP = 1.05
GLOBAL_GAP = 0.25
_last_global = 0.0
LEDGER_PATH = os.path.join(PROBE_DIR, "request_ledger.json")
LEDGER: list[dict] = []


def _load_ledger() -> None:
    global LEDGER
    if os.path.exists(LEDGER_PATH):
        try:
            LEDGER = json.load(open(LEDGER_PATH, encoding="utf-8"))
        except Exception:
            LEDGER = []
    REQ_COUNT["n"] = len(LEDGER)


def _save_ledger() -> None:
    """追加式落盘：先读回磁盘上的已有条目再合并，避免多进程/多次运行互相覆盖。

    建目录按 LEDGER_PATH 的真实父目录来（调用方可能把台账指到渠道自有目录，
    见 WORK-623），否则在新落盘根上第一次请求就 FileNotFoundError。
    """
    os.makedirs(os.path.dirname(LEDGER_PATH) or PROBE_DIR, exist_ok=True)
    on_disk = []
    if os.path.exists(LEDGER_PATH):
        try:
            on_disk = json.load(open(LEDGER_PATH, encoding="utf-8"))
        except Exception:
            on_disk = []
    known = {(e.get("url"), e.get("ts")) for e in on_disk}
    merged = list(on_disk)
    for e in LEDGER:
        if (e.get("url"), e.get("ts")) not in known:
            merged.append(e)
    json.dump(merged, open(LEDGER_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- HTTP 基础
class _RedirectTracker(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(f"{code} {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _throttle(url: str) -> None:
    """节流：同域名**上一次请求完成后**至少再等 DOMAIN_GAP 秒（严于「≤1 请求/秒」的起始间隔口径）。"""
    global _last_global
    domain = urlparse(url).netloc.lower()
    now = time.monotonic()
    wait = 0.0
    prev = _last_by_domain.get(domain)
    if prev is not None:
        wait = max(wait, DOMAIN_GAP - (now - prev))
    wait = max(wait, GLOBAL_GAP - (now - _last_global))
    if wait > 0:
        time.sleep(wait)
    _last_global = time.monotonic()


def http_get(url: str, ua: str = UA_BROWSER, timeout: int = 20) -> dict:
    """单次 GET。返回状态/正文/重定向链；不重试。请求全部登记到 ledger（频控与总量可审计）。"""
    _throttle(url)
    if REQ_COUNT["n"] >= MAX_REQUESTS:
        return {"url": url, "ok": False, "status": 0, "error": "budget_exhausted",
                "body": "", "final_url": url, "chain": [], "headers": {}, "bytes": 0,
                "fetched_at": datetime.now(CST).isoformat(timespec="seconds")}
    t0 = time.monotonic()
    tracker = _RedirectTracker()
    opener = urllib.request.build_opener(tracker, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Accept-Encoding": "gzip, identity",
        "Connection": "close",
    })
    out = {"url": url, "ok": False, "status": 0, "error": "", "body": "", "final_url": url,
           "chain": [], "headers": {}, "bytes": 0, "fetched_at": datetime.now(CST).isoformat(timespec="seconds")}
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
            elif "br" in enc:
                try:
                    import brotli
                    raw = brotli.decompress(raw)
                except Exception:
                    pass          # 无 brotli 解码器：正文不可解析，如实记录
            out["status"] = resp.status
            out["headers"] = {k.lower(): v for k, v in resp.headers.items()}
            out["final_url"] = resp.geturl()
            out["body"] = raw.decode("utf-8", errors="replace")
            out["bytes"] = len(raw)
            out["ok"] = True
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["final_url"] = url
        try:
            body = e.read()
            out["body"] = body.decode("utf-8", errors="replace")[:4000]
            out["bytes"] = len(body)
        except Exception:
            pass
        out["error"] = f"HTTPError {e.code} {e.reason}"
    except urllib.error.URLError as e:
        out["error"] = f"URLError {e.reason}"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    out["chain"] = tracker.chain
    REQ_COUNT["n"] += 1
    _last_by_domain[urlparse(url).netloc.lower()] = time.monotonic()   # 记录**完成**时刻，供下次节流
    LEDGER.append({"n": REQ_COUNT["n"], "url": url, "domain": urlparse(url).netloc.lower(),
                   "status": out["status"], "bytes": out.get("bytes", 0),
                   "error": out.get("error", ""), "ua": ua,
                   "elapsed_s": round(time.monotonic() - t0, 2),
                   "ts": datetime.now(CST).isoformat(timespec="seconds")})
    _save_ledger()
    return out


# ---------------------------------------------------------------- robots.txt
def parse_robots(text: str) -> list[tuple[list[str], list[tuple[str, bool]]]]:
    """RFC 9309 简化解析：返回 [(该组的 User-agent 列表, [(pattern, is_allow)])]。

    注意：一个组内可以有多行 User-agent；出现规则后再出现 User-agent 即为新组。
    """
    groups: list[tuple[list[str], list[tuple[str, bool]]]] = []
    uas: list[str] = []
    rules: list[tuple[str, bool]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key == "user-agent":
            if rules:                      # 上一组已有规则 → 收组
                groups.append((uas, rules))
                uas, rules = [], []
            uas.append(val)
        elif key in ("allow", "disallow"):
            if not uas:
                uas = ["*"]
            if key == "allow":
                rules.append((val, True))
            elif val != "":                # "Disallow:" 空值 = 允许全部
                rules.append((val, False))
    if uas:
        groups.append((uas, rules))
    return groups


def select_group(groups, ua: str):
    """取最具体的 UA 组；无匹配则取 '*' 组。"""
    best, best_len = None, -1
    for uas, rules in groups:
        for tok in uas:
            t = tok.strip().lower()
            if t == "*":
                continue
            if ua.lower().startswith(t) or t in ua.lower():
                if len(t) > best_len:
                    best_len, best = len(t), rules
    if best is None:
        for uas, rules in groups:
            if any(t.strip().lower() == "*" for t in uas):
                best = rules
                break
    return best or []


def robots_allows(rules, path: str) -> tuple[bool, str]:
    """最长匹配优先；同长度 Allow 优先。返回 (allowed, 命中规则原文)。"""
    best_len, best_allow, best_rule = -1, True, ""
    for pat, allow in rules:
        if pat == "":
            continue
        if pat.endswith("$"):
            regex = re.escape(pat[:-1]).replace(r"\*", ".*") + "$"
        else:
            regex = re.escape(pat).replace(r"\*", ".*")
        if re.match(regex, path):
            L = len(pat)
            if L > best_len or (L == best_len and allow):
                best_len, best_allow, best_rule = L, allow, ("Allow:" if allow else "Disallow:") + pat
    if best_len < 0:
        return True, "无匹配规则(允许)"
    return best_allow, best_rule


def get_robots(url: str) -> dict:
    """按**目标 URL 的 host** 取 robots.txt（不同 host 的 robots 不通用）。"""
    p = urlparse(url)
    robots_url = "{0.scheme}://{0.netloc}/robots.txt".format(p)
    r = http_get(robots_url)
    if r["status"] == 200 and r.get("body"):
        groups = parse_robots(r["body"])
        rules = select_group(groups, UA_BROWSER)
        return {"robots_url": robots_url, "robots_status": r["status"], "rules": rules,
                "groups": len(groups), "robots_error": ""}
    if r["status"] == 0:
        # 本机出口不可达（DNS/路由层）：连 robots.txt 都取不到，与站点策略无关
        return {"robots_url": robots_url, "robots_status": 0, "rules": None, "groups": 0,
                "robots_error": r.get("error", "") or "连接失败"}
    if r["status"] in (401, 403):
        # robots 不可读：保守按禁止抓取处理，记录实测状态码
        return {"robots_url": robots_url, "robots_status": r["status"], "rules": None, "groups": 0,
                "robots_error": r.get("error", "") or f"HTTP {r['status']}"}
    # 4xx（含 404）= 无 robots 限制；5xx = 不可判定，保守禁抓
    if 400 <= r["status"] < 500:
        return {"robots_url": robots_url, "robots_status": r["status"], "rules": [], "groups": 0,
                "robots_error": ""}
    return {"robots_url": robots_url, "robots_status": r["status"], "rules": None, "groups": 0,
            "robots_error": r.get("error", "") or f"HTTP {r['status']}"}


# ---------------------------------------------------------------- 页面解析
SCRIPT_STYLE = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
TAG = re.compile(r"(?s)<[^>]+>")
TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
H1 = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")
DATE_PATS = [
    (re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})"), "ymd"),
    (re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})", re.I), "mdy"),
    (re.compile(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(20\d{2})", re.I), "dmy"),
]


def clean_text(html: str) -> str:
    t = SCRIPT_STYLE.sub(" ", html)
    t = TAG.sub(" ", t)
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def extract_title(html: str) -> str:
    for rx in (TITLE, H1):
        m = rx.search(html)
        if m:
            t = clean_text(m.group(1))
            if t:
                return t[:160]
    return ""


def extract_date(text: str) -> str:
    for rx, kind in DATE_PATS:
        m = rx.search(text)
        if m:
            if kind == "ymd":
                y, mo, d = m.group(1), m.group(2), m.group(3)
            elif kind == "mdy":
                mo = datetime.strptime(m.group(1)[:3], "%b").month
                d, y = m.group(2), m.group(3)
            else:
                mo = datetime.strptime(m.group(2)[:3], "%b").month
                d, y = m.group(1), m.group(3)
            try:
                return datetime(int(y), int(mo), int(d)).date().isoformat()
            except ValueError:
                continue
    return ""


PAYWALL_PAT = re.compile(
    r"(subscribe to (read|continue)|sign in to (continue|read)|this content is for subscribers|"
    r"订阅后阅读|登录后查看|开通会员|付费阅读|会员专享|阅读全文需)", re.I)
JS_PAT = re.compile(
    r"(enable javascript|javascript is (required|disabled)|you need to enable javascript|"
    r"请开启\s*javascript|需要启用\s*javascript|noscript.{0,40}javascript)", re.I)


JSON_TITLE_KEYS = ("form", "type", "primaryDocDescription", "title", "name", "description", "entityType")
JSON_DATE_KEYS = ("filingDate", "acceptanceDateTime", "reportDate", "date", "publishDate", "updatedAt")


def analyse_json(body: str) -> dict:
    """JSON 接口（如 SEC submissions）：取可解析的标题与日期。"""
    title, date = "", ""
    try:
        data = json.loads(body)
    except Exception:
        return {"title": "", "date": "", "text_len": len(body)}
    if isinstance(data, dict):
        for k in JSON_TITLE_KEYS:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                title = v.strip()
                break
        recs = data.get("filings") or data.get("recent") or {}
        if isinstance(recs, dict):
            for k in JSON_DATE_KEYS:
                v = recs.get(k)
                if isinstance(v, list) and v:
                    first = str(v[0])
                    d = extract_date(first) or (first[:10] if re.match(r"\d{4}-\d{2}-\d{2}", first) else "")
                    if d:
                        date = d
                        break
            if not date and isinstance(recs.get("recent"), dict):   # SEC: filings.recent.*
                sub = recs["recent"]
                for k in JSON_DATE_KEYS:
                    v = sub.get(k)
                    if isinstance(v, list) and v:
                        first = str(v[0])
                        d = extract_date(first) or (first[:10] if re.match(r"\d{4}-\d{2}-\d{2}", first) else "")
                        if d:
                            date = d
                            break
        if not date:
            for k in JSON_DATE_KEYS:
                v = data.get(k)
                if isinstance(v, str):
                    d = extract_date(v)
                    if d:
                        date = d
                        break
    return {"title": title, "date": date, "text_len": len(body)}


MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
BARE_DATE_PAT = re.compile(
    r"(?i)(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"|(\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*")


def all_dates(text: str, ref: datetime | None = None, limit: int = 40000) -> list[str]:
    """抽页面可见文本里的日期。

    - 完整日期（含年份）直接取；
    - **年份缺失**的「Nov 22」「22 November」按参考日推断年份：参考日 = 页内最新完整日期
      （无则用页面元数据日期，再无则用抓取日）；若推断出的日期比参考日晚 45 天以上，
      判为上一年（跨年列表页的常见写法）。
    """
    out = []
    for rx, kind in DATE_PATS:
        for m in rx.finditer(text[:limit]):
            if kind == "ymd":
                y, mo, d = m.group(1), m.group(2), m.group(3)
            elif kind == "mdy":
                mo = datetime.strptime(m.group(1)[:3], "%b").month
                d, y = m.group(2), m.group(3)
            else:
                mo = datetime.strptime(m.group(2)[:3], "%b").month
                d, y = m.group(1), m.group(3)
            try:
                out.append(datetime(int(y), int(mo), int(d)).date().isoformat())
            except ValueError:
                continue
    full = sorted(set(out))
    base = ref
    if base is None and full:
        base = datetime.fromisoformat(full[-1])
    if base is None:
        base = datetime.now()      # naive，与其它 naive datetime 保持可比较
    for m in BARE_DATE_PAT.finditer(text[:limit]):
        if m.group(1):
            mo, d = MONTHS[m.group(1).lower()], int(m.group(2))
        else:
            mo, d = MONTHS[m.group(4).lower()], int(m.group(3))
        for y in (base.year, base.year - 1):
            try:
                cand = datetime(y, mo, d)
            except ValueError:
                continue
            if cand > base + timedelta(days=45):
                continue
            out.append(cand.date().isoformat())
            break
    return sorted(set(out))


def analyse_page(resp: dict) -> dict:
    html = resp.get("body", "")
    ctype = (resp.get("headers", {}).get("content-type") or "")
    if "json" in ctype.lower() or html[:1] in ("{", "["):
        j = analyse_json(html)
        js_flag = False
        if resp["status"] == 200 and j["text_len"] < 200:
            js_flag = True
        return {"title": j["title"], "date": j["date"], "date_first": j["date"], "date_max": j["date"],
                "text_len": j["text_len"], "text_head": html[:600], "js_needed": js_flag,
                "paywall": False, "list_parseable": bool(j["title"] and j["date"]),
                "date_source": "接口字段" if j["date"] else "未取到", "meta_date": ""}
    text = clean_text(html)
    title = extract_title(html)
    meta_date = extract_date(html)             # HTML 元数据/服务端时间戳，仅作参考
    ref = None                                  # 年份推断的参考日 = 页内最新完整日期（无则用抓取日）
    ds = all_dates(text, ref=ref)              # 只认**列表可见文本**里的日期
    date_first = ds[0] if ds else ""
    # 「页内最新」排除未来事件/预测日期（列表页常含未来年份的展望），只取 ≤ 今天+45 天的最大日期
    cutoff = datetime.now() + timedelta(days=45)
    ds_ok = [d for d in ds if datetime.fromisoformat(d) <= cutoff]
    date_max = ds_ok[-1] if ds_ok else date_first
    # 需 JS 判定：可见文本过短 = 正文没渲染；正文已很长时，页面上出现的 JS 提示语
    # （cookie 横幅、noscript 模板等）不足以判定「需 JS」。
    js_flag = len(text) < 800 or (bool(JS_PAT.search(html[:20000])) and len(text) < 2000)
    paywall = bool(PAYWALL_PAT.search(text[:4000]))
    return {"title": title, "date": date_max or date_first or meta_date,
            "date_first": date_first, "date_max": date_max,
            "date_source": ("列表可见文本" if ds else ("HTML元数据(页面自身标注)" if meta_date else "未取到")),
            "meta_date": meta_date,
            "text_len": len(text), "text_head": text[:600], "js_needed": js_flag, "paywall": paywall,
            "list_parseable": bool(title and ds)}


RATING_PAT = re.compile(
    r"(price target|target price|our \w+ rating|overweight|underweight|equalweight|"
    r"outperform|underperform|buy rating|sell rating|"
    r"目标价|评级|买入评级|增持|减持|中性评级)", re.I)


# ---------------------------------------------------------------- 渠道定义
# type: 投行官网 / 财经媒体 / 监管披露 / 聚合平台 / 社区
# entry: 主入口 URL；fallbacks: 主入口失败时的备选入口（每个各算一次请求）
CHANNELS = [
    # ---------------- 投行官网 13 ----------------
    dict(id="gs", name="Goldman Sachs Insights", org="Goldman Sachs", type="投行官网",
         entry="https://www.goldmansachs.com/insights", fallbacks=[],
         form="文章/摘要/视频", freq="每周 1-2 篇（页面自述）", coverage="全球/美国",
         note="大管家 2026-11-23 基线：HTTP 200"),
    dict(id="ms", name="Morgan Stanley Ideas", org="Morgan Stanley", type="投行官网",
         entry="https://www.morganstanley.com/ideas", fallbacks=[],
         form="文章/摘要/视频", freq="每周 2-3 篇", coverage="全球/美国",
         note="大管家基线：HTTP 301"),
    dict(id="jpm", name="J.P. Morgan Insights", org="J.P. Morgan", type="投行官网",
         entry="https://www.jpmorgan.com/insights", fallbacks=["https://www.jpmorgan.com/insights/research"],
         form="文章/摘要/视频/播客", freq="每周 2-4 篇", coverage="全球/美国",
         note="大管家基线：HTTP 200"),
    dict(id="ubs", name="UBS Insights", org="UBS", type="投行官网",
         entry="https://www.ubs.com/global/en/insights.html",
         fallbacks=["https://www.ubs.com/global/en/insights/"],
         form="文章/摘要/视频", freq="每周 1-3 篇", coverage="全球/欧洲/亚太",
         note="大管家基线：HTTP 403（Akamai 边缘拒绝，非登录墙）"),
    dict(id="citi", name="Citi Insights", org="Citi", type="投行官网",
         entry="https://www.citigroup.com/global/insights",
         fallbacks=["https://www.citigroup.com/global/insights/citigps"],
         form="文章/摘要/视频", freq="每周 1-2 篇", coverage="全球/美国",
         note="大管家基线：HTTP 301"),
    dict(id="bofa", name="BofA Insights", org="Bank of America", type="投行官网",
         entry="https://www.bankofamerica.com/insights/",
         fallbacks=["https://www.bankofamerica.com/insights.html",
                    "https://institute.bankofamerica.com/",
                    "https://newsroom.bankofamerica.com/"],
         form="文章/摘要", freq="每周 1-2 篇", coverage="美国/全球",
         note="大管家基线：其尝试的 URL 404，需重找入口"),
    dict(id="barclays", name="Barclays Insights", org="Barclays", type="投行官网",
         entry="https://www.home.barclays/insights/",
         fallbacks=["https://www.home.barclays/", "https://www.home.barclays/investment-bank/insights/"],
         form="文章/摘要/视频", freq="每周 1-2 篇", coverage="全球/欧洲/亚太",
         note="需自找入口"),
    dict(id="hsbc", name="HSBC Insights（Global Banking and Markets）", org="HSBC", type="投行官网",
         entry="https://www.gbm.hsbc.com/en-gb/insights",
         fallbacks=["https://www.business.hsbc.com/en-gb/insights", "https://www.hsbc.com/insights"],
         form="文章/摘要/视频", freq="每周 1-3 篇", coverage="全球/欧洲/亚太",
         note="www.hsbc.com/insights 已 404（2026-11-23 实测）；现用 GBM 洞察栏目，含 Global Research 条目"),
    dict(id="nomura", name="Nomura Insights", org="Nomura", type="投行官网",
         entry="https://www.nomura.com/insights/",
         fallbacks=["https://www.nomuraholdings.com/", "https://www.nomura.com/"],
         form="文章/摘要/视频", freq="每周 1-2 篇", coverage="亚太/日本/全球",
         note="大管家基线：nomuraholdings.com HTTP 403"),
    dict(id="daiwa", name="Daiwa Insights", org="Daiwa", type="投行官网",
         entry="https://www.daiwa.com/insights/",
         fallbacks=["https://www.daiwa.com/", "https://www.daiwa.com/insights/index.html"],
         form="文章/摘要", freq="不定期", coverage="亚太/日本",
         note="需自找入口"),
    dict(id="macq", name="Macquarie Insights", org="Macquarie", type="投行官网",
         entry="https://www.macquarie.com/insights/",
         fallbacks=["https://www.macquarie.com/au/en/insights.html", "https://www.macquarie.com/"],
         form="文章/摘要/视频", freq="每周 1-2 篇", coverage="全球/亚太",
         note="需自找入口"),
    dict(id="db", name="dbInsights（Deutsche Bank Research）", org="Deutsche Bank", type="投行官网",
         entry="https://www.dbresearch.com/PROD/IE-PROD/RI_DBI.alias",
         fallbacks=["https://www.dbresearch.de/PROD/IE-PROD/RI_DBI.alias",
                    "https://dbresearch.com/PROD/IE-PROD/dbInSights__Research_Institute/RI_DBI.alias"],
         form="文章/摘要/视频", freq="每周 2-4 篇", coverage="全球/欧洲",
         note="db.com/insights、db.com/news/insights 均 404（2026-11-23 实测）；"
              "公开研究发布平台为 dbInsights（dbresearch.com / dbresearch.de），条目自带日期、主题、分析师"),
    dict(id="bernstein", name="Bernstein Research", org="Bernstein", type="投行官网",
         entry="https://www.bernsteinresearch.com/",
         fallbacks=["https://www.wallstreet.com/"],
         form="文章/摘要", freq="每周 1-2 篇", coverage="美国/全球",
         note="需自找入口"),

    # ---------------- 财经媒体 6 ----------------
    dict(id="reuters", name="Reuters Markets / Analysis", org="Reuters", type="财经媒体",
         entry="https://www.reuters.com/markets/",
         fallbacks=["https://www.reuters.com/business/finance/"],
         form="文章/摘要", freq="日更", coverage="全球",
         note="公开页；本机出口不可达（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）；文章多受登录/自动化防护限制"),
    dict(id="bloomberg", name="Bloomberg 公开页", org="Bloomberg", type="财经媒体",
         entry="https://www.bloomberg.com/markets",
         fallbacks=["https://www.bloomberg.com/"],
         form="文章/摘要", freq="日更", coverage="全球",
         note="公开页；本机出口不可达（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）；Bloomberg Terminal 需订阅"),
    dict(id="ft", name="FT 公开页", org="Financial Times", type="财经媒体",
         entry="https://www.ft.com/",
         fallbacks=["https://www.ft.com/markets"],
         form="文章/摘要", freq="日更", coverage="全球/欧洲",
         note="公开页；本机出口不可达（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）；正文多在付费墙后"),
    dict(id="wsj", name="WSJ 公开页", org="Wall Street Journal", type="财经媒体",
         entry="https://www.wsj.com/",
         fallbacks=["https://www.wsj.com/market-data"],
         form="文章/摘要", freq="日更", coverage="美国/全球",
         note="公开页；本机出口不可达（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）；正文多在付费墙后"),
    dict(id="cnbc", name="CNBC Investing / Research", org="CNBC", type="财经媒体",
         entry="https://www.cnbc.com/investing/",
         fallbacks=["https://www.cnbc.com/research/", "https://www.cnbc.com/"],
         form="文章/摘要/视频", freq="日更", coverage="美国/全球",
         note="需自找入口"),
    dict(id="barrons", name="Barron's", org="Dow Jones / Barron's", type="财经媒体",
         entry="https://www.barrons.com/",
         fallbacks=["https://www.barrons.com/market-data"],
         form="文章/摘要", freq="日更", coverage="美国/全球",
         note="公开页；正文多在付费墙后。round-1（robots 判定生效前）实发 2 次："
              "https://www.barrons.com/ → HTTP 401、https://www.barrons.com/market-data → HTTP 200"
              "（该次正文已取到，但未进入任何分析、未入样例、未入结论）；round-2 起 robots 优先，未再发正文请求"),

    # ---------------- 监管 / 披露 3 ----------------
    dict(id="sec", name="SEC EDGAR（公开机器接口）", org="U.S. SEC", type="监管披露",
         entry="https://data.sec.gov/submissions/CIK0000320193.json",
         fallbacks=["https://www.sec.gov/edgar/searchedgar/companysearch.html"],
         form="JSON（申报标题/日期）", freq="实时", coverage="美国",
         note="人类入口 https://www.sec.gov/edgar/searchedgar/companysearch.html；"
              "data.sec.gov 为 SEC 官方公开接口，要求声明式 UA（含联系邮箱）"),
    dict(id="finra", name="FINRA 公开披露", org="FINRA", type="监管披露",
         entry="https://www.finra.org/finra-data",
         fallbacks=["https://www.finra.org/media-center/newsroom", "https://www.finra.org/rules-guidance"],
         form="文章/公告", freq="日更", coverage="美国",
         note="自律组织公开信息；含分析师研究相关的公开指引。robots.txt 不可读（HTTP 403）；"
              "round-1（robots 判定生效前）实发 3 次均 HTTP 403（finra-data / media-center/newsroom / "
              "rules-guidance）；round-2 起 robots 优先，未再发正文请求"),
    dict(id="exch", name="交易所公开披露", org="NYSE / 交易所", type="监管披露",
         entry="https://www.nyse.com/listed-company-information",
         fallbacks=["https://www.nyse.com/quote/XNYS", "https://www.hkexnews.hk/"],
         form="公告/列表", freq="实时", coverage="美国/香港",
         note="需自找入口；港交所披露易为机器可读备选"),

    # ---------------- 聚合与社区 7 ----------------
    dict(id="hibor", name="慧博投研", org="慧博投研", type="聚合平台",
         entry="https://www.hibor.com.cn/",
         fallbacks=["https://www.hibor.com.cn/yanbao/", "https://www.hibor.com.cn/list_yb.html"],
         form="研报摘要/PDF（部分需登录）", freq="日更", coverage="中国/全球中文",
         note="聚合平台；正文/PDF 多需登录或付费"),
    dict(id="fxbaogao", name="发现报告", org="发现报告", type="聚合平台",
         entry="https://www.fxbaogao.com/",
         fallbacks=["https://www.fxbaogao.com/"],
         form="研报摘要", freq="日更", coverage="中国/全球中文",
         note="聚合平台"),
    # WORK-623：同一站点的正文渠道。默认 probe 不跑（cmd_probe 跳过 *_vip，保持 WORK-537
    # 的既有探测结果不被改写）；正文采集器见 scripts/fxbaogao_fulltext.py。
    dict(id="fxbaogao_vip", name="发现报告（高级 VIP 正文）", org="发现报告", type="聚合平台",
         entry="https://www.fxbaogao.com/",
         fallbacks=["https://www.fxbaogao.com/"],
         form="研报正文/PDF（需登录；robots 禁 /pdf）", freq="日更", coverage="中国/全球中文",
         note="正文仅由用户登录浏览器会话或手动点击产生；HTTP 客户端不请求 /pdf"),
    dict(id="robo", name="萝卜投研", org="Datayes 萝卜投研", type="聚合平台",
         entry="https://robo.datayes.com/",
         fallbacks=["https://robo.datayes.com/vip/index", "https://www.datayes.com/"],
         form="研报摘要/数据", freq="日更", coverage="中国",
         note="部分内容需登录/付费"),
    dict(id="emreport", name="东方财富研报中心", org="东方财富", type="聚合平台",
         entry="https://data.eastmoney.com/report/",
         fallbacks=["https://data.eastmoney.com/report/strategyreport.jshtml"],
         form="研报摘要", freq="日更", coverage="中国",
         note="聚合平台；页面有 JSON 接口 reportapi.eastmoney.com"),
    dict(id="xueqiu", name="雪球", org="雪球", type="社区",
         entry="https://xueqiu.com/",
         fallbacks=["https://xueqiu.com/today"],
         form="帖子/摘要", freq="日更", coverage="中国",
         note="UGC 社区；非投行原文，多为二次转述"),
    dict(id="gelonghui", name="格隆汇", org="格隆汇", type="社区",
         entry="https://www.gelonghui.com/",
         fallbacks=["https://www.gelonghui.com/news"],
         form="快讯/文章", freq="日更", coverage="中国/港美股",
         note="快讯为主；多为二次转述"),
    dict(id="cninfo", name="巨潮资讯", org="巨潮资讯网（深交所）", type="监管披露",
         entry="http://www.cninfo.com.cn/new/index",
         fallbacks=["https://www.cninfo.com.cn/new/index", "https://www.cninfo.com.cn/"],
         form="公告列表", freq="实时", coverage="中国",
         note="法定信息披露平台；非投行研究内容，但为中文公开层原文基座"),
]


# ---------------------------------------------------------------- 分类逻辑
LOGIN_URL_PAT = re.compile(r"(login|logon|signin|sign-in|/account/)", re.I)


def classify(ch: dict, resp: dict, an: dict, rob: dict, rob_hit: str) -> str:
    if rob.get("rules") is None:
        if rob.get("robots_status") == 0:
            return "本机出口不可达"
        return f"robots不可读(HTTP {rob.get('robots_status')})"
    if not rob.get("allowed", True):
        return "robots禁"
    st = resp["status"]
    if st == 403:
        return "403"
    if st == 401:
        return "需登录"
    if st == 404:
        return "404入口失效"
    if st == 0:
        return "超时/连接失败"
    if st >= 500:
        return f"{st}服务端错误"
    if st >= 300 and st < 400:
        return "重定向未落地"
    if LOGIN_URL_PAT.search(urlparse(resp.get("final_url", "")).path or ""):
        return "需登录(落地登录页)"
    if st == 200:
        if an.get("paywall"):
            return "需登录/付费墙"
        # issue 的验收口径：列表页必须能解析出「标题 + 日期」各 1 条；
        # 取不到日期 = 文章列表不在 HTML 里（JS 渲染），即使导航文本很长也不能算直连。
        if not an.get("list_parseable"):
            return "需JS(列表未渲染)"
        if an.get("js_needed"):
            return "需JS"
        return "直连"
    return f"HTTP{st}"


RUN1_LOG = os.path.join(PROBE_DIR, "probe_log_run1_robots_bug.json")


def round1_facts() -> dict:
    """读取 round-1（robots 判定生效前）的实际发出记录：{channel_id: [(status, url), ...]}。

    只收录**带真实 HTTP 状态码**的尝试（200/401/403 等），status=0（连接失败）不收，
    因为那是网络层失败、没有状态码可写。
    """
    out: dict[str, list] = {}
    if not os.path.exists(RUN1_LOG):
        return out
    try:
        log = json.load(open(RUN1_LOG, encoding="utf-8"))
    except Exception:
        return out
    for rec in log:
        for t in rec.get("tried", []):
            st = t.get("status")
            if not isinstance(st, int) or st <= 0:
                continue
            out.setdefault(rec["id"], []).append((st, t.get("url", "")))
    return out


def evidence_text(resp: dict, an: dict, rob: dict, cls: str, r1: list | None = None) -> str:
    st = resp["status"]
    bits = []
    if st == 0:
        if rob.get("robots_status") == 0:
            # robots.txt 本身就取不到 = 网络层失败，与站点策略无关
            bits.append("无 HTTP 响应（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）")
        else:
            bits.append("无 HTTP 响应（round-2 未发出正文请求：robots 禁 / robots 不可读）")
    else:
        bits.append(f"HTTP {st}")
    if resp.get("chain"):
        bits.append("跳转:" + " -> ".join(resp["chain"])[:120])
    if resp.get("headers", {}).get("content-type"):
        bits.append("CT=" + resp["headers"]["content-type"][:40])
    if an.get("title"):
        bits.append(f'标题="{an["title"][:70]}"')
    else:
        bits.append("标题=未取到")
    if an.get("date_first") and an.get("date_max") and an["date_first"] != an["date_max"]:
        bits.append(f'日期={an["date_first"]}(首个)/{an["date_max"]}(页内最新)')
    elif an.get("date"):
        bits.append(f'日期={an["date"]}')
    else:
        bits.append("日期=未取到")
    bits.append(f'日期来源={an.get("date_source", "未取到")}')
    bits.append(f'可解析标题+日期={"是" if an.get("list_parseable") else "否"}')
    bits.append(f'需JS={"是" if an.get("js_needed") else "否"}')
    bits.append(f'付费墙标记={"是" if an.get("paywall") else "未检出"}')
    bits.append(f'robots={rob.get("robots_status")}')
    if not an.get("title") and resp.get("body") and resp["status"] != 200:
        snippet = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", resp["body"])).strip()[:80]
        bits.append(f'失败原文="{snippet}"')
    elif resp.get("error"):
        bits.append(f'失败原文="{resp["error"][:80]}"')
    if r1:
        items = " ; ".join(f"HTTP {st} {u}" for st, u in r1)
        bits.append(f"round1 实发: {items}"
                    f"（robots 判定生效前的尝试；抓取内容未进入任何分析、未入样例、未入结论）")
    return " | ".join(bits)


# ---------------------------------------------------------------- probe
def probe_channel(ch: dict, use_declared_ua: bool = False) -> dict:
    """robots 优先：先查该 host 的 robots.txt，被 Disallow 的路径**根本不发请求**。"""
    candidates = [ch["entry"]] + list(ch.get("fallbacks", []))
    tried: list[dict] = []
    rob_by_host: dict[str, dict] = {}
    resp = an = rob = None
    rob_hit = ""
    for url in candidates:
        host = urlparse(url).netloc.lower()
        if host not in rob_by_host:
            rob_by_host[host] = get_robots(url)
        r_rob = rob_by_host[host]
        path = urlparse(url).path or "/"
        if r_rob.get("rules") is None:
            allowed, hit = False, (f"robots不可读(HTTP {r_rob.get('robots_status')})"
                                  if r_rob.get("robots_status") else
                                  f"出口不可达: {r_rob.get('robots_error', '')}")
        else:
            allowed, hit = robots_allows(r_rob["rules"], path)
        if not allowed:
            tried.append({"url": url, "status": "robots禁", "error": hit, "final_url": url})
            continue
        r = http_get(url, ua=UA_DECLARED if use_declared_ua else UA_BROWSER)
        tried.append({"url": url, "status": r["status"], "error": r.get("error", ""),
                      "final_url": r.get("final_url", url)})
        if resp is None:
            resp, an, rob, rob_hit = r, analyse_page(r), r_rob, hit
        if r["status"] == 200:
            resp, an, rob, rob_hit = r, analyse_page(r), r_rob, hit
            break
    if resp is None:                       # 全部候选入口都未发出正文请求
        _e = rob_by_host.get(urlparse(ch["entry"]).netloc.lower(), {})
        _st = _e.get("robots_status")
        why = (f"robots.txt 不可读(HTTP {_st})" if _st else
               f"本机出口不可达（连 robots.txt 都取不到，非站点返回码）：{_e.get('robots_error','')}")
        resp = {"status": 0, "body": "", "final_url": ch["entry"], "chain": [], "headers": {},
                "bytes": 0, "error": f"未发正文请求：{why}",
                "fetched_at": datetime.now(CST).isoformat(timespec="seconds")}
        an = analyse_page(resp)
        rob = _e or {"robots_status": 0, "rules": [], "robots_url": ""}
        rob_hit = rob.get("robots_error") or "robots不可读"
        rob["allowed"] = False
    else:
        # 最终落地 host 可能与入口 host 不同（跨域跳转）→ 用落地 host 的 robots 判定最终路径
        final_host = urlparse(resp.get("final_url", "")).netloc.lower()
        if final_host and final_host != urlparse(resp.get("final_url", "")).netloc.lower():
            pass
        entry_host = urlparse(resp.get("final_url", "")).netloc.lower()
        if final_host not in rob_by_host:
            rob_by_host[final_host] = get_robots(resp["final_url"])
        rob = rob_by_host[final_host]
        fpath = urlparse(resp["final_url"]).path or "/"
        if rob.get("rules") is None:
            rob["allowed"] = False
            rob_hit = "robots不可读"
        else:
            allowed, rob_hit = robots_allows(rob["rules"], fpath)
            rob["allowed"] = allowed
    # 列表页 HTML 落盘（供 sample 阶段挑文章链接，避免重复请求）
    os.makedirs(PROBE_DIR, exist_ok=True)
    body_path = os.path.join(PROBE_DIR, f"{ch['id']}_listing.html")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(resp.get("body", ""))
    cls = classify(ch, resp, an, rob, rob_hit)
    # 评级/目标价判定：扫**整页**可见文本（不是只看开头 600 字）
    full_text = clean_text(resp.get("body", "")) if resp.get("body") else ""
    if full_text:
        hits = sorted(set(m.group(0).lower() for m in RATING_PAT.finditer(full_text)))
        has_rating = ("部分（列表页出现评级/目标价字样：%s）" % "、".join(hits[:6])) if hits \
            else "无（列表页全文未出现评级/目标价字样）"
        rating_hits = len(hits)
    else:
        has_rating = "未取到原文"
        rating_hits = 0
    return {
        "id": ch["id"], "channel": ch["name"], "org": ch["org"], "type": ch["type"],
        "entry_url": ch["entry"], "final_url": resp.get("final_url", ch["entry"]),
        "tried": tried, "status": resp["status"], "bytes": resp.get("bytes", 0),
        "content_type": resp.get("headers", {}).get("content-type", ""),
        "title": an.get("title", ""), "date": an.get("date", ""),
        "date_first": an.get("date_first", ""), "date_max": an.get("date_max", ""),
        "text_len": an.get("text_len", 0), "js_needed": an.get("js_needed", False),
        "paywall": an.get("paywall", False), "crawlability": cls,
        "robots_status": rob.get("robots_status"), "robots_url": rob.get("robots_url"),
        "robots_rule_hit": rob_hit, "robots_allowed": rob.get("allowed", True),
        "evidence": evidence_text(resp, an, rob, cls),
        "has_rating_tp": has_rating, "rating_hits": rating_hits,
        "form": ch["form"], "freq": ch["freq"], "coverage": ch["coverage"], "note": ch["note"],
        "fetched_at": resp.get("fetched_at"), "ua": UA_DECLARED if use_declared_ua else UA_BROWSER,
        "body_path": body_path,
    }


def collect_samples() -> dict:
    """扫描 raw/foreign_research 下的正文样例（meta.json），供交付 CSV 引用。"""
    out = {}
    for root, _dirs, files in os.walk(RAW_ROOT):
        if "_rejected" in root or "_probe" in root:
            continue
        for fn in files:
            if fn != "meta.json":
                continue
            try:
                m = json.load(open(os.path.join(root, fn), encoding="utf-8"))
            except Exception:
                continue
            out.setdefault(m.get("channel_id"), []).append(m)
    return out


def recommendation(r: dict, samples: dict) -> str:
    c = r["crawlability"]
    if c == "直连":
        return "采"
    if c in ("需JS", "需JS(列表未渲染)", "需登录(落地登录页)", "需登录/付费墙", "本机出口不可达", "403"):
        return "观察"      # 公开层有内容，但本机机器采不到（换出口/人工看）
    return "不采"          # robots禁 / robots不可读：合规侧不应抓


def write_csvs(results: list[dict]) -> None:
    samples = collect_samples()
    os.makedirs(os.path.dirname(CSV_DATA), exist_ok=True)
    data_header = ["渠道", "归属机构/媒体", "类型", "入口URL", "最终URL", "HTTP状态", "重定向链",
                   "Content-Type", "字节数", "页面标题", "页面日期(首个)", "页面日期(页内最新)", "可见文本长度", "是否需JS",
                   "是否检出付费墙标记", "robots状态", "robots命中规则", "可抓性", "实测证据",
                   "是否含评级/目标价", "内容形态", "更新频率", "覆盖范围", "抓取时间", "UA"]
    with open(CSV_DATA, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(data_header)
        for r in results:
            w.writerow([r["channel"], r["org"], r["type"], r["entry_url"], r["final_url"], r["status"],
                        " | ".join(r["tried"] and [f'{t["status"]}:{t["url"]}' for t in r["tried"]] or []),
                        r["content_type"], r["bytes"], r["title"], r.get("date_first", r["date"]),
                        r.get("date_max", r["date"]), r["text_len"],
                        "是" if r["js_needed"] else "否", "是" if r["paywall"] else "未检出",
                        r["robots_status"], r["robots_rule_hit"], r["crawlability"], r["evidence"],
                        r["has_rating_tp"], r["form"], r["freq"], r["coverage"], r["fetched_at"], r["ua"]])
    deliv_header = ["渠道", "归属机构/媒体", "类型(投行官网/财经媒体/监管披露/聚合平台/社区)", "入口URL",
                     "内容形态(文章/摘要/PDF/视频)", "可抓性(直连/需JS/403/需登录/robots禁)",
                     "实测证据(状态码+抓到的标题或失败原文)", "更新频率", "覆盖范围(全球/美国/欧洲/亚太)",
                     "是否含评级/目标价", "建议(采/观察/不采)", "备注"]
    with open(CSV_DELIVER, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(deliv_header)
        for r in results:
            note = r["note"]
            sm = samples.get(r["id"])
            if sm:
                m = max(sm, key=lambda x: x.get("chars", 0))
                note += f"；正文样例：{m['title'][:60]}（{m['page_date']}，{m['chars']}字符，sha256 {m['sha256'][:12]}）"
                if r["crawlability"].startswith("需JS"):
                    note += "；注意：入口列表需 JS 渲染，但文章详情页可直接抓（样例即取自详情页）"
            elif r["crawlability"] == "直连":
                note += "；入口页直连，本轮未取到可用的文章正文样例（候选链接见 raw/foreign_research/_rejected/）"
            if r["id"] in ("gelonghui", "cninfo", "emreport"):
                note += ("；评级/目标价字样来自转载或公告原文（中资券商口径的转述），"
                         "不是外资投行给出的评级/目标价")
            w.writerow([r["channel"], r["org"], r["type"], r["entry_url"], r["form"],
                        r["crawlability"], r["evidence"],
                        r.get("freq_observed") or "未实测（单次快照不足以判断发布节奏）",
                        r["coverage"],
                        r["has_rating_tp"], recommendation(r, samples), note])


def cmd_probe(args) -> None:
    # WORK-623 的 _vip 渠道单独跑，避免把「首页可抓性」混进 WORK-537 的渠道表；
    # 显式 --only fxbaogao_vip 仍可探测。
    chans = [c for c in CHANNELS if not c["id"].endswith("_vip")]
    if args.only:
        want = set(args.only.split(","))
        chans = [c for c in CHANNELS if c["id"] in want]
    results = []
    log_path = os.path.join(PROBE_DIR, "probe_log.json")
    os.makedirs(PROBE_DIR, exist_ok=True)
    if os.path.exists(log_path):
        try:
            results = json.load(open(log_path, encoding="utf-8"))
        except Exception:
            results = []
    have = {r["id"] for r in results}
    for c in chans:
        if c["id"] in have and not args.force:
            print(f"[skip] {c['id']} 已有结果", flush=True)
            continue
        if REQ_COUNT["n"] >= MAX_REQUESTS:
            print("[budget] 请求预算耗尽，停止", flush=True)
            break
        print(f"[probe] {c['id']} {c['entry']}", flush=True)
        r = probe_channel(c)
        results = [x for x in results if x["id"] != c["id"]] + [r]
        json.dump(results, open(log_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"   -> {r['status']} {r['crawlability']} | {r['evidence'][:150]}", flush=True)
    order = {c["id"]: i for i, c in enumerate(CHANNELS)}
    results.sort(key=lambda r: order.get(r["id"], 999))
    write_csvs(results)
    print(f"[done] 渠道 {len(results)} 个，请求 {REQ_COUNT['n']} 次 → {CSV_DELIVER}", flush=True)


# ---------------------------------------------------------------- sample
LINK_PAT = re.compile(r'href=["\']([^"\']+)["\']', re.I)
ANCHOR_PAT = re.compile(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>')
KEYWORDS = re.compile(r"(insight|idea|research|article|news|post|view|detail|report|story|content|perspective|PROD0|xhtml|alias)", re.I)


def pick_articles(ch_id: str, listing_html: str, limit: int = 3) -> list[str]:
    """从列表页挑候选文章链接：带锚文本的链接优先打分，取前 limit 个去重候选。"""
    base = None
    for r in json.load(open(os.path.join(PROBE_DIR, "probe_log.json"), encoding="utf-8")):
        if r["id"] == ch_id:
            base = r["final_url"]
    if not base:
        return []
    scored: list[tuple[float, str]] = []
    for href, anchor in ANCHOR_PAT.findall(listing_html):
        u = urljoin(base, href)
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|svg|zip|ico|css|js|pdf)$", p.path, re.I):
            continue
        depth = len([x for x in p.path.split("/") if x])
        if depth < 2:
            continue
        atext = clean_text(anchor)
        score = 0.0
        if KEYWORDS.search(p.path):
            score += 2.0
        if len(atext) >= 30:
            score += 1.5
        if len(atext) >= 60:
            score += 0.5
        if depth >= 3:
            score += 0.5
        if score <= 0:
            continue
        scored.append((score, u))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _s, u in scored:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


_ROBOTS_CACHE: dict[str, dict] = {}


def get_robots_cached(url: str) -> dict:
    """按 host 缓存 robots，避免同一 host 重复请求。"""
    host = urlparse(url).netloc.lower()
    if host not in _ROBOTS_CACHE:
        _ROBOTS_CACHE[host] = get_robots(url)
    return _ROBOTS_CACHE[host]


def cmd_sample(args) -> None:
    log = json.load(open(os.path.join(PROBE_DIR, "probe_log.json"), encoding="utf-8"))
    byid = {r["id"]: r for r in log}
    max_tries = getattr(args, "tries", 1)
    for cid in args.channels.split(","):
        cid = cid.strip()
        r = byid.get(cid)
        if r is None:
            print(f"[sample] {cid} 无探测结果，跳过", flush=True)
            continue
        listing = open(r["body_path"], encoding="utf-8").read()
        urls = pick_articles(cid, listing, max_tries)
        if not urls:
            print(f"[sample] {cid} 未从列表页解析出文章链接", flush=True)
            continue
        done = False
        for url in urls:
            # 抓正文前再查一次 robots：被 Disallow 的路径直接不请求
            rob = get_robots_cached(url)
            path = urlparse(url).path or "/"
            if rob.get("rules") is None:
                print(f"[sample] {cid} robots 不可读({rob.get('robots_status')})，跳过 {url}", flush=True)
                continue
            allowed, hit = robots_allows(rob["rules"], path)
            if not allowed:
                print(f"[sample] {cid} robots 禁止({hit})，不请求 {url}", flush=True)
                continue
            resp = http_get(url)
            an = analyse_page(resp)
            text = clean_text(resp.get("body", ""))
            if resp["status"] != 200 or len(text) < 200:
                print(f"[sample] {cid} 候选不可用 HTTP {resp['status']} 字符 {len(text)} -> {url}", flush=True)
                continue
            date = an.get("date") or r.get("date") or datetime.now(CST).date().isoformat()
            slug = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", unescape(an.get("title") or r.get("title") or "sample"))[:60].strip("-") or "sample"
            d = os.path.join(RAW_ROOT, cid, date, slug)
            os.makedirs(d, exist_ok=True)
            md_path = os.path.join(d, f"{slug}.md")
            # 不做任何填充：正文短就是短，如实记录字符数（验收门 chars>500 靠真实内容达成）
            md = (f"# {an.get('title') or r.get('title') or slug}\n\n"
                  f"- 来源：{r['channel']}（{r['org']}）\n"
                  f"- URL：{resp.get('final_url', url)}\n"
                  f"- 页面日期：{date}\n"
                  f"- 抓取时间：{resp.get('fetched_at')}\n"
                  f"- HTTP 状态：{resp['status']}\n"
                  f"- 正文可见字符数：{len(text)}\n\n---\n\n{text}\n")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md)
            sha = hashlib.sha256(md.encode("utf-8")).hexdigest()
            meta = {
                "channel_id": cid, "channel": r["channel"], "org": r["org"], "type": r["type"],
                "title": an.get("title") or r.get("title"), "page_date": date,
                "url": resp.get("final_url", url), "entry_url": r["entry_url"],
                "fetched_at": resp.get("fetched_at"), "http_status": resp["status"],
                "content_type": resp.get("headers", {}).get("content-type", ""),
                "sha256": sha, "chars": len(md), "body_chars": len(text),
                "js_needed": an.get("js_needed"), "paywall_marker": an.get("paywall"),
                "source_robots": r.get("robots_status"), "robots_rule": hit,
                "raw_body_path": r.get("body_path"),
            }
            with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            print(f"[sample] {cid} -> {md_path} ({len(md)} chars, 正文 {len(text)}, sha {sha[:12]})", flush=True)
            done = True
            break
        if not done:
            print(f"[sample] {cid} 未取到可用正文（尝试 {len(urls)} 个候选）", flush=True)


def cmd_verify(args) -> None:
    """重新抓样例 URL，核对标题一致（验收抽查）。"""
    for meta_path in args.meta.split(","):
        meta_path = meta_path.strip()
        if not meta_path:
            continue
        m = json.load(open(meta_path, encoding="utf-8"))
        r = http_get(m["url"])
        an = analyse_page(r)
        got_title = an.get("title", "")
        ok_title = bool(got_title) and (got_title[:40] in m["title"] or m["title"][:40] in got_title or
                                        got_title == m["title"])
        print(f"[verify] {m['url']}\n   HTTP {r['status']} | 落盘标题={m['title'][:60]} | 重取标题={got_title[:60]} | 标题一致={ok_title}", flush=True)


def cmd_reclass(args) -> None:
    """离线重判可抓性：0 请求，用已保存的列表页快照重跑 analyse_page + classify。

    用于「分类逻辑修正后不重新打站」：判据变化时，对同一份快照重放判定，结果可复现。
    `--keep-log` 只重生成 CSV，不回写 probe_log.json（原始快照保持逐字节不变）。
    """
    log = json.load(open(os.path.join(PROBE_DIR, "probe_log.json"), encoding="utf-8"))
    r1facts = round1_facts()
    notes = {c["id"]: c["note"] for c in CHANNELS}
    changed = []
    for r in log:
        body = ""
        if os.path.exists(r.get("body_path", "")):
            body = open(r["body_path"], encoding="utf-8").read()
        resp = {"status": r["status"], "body": body, "final_url": r["final_url"],
                "headers": {"content-type": r.get("content_type", "")}, "bytes": r.get("bytes", 0),
                "error": "", "fetched_at": r.get("fetched_at"),
                "chain": r.get("chain", [])}
        an = analyse_page(resp)
        if r.get("robots_status") in (0, 401, 403):
            rob = {"rules": None, "robots_status": r["robots_status"], "allowed": False,
                   "robots_url": r.get("robots_url"), "robots_error": ""}
        else:
            rob = {"rules": [], "robots_status": r.get("robots_status"), "allowed": r.get("robots_allowed", True),
                   "robots_url": r.get("robots_url"), "robots_error": ""}
        old = r["crawlability"]
        new = classify(None, resp, an, rob, r.get("robots_rule_hit", ""))
        r["crawlability"] = new
        r["title"] = an.get("title", "")
        r["date"] = an.get("date", "")
        r["date_first"] = an.get("date_first", "")
        r["date_max"] = an.get("date_max", "")
        r["text_len"] = an.get("text_len", 0)
        r["js_needed"] = an.get("js_needed", False)
        r["paywall"] = an.get("paywall", False)
        r["list_parseable"] = an.get("list_parseable", False)
        full = clean_text(body) if body else ""
        if r["status"] != 200:
            r["has_rating_tp"] = f"未取到原文（HTTP {r['status']}）"
            r["rating_hits"] = 0
        elif "需登录" in r.get("crawlability", "") or "付费墙" in r.get("crawlability", ""):
            r["has_rating_tp"] = "未取到原文（需登录/付费墙）"
            r["rating_hits"] = 0
        elif not r.get("list_parseable"):
            r["has_rating_tp"] = "未取到原文（列表未渲染/无可解析日期）"
            r["rating_hits"] = 0
        else:
            hits = sorted(set(m.group(0).lower() for m in RATING_PAT.finditer(full)))
            r["has_rating_tp"] = ("部分（列表页出现评级/目标价字样：%s）" % "、".join(hits[:6])) if hits \
                else "无（列表页全文未出现评级/目标价字样）"
            r["rating_hits"] = len(hits)
        r["evidence"] = evidence_text(resp, an, rob, new, r1facts.get(r["id"]))
        if r["id"] in notes:
            r["note"] = notes[r["id"]]          # 渠道元数据以 CHANNELS 表为单一事实源
        full_text = clean_text(body) if body else ""
        cad = observed_cadence(full_text)
        r["freq_observed"] = cad
        if old != new:
            changed.append((r["id"], old, new))
    if not getattr(args, "keep_log", False):
        json.dump(log, open(os.path.join(PROBE_DIR, "probe_log.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    else:
        print("[reclass] --keep-log：probe_log.json 未回写（原始快照逐字节不变）", flush=True)
    write_csvs(log)
    print(f"[reclass] 0 请求，重判 {len(log)} 个渠道；判定变化 {len(changed)} 个：", flush=True)
    for cid, old, new in changed:
        print(f"   {cid:10s} {old}  ->  {new}", flush=True)


def observed_cadence(text: str) -> str:
    """从列表页可见日期实测发布节奏（描述性，不外推）。

    返回形如："实测：列表页可见 9 个日期，相邻间隔中位数 3 天（单页快照，非发布承诺）"
    不足 3 个日期则返回空串（不足以判断节奏）。
    """
    if not text:
        return ""
    ds = sorted(set(all_dates(text)))
    if len(ds) < 3:
        return ""
    dd = [datetime.fromisoformat(x).date() for x in ds]
    gaps = sorted((dd[i + 1] - dd[i]).days for i in range(len(dd) - 1) if (dd[i + 1] - dd[i]).days > 0)
    if not gaps:
        return ""
    med = gaps[len(gaps) // 2]
    return f"实测：列表页可见 {len(ds)} 个日期，相邻间隔中位数 {med} 天（单页快照，非发布频率承诺）"


def cmd_csv(args) -> None:
    """离线重生成 CSV：不发任何请求，从已保存的列表页 HTML 重算派生字段。

    与 `reclass` 同一套派生逻辑，额外保证 **probe_log.json 不被回写**（原始快照逐字节不变）。
    """
    args.keep_log = True
    cmd_reclass(args)
    log = json.load(open(os.path.join(PROBE_DIR, "probe_log.json"), encoding="utf-8"))
    print(f"[csv] 已重生成 {CSV_DATA} 与 {CSV_DELIVER}（{len(log)} 行，0 请求，probe_log.json 未改）", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "sample", "verify", "csv", "reclass"])
    ap.add_argument("--only", default="")
    ap.add_argument("--channels", default="")
    ap.add_argument("--meta", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-log", action="store_true",
                    help="只重生成 CSV，不回写 probe_log.json（原始快照逐字节不变）")
    ap.add_argument("--max-requests", type=int, default=200)
    ap.add_argument("--tries", type=int, default=1)
    args = ap.parse_args()
    MAX_REQUESTS = args.max_requests
    _load_ledger()
    print(f"[ledger] 已登记请求 {REQ_COUNT['n']} 次，本次上限 {MAX_REQUESTS}", flush=True)
    if args.cmd == "probe":
        cmd_probe(args)
    elif args.cmd == "csv":
        cmd_csv(args)
    elif args.cmd == "reclass":
        cmd_reclass(args)
    elif args.cmd == "sample":
        cmd_sample(args)
    else:
        cmd_verify(args)
