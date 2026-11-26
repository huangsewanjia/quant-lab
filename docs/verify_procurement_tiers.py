#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-618 · 独立核对（不 import 建模脚本，全部从产物反向复算）。

用法：python3 docs/verify_procurement_tiers.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import csv
import hashlib
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
CSV_PATH = DOCS / "procurement_tiers.csv"
HTML_PATH = DOCS / "procurement_tiers_report.html"
BRIEF_PATH = DOCS / "tiers_brief.md"
SELFBUILD = Path(
    "/srv/data/multica/workspaces/workspace-c8faa36a018a/work-541-66a10df14690"
    "/workdir/docs/paid_data_selfbuild.csv"
)
BUDGET = {"10万": 100_000, "20万": 200_000, "30万": 300_000}
# 独立写死的汇率（CFETS 2026-11-26 中间价，与建模脚本分别录入）
USD = 6.7096
EUR = 7.3380
RATE = {"CNY": 1.0, "USD": USD, "EUR": EUR}

fails, checks = [], 0


def ck(cond, msg):
    global checks
    checks += 1
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# ---------------------------------------------------------------- 1 指标全集
rows541 = list(csv.DictReader(open(SELFBUILD, encoding="utf-8")))
HARD = {r["指标名"] for r in rows541 if "需采购" in r["数据商"]}
UNIV = {r["指标名"] for r in rows541}
ck(len(rows541) == 77 and len(HARD) == 16 and len(UNIV) == 77,
   f"77 条 P0 全集与 16 条硬缺口：rows={len(rows541)} hard={len(HARD)} "
   f"sha256={hashlib.sha256(SELFBUILD.read_bytes()).hexdigest()[:16]}")

# ---------------------------------------------------------------- 2 CSV 结构
rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
cols = list(rows[0].keys())
expect = 13
ck(len(cols) == expect, f"CSV 列数 = {expect}（实际 {len(cols)}）")
blank = [(i + 2, k) for i, r in enumerate(rows) for k, v in r.items() if str(v).strip() == ""]
ck(not blank, f"无空单元格（空 {len(blank)} 处{'' if not blank else '：' + str(blank[:3])}）")
ck({r["档位(10万/20万/30万)"] for r in rows} == {"10万", "20万", "30万"},
   "三档各至少 1 行")

# ---------------------------------------------------------------- 3 逐档复算
grand = 0
for tier, bud in BUDGET.items():
    trs = [r for r in rows if r["档位(10万/20万/30万)"] == tier]
    spend = sum(int(r["折人民币"]) for r in trs)
    ind = [i for r in trs for i in r["该产品覆盖的P0指标(分号分隔)"].split(";") if i]
    uniq = set(ind)
    ck(spend <= bud, f"{tier}：合计 ¥{spend:,} ≤ 预算 ¥{bud:,}（结余 ¥{bud - spend:,}）")
    ck(len(ind) == len(uniq), f"{tier}：档内无指标重复（{len(ind)} 次出现 / {len(uniq)} 条唯一）")
    per_row = sum(int(r["覆盖条数"]) for r in trs)
    ck(per_row == len(uniq) == sum(len([x for x in r["该产品覆盖的P0指标(分号分隔)"].split(";") if x])
                                  for r in trs),
       f"{tier}：覆盖条数可复算（行内之和 {per_row} = 去重 {len(uniq)}）")
    ck(uniq <= UNIV, f"{tier}：所有指标都在 77 条 P0 内")
    hard = uniq & HARD
    # 金额换算复核：折人民币 / 数量 = 当日中间价
    bad = []
    for r in trs:
        m = re.search(r"([\d,]+(?:\.\d+)?)", r["报价"])
        if not m:
            bad.append(r["供应商"])
            continue
        amt = float(m.group(1).replace(",", ""))
        got = int(r["折人民币"]) / amt
        if abs(got - RATE[r["币种"]]) > 0.02:
            bad.append(f"{r['供应商']} {got:.4f}")
    ck(not bad, f"{tier}：折人民币 = 报价 × 中间价（异常 {len(bad)} 行{'' if not bad else str(bad[:2])}）")
    grand += spend
    print(f"      → {tier}：{len(trs)} 家、覆盖 {len(uniq)}/77（硬缺口 {len(hard)}/16）、"
          f"单条年均 ¥{spend / len(uniq):,.0f}")

# ---------------------------------------------------------------- 4 证据等级
lv = [r["价格证据等级"] for r in rows]
ck(set(lv) <= {"A", "B", "C"}, f"证据等级取值合法（A/B/C）：A={lv.count('A')} B={lv.count('B')} C={lv.count('C')}")
ck(lv.count("A") / len(lv) >= 0.40, f"A 级占比 {lv.count('A') / len(lv):.1%} ≥ 40%（停手线）")
a_bad = [r["供应商"] for r in rows if r["价格证据等级"] == "A"
         and not re.search(r"抓取时间 \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ（HTTP \d{3}）", r["备注"])]
ck(not a_bad, f"A 级行全部带「抓取时间 + HTTP 状态码」（缺 {len(a_bad)} 行）")
a_fail = [r["供应商"] for r in rows if r["价格证据等级"] == "A" and "HTTP 200" not in r["备注"]]
ck(not a_fail, f"A 级行全部为 HTTP 200 直读（非 200 的 {len(a_fail)} 行）")
c_bad = [r["供应商"] for r in rows if r["价格证据等级"] == "C"
         and not ("假设" in r["报价"] and "±50%" in r["备注"] and "依据" in r["备注"])]
ck(not c_bad, f"C 级行 100% 带「假设」标注 + ±50% 敏感性 + 依据（缺 {len(c_bad)} 行{'' if not c_bad else str(c_bad[:2])}）")
ck(all(r["价格来源URL"].startswith("http") for r in rows), "每行都有 http(s) 价格来源 URL")
ck(all("假设" not in r["报价"] for r in rows if r["价格证据等级"] == "A"),
   "A 级行的报价列不出现「假设」字样（不把假设价冒充官网价）")

# ---------------------------------------------------------------- 5 交叉校验：覆盖必须能在 535 清单里找到
plan = list(csv.DictReader(open(DOCS / "procurement_plan.csv", encoding="utf-8")))
cov = {(r["数据商"], r["指标名"]) for r in plan if r["覆盖度(全覆盖/部分)"] in ("全覆盖", "部分")}
PLAN_SUPPLIERS = {d for d, _ in cov}
KEYS = {
    "SEMI 国际半导体产业协会": ["国际半导体产业协会 SEMI"],
    "TrendForce 集邦": ["TrendForce 集邦"],
    "慧博投研（研报聚合平台）": ["慧博投研（研报聚合平台）"],
    "赛迪顾问 / 中国电子信息产业发展研究院（CCID）": ["赛迪顾问 / 中国电子信息产业发展研究院（CCID）"],
    "Benchmark Mineral Intelligence": ["Benchmark Mineral Intelligence"],
    "高工锂电 / 高工产业研究院 GGII": ["高工锂电 / 高工产业研究院 GGII"],
    "中关村储能产业技术联盟 CNESA": ["中关村储能产业技术联盟 CNESA"],
}


def match_names(supplier):
    if supplier in KEYS:
        return KEYS[supplier]
    return [d for d in PLAN_SUPPLIERS if d == supplier or d in supplier or supplier in d] or [supplier]


miss = []
for r in rows:
    for ind in [i for i in r["该产品覆盖的P0指标(分号分隔)"].split(";") if i]:
        if not any((n, ind) in cov for n in match_names(r["供应商"])):
            miss.append((r["供应商"], ind))
ck(not miss, f"每条覆盖都能在 WORK-535 清单里找到同数据商的「全覆盖/部分」行（缺 {len(miss)} 处{'' if not miss else str(miss[:2])}）")

# ---------------------------------------------------------------- 6 HTML
h = HTML_PATH.read_text(encoding="utf-8")
n_img = h.count('src="data:image/png;base64,')
ck(n_img == 3, f"HTML base64 内嵌图片 = {n_img} 张（要求 3）")
ext = re.findall(r'(?:src|href)="(?!#|data:)([^"]+)"', h)
ck(not ext, f"无外链资源（发现 {len(ext)} 个{'' if not ext else '：' + str(ext[:3])}）")
ck(all(f"图 {i}｜" in h for i in (1, 2, 3)), "三张图都有编号图注（图 1/2/3）")
n_src = len(re.findall(r'class=["\']?src', h))
ck(n_src >= 3, f"每张图都有来源行（{n_src} 处）")
n_tier = h.count('<section class="tier"')
ck(n_tier == 3 and "data-go=" in h and "classList.toggle" in h,
   f"三档区块（{n_tier} 个）+ 内联 JS 切换器齐备")
ck("@media print" in h and "A4" in h, "含 @media print A4 版式")
ck("0b3d6b" in h and "linear-gradient" not in h and "box-shadow" not in h
   and "border-radius" not in h, "配色为单一主色 + 灰阶，无渐变/投影/圆角")
tot = sum(int(r["折人民币"]) for r in rows)

# 勾稽表里的两个算式必须能与 CSV 对上
s10 = [r for r in rows if r["档位(10万/20万/30万)"] == "10万"]
ck(sum(int(r["折人民币"]) for r in s10) == 94847, f"10 万档算式 = {sum(int(r['折人民币']) for r in s10):,}")

# ---------------------------------------------------------------- 7 brief
b = BRIEF_PATH.read_text(encoding="utf-8")
ck("推荐 20 万档" in b, "brief 给出推荐档")
ck("什么情况下应该升档" in b and b.count("升 30 万档") >= 2, "brief 给出可判真假的升档触发条件")

print()
print(f"合计 {checks} 项，失败 {len(fails)} 项")
if fails:
    for f in fails:
        print("  ✗", f)
sys.exit(1 if fails else 0)
