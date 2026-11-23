"""WORK-537 审计 · 频控回归测试。

问题（审计阶段发现）：初版节流按「请求**开始**时刻」记间隔，同域上一次请求耗时较长时，
「完成 → 下一次开始」的间隔可低至 0.25s，严于 issue「同一域名 ≤1 请求/秒」的口径。
修正后：同域上一次请求**完成**后再等 ≥1.05s。

本测试用 2 次同域请求，实测「完成 → 下一次开始」间隔，断言 ≥1.0s。
"""
import os
import sys
import time

sys.path.insert(0, "/srv/share/quantdb/code/quant-lab/scripts")
import work537_probe as P  # noqa: E402

URL = "https://www.ubs.com/robots.txt"   # 只取 robots.txt，不取正文

t0 = time.monotonic()
r1 = P.http_get(URL)
t1 = time.monotonic()                     # 第一次请求完成
r2 = P.http_get(URL)
t2 = time.monotonic()                     # 第二次请求完成

# 第二次请求的开始时刻 ≈ t1 + 节流等待 + 建连时间；用「总耗时 - 第二次请求耗时」近似
gap = (t2 - t1) - (t2 - t1)  # 占位，下面用 ledger 的 elapsed 反推
# 更精确：用两次请求完成时刻差 − 第二次请求自身耗时
second_elapsed = P.LEDGER[-1]["elapsed_s"]
first_elapsed = P.LEDGER[-2]["elapsed_s"]
gap = (t2 - t1) - second_elapsed           # ≈ 第一次完成 → 第二次开始
print(f"第一次: HTTP {r1['status']} 耗时 {first_elapsed}s")
print(f"第二次: HTTP {r2['status']} 耗时 {second_elapsed}s")
print(f"实测「完成 → 下一次开始」间隔 = {gap:.2f}s  阈值 ≥1.00s")
print("结论:", "PASS" if gap >= 1.0 else "FAIL")
