#!/usr/bin/env python3
"""WORK-557：段1 补充回帖的**更正通知**（逐年表两列标题写反）。

背景：上一条「段1 补充」里，逐年真缺口表的「其中停牌」与「真缺口」两列标题互换了。
数值本身来自同一份 crosscheck.json，聚合行（无行 26,442 / 停牌 26,397 / 真缺口 45）正确，
错的是逐年表的列标题 —— 已修 crosscheck.py 的位置解包并重跑，逐年与聚合一致后自洽。

本脚本输出更正通知正文；数字仍从 crosscheck.json 读。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    cc = json.load(open(os.path.join(HERE, "crosscheck.json"), encoding="utf-8"))
    A = cc["check_A_suspend"]["values"]
    yr = cc["check_A_suspend"]["by_year"]
    sum_su = sum(r["suspended"] for r in yr)
    sum_rg = sum(r["real_gap"] for r in yr)
    sum_nb = sum(r["no_bar"] for r in yr)

    L = []
    P = L.append
    P("## 更正｜段1 补充回帖「逐年真缺口」表的两个列标题写反了")
    P("")
    P("**更正内容（一句话）**：上一条「段1 补充」中，逐年表的 **「其中停牌」与「真缺口」两列标题互换了**；"
      "**数值全部正确**，错的是列标题 —— 已修脚本重跑，逐年与聚合一致后自洽。"
      "聚合行（无行 %s / 停牌 %s / 真缺口 %s）**未受影响**，结论不变。"
      % ("{:,}".format(A["no_bar"]), "{:,}".format(A["no_bar_and_susp"]),
         "{:,}".format(A["no_bar_not_susp"])))
    P("")
    P("**成因（不藏）**：`work557_crosscheck.py` 里逐年查询的 SQL 列序是 "
      "`year, member_days, no_bar, real_gap, suspended`，而位置解包写成 "
      "`for y, md, nb, su, rg in yearly` —— 两个标签被对调。已改为 "
      "`for y, md, nb, rg, su in yearly` 并重跑。")
    P("")
    P("**自查（重跑后逐年求和 vs 聚合）**")
    P("")
    P("| 项 | 逐年求和 | 聚合查询 | 一致 |")
    P("|---|---|---|---|")
    P("| `daily` 无行 | %s | %s | %s |"
      % ("{:,}".format(sum_nb), "{:,}".format(A["no_bar"]),
         "✅" if sum_nb == A["no_bar"] else "❌"))
    P("| 停牌（无行 ∧ `suspend_d` 有行） | %s | %s | %s |"
      % ("{:,}".format(sum_su), "{:,}".format(A["no_bar_and_susp"]),
         "✅" if sum_su == A["no_bar_and_susp"] else "❌"))
    P("| 真·数据缺口（无行 ∧ `suspend_d` 无行） | %s | %s | %s |"
      % ("{:,}".format(sum_rg), "{:,}".format(A["no_bar_not_susp"]),
         "✅" if sum_rg == A["no_bar_not_susp"] else "❌"))
    P("")
    P("**逐年真缺口（修正后的正确列序，以本表为准）**")
    P("")
    P("| 年 | member_days | daily 无行 | 其中停牌 | 真缺口 | 真缺口率 |")
    P("|---|---|---|---|---|---|")
    for r in yr:
        P("| %d | %s | %s | %s | %s | %.6f%% |"
          % (r["year"], "{:,}".format(r["member_days"]), "{:,}".format(r["no_bar"]),
             "{:,}".format(r["suspended"]), "{:,}".format(r["real_gap"]),
             100.0 * r["real_gap_rate"]))
    P("")
    P("**读法**：停牌逐年在 %s~%s 之间（2009–2018 高、2019 后骤降），"
      "**真缺口每年 0~%s 个 member-day、十六年合计 %s 个（%.6f%%）** —— "
      "「2009–2013 的 1.57%%~2.92%% 是停牌不是数据缺口」这一结论**不变且更硬**。"
      % ("{:,}".format(min(r["suspended"] for r in yr)),
         "{:,}".format(max(r["suspended"] for r in yr)),
         "{:,}".format(max(r["real_gap"] for r in yr)),
         "{:,}".format(sum_rg), 100.0 * sum_rg / A["member_days"]))
    P("")
    P("**受影响 / 不受影响**")
    P("")
    P("| 项 | 状态 |")
    P("|---|---|")
    P("| 核对 A 的聚合数与「结论（一句）」段 | **不受影响**（来自带别名的独立查询） |")
    P("| 核对 B 全部数字（涨跌停口径偏差） | **不受影响**（与逐年表无关） |")
    P("| 逐年真缺口表的列标题 | **已更正，以本表为准** |")
    P("| 停手条件① 判定（2009–2013 缺口 > 5%%） | **不变** |")
    P("| `WORK-557` 的口径处置（照抄不改、只披露） | **不变** |")
    P("")
    P("**产物（已更新）**")
    P("")
    P("| 文件 | sha256 |")
    P("|---|---|")
    for rel in ("experiments/WORK-557/crosscheck.json",
                "experiments/WORK-557/work557_crosscheck.py",
                "experiments/WORK-557/work557_comment_stage1b.py"):
        P("| `%s` | `%s` |" % (rel, sha256(os.path.join(REPO, rel.replace("/", os.sep)))))
    P("")
    P("```powershell")
    P("python -u experiments/WORK-557/work557_crosscheck.py   # 重跑即得本表")
    P("```")
    P("")
    P("本 run 已用 ≈22.9M / 硬闸 25M")
    P("")
    P("<!-- correction end -->")
    out = "\n".join(L)
    if len(sys.argv) > 1:
        dst = os.path.abspath(sys.argv[1])
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("wrote -> %s (%d chars)" % (dst, len(out)), file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
