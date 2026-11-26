#!/usr/bin/env python3
"""生成 docs/alt_gap_ledger.csv：54 条 B 类里未入选 Top-10 的 44 条差距台账。

输入：
  WORK-541 判定表（默认 /srv/data/multica/workspaces/workspace-c8faa36a018a/work-541-66a10df14690/workdir/docs/paid_data_selfbuild.csv）
  scripts/alt/selection/ledger_map.csv（逐行人写的近似度/原因/触发条件）
输出：
  docs/alt_gap_ledger.csv
校验：行数必须 = 54 - 10 = 44；无空单元格；每条「未做的原因」不得是空话。
"""
from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DOCS = os.path.join(REPO, "docs")
SRC = os.environ.get(
    "WORK541_CSV",
    "/srv/data/multica/workspaces/workspace-c8faa36a018a/work-541-66a10df14690/workdir/docs/paid_data_selfbuild.csv")
SELECTED = {10, 4, 2, 67, 47, 57, 55, 54, 53, 20}
BANNED = {"暂不处理", "优先级低", "以后再说", ""}


def main() -> int:
    with open(SRC, newline="", encoding="utf-8") as f:
        rows541 = list(csv.DictReader(f))
    with open(os.path.join(HERE, "ledger_map.csv"), newline="", encoding="utf-8") as f:
        mp = {int(r["row_idx"]): r for r in csv.DictReader(f)}

    out = []
    for i, r in enumerate(rows541):
        if r["判定(A/B/C)"] != "B" or i in SELECTED:
            continue
        m = mp.get(i)
        if m is None:
            print(f"FAIL 第 {i} 行缺 ledger_map 条目：{r['指标名']}", file=sys.stderr)
            return 1
        out.append({
            "指标": r["指标名"],
            "行业": r["行业"],
            "区域": r["区域"],
            "WORK541行号": i,
            "判定": r["判定(A/B/C)"],
            "对应源数据集": m["dataset"],
            "未做的原因": m["reason"],
            "若能近似到几成": m["approx"],
            "差在哪(频率/滞后/覆盖/口径)": r["差距量化"],
            "将来做的触发条件": m["trigger"],
            "附带覆盖脚本": m["byproduct"] or "无",
            "541自建人日": r["自建工时(人日)"],
        })

    if len(out) != 44:
        print(f"FAIL 台账行数 {len(out)} != 44", file=sys.stderr)
        return 1
    for o in out:
        for k, v in o.items():
            if str(v).strip() in BANNED:
                print(f"FAIL 空/空话单元格：{o['指标']} / {k}", file=sys.stderr)
                return 1

    dst = os.path.join(DOCS, "alt_gap_ledger.csv")
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    by = sum(1 for o in out if o["附带覆盖脚本"] != "无")
    print(f"台账 {len(out)} 行 → {dst}；其中 {by} 行由入选脚本附带覆盖，{len(out) - by} 行零脚本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
