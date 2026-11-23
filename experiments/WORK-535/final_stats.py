"""WORK-535 决策用算式：供应商 P0 覆盖度排名 + 「先买哪 2–3 家」贪心集合覆盖。

口径（全部按 CSV 实际内容统计，不靠印象）：
  * 覆盖 = 该行「覆盖度」为 全覆盖 / 部分；「不覆盖」不计入覆盖（已核实不覆盖的行不能拿来充数）。
  * 「建议买」= 该行「建议(买/等/不买)」= 买。
  * 两套目标集：16 条需采购硬缺口；77 条 P0 合计（16 需采购 + 61 部分免费）。

Usage: .venv/bin/python final_stats.py <csv-path>
"""
import collections
import csv
import sys

import duckdb

PLAN = sys.argv[1] if len(sys.argv) > 1 else 'quant-lab/docs/procurement_plan.csv'
DB = '/srv/share/quantdb/db/quantdb.duckdb'

con = duckdb.connect(DB, read_only=True)
buy = {r[0] for r in con.execute(
    "select \"指标名\" from data_catalog where \"缺口优先级\"='P0' and \"是否公开可获取\"='否（需采购）'"
).df().itertuples(index=False)}
part = {r[0] for r in con.execute(
    "select \"指标名\" from data_catalog where \"缺口优先级\"='P0' and \"是否公开可获取\"='部分免费\"'"
).df().itertuples(index=False)} if False else {r[0] for r in con.execute(
    "select \"指标名\" from data_catalog where \"缺口优先级\"='P0' and \"是否公开可获取\"='部分免费'"
).df().itertuples(index=False)}

rows = list(csv.DictReader(open(PLAN, encoding='utf-8')))

V_COV = '覆盖度(全覆盖/部分)'
V_ADV = '建议(买/等/不买)'

stat = collections.defaultdict(lambda: {
    'buy': set(), 'part': set(), 'adv_buy': set(), 'adv_part': set(),
    'none': set(), 'price': set(),
})

for r in rows:
    name = r['指标名']
    vendors = [v.strip() for v in r['数据商'].split(' + ')]
    cov, adv = r[V_COV], r[V_ADV]
    is_buy = name in buy
    for v in vendors:
        s = stat[v]
        if cov in ('全覆盖', '部分'):
            (s['buy'] if is_buy else s['part']).add(name)
        else:
            s['none'].add(name)
        if adv == '买':
            (s['adv_buy'] if is_buy else s['adv_part']).add(name)
        # 与 verify_prices.py 同一口径：含货币符号或 USD/EUR/RMB 且未写「未公开」
        p = str(r['公开价格'])
        if (any(c in p for c in '$€¥') or any(c in p for c in ('USD', 'EUR', 'RMB'))) \
                and '未公开' not in p:
            s['price'].add(name)


def greedy(target, pool, key_fn, k=3, label=''):
    cover, picked = set(), []
    while len(picked) < k:
        best, best_new = None, set()
        for v, s in pool:
            new = key_fn(s) - cover
            if len(new) > len(best_new):
                best, best_new = v, new
        if best is None or not best_new:
            break
        cover |= best_new
        picked.append((best, best_new))
    print(f'\n{label}')
    for i, (v, new) in enumerate(picked, 1):
        print(f'  第 {i} 家: {v:<38} 新增覆盖 {len(new)} 条')
        for n in sorted(new):
            print(f'      - {n}')
    print(f'  → {len(picked)} 家合计覆盖 {len(cover)} / {len(target)} 条 = '
          f'{len(cover) / len(target) * 100:.1f}%')
    if len(picked) < k:
        print(f'  （不足 {k} 家：再买没有新增覆盖）')
    print(f'  未覆盖 {len(target - cover)} 条')
    return picked, cover


print(f'口径：需采购 {len(buy)} 条 + 部分免费 {len(part)} 条 = P0 合计 {len(buy) + len(part)} 条；'
      f'CSV 行数 {len(rows)}')
print('\n=== A. 按「建议买」条数排名（全部 84 行）===')
print(f'{"数据商":<40}{"建议买":>7}{"需采购":>8}{"部分免费":>10}{"覆盖合计":>9}{"不覆盖":>8}{"公开价行":>9}')
print('-' * 92)
ranked = sorted(stat.items(), key=lambda kv: (-(len(kv[1]['adv_buy']) + len(kv[1]['adv_part'])),
                                             -len(kv[1]['buy'] | kv[1]['part'])))
for v, s in ranked:
    adv = len(s['adv_buy']) + len(s['adv_part'])
    if adv == 0 and not s['buy']:
        continue
    print(f'{v:<40}{adv:>7}{len(s["buy"]):>8}{len(s["part"]):>10}'
          f'{len(s["buy"] | s["part"]):>8}{len(s["none"]):>8}{len(s["price"]):>9}')

row_adv = [r for r in rows if r[V_ADV] == '买']
print(f'\n  「建议买」的行（CSV 行口径，不按厂商重复计）: {len(row_adv)} 行'
      f'（需采购 {sum(1 for r in row_adv if r["指标名"] in buy)} 行 + 部分免费 '
      f'{sum(1 for r in row_adv if r["指标名"] not in buy)} 行）')
print('  上表按厂商计，故「数据商」列之和大于行数（多厂商行被拆到各厂商名下）')

pool_all = list(stat.items())
pool_buy = [(v, s) for v, s in stat.items()
            if len(s['adv_buy']) + len(s['adv_part']) > 0]

greedy(buy, pool_all, lambda s: s['buy'], 3,
       '=== B. 贪心集合覆盖（全部供应商）→ 16 条需采购硬缺口 ===')
greedy(buy | part, pool_all, lambda s: s['buy'] | s['part'], 3,
       '=== C. 贪心集合覆盖（全部供应商）→ 77 条 P0 ===')
greedy(buy, pool_buy, lambda s: s['buy'], 3,
       '=== D. 贪心集合覆盖（只算「建议买」的供应商）→ 16 条需采购硬缺口 ===')
picked, cover = greedy(buy | part, pool_buy, lambda s: s['buy'] | s['part'], 3,
                       '=== E. 贪心集合覆盖（只算「建议买」的供应商）→ 77 条 P0 ===')

print('\n=== F. 逐家缺口：没有任何一家覆盖全部 16 条需采购 ===')
for v, s in sorted(stat.items(), key=lambda kv: -len(kv[1]['buy'])):
    if not s['buy']:
        continue
    print(f'  {v:<40} 覆盖 {len(s["buy"])}/16（{len(s["buy"]) / 16 * 100:.1f}%），'
          f'缺 {16 - len(s["buy"])} 条')
print(f'\n  单家最大覆盖 = {max(len(s["buy"]) for s in stat.values())}/16 = '
      f'{max(len(s["buy"]) for s in stat.values()) / 16 * 100:.1f}%')

print('\n=== G. 覆盖度取值分布 ===')
print(collections.Counter(r[V_COV] for r in rows))
print('  「不覆盖」的行:')
for r in rows:
    if r[V_COV] == '不覆盖':
        print(f'    - {r["指标名"]} × {r["数据商"]}（建议={r[V_ADV]}）')

print('\n=== H. 建议分布 ===')
print(f'  需采购 16 条: ', collections.Counter(r[V_ADV] for r in rows if r['指标名'] in buy))
print(f'  部分免费 61 条: ', collections.Counter(r[V_ADV] for r in rows if r['指标名'] not in buy))
