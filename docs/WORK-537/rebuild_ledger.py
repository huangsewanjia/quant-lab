"""WORK-537 审计 · 请求台账重建（工具缺陷修复用）。

背景：审计阶段跑频控回归测试时，测试脚本另起进程调用 http_get，触发 `_save_ledger()`
用内存中的 2 条记录覆盖了磁盘上 98 条台账。已修正 `_save_ledger()` 为追加式落盘。

本脚本按 probe_log.json 的 `tried` 列表重建**请求级**台账（URL + 状态码 + 字节量来自
探测记录本身），并与 audit_ledger.json（审计独立复跑的 12 次请求）合并。
重建条目标记 reconstructed=true、ts 置空——**时间戳不可恢复**，因此重建条目不参与
「同域间隔」判定（该判定只对有真实时间戳的条目有效）。

审计结论口径：请求总量以脚本内计数器输出为准（见 04-audit.md），台账重建仅用于
核对「打了哪些 URL、各自什么状态码」。
"""
import json
import os
import re

RAW = "/srv/share/quantdb/raw/foreign_research"
PROBE_DIR = os.path.join(RAW, "_probe")
LOG = os.path.join(PROBE_DIR, "probe_log.json")
LEDGER = os.path.join(PROBE_DIR, "request_ledger.json")
AUDIT_LEDGER = os.path.join(PROBE_DIR, "audit_ledger.json")

entries = []
n = 0
for rec in json.load(open(LOG, encoding="utf-8")):
    for t in rec.get("tried", []):
        n += 1
        url = t.get("url", "")
        dom = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        entries.append({"n": n, "url": url, "domain": dom.lower(), "status": t.get("status", ""),
                        "bytes": 0, "ts": "", "reconstructed": True, "kind": "page",
                        "source": f"probe_log[{rec['id']}]"})
    # 每个被触及过的 host 都会取一次 robots.txt（取不到就不发正文请求）
    hosts = []
    for t in rec.get("tried", []):
        h = re.sub(r"^https?://([^/]+).*$", r"\1", t.get("url", ""))
        if h and h not in hosts:
            hosts.append(h)
    for h in hosts:
        n += 1
        entries.append({"n": n, "url": f"https://{h}/robots.txt", "domain": h.lower(),
                        "status": rec.get("robots_status", "?"), "bytes": 0, "ts": "",
                        "reconstructed": True, "kind": "robots",
                        "source": f"probe_log[{rec['id']}]"})

audit = []
if os.path.exists(AUDIT_LEDGER):
    for e in json.load(open(AUDIT_LEDGER, encoding="utf-8")):
        n += 1
        e2 = dict(e)
        e2["n"] = n
        e2.setdefault("source", "audit_replay")
        audit.append(e2)

merged = entries + audit
json.dump(merged, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with_ts = [e for e in merged if e.get("ts")]
print(f"重建台账：{len(entries)} 条探测请求（reconstructed，无时间戳）"
      f" + {len(audit)} 条审计复跑请求（有时间戳）= {len(merged)} 条")
print(f"可参与同域间隔判定的条目：{len(with_ts)} 条")
print("注意：第一轮全量扫描的 75 次请求未进台账（脚本内计数器输出为唯一凭据），"
      "故请求总量以计数器为准 = 75 + 本台账可核对部分。")
