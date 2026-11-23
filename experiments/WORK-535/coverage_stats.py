"""Compute the P0 coverage arithmetic used in docs/procurement_brief.md.

Coverage is counted over the P0 set that is actually in scope for procurement decisions:
  - 16 rows 「否（需采购）」
  - 61 rows 「部分免费」
and reported per vendor as:
  覆盖条数 / 建议买条数 / 全覆盖条数 / 部分覆盖条数 / 不覆盖条数
"""
import collections
import csv
import sys

import duckdb

PLAN = sys.argv[1] if len(sys.argv) > 1 else '/tmp/plan_test.csv'
con = duckdb.connect('/srv/share/quantdb/db/quantdb.duckdb', read_only=True)
buy = set(con.execute(
    "select \"指标名\" from data_catalog where \"缺口优先级\"='P0' and \"是否公开可获取\"='否（需采购）'"
).df()['指标名'])
part = set(con.execute(
    "select \"指标名\" from data_catalog where \"缺口优先级\"='P0' and \"是否公开可获取\"='部分免费'"
).df()['指标名'])

rows = list(csv.DictReader(open(PLAN, encoding='utf-8')))

# canonical vendor key: merge aliases of the same supplier
ALIAS = {
    '高工锂电 / 高工产业研究院 GGII': '高工锂电 / GGII（高工产研）',
    '赛迪顾问 / 中国电子信息产业发展研究院（CCID）': '赛迪顾问 / 中国电子信息产业发展研究院',
    '国际半导体产业协会 SEMI': 'SEMI 国际半导体产业协会',
    '全球半导体贸易统计 WSTS（经 ESIA 独家许可订阅）': 'WSTS 世界半导体贸易统计（订阅经 ESIA）',
    '欧洲半导体产业协会 ESIA': 'ESIA 欧洲半导体产业协会',
    '国际能源署 IEA + SNE Research': 'IEA + SNE Research',
    'SNE Research': 'SNE Research',
}
# rows whose vendor cell names several suppliers -> expand into per-supplier coverage
SPLIT = {
    'SNE Research': ['SNE Research'],
    'SEMI 国际半导体产业协会': ['SEMI 国际半导体产业协会'],
}

stat = collections.defaultdict(lambda: {
    'buy': set(), 'part': set(), 'full': set(), 'partial': set(), 'none': set(),
    'adv_buy': set(), 'adv_part': set(), 'price': set(),
})

for r in rows:
    name = r['指标名']
    vendors = [r['数据商']]
    if ' + ' in r['数据商']:
        vendors = [v.strip() for v in r['数据商'].split(' + ')]
    cov = r['覆盖度(全覆盖/部分)']
    buy_flag = name in buy
    for v in vendors:
        s = stat[v]
        # 只有 覆盖度 ∈ {全覆盖, 部分} 才算「这家能供」；不覆盖的行必须排除，
        # 否则会把「已核实不覆盖」误算成覆盖，夸大某家的覆盖率。
        if cov in ('全覆盖', '部分'):
            if buy_flag:
                s['buy'].add(name)
            else:
                s['part'].add(name)
        if cov == '全覆盖':
            s['full'].add(name)
        elif cov == '部分':
            s['partial'].add(name)
        else:
            s['none'].add(name)
        if r['建议(买/等/不买)'] == '买':
            (s['adv_buy'] if buy_flag else s['adv_part']).add(name)
        if any(c in str(r['公开价格']) for c in '$€¥') and '未公开' not in str(r['公开价格']):
            s['price'].add(name)

print(f'口径：需采购 {len(buy)} 条 + 部分免费 {len(part)} 条 = P0 合计 {len(buy) + len(part)} 条\n')
print(f'{"数据商":<38}{"需采购":>7}{"部分免费":>9}{"P0合计":>8}{"建议买":>8}'
      f'{"全覆盖":>8}{"部分":>7}{"不覆盖":>8}  公开价行')
print('-' * 100)
rowsout = []
for v, s in stat.items():
    total = len(s['buy']) + len(s['part'])
    rowsout.append((total, len(s['buy']), len(s['adv_buy']) + len(s['adv_part']), v, s))
for total, nb, adv, v, s in sorted(rowsout, key=lambda x: (-x[0], -x[2])):
    print(f'{v:<38}{len(s["buy"]):>7}{len(s["part"]):>9}{total:>8}{adv:>8}'
          f'{len(s["full"]):>8}{len(s["partial"]):>7}{len(s["none"]):>8}  {len(s["price"])}')

# --- 「先买哪 2–3 家」: greedy set cover over the 16 purchase indicators ---
print('\n=== 贪心集合覆盖：按「新增覆盖条数」挑供应商，目标是 16 条需采购指标 ===')
cover = set()
picked = []
pool = sorted(stat.items(), key=lambda kv: -len(kv[1]['buy']))
while len(picked) < 3:
    best, best_new, best_adv = None, set(), 0
    for v, s in pool:
        if v in [p[0] for p in picked]:
            continue
        new = s['buy'] - cover
        adv = len(new & s['adv_buy'])
        if len(new) > len(best_new) or (len(new) == len(best_new) and adv > best_adv):
            best, best_new, best_adv = v, new, adv
    if best is None or not best_new:
        break
    cover |= best_new
    picked.append((best, best_new, best_adv))
    print(f'  第 {len(picked)} 家: {best}  → 新增覆盖 {len(best_new)} 条'
          f'（其中建议「买」的 {best_adv} 条）')
    print(f'      {sorted(best_new)}')
print(f'\n  3 家合计覆盖 {len(cover)} / 16 条 = {len(cover) / 16 * 100:.1f}%')
print(f'  未覆盖: {sorted(buy - cover)}')

# --- 加上部分免费行的三选一 ---
print('\n=== 同样的贪心，但把 61 条部分免费也纳入覆盖（更贴近预算决策）===')
cover2 = set()
picked2 = []
while len(picked2) < 3:
    best, best_new = None, set()
    for v, s in sorted(stat.items(), key=lambda kv: -len(kv[1]['buy'] | kv[1]['part'])):
        if v in [p[0] for p in picked2]:
            continue
        new = (s['buy'] | s['part']) - cover2
        if len(new) > len(best_new):
            best, best_new = v, new
    if best is None or not best_new:
        break
    cover2 |= best_new
    picked2.append((best, best_new))
    print(f'  第 {len(picked2)} 家: {best}  → 新增 {len(best_new)} 条')
print(f'\n  3 家合计覆盖 {len(cover2)} / 77 条 P0 = {len(cover2) / 77 * 100:.1f}%')
print(f'  未覆盖 {77 - len(cover2)} 条')

# --- 没有一家全覆盖 ---
print('\n=== 「没有一家能全覆盖」的证明：逐家看 16 条需采购指标的缺口 ===')
for v, s in sorted(stat.items(), key=lambda kv: -len(kv[1]['buy'])):
    if not s['buy']:
        continue
    gap = sorted(buy - s['buy'])
    print(f'  {v}: 覆盖 {len(s["buy"])}/16, 缺 {len(gap)} 条')
print(f'\n  单家最大覆盖 = {max(len(s["buy"]) for s in stat.values())}/16'
      f' = {max(len(s["buy"]) for s in stat.values()) / 16 * 100:.1f}%')
