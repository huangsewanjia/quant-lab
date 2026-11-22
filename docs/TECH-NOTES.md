# 技术选型与风险

## 选型原则

按用户 2026-11-21 定的复用顺序：先用成熟开源；缺一小块才二开；最后才自研。每个候选都给出排除理由，不写「不熟悉/没试过」。

## 1. 列式存储

候选：

- Parquet + zstd：WORK-512 已实测 11 列日线 24.3 B/行，20 年全市场约 0.74 GB；列存、压缩、谓词下推成熟。
- CSV：体积大、类型不稳定、无压缩、行数大时扫描慢。
- Feather：更快但长期归档生态弱于 Parquet。

选中：Parquet + zstd，按 `trade_date=YYYYMMDD` 分区。

## 2. 查询引擎

候选：

- DuckDB 2.0.1：单文件、零服务、只读连接、直接读 Parquet、窗口/聚合足够；与 pandas/pyarrow 同栈。
- SQLite：单文件但列存与 Parquet 直读弱，分析型扫描慢。
- ClickHouse：适合大规模服务，但单机部署与运维成本超出本单。
- PostgreSQL/TimescaleDB：需要常驻服务与运维，本单不需要。

选中：DuckDB。代价是单写者；ETL 装载串行，查询只读并发。

## 3. 行情 SDK

候选：

- tushare 1.4.29：官方 SDK，接口字段与限流口径直接对应。
- 直接 requests 调 Tushare HTTP：需要自己维护 URL、参数、错误码与字段，重复造轮子。
- akshare：无 token 时可用，但字段与限流策略不适合作为主库来源；本单只用于冒烟和跨源校验。

选中：tushare 作为主源，akshare 新浪通道作为无 token 冒烟与交叉校验。

## 4. 政策正文抽取

候选：

- BeautifulSoup + lxml + 站点容器选择器：对 gov.cn `#UCAP-CONTENT`、PBC `td.content`、MOF `.TRS_Editor` 等命中率高。
- trafilatura 2.3.1：通用正文抽取成熟，但对 gov.cn 实测误抽导航（只得到 62 字导航文本），只能作兜底。
- newspaper3k：维护活跃度与中文站点适配不如前两者。

选中：站点选择器优先，trafilatura 兜底。配置驱动，新增源只改 `sources.toml`。

## 5. CLI 表格

候选：

- 自写固定宽度格式化：只服务 200 行终端输出，零新增依赖，宽度可控。
- tabulate：成熟，但多一个依赖；本单表格简单。
- rich：交互体验好，但依赖更重，且输出含 ANSI 控制符不利于管道。

选中：自写 ASCII 表格；JSON/CSV 用 pandas 原生序列化。

## 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 无 token | 不能验证 Tushare 真实抓取与全市场行数 | 本单只做参数级/单测级验证；token 到位后另开回填任务 |
| 政策站点改版 | 选择器失效，正文抽空 | 多选择器 + 最大文本块 + trafilatura 兜底；失败写 missing |
| 4,808 个单行 Parquet 文件 | 文件系统块开销约 20 MB，inode 压力 | 单股冒烟可接受；全市场按日整截面后每分区数千行，文件数固定为交易日数 |
| DuckDB 单写者 | 并发写会锁冲突 | ETL 写串行；查询用只读连接 |
| 新浪接口更名 | `stock_zh_a_daily` 在 1.20.46 后移除 | 代码使用 `stock_zh_a_hist_sina`，并在 ENVIRONMENT 记录版本 |
| 财务表字段极多 | 全量建列易漏 | L0 分区保存上游全部列，DuckDB 层按 schema 归一化，不丢字段 |
