# Changelog

## 2026-11-25 · WORK-568 行业层入库与定更

新增：

- `db/schema_industry.sql`：17 张 `industry_*` 表（列名 snake_case、单位进列注释、每表主键）、`industry_catalog_map`（data_catalog 指标名 → 表/字段的人工映射）、`industry_load_audit`（每次装载审计）、`v_industry_catalog_coverage` 与汇总视图。
- `db/etl/industry.py`：按「源 × 期」分区替换的幂等装载；脏源用 `row_hash` 主键保留原文行，自然键源装前强校验唯一；日期/月份解析保留 `*_raw`，解析失败写 NULL，不推断。
- `db/checks/industry.py`：7 道闸（行数 / 主键 / 日期域 / 单位勾稽 / 审计一致 / 目录覆盖 / 新鲜度），任一 FAIL 退出 1。
- `db/timers/industry.sh`：抓取失败重试 2 次、装载+闸整体重试 2 次、与其它 timer 共用 DB 锁；日频/月频两档。
- `db/timers/systemd/quantdb-industry.{service,timer}`、`quantdb-industry-monthly.{service,timer}`。
- `db/tests/test_industry.py`：解析、去重、分区幂等、主键冲突共 9 项测试。
- `docs/industry-ingest-2026.md`：源 → 表 → 字段 → 单位 → 频率 → 口径 → 一手 URL，含 6 项已知缺陷的实测证据与回滚。

要点（实测）：

- 落盘 17 个源（国内 10 + 国际 7）全部进库，16,834 行；同参数重跑行数不变。
- GFEX `turnover` 源标「元」实为万元：`turnover×10000/volume` 在 1326 个合约×交易日里有 1325 个落在当日 low..high。
- `cnesa_storage` 只有 2 行是提取器覆盖不足（公开页另有 66.43GW、144.7GW、121.5GWh 等 12+ 个数字），不是源只公开 2 条。
- `data_catalog` 405 条中 47 条有可查记录（数值 42 + 文本索引 5；严格完整 8 条），压在 `v_industry_catalog_coverage`，映射孤儿 0 条。

修复（第 1 轮独立复审，`1d8b9d4` → 本提交）：

- `parse_number` 吞掉前导负号：benchmark 16 个指数里 10 个 `change_pct` 涨跌方向写反；改为保留 `-`/`▼` 为负号，新增 2 个回归测试。
- C4 `nea` 子闸按行计数恒 `pairs=0` 假 PASS：改为同文章同期取当月值，实测 `pairs=1, ok=1`；`pairs=0` 现在判 FAIL。
- C4 删除不可能成立的 100ppi 跨单位比值闸，改为 `unit_vocabulary`（10 张带单位表）；单闸报告不再覆盖当日 7 闸报告。
- 覆盖口径改为「有可查记录」并单列 `text_only`；docstring/注释 3 处 nit 修正。

## 2026-11-25 · WORK-572 行业层历史回填

改动（只动抓取窗口/取法，不动 `db/etl/industry.py`、`db/checks/industry.py`、表结构与 A 股表）：

- GFEX 碳酸锂：`DAYS 180 → 1825`，起点夹到上市日 2023-07-21；新增 `--resume` 只补缺日（实测 67 个工作日 / 108 秒）。
- LPR：换 `strStartDate/strEndDate` 按日历年切片（`pageNum/startDate` 被服务端忽略），2019-08-20 起 8 个窗口。
- 巨潮公告：`--days 30 → 1095`、`--max-pages 400 → 1200`，按响应 `totalpages` 全取并记 `truncated`。
- 中汽协：回溯改按自然月（`--months 78`），`--articles/--scan` 可配；乘联分会：新增列表翻页（`--pages`）。
- 财政部：新增 `index_<n>.htm` 列表翻页；统计局：同法翻页，每组取 `--articles` 篇。
- 能源局：搜索改 `sort=date` + `pageSize=500` + 早停，取 `originUrl` 作首选 URL；人民银行：按年度目录循环（2015–2026 自动发现，缓存 `year_dirs.json`）。
- OWID：取全部有值年份（原 `sel[-2:]` 只取最近 2 年）；Comtrade：按年循环 2019–2024。

实测（2026-11-25 18:00 统计时点）：

- `industry_*` 17 表 16,834 → **241,892 行**；11 个源有新增，6 个快照/新闻类源上游无历史序列，保持原样。
- 质量闸 7/7 PASS；同参数连跑两次装载 17 表行数完全一致；A 股 20 表装载前后零 diff。
- 未取到：NEA 2021–2023 月度稿（索引 0 命中 + 部分文章 URL 404 + 表格是 PNG）、Comtrade HS 854143 2019–2021 上游无记录、GFEX 1,000 交易日（碳酸锂 2023-07-21 才上市，811 个开市日无亏缺）。

新增文件：`scripts/work572_verify.py`（覆盖/幂等/抽点/A 股零 diff 核对）、`scripts/work572_build_delivery.py`（交付页生成）、`docs/DELIVERY-WORK-572.html`。抓取脚本本体在 `/srv/share/quantdb/code/industry_fetch/` 与 `raw/industry_alt/*/fetch.py`，不在本仓库（既有形态），改动以 `meta/industry/work572-code-sha256.txt` 锁版。

## 2026-11-25 · WORK-576 行业层定时抓取改增量

修复：

- `db/timers/industry.sh`：daily / monthly 两档**显式传窗口参数**，不再依赖各脚本 argparse 默认值；日志逐源打印 `args <源>: …`，便于事后核对。
- 抓取脚本统一 `--mode incremental|backfill`（`_common.add_mode_arg` / `resolve_mode`）：所有深度旋钮改 `default=None`，默认档 = 小窗口，回填必须显式 `--mode backfill`；显式数值优先。
  - GFEX：`--days` 默认 1825 → 日常 30，`--resume` 两档默认开（`--no-resume` 强制重取）。
  - 巨潮 90 天 / 60 页、中汽协 3 个月 / 400 id、财政部 4 页 / 24 篇、乘联 3 页 / 30 篇、能源局每词 3 页 + 当年扩词、人民银行当年、统计局 6 页 / 每组 6 篇。
- manifest 增 `params.mode`，回填语义字段从 `backfill_*` 改为实际值（`window_days` / `pages_scanned` 等），不再写「回填」标签。

实测（2026-11-25 20:27，日志 `logs/industry-20261125.log`）：

- `industry.sh daily`：拿到数据库锁后 **67 秒**完成抓取 + 装载 + 7 道闸；GFEX `workdays_queried=3`（改前 874 个工作日 / 20–25 分钟）。
- `industry_*` 17 表 241,892 → 263,698 行（增量未丢历史；含同日并行回补的 exchange / cnesa 行）。
- 7/7 PASS；`pytest db/tests/test_industry.py -q` 11 passed（需 `TMPDIR=/srv/backup/tmp`，`/tmp` 是 7.5G tmpfs 且已用 80%，默认 TMPDIR 会 `Disk quota exceeded`）。
- 文档 §3 / §10.2 / §11 同步新口径；回填命令全部标注「手动、非定时」。

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
