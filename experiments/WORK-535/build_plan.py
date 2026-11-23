"""Build docs/procurement_plan.csv for WORK-535 from data_catalog + the enriched tables."""
import csv
import os
import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plan_partial import PARTIAL_BY_SOURCE  # noqa: E402
from plan_purchase import PURCHASE  # noqa: E402
from plan_reach import OK_TAG, REACH  # noqa: E402
from plan_research import RESEARCH_ROWS  # noqa: E402

COLUMNS = [
    '指标名', '行业', '区域', '数据商', '覆盖度(全覆盖/部分)', '官网价格页URL', '公开价格',
    '计价方式(年费/单报告/按席位)', '免费替代源', '替代源能力差距', '建议(买/等/不买)', '备注',
]

# the enriched tables use short keys; map them onto the CSV header names
ALIAS = {
    '覆盖度': '覆盖度(全覆盖/部分)',
    '建议': '建议(买/等/不买)',
    '计价方式': '计价方式(年费/单报告/按席位)',
}


def normalize(row):
    out = {}
    for k, v in row.items():
        out[ALIAS.get(k, k)] = v
    return out

DB = '/srv/share/quantdb/db/quantdb.duckdb'

# 规格要求「一行一个「指标 × 数据商」」。同一组合在源表里可能有两条证据
# （例如 Benchmark 的价格页 + API 页是同一份数据的两个通道），合并成一行：
#   覆盖度取保守值（宁可标「部分」也不高估），建议取强（「买」优先于「等」优先于「不买」），
#   两边不一致时在备注写明，避免把「佐证页的口径」当成「已验证的覆盖」。
RANK_COV = {'全覆盖': 2, '部分': 1, '不覆盖': 0}
RANK_ADV = {'买': 2, '等': 1, '不买': 0}


def merge_rows(rows):
    order = []
    merged = {}
    for r in rows:
        key = (r['指标名'], r['数据商'])
        if key not in merged:
            merged[key] = dict(r)
            order.append(key)
            continue
        m = merged[key]
        cov_old, cov_new = m['覆盖度(全覆盖/部分)'], r['覆盖度(全覆盖/部分)']
        if cov_old != cov_new:
            # 保守口径：取覆盖度更低的一方
            keep = cov_old if RANK_COV.get(cov_old, -1) <= RANK_COV.get(cov_new, -1) else cov_new
            m['覆盖度(全覆盖/部分)'] = keep
            m['备注'] = (m['备注'] + f' 覆盖度冲突：另一佐证页标注「{cov_new}」，'
                         f'本表按保守口径取「{keep}」。').strip()
        adv_old, adv_new = m['建议(买/等/不买)'], r['建议(买/等/不买)']
        if adv_old != adv_new:
            keep_adv = adv_old if RANK_ADV.get(adv_old, -1) >= RANK_ADV.get(adv_new, -1) else adv_new
            m['建议(买/等/不买)'] = keep_adv
            m['备注'] = (m['备注'] + f' 建议冲突：另一佐证页标「{adv_new}」，'
                         f'本表取「{keep_adv}」（同一份数据、只是取数通道不同）。').strip()
        for col in ('公开价格', '计价方式(年费/单报告/按席位)'):
            a, b = str(m.get(col, '')).strip(), str(r.get(col, '')).strip()
            if b and b not in a and a and not b.startswith('不适用') and not a.startswith('不适用'):
                m[col] = f'{a} ｜ {b}'
        for col in ('免费替代源', '替代源能力差距', '备注'):
            a, b = str(m.get(col, '')).strip(), str(r.get(col, '')).strip()
            if b and b not in a:
                m[col] = (a + ' ' + b).strip()
        if r['官网价格页URL'] and r['官网价格页URL'] != m['官网价格页URL']:
            extra = r['官网价格页URL']
            if extra not in m['备注']:
                m['备注'] = (m['备注'] + f' 另一佐证页（同一数据源的另一入口）：{extra}').strip()
    print(f'合并重复「指标×数据商」组合: {len(rows)} 行 -> {len(order)} 行')
    return [merged[k] for k in order]


def main(out_path):
    con = duckdb.connect(DB, read_only=True)
    buy = con.execute(
        'select "行业","区域","层级","指标名","一手来源" from data_catalog '
        "where \"缺口优先级\"='P0' and \"是否公开可获取\"='否（需采购）' "
        'order by "行业","区域","指标名"'
    ).df()
    part = con.execute(
        'select "行业","区域","层级","指标名","一手来源","备注" from data_catalog '
        "where \"缺口优先级\"='P0' and \"是否公开可获取\"='部分免费' "
        'order by "行业","区域","指标名"'
    ).df()

    rows = []

    # ---- 16 条需采购：一行一个「指标 × 数据商」 ----
    used = set()
    for _, r in buy.iterrows():
        hit = [p for p in PURCHASE if p['指标名'] == r['指标名']]
        if not hit:
            print(f'!! 需采购指标缺供应商行: {r["指标名"]}')
        for p in hit:
            rows.append(normalize({**p, '行业': r['行业'], '区域': r['区域']}))
            used.add((p['指标名'], p['数据商']))
    # ---- 研报订阅通道（采购范围增补）：服务 5 条战略层「产业链利润/价值分布」硬缺口 ----
    n_research = 0
    for r in RESEARCH_ROWS:
        hit = buy[buy['指标名'] == r['指标名']]
        if hit.empty:
            print(f'!! 研报通道指标不在需采购集: {r["指标名"]}')
            continue
        rows.append(normalize({**r, '行业': hit.iloc[0]['行业'], '区域': hit.iloc[0]['区域']}))
        n_research += 1
    print(f'研报订阅通道追加行: {n_research}')

    orphan = [(p['指标名'], p['数据商']) for p in PURCHASE
              if p['指标名'] not in set(buy['指标名'])]
    if orphan:
        print('!! PURCHASE 表里有库中不存在的指标:', orphan)

    # ---- 61 条部分免费：一行一个「指标 × 数据商（付费源）」 ----
    missing = []
    for _, r in part.iterrows():
        key = r['一手来源']
        if key not in PARTIAL_BY_SOURCE:
            missing.append((r['指标名'], key))
            continue
        e = PARTIAL_BY_SOURCE[key]
        rows.append(normalize({
            '指标名': r['指标名'], '行业': r['行业'], '区域': r['区域'],
            **e,
        }))
    if missing:
        print(f'!! 部分免费行缺映射 {len(missing)} 条:')
        for m in missing:
            print('   ', m)

    rows = merge_rows(rows)

    # 每一行都带上自己那个「官网价格页URL」的实测状态，验收时可逐行自证
    for row in rows:
        u = (row.get('官网价格页URL') or '').strip()
        tag = REACH.get(u, OK_TAG)
        note = (row.get('备注') or '').strip()
        if tag not in note:
            row['备注'] = (note + ' ' + tag).strip()

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, '') for c in COLUMNS})

    # ---- 自检 ----
    buy_inds = set(buy['指标名'])
    part_inds = set(part['指标名'])
    cov_buy = {row['指标名'] for row in rows if row['指标名'] in buy_inds}
    has_free = {row['指标名'] for row in rows
                if row['指标名'] in part_inds and row.get('免费替代源')}
    n_with_price = sum(1 for row in rows
                        if any(c in str(row.get('公开价格', '')) for c in '$€¥')
                        and '未公开' not in str(row.get('公开价格', '')))
    print(f'\n=== 输出 {out_path} ===')
    print(f'总行数: {len(rows)}')
    print(f'需采购指标: {len(buy_inds)} 条，其中至少 1 个供应商的: {len(cov_buy)} 条'
          f'（缺口: {sorted(buy_inds - cov_buy)}）')
    print(f'部分免费指标: {len(part_inds)} 条，其中给出免费替代源的: {len(has_free)} 条'
          f'（{len(has_free) / len(part_inds) * 100:.1f}%，门槛 80%）')
    print(f'带可核对公开价格的行: {n_with_price}')
    print(f'每行都有 URL: {all(str(r.get("官网价格页URL", "")).startswith("http") for r in rows)}')
    return 0 if (not missing and buy_inds <= cov_buy) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'docs/procurement_plan.csv'))
