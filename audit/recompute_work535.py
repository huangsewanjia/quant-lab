#!/usr/bin/env python3
"""WORK-535 · 独立复算闸门 —— 把采购决策摘要里的每个数字重新算一遍。

契约（同 audit/README.md）：
  * 本脚本**不 import 任何建模/汇总阶段的函数**，不复用 experiments/WORK-535/final_stats.py
    或 build_plan.py 的任何逻辑；它自己从 `docs/procurement_plan.csv` + DuckDB 重新推导。
  * 它再把 `docs/procurement_brief.md` 里写出来的数字**解析出来**，逐个与复算值比对；
    对不上就是 blocking finding。
  * 退出码 0 = 无 blocking finding。

用法：
  python audit/recompute_work535.py                 # 复算 + 比对
  python audit/recompute_work535.py --out <json>    # 指定 findings 输出路径

产物： experiments/WORK-535/audit_findings.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

DB = '/srv/share/quantdb/db/quantdb.duckdb'
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN_CSV = os.path.join(REPO, 'docs', 'procurement_plan.csv')
BRIEF_MD = os.path.join(REPO, 'docs', 'procurement_brief.md')
DEFAULT_OUT = os.path.join(REPO, 'experiments', 'WORK-535', 'audit_findings.json')

C_COV = '覆盖度(全覆盖/部分)'
C_ADV = '建议(买/等/不买)'
C_VEND = '数据商'
C_IND = '指标名'
C_FREE = '免费替代源'
C_PRICE = '公开价格'
COVERED = ('全覆盖', '部分')


def load_catalog():
    import duckdb
    con = duckdb.connect(DB, read_only=True)
    buy = {r[0] for r in con.execute(
        'select "指标名" from data_catalog where "缺口优先级"=\'P0\' '
        'and "是否公开可获取"=\'否（需采购）\'').df().itertuples(index=False)}
    part = {r[0] for r in con.execute(
        'select "指标名" from data_catalog where "缺口优先级"=\'P0\' '
        'and "是否公开可获取"=\'部分免费\'').df().itertuples(index=False)}
    con.close()
    return buy, part


def recompute():
    """独立复算：覆盖度、建议分布、贪心集合覆盖、联合覆盖。"""
    buy, part = load_catalog()
    rows = list(csv.DictReader(open(PLAN_CSV, encoding='utf-8')))

    cov = defaultdict(lambda: {'hard': set(), 'p0': set()})
    adv_rows = {'买': 0, '等': 0, '不买': 0}
    adv_hard = {'买': 0, '等': 0, '不买': 0}
    adv_part = {'买': 0, '等': 0, '不买': 0}
    cov_values = defaultdict(int)
    price_rows = 0
    free_alt_ok = set()
    hard_with_vendor = set()
    combos = set()
    combos_hard = set()

    for r in rows:
        name = (r[C_IND] or '').strip()
        vendor = (r[C_VEND] or '').strip()
        is_hard = name in buy
        for v in [x.strip() for x in vendor.split(' + ')]:
            combos.add((name, v))
            if is_hard:
                combos_hard.add((name, v))
        c = (r[C_COV] or '').strip()
        cov_values[c] += 1
        if c in COVERED:
            for v in [x.strip() for x in vendor.split(' + ')]:
                cov[v]['hard' if is_hard else 'p0'].add(name)
        a = (r[C_ADV] or '').strip()
        if a in adv_rows:
            adv_rows[a] += 1
            (adv_hard if is_hard else adv_part)[a] += 1
        p = r[C_PRICE] or ''
        if (any(c in p for c in '$€¥') or any(c in p for c in ('USD', 'EUR', 'RMB'))) \
                and '未公开' not in p:
            price_rows += 1
        if name in part and (r[C_FREE] or '').strip():
            free_alt_ok.add(name)
        if name in buy:
            hard_with_vendor.add(name)

    def greedy(target, pool, key, k=3):
        cover, picked = set(), []
        for _ in range(k):
            best, best_new = None, set()
            for v, s in pool:
                new = (s['hard'] if key == 'hard' else s['hard'] | s['p0']) - cover
                if len(new) > len(best_new):
                    best, best_new = v, new
            if best is None or not best_new:
                break
            cover |= best_new
            picked.append((best, best_new))
        return picked, cover

    all_pool = list(cov.items())
    buy_pool = [(v, s) for v, s in cov.items()
                if any((r[C_ADV] or '').strip() == '买'
                       for r in rows if v in (r[C_VEND] or ''))]
    _, g_all_hard = greedy(buy, all_pool, 'hard')
    _, g_all_p0 = greedy(buy | part, all_pool, 'p0')
    _, g_buy_hard = greedy(buy, buy_pool, 'hard')
    _, g_buy_p0 = greedy(buy | part, buy_pool, 'p0')

    top3 = ('国际半导体产业协会 SEMI', 'Benchmark Mineral Intelligence', 'Wood Mackenzie')
    union = set()
    for v in top3:
        union |= cov.get(v, {'hard': set(), 'p0': set()})['hard'] \
            | cov.get(v, {'hard': set(), 'p0': set()})['p0']

    return {
        'n_rows': len(rows),
        'n_combos': len(combos_hard),
        'n_combos_all': len(combos),
        'n_hard_rows': sum(1 for r in rows if (r[C_IND] or '').strip() in buy),
        'n_part_rows': sum(1 for r in rows if (r[C_IND] or '').strip() in part),
        'n_buy_indicator_rows': len({(r[C_IND] or '').strip() for r in rows
                                     if (r[C_IND] or '').strip() in buy}),
        'hard_with_vendor': len(hard_with_vendor),
        'part_with_free_alt': len(free_alt_ok),
        'adv_rows': adv_rows,
        'adv_hard': adv_hard,
        'adv_part': adv_part,
        'cov_values': dict(cov_values),
        'price_rows': price_rows,
        'vendor_hard': {v: len(s['hard']) for v, s in cov.items()},
        'vendor_p0': {v: len(s['hard'] | s['p0']) for v, s in cov.items()},
        'greedy_all_hard': len(g_all_hard),
        'greedy_all_p0': len(g_all_p0),
        'greedy_buy_hard': len(g_buy_hard),
        'greedy_buy_p0': len(g_buy_p0),
        'top3_union_p0': len(union),
        'top3_union_hard': len(union & buy),
        'single_max_hard': max((len(s['hard']) for s in cov.values()), default=0),
        'n_hard': len(buy),
        'n_part': len(part),
        'n_p0': len(buy) + len(part),
    }


def brief_ratios():
    """把 brief 里写成 “a/b = c%” 的比例解析出来。"""
    md = open(BRIEF_MD, encoding='utf-8').read()
    out = []
    for m in re.finditer(r'(\d+)\s*/\s*(\d+)\s*=\s*(\d+(?:\.\d+)?)\s*%', md):
        out.append((int(m.group(1)), int(m.group(2)), float(m.group(3))))
    return out, md


def check(findings, name, expected, actual, detail=''):
    ok = expected == actual
    findings.append({'check': name, 'expected': expected, 'actual': actual,
                     'blocking': not ok, 'detail': detail})
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=DEFAULT_OUT)
    args = ap.parse_args()

    r = recompute()
    findings = []

    check(findings, 'CSV 总行数 = 84', 84, r['n_rows'])
    check(findings, '需采购「指标×数据商」组合数 = 23', 23, r['n_combos'])
    check(findings, '全表「指标×数据商」组合数 = 92', 92, r['n_combos_all'])
    check(findings, '需采购行数 = 23', 23, r['n_hard_rows'])
    check(findings, '部分免费行数 = 61', 61, r['n_part_rows'])
    check(findings, '16 条需采购指标每条 ≥1 供应商', 16, r['hard_with_vendor'])
    check(findings, '61 条部分免费给出免费替代源', 61, r['part_with_free_alt'])
    check(findings, '建议买行数合计 = 22', 22, r['adv_rows']['买'])
    check(findings, '需采购行建议买 = 6', 6, r['adv_hard']['买'])
    check(findings, '部分免费行建议买 = 16', 16, r['adv_part']['买'])
    check(findings, '覆盖度取值：全覆盖 54', 54, r['cov_values'].get('全覆盖', 0))
    check(findings, '覆盖度取值：部分 28', 28, r['cov_values'].get('部分', 0))
    check(findings, '覆盖度取值：不覆盖 2', 2, r['cov_values'].get('不覆盖', 0))

    # brief 里写的比例，逐个与复算值对
    expect_ratios = {
        (r['single_max_hard'], r['n_hard']): '单家最大覆盖硬缺口',
        (r['greedy_all_hard'], r['n_hard']): '三家贪心（全部厂商）覆盖硬缺口',
        (r['greedy_all_p0'], r['n_p0']): '三家贪心（全部厂商）覆盖 P0',
        (r['greedy_buy_hard'], r['n_hard']): '三家贪心（只算建议买）覆盖硬缺口',
        (r['greedy_buy_p0'], r['n_p0']): '三家贪心（只算建议买）覆盖 P0',
        (r['top3_union_p0'], r['n_p0']): '推荐三家联合覆盖 P0',
        (r['top3_union_hard'], r['n_hard']): '推荐三家联合覆盖硬缺口',
    }
    ratios, md = brief_ratios()
    seen = set()
    for num, den, pct in ratios:
        label = expect_ratios.get((num, den))
        if label is None:
            continue
        if (num, den) in seen:
            continue
        seen.add((num, den))
        exp_pct = round(num / den * 100, 1)
        check(findings, f'brief 比例 {num}/{den}（{label}）', exp_pct, pct,
              f'{num}/{den} 应为 {exp_pct}%')
    for (num, den) in expect_ratios:
        if (num, den) not in seen:
            findings.append({'check': f'brief 缺少比例 {num}/{den}',
                             'expected': f'{num}/{den}',
                             'actual': '未在 brief 中出现', 'blocking': True, 'detail': ''})

    # brief §二 表格里逐家的覆盖数（**只切 §二 这一段**，避免串到 §四 的横向对比表）
    m2 = re.search(r'##\s*二、.*?(?=\n##\s*三、)', md, re.S)
    sec2 = m2.group(0) if m2 else ''
    table_rows = re.findall(
        r'^\|\s*([^|]+?)\s*\|\s*\*{0,2}(\d+)\*{0,2}[^|]*\|\s*\*{0,2}(\d+)\*{0,2}\s*\|',
        sec2, re.M)
    checked_vendors = 0
    # brief 里用简称，CSV 里是全称；两边都要核，所以给一张别名表
    ALIAS = {
        'SEMI 国际半导体产业协会': '国际半导体产业协会 SEMI',
        '赛迪顾问 CCID': '赛迪顾问 / 中国电子信息产业发展研究院（CCID）',
        'GGII 高工锂电': '高工锂电 / 高工产业研究院 GGII',
        'McKinsey': 'McKinsey 麦肯锡',
    }
    for vendor, h, p in table_rows:
        v = vendor.replace('*', '').strip()
        v = ALIAS.get(v, v)
        if v not in r['vendor_hard']:
            continue
        checked_vendors += 1
        check(findings, f'brief §二 覆盖数：{v}（硬缺口）', r['vendor_hard'][v], int(h))
        check(findings, f'brief §二 覆盖数：{v}（P0）', r['vendor_p0'][v], int(p))
    if checked_vendors < 5:
        findings.append({'check': 'brief §二 表格可解析的数据商行数',
                         'expected': '>=5', 'actual': checked_vendors, 'blocking': True,
                         'detail': '表格结构可能变了，人工核对'})

    blocking = [f for f in findings if f['blocking']]
    out = {
        'experiment': 'WORK-535',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'plan_csv': os.path.relpath(PLAN_CSV, REPO),
        'brief_md': os.path.relpath(BRIEF_MD, REPO),
        'recomputed': r,
        'findings': findings,
        'n_checks': len(findings),
        'n_blocking': len(blocking),
        'verdict': 'PASS' if not blocking else 'FAIL',
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    for f in findings:
        flag = 'BLOCKING' if f['blocking'] else 'ok'
        print(f"[{flag:>8}] {f['check']}: 期望 {f['expected']} / 实得 {f['actual']}"
              + (f"  {f['detail']}" if f['detail'] else ''))
    print(f"\n=== WORK-535 独立复算：{len(findings)} 项检查，blocking {len(blocking)} 项"
          f" → {out['verdict']} ===")
    print(f"产物：{args.out}")
    return 1 if blocking else 0


if __name__ == '__main__':
    sys.exit(main())
