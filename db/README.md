# quantdb · A 股增量数据底座

代码在 `quant-lab/db/`，数据在 `/srv/share/quantdb/`。本目录只放代码、schema、测试与文档；行情、政策原文、DuckDB 文件一律不入 git。

## 目录

| 路径 | 作用 |
|---|---|
| `/srv/share/quantdb/raw/<dataset>/trade_date=YYYYMMDD/data.parquet` | L0 原始层，按交易日分区；只追加，不按当前在市状态过滤 |
| `/srv/share/quantdb/meta/<dataset>/<partition>.json` | 每分区行数、字节数、sha256、来源、抓取时间 |
| `/srv/share/quantdb/db/quantdb.duckdb` | L1 DuckDB 单文件，schema 见 `schema.sql` |
| `/srv/share/quantdb/policy/` | 政策正文 txt + meta.json + index.json + missing 记录 |
| `/srv/share/quantdb/logs/fetch-YYYYMMDD.jsonl` | 每次上游调用的参数、耗时、行数、状态；不含 token |
| `/srv/share/quantdb/staging/tmp/` | DuckDB 溢写目录 |

## 快速开始

```bash
cd /path/to/quant-lab
export PYTHONPATH="$PWD"
PY=/home/moyongkai/.venvs/quantdb/bin/python

# 建库（15 张 P0 表）
$PY -m db.etl.fetch init-db

# 拉交易日历 + 日线（需要 Tushare token）
$PY -m db.etl.fetch calendar --start 20200101 --end 20201231
$PY -m db.etl.fetch daily --start 20200101 --end 20201231

# 装入 DuckDB
$PY -m db.etl.fetch load --datasets daily,trade_cal

# 查询
db/bin/ql daily --code 600000.SH --start 20200101 --end 20201231
db/bin/ql sql "SELECT count(*) FROM daily"
db/bin/ql fin --code 600000.SH --period 2020
```

`ql` 默认最多显示 200 行；超限时 stderr 提示用 `--limit N` 或 `--out FILE`。支持 `--format table|json|csv`。

## Token

只从以下两处读取，二选一：

- 环境变量 `TUSHARE_TOKEN`
- `/home/moyongkai/.config/quantdb/tushare.token`（权限 600）

日志、manifest、评论与提交都不会写明文 token。

## 口径

- `daily` 只存原始 `close`；复权在查询期用 `adj_factor` 计算，不落死。
- 财务表保留 `ann_date` / `f_ann_date`；回测按公告日入样，`end_date` 只作报告期。
- `vol` 单位手，`amount` 单位千元，`total_mv` 单位万元；新浪 `volume` 是股、`amount` 是元，入库前分别 ÷100、÷1000。
- 交易日历是唯一日期源；分区和行数勾稽对 `trade_cal`。

## 质量闸

```bash
$PY -m db.checks.run all
```

包含：manifest sha256、日历勾稽、主键唯一、OHLC 边界、跨源抽样（新浪 vs 落盘 close，容差 0.01 元）、每日行数。任一失败退出码 1。

## 无 token 冒烟

```bash
$PY -m db.etl.smoke_sina --code 600000.SH --years 20
```

走本机 `/home/moyongkai/mcp/akshare-one-mcp/bin/python` 的 akshare 1.20.65 新浪通道，完成 raw→manifest→DuckDB→`ql` 全链路，报告写到 `/srv/share/quantdb/meta/smoke-report.json`。

## 政策采集

```bash
$PY -m db.policy.fetch_policy --source all --max-items 2
```

白名单六源见 `policy/sources.toml`：国务院、央行、证监会、发改委、财政部、工信部。只有 `publish_date` 与 `effective_date` 都齐全的条目才写正文；缺一写入 `policy/missing/YYYYMMDD.jsonl`，不入库。同文按正文 sha256 去重。

## 测试

```bash
PYTHONPATH=. $PY -m pytest db/tests -q
```

覆盖分区幂等、断点续跑、manifest 校验、`ql` 行数上限、schema 完整性与 PIT 列、政策双时间轴抽取。
