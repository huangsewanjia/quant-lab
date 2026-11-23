# Changelog

## 2026-11-23 · WORK-529 定时更新

新增：

- `db/etl/incremental.py`：行情水位、财务披露窗口、指数成分月度窗口、watermark、幂等装载。
- `db/etl/weekly.py`：调用成功率、失败清单、日历缺口、行数变化的周报。
- `db/timers/`：`daily.sh`、`finance.sh`、`policy.sh`、`weekly.sh` 与 4 个 systemd service/timer。
- `db/timers/backup-*.sh` 与 `systemd/backup-nightly.*`：冷备逻辑进入稳定仓库；home 脚本变成 shim。
- `db/policy/fetch_policy.py`：已知 URL 优先跳过；`--reconcile` 有界补抓。
- `db/README.md` §11、`docs/WORK-529.md`、`DELIVERY.html`。

修复：

- `common.POLICY_DIR` 缺失（政策采集的路径常量）。
- `normalize_date_columns` 对「已存 DATE + 新到 YYYYMMDD」混合列会把旧值转成 NaT。
- `drop_null_pk_rows` 会把合成列 `dup_seq` 当自然键，增量合并时误删新行。
- 财务/指数成分增量改为「旧分区 + 新行 + 内容去重」，重复装载行数不变。

实测：

- `pytest db/tests -q` → 160 passed。
- 行情增量在收盘前 no-op；模拟收盘后无数据返回 75 且不写空分区。
- 财务 2026-11-13 单日实测四次抓取 + dividend 21 个分区，重复跑行数不变。
- 冷备 rsync + `SHA256SUMS` 155,640 个文件校验通过。
- 四个 timer `systemctl --user list-timers` 均 active。

## 2026-11-22 · WORK-513 建库 P1

新增 `db/`：

- `db/schema.sql`：15 张 P0 表，主键、单位注释、PIT 列。
- `db/etl/`：Tushare 增量 ETL、限流、并发、manifest、断点、失败清单；新浪单股冒烟。
- `db/checks/`：manifest、日历勾稽、主键、OHLC、跨源抽样、每日行数 6 项质量闸。
- `db/ql/` 与 `db/bin/ql`：只读查询 CLI，默认 200 行，table/json/csv。
- `db/policy/`：六源白名单政策采集，双时间轴，正文 txt + meta.json，同文去重。
- `db/tests/`：分区幂等、断点、manifest、ql 上限、schema、政策抽取，14 项测试。

文档：

- `docs/INDEX.md`、`PROJECT.md`、`SPEC.md`、`ENVIRONMENT.md`、`TECH-NOTES.md`、`RUNBOOK.md`。
- `docs/decisions/0001-storage-layout.md`、`0002-policy-collection.md`。

实测：

- 600000.SH 20 年：4,808 行，4,808 个 raw 分区，32.5 MB Parquet。
- DuckDB 装载 5.2 s，ql 查询正常。
- 质量闸 6 项 PASS。
- 政策 5 篇入库，6 条 missing。
