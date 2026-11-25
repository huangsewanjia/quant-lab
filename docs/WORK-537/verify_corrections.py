"""WORK-537 补正 · 现场取证（只读本地文件，不发任何网络请求）。

用途：在改文字之前，独立复算三条补正的原始依据：
  ① 请求量到底可核对多少条；
  ② round-1 实际发出的请求（Barron's / FINRA 的真实状态码）；
  ③ 两轮 `tried` 的条目数与其中「未发出」的数量。
"""
import hashlib
import json
import os

RAW = "/srv/share/quantdb/raw/foreign_research"
P1 = os.path.join(RAW, "_probe", "probe_log_run1_robots_bug.json")
P2 = os.path.join(RAW, "_probe", "probe_log.json")
LED = os.path.join(RAW, "_probe", "request_ledger.json")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def tried_of(path, label):
    log = json.load(open(path, encoding="utf-8"))
    tried = [t for r in log for t in r.get("tried", [])]
    not_sent = [t for t in tried if t.get("status") == "robots禁"]
    sent = [t for t in tried if t.get("status") != "robots禁"]
    print(f"\n=== {label}（{os.path.basename(path)}）===")
    print(f"  渠道数 {len(log)}；tried 合计 {len(tried)}；其中 robots禁(未发出) {len(not_sent)}；"
          f"实际发出 {len(sent)}")
    hosts = {t["url"].split("//")[-1].split("/")[0] for t in tried}
    print(f"  触及 host 数 {len(hosts)}（robots.txt 抓取不计入 tried，故真实请求数更高）")
    for cid in ("barrons", "finra", "reuters", "bloomberg", "ft", "wsj"):
        for r in log:
            if r["id"] == cid:
                for t in r.get("tried", []):
                    print(f"  · [{cid}] status={t['status']!r:>12}  {t['url'][:88]}"
                          + (f"  err={t.get('error','')[:40]}" if t.get("error") else ""))
                break
    return len(tried), len(not_sent), len(sent)


t1, n1, s1 = tried_of(P1, "round 1（robots 判定生效前）")
t2, n2, s2 = tried_of(P2, "round 2（robots 优先）")

led = json.load(open(LED, encoding="utf-8"))
rec = [e for e in led if e.get("reconstructed")]
tsd = [e for e in led if e.get("ts")]
print("\n=== 请求台账 request_ledger.json ===")
print(f"  条目总数 {len(led)}；reconstructed {len(rec)}；带时间戳 {len(tsd)}")
print(f"  其中 kind=robots {sum(1 for e in led if e.get('kind')=='robots')}，"
      f"kind=page {sum(1 for e in led if e.get('kind')=='page')}")

audit = os.path.join(RAW, "_probe", "audit_ledger.json")
if os.path.exists(audit):
    al = json.load(open(audit, encoding="utf-8"))
    print(f"  audit_ledger.json {len(al)} 条（审计独立复跑）")

print("\n=== 可核对口径 ===")
print(f"  台账可核对条目：{len(led)}（{len(rec)} 重建 + {len(tsd)} 带时间戳）")
print(f"  实际发出下界：round1 {s1} + round2 {s2} + 审计 {len(tsd)} = {s1 + s2 + len(tsd)} 次")
print("  注：两轮 tried 均不含 robots.txt 抓取（每轮触及 ≥31 个 host），")
print("      故真实请求数 > 上述下界；185 属不可由台账复现的上界估计。")

print("\n=== 关键文件 sha256（用于「无新发请求」自证）===")
for p in (P1, P2, LED):
    print(f"  {sha(p)}  {p}")
