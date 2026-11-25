# Changelog

## 2026-11-25 · WORK-559 夜间备份恢复（docker 组 + 失败自证）

修复：

- `db/timers/backup-nightly.sh`：所有 docker 调用统一走 `dk()`（`/usr/bin/sg docker -c`）。
  `systemd --user` 的服务会话不继承 `107(docker)` 补充组，原脚本 3 处直接 `docker compose`
  （multica / weknora / langfuse 各一处）在 03:30 必然 EACCES；自 2026-11-20 起连续 6 晚失败。
- 三个 `pg_dump` 先写 `<file>.part`，校验大小与 `PGDMP` 头后再改名；任一步失败删除不完整文件。
  原实现用 `>` 直接落盘，失败后留下 0 字节 dump，看起来像一次成功备份。
- 新增 `run_step`：`pg_dump` / `borg create` / `borg prune` / `borg compact` 失败时日志写出
  「步骤 + 库 + 退出码 + stderr 首行」；原日志只有一个 `exit=1`。
- `pg_restore -l` 读不出归档目录即判失败（不再把「头部正常」当通过）。本机客户端落后于容器
  归档格式时会明确报 `unsupported version` 并停止，需升级客户端——宁可响，不可静默收下损坏文件。
- 失败归属细化：进目录失败单独报「进入 <dir>」；每步的临时文件收敛到一次运行一个
  `/tmp/backup-nightly-XXXXXX/` 目录，中断遗留的目录与 `*.dump.part*` 在下次启动时按 `find -mtime +1`（≥48 小时）清理。

文档：

- `docs/ENVIRONMENT.md` 记录「systemd --user 不继承补充组」的实测与 `sg docker` 绕过方式。
- `db/README.md` 删除「borg 原始错误仍需单独处理」一条（已在本单修复）。

## 2026-11-23 · WORK-537 外资研报公开层渠道清单与可抓性实测

新增：

- `docs/foreign_research_channels.csv`：29 个外资研报公开层渠道清单（12 列，实测证据列无空；采 13 / 观察 14 / 不采 2）。
- `docs/foreign_research_brief.md` + `docs/foreign_research_brief.html`：一页结论（配图 base64 内嵌，可打印）。
- `docs/WORK-537/`：五阶段落盘（`01-hypothesis.md`、`02-data-contract.md`、`02-data.csv`、`02-data.sha256`、`03-analysis.md`、`04-audit.md`、`05-memo.md`）。
- `scripts/work537_probe.py`：渠道可抓性探测（robots 优先、同域节流、请求台账、离线重判 reclass）。
- `scripts/work537_audit.py`：独立审计（curl 复跑、静态质量门 G1–G9、台账核查）。
- `scripts/work537_figures.py` / `scripts/work537_build_html.py`：配图与单文件 HTML 构建。

要点（实测结论）：

- 外资投行**评级/目标价在公开层实测不可得**（5 篇原文正文全文命中 0 处；唯一含完整评级的是中资平台转载）。
- 可机器采集入口 13 个，其中投行官网 7 个；建议每周一次增量（约 26 次请求/周）。

修复：

- robots 判定改用**落地 host** 的 robots.txt（原实现误用入口 host，把 BofA 误判为 robots 禁）。
- robots 分组解析按 RFC 9309 重写（原实现把 `googlebot` 组当 `*` 组，把 CNBC 误判为 robots 禁）。
- 「需 JS」判据改为 issue 口径「列表页能解析出标题+日期」（原按文本长度，把 CNBC/NYSE/东方财富误判为直连）。
- 支持无年份日期（“Nov 22”），按页内最新日期推断年份。
- 同域节流改为按**请求完成**时刻计算（原按开始时刻，出现 0.25s 的完成间隔）。
- 请求台账改为追加式落盘（原覆盖式，跨进程丢失 98 条记录）。

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

- `pytest db/tests -q` → 162 passed。
- 行情增量在收盘前 no-op；模拟收盘后无数据返回 75 且不写空分区。
- 财务 2026-11-13 单日实测四次抓取 + dividend 21 个分区，重复跑行数不变。
- 冷备 rsync + `SHA256SUMS` 155,640 个文件校验通过。
- 四个 timer `systemctl --user list-timers` 均 active。
- 第五轮独立定向复审 `ec2fa63`：PASS，0 blocking。

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
