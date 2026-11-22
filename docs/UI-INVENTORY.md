# 界面清单

WORK-513 没有图形界面，交付面是命令行。记录于此，避免后续误以为漏了 UI 验收。

| 入口 | 用途 | 关键控件/参数 | 失败状态 |
|---|---|---|---|
| `db/bin/ql` | 只读查询 | `sql` / `daily` / `fin`；`--format`、`--limit`、`--out` | 数据库不存在、SQL 非只读、结果超 200 行提示 |
| `python -m db.etl.fetch` | 拉数与装载 | 子命令、`--start/--end`、`--codes`、`--rpm`、`--concurrency` | 无 token、上游失败、并发 >19 |
| `python -m db.checks.run` | 质量闸 | `all` 或单项；`--sample`、`--tolerance` | 任一检查失败退出码 1 |
| `python -m db.policy.fetch_policy` | 政策采集 | `--source`、`--max-items`、`--list-only` | 双时间轴缺失写 missing；六源全不可达退出码 2 |
| `python -m db.etl.smoke_sina` | 无 token 冒烟 | `--code`、`--years` | 新浪返回空、DuckDB 装载失败 |
