# 项目目标：可增量更新的 A 股数据底座

## 用户与问题

用户是量化研究工程师。WORK-505 的失败根因不是数据源没数据，而是没有本地库：MCP 逐只拉取约 2,040 次调用、约 5,000 万 token，快照仍丢失 674/680。研究需要一套可重复、可增量、能审计的数据底座。

## 目标

把 `/srv/share/quantdb` 建成 P0 日频优先的本地数据底座：

1. L0 原始层用 Parquet 按交易日分区，只追加。
2. 每个分区有行数、字节数、sha256 的 manifest。
3. L1 用 DuckDB 单文件，schema 覆盖 16 张 P0 表。
4. ETL 支持增量、幂等、断点续跑、失败清单、限流与并发上限。
5. 提供只读 `ql` CLI，默认最多 200 行。
6. 提供质量闸，失败退出码非 0。
7. 提供六源白名单政策采集，双时间轴缺一不入库。
8. 无 token 条件下完成一条单股端到端冒烟。

## 成功标准

WORK-513 issue 的 10 项验收全部有证据：

| # | 成功标准 | 当前证据 |
|---|---|---|
| 1 | 六目录 + venv 四包可 import | `docs/ENVIRONMENT.md` |
| 2 | 16 张 P0 表，PK/单位/PIT 齐，schema 一次建库 | `db/tests/test_schema.py` |
| 3 | ETL 幂等、断点、manifest、失败清单 | `db/tests/test_partition_idempotent.py`、`test_resume.py`、`test_manifest.py` |
| 4 | 默认 9 rpm，并发上限 19，JSONL 调用日志 | `db/etl/common.py`、`db/etl/fetch.py` |
| 5 | 至少 4 个质量闸，失败非 0 | `db/checks/`，实测 6 项全 PASS |
| 6 | `ql sql/daily/fin`，只读，默认 200 行，三种格式 | `db/ql/`、`db/tests/test_ql_limit.py` |
| 7 | 六源白名单、正文 txt + meta、双时间轴、去重 | `db/policy/`，实测 5 篇入库 |
| 8 | 无 token 单股 20 年 + 1 交易日全链路 | `/srv/share/quantdb/meta/smoke-report.json` |
| 9 | 四类单测全绿 | `pytest -q` 14 passed |
| 10 | 独立审查 | `03-review.md` 与交付说明中的审查结论 |

## 范围

做：

- 目录、schema、ETL 骨架、质量闸、`ql`、政策采集、单股冒烟、测试。
- 代码落在 `quant-lab/db/`，数据落在 `/srv/share/quantdb/`。

不做：

- 不拉全市场历史。token 未就位，全市场回填留到 token 到位后的独立任务。
- 不改 `quant-lab` 既有 `data/`、`experiments/`、`figures/`。
- 不把行情、政策原文、DuckDB 文件写进 git。
- 不装系统级包。
- 不把复权价写死；不按当前在市状态过滤历史行情。

## 最终用户主流程

1. 研究者用 `python -m db.etl.fetch` 增量拉数。
2. ETL 写 `raw/<dataset>/trade_date=YYYYMMDD/part.parquet` 与 `meta/<dataset>/<partition>.json`。
3. 研究者运行 `python -m db.etl.fetch load` 把新分区 upsert 进 `quantdb.duckdb`。
4. 研究者用 `db/bin/ql` 查询日线、财务或任意只读 SQL。
5. 发布前运行 `python -m db.checks.run all`，失败即阻断。
6. 政策研究用 `python -m db.policy.fetch_policy` 采集，缺双时间轴的条目只进 missing 清单。
