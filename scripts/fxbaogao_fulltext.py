#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-623 · 发现报告（fxbaogao）官方 API 正文采集。

口径（2026-11-27 契约变更 01）：走官方接口，不做浏览器自动化。

    Base:  https://api.fxbaogao.com
    鉴权:  Authorization: Bearer <API Key>   （token 只从 secrets 文件读）
    搜索:  POST /mofoun/agent/search         keywords / orgNames / startTime / endTime / pageNum(1-10)
    段落:  POST /mofoun/agent/paragraph      reportId / keyword
    取地址: GET  /mofoun/agent/download?reportId=<id>   -> PDF 地址（拉 PDF 扣下载权益）

子命令：

  check-auth   用一次搜索验证 token 是否有效（不消耗下载额度），写 check_auth.json
  search       搜索 -> 候选 JSONL（report_id / title / org / page_date / site_url / raw）
  fetch        按候选逐篇取 PDF：download 拿地址 -> 拉 PDF -> 校验 -> 落盘 + meta.json
               失败写 failed.jsonl（report_id / url / http_status / code / msg / reason）
  verify       用官网详情页标题核对落盘 meta（robots 允许的 /detail/ 路径），并复核页数

鉴权口径（必须）：失败返回 **HTTP 200 + body.code=300011**，不是 401。
所以本脚本判成败看 `code`（0/200 视为成功），HTTP 状态只作证据。

复用：节流（同域名 ≥1.05s，按请求**完成**时刻计）、请求台账、robots 判定全部来自
``scripts/work537_probe.py``；本文件不新造 HTTP 框架。

凭据：``/srv/share/quantdb/secrets/fxbaogao_api_key``（单行、600、目录 700）。
日志/交付/issue 里只写路径与 sha256 前 8 位；token 不落 ledger、不打印、不进 git。
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

API_BASE = "https://api.fxbaogao.com"
PDF_BASE = "https://dr.fxbaogao.com/"
SITE_VIEW = "https://www.fxbaogao.com/view?id={report_id}"
DETAIL_BASE = "https://www.fxbaogao.com/detail/"
TOKEN_PATH = Path("/srv/share/quantdb/secrets/fxbaogao_api_key")
CANON_ROOT = Path(os.environ.get("FXBAOGAO_ROOT")
                  or "/srv/share/quantdb/raw/reports_fulltext/fxbaogao")
DEFAULT_GAP = 1.05
DEFAULT_LIMIT = 5
MAX_PAGES = 10
SUCCESS_CODES = {0, 200}

PDF_URL_KEYS = {"pdfurl", "url", "fileurl", "downloadurl"}
PDF_RELATIVE_KEYS = PDF_URL_KEYS | {"data"}
PARAGRAPH_NOT_USED = True  # paragraph 只做预留；本单不写正文文本层

UA_API = "quantdb-worker-623/1.0 (internal research)"


class AuthError(RuntimeError):
    pass


# ---------------------------------------------------------------- 复用 work537_probe
def _load_probe():
    path = Path(__file__).with_name("work537_probe.py")
    spec = importlib.util.spec_from_file_location("work537_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_probe(root: Path, max_requests: int):
    probe = _load_probe()
    probe.LEDGER_PATH = str(root.parent / "_probe" / "request_ledger.json")
    probe._load_ledger()
    probe.REQ_COUNT["n"] = 0          # --max-requests 是单轮预算；台账仍累积历史
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


# ---------------------------------------------------------------- 凭据
def load_token(path: Path = TOKEN_PATH) -> tuple[str, str]:
    """读 token；返回 (token, 指纹前 8 位)。指纹是 sha256(token) 的前 8 位，可写进日志。"""
    path = Path(path)
    if not path.exists():
        raise AuthError(f"token 文件不存在: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise AuthError(f"token 文件为空: {path}")
    if any(ch.isspace() for ch in token):
        raise AuthError(f"token 文件应为单行且不含空白: {path}")
    # 权限按符号链接的真实目标判定（链接所在目录不受本文件控制，避免正常布局被误拒）
    target = path.resolve()
    mode = target.stat().st_mode & 0o777
    if mode & 0o077:
        raise AuthError(f"token 文件权限 {oct(mode)}，必须为 600（组/其他不可读）: {path}")
    parent = target.parent
    if parent.exists():
        dir_mode = parent.stat().st_mode & 0o777
        if dir_mode & 0o077:
            raise AuthError(f"token 目录权限 {oct(dir_mode)}，必须为 700: {parent}")
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- API 调用
def norm_code(value):
    """把 code 归一成 int；bool 不当作 0/1（显式排除），无法识别返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def api_call(probe, token: str, method: str, path: str, body: dict | None = None,
             accept: str = "application/json", timeout: int = 60) -> dict:
    """发一次 API 请求并解析 envelope：{http_status, code, msg, data, raw, url}。"""
    url = API_BASE + path
    headers = {"Authorization": f"Bearer {token}", "Accept": accept,
               "Content-Type": "application/json", "User-Agent": UA_API}
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    resp = probe.http_request(url, method, payload, headers, UA_API, timeout)
    parsed, parse_error = None, ""
    if resp.get("body"):
        try:
            parsed = json.loads(resp["body"])
        except json.JSONDecodeError as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
    code = norm_code(parsed.get("code")) if isinstance(parsed, dict) else None
    msg = (parsed.get("msg") or parsed.get("message")) if isinstance(parsed, dict) else None
    # 契约口径：成败只看 code。缺 code 视为未知报文 → 判失败（宁可报错也不把失败当成功）。
    ok = resp["status"] == 200 and not parse_error and code in SUCCESS_CODES
    return {"ok": ok, "http_status": resp["status"], "code": code, "msg": msg or "",
            "data": parsed.get("data") if isinstance(parsed, dict) else None,
            "raw": parsed, "body": resp.get("body", ""), "error": resp.get("error", ""),
            "parse_error": parse_error, "bytes": resp.get("bytes", 0), "url": url}


def parse_error_body(raw: bytes) -> tuple:
    """从 PDF 端点的错误回包里取 (code, msg)；非 JSON 时 msg=body 前 200 字。"""
    text = raw.decode("utf-8", errors="replace")
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return None, text[:200]
    if isinstance(body, dict):
        return norm_code(body.get("code")), (body.get("msg") or body.get("message") or text[:200])
    return None, text[:200]


def api_error_text(result: dict) -> str:
    bits = [f"http={result.get('http_status')}", f"code={result.get('code')}"]
    if result.get("code") is None:
        bits.append("code 缺失（未知报文格式）")
    if result.get("msg"):
        bits.append(f"msg={result['msg']}")
    if result.get("parse_error"):
        bits.append(f"parse_error={result['parse_error']}")
    if result.get("error"):
        bits.append(f"transport={result['error']}")
    return "; ".join(bits)


# ---------------------------------------------------------------- 报文规整
DATE_TXT = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


def norm_date(value) -> str:
    """接受毫秒/秒时间戳、ISO 字符串、YYYY-MM-DD（含全角分隔）；解析不了返回空串。"""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = int(value)
        seconds = number / 1000 if number > 10**11 else number
        try:
            return dt.datetime.fromtimestamp(seconds, dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    match = DATE_TXT.search(str(value))
    if match:
        try:
            return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return ""
    return ""


def _first(item: dict, keys: tuple[str, ...]):
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def normalize_report(item: dict) -> dict | None:
    """把搜索结果的字段变体收敛成规范行（字段名以官方 MCP 源码可证的 reportId 为准，其余容错）。"""
    if not isinstance(item, dict):
        return None
    report_id = _first(item, ("reportId", "report_id", "id"))
    if report_id is None or not str(report_id).strip().isdigit():
        return None
    report_id = str(report_id).strip()
    title = _first(item, ("title", "reportTitle", "reportName", "name")) or ""
    org = _first(item, ("orgName", "orgShortName", "org", "orgNameCn", "author")) or ""
    page_date = norm_date(_first(item, ("publishTime", "publishDate", "publish_date", "date", "pubTime")))
    return {
        "report_id": report_id,
        "title": str(title).strip(),
        "org": str(org).strip(),
        "page_date": page_date,
        "url": SITE_VIEW.format(report_id=report_id),
        "raw_item": item,
    }


def iter_report_items(data) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("dataList", "list", "records", "items", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def extract_pdf_urls(data) -> list[dict]:
    """从 download 返回里找 PDF 地址；相对路径按官方 MCP 实现拼 dr.fxbaogao.com。"""
    urls: list[dict] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if not isinstance(child, str):
                    walk(child)
                    continue
                looks_relative = child.lower().startswith(("/", "http://", "https://")) or ".pdf" in child.lower()
                if lowered in PDF_RELATIVE_KEYS and looks_relative:
                    text = child.strip()
                    if not text:
                        continue
                    url = text if text.startswith(("http://", "https://")) else PDF_BASE + text.lstrip("/")
                    urls.append({"field": str(key), "url": url})
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    seen, out = set(), []
    for item in urls:
        if item["url"] not in seen:
            seen.add(item["url"])
            out.append(item)
    return out


def slugify(title: str, limit: int = 60) -> str:
    text = re.sub(r"[-_—|]?\s*发现报告网?$", "", title or "")
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return (text[:limit].strip("-") or "report")


def dest_path(root: Path, page_date: str, report_id: str, title: str) -> Path:
    date = page_date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", page_date or "") else dt.date.today().isoformat()
    return root / date / f"{report_id}-{slugify(title)}.pdf"


def failure(root: Path, report_id, url: str, http_status, reason: str,
            code=None, msg: str = "", path: str = "") -> dict:
    if isinstance(report_id, str) and report_id.isdigit():
        report_id = int(report_id)
    rec = {"report_id": report_id, "url": url or "", "http_status": http_status or 0,
           "code": code, "msg": msg or "", "reason": reason or "", "path": path, "ts": now_iso()}
    out = root / "failed.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ---------------------------------------------------------------- 搜索
def search_page(probe, token: str, keywords: str, orgs: list[str], start: str, end: str,
                page: int) -> dict:
    if not 1 <= page <= MAX_PAGES:
        raise ValueError(f"pageNum 必须在 1–{MAX_PAGES}（官方上限，超出要改检索条件）")
    body = {"keywords": keywords or "", "orgNames": orgs or [], "pageNum": page}
    if start:
        body["startTime"] = start
    if end:
        body["endTime"] = end
    return api_call(probe, token, "POST", "/mofoun/agent/search", body)


def cmd_check_auth(args) -> int:
    root = Path(args.root)
    probe = prepare_probe(root, args.max_requests)
    try:
        token, fingerprint = load_token(Path(args.token_file))
    except AuthError as exc:
        record = {"checked_at": now_iso(), "ok": False, "error": str(exc),
                  "token_file": str(args.token_file), "token_sha256_8": None}
        root.mkdir(parents=True, exist_ok=True)
        (root / "check_auth.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
        print(f"[check-auth] {exc}", file=sys.stderr)
        return 3
    result = search_page(probe, token, args.keywords, [], "", "", 1)
    record = {
        "checked_at": now_iso(), "ok": result["ok"], "token_file": str(args.token_file),
        "token_sha256_8": fingerprint, "http_status": result["http_status"],
        "code": result["code"], "msg": result["msg"], "parse_error": result["parse_error"],
        "error": result["error"], "url": result["url"], "bytes": result["bytes"],
        "request_fingerprint": "POST /mofoun/agent/search pageNum=1",
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "check_auth.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(f"[check-auth] ok={result['ok']} http={result['http_status']} code={result['code']} "
          f"msg={result['msg'][:80]!r} token_sha256_8={fingerprint}")
    return 0 if result["ok"] else 4


def cmd_search(args) -> int:
    root = Path(args.root)
    probe = prepare_probe(root, args.max_requests)
    try:
        token, fingerprint = load_token(Path(args.token_file))
    except AuthError as exc:
        print(f"[search] {exc}", file=sys.stderr)
        return 3
    if not 1 <= args.pages <= MAX_PAGES:
        print(f"[search] --pages 必须在 1–{MAX_PAGES}（收到 {args.pages}）", file=sys.stderr)
        return 2
    orgs = [x.strip() for x in (args.org or "").split(",") if x.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = Path(args.raw_dir) if args.raw_dir else out.parent / "search_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    failures = 0
    for page in range(1, args.pages + 1):
        result = search_page(probe, token, args.keywords, orgs, args.time or "", args.end_time or "", page)
        (raw_dir / f"page{page}.json").write_text(
            json.dumps({"checked_at": now_iso(), "http_status": result["http_status"],
                        "code": result["code"], "msg": result["msg"], "raw": result["raw"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        if not result["ok"]:
            failures += 1
            print(f"[search] page{page} 失败：{api_error_text(result)}", file=sys.stderr)
            failure(root, None, result["url"], result["http_status"],
                    f"search 失败: {api_error_text(result)}", result["code"], result["msg"])
            break
        items = iter_report_items(result["data"])
        page_rows = [row for row in (normalize_report(x) for x in items) if row]
        for row in page_rows:
            rows.setdefault(row["report_id"], row)
        print(f"[search] page{page}: 报文 {len(items)} 条，可规整 {len(page_rows)} 条")
        time.sleep(args.gap)
    with open(out, "w", encoding="utf-8") as fh:
        for row in rows.values():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta = {"checked_at": now_iso(), "keywords": args.keywords, "orgs": orgs,
            "time": args.time, "pages_requested": args.pages,
            "token_sha256_8": fingerprint, "rows": len(rows), "failures": failures,
            "out": str(out), "raw_dir": str(raw_dir)}
    (out.parent / "search_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
    print(f"[search] {len(rows)} 条 -> {out}（失败页 {failures}）")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------- 下载
def load_items(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        return [{k: (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(text.splitlines()) if (row.get("report_id") or "").strip()]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def existing_pdf(root: Path, report_id: str) -> Path | None:
    for path in root.glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/{report_id}-*.pdf"):
        if path.exists():
            return path
    return None


def fetch_pdf_bytes(probe, token: str, pdf_url: str, timeout: int = 180) -> dict:
    headers = {"Accept": "application/pdf,*/*", "User-Agent": UA_API}
    if pdf_url.startswith(API_BASE):
        headers["Authorization"] = f"Bearer {token}"
    return probe.http_request(pdf_url, "GET", None, headers, UA_API, timeout, binary=True)


def cmd_fetch(args) -> int:
    root = Path(args.root)
    probe = prepare_probe(root, args.max_requests)
    try:
        token, fingerprint = load_token(Path(args.token_file))
    except AuthError as exc:
        print(f"[fetch] {exc}", file=sys.stderr)
        return 3
    items = load_items(Path(args.input))
    if not items:
        print("[fetch] 候选为空", file=sys.stderr)
        return 2
    stats = {"ok": 0, "failed": 0, "skipped_existing": 0, "quota_suspect": 0,
             "token_sha256_8": fingerprint, "destinations": []}
    for item in items[: args.limit]:
        rid = str(item.get("report_id") or item.get("reportId") or "").strip()
        url = item.get("url") or SITE_VIEW.format(report_id=rid or "")
        if not rid.isdigit():
            stats["failed"] += 1
            failure(root, rid, url, 0, "report_id 非数字", None, "", "")
            continue
        if not args.force:
            existing = existing_pdf(root, rid)
            if existing:
                stats["skipped_existing"] += 1
                print(f"[fetch] {rid} 已存在，跳过（避免重复扣额度）: {existing}")
                continue
        resolved = api_call(probe, token, "GET", f"/mofoun/agent/download?reportId={rid}")
        if not resolved["ok"]:
            stats["failed"] += 1
            failure(root, rid, url, resolved["http_status"],
                    f"取 PDF 地址失败: {api_error_text(resolved)}", resolved["code"], resolved["msg"])
            print(f"[fetch] {rid} 取地址失败：{api_error_text(resolved)}", file=sys.stderr)
            time.sleep(args.gap)
            continue
        title = (item.get("title") or "").strip() or f"report-{rid}"
        page_date = norm_date(item.get("page_date")) or dt.date.today().isoformat()
        dest = dest_path(root, page_date, rid, title)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 额度证据：download 一成功就落原始回包；--force 重试不覆盖已有正常 PDF 的证据
        stem = dest.stem if not dest.exists() else f"{dest.stem}-attempt-{dt.datetime.now():%Y%m%dT%H%M%S}"
        download_note = dest.with_name(stem + ".download.json")
        urls = extract_pdf_urls(resolved["raw"])
        pdf_url = urls[0]["url"] if urls else ""
        note_payload = {
            "report_id": rid, "site_url": url, "pdf_url": pdf_url or None, "urls": urls,
            "http_status": resolved["http_status"], "code": resolved["code"],
            "msg": resolved["msg"], "raw": resolved["raw"], "fetched_at": now_iso(),
        }
        if not urls:
            stats["failed"] += 1
            stats["quota_suspect"] += 1
            note_payload["reason"] = "no_pdf_url"
            download_note.write_text(json.dumps(note_payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
            failure(root, rid, url, resolved["http_status"],
                    "download 返回里没有 PDF 地址（可能是额度不足或字段变更）",
                    resolved["code"], resolved["msg"])
            print(f"[fetch] {rid} 无 PDF 地址；原始报文 code={resolved['code']} msg={resolved['msg']!r}",
                  file=sys.stderr)
            time.sleep(args.gap)
            continue
        download_note.write_text(json.dumps(note_payload, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        resp = fetch_pdf_bytes(probe, token, pdf_url)
        # 非 2xx 时 probe 把正文放在 body（str），2xx 时才放 raw（bytes）；两条都要能取证
        raw = resp.get("raw") or (resp.get("body") or "").encode("utf-8")
        if resp["status"] != 200 or not raw.startswith(b"%PDF-"):
            pdf_code, pdf_msg = parse_error_body(raw)
            note_payload["pdf_error"] = {"http_status": resp["status"], "code": pdf_code,
                                         "msg": pdf_msg, "bytes": len(raw),
                                         "error": resp.get("error", ""),
                                         "head": raw[:60].decode("utf-8", errors="replace")}
            download_note.write_text(json.dumps(note_payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
            stats["failed"] += 1
            failure(root, rid, url, resp["status"],
                    f"PDF 拉取失败（字段 {urls[0]['field']}，{len(raw)} 字节）: {str(pdf_msg)[:200]}",
                    pdf_code, str(pdf_msg), pdf_url)
            print(f"[fetch] {rid} PDF 拉取失败 http={resp['status']} code={pdf_code} "
                  f"msg={str(pdf_msg)[:80]!r}", file=sys.stderr)
            time.sleep(args.gap)
            continue
        tmp = dest.with_suffix(".pdf.part")
        tmp.write_bytes(raw)
        try:
            pages = pdf_pages(tmp)
            if pages <= 0:
                raise ValueError("页数为 0")
        except Exception as exc:  # noqa: BLE001 - 坏 PDF 留证据
            stats["failed"] += 1
            failure(root, rid, url, resp["status"],
                    f"PDF 校验失败: {type(exc).__name__}: {exc}", resolved["code"], resolved["msg"],
                    str(tmp))
            time.sleep(args.gap)
            continue
        os.replace(tmp, dest)
        for old in root.glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/{rid}-*.pdf"):
            if old.resolve() != dest.resolve():
                old.unlink(missing_ok=True)
                old.with_suffix(".meta.json").unlink(missing_ok=True)
                old.with_suffix(".download.json").unlink(missing_ok=True)
        meta = {
            "channel_id": "fxbaogao_vip",
            "report_id": int(rid),
            "title": title,
            "org": item.get("org", ""),
            "page_date": page_date,
            "source_url": url,
            "site_url": SITE_VIEW.format(report_id=rid),
            "pdf_url": pdf_url,
            "bytes": len(raw),
            "pages": pages,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "fetched_at": now_iso(),
            "fetch_mode": "api",
            "api": {"http_status": resolved["http_status"], "code": resolved["code"],
                    "msg": resolved["msg"], "download_note": str(download_note)},
        }
        dest.with_suffix(".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.out:
            with open(args.out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"report_id": rid, "title": title, "pdf": str(dest),
                                     "pages": pages, "site_url": meta["site_url"]},
                                    ensure_ascii=False) + "\n")
        stats["ok"] += 1
        stats["destinations"].append(str(dest))
        print(f"[fetch] {rid} {pages} 页 -> {dest}")
        time.sleep(args.gap)
    print(json.dumps(stats, ensure_ascii=False))
    return 0 if stats["failed"] == 0 else 1


# ---------------------------------------------------------------- 核对（官网详情页）
def _load_web():
    return _load_probe()


META_TAG = re.compile(r'<meta\s+[^>]*?(?:property|name)=["\']([^"\']+)["\'][^>]*?content=["\'](.*?)["\']',
                      re.I | re.S)
TITLE_TAG = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


def clean(fragment: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_detail_title(page_html: str) -> str:
    metas = {k.strip().lower(): clean(v) for k, v in META_TAG.findall(page_html)}
    if metas.get("og:title"):
        return metas["og:title"]
    match = TITLE_TAG.search(page_html)
    return clean(match.group(1)) if match else ""


def check_robots(probe, robots: dict, url: str) -> tuple[bool, str]:
    if robots.get("rules") is None:
        return False, f"robots.txt 不可读（HTTP {robots.get('robots_status')}）"
    return probe.robots_allows(robots["rules"], urlparse(url).path or "/")


def cmd_verify(args) -> int:
    root = Path(args.root)
    probe = prepare_probe(root, args.max_requests)
    bad = 0
    for meta_path in [Path(p) for p in args.meta.split(",") if p.strip()]:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rid = str(meta["report_id"])
        detail_url = DETAIL_BASE + rid
        robots = probe.get_robots_cached(detail_url)
        allowed, hit = check_robots(probe, robots, detail_url)
        if not allowed:
            print(f"[verify] {rid} robots 禁止详情页: {hit}")
            bad += 1
            continue
        resp = probe.http_get(detail_url)
        live = parse_detail_title(resp.get("body", "")) if resp["status"] == 200 else ""
        stored = (meta.get("title") or "").strip()
        same = bool(live) and (stored == live or stored in live or live in stored)
        pdf = meta_path.with_suffix("").with_suffix(".pdf")
        pages = pdf_pages(pdf) if pdf.exists() else -1
        print(f"[verify] {rid} HTTP {resp['status']} 落盘标题={stored[:60]!r} "
              f"重取标题={live[:60]!r} 一致={same} 页数={pages}")
        bad += 0 if same and pages > 0 else 1
        time.sleep(args.gap)
    return 0 if bad == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=str(CANON_ROOT), help="落盘根目录（或 FXBAOGAO_ROOT）")
    common.add_argument("--token-file", default=str(TOKEN_PATH), help="API Key 文件（单行、600；所在目录 700）")
    common.add_argument("--max-requests", type=int, default=200, help="单轮 HTTP 请求上限")
    common.add_argument("--gap", type=float, default=DEFAULT_GAP, help="同域名请求间隔秒（≥1.05）")

    parser = argparse.ArgumentParser(prog="fxbaogao_fulltext.py",
                                     description="发现报告官方 API 正文采集（WORK-623）")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-auth", parents=[common], help="验证 token（一次搜索，不扣下载额度）")
    check.add_argument("--keywords", default="储能", help="用于验证的搜索词")

    search = sub.add_parser("search", parents=[common], help="搜索候选 -> JSONL")
    search.add_argument("--keywords", default="")
    search.add_argument("--org", default="", help="机构，逗号分隔")
    search.add_argument("--time", default="last3mon", help="last3day|last7day|last1mon|last3mon|last1year")
    search.add_argument("--end-time", default="", help="毫秒时间戳")
    search.add_argument("--pages", type=int, default=1, help=f"1–{MAX_PAGES}（官方上限）")
    search.add_argument("--out", required=True)
    search.add_argument("--raw-dir", default="")

    fetch = sub.add_parser("fetch", parents=[common], help="按候选逐篇取 PDF（扣下载额度）")
    fetch.add_argument("--input", required=True)
    fetch.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    fetch.add_argument("--out", default="", help="成功清单 JSONL 追加输出")
    fetch.add_argument("--force", action="store_true", help="已存在也重新下载（会重复扣额度）")

    verify = sub.add_parser("verify", parents=[common], help="官网详情页标题核对 + 页数复核")
    verify.add_argument("--meta", required=True, help="逗号分隔的 meta.json 路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = {"check-auth": cmd_check_auth, "search": cmd_search,
               "fetch": cmd_fetch, "verify": cmd_verify}[args.command]
    return command(args)


if __name__ == "__main__":
    raise SystemExit(main())
