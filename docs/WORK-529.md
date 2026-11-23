# WORK-529 定时更新规格与验收台账

## 1. 目标与范围

**用户**：维护 quantdb 的研究/数据人员。**问题**：行情、财务、指数成分和政策库此前依赖手工跑任务目录，
任务目录被清理后无法复现，且没有失败告警。**目标**：把更新迁到稳定代码路径，按交易日历自动增量更新，
失败可追溯、可重试、可停用、可回滚。

**做**：行情/财务/指数成分/政策/对账/周报/冷备的 systemd timer；增量水位；幂等装载；失败告警；文档。

**不做**：不改 `db/schema.sql`；不动已落 Parquet；不做全量重载（首次建库除外）；不新增数据源。

## 2. 稳定路径与基线

- 代码：`/srv/share/quantdb/code/quant-lab`（git clone，更新用 `git pull --ff-only`）。
- 数据：`/srv/share/quantdb`。
- 冷备：`/srv/backup/quantdb`（rsync + `SHA256SUMS`）。
- 基线：`8d6ca2ecd634722ef9766d418df456243c64ef94`（`agent/work-518-fix`）。
- 工作分支：`agent/work-529-timers`。

## 3. 时间表

| 任务 | 频率 | 时间 | 内容 |
|---|---|---|---|
| 行情 | 每交易日 | 18:30；19:30 / 20:30 重试 | `daily` `daily_basic` `adj_factor` `stk_limit` `suspend_d` `index_daily` + `stock_basic` |
| 财务 | 报告期每交易日；其余周一 | 19:00；其余周一 09:30（19:00 失败重试） | `income` `balancesheet` `cashflow` `fina_indicator` `dividend` |
| 指数成分 | 每月首个交易日 | 19:30（daily 第二次触发） | `index_weight` |
| 政策 | 每天 | 09:00 / 18:00 | 六源增量 → 入库 |
| 政策对账 | 每周一 | 06:00 | 列表 vs 库内，补抓 |
| 周报 | 每周一 | 09:00 | 成功率 / 缺口 / 重试 / 行数变化 |
| 冷备 | 每天 | 03:30 | quantdb → `/srv/backup/quantdb` |

## 4. 关键实现

| 件 | 位置 | 说明 |
|---|---|---|
| 增量计划/装载 | `db/etl/incremental.py` | 水位、财务披露窗口、幂等装载、watermark |
| ETL 扩展 | `db/etl/fetch.py` | `--incremental`、`--group`、`--overlap-days`、合并分区、源端延迟 |
| 周报 | `db/etl/weekly.py` | 调用成功率、失败清单、日历缺口、行数变化 |
| 政策增量/对账 | `db/policy/fetch_policy.py` | 已知 URL 优先跳过；`--reconcile` 有界补抓 |
| wrapper | `db/timers/*.sh` | 日志、flock、日历检查、失败告警 |
| systemd | `db/timers/systemd/*` | 4 个 service + 4 个 timer |

### 4.1 上游事实（2026-11-23 实测）

| 接口 | 结论 |
|---|---|
| `disclosure_date(actual_date=YYYYMMDD)` | 可用；用于找当日实际披露的代码 |
| `income_vip` / `balancesheet_vip` / `cashflow_vip` / `fina_indicator_vip` | 无权限（40203） |
| `dividend(ann_date=YYYYMMDD)` | 可用；截面返回当日除权除息事项 |
| `income` / `balancesheet` / `cashflow` | 日期参数按 `ann_date` 过滤 |
| `fina_indicator` | 日期参数按 `end_date`（报告期）过滤 |

因此财务增量窗口同时覆盖 `actual_date` 和 `end_date`，而不是只用一个 7 天公告窗口。

## 5. 验收矩阵

| # | 验收项 | 证据 | 结论 |
|---|---|---|---|
| 1 | 稳定路径是 git 仓库 | `git -C /srv/share/quantdb/code/quant-lab log -1` | PASS |
| 2 | 增量区间正确 | `db/tests/test_incremental.py`：空库、正常、跨节假日、源端延迟 | PASS |
| 3 | 幂等装载 | 同数据装两次行数不变；新增分区后 max 前移 | PASS |
| 4 | 四个 wrapper | `db/timers/*.sh`；`bash -n` 全通过 | PASS |
| 5 | systemd 单元 | `systemctl --user list-timers` 四个 timer active | PASS |
| 6 | 失败告警 | 错误 token 路径触发 `WORK-533`（验证后已取消） | PASS |
| 7 | 手动验证 | `quantdb-daily.service` 2026-11-23 10:25 实测 exit 0，日志与 0 个新分区 | PASS |
| 8 | 文档 | `db/README.md` §11、`docs/RUNBOOK.md` §9/§10 | PASS |

## 6. 选型与排除

| 候选 | 结论 | 理由 |
|---|---|---|
| `systemd --user` timer | 选中 | 与现有 `backup-nightly.timer` 同套；有 `Persistent`、`list-timers`、journal |
| cron | 排除 | 无统一的失败/重试/状态视图；用户已明确要求 systemd |
| Airflow / Prefect | 排除 | 为一个本机单用户 ETL 引入服务端调度器，运维面过大 |
| 自研调度器 | 排除 | 需求可由 systemd + 现有 Python ETL 直接满足 |
| `flock` | 选中 | util-linux 自带，避免 DuckDB 单写锁冲突 |
| `rsync` + `sha256sum` | 选中 | 冷备可单文件恢复、可校验；与 borg 并存 |
| `disclosure_date` | 选中 | 避免每次 5,924 只股票 × 4 张表的全量扫描；有权限且实测可用 |
| `income_vip` 等 | 排除 | 无权限（40203），实测不可用 |

## 7. 风险与遗留

- 政策库历史欠账：对账单轮默认补抓 200 条，`remaining` 逐周下降。
- 非报告期周一财务任务可能跑几十分钟；只处理最近 7 天实际披露的代码。
- `backup-nightly.service` 在 WORK-529 之前就是 failed；本单没有改 borg，borg 错误需单独处理。
- `daily.sh` 在 19:30 / 20:30 的第二次/第三次触发既承担行情重试，也承担每月指数成分任务。
- issue 的 `M = 15M` 与 `AGENTS.md` 的「不设 issue 级 token 总额」冲突；按 `AGENTS.md` 执行。

## 8. 实测证据（2026-11-23）

| 项 | 命令 | 结果 |
|---|---|---|
| 单测 | `pytest db/tests -q` | 156 passed |
| 数据闸门 | `python -m db.checks.run all --out .../checks-20261123-work529.json` | `ok: true`，G1–G6 全 PASS |
| 台账 | `python -m db.etl.stats --json` | 16 个数据集 / 65,919 分区 / 102,391,068 行 / 6,290,875,840 字节 |
| 手动 daily | `systemctl --user start quantdb-daily.service` | exit 0；2026-11-23 10:25；0 个新行情分区；日志 `logs/daily-20261123.log` |
| 源端延迟 | `fetch --incremental --group market --now 2026-11-23T18:35:00+08:00`（临时 home） | exit 75；`daily/20261123` 无分区、失败清单记 `source_delay` |
| 财务幂等 | 临时 home 连续两次 `fetch --incremental --group finance --overlap-days 1 --now 2026-11-13T19:00:00+08:00` | 行数保持 income 2 / balancesheet 2 / cashflow 1 / fina_indicator 2 / dividend 33 |
| 指数独立 | 临时 home 伪造 19:35 + 错误 token 跑 `daily.sh` | market=1 时仍执行 index_weight，日志 `daily finished market=1 index_weight=1 final=1` |
| 告警 | 错误 token 路径运行 `daily.sh` | 建单 `WORK-533`，验证后已取消 |
| 冷备 | rsync + `SHA256SUMS` | 155,640 个文件校验通过；`/srv/backup/quantdb` 13G |
| 定时器 | `systemctl --user list-timers --all` | daily 18:30 / finance 19:00 / policy 18:00 / weekly 下周一 06:00 |

## 9. 回滚

```bash
systemctl --user disable --now quantdb-daily.timer quantdb-finance.timer \
  quantdb-policy.timer quantdb-weekly.timer
cd /srv/share/quantdb/code/quant-lab
git revert <delivery-sha>
cp db/timers/systemd/quantdb-* ~/.config/systemd/user/
systemctl --user daemon-reload
```

数据回滚：停 timer → 用 `/srv/backup/quantdb/SHA256SUMS` 校验/恢复 → 必要时按
`docs/RUNBOOK.md` §10 从 raw 重建 DuckDB。

## 10. 独立审查处置

第一轮独立审查（提交 `10c9813`）给出 9 条 blocking；已全部修复：

| 编号 | 修复 |
|---|---|
| B1 | `fetch_index_weight` 补传 `concurrency`，新增 runner 回归 |
| B2 | 指数成分空响应按源端延迟返回 75；成功才写完成 marker |
| B3 | 关键行情数据集对全部待补交易日启用延迟保护；`stk_limit` / `suspend_d` 显式允许合法空分区 |
| B4 | 装载改为按 DuckDB 水位补装所有更晚 raw 分区，抓取/装载崩溃可自愈 |
| B5 | daily wrapper 先查日历，非交易日不创建客户端直接退出 0 |
| B6 | 财务 timer 增加周一 09:30；报告期 09:30 等待 19:00，非报告期 09:30 失败后 19:00 重试 |
| B7 | 政策同文别名写入 `index.json` 的 `alias_urls`，跨轮对账跳过 |
| B8 | 政策源级/条目级错误返回非 0，触发告警 |
| B9 | 冷备脚本与 unit 移入稳定仓库；home 脚本变 shim |

第二轮复审在修复提交上重新执行，不复用第一轮会话。

### 第二轮复审的 6 条 blocking（已修）

| 编号 | 修复 |
|---|---|
| R1 | `stk_limit` / `suspend_d` 的合法空响应不再按源端延迟处理 |
| R2 | 增量 `dividend` 写分区前清理空主键，`rows_dropped_null_pk` 入 manifest |
| R3 | 日历刷新先校验非空、覆盖区间和 `is_open`，异常时保留旧分区 |
| R4 | daily 的 19:30 / 20:30 分支无条件执行 `index_weight`，再合并退出码 |
| R5 | 冷备从源目录生成 `SHA256SUMS`，覆盖 `raw/db/meta/policy`，在目标目录校验 |
| R6 | `--group index_weight` 与 `market` 一样先查日历，节假日不创建客户端 |
