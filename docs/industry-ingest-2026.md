# 行业层入库与定更（WORK-568）

> 统计时点：2026-11-25 11:00–12:00（Asia/Shanghai）。
> 范围：把 `raw/industry/`（国内 10 源）与 `raw/industry_alt/`（国际 7 源）已落盘的 **17 个源**规范化成 `industry_*` 表，配 7 道质量闸与日/月两档 systemd timer。
> 不在范围：重写抓取逻辑、行业结论、研报、修改 `data_catalog` 405 条、修改既有 20 张 A 股表与 `v_daily_qfq/v_daily_hfq`。

## 1. 一句话结论

17 个源全部进了 DuckDB（`industry_*` 17 张表、16,834 行），每次装载按「源 × 期」分区替换，同参数重跑行数不变；7 道闸全 PASS；日频/月频两个 timer 已安装；`data_catalog` 405 条中 47 条有可查记录（数值 42 + 公告/新闻稿文本索引 5；严格「完整」8 条）。

## 2. 源清单与口径（源 → 表 → 字段 → 单位 → 频率 → 口径 → 一手 URL）

表名前缀统一为 `industry_`；「频率」是**上游发布频率**，定时任务按日/月两档调度（日频档也会跑月频源的成本很低，见 §5）。

| # | 源 | 表 | 主要字段 | 单位 | 频率 | 口径要点 | 一手 URL |
|---|---|---|---|---|---|---|---|
| 1 | 广州期货交易所·碳酸锂 | `industry_gfex_lithium_carbonate` | trade_date, contract, field, value | 元/吨；手；**万元** | 日 | 全合约逐日 9 字段；`turnover` 源标「元」实为万元（§4.1） | `http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList` |
| 2 | 中国货币网·LPR | `industry_chinamoney_lpr` | quote_date, tenor, value | % | 每月 20 日 | 1Y / 5Y+ 两个期限 | `https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis?lang=CN` |
| 3 | CNESA·储能年度数据 | `industry_cnesa_storage` | period, indicator, value | GW；% | 年度 | 电力储能累计装机、同比；抓取只提到 2 个指标（§4.2） | `https://www.cnesa.org/information/detail/?column_id=1&id=7798` |
| 4 | 巨潮资讯·公告索引 | `industry_cninfo_announcements` | announcement_id, keyword, category, published_date | — | 事件 | 半导体/锂业/有色金属三关键词 30 天窗口；只有元数据与 PDF 链接 | `http://www.cninfo.com.cn/new/hisAnnouncement/query` |
| 5 | 中汽协·产销 | `industry_caam_auto_output` | period, indicator, value, scope | 万辆 | 月度 | 当月/累计，汽车/乘用车/商用车/新能源，含出口量 | `http://www.auto-stats.org.cn/ReadArticle.asp?NewsID=...` |
| 6 | 乘联分会·新能源 | `industry_cpcaauto_nev` | period, kind, indicator, value | 万辆；%；条 | 月度 | 零售/批发/渗透率；`report_index` 行只记报告存在 | `http://www.cpcaauto.com/newslist.php?types=bgzl` |
| 7 | 财政部·财政收支 | `industry_mof_fiscal` | period, indicator, value | 亿元 | 月度（累计） | 一般公共预算/政府性基金收支；日期与期有解析错误（§4.3） | `https://gks.mof.gov.cn/tongjishuju/` |
| 8 | 国家能源局·电力统计 | `industry_nea_power` | period, indicator, value | 亿千瓦；亿千瓦时；小时 | 月度 | 装机、分产业用电量、利用小时；同指标混当月/累计（§4.4） | `https://www.nea.gov.cn/` |
| 9 | 人民银行·社融 | `industry_pbc_shrzgm` | period, indicator, value | 亿元 | 月度 | 增量分项（人民币贷款/表外/债券等） | `http://www.pbc.gov.cn/diaochatongjisi/` |
| 10 | 国家统计局·发布 | `industry_stats_gov_release` | period, group_name, indicator, row_label, col_header, value | %；亿元；万吨；亿块… | 月度 | 六组原表单元格；利润表表头错位（§4.5） | `https://www.stats.gov.cn/sj/zxfb/` |
| 11 | Benchmark 矿物指数 | `industry_benchmark_public_index` | snapshot_date, kind, name, value, change_pct | 点位；% | 日（页面随时更新） | 16 个指数 + 10 条简报索引；CSV 内重复 3 遍 | `https://www.benchmarkminerals.com/price-assessments` |
| 12 | UN Comtrade | `industry_comtrade_trade_values` | hs_code, reporter, period_year, flow, value_usd, netweight_kg | 美元；千克 | 年度 | 5 个 HS、中国出口、2024 | `https://comtradeapi.un.org/public/v1/preview/C/A/HS` |
| 13 | 交易所快照 | `industry_exchange_daily_quotes` | exchange, contract, month, trade_date, close, settle, volume, oi | 元/吨；手 | 日 | 一次抓一个交易日（GFEX/SHFE） | `http://www.gfex.com.cn/...`、`https://www.shfe.com.cn/data/tradedata/...` |
| 14 | OWID/IEA/USGS | `industry_ow_iea_usgs_timeseries` | slug, entity, year, value | 辆；%；吨；GW | 年度 | 电动车销量/渗透率、锂钴产量、CO2、光伏累计 | `https://ourworldindata.org/grapher/<slug>.csv` |
| 15 | 生意社·挂牌价 | `industry_ppi_spot_prices` | goods_id, goods, spec, market, supplier, as_of, price, price_unit | 元（元/吨、元/公斤） | 日 | **厂商挂牌价，非地区均价**；原始文本保留 | `https://www.100ppi.com/mprice/plist-1-<id>-1.html` |
| 16 | SNE Research | `industry_sne_public_battery` | release_id, title, publish_date, key_figure | — | 事件（新闻稿） | 只有标题与关键数字文本，未解析结构化数值 | `https://www.sneresearch.com/en/insight/release/` |
| 17 | TrendForce 现货 | `industry_trendforce_spot_price` | snapshot_date, series, spec, value_text, value, unit_kind | USD；% | 日 | DRAM/NAND 现货；value 混价格与涨跌幅（§4.6） | `https://www.trendforce.cn/price/dram/dram_spot` |

### 2.1 「18 个源」的核对

issue 写「11 个国内 parquet + 7 个国际 csv」。实测：

```bash
ls -d /srv/share/quantdb/raw/industry/*/        # 10
ls -d /srv/share/quantdb/raw/industry_alt/*/    # 7
```

国内只有 **10** 个。差的 1 个是 issue 提到的「汇率」：`raw/` 下没有任何汇率源的 manifest 或 parquet，`code/industry_fetch/` 也没有对应 fetcher。**结论：汇率源未落盘，本单无法装载**，已在 §6 登记为缺口；不伪造、不拿 LPR 顶替汇率。

## 3. 复跑命令（绝对路径）

```bash
# 1) 只装载（读 raw，写 DuckDB；需要独占写，用统一锁）
cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  flock -w 7200 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load

# 2) 质量闸（只读）
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all

# 3) 手工触发定时任务（抓取 + 装载 + 闸）
systemctl --user start quantdb-industry.service          # 日频
systemctl --user start quantdb-industry-monthly.service  # 月频

# 4) 只装一个源（排查）
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load --sources cnesa_storage
```

装载报表：`/srv/share/quantdb/meta/industry/load-<run_id>.json`（每源 raw/staged/loaded/分区数）。
质量闸报告：`/srv/share/quantdb/meta/checks/industry-<YYYYMMDD>.json`。
任务日志：`/srv/share/quantdb/logs/industry-<YYYYMMDD>.log`。

## 4. 已知缺陷（实测证据，不猜、不修）

### 4.1 GFEX 成交额单位是万元，不是元

fetcher 把 `turnover` 标为「元」。用当日 `low..high` 反推 VWAP：

```sql
-- 1326 个「合约×交易日」中 1325 个的 turnover*10000/volume 落在 [low*0.995, high*1.005]
```

若 turnover 真是元，比值会差 10000 倍、几乎全部落在区间外。装载层把该字段单位改写为「万元」，并在 C4 闸里每次复核。

### 4.2 CNESA 只有 2 行：是抓取失败，不是源只公开 2 条

公开页 HTML（`/tmp/cnesa_7798.html`，HTTP 200，341,562 字节）正文至少含这些带单位数字：
66.43GW、189.48GWh、213.3GW、+54%、31.3%、144.7GW、+85%、100GW、35%、58%、8%、+52%、+73%、121.5GWh、+140.1%、206.3GWh、+125.5%。

fetcher 的 6 条正则只命中 2 条（`中国电力储能累计装机规模`、`累计装机规模同比增速`），其余句式（「新型储能累计装机规模达到 144.7GW，同比+85%」「新增投运 66.43GW/189.48GWh」）未覆盖。
**结论：抓取/提取覆盖不足**。本单按 issue 要求不改抓取逻辑；该源按「部分覆盖」计入目录映射，不按「已入库完整」计。

### 4.3 财政部：published_date 全错、period 与文章年份冲突

- 37 行 `published_date` 全部是 `2017年11月21日`，而 `source_url` 是 2026 年文章（`t20261023_...`）。
- 12 行 `period=2017-11`（如「全国一般公共预算收入 172292 亿元」），与同文其余 2026 年期并排，属提取串期。

处置：`published_date` 由 URL 的 `tYYYYMMDD` 派生（可证的发布日期），原始文本保留在 `published_date_raw`；`period` **不推断**，原样保留，质量闸只做日期域检查。引用 2017-11 那 12 行前必须人工回原文核对。

### 4.4 国家能源局：同一指标名混「当月/累计」

例：2026-08 同一文章下「城乡居民生活用电量」同时有 1,883 与 11,132 两个值（当月 vs 累计），fetcher 未区分。故 `industry_nea_power` 用 `row_hash` 作主键保留全部行，业务键重复数在 C2 里如实报出；查询时必须结合 `value` 量级或回原文判断口径。

### 4.5 统计局利润表：表头与数值错位一列

`table_index=0,row_index=2` 的 `col_header='营业收入/同比增长（%）'` 对应 `value=1062066`（明显是亿元营收，不是同比 %），下一列才是 6.60。本层原样保留单元格与行列号，不猜正确配对；C6 映射该指标记为「部分」。

### 4.6 TrendForce：value 列混价格、涨跌幅与表头

CSV 里同一列出现 `60.70`（价格）、`▲ 1.85 %`（涨跌幅）、`盘涨跌幅`（分组表头）。装载层保留 `value_text`，解析数值到 `value`，用 `unit_kind ∈ price/pct_change/header/unparsed` 区分。按价格使用时应过滤 `unit_kind='price'`。

### 4.7 100ppi 跨单位勾稽不成立（因此没有做比值闸）

设想用「同货同供方同日 元/公斤 × 1000 ≈ 元/吨」勾稽，实测无法成立：落库表 `price_unit` 的实际取值是 `吨`(26) / `千克`(4) / `公斤`(1) / NULL(1)（raw CSV 33 行，row_hash 去重后 32 行），而每家公司只报一种单位——碳酸锂 190 元/公斤来自「湖北七八九化工」，174000 元/吨来自「山东亿禾」，不存在同一供方同日双单位样本。硬做一个 `pairs=0` 的闸等于空转，故删除该子闸，改成 C4 的 `unit_vocabulary`：有 unit 列的 11 张表只允许 schema 注释里写明的单位，出现新单位即 FAIL（11 张全过）。NULL 单位仅在数值也为 NULL 时放行（100ppi 的分类标题行）。

### 4.8 其他小问题（不影响本次交付）

- `comtrade` 1 行 HTTP 500（HS 253090 锂辉矿），以 NULL 值 + status 保留，不删行。
- `sne` 1 行 HTTPS 连接失败（release 776），status 列保留错误文本。
- `benchmark` CSV 把 16 个指数重复写了 3 遍，row_hash 去重后 26 行（16 指数 + 10 简报）。
- `cnesa` 的 `caliber` 列只有第 2 行有值。
- 100ppi 有 1 行空壳（goods_id=59 黄金，除货名外全空），作为分类标题行的抓取残留保留，`unit_vocabulary` 已放行。

## 5. 调度（systemd --user）

| 单元 | 档位 | 时间 | 跑什么 |
|---|---|---|---|
| `quantdb-industry.timer` → `.service` | 日频 | 06:10，07:10/08:10 重试 | GFEX 碳酸锂、LPR、交易所快照、生意社现货、TrendForce、Benchmark |
| `quantdb-industry-monthly.timer` → `.service` | 月频 | 每月 3 日 06:40 | 中汽协、乘联会、CNESA、能源局、财政部、人民银行、统计局、巨潮、SNE、Comtrade、OWID |

- 抓取失败 **重试 2 次**（共 3 次）；仍失败只记日志并继续装载已有 raw（数据旧但不空）。
- 装载 + 质量闸整体再重试 2 次，全失败才 `alert_failure` 建告警单。
- 与 daily/finance/policy/weekly/backup 共用 `/srv/share/quantdb/logs/.quantdb-db.lock`，**串行写库**。
- 日志 `/srv/share/quantdb/logs/industry-<YYYYMMDD>.log`。

## 6. 目录覆盖（`v_industry_catalog_coverage`）

口径：`coverage` 二值（已覆盖 = 表里能查到该指标的可比记录，含只有部分维度；未覆盖 = 无源或无数值）；`completeness` 再分「完整/部分」。其中 5 条是公告/新闻稿标题索引（`text_only`），不是结构化数值，单列计数，不混进「数值覆盖」。

实测（2026-11-25）：

| 行业 | 目录条数 | 已覆盖 | 完整 | 部分 | 未覆盖 |
|---|---:|---:|---:|---:|---:|
| 宏观 | 71 | 10 | 2 | 8 | 61 |
| 储能与电力设备 | 70 | 7 | 2 | 5 | 63 |
| 半导体 | 69 | 6 | 0 | 6 | 63 |
| 有色与锂 | 68 | 11 | 0 | 11 | 57 |
| 新能源车 | 63 | 9 | 4 | 5 | 54 |
| 医药 | 64 | 4 | 0 | 4 | 60 |
| **合计** | **405** | **47** | **8** | **39** | **358** |

- 验收线「已覆盖 ≥ 30」按二值口径 **47 条**达标。
- 严格「完整」只有 **8 条**。可达上限受三点卡住：
  1. 目录里大量条目要「全球 + 月度 + 多品种」（如全球锂供需、美国储能装机），现有 17 源是国家统计/协会摘要或单点快照，维度不够；
  2. 半导体、医药两个行业只有贸易与现货价，没有产业统计（设备账单、CDE 受理、医保支出）；
  3. 汇率、M2、仓单、合约价、地区均价等根本没有落盘源。
- 映射逐条写在 `industry_catalog_map`，每条含表名、字段名、完整/部分与缺口说明；C6 会校验映射没有「目录里不存在的指标名」（防手打错）。

## 7. 回滚

```bash
# 只回滚行业层（不影响 20 张 A 股表）
duckdb /srv/share/quantdb/db/quantdb.duckdb -c "
  DROP VIEW IF EXISTS v_industry_catalog_coverage_summary;
  DROP VIEW IF EXISTS v_industry_catalog_coverage;
  DROP TABLE IF EXISTS industry_gfex_lithium_carbonate;
  ...（17 张 industry_* + industry_catalog_map + industry_load_audit）
"
systemctl --user disable --now quantdb-industry.timer quantdb-industry-monthly.timer
rm -f ~/.config/systemd/user/quantdb-industry*.{service,timer}
systemctl --user daemon-reload
```

全量回滚：冷备 `/srv/backup/quantdb/`（backup-nightly 每晚 03:30）。

## 8. 变更文件

- `db/schema_industry.sql`（新）
- `db/etl/industry.py`（新）
- `db/checks/industry.py`（新）
- `db/timers/industry.sh`（新）
- `db/timers/systemd/quantdb-industry.{service,timer}`（新）
- `db/timers/systemd/quantdb-industry-monthly.{service,timer}`（新）
- `db/tests/test_industry.py`（新）
- `scripts/work568_verify.py`（新，三源全量回对）
- `scripts/work568_build_delivery.py`（新，交付页生成）
- `docs/industry-ingest-2026.md`（本文件）

## 9. 独立复审记录

第 1 轮（子代理，审 `1d8b9d4`）：**NEEDS WORK，2 blocking**。

| 编号 | 问题 | 处置 |
|---|---|---|
| B1 | `parse_number` 把前导负号当装饰符吞掉，`-8.43` 存成 `8.43`，benchmark 16 个指数里 10 个涨跌方向反了 | 保留前导 `-`/`▼` 为负号，`—` 仍作中性；新增 2 个回归测试；重装后库内 10 负 / 3 正与原始 CSV 一致 |
| B2 | C4 的 `nea` 子闸按行计数，永远 `pairs=0`，等于假 PASS | 改为「同文章同期、指标成对出现时取当月值」，实测 `pairs=1, ok=1`（2026-10-21：135+5800+1852+1210 = 8997 = 全社会用电量）；`pairs=0` 现在判 FAIL 而非 PASS |
| S1 | C4 的 `ppi` 子闸单位字面量与库内词表不一致，且该源不可能有跨单位样本 | 删除该子闸，改为 `unit_vocabulary`（10 张带单位表，出现新单位即 FAIL） |
| S2 | 「已覆盖 47」含 5 条文本索引，与「可查到数值」口径不符 | 文档改为「有可查记录（数值 42 + 文本 5）」，C6 报文新增 `text_only=5` |
| S3 | 单闸运行会覆盖当日 7 闸报告 | 单闸报告改名为 `industry-<date>-<gate>.json` |
| nit | docstring「六道闸」、mof 注释引用不存在的 `quality_flag`、分区替换测试注释与断言相反 | 已全部改正 |

复审独立复跑：`pytest` 9 passed（当时）、`checks all` 7 PASS、`verify` RESULT PASS；并核对 `db/schema.sql`/`common.py` 零 diff、非 industry 表仍 20 张、`data_catalog` 仍 405 行。

第 2 轮（新子代理，审 `9ad6093`）：**PASS，0 blocking**。

- 复核 B1：对 raw 里 211 个待解析值做新旧函数对比，只有 benchmark 27 处（去重后 10 个负值）变化；库内 16 个指数符号与 CSV 一致（10 负 / 3 正 / 3 零）；新增回归测试在旧代码上实测失败，不是空测试。
- 复核 B2：旧 SQL 实测 `(0,0)`，新 SQL `(1,1)`；空表/少分项构造用例均判 FAIL；`min()` 取当月值的前提在 8 个成对分组上全部成立。
- 复核 S1–S3 与 3 个 nit：全部到位。
- 本轮 1 条 suggestion（单位词表漏了 `industry_stats_gov_release`，11 张有 unit 列的表只查了 10 张）与 2 条 nit（benchmark 指数 15→16；`price_unit` 计数应取去重后口径）已在收口提交中修正并复跑验证。
