# ADR 0001：Parquet L0 + DuckDB L1，按交易日分区

状态：已采纳（2026-11-22）

## 背景

研究需要本地、可增量、可审计的 A 股数据。全市场 20 年日线约 2,842 万行，11 列 zstd 约 0.74 GB；1 分钟线约 57 GB。必须把原始层与分析层分开，且溢写不能落 SSD。

## 决策

1. L0 原始层用 Parquet + zstd，路径 `/srv/share/quantdb/raw/<dataset>/trade_date=YYYYMMDD/part.parquet`（财务类为 `ts_code=<code>/part.parquet`）。
2. 每个分区写 `meta/<dataset>/<partition>.json`，含行数、字节数、sha256、来源、抓取时间。
3. L1 用单文件 DuckDB `/srv/share/quantdb/db/quantdb.duckdb`，schema 由 `db/schema.sql` 建。
4. DuckDB `temp_directory` 指向 `/srv/share/quantdb/staging/tmp`。
5. L0 分区保存上游返回的**全部列**（不做字段子集），DuckDB 层再按 `db/schema.sql` 归一化；因此不需要 `raw_json` 兜底列。
6. 复权不落死，只存 `close` 与 `adj_factor`。

## 理由

- Parquet 列存与 zstd 已在 WORK-512 实测，体积可接受。
- DuckDB 零服务、单文件、可直接读 Parquet，适合单机研究。
- 分区 manifest 让重跑、断点、审计都有不可变证据。
- L0 保存全部上游列、L1 按 schema 归一化，解决 Tushare 财务字段多、版本变化的问题，不因 schema 子集丢数据。

## 后果

- 单写者：ETL 装载串行；查询只读并发。
- 4,808 个单行 Parquet 有文件系统块开销；全市场按日分区后每文件数千行，摊薄明显。
- 分区目录名 `trade_date=...` 会触发 DuckDB hive 分区推断；读取时必须 `hive_partitioning=false`，否则日期列会被推断成 BIGINT。此坑已写入代码与测试。

## 回滚

- 代码回滚：`git revert` 本单提交。
- 数据回滚：删除 `/srv/share/quantdb/db/quantdb.duckdb` 后由 raw 重建；raw 不被 ETL 删除。
- 分区回滚：按 manifest 的 sha256 找到异常分区，重拉该分区覆盖。
