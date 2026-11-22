# WORK-513 规格

## 1. 数据分层与路径

| 层 | 路径 | 规则 |
|---|---|---|
| L0 raw | `/srv/share/quantdb/raw/<dataset>/trade_date=YYYYMMDD/data.parquet` | 只追加；写盘后不改写；重跑同分区原子覆盖 |
| L0 manifest | `/srv/share/quantdb/meta/<dataset>/<partition>.json` | 行数、字节数、sha256、来源、抓取时间、列、主键 |
| L1 | `/srv/share/quantdb/db/quantdb.duckdb` | 单文件；schema 由 `db/schema.sql` 建 |
| 政策 | `/srv/share/quantdb/policy/<source>/<sha16>/{content.txt,meta.json}` | 只存双时间轴齐全的正文 |
| 日志 | `/srv/share/quantdb/logs/fetch-YYYYMMDD.jsonl` | 每次上游调用一行；不含 token |
| 溢写 | `/srv/share/quantdb/staging/tmp` | DuckDB `temp_directory` |
| 备份 | `/srv/backup/quantdb/` | 冷备，夜间 rsync/zstd；本单实现备份命令，不启用定时任务 |

禁止把数据或 DuckDB 文件写到 `/`（SSD）。token 禁止写入 `/srv/share`。

## 2. P0 schema

`db/schema.sql` 覆盖 15 张表：

`trade_cal`、`stock_basic`、`daily`、`adj_factor`、`daily_basic`、`stk_limit`、`suspend_d`、`index_daily`、`index_weight`、`income`、`balancesheet`、`cashflow`、`fina_indicator`、`dividend`、`namechange`。

硬口径：

- 每表有主键；主键与 `db/etl/common.py::TABLE_PK` 保持一致。
- 单位写进 `COMMENT ON COLUMN`：`vol` 手、`amount` 千元、`total_mv` 万元、`index_daily.close` 点。
- 财务表必须有 `ann_date` 与 `f_ann_date`；回测按公告日入样。
- `stock_basic.list_status` 允许 `L/D/P`；`daily` 落盘后不按在市状态过滤。
- `daily.close` 是原始收盘；复权只在查询期用 `adj_factor` 计算。
- 财务表的未建模字段保留在 `raw_json`，避免同步时丢字段。

## 3. ETL 契约

命令：

```bash
python -m db.etl.fetch init-db
python -m db.etl.fetch calendar --start 20200101 --end 20201231
python -m db.etl.fetch stock-basic
python -m db.etl.fetch daily --start 20200101 --end 20201231
python -m db.etl.fetch adj-factor --start 20200101 --end 20201231
python -m db.etl.fetch daily-basic --start 20200101 --end 20201231
python -m db.etl.fetch stk-limit --start 20200101 --end 20201231
python -m db.etl.fetch suspend --start 20200101 --end 20201231
python -m db.etl.fetch index-daily --codes 000300.SH --start 20200101 --end 20201231
python -m db.etl.fetch index-weight --index-code 000300.SH --start 20200101 --end 20201231
python -m db.etl.fetch financials --codes 600000.SH --datasets income,fina_indicator
python -m db.etl.fetch load --datasets all
```

规则：

- 按 `trade_date` 整截面拉行情；按 `ts_code` 拉财务。
- 幂等：同参数重跑不产生重复行；分区按主键去重后原子覆盖。
- 断点：manifest 存在、文件存在、字节数和 sha256 都匹配才跳过；否则重拉。
- 失败：每次调用最多重试 2 次；最终失败写 `meta/failures-YYYYMMDD.json`，继续其余分区。
- 速率：默认 9 次/分钟；`--rpm` 可调；`--concurrency` 范围 1..19。
- 日志：每次调用写 `trade_date`/`ts_code`/耗时/行数/状态；不写 token。
- token：环境变量 `TUSHARE_TOKEN` 或 `/home/moyongkai/.config/quantdb/tushare.token`（600）。

## 4. ql 契约

```bash
db/bin/ql --help
db/bin/ql sql "SELECT count(*) FROM daily"
db/bin/ql daily --code 600000.SH --start 20200101 --end 20201231
db/bin/ql fin --code 600000.SH --period 2020
db/bin/ql sql "SELECT * FROM daily" --format json
db/bin/ql sql "SELECT * FROM daily" --limit 1000
db/bin/ql sql "SELECT * FROM daily" --out /tmp/all.csv
```

- 只读连接：`duckdb.connect(read_only=True)`。
- 默认最多 200 行；超限时 stderr 提示 `--limit` / `--out`。
- `--format table|json|csv`。
- `--out` 写完整结果，不截断。
- `fin --table` 可选 `fina_indicator`（默认）、`income`、`balancesheet`、`cashflow`；`--ann-before` 做 PIT 过滤。

## 5. 质量闸

```bash
python -m db.checks.run all
```

| 检查 | 判据 |
|---|---|
| manifest | 每个分区 manifest 可读、文件存在、字节数与 sha256 匹配 |
| calendar | `daily` 的日期必须落在 `trade_cal.is_open=1`；区间内 open 日不得缺 |
| pk | 每表按 `TABLE_PK` 分组无重复 |
| ohlc | `high >= max(open, close)`、`low <= min(open, close)`、`high >= low`、价格非空 |
| cross-source | 抽样股票用新浪 `stock_zh_a_hist_sina` 比对落盘 `close`，绝对差 ≤ 0.01 元 |
| rowcount | 每日行数 >0，且至少一个 code |

任一失败退出码 1；报告写 `/srv/share/quantdb/meta/checks/checks-YYYYMMDD.json`。

## 6. 政策采集

白名单六源：`gov`、`pbc`、`csrc`、`ndrc`、`mof`、`miit`。配置在 `db/policy/sources.toml`。

规则：

- 只抓白名单主机；跟随官方子域（如 `gks.mof.gov.cn`）但校验根域。
- 正文抽为纯文本，写 `content.txt`。
- `meta.json` 至少含：`publish_date`、`effective_date`、`agency`、`document_number`、`url`、`fetched_at`、`sha256`。
- 双时间轴缺一：不写正文与 meta，写 `policy/missing/YYYYMMDD.jsonl`。
- 同文去重：正文 sha256 相同即跳过；同 URL 也跳过。
- 失败重试 2 次；六源全部列表不可达时退出码 2。

## 7. 测试

```bash
PYTHONPATH=. pytest db/tests -q
```

四类必测：

1. 分区幂等：同参数重跑行数不变、sha256 稳定。
2. 断点续跑：已完成分区不再调用上游。
3. manifest 校验：改字节或删文件后校验失败。
4. `ql` 行数上限：默认截断、`--out` 写全量。

附加：schema 15 表、PIT 列、单位注释、政策双时间轴抽取。
