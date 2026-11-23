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
| `/srv/backup/quantdb/` | 已创建；2026-11-23 首次 rsync + `SHA256SUMS` 校验通过（155,640 个文件，约 13G） |

## 凭据

| 凭据 | 状态 |
|---|---|
| Tushare token 环境变量 | 未设置（不依赖） |
| `/home/moyongkai/.config/quantdb/tushare.token` | 存在，权限 0600（2026-11-22 已就位） |

结论：全市场回填走 Tushare 直连（`api.tushare.pro`），token 只从该文件或 `TUSHARE_TOKEN` 读，不打印、不写日志。

## 网络与端口

- 六政策源列表页实测可达：gov.cn、pbc.gov.cn、csrc.gov.cn、ndrc.gov.cn、mof.gov.cn、miit.gov.cn。
- 本单不需要监听端口；`ql`、ETL、checks 都是命令行进程。
- 新浪行情接口单次全历史调用实测约 0.8 到 1.0 秒。

## 质量门

| 命令 | 实测结果 |
|---|---|
| `pytest db/tests -q` | 162 passed（WORK-529 第四轮修复后实测；含增量区间、源端延迟、合并幂等、混合日期格式、假日直接退出、指数 runner、日历空响应、dividend 空主键回归） |
| `python -m db.checks.run all` | 6 项闸，见 `meta/checks/checks-<date>.json`；空数据一律判 FAIL（缺数据 ≠ 数据干净） |
| `python -m db.etl.stats` | 台账统计（分区/行数/字节/空分区/上游调用/上游耗时） |
| `python -m db.etl.delivery` | 交付快照（环境版本、逐数据集行数与字节、调用与报错数） |
| `systemctl --user list-timers --all \| grep quantdb` | 四个 timer：daily / finance / policy / weekly，均 active |

## 定时任务

WORK-529 起，稳定代码路径是 `/srv/share/quantdb/code/quant-lab`；四个 systemd user timer 已启用：

| timer | 下次触发（2026-11-23 实测） |
|---|---|
| `quantdb-policy.timer` | 18:00 |
| `quantdb-daily.timer` | 18:30（19:30 / 20:30 重试） |
| `quantdb-finance.timer` | 19:00（周一 09:30 也触发；报告期 09:30 只等待） |
| `quantdb-weekly.timer` | 下周一 06:00（对账）/ 09:00（周报） |

冷备已并入现有 `backup-nightly.timer`（03:30）；`/srv/backup/quantdb` 的首次 rsync +
`SHA256SUMS` 实测在 2026-11-23 完成，155,640 个文件校验通过。

冷备脚本与 unit 已版本化到稳定仓库：`db/timers/backup-quantdb.sh`、
`db/timers/backup-nightly.sh`、`db/timers/systemd/backup-nightly.*`；
`/home/moyongkai/bin/backup-nightly.sh` 只是兼容 shim。

实测的上游约束（2026-11-22 直连 `api.tushare.pro`，非 MCP）：

| 接口 | 实测到的行数上限 | 本库的处理 |
|---|---|---|
| `daily` / `daily_basic` / `adj_factor` | 6,000 行 | 按日期窗口取，命中上限自动细分 |
| `stk_limit` | 7,800 行 | 同上 |
| `suspend_d` | 5,000 行 | 同上 |
| `namechange` | 10,000 行（全窗口）/ 按年 241～2,417 行 | 按自然年取，21 次调用后按 `ts_code` 拆分 |
| `index_weight` | 7,000 行 | 按指数 × 月取 |
| `dividend` | 2,000 行 | 按 `ts_code` 取（实测单股 45～93 行） |
| `income` | 170 行安全阈值（实测单窗口最高 178 行） | 按 `ts_code` + 公告日窗口取 |
| `balancesheet` / `cashflow` / `fina_indicator` | 100 行 | 同上；实测全窗口 = 两半窗口之和，未发生截断 |

## 环境阻塞

- 无阻塞项。
- 上游单次返回有行数上限（daily 类 6,000 行、财务四表 100 行），必须按窗口取数并在命中上限时折半重取，否则静默截断。
- `stk_limit` / `suspend_d` 早年上游无数据：已完整取到但没有数据的交易日会落成**空分区**（manifest 记 `empty_upstream: true`）；上游窗口**失败**时不写分区（manifest 缺失 → 下轮重试）。两者必须区分，否则空数据会被当成「还没取过」反复重拉，回填永远跑不完。
