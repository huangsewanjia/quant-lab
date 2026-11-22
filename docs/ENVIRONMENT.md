# 环境事实

采样时间：2026-11-22（Asia/Shanghai）。

## 主机

| 项 | 实测值 |
|---|---|
| OS | Ubuntu，Linux 7.0.0-43-generic，x86_64 |
| CPU | Intel Core i3-12100F，4 核 8 线程 |
| 内存 | 14 GiB total，10 GiB available |
| Swap | 8 GiB total，3.4 GiB free |
| `/` SSD | 233 GB，168 GB available |
| `/srv/share` HDD | 2.0 TB，2.0 TB available |
| `/srv/backup` 独立盘 | 3.6 TB，3.6 TB available |

## Python 与依赖

| 项 | 值 | 证据 |
|---|---|---|
| 系统 Python | 3.14.4 | `/usr/bin/python3` |
| quantdb venv | `/home/moyongkai/.venvs/quantdb` | `uv venv --python /usr/bin/python3.14` |
| duckdb | 2.0.1 | venv import |
| pandas | 3.1.1 | venv import |
| pyarrow | 26.0.1 | venv import |
| tushare | 1.4.29 | venv import |
| six | 1.17.0 | tushare 依赖补齐 |
| requests | 2.34.2 | venv import |
| beautifulsoup4 | 4.15.0 | venv import |
| lxml | 6.1.3 | venv import |
| trafilatura | 2.3.1 | venv import |
| pytest | 9.2.0 | venv import |
| akshare | 1.20.65 | `/home/moyongkai/mcp/akshare-one-mcp/bin/python` |

精确版本见 `../db/requirements.txt`。

## 数据目录

| 路径 | 状态 |
|---|---|
| `/srv/share/quantdb/raw` | 存在，4,808 个 daily 分区 + 1 个 trade_cal 分区 |
| `/srv/share/quantdb/db` | 存在，`quantdb.duckdb` 1.3 MB |
| `/srv/share/quantdb/meta` | 存在，4,811 个 JSON（含 manifest 与检查报告） |
| `/srv/share/quantdb/policy` | 存在，5 篇入库，6 条 missing |
| `/srv/share/quantdb/staging` | 存在，含 `tmp/` 溢写目录 |
| `/srv/share/quantdb/logs` | 存在 |
| `/srv/backup/quantdb/` | 尚未创建；RUNBOOK 给出创建与冷备命令 |

## 凭据

| 凭据 | 状态 |
|---|---|
| Tushare token 环境变量 | 未检测到 |
| `/home/moyongkai/.config/quantdb/tushare.token` | 不存在 |

结论：本单不依赖 token，单股冒烟走 akshare 新浪通道；全市场回填需要 token 就位后另开任务。

## 网络与端口

- 六政策源列表页实测可达：gov.cn、pbc.gov.cn、csrc.gov.cn、ndrc.gov.cn、mof.gov.cn、miit.gov.cn。
- 本单不需要监听端口；`ql`、ETL、checks 都是命令行进程。
- 新浪行情接口单次全历史调用实测约 0.8 到 1.0 秒。

## 质量门

| 命令 | 实测结果 |
|---|---|
| `pytest db/tests -q` | 39 passed |
| `python -m db.checks.run all` | 6 项 PASS |
| `python -m db.etl.smoke_sina` | 4,808 行，raw 32.5 MB，DuckDB 装载 5.2 s，ql 正常 |

## 环境阻塞

- 无阻塞项。
- 全市场历史回填需要 Tushare token；本单按计划不拉全市场。
