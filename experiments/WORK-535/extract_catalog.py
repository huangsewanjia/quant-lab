"""从 data_catalog 抽取 WORK-535 的两个采购面：16 条「否（需采购）」+ 61 条「部分免费」。

只读打开 DuckDB，**不写库**（本单对 data_catalog 是只读的；修正只记在
data_contracts/data_source_registry.md §6，不回写目录）。

用法：
  .venv/bin/python extract_catalog.py                      # 打印两个面的条数与指标清单
  .venv/bin/python extract_catalog.py --dump               # 另存 p0_buy.csv / p0_partial.csv
  .venv/bin/python extract_catalog.py --full               # 另存 full 文本（含口径定义/频率/单位/一手来源）
"""
import sys

import duckdb

DB = '/srv/share/quantdb/db/quantdb.duckdb'
COLS = ['行业', '区域', '层级', '指标名', '口径定义', '频率', '单位',
        '一手来源', '来源类型', '我们现有', '备注']


def query(con, flag):
    sel = ', '.join('"%s"' % c for c in COLS)
    sql = ('select ' + sel + ' from data_catalog '
           'where "缺口优先级"=\'P0\' and "是否公开可获取"=%s '
           'order by "行业","区域","指标名"')
    return con.execute(sql, [flag]).df()


def main():
    args = sys.argv[1:]
    con = duckdb.connect(DB, read_only=True)
    buy = query(con, '否（需采购）')
    part = query(con, '部分免费')
    print(f'需采购（否（需采购））: {len(buy)} 条')
    for i, r in buy.iterrows():
        print(f'  [{i:>2}] {r["行业"]}｜{r["指标名"]}｜一手来源: {r["一手来源"]}')
    print(f'\n部分免费（部分免费）: {len(part)} 条')
    for i, r in part.iterrows():
        print(f'  [{i:>2}] {r["行业"]}｜{r["指标名"]}｜一手来源: {r["一手来源"]}')

    if '--dump' in args or '--full' in args:
        buy.to_csv('p0_buy.csv', index=False)
        part.to_csv('p0_partial.csv', index=False)
        print('\n已写 p0_buy.csv / p0_partial.csv')
    if '--full' in args:
        with open('partial_full.txt', 'w', encoding='utf-8') as f:
            for i, r in part.iterrows():
                f.write(f'[{i}] {r["行业"]}|{r["区域"]}|{r["层级"]} :: {r["指标名"]}\n')
                f.write(f'   口径: {r["口径定义"]} | 频率: {r["频率"]} | 单位: {r["单位"]}\n')
                f.write(f'   一手来源: {r["一手来源"]}  [类型: {r["来源类型"]}]\n')
                f.write(f'   现有: {r["我们现有"]}\n   备注: {r["备注"]}\n\n')
        print('已写 partial_full.txt')


if __name__ == '__main__':
    main()
