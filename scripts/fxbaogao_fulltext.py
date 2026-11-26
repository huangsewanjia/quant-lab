#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-623 · 发现报告（fxbaogao）高级 VIP 正文采集。

不是新爬虫框架：节流（同域名 ≥1.05s）、请求台账、robots 判定、`sample`/`verify` 的
「状态码 + 原文」证据口径全部复用 ``scripts/work537_probe.py``；本文件只加一个正文渠道
``fxbaogao_vip`` 的批量入口。

子命令
------
  candidates  从一个或多个列表/搜索页解析详情链接，写候选 CSV（robots 允许的路径才抓）
  plan        候选 -> 队列 JSONL：逐条抓详情页补齐标题/机构/发布日（不下载 PDF）
  fetch       按队列批量下载 PDF：走用户已登录的 Chrome（CDP），逐条落盘 + meta.json，
              失败写 failed.jsonl（report_id / url / http_status / reason）
  sheet       不下载，生成单文件 HTML「待下载清单」，用户手动点（robots/风控兜底）
  verify      重抓详情页核对标题与落盘 meta 一致（验收抽查）
  robots      打印当前 robots.txt 判定（哪条路径能抓、命中哪条规则）

合规边界（2026-11-27 实测 robots.txt）
--------------------------------------
  Disallow: /pdf, /user, /page2, /view
  因此本脚本的 **HTTP 客户端从不请求 /pdf**；PDF 只能由用户自己的登录浏览器会话
  （CDP 驱动其已有窗口）或用户手动点击产生。自动化部分只抓 /detail/ 等允许路径。

依赖
----
  只读详情/列表页：Python 标准库。
  自动下载：Playwright（装在任务工作区 venv，``pip install playwright``，不需 ``playwright install``，
  因为用的是用户已经开着的 Chrome，不下载浏览器内核）；pypdf（核对页数）。

用法见 docs/WORK-623/RUNBOOK.md。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

CHANNEL_ID = "fxbaogao_vip"
SITE = "https://www.fxbaogao.com"
DETAIL_RE = re.compile(r"^/detail/(\d+)/?$")
CANON_ROOT = Path("/srv/share/quantdb/raw/reports_fulltext/fxbaogao")
DEFAULT_GAP = 1.05
DEFAULT_CDP = "http://127.0.0.1:9222"


# ---------------------------------------------------------------- 复用 work537_probe
def _load_probe():
    path = Path(__file__).with_name("work537_probe.py")
    spec = importlib.util.spec_from_file_location("work537_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare_probe(root: Path, max_requests: int):
    probe = _load_probe()
    probe.LEDGER_PATH = str(root.parent / "_probe" / "request_ledger.json")
    probe._load_ledger()
    probe.REQ_COUNT["n"] = 0          # --max-requests 是单轮预算；台账仍累积所有历史请求
    probe.MAX_REQUESTS = max_requests
    probe.DOMAIN_GAP = DEFAULT_GAP
    return probe


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_pages(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def failure(root: Path, report_id, url: str, http_status, reason: str, path: str = "") -> dict:
    rec = {
        "report_id": report_id,
        "url": url or "",
        "http_status": http_status if http_status is not None else 0,
        "reason": reason,
        "path": path,
        "ts": now_iso(),
    }
    out = root / "failed.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ---------------------------------------------------------------- 页面解析
META_TAG = re.compile(r'<meta\s+[^>]*?(?:property|name)=["\']([^"\']+)["\'][^>]*?content=["\'](.*?)["\']', re.I | re.S)
TITLE_TAG = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
LD_JSON = re.compile(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>')
ANCHOR_BLOCK = re.compile(r'(?is)<a\b[^>]*href=["\'](/detail/\d+)["\'][^>]*>(.*?)</a>')
SPAN = re.compile(r"(?is)<span\b[^>]*>(.*?)</span>")
DATE_TXT = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")


def clean(fragment: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_detail(html_text: str) -> dict:
    """详情页 -> 标题 / 机构 / 发布日期。取不到就留空，不猜。"""
    metas = {k.strip().lower(): clean(v) for k, v in META_TAG.findall(html_text)}
    out = {
        "title": metas.get("og:title") or (clean(TITLE_TAG.search(html_text).group(1)) if TITLE_TAG.search(html_text) else ""),
        "org": "",
        "page_date": "",
    }
    keywords = metas.get("keywords", "")
    if keywords:
        first = keywords.split(",")[0].strip()
        if first and not first.isdigit():
            out["org"] = first[:80]
    published = metas.get("article:published_time", "")
    match = DATE_TXT.search(published)
    if match:
        out["page_date"] = "%s-%02d-%02d" % (match.group(1), int(match.group(2)), int(match.group(3)))
    else:
        for blob in LD_JSON.findall(html_text):
            match = DATE_TXT.search(blob)
            if match:
                out["page_date"] = "%s-%02d-%02d" % (match.group(1), int(match.group(2)), int(match.group(3)))
                break
    if not out["org"]:
        title = out["title"]
        head = re.split(r"[:：]", title, maxsplit=1)
        if len(head) == 2 and 1 < len(head[0].strip()) <= 30:
            out["org"] = head[0].strip()[:80]
    return out


def parse_candidates(html_text: str) -> list[dict]:
    """列表页 -> [{report_id,url,title,org,page_date}]。相邻卡片里的机构/日期按就近取。"""
    found: dict[str, dict] = {}
    for match in ANCHOR_BLOCK.finditer(html_text):
        href, inner = match.group(1), match.group(2)
        tail = html_text[match.end(): match.end() + 1200]
        m = DETAIL_RE.match(href)
        if not m:
            continue
        rid = m.group(1)
        title = clean(inner)
        if not title and re.search(r"(?is)<h\d[^>]*>(.*?)</h\d>", tail):
            title = clean(re.search(r"(?is)<h\d[^>]*>(.*?)</h\d>", tail).group(1))
        if not title or len(title) < 6:
            continue
        spans = [clean(s) for s in SPAN.findall(tail)]
        date = ""
        date_at = len(spans)
        for index, span in enumerate(spans):
            dm = DATE_TXT.search(span)
            if dm:
                date = "%s-%02d-%02d" % (dm.group(1), int(dm.group(2)), int(dm.group(3)))
                date_at = index
                break
        # 卡片结构是「行业标签 / 机构 / 日期」：取日期前最后一个短 span 作机构
        org = ""
        for span in reversed(spans[:date_at]):
            if 1 < len(span) <= 30 and not DATE_TXT.search(span):
                org = span
                break
        if rid in found:
            continue
        found[rid] = {"report_id": rid, "url": SITE + href, "title": title,
                      "org": org, "page_date": date}
    return list(found.values())


# ---------------------------------------------------------------- 抓详情页
def fetch_detail(probe, report_id: str, url: str = "") -> dict:
    """robots 允许才抓；返回 detail dict + 状态码原文（证据口径同 work537_probe）。"""
    target = url or f"{SITE}/detail/{report_id}"
    robots = probe.get_robots_cached(target)
    path = urlparse(target).path or "/"
    if robots.get("rules") is None:
        return {"ok": False, "url": target, "http_status": robots.get("robots_status", 0),
                "reason": f"robots.txt 不可读（HTTP {robots.get('robots_status')}），按禁止处理"}
    allowed, hit = probe.robots_allows(robots["rules"], path)
    if not allowed:
        return {"ok": False, "url": target, "http_status": 0, "reason": f"robots 禁止: {hit}"}
    resp = probe.http_get(target)
    detail = parse_detail(resp.get("body", ""))
    detail.update({"ok": resp["status"] == 200, "url": resp.get("final_url", target),
                   "http_status": resp["status"], "content_type": resp.get("headers", {}).get("content-type", ""),
                   "bytes": resp.get("bytes", 0), "fetched_at": resp.get("fetched_at"),
                   "robots_rule": hit})
    if not detail["ok"]:
        detail["reason"] = resp.get("error") or f"HTTP {resp['status']}"
    return detail


# ---------------------------------------------------------------- 浏览器下载（CDP）
LOGIN_JS = """
() => {
  const raw = localStorage.getItem('user');
  const out = {logged_in: false};
  if (!raw) return out;
  try {
    const u = JSON.parse(raw);
    const now = Date.now();
    const ts = x => { const n = Number(x); if (!n) return 0; return n < 1e12 ? n * 1000 : n; };
    const info = u.userInfo || {};
    const vip = ts(u.vipTime) || ts(info.vipTime);
    const basic = ts(u.bVipTime) || ts(info.bVipTime);
    out.logged_in = true;
    out.advanced_vip = vip > now;
    out.standard_vip = basic > now;
    out.vip_time = vip ? new Date(vip).toISOString() : null;
    out.basic_vip_time = basic ? new Date(basic).toISOString() : null;
    out.nickname = info.nickname || u.nickname || '';
  } catch (e) {
    out.error = String(e);
  }
  return out;
}
"""

PAGE_META_JS = """
() => {
  const pick = sel => { const el = document.querySelector(sel); return el ? el.getAttribute('content') : ''; };
  return {
    title: pick('meta[property="og:title"]') || document.title,
    keywords: pick('meta[name="keywords"]'),
    published: pick('meta[property="article:published_time"]')
  };
}
"""

DEFAULT_SELECTORS = [
    'a[href^="/pdf"]',
    'button:has-text("下载")',
    'a:has-text("下载")',
    '[class*="download"]',
    '[data-testid*="download"]',
]


class CdpDriver:
    """驱动用户已经登录的 Chrome（Playwright connect_over_cdp），不接触账号密码。"""

    def __init__(self, cdp_url: str = DEFAULT_CDP, settle_ms: int = 1500):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
        except Exception:
            self._pw.stop()
            raise
        if not self._browser.contexts:
            raise RuntimeError("CDP 已连上但没有浏览器上下文：Chrome 需带 --user-data-dir 启动")
        self._ctx = self._browser.contexts[0]
        self._settle_ms = settle_ms

    def login_state(self) -> dict:
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        try:
            return page.evaluate(LOGIN_JS)
        except Exception as exc:  # noqa: BLE001 - about:blank 等不能读 localStorage 的页面
            return {"logged_in": False, "error": f"{type(exc).__name__}: {exc}"}

    def page_meta(self, url: str) -> dict:
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(self._settle_ms)
            return page.evaluate(PAGE_META_JS)
        finally:
            page.close()

    def _find_control(self, page, selectors):
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if locator.count() == 0:
                    continue
                first = locator.first
                if first.is_visible():
                    return first, selector
            except Exception:  # noqa: BLE001 - 选择器语法/元素消失都不该终止整批
                continue
        return None, ""

    def download(self, url: str, dest: Path, selectors=None, timeout_ms: int = 60000) -> dict:
        selectors = selectors or DEFAULT_SELECTORS
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(self._settle_ms)
            meta = page.evaluate(PAGE_META_JS)
            control, used = self._find_control(page, selectors)
            if control is None:
                return {"ok": False, "reason": "download_control_not_found",
                        "page_meta": meta, "html": page.content()[:60000]}
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_suffix(".pdf.part")
            with page.expect_download(timeout=timeout_ms) as pending:
                control.click()
            download = pending.value
            download.save_as(str(part))
            os.replace(part, dest)
            return {"ok": True, "selector": used, "page_meta": meta,
                    "suggested_filename": download.suggested_filename}
        except Exception as exc:  # noqa: BLE001 - 单篇失败写 failed.jsonl，继续
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            page.close()

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()


# ---------------------------------------------------------------- 队列与落盘
def slugify(title: str, limit: int = 60) -> str:
    text = re.sub(r"[-_—|]?\s*发现报告网?$", "", title or "")
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return (text[:limit].strip("-") or "report")


def dest_path(root: Path, page_date: str, report_id: str, title: str) -> Path:
    date = page_date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", page_date or "") else dt.date.today().isoformat()
    return root / date / f"{report_id}-{slugify(title)}.pdf"


def write_meta(pdf: Path, info: dict) -> Path:
    pages = pdf_pages(pdf)
    meta = {
        "channel_id": CHANNEL_ID,
        "report_id": int(info["report_id"]),
        "title": info["title"],
        "org": info.get("org", ""),
        "page_date": info.get("page_date") or "",
        "source_url": info["url"],
        "bytes": pdf.stat().st_size,
        "pages": pages,
        "sha256": sha256_file(pdf),
        "fetched_at": now_iso(),
        "fetch_mode": info.get("fetch_mode", "cdp"),
        "http_status": info.get("http_status", 200),
        "robots_rule": info.get("robots_rule", ""),
    }
    out = pdf.with_suffix(".meta.json")
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load_items(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        return [{k: (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(text.splitlines()) if (row.get("report_id") or "").strip()]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_csv(items: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["report_id", "url", "title", "org", "page_date"])
        writer.writeheader()
        for item in items:
            writer.writerow({k: item.get(k, "") for k in writer.fieldnames})


# ---------------------------------------------------------------- 子命令
def cmd_candidates(args) -> int:
    probe = _prepare_probe(Path(args.root), args.max_requests)
    items: dict[str, dict] = {}
    for url in args.url:
        robots = probe.get_robots_cached(url)
        path = urlparse(url).path or "/"
        if robots.get("rules") is None:
            print(f"[candidates] robots 不可读，跳过 {url}", file=sys.stderr)
            continue
        allowed, hit = probe.robots_allows(robots["rules"], path)
        if not allowed:
            print(f"[candidates] robots 禁止（{hit}），跳过 {url}", file=sys.stderr)
            continue
        resp = probe.http_get(url)
        found = parse_candidates(resp.get("body", ""))
        print(f"[candidates] HTTP {resp['status']} {url} -> {len(found)} 条")
        for item in found:
            items.setdefault(item["report_id"], item)
    out = Path(args.out)
    write_csv(list(items.values()), out)
    print(f"[candidates] 共 {len(items)} 条 -> {out}")
    return 0


def cmd_plan(args) -> int:
    root = Path(args.root)
    probe = _prepare_probe(root, args.max_requests)
    items = load_items(Path(args.input))
    queue: list[dict] = []
    fails = 0
    for item in items[: args.limit]:
        rid = str(item.get("report_id") or "")
        if args.no_fetch:
            queue.append({**item, "report_id": rid})
            continue
        detail = fetch_detail(probe, rid, item.get("url", ""))
        if not detail.get("ok"):
            fails += 1
            failure(root, rid, item.get("url", ""), detail.get("http_status", 0),
                    detail.get("reason", "详情页未取到"))
            continue
        queue.append({
            "report_id": rid,
            "url": detail["url"],
            "title": detail.get("title") or item.get("title", ""),
            "org": detail.get("org") or item.get("org", ""),
            "page_date": detail.get("page_date") or item.get("page_date", ""),
            "http_status": detail.get("http_status"),
            "robots_rule": detail.get("robots_rule", ""),
            "content_type": detail.get("content_type", ""),
            "fetched_at": detail.get("fetched_at"),
        })
        time.sleep(args.gap if args.gap else DEFAULT_GAP)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for row in queue:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[plan] 队列 {len(queue)} 条，失败 {fails} 条 -> {out}")
    return 0 if fails == 0 else 1


def _assert_advanced_vip(driver, root: Path, allow_standard: bool) -> dict:
    state = driver.login_state()
    ok = bool(state.get("logged_in")) and (state.get("advanced_vip") or
                                           (allow_standard and state.get("standard_vip")))
    record = {"checked_at": now_iso(), "login_state": state, "ok": ok,
              "cdp": getattr(driver, "cdp_url", None)}
    (root / "login_state.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    return record


def download_batch(driver, items: list[dict], root: Path, probe, limit: int = 20,
                   gap: float = DEFAULT_GAP, selectors=None, robots_lookup=None,
                   success_log: str = "", sleep=time.sleep) -> dict:
    """逐条下载：先过 robots，再交给 driver（CDP 浏览器会话），逐条落盘 + meta.json。

    任何一篇失败都只写 failed.jsonl 并继续；返回统计，不做「整批成功/失败」的二值判断。
    """
    robots_lookup = robots_lookup or probe.get_robots_cached
    stats = {"ok": 0, "failed": 0, "skipped_robots": 0, "destinations": []}
    for item in items[:limit]:
        rid = str(item.get("report_id") or "")
        url = item.get("url") or f"{SITE}/detail/{rid}"
        robots = robots_lookup(url)
        path = urlparse(url).path or "/"
        if robots.get("rules") is None:
            failure(root, rid, url, robots.get("robots_status", 0), "robots.txt 不可读")
            stats["failed"] += 1
            stats["skipped_robots"] += 1
            continue
        allowed, hit = probe.robots_allows(robots["rules"], path)
        if not allowed:
            failure(root, rid, url, 0, f"robots 禁止: {hit}")
            stats["failed"] += 1
            stats["skipped_robots"] += 1
            continue
        tmp = root / "_partial" / f"{rid}.pdf.part"
        result = driver.download(url, tmp, selectors=selectors)
        if not result.get("ok"):
            reason = result.get("reason", "下载失败")
            if result.get("html"):
                dump = root / "_diagnostics" / f"{rid}.html"
                dump.parent.mkdir(parents=True, exist_ok=True)
                dump.write_text(result["html"], encoding="utf-8")
                reason += f"（页面快照: {dump}）"
            failure(root, rid, url, result.get("http_status", 200), reason)
            stats["failed"] += 1
            sleep(gap)
            continue
        page_meta = result.get("page_meta") or {}
        detail = parse_detail(
            '<meta property="og:title" content="%s"><meta property="article:published_time" content="%s">'
            '<meta name="keywords" content="%s">'
            % (html.escape(page_meta.get("title", "")), html.escape(page_meta.get("published", "")),
               html.escape(page_meta.get("keywords", ""))))
        title = detail.get("title") or item.get("title", "")
        page_date = detail.get("page_date") or item.get("page_date", "")
        dest = dest_path(root, page_date, rid, title)
        try:
            with open(tmp, "rb") as fh:
                magic = fh.read(5)
            if magic != b"%PDF-":
                raise ValueError(f"不是 PDF（文件头 {magic!r}）")
            pages = pdf_pages(tmp)
            if pages <= 0:
                raise ValueError("页数为 0")
        except Exception as exc:  # noqa: BLE001 - 坏文件要留证据，不算成功
            failure(root, rid, url, 200, f"PDF 校验失败: {type(exc).__name__}: {exc}", str(tmp))
            stats["failed"] += 1
            sleep(gap)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, dest)
        write_meta(dest, {"report_id": rid, "title": title,
                          "org": detail.get("org", "") or item.get("org", ""),
                          "page_date": page_date, "url": url,
                          "robots_rule": hit, "fetch_mode": "cdp"})
        if success_log:
            with open(success_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"report_id": rid, "title": title, "pdf": str(dest),
                                     "pages": pages, "url": url}, ensure_ascii=False) + "\n")
        stats["ok"] += 1
        stats["destinations"].append(str(dest))
        print(f"[fetch] {rid} {pages} 页 -> {dest}", flush=True)
        sleep(gap)
    return stats


def cmd_fetch(args) -> int:
    root = Path(args.root)
    probe = _prepare_probe(root, args.max_requests)
    items = load_items(Path(args.input))
    if not items:
        print("[fetch] 队列为空", file=sys.stderr)
        return 2
    if args.dry_run:
        return _fetch_dry_run(args, probe, items, root)
    from playwright.sync_api import sync_playwright  # noqa: F401  提前暴露缺依赖

    driver = CdpDriver(args.cdp, settle_ms=args.settle_ms)
    try:
        state = _assert_advanced_vip(driver, root, args.allow_standard)
        if not state["ok"]:
            print("[fetch] 登录态不是高级 VIP（或未登录），停止；证据见 "
                  f"{root / 'login_state.json'}", file=sys.stderr)
            return 3
        stats = download_batch(driver, items, root, probe, limit=args.limit,
                               gap=args.gap, selectors=args.selector,
                               success_log=args.out)
    finally:
        driver.close()
    print(f"[fetch] 成功 {stats['ok']} 篇，失败 {stats['failed']} 篇；"
          f"失败明细 {root / 'failed.jsonl'}")
    return 0 if stats["failed"] == 0 else 1


def _fetch_dry_run(args, probe, items, root: Path) -> int:
    """只读诊断：核对登录态、详情页 robots/状态与下载控件候选，不点击、不落 PDF。"""
    from playwright.sync_api import sync_playwright  # noqa: F401

    driver = CdpDriver(args.cdp, settle_ms=args.settle_ms)
    report = {"checked_at": now_iso(), "cdp": args.cdp, "items": []}
    try:
        report["login_state"] = driver.login_state()
        for item in items[: args.limit]:
            rid = str(item.get("report_id") or "")
            url = item.get("url") or f"{SITE}/detail/{rid}"
            robots = probe.get_robots_cached(url)
            allowed, hit = probe.robots_allows(robots.get("rules") or [], urlparse(url).path or "/")
            page = driver._ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(args.settle_ms)
                candidates = []
                for selector in args.selector or DEFAULT_SELECTORS:
                    try:
                        count = page.locator(selector).count()
                    except Exception:  # noqa: BLE001
                        count = -1
                    if count:
                        candidates.append({"selector": selector, "count": count})
                report["items"].append({"report_id": rid, "url": url, "robots_allowed": allowed,
                                        "robots_rule": hit, "meta": page.evaluate(PAGE_META_JS),
                                        "controls": candidates})
            finally:
                page.close()
            time.sleep(args.gap)
    finally:
        driver.close()
    out = root / "dry_run_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[fetch --dry-run] 诊断 {len(report['items'])} 条 -> {out}")
    return 0


def cmd_sheet(args) -> int:
    items = load_items(Path(args.input))
    root = Path(args.root)
    rows = []
    for index, item in enumerate(items[: args.limit], 1):
        rid = str(item.get("report_id") or "")
        title = item.get("title") or ""
        page_date = item.get("page_date") or ""
        name = f"{rid}-{slugify(title)}.pdf"
        rows.append(
            "<tr data-q=\"%s\">"
            "<td class=num>%d</td>"
            "<td><a href=\"%s\" target=\"_blank\" rel=\"noopener\">%s</a></td>"
            "<td>%s</td><td>%s</td>"
            "<td><code class=fname>%s</code> <button class=copy data-name=\"%s\">复制</button></td>"
            "</tr>" % (html.escape((title + " " + (item.get("org") or "")).lower()),
                       index,
                       html.escape(item.get("url") or f"{SITE}/detail/{rid}"),
                       html.escape(title or rid),
                       html.escape(item.get("org") or ""),
                       html.escape(page_date),
                       html.escape(name), html.escape(name)))
    doc = SHEET_TEMPLATE.replace("{{ROWS}}", "\n".join(rows)) \
                        .replace("{{COUNT}}", str(len(rows))) \
                        .replace("{{DATE}}", dt.date.today().isoformat()) \
                        .replace("{{INBOX}}", html.escape(str(root / "_inbox")))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"[sheet] {len(rows)} 条 -> {out}")
    return 0


def cmd_verify(args) -> int:
    probe = _prepare_probe(Path(args.root), args.max_requests)
    bad = 0
    for meta_path in [Path(p) for p in args.meta.split(",") if p.strip()]:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        detail = fetch_detail(probe, str(meta["report_id"]), meta.get("source_url", ""))
        stored = (meta.get("title") or "").strip()
        live = (detail.get("title") or "").strip()
        same = bool(live) and (stored == live or stored in live or live in stored)
        pages = pdf_pages(Path(meta_path).with_suffix(".pdf")) if Path(meta_path).with_suffix(".pdf").exists() else -1
        print(f"[verify] {meta.get('source_url')} HTTP {detail.get('http_status')} "
              f"落盘标题={stored[:50]} 重取标题={live[:50]} 一致={same} 页数={pages}")
        bad += 0 if same and pages > 0 else 1
        time.sleep(args.gap)
    return 0 if bad == 0 else 1


def cmd_robots(args) -> int:
    probe = _prepare_probe(Path(args.root), args.max_requests)
    for url in args.url:
        robots = probe.get_robots_cached(url)
        path = urlparse(url).path or "/"
        if robots.get("rules") is None:
            print(f"{url} -> robots 不可读（HTTP {robots.get('robots_status')}）")
            continue
        allowed, hit = probe.robots_allows(robots["rules"], path)
        print(f"{url} -> {'允许' if allowed else '禁止'}（{hit}）")
    return 0


SHEET_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>发现报告 VIP 待下载清单 · WORK-623</title>
<style>
:root{--ink:#1b1f24;--muted:#5b6572;--line:#dfe4ea;--accent:#2155a3;--bg:#fbfcfd}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:28px 32px 18px;border-bottom:1px solid var(--line);background:#fff}
h1{margin:0 0 6px;font-size:20px;font-weight:650}
.sub{color:var(--muted);font-size:13px}
main{padding:18px 32px 48px;max-width:1180px}
ol.steps{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px 14px 14px 34px;margin:0 0 18px}
ol.steps li{margin:4px 0}
.inbox{font-family:ui-monospace,Consolas,monospace;background:#eef2f7;padding:1px 6px;border-radius:4px}
.tools{display:flex;gap:10px;align-items:center;margin:0 0 12px}
input[type=search]{flex:1;max-width:380px;padding:7px 10px;border:1px solid var(--line);border-radius:6px;font:inherit}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left;font-size:14px}
th{background:#f3f6f9;font-weight:600;font-size:13px;color:var(--muted)}
td.num{color:var(--muted);width:44px}
td:nth-child(3),td:nth-child(4){white-space:nowrap}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code.fname{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;background:#f4f6f8;padding:1px 5px;border-radius:4px;word-break:break-all}
button.copy{border:1px solid var(--line);background:#fff;border-radius:5px;padding:2px 7px;font-size:12px;cursor:pointer;color:var(--muted)}
button.copy:hover{border-color:var(--accent);color:var(--accent)}
tr:hover td{background:#f8fafc}
.note{margin-top:16px;color:var(--muted);font-size:13px}
</style></head><body>
<header>
<h1>发现报告 VIP · 待下载清单</h1>
<div class="sub">生成日期 {{DATE}} · 共 {{COUNT}} 条 · 本页只列链接与目标文件名，不含正文；正文版权仅限内部检索使用</div>
</header>
<main>
<ol class="steps">
<li>点标题打开详情页（已登录的高级 VIP 会话）。</li>
<li>点页面上的「下载」按钮，浏览器开始下载 PDF。</li>
<li>把下载好的 PDF 放进 <span class="inbox">{{INBOX}}</span>，文件名不用改。</li>
<li>回到 Mo-server 执行 <span class="inbox">python -m db.etl.report_fulltext adopt --queue &lt;队列.jsonl&gt; --inbox {{INBOX}}</span>，再 <span class="inbox">ingest</span> 入库。</li>
</ol>
<div class="tools"><input type="search" id="q" placeholder="按标题/机构筛选…"></div>
<table><thead><tr><th>#</th><th>标题（点开详情页）</th><th>机构</th><th>页面日期</th><th>收编后的文件名</th></tr></thead>
<tbody id="rows">
{{ROWS}}
</tbody></table>
<p class="note">文件名里的日期段以页面日期为准；adopt 会按 PDF 首页文字匹配队列，匹配不上或出现并列时写 unmatched.jsonl，不猜。</p>
</main>
<script>
const q = document.getElementById('q');
q.addEventListener('input', () => {
  const v = q.value.trim().toLowerCase();
  document.querySelectorAll('#rows tr').forEach(tr => {
    tr.style.display = !v || (tr.dataset.q || '').includes(v) ? '' : 'none';
  });
});
document.querySelectorAll('button.copy').forEach(btn => {
  btn.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(btn.dataset.name); btn.textContent = '已复制'; }
    catch (e) { btn.textContent = '复制失败'; }
    setTimeout(() => btn.textContent = '复制', 1200);
  });
});
</script>
</body></html>
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=str(CANON_ROOT), help="落盘根目录")
    common.add_argument("--max-requests", type=int, default=200, help="单轮 HTTP 请求上限")
    common.add_argument("--gap", type=float, default=DEFAULT_GAP, help="同域名请求间隔秒（≥1.05）")

    parser = argparse.ArgumentParser(prog="fxbaogao_fulltext.py",
                                     description="发现报告 VIP 正文采集（WORK-623）")
    sub = parser.add_subparsers(dest="command", required=True)

    cand = sub.add_parser("candidates", parents=[common], help="列表页 -> 候选 CSV")
    cand.add_argument("--url", action="append", required=True)
    cand.add_argument("--out", required=True)

    plan = sub.add_parser("plan", parents=[common], help="候选 -> 队列 JSONL（补标题/机构/日期）")
    plan.add_argument("--input", required=True)
    plan.add_argument("--out", required=True)
    plan.add_argument("--limit", type=int, default=200)
    plan.add_argument("--no-fetch", action="store_true")

    fetch = sub.add_parser("fetch", parents=[common], help="按队列批量下载 PDF（需用户登录 Chrome 的 CDP）")
    fetch.add_argument("--input", required=True)
    fetch.add_argument("--limit", type=int, default=20)
    fetch.add_argument("--cdp", default=DEFAULT_CDP)
    fetch.add_argument("--selector", action="append", help="下载控件选择器（可多次给）")
    fetch.add_argument("--settle-ms", type=int, default=1500)
    fetch.add_argument("--allow-standard", action="store_true", help="标准 VIP 也尝试（默认只认高级 VIP）")
    fetch.add_argument("--dry-run", action="store_true", help="只诊断登录态与控件，不下载")
    fetch.add_argument("--out", default="", help="成功清单 JSONL 追加输出")

    sheet = sub.add_parser("sheet", parents=[common], help="生成单文件 HTML 待下载清单")
    sheet.add_argument("--input", required=True)
    sheet.add_argument("--out", required=True)
    sheet.add_argument("--limit", type=int, default=500)

    verify = sub.add_parser("verify", parents=[common], help="重抓详情页核对标题与页数")
    verify.add_argument("--meta", required=True, help="逗号分隔的 meta.json 路径")

    robots = sub.add_parser("robots", parents=[common], help="打印 robots 判定")
    robots.add_argument("--url", action="append", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for command in ("candidates", "plan", "fetch", "sheet", "verify", "robots"):
        if args.command == command:
            return {"candidates": cmd_candidates, "plan": cmd_plan, "fetch": cmd_fetch,
                    "sheet": cmd_sheet, "verify": cmd_verify, "robots": cmd_robots}[command](args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
