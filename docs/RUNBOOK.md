# 运行手册

所有命令默认在 `quant-lab` 仓库根目录执行。`$PY` 指 `/home/moyongkai/.venvs/quantdb/bin/python`。

## 1. 首次建库

```bash
export PYTHONPATH="$PWD"
$PY -m db.etl.fetch init-db
```

验证：

```bash
$PY - <<'PY'
import duckdb
c = duckdb.connect('/srv/share/quantdb/db/quantdb.duckdb', read_only=True)
print(c.execute("select count(*) from duckdb_tables() where schema_name='main'").fetchone())
PY
```

## 2. token

二选一：

```bash
export TUSHARE_TOKEN='...'
```

或：

```bash
mkdir -p /home/moyongkai/.config/quantdb
printf '%s' '...' > /home/moyongkai/.config/quantdb/tushare.token
chmod 600 /home/moyongkai/.config/quantdb/tushare.token
```

不要把 token 写进 `/srv/share`、git、评论或日志。

## 3. 增量拉数

```bash
$PY -m db.etl.fetch --incremental --group market          # 收盘后行情
$PY -m db.etl.fetch --incremental --group finance         # 最近 7 天公告
$PY -m db.etl.fetch --incremental --group index_weight    # 每月首个交易日
$PY -m db.etl.fetch --load-only --incremental --dataset daily

# 旧的手工窗口仍然可用
$PY -m db.etl.fetch calendar --start 20200101 --end 20201231
$PY -m db.etl.fetch stock-basic
$PY -m db.etl.fetch daily --start 20200101 --end 20201231
$PY -m db.etl.fetch adj-factor --start 20200101 --end 20201231
$PY -m db.etl.fetch daily-basic --start 20200101 --end 20201231
$PY -m db.etl.fetch stk-limit --start 20200101 --end 20201231
$PY -m db.etl.fetch suspend --start 20200101 --end 20201231
$PY -m db.etl.fetch financials --codes 600000.SH --datasets income,balancesheet,cashflow,fina_indicator,dividend
$PY -m db.etl.fetch load --datasets all
```

默认 9 次/分钟。并发只影响单进程内并行，速率闸仍是全局的：

```bash
$PY -m db.etl.fetch daily --start 20200101 --end 20201231 --concurrency 19 --rpm 120
```

`--concurrency` 超过 19 会直接报错。

## 4. 断点与失败

- 已完成分区：manifest + 文件 + 字节数 + sha256 全匹配才跳过。
- 失败：每次调用重试 2 次，最终失败写 `/srv/share/quantdb/meta/failures-YYYYMMDD.json`。
- 查看分区：`$PY -m db.etl.fetch status`。
- 重拉某分区：删除该分区 manifest 后重跑，或直接重跑同一命令。

## 5. 质量闸

```bash
$PY -m db.checks.run all
```

单项：

```bash
$PY -m db.checks.run manifest
$PY -m db.checks.run calendar
$PY -m db.checks.run pk --datasets daily,adj_factor
$PY -m db.checks.run ohlc
$PY -m db.checks.run cross-source --sample 5 --tolerance 0.01
$PY -m db.checks.run rowcount
```

失败退出码 1，报告在 `/srv/share/quantdb/meta/checks/`。

## 6. 查询

```bash
db/bin/ql --help
db/bin/ql daily --code 600000.SH --start 20200101 --end 20201231
db/bin/ql fin --code 600000.SH --period 2020
db/bin/ql sql "SELECT trade_date, close FROM daily WHERE ts_code='600000.SH' ORDER BY trade_date DESC LIMIT 5"
db/bin/ql sql "SELECT * FROM daily" --out /srv/share/quantdb/staging/all_daily.csv
```

## 7. 无 token 冒烟

```bash
$PY -m db.etl.smoke_sina --code 600000.SH --years 20
```

产物：

- `/srv/share/quantdb/meta/smoke-report.json`
- `/srv/share/quantdb/meta/smoke-ql.txt`
- `/srv/share/quantdb/raw/daily/trade_date=.../part.parquet`

## 8. 政策采集

```bash
$PY -m db.policy.fetch_policy --source all --max-items 2
$PY -m db.policy.fetch_policy --source gov --list-only
```

查看：

```bash
cat /srv/share/quantdb/policy/last-run.json
cat /srv/share/quantdb/policy/index.json
cat /srv/share/quantdb/policy/missing/$(date +%Y%m%d).jsonl
```

## 9. 备份

冷备到独立盘 `/srv/backup/quantdb/`：

```bash
mkdir -p /srv/backup/quantdb
rsync -a --delete --exclude 'staging/tmp/' /srv/share/quantdb/ /srv/backup/quantdb/
cd /srv/share/quantdb
find raw db meta policy -type f -print0 | sort -z | xargs -0 -r sha256sum > /srv/backup/quantdb/SHA256SUMS
cd /srv/backup/quantdb
sha256sum -c SHA256SUMS --quiet
```

现有 `backup-nightly.timer` 每天 03:30 先做这一步，再跑原来的 borg。`backup-nightly.service`
在 WORK-529 之前就处于 failed 状态；本单没有改 borg 逻辑，borg 的原始错误需单独处理。

### 9.1 定时任务

四个 `systemd --user` timer 已安装并启用：

```bash
systemctl --user list-timers --all | grep quantdb
systemctl --user start quantdb-daily.service
tail -f /srv/share/quantdb/logs/daily-$(date +%Y%m%d).log
```

时间表、手动触发、停用和失败排查见 `../db/README.md` §11。

## 10. 回滚

- 代码：`git revert <sha>`，重新跑 `pytest` 与 `checks`。
- 调度：`systemctl --user disable --now quantdb-daily.timer quantdb-finance.timer quantdb-policy.timer quantdb-weekly.timer`；代码回滚后重新 `cp db/timers/systemd/quantdb-* ~/.config/systemd/user/ && systemctl --user daemon-reload`。
- DuckDB：停止写入，备份 `quantdb.duckdb`，删除后 `init-db` + `load --full-refresh` 从 raw 重建。
- raw 分区：按 manifest 的 sha256 定位异常文件，重拉该分区覆盖。
- 政策：删除 `policy/<source>/<sha16>/` 与 `index.json` 对应条目，重跑采集。
- 冷备：`/srv/backup/quantdb/SHA256SUMS` 可验证副本；恢复时先停 timer，再 `rsync -a /srv/backup/quantdb/ /srv/share/quantdb/`。

## 11. 停手条件

- token 缺失：不做全市场回填。
- `/srv/share` 余量 < 500 GB：停止拉数。
- 同一命令连续失败 2 次：先定位根因，不盲目重跑。
- 政策六源全部不可达：停止并回报，不用转载源替代。
