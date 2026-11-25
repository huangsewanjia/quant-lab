#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 · 把登记行与详情卡粘进 experiments/registry.md（幂等，可重复跑）。

输入：experiments/WORK-566/registry_row.md（由 work566_registry_row.py 生成，数字只抄审计产出）
输出：experiments/registry.md
      一、登记表（主表）→ 插在「（模板行）」之前；已有 `| `WORK-566` |` 行则原地替换
      二、实验详情卡   → 追加到该节末尾；已有 `### WORK-566 ·` 小节则原地替换

纪律：不改动其它实验的任何行；只动 WORK-566 自己的行/小节。
运行：python scripts/work566_registry_paste.py
"""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "experiments", "WORK-566")
REG = os.path.join(REPO, "experiments", "registry.md")
SRC = os.path.join(OUT, "registry_row.md")

ROW_PREFIX = "| `WORK-566` |"
CARD_PREFIX = "### WORK-566 ·"


def main():
    if not os.path.exists(SRC):
        raise SystemExit(f"缺 {SRC}，先跑 scripts/work566_registry_row.py")
    src = open(SRC, encoding="utf-8").read()
    lines = src.splitlines()

    row = next((l for l in lines if l.startswith(ROW_PREFIX)), None)
    if row is None:
        raise SystemExit("registry_row.md 里找不到登记行")
    # 详情卡：从「## 详情卡」之后的第一个 "### WORK-566" 行到下一个 "## " 之前
    try:
        ci = next(i for i, l in enumerate(lines) if l.startswith("### WORK-566 ·"))
    except StopIteration:
        raise SystemExit("registry_row.md 里找不到详情卡")
    cend = len(lines)
    for j in range(ci + 1, len(lines)):
        if lines[j].startswith("## "):
            cend = j
            break
    card = "\n".join(lines[ci:cend]).rstrip() + "\n"

    reg = open(REG, encoding="utf-8").read()
    reg_lines = reg.splitlines()

    # ---- 主表 ----
    out = []
    replaced = False
    for l in reg_lines:
        if l.startswith(ROW_PREFIX):
            if not replaced:
                out.append(row)
                replaced = True
            continue
        out.append(l)
    if not replaced:
        # 插在「（模板行）」之前
        idx = next((i for i, l in enumerate(out) if l.startswith("| （模板行）")), None)
        if idx is None:
            raise SystemExit("registry.md 里找不到「（模板行）」锚点")
        out.insert(idx, row)

    # ---- 详情卡 ----
    txt = "\n".join(out)
    if CARD_PREFIX in txt:
        s = txt.index(CARD_PREFIX)
        e = txt.find("\n### ", s + 1)
        e2 = txt.find("\n## ", s + 1)
        ends = [x for x in (e, e2) if x != -1]
        e = min(ends) if ends else len(txt)
        txt = txt[:s] + card + txt[e + 1:]
    else:
        # 追加到「二、实验详情卡」小节末尾（下一个 "## " 之前）
        m = re.search(r"^## 二、实验详情卡.*$", txt, flags=re.M)
        if not m:
            raise SystemExit("registry.md 里找不到「二、实验详情卡」小节")
        rest = txt[m.end():]
        nxt = re.search(r"^## ", rest, flags=re.M)
        pos = m.end() + (nxt.start() if nxt else len(rest))
        txt = txt[:pos] + "\n" + card + txt[pos:]

    with open(REG, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"pasted into {REG}")
    print(f"  登记行 {len(row)} 字符；详情卡 {len(card)} 字符")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
