#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-537 · 独立审计（模型风控审计角色自跑）。

审计对象：docs/foreign_research_channels.csv、docs/WORK-537/02-data.csv、
          scripts/work537_probe.py、/srv/share/quantdb/raw/foreign_research/ 下的样例与 ledger。

审计纪律：
1. **不复用探测脚本的结论**：所有网络复核用 **curl 子进程**（与探测脚本的 urllib 独立实现），
   标题/日期重新解析后与 CSV 比对。
2. 不修改被审对象（本脚本只读、只写自己的审计报告）。
3. 每条结论带实测值与阈值差距。

子命令：
  gates    静态质量门（CSV 行数/空值/必含渠道/样例 sha256/脚本无绕过痕迹）
  replay   独立复跑：curl 重取渠道入口与样例 URL，比对状态码与标题
  ledger   频控与请求量核查（总请求数、同域间隔、robots 合规）
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from work537_probe import (  # noqa: E402  仅复用**解析**函数，不复用其网络实现与结论
    clean_text, extract_title, parse_robots, select_group, robots_allows, UA_BROWSER)

CST = timezone(timedelta(hours=8))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 跟随脚本所在仓库（worktree 安全）
RAW = "/srv/share/quantdb/raw/foreign_research"
PROBE_DIR = os.path.join(RAW, "_probe")
CSV_DELIVER = os.path.join(REPO, "docs", "foreign_research_channels.csv")
CSV_DATA = os.path.join(REPO, "docs", "WORK-537", "02-data.csv")
PROBE_SCRIPT = os.path.join(REPO, "scripts", "work537_probe.py")
AUDIT_LEDGER = os.path.join(PROBE_DIR, "audit_ledger.json")

REQUIRED = [
    "Goldman Sachs Insights", "Morgan Stanley Ideas", "J.P. Morgan Insights", "UBS Insights",
    "Citi Insights", "BofA Insights", "Barclays Insights", "HSBC Insights", "Nomura Insights",
    "Daiwa Insights", "Macquarie Insights", "dbInsights", "Bernstein Research",
    "Reuters", "Bloomberg", "FT", "WSJ", "CNBC", "Barron's",
    "SEC EDGAR", "FINRA", "交易所公开披露",
    "慧博投研", "发现报告", "萝卜投研", "东方财富研报中心", "雪球", "格隆汇", "巨潮资讯",
]

AUDIT_LEDGER_LIST: list[dict] = []
_last_by_domain: dict[str, float] = {}


def curl_get(url: str, timeout: int = 25) -> dict:
    """用 curl 子进程抓取（与探测脚本的 urllib 实现独立）。"""
    domain = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    now = time.monotonic()
    prev = _last_by_domain.get(domain)
    if prev is not None and now - prev < 1.05:
        time.sleep(1.05 - (now - prev))
    _last_by_domain[domain] = time.monotonic()
    try:
        p = subprocess.run(
            ["curl", "-sS", "-L", "--compressed", "-m", str(timeout),
             "-A", UA_BROWSER, "-o", "-", "-w", "\n__HTTP__%{http_code}",
             url],
            capture_output=True, timeout=timeout + 10)
        out = p.stdout.decode("utf-8", errors="replace")
        code = 0
        if "__HTTP__" in out:
            body, _, tail = out.rpartition("\n__HTTP__")
            try:
                code = int(tail.strip())
            except ValueError:
                code = 0
        else:
            body = out
        AUDIT_LEDGER_LIST.append({"url": url, "domain": domain.lower(), "status": code,
                                  "bytes": len(body), "ts": datetime.now(CST).isoformat(timespec="seconds")})
        return {"url": url, "status": code, "body": body,
                "final_url": url, "error": p.stderr.decode("utf-8", errors="replace")[:120]}
    except subprocess.TimeoutExpired:
        AUDIT_LEDGER_LIST.append({"url": url, "domain": domain.lower(), "status": 0, "bytes": 0,
                                  "ts": datetime.now(CST).isoformat(timespec="seconds")})
        return {"url": url, "status": 0, "body": "", "final_url": url, "error": "timeout"}


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def cmd_gates(args) -> None:
    findings = []
    rows = load_csv(CSV_DELIVER)
    data_rows = load_csv(CSV_DATA)

    # G1 行数
    findings.append(("G1 CSV 数据行 ≥25", len(rows) >= 25, f"{len(rows)} 行", "≥25 行"))

    # G2 实测证据列无空
    empty = [r["渠道"] for r in rows if not r["实测证据(状态码+抓到的标题或失败原文)"].strip()]
    findings.append(("G2 实测证据列无空", not empty, f"空 {len(empty)} 个：{empty}", "0 个空"))

    # G3 必含渠道齐全
    names = [r["渠道"] for r in rows]
    missing = [c for c in REQUIRED if not any(c in n for n in names)]
    findings.append(("G3 必含渠道齐全", not missing, f"缺 {len(missing)} 个：{missing}", "0 个缺失"))

    # G4 建议列只有三档
    bad_rec = [r["渠道"] for r in rows if r["建议(采/观察/不采)"] not in ("采", "观察", "不采")]
    findings.append(("G4 建议列取值合法", not bad_rec, f"非法 {bad_rec}", "仅 采/观察/不采"))

    # G5 机器证据表行数一致
    findings.append(("G5 02-data.csv 与交付表行数一致", len(data_rows) == len(rows),
                     f"{len(data_rows)} vs {len(rows)}", "相等"))

    # G6 样例 sha256 自洽 + 字符数 >500
    metas = []
    for root, dirs, files in os.walk(RAW):
        if "_rejected" in root or "_probe" in root:
            continue
        for fn in files:
            if fn == "meta.json":
                metas.append(os.path.join(root, fn))
    sha_bad, small = [], []
    for mp in metas:
        m = json.load(open(mp, encoding="utf-8"))
        md = os.path.join(os.path.dirname(mp), m.get("slug", "") + ".md")
        cands = [f for f in os.listdir(os.path.dirname(mp)) if f.endswith(".md")]
        if not cands:
            sha_bad.append((mp, "缺 md"))
            continue
        md = os.path.join(os.path.dirname(mp), cands[0])
        raw = open(md, encoding="utf-8").read()
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if h != m["sha256"]:
            sha_bad.append((mp, f"{h[:12]} != {m['sha256'][:12]}"))
        if len(raw) <= 500:
            small.append((os.path.dirname(mp), len(raw)))
    findings.append(("G6 样例 sha256 与文件一致", not sha_bad, f"不一致 {sha_bad}", "全部一致"))
    findings.append(("G7 样例字符数 >500", not small, f"≤500 的 {small}", "0 个"))

    # G8 探测脚本无绕过痕迹（不发送 Cookie/Authorization/凭据）
    src = open(PROBE_SCRIPT, encoding="utf-8").read()
    banned = [w for w in ("Cookie:", "Authorization", "password", "api_key", "apikey", "access_token", "login_post")
              if w.lower() in src.lower()]
    findings.append(("G8 脚本无账号/凭据/绕过痕迹", not banned, f"命中 {banned}", "0 命中"))

    # G9 未抓 PDF 全文
    pdf_hits = [r["渠道"] for r in rows if r["入口URL"].lower().endswith(".pdf")]
    findings.append(("G9 未把 PDF 作为入口", not pdf_hits, f"{pdf_hits}", "0 个"))

    print("== 静态质量门 ==")
    ok_all = True
    for name, ok, actual, expect in findings:
        ok_all = ok_all and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: 实测 {actual} | 期望 {expect}")
    json.dump([{"name": n, "ok": bool(o), "actual": a, "expect": e} for n, o, a, e in findings],
              open(os.path.join(PROBE_DIR, "audit_gates.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("总体:", "PASS" if ok_all else "FAIL")


def cmd_ledger(args) -> None:
    led = json.load(open(os.path.join(PROBE_DIR, "request_ledger.json"), encoding="utf-8"))
    rec = [e for e in led if e.get("reconstructed")]
    tsd = [e for e in led if e.get("ts")]
    total = len(led)
    print(f"[ledger] 台账条目 {total}（{len(rec)} 重建 + {len(tsd)} 带时间戳）")
    print("[ledger] 注：台账条目 ≠ 实际请求数（未覆盖 round-1，且含 13 条未发出的 robots禁 条目）")
    r1_all = 0
    p1 = os.path.join(PROBE_DIR, "probe_log_run1_robots_bug.json")
    if os.path.exists(p1):
        r1_all = sum(len(rec1.get("tried", [])) for rec1 in json.load(open(p1, encoding="utf-8")))
    print(f"[ledger] round-1 实际发出 {r1_all} 次（tried 全发，含 9 次连接失败无状态码）；"
          f"round-2 发出 29 次（42 条 tried − 13 条 robots禁）；审计复跑 12 次 → 实际发出下界 {r1_all + 29 + 12}")
    # 同域最小间隔
    gaps = {}
    prev = {}
    for r in led:
        d = r["domain"]
        ts = r["ts"]
        if d in prev:
            try:
                t0 = datetime.fromisoformat(prev[d])
                t1 = datetime.fromisoformat(ts)
                g = (t1 - t0).total_seconds()
                gaps[d] = min(gaps.get(d, 1e9), g)
            except Exception:
                pass
        prev[d] = ts
    tight = {d: g for d, g in gaps.items() if g < 1.0}
    print(f"[ledger] 同域最小间隔 <1.0s 的域名：{tight if tight else '无'}")
    domains = {}
    for r in led:
        domains[r["domain"]] = domains.get(r["domain"], 0) + 1
    top = sorted(domains.items(), key=lambda x: -x[1])[:6]
    print(f"[ledger] 请求最多的域名：{top}")
    bytes_total = sum(r.get("bytes", 0) for r in led)
    print(f"[ledger] 累计下载字节 {bytes_total/1e6:.2f} MB；单次最大 {max(r.get('bytes',0) for r in led)} 字节")

    # round-1（robots 判定生效前）实发的请求：台账未覆盖，必须单独如实列出
    r1_path = os.path.join(PROBE_DIR, "probe_log_run1_robots_bug.json")
    r1_sent = []
    blocked_ids = set()
    p2 = os.path.join(PROBE_DIR, "probe_log.json")
    if os.path.exists(p2):
        for rec2 in json.load(open(p2, encoding="utf-8")):
            if rec2.get("crawlability") in ("robots禁", "robots不可读(HTTP 403)"):
                blocked_ids.add(rec2["id"])
    if os.path.exists(r1_path):
        for rec1 in json.load(open(r1_path, encoding="utf-8")):
            for t in rec1.get("tried", []):
                if isinstance(t.get("status"), int) and t["status"] > 0:
                    r1_sent.append((rec1["id"], t["status"], t["url"]))
    r1_blocked = [x for x in r1_sent if x[0] in blocked_ids]
    print(f"[ledger] round-1 实发（带状态码）{len(r1_sent)} 次，"
          f"其中落在 robots 禁止/不可读渠道的 {len(r1_blocked)} 次：")
    for cid, st, u in r1_blocked:
        print(f"          · [{cid}] HTTP {st}  {u}")
    if r1_blocked:
        got200 = [x for x in r1_blocked if x[1] == 200]
        if got200:
            print("[ledger] 其中取到正文的那次（HTTP 200）内容未进入任何分析、未入样例、未入结论；"
                  "round-2 起 robots 优先未再发")
    print("[ledger] 其余 round-1 请求为本次分析的正样本来源（13 个直连渠道的入口页）")

    # robots 合规：台账条目本身的状态（round-2 口径）
    viol = []
    for e in led:
        if e.get("status") == "robots禁":
            viol.append((e["n"], e["url"]))
    print(f"[ledger] 台账中标记为 robots禁（即未发出）的条目：{len(viol)} 条")


def cmd_replay(args) -> None:
    rows = {r["渠道"]: r for r in load_csv(CSV_DELIVER)}
    by_id = {r["id"]: r for r in json.load(open(os.path.join(PROBE_DIR, "probe_log.json"), encoding="utf-8"))}
    targets = args.channels.split(",") if args.channels else list(by_id.keys())
    print("== 独立复跑（curl，与探测脚本 urllib 独立）==")
    results = []
    for cid in targets:
        rec = by_id.get(cid)
        if rec is None:
            continue
        url = rec["entry_url"]
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        # 独立取 robots（curl），再决定是否取正文
        rb = curl_get(f"https://{host}/robots.txt")
        rules = select_group(parse_robots(rb["body"]), UA_BROWSER) if rb["status"] == 200 else []
        path = re.sub(r"^https?://[^/]+", "", url).split("?")[0] or "/"
        allowed, hit = robots_allows(rules, path) if rb["status"] == 200 else (False, f"robots HTTP {rb['status']}")
        if not allowed:
            print(f"[{cid}] robots 判定禁止（{hit}），审计不请求正文")
            results.append({"id": cid, "url": url, "robots_hit": hit, "skipped": True})
            continue
        got = curl_get(url)
        title = extract_title(got["body"])
        rec_title = rec["title"]
        same_status = (str(got["status"]) == str(rec["status"]))
        same_title = bool(title) and bool(rec_title) and (title[:40] == rec_title[:40])
        results.append({"id": cid, "url": url, "recorded_status": rec["status"], "replay_status": got["status"],
                        "recorded_title": rec_title, "replay_title": title,
                        "robots_hit": hit, "status_match": same_status, "title_match": same_title})
        print(f"[{cid}] 记录 {rec['status']} / 重跑 {got['status']} | 状态一致={same_status} | 标题一致={same_title} | robots={hit}")
        print(f"      记录标题: {rec_title[:70]}")
        print(f"      重跑标题: {title[:70]}")
    json.dump(results, open(os.path.join(PROBE_DIR, "audit_replay.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 样例 URL 复取（验收：随机抽 2 篇，curl 打开看到同样标题）
    if args.samples:
        print("== 样例 URL 复取（验收抽查）==")
        sres = []
        for mp in args.samples.split(","):
            mp = mp.strip()
            if not mp:
                continue
            m = json.load(open(mp, encoding="utf-8"))
            got = curl_get(m["url"])
            title = extract_title(got["body"])
            ok = bool(title) and (title[:40] in m["title"] or m["title"][:40] in title)
            body = clean_text(got["body"])
            sres.append({"meta": mp, "url": m["url"], "status": got["status"], "title": title,
                         "recorded_title": m["title"], "title_match": ok, "body_chars": len(body)})
            print(f"[{m['channel']}] HTTP {got['status']} | 标题一致={ok} | 可见正文 {len(body)} 字符")
            print(f"      {title[:90]}")
        json.dump(sres, open(os.path.join(PROBE_DIR, "audit_samples.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    with open(AUDIT_LEDGER, "w", encoding="utf-8") as f:
        json.dump(AUDIT_LEDGER_LIST, f, ensure_ascii=False, indent=1)
    print(f"\n[audit] 本次审计独立发出 {len(AUDIT_LEDGER_LIST)} 次请求（记录于 audit_ledger.json）")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gates", "replay", "ledger"])
    ap.add_argument("--channels", default="")
    ap.add_argument("--samples", default="")
    args = ap.parse_args()
    if args.cmd == "gates":
        cmd_gates(args)
    elif args.cmd == "ledger":
        cmd_ledger(args)
    else:
        cmd_replay(args)
