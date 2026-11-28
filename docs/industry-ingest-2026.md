# 行业层入库与定更（WORK-568）

> 统计时点：2026-11-25 11:00–12:00（Asia/Shanghai）。
> 范围：把 `raw/industry/`（国内 10 源）与 `raw/industry_alt/`（国际 7 源）已落盘的 **17 个源**规范化成 `industry_*` 表，配 7 道质量闸与日/月两档 systemd timer。
> 不在范围：重写抓取逻辑、行业结论、研报、修改 `data_catalog` 405 条、修改既有 20 张 A 股表与 `v_daily_qfq/v_daily_hfq`。

## 1. 一句话结论

17 个源全部进了 DuckDB（`industry_*` 17 张表；WORK-568 装载时 **16,834 行**，WORK-572 回填历史后 **241,892 行**，见 §2.2），每次装载按「源 × 期」分区替换，同参数重跑行数不变（WORK-572 实测两次装载 17 表差异 0）；另有预测表 `industry_forecast` 3 行（WORK-584，见 §13），与观测表分表、分闸；**8 道闸全 PASS**（观测 7 道判据未改 + 预测 1 道）；日频/月频两个 timer 已安装；`data_catalog` 405 条中 47 条有可查记录（数值 42 + 公告/新闻稿文本索引 5；严格「完整」8 条）。

## 2. 源清单与口径（源 → 表 → 字段 → 单位 → 频率 → 口径 → 一手 URL）

表名前缀统一为 `industry_`；「频率」是**上游发布频率**，定时任务按日/月两档调度（日频档也会跑月频源的成本很低，见 §5）。

| # | 源 | 表 | 主要字段 | 单位 | 频率 | 口径要点 | 一手 URL |
|---|---|---|---|---|---|---|---|
| 1 | 广州期货交易所·碳酸锂 | `industry_gfex_lithium_carbonate` | trade_date, contract, field, value | 元/吨；手；**万元** | 日 | 全合约逐日 9 字段；`turnover` 源标「元」实为万元（§4.1） | `http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList` |
| 2 | 中国货币网·LPR | `industry_chinamoney_lpr` | quote_date, tenor, value | % | 每月 20 日 | 1Y / 5Y+ 两个期限 | `https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis?lang=CN` |
| 3 | CNESA·储能年度数据 | `industry_cnesa_storage` | period, indicator, value | GW；%；GWh；个；项；元/kWh；小时 | 年度 | 33 行观测值（§4.2）；3 条未来期预测值走 `industry_forecast`（§13） | `https://www.cnesa.org/information/detail/?column_id=1&id=7798` |
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

### 2.2 回填后的覆盖区间（WORK-572，2026-11-25）

「回填前」= WORK-568 装载时的库内状态（快照见 `raw/_backup-work572/db-pre-work572/`）；
「回填后」= 本次重抓 + 重装后；「未改」= 上游只有当日快照/新闻稿，没有可回填的历史序列。
行数为装载后库内实值（统计时点 2026-11-25 18:00 Asia/Shanghai）。

| # | 源 | 回填前（行数 / 区间） | 回填后（行数 / 区间） | 上游深度上限（实测） |
|---|---|---|---|---|
| 1 | gfex_lithium_carbonate | 12,960 / 2026-05-28..2026-11-20（120 交易日） | 84,951 / **2023-07-21**..2026-11-25（811 交易日） | 碳酸锂期货 2023-07-21 上市；上市前查询只回工业硅/总计行 |
| 2 | chinamoney_lpr | 24 / 2025-12-22..2026-11-20（12 期） | 176 / **2019-08-20**..2026-11-20（88 期） | LPR 改革后首期 2019-08-20；一年以上区间需按日历年切片 |
| 3 | cninfo_announcements | 749 / 2026-10-24..2026-11-23（26 日） | 13,821 / **2023-12-12**..2026-11-25（650 日，8,243 个公告） | `seDate` 支持 3 年整段；分页按响应 `totalpages` 全取（161/55/694/360 页） |
| 4 | caam_auto_output | 146 / 2026-04..2026-10（6 期） | 1,614 / **2020-05**..2026-10（68 期） | 列表页只有 32 个链接，正文须按 NewsID 回溯；不存在的 id 返回 500 |
| 5 | cpcaauto_nev | 52 / 2025-10..2026-11（14 期） | 506 / **2019-11**..2026-11（85 期） | 列表 20 篇/页、每栏目 5 页；缺 `Upgrade-Insecure-Requests` 头会被 WAF 403 |
| 6 | mof_fiscal | 37 / 2017-11..2026-10（5 期，含 §4.3 串期行） | 836 / **2015-02**..2026-10（84 期；另有 227 行期解析失败→NULL，见 §4.3） | 列表 `index_<n>.htm` 一页约 10 篇财政收支情况 |
| 7 | nea_power | 45 / 2026-06..2026-09（4 期） | 1,677 / **2021-03**..2026-10（54 个非空统计期；WORK-581 换成 xwfb `ds_*.json` 源 + 解析正文 HTML 表，见 §12） | 站内搜索深翻 2,400 条只到 2024-08 且无年份标题被旧正则全丢；`ds_*.json` 一次 GET 拿 400 条全量（滚动快照）；26 篇电力统计稿里 **6 篇的表是图片**，表行占 663 行 |
| 8 | pbc_shrzgm | 110 / 2026-01..2026-10（10 期） | 770 / **2021-01**..2026-10（70 期） | 年度目录 2015–2026，每年一张含 12 个月的增量统计表 |
| 9 | stats_gov_release | 2,468 / 2025-10..2026-10（13 期） | 137,342 / **2020-12**..2026-10（71 期；另 4,724 行年度类文章期解析失败→NULL，见 §10.3） | 列表 `index_<n>.html` 49 篇/页；六组指标各取 70 篇 |
| 10 | ow_iea_usgs_timeseries | 42 / 2024..2025 | 1,044 / **1750..2025**（6 个 slug、22 个实体） | 取上游 CSV 全序列（lithium 558 行；World 1995–2025 共 31 年） |
| 11 | comtrade_trade_values | 5 / 2024 | 30 / **2019..2024**（5 HS × 6 年） | preview 接口按年可用；HS 854143（光伏组件）在 2019–2021 返回空 |
| 12 | cnesa_storage | 2 / 2026-01 | 未改 | 只有年度摘要页，改造属另单 |
| 13 | benchmark_public_index | 26 / 2026-11-23 | 未改 | 页面临时快照，上游无历史序列 |
| 14 | exchange_daily_quotes | 84 / 2026-11-23 | 21,756 / **2025-11-03**..2026-11-25（259 交易日；WORK-578 回补，**未计入本行合计 241,892**，见 §10.7） | 上游按交易日发布（SHFE `kxYYYYMMDD.dat` 归档实测最早 2002-01-07；GFEX 碳酸锂 2023-07-21 上市），脚本按交易日循环 |
| 15 | ppi_spot_prices | 32 / 2026-11-20..2026-11-23 | 未改 | 厂商挂牌价当日快照 |
| 16 | sne_public_battery | 8 / 2026-11-04..2026-11-11 | 未改 | 新闻稿列表，无月度数值序列 |
| 17 | trendforce_spot_price | 44 / 2026-11-23 | 未改 | 现货页当日快照 |

合计：**16,834 → 241,892 行**（17 表逐行相加，见 §10.4 的复跑命令）。
「241,892」是 **WORK-578 之前**的口径；若按本表第 14 行显示的回补后值列相加则为 **263,564 = 241,892 + 21,672**。两数都只对应 18:00 那个时点——之后 WORK-576 的日频 timer 又在装载，库内 17 表合计会继续变（实时值用 `verify.py shape` 读，不要引用本节快照数）。

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

# 3) 手工触发定时任务（抓取 + 装载 + 闸；脚本内部只走 --mode incremental）
systemctl --user start quantdb-industry.service          # 日频
systemctl --user start quantdb-industry-monthly.service  # 月频

# 4) 只装一个源（排查）
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load --sources cnesa_storage

# 4b) 只装预测表 + 预测核对（WORK-584）
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  flock -w 7200 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load --sources cnesa_forecast
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python scripts/work584_verify.py

# 5) 手工历史回填（非定时；深度大、耗时长，只在需要补历史时手动跑）
cd /srv/share/quantdb/code/industry_fetch
/home/moyongkai/.venvs/quantdb/bin/python fetch_gfex_lithium.py --mode backfill
/home/moyongkai/.venvs/quantdb/bin/python fetch_cninfo_ann.py  --mode backfill
#   caam / cpcaauto / mof / nea / pbc / stats 同法：加 --mode backfill
cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  flock -w 7200 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load
```

两档参数的分工（WORK-576）：

| 档位 | 默认值 | 谁用 | 深度 |
|---|---|---|---|
| `--mode incremental`（默认） | 不传参就是这个 | 定时任务（`industry.sh daily/monthly` 显式传） | GFEX 30 天 + `--resume`、巨潮 90 天、中汽协 3 个月、财政部 4 页、乘联 3 页、能源局只固定 `--mode incremental`（近 6 个月窗口由脚本默认）、人民银行当年、统计局 6 页 / 每组 6 篇 |
| `--mode backfill` | 必须显式 `--mode backfill` | 手工回填 | GFEX 1825 天、巨潮 1095 天、中汽协 78 个月 / 1600 个 id、财政部 30 页 / 150 篇、乘联 10 页 / 90 篇、能源局 `--mode backfill`（默认不限窗口，可用 `--from-ym/--to-ym` 收窄）、人民银行 2021 年起、统计局 80 页 / 每组 70 篇 |

显式传数值（如 `--days 7`）永远优先于 `--mode`；manifest 的 `params.mode` 与各旋钮实际值可事后核对。
抓取落盘是合并写（按 key 去重），窗口收窄只影响「本次请求多少」，不会丢已有 raw 行。

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

### 4.2 CNESA：2 行 → 33 行（WORK-577 已修）；预测值分流到预测表（WORK-584）

WORK-568 时该源只入库 2 行，原因是提取规则只覆盖 6 个句式，**不是源只公开 2 条**：
公开页 HTML（HTTP 200，341,562 字节）正文至少含 66.43GW、189.48GWh、213.3GW、+54%、
31.3%、144.7GW、+85%、100GW、35%、58%、8%、+52%、+73%、121.5GWh、+140.1%、206.3GWh、
+125.5% 等带单位数字。

WORK-577 把句式扩到 24 条规则，观测表入库 33 行（2025 年末 32 行 + 2021 年末时长 1 行）；
未提取的数字逐条登记在 `raw/industry/cnesa_storage/failed.json`（2026-11-25 22:40 实测 11 条）。

WORK-584 把 3 条目标期在未来的预测值（2030 年累计装机 3.7 亿千瓦、2030 年平均时长
3.47 小时、十五五工商业储能 30GW）分流到 `cnesa_forecast` → `industry_forecast`；
观测表结构与 C3 日期域判据**未放宽**，预测值也不再出现在观测层的「未提取」清单里。详见 §13。

### 4.3 财政部：published_date 全错、period 与文章年份冲突

- 37 行 `published_date` 全部是 `2017年11月21日`，而 `source_url` 是 2026 年文章（`t20261023_...`）。
- WORK-568 时 12 行 `period=2017-11`（如「全国一般公共预算收入 172292 亿元」），与同文其余 2026 年期并排，属提取串期；WORK-572 回填放大了这个缺陷——正文模板固定带「2017年11月21日」，旧代码在标题解析失败时回退正文，产生 **237 行/36 篇** 错期为 2017-11。

处置（WORK-572 已修）：期**只从标题取**（标题正则放宽到 `[-—－]` 与可选「全国」），标题匹配不到时 `period` 置空并逐篇记入 `failed.json`（本次 55 条：35 篇期置空、20 篇数字规则也未命中而零产行），不再回退正文。仍为空的 35 篇分两类：①**1 篇** 2016 老稿标题是「YYYY年M月**份**财政收支情况」（多一个「份」字未命中）；②其余 **34 篇**是 2017–2026 的季度/年度稿（`2026年前三季度财政收支情况`、`2025年财政收支情况`），期只能靠推断，按本层规矩不推断。另：2012–2014 共 19 篇标题与数字规则**都**未命中（零产行），加 `份?` 只能补期、补不回这 3 年的数值。`published_date` 由 URL 的 `tYYYYMMDD` 派生（可证的发布日期），原始文本保留在 `published_date_raw`；质量闸只做日期域检查。引用 `period` 为空的 227 行前必须回原文核对期次。

### 4.4 国家能源局：同一指标名混「当月/累计」

例：2026-08 同一文章下「城乡居民生活用电量」同时有 1,883 与 11,132 两个值（当月 vs 累计）。库层口径不变：`industry_nea_power` 用 `row_hash` 作主键保留全部行，业务键 `(period, indicator, source_url)` 重复 **365 组**（2026-11-25 回补后实测），即同一期同一指标的当月/累计两行，按约定不合并；查询时必须结合 `value` 量级或回原文判断口径。

WORK-581 起抓取层已按句/按列显式判口径并写进 raw 的 `context`（`[当月]`/`[累计]`/`[存量]` 前缀、表格行另带 `表N行M·累计`），所以逐行能回查口径；C4 的 nea 子闸两侧都取 `min`（当月）比较，不再依赖「全社会用电量只有一行」这个巧合。

### 4.5 统计局利润表：表头与数值错位一列

`table_index=0,row_index=2` 的 `col_header='营业收入/同比增长（%）'` 对应 `value=1062066`（明显是亿元营收，不是同比 %），下一列才是 6.60。本层原样保留单元格与行列号，不猜正确配对；C6 映射该指标记为「部分」。

### 4.6 TrendForce：value 列混价格、涨跌幅与表头

CSV 里同一列出现 `60.70`（价格）、`▲ 1.85 %`（涨跌幅）、`盘涨跌幅`（分组表头）。装载层保留 `value_text`，解析数值到 `value`，用 `unit_kind ∈ price/pct_change/header/unparsed` 区分。按价格使用时应过滤 `unit_kind='price'`。

### 4.7 100ppi 跨单位勾稽不成立（因此没有做比值闸）

设想用「同货同供方同日 元/公斤 × 1000 ≈ 元/吨」勾稽，实测无法成立：落库表 `price_unit` 的实际取值是 `吨`(26) / `千克`(4) / `公斤`(1) / NULL(1)（raw CSV 33 行，row_hash 去重后 32 行），而每家公司只报一种单位——碳酸锂 190 元/公斤来自「湖北七八九化工」，174000 元/吨来自「山东亿禾」，不存在同一供方同日双单位样本。硬做一个 `pairs=0` 的闸等于空转，故删除该子闸，改成 C4 的 `unit_vocabulary`：有 unit 列的 11 张表只允许 schema 注释里写明的单位，出现新单位即 FAIL（11 张全过）。NULL 单位仅在数值也为 NULL 时放行（100ppi 的分类标题行）。

### 4.8 其他小问题（不影响本次交付）

- `comtrade` 2026-11-25 重抓后无 HTTP 500；HS 854143（光伏组件）2019–2021 为 `status=empty`（上游无记录），以空值 + status 保留，不删行。
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

## 10. 回填记录（WORK-572，2026-11-25）

> 范围：只改抓取参数/取法，不动入库层（`db/etl/industry.py`）、不动质量闸（`db/checks/industry.py`）、不动 `db/schema.sql` 与 `v_daily_qfq/v_daily_hfq`。
> 代码位置：国内 fetcher 在 `/srv/share/quantdb/code/industry_fetch/`，国际 fetcher 在 `/srv/share/quantdb/raw/industry_alt/<源>/fetch.py`（两者都不在 git 里，是本工作区既有形态；本单以产物摘要锁版，见 §10.6）。

### 10.1 一句话

11 个源改了抓取窗口/取法（其中 2 个换接口/换取法：LPR 用 `strStartDate` 按日历年切片、OWID 取消 `sel[-2:]` 只取最近 2 年），库内 industry_* 从 16,834 行回填到 241,892 行；6 个快照/新闻类源无历史序列，保持原样；7 道质量闸维持 PASS。

### 10.2 逐源改动与实测上限

> 下表「复跑命令」列是**回填档**（WORK-576 起必须显式 `--mode backfill`，否则默认只跑日常增量窗口）；`--sleep` 等为实测时的限速值。

| 源 | 改了什么 | 实测上限（命令 → 输出） | 复跑命令 |
|---|---|---|---|
| gfex_lithium_carbonate | `DAYS 180 → 1825`，起点夹到上市日；新增 `--resume` 只补缺日 | `POST loadList trade_date=20230103` → `varieties={工业硅:5,总计:1}`（无碳酸锂）；`trade_date=20230721` → `碳酸锂 7 行` | `python fetch_gfex_lithium.py --mode backfill --sleep 0.9` |
| chinamoney_lpr | 换接口参数：`strStartDate/strEndDate` 按日历年切片（`pageNum`/`startDate` 被服务端忽略） | `strStartDate=2019-08-20&strEndDate=2019-12-31` → `records=5`；每个整年 `records=12` | `python fetch_chinamoney_lpr.py --start 2019-08-20` |
| cninfo_announcements | `--days 30 → 1095`，`--max-pages 400 → 1200`，按响应 `totalpages` 全取并记 `truncated` | `seDate=2023-11-25~2026-11-25`：半导体 `totalpages=161`、锂业 `55`、有色金属 `694`（szse）/`360`（sse） | `python fetch_cninfo_ann.py --mode backfill` |
| caam_auto_output | `--articles 90 → 可配`、`--scan` 可配、回溯改按自然月（`--months 78`）；NewsID 扫描不再重试（不存在即 500） | 列表页 `ReadArticle.asp?NewsID=` 只有 32 个链接；`NewsID=11442` 返回 200 空壳 | `python fetch_caam_output.py --mode backfill --sleep 0.22` |
| cpcaauto_nev | `--articles 12 → 90`，新增列表翻页（`news.php?types=..&anid=..&nid=0&page=N`）；`_common` 补 `Upgrade-Insecure-Requests` 头（缺则 403「请使用浏览器访问本站」） | 栏目 `bgzl&anid=126` → `items=20`、页脚 `page=2..5` | `python fetch_cpcaauto_nev.py --mode backfill` |
| mof_fiscal | `--articles 6 → 90`，新增 `index_<n>.htm` 列表翻页 | `index.htm` → 财政收支 9 篇；`index_5.htm` → 最早 2022-09 | `python fetch_mof_fiscal.py --mode backfill` |
| nea_power | **WORK-581 换源**：站内搜索翻页 → `xwfb/ds_<id>.json` 一次 GET；正则兼容无年份标题；正文 HTML 表逐行解析 | 400 条快照（2016-04-07..2026-11-25），2021–2023 命中 55 篇，见 §12 | `python fetch_nea_power.py --mode backfill --from-ym 2021-01 --to-ym 2023-12 --sleep 2.0` |
| pbc_shrzgm | 由「只抓 2026 年度页」改为按年度目录循环（2015–2026，按锚文本发现年份，结果缓存 `year_dirs.json`）；WAF 403 单请求重试 | `116219/index.html` 锚文本 → `2015..2026 共 12 个年度目录`；每年 `shrzgm` 页挂 1 份含 12 个月的增量表 `.htm` | `python fetch_pbc_shrzgm.py --mode backfill --sleep 5` |
| stats_gov_release | 列表改 `index_<n>.html` 翻页（49 篇/页），每个指标组取 `--articles` 篇 | `index_20.html` → 首篇 2025-05-19（约 18 个月/20 页） | `python fetch_stats_gov.py --mode backfill` |
| ow_iea_usgs_timeseries | `for x in sel[-2:]` → 取全部有值年份（深度可配 `--min-year`），manifest 增 `params.depth` | `lithium-production.csv` → 558 行，`World` 1995–2025（31 年） | `python fetch.py`（无参即全历史） |
| comtrade_trade_values | 只查 2024 → 按年循环 2019–2024（`--start-year/--end-year`），超时重试 | `period=2019..2024` 逐年可用；HS 854143（光伏组件）在 2019–2021 返回空 | `python fetch.py --start-year 2019 --end-year 2024` |

### 10.3 未取到 / 未达标清单（分层原因，不估算补数）

| 项 | 状态 | 原因分层 |
|---|---|---|
| GFEX 1,000 个交易日 | 811 | **上游只到上市日**：碳酸锂 2023-07-21 才有合约（§10.2 实测）。核对：`trade_cal` 中 2023-07-21..2026-11-24 开市日 810 个，加当日 2026-11-25（`trade_cal` 尚未收录）= 811，**无亏缺**；区间自然工作日 874 个，差额 64 天全部是休市日 |
| NEA 60 期 | 23 期（2024-07 起）→ **54 期（2021-03 起）**，WORK-581 已补 | **边界在搜索通道，不在上游**（WORK-579 探针 + WORK-581 实施两次实测）：①站内搜索深翻页上限——`sort=date` 下「全社会用电量」12 页/2,400 条只到 2024-08-17，够不到 2021–2023；②无年份标题被旧 `TITLE_RE` 丢弃——2021–2023 的稿多为「11月份全社会用电量…」，旧正则要求「YYYY年M月」，对窗口内 56 篇候选命中 **0**（§12 复跑命令 A 段）；③正文表**并非全是 HTML**——26 篇电力统计稿里 19 篇是 HTML 表（663 数据行）、**6 篇是图片表**（发布日 2021-07-16、2021-10-21、2022-01-26、2022-04-20、2022-07-19、2023-01-18；统计期 2021-06/09/12、2022-03/06/12，只能取到正文摘要数字，需 OCR 才能补表行）、1 篇无表；④该频道本身缺 5 个月（用电量缺 2021-01/02/10、2022-01/06、2023-01/05 等，见 §12 覆盖表），不是脚本漏抓。未排序扫描另有限速与 ~10,100 条深分页上限。 |
| CNINFO 有色金属 3 年 | 已取全 | 6 个查询均 `pages_fetched == totalpages`，无截断（`manifest.params.queries[].truncated=false`） |
| COMTRADE HS 854143（光伏组件） | 2019–2021 空 | 上游 preview 接口这三年的该编码无记录（status=empty），2022–2024 有值；其余 4 个编码 6 年齐全（27/30 OK） |
| 统计局年度类文章 | 4,724 行 period=NULL | 标题为纯年份（如「2021年…」）且表内无月份列，期解析失败后置空、不推断；分布：工业企业利润 3,634、固定资产投资 658、工业增加值 432 |
| 快照类 6 源（cnesa/benchmark/exchange/ppi/sne/trendforce） | 未回填 | 上游只给当日/当年快照，无历史序列可抓；cnesa 提取器改造另有专门单 |

### 10.4 幂等与质量闸证据

```bash
# 0) 幂等比对（取最后两份「17 源完整」装载报表逐表比 loaded_rows / partitions）
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python scripts/work572_verify.py idem

# 1) 同一参数连跑两次装载，逐表行数不变（本单实测：两次 run_id 不同的装载，17 表行数完全一致）
cd /srv/share/quantdb/code/quant-lab
for i in 1 2; do
  QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
    flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \
    /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load
done

# 2) 质量闸（7 道，含 C4 三个子闸）
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all

# 3) 覆盖表（回填前 vs 回填后，回填前快照直读 parquet）
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python scripts/work572_verify.py coverage
# 4) 表数 / data_catalog / industry_* 合计（列相加自检）
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python scripts/work572_verify.py shape
# 5) 抽 2 个新增期间点与 raw 全量双向 EXCEPT（owid 全序列 + gfex 2023-08）
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python scripts/work572_verify.py spot
# 6) A 股 20 表零 diff（装载前拍基线，装载后比对）
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python scripts/work572_verify.py astock
```

### 10.5 回滚

```bash
# ① 只回滚行业层数据到回填前（快照是 2026-11-25 17:05 的 19 张表）
cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python - <<'PY'
import duckdb, glob, os
con = duckdb.connect("/srv/share/quantdb/db/quantdb.duckdb")
for p in sorted(glob.glob("/srv/share/quantdb/raw/_backup-work572/db-pre-work572/*.parquet")):
    t = os.path.basename(p)[:-8]
    con.execute(f"DELETE FROM {t}")
    con.execute(f"INSERT INTO {t} BY NAME SELECT * FROM read_parquet('{p}')")
PY

# ② 回滚抓取代码：本次改动的是 /srv/share/quantdb/code/industry_fetch/*.py 与
#    raw/industry_alt/{ow_iea_usgs_timeseries,comtrade_trade_values}/fetch.py；
#    改前版本没有 git 历史（既有形态），按 §10.6 的产物摘要核对，需要退回时按 §10.2
#    「改了什么」逐条还原参数即可（唯一结构性改动：LPR 接口参数、OWID 取全序列）。
# ③ raw 层：本次抓取是「合并写」，回填前的旧行仍在 part.parquet 里；如需 raw 也回到
#    回填前，按 §10.2 的旧参数重跑一次即可（行数会停在旧窗口）。
```

### 10.5b 已知瑕疵（已记录，未改代码以免更换审查锁版 SHA）

第 2 轮独立复审 PASS（0 blocking）后遗留 3 条 docstring/参数命名瑕疵，均为**注释层面的数字与 argparse 默认值不一致**，
不影响已交付数据的正确性；改它们会改变 14 文件摘要、使审查结论失效，故按「先记账、下一单统一改」处理：

| 位置 | 现状 | 正确值 |
|---|---|---|
| `code/industry_fetch/fetch_cninfo_ann.py:8` | 用法注释写 `--max-pages 400` | 默认已是 `--days 1095 --max-pages 1200`（§10.2 的命令是正确的） |
| `code/industry_fetch/fetch_mof_fiscal.py:7` | 注释写 `--articles 6` | 默认 `--articles 90 --pages 20 --sleep 0.3` |
| `code/industry_fetch/fetch_pbc_shrzgm.py:10` | 注释写 `--sleep 0.3` | 默认 `--sleep 3.0`（0.3 会触发 WAF 连续 403） |

另记两条复审「需要确认」的口径，供下一单决策：
- `fetch_mof_fiscal.py` / `fetch_stats_gov.py` 的**列表**翻页 `if not fresh: break` 不记 `stop_reason`（列表型站点空页即正常结束），与
  `fetch_nea_power.py` 已修的口径不统一；
- `fetch_pbc_shrzgm.py` 的年度目录缓存只守「最大年份 ≥ 当前年」，若某次发现只写回单年缓存，之后不会重发现（当前缓存为完整的 2015–2026）。

### 10.6 变更文件与产物摘要

| 文件 | 变更 |
|---|---|
| `code/industry_fetch/_common.py` | `BASE_HEADERS` 增 `Upgrade-Insecure-Requests`（cpca 403） |
| `code/industry_fetch/fetch_gfex_lithium.py` | 上市日夹取、`--resume`、manifest 增回填参数 |
| `code/industry_fetch/fetch_chinamoney_lpr.py` | 按日历年切片 + `strStartDate`（本单前已改，本次只跑） |
| `code/industry_fetch/fetch_cninfo_ann.py` | `--days 1095`、`--max-pages 1200`、截断检测 |
| `code/industry_fetch/fetch_caam_output.py` | 月粒度回溯、NewsID 扫描不重试 |
| `code/industry_fetch/fetch_cpcaauto_nev.py` | 列表翻页、`--articles 90` |
| `code/industry_fetch/fetch_mof_fiscal.py` | 列表翻页、`--articles 90` |
| `code/industry_fetch/fetch_nea_power.py` | 日期排序翻页 + 年份扩词 + `originUrl`（WORK-581 再次改写：`ds_*.json` 源 + HTML 表解析，见 §12） |
| `code/industry_fetch/fetch_pbc_shrzgm.py` | 年度目录循环 + 目录缓存 + 403 重试 |
| `code/industry_fetch/fetch_stats_gov.py` | 列表翻页 + 每组多篇 |
| `raw/industry_alt/ow_iea_usgs_timeseries/fetch.py` | 全序列 + `params` |
| `raw/industry_alt/comtrade_trade_values/fetch.py` | 按年循环 + 超时重试 + `params` |
| `scripts/work572_verify.py`（本仓库） | 覆盖表 / 幂等 / 双向 EXCEPT / A 股零 diff |
| `docs/industry-ingest-2026.md`（本文件） | §2.2 覆盖区间表 + §10 回填记录 |
| `code/quant-lab/scripts/work572_build_delivery.py`（本仓库） | 由主库/快照现场读数生成单文件交付页 |

**manifest.params 的覆盖口径**：11 个回填源的 `manifest.json` 都含回填深度（可 `jq .params` 核对）；
5 个未回填的国际快照源（`benchmark_public_index`、`exchange_daily_quotes`、`ppi_spot_prices`、
`sne_public_battery`、`trendforce_spot_price`）本次**未触碰**，其 manifest 仍无 `params`——不是遗漏，
是本单不重抓这些源（重抓会改快照日）；如要求 17/17 全带 `params`，属扩范围，需另行确认。

产物摘要（sha256，交付时锁定）：

```bash
cd /srv/share/quantdb && sha256sum \
  code/industry_fetch/_common.py \
  code/industry_fetch/fetch_{gfex_lithium,chinamoney_lpr,cninfo_ann,caam_output,cpcaauto_nev,mof_fiscal,nea_power,pbc_shrzgm,stats_gov}.py \
  raw/industry_alt/ow_iea_usgs_timeseries/fetch.py \
  raw/industry_alt/comtrade_trade_values/fetch.py \
  code/quant-lab/scripts/work572_verify.py \
  code/quant-lab/scripts/work572_build_delivery.py | tee /srv/share/quantdb/meta/industry/work572-code-sha256.txt
# 复核：sha256sum meta/industry/work572-code-sha256.txt 应为 2f300f5e88ef8168b2651fecedbe228d1da077369e4453fbb174b8ff2bd76c19（14 行）
```

### 10.7 交易所日行情回补（WORK-578，2026-11-25）

> 范围：只改 `/srv/share/quantdb/raw/industry_alt/exchange_daily_quotes/fetch.py` 的抓取窗口与失败记录，不动入库层（`db/etl/industry.py`）、不动质量闸、不动 `db/schema.sql` 与 A 股 20 表。

**一句话**：交易所表从 1 个交易日（84 行）回补到 **259 个交易日（21,756 行，2025-11-03..2026-11-25）**，双源逐日齐全、零失败日；日常定时任务仍只补最近窗口。

上游形态与一手 URL（都是一天一个文件/一次查询）：

| 源 | 取法 | 一手 URL |
|---|---|---|
| 上期所 SHFE | GET，一天一个 `.dat`，JSON 里 `o_curinstrument` | `https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{YYYYMMDD}.dat` |
| 广期所 GFEX | POST 表单 `trade_date=<YYYYMMDD>&trade_type=0&variety_id=tslz` | `http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList` |

上游深度实测（逐条是实际响应）：

| 探测日 | SHFE `kx<date>.dat` | GFEX `loadList` |
|---|---|---|
| 2001-12-28 | **404**（HTML 错误页） | — |
| 2002-01-04 | **404**（当年首个交易日仍无归档） | — |
| 2002-01-07 | **200**，`o_curinstrument=38` | — |
| 2005-01-04 / 2010-01-04 / 2015-01-05 / 2020-01-02 | 200，`o_curinstrument` 49 / 102 / 149 / 236 | — |
| 2023-07-20 | 200（该文件只有 SHFE 品种） | 200，`data=10`，**无碳酸锂** |
| 2023-07-21 | — | 200，`碳酸锂合约行=7`（上市首日） |

复跑：

```bash
for d in 20011228 20020104 20020107 20230720 20230721; do
  curl -s -o /dev/null -w "$d SHFE %{http_code}\n" \
    "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx$d.dat"
  curl -s -X POST -d "trade_date=$d&trade_type=0&variety_id=tslz" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    "http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList" \
    | python3 -c 'import json,sys;d=json.load(sys.stdin).get("data") or [];print("  GFEX data=%d 碳酸锂=%d"%(len(d),len([x for x in d if str(x.get("variety"))=="碳酸锂"])))'
done
```

结论：SHFE 归档最早可下载日实测 **2002-01-07**（2002-01-04 与更早的 2001-12-28 均 404；节假日也会 404，故只以「交易日」判定）；
GFEX 碳酸锂自 **2023-07-21** 上市起有数据。本单按验收口径只回补最近 1 年，更早历史未逐日探测，也不在本单承诺内。

抓取脚本改动（`fetch.py`）。**改前那一版没有留档**（20:08 首次改写前未备份，属本单疏漏；全盘只有当前版本），因此代码层面无法逐字节回滚，只能按下面的改动清单还原语义；数据层回滚不依赖它（见本节末）。当前版本 sha256 见表末。

- 按交易日循环：交易日历取主库 `trade_cal`（`exchange='SSE'`，与两个期货所节假日一致），取不到时退回周一至周五并在 `manifest.params.calendar` 标 `weekday-fallback`；
- `--from/--to`（YYYYMMDD 或 YYYY-MM-DD）、`--days N`（默认 **7**，日常增量窗口）、`--resume`（默认开）、`--no-resume`、`--sleep`（默认 1.1 秒）、`--calendar db|weekdays`、`--retries`（默认 3）；
- `--resume` 的「已抓」判据是**该日已抓齐**而不是「CSV 里有这一天」：SHFE 必有，且 >= 碳酸锂上市日 2023-07-21 时 GFEX 也必有；只有一所的残缺日会被重抓补一所（记入 `manifest.params.incomplete_days_refetched`），避免一次瞬时失败留下永不修复的洞；
- 失败逐条落 `failed.json`（`trade_date` + `source` + `method` + `url` + `http_status` + `error`），不写占位行、不估算补数；403/429/5xx/网络错误按 2s→5s 退避，最多 3 次尝试；
- `manifest.json` 增 `params`（起止日、交易日数、抓取数、跳过数、残缺重抓日、请求数、重试数、失败日清单、`resume` 标志、耗时）与 `coverage`（首末日、distinct 交易日、按交易所行数）。
- 未来交易日不请求、记 `pending_days`；「今天」上游未发布也记 pending 并把请求详情放 `pending_requests`，均不计入失败日；
- 写 CSV 前取 `<目录>/.fetch.lock` 排他锁（`--lock-timeout`，默认 600 秒），CSV 用 `data.csv.tmp.<pid>` 原子替换；
- 失败清单每次运行另存 `failed-<run_id>.json`，重放不会抹掉回补那次的记录；
- 退出码：个别历史缺日返回 0（不拖垮 timer）；「有交易日、有失败、一行未取到」返回 1，让 timer 告警；
- `--days N` 是**含 `--to`** 的 N 个自然日（默认 7 → 7 天窗口）。

回补实测（统计时点 2026-11-25 20:19 Asia/Shanghai，日志 `/srv/share/quantdb/logs/work578-backfill.log`）：

| 项 | 值 |
|---|---|
| 命令 | `python fetch.py --from 20251101 --to 20261125 --resume` |
| 窗口 | 2025-11-01..2026-11-25（390 自然日） |
| 交易日总数 | 259（`trade_cal` SSE 开市日） |
| 本次抓取 / 跳过 | 258 / 1（2026-11-23 已于 WORK-568 落盘） |
| HTTP 请求 / 重试 | 516 / 0 |
| 失败日 | **0** |
| 耗时 | 616.4 秒 |
| 落盘行数 | 21,756（SHFE 18,648 + GFEX 3,108） |
| 每日行数 | 恒 84（SHFE 6 品种 × 12 个挂牌月 + GFEX 12 个合约） |

分项自检：18,648 + 3,108 = **21,756** = 库内总行数；259 × 84 = **21,756**（两路算法一致）。
CSV 摘要：`csv_sha256 = 2ac82165a0e3567de34ca188d25bcccaa70bee1ec119a0d452b0c19c7de9db98`。

复跑（重放同一条命令，全部命中 `--resume`，应为 0 请求、CSV 字节不变）：

```bash
cd /srv/share/quantdb/raw/industry_alt/exchange_daily_quotes
/home/moyongkai/.venvs/quantdb/bin/python fetch.py --from 20251101 --to 20261125 --resume
jq -c '{rows, csv_sha256, p: .params | {trading_days, trading_days_fetched, trading_days_skipped, http_calls, failed_days, resume}}' manifest.json
```

实测复跑：`rows=21756`、`csv_sha256` 同上、`trading_days=259`、`fetched=0`、`skipped=259`、`http_calls=0`、`failed_days=[]`、`pending_days=[]`、`incomplete_days_refetched=[]`。
注意 `manifest.params` 记的是**最近一次调用**：交付时是上面这条重放（0 请求 259 跳过）；真实回补那次的 258 抓取 / 516 请求 / 0 失败在 `logs/work578-backfill.log` 里逐条可查。

残缺日修复实测：把 `2026-11-24` 的 12 行 GFEX 从 CSV 删掉，再跑 `--from 20261124 --to 20261124` →
`incomplete_days_refetched=["20261124"]`、`http_calls=2`、该日行数回到 84，CSV 摘要回到 `2ac82165…`（与删除前逐字节相同）。
上游行为实测：夜盘后 SHFE 会提前给出**次日**文件（21:00 请求 `kx20261126.dat` 返回 200、72 行），而 GFEX 当天还没有数据。
代码对**未来交易日一律不取**（`date > today` → 记 `pending_days`，0 请求），避免把「半个预发行日」写进 CSV；
「今天」若上游还没发布则记 `pending_days` 且请求详情进 `pending_requests`，不算失败日。
另注：`trade_cal` 由 18:30 的日频任务刷新，06:10 的定时档里「今天」通常还不在 `dates` 中，所以 pending 主要在手工作业时出现。

装载与幂等（写库一律 `flock /srv/share/quantdb/logs/.quantdb-db.lock`）：

```bash
cd /srv/share/quantdb/code/quant-lab
for i in 1 2; do
  QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
    flock -w 3600 /srv/share/quantdb/logs/.quantdb-db.lock \
    /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load --sources exchange_daily_quotes
done
/home/moyongkai/.venvs/quantdb/bin/python scripts/work578_verify.py exchange   # 形态 + CSV↔库双向 EXCEPT + 装载审计
```

两次装载的 `raw/staged/loaded/partitions` 全同（21,756 / 21,756 / 21,756 / 259）。幂等另有 4 秒窗口实测
（`work578_verify.py rows` 前后各拍一次，2026-11-25 20:58:38 → 20:58:42）：

| 项 | 装载前 | 装载后 |
|---|---|---|
| 19 张 `industry_*` 表行数合计 | 264,252 | 264,253 |
| 唯一变化的表 | — | `industry_load_audit` +1（本次装载自己的审计行） |
| 17 张数据表逐表行数 | `meta/industry/work578-rows-t0.json` | 与 t0 完全一致（`work578-rows-t1.json`） |

装载范围由 `--sources exchange_daily_quotes` 限定为一张表。A 股核对用的是**改前基线**（`meta/industry/work578-astock-baseline.json`，20:20:13 拍的，早于 20:27 的装载）：`scripts/work578_verify.py astock` → 20/20 张「行数 + 两列数值求和」一致。
证据边界要说清：改前基线当时只存了行数与两列求和，所以它能证明的是「行数 + 这两列未变」；全列哈希那一档只能对比「改前状态的库」，而手边唯一的库副本（20:41 冷备）已经含回补结果，脚本会**拒绝**拿它当证据（`--against` 有改前态护栏）。要更强证据得在下次装载前拍全列基线。
对照说明：20:20→20:53 的长窗口里 benchmark / cnesa / cninfo / ppi / trendforce 行数也变过，那是 WORK-576 的日频 timer 与 WORK-577 的 CNESA 装载同时在写库，不是本单改动。

回滚：

```bash
# 数据：删掉本次新增交易日，回到只有 2026-11-23 的状态
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=/srv/share/quantdb/code/quant-lab \
  flock -w 3600 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python - <<'PY'
import duckdb
con = duckdb.connect("/srv/share/quantdb/db/quantdb.duckdb")
con.execute("delete from industry_exchange_daily_quotes where trade_date <> DATE '2026-11-23'")
PY
# raw 与文档留档在 /srv/share/quantdb/raw/_backup-work578/（data.csv / manifest.json / 旧文档）
# 抓取脚本改前版本未留档，见上文说明

# 重建装载审计并复跑闸：只删行会让 C5（审计与现状一致）变红
cd /srv/share/quantdb/code/quant-lab
for i in 1 2; do
  QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
    flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \
    /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load --sources exchange_daily_quotes
done
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all
```

产物摘要（交付时锁定，`sha256sum` 复核）：

| 文件 | sha256 |
|---|---|
| `raw/industry_alt/exchange_daily_quotes/fetch.py` | `4127985f392a235e98b34705905f3a7fdf55ec54f9658988febb051903c8b2a2` |
| `raw/industry_alt/exchange_daily_quotes/data.csv` | `2ac82165a0e3567de34ca188d25bcccaa70bee1ec119a0d452b0c19c7de9db98` |
| `code/quant-lab/scripts/work578_verify.py` | `7f59685f031365460143e35f289e92fb7b89bb664b4dc4a92937c91b6dae9173` |
| `code/quant-lab/scripts/work578_build_delivery.py` | `b7c6bfa436b33716444b00750451009a722f7e71cd660277f77157f2fb048955` |
| `code/quant-lab/scripts/work578_delivery.tpl.html` | `3e8a0c3c565ad65cf6d06c0b106ab6782c8d86a32797933fa0d38d734c0317fc` |
| `code/quant-lab/docs/industry-ingest-2026.md` | 随本单 git 提交锁定（提交 sha 见 issue 交付评论；文件自身内容哈希无法自引用） |

## 11. 定时抓取改增量（WORK-576，2026-11-25）

### 11.1 一句话

`db/timers/industry.sh` 两档改为**显式传窗口参数**；8 个带深度旋钮的抓取脚本统一支持
`--mode incremental|backfill`（默认 `incremental` = 小窗口，`backfill` 必须显式写）。
改动后日常档实测：拿到数据库锁后 **67 秒**跑完抓取 + 装载 + 质量闸，GFEX 只请求 **3 个工作日**
（改前：不带 `--resume` 的 874 个工作日，约 20–25 分钟）。

### 11.2 定时档实测（手动触发一次，2026-11-25）

```bash
cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb QUANTDB_PYTHON=/home/moyongkai/.venvs/quantdb/bin/python \
  db/timers/industry.sh daily
```

日志 `/srv/share/quantdb/logs/industry-20261125.log`（原文摘录）：

```
[2026-11-25T20:10:20+08:00] industry daily start
[2026-11-25T20:27:01+08:00] 已获得数据库锁
[2026-11-25T20:27:01+08:00] 抓取 2 个国内源
[2026-11-25T20:27:01+08:00]   args gfex_lithium: --mode incremental --days 30 --resume
[2026-11-25T20:27:04+08:00]   ok   gfex_lithium (attempt 1)
[2026-11-25T20:27:04+08:00]   args chinamoney_lpr: --start 2019-08-20
[2026-11-25T20:27:14+08:00]   ok   chinamoney_lpr (attempt 1)
[2026-11-25T20:27:14+08:00] 抓取 4 个国际源
[2026-11-25T20:27:21+08:00]   ok   benchmark_public_index (attempt 1)
[2026-11-25T20:27:22+08:00]   ok   exchange_daily_quotes (attempt 1)
[2026-11-25T20:27:35+08:00]   ok   ppi_spot_prices (attempt 1)
[2026-11-25T20:27:38+08:00]   ok   trendforce_spot_price (attempt 1)
[2026-11-25T20:28:08+08:00] industry daily ok（抓取失败 0 个，已在日志逐条列出）
```

| 阶段 | 起 | 止 | 耗时 |
|---|---|---|---|
| 等数据库锁（backup-quantdb 持有） | 20:10:20 | 20:27:01 | 16m41s |
| 抓取 6 源 | 20:27:01 | 20:27:38 | 37s |
| 装载 + 7 道闸（第 3 次尝试通过） | 20:27:38 | 20:28:08 | 30s |
| **拿到锁后的实际工作量** | 20:27:01 | 20:28:08 | **67s** |

分项耗时：gfex 3.1s、LPR 9.2s、benchmark 7s、exchange 1s（`http_calls=0`，6 个交易日已在 raw）、
ppi 13s、trendforce 3s。`real 17m48s` 里的 16m41s 是等锁，不是抓取成本。

GFEX 的实际参数（`jq '{params:.params, elapsed:.elapsed_seconds}' raw/industry/gfex_lithium_carbonate/manifest.json`）：

```json
{"mode": "incremental", "days_back": 30, "from": "2026-10-26", "to": "2026-11-25",
 "workdays_queried": 3, "workdays_skipped_resume": 20, "resume": true,
 "fetched_trade_dates": 3, "elapsed_seconds": 3.1}
```

改前同一脚本的 `params` 是 `days_back=1825, resume=false`（回填时才手动加 `--resume`，
实测 67 个工作日 / 108 秒）；按 874 个工作日线性折算约 20–25 分钟，且 07:10、08:10 两档会叠加。

### 11.3 两档参数（与 §3 的表同一份口径）

- **日常档**（`industry.sh daily`）：gfex `--mode incremental --days 30 --resume`、LPR `--start 2019-08-20`；
  国际源按各自旋钮显式传：exchange `--days 7 --resume`、comtrade `--start-year 2019 --end-year 2024`、
  owid `--min-year 0`；benchmark / ppi / trendforce 是单页快照，无参。
- **月频档**（`industry.sh monthly`）：8 个国内源全部 `--mode incremental`，窗口见 §3 表：
  巨潮 90 天、中汽协 3 个月、财政部 4 页 / 24 篇、乘联 3 页 / 30 篇、能源局每词 3 页 + 当年扩词、
  人民银行当年、统计局 6 页 / 每组 6 篇；CNESA 无参数；国际三源同日常档显式参数。
- **回填档**：只手工跑，命令见 §3 第 5 段与 §10.2 复跑列（`--mode backfill`）。

月频 11 个抓取脚本按上表参数逐个实测（2026-11-25 20:36–20:52，只抓取不装载，逐源输出在
`meta/industry/w576-smoke/*.log`，本轮 11 个源退出码均为 0，逐个人工核对）：

| 源 | 耗时 | 本次抓到的行数（raw 合并后） | 备注 |
|---|---:|---:|---|
| caam_output | 14s | 1,614 | 到 2026-08 边界即停（`--months 3`） |
| cnesa_storage | 1s | 33 | 单页快照，无窗口参数 |
| cninfo_ann | 41s | 14,075（+254） | 90 天窗口，2 条截断告警（见 §11.6） |
| cpcaauto_nev | 13s | 506 | 每栏目 3 页 |
| mof_fiscal | 4s | 836 | 4 页 / 24 篇 |
| nea_power | 113s | 606 | 每词 3 页 + 当年扩词（3 次请求/词） |
| pbc_shrzgm | 24s | 770 | 只取当年 |
| stats_gov_release | 91s | 137,342 | 6 页 + 每组 6 篇 |
| comtrade_trade_values | 274s | 30 | 无窗口参数（按年循环 2019–2024，本单未改） |
| ow_iea_usgs_timeseries | 20s | 1,044 | 无窗口参数（全序列 1 次 CSV/源） |
| sne_public_battery | 352s | 8 | 无窗口参数（新闻稿，本单未改） |

合计约 15.8 分钟，跑在每月 3 日 06:40 的月频档；日频档不包含这些源，所以不影响每日成本。

表中的「耗时」是 smoke 脚本的墙钟（含解释器启动与 import pandas），比 fetcher 自报的
`elapsed_seconds` 多 1–7 秒（如 stats 91s vs 84.0s、pbc 24s vs 20.6s）。本轮 smoke 脚本的
`SMOKE <源> rc=…` 汇总只打到终端；脚本已改为 `… | tee meta/industry/w576-smoke/index.log`，
下一轮可直接复核退出码，不再依赖人工核对。

设计要点：脚本里所有深度旋钮 `default=None`，由 `--mode` 解析（`_common.resolve_mode`），
显式数值永远优先；manifest 的 `params.mode` 与解析后的实际值可事后核对，
不会出现「以为传了、其实走了默认」的静默偏差。

### 11.4 行数不减少（装载后 17 表）

统计时点 2026-11-25 **20:29**（`python scripts/work572_verify.py shape`，输出存
`meta/industry/work576-shape-after.txt`，mtime 20:29:51）：

| 检查 | 数值 |
|---|---|
| `industry_*` 17 表合计（改前 20:05 基线） | 241,892 |
| `industry_*` 17 表合计（本次增量装载后，20:29） | **263,698** |
| 列相加自检 | PASS：263,698 = 上述 17 行逐行相加（`work572_verify.py shape` 内置断言，失败返回 1） |
| `data_catalog` | 405（未变）|

20:38 起并行单又装了一轮（grep 到 cninfo 14,075 行），当前读库为 **263,952**；
两轮都在基线之上，「不减少」成立。增量只做「按源 × 期分区替换 + 合并写 raw」，
收窄窗口只减少请求数，不会删掉已有 raw 行；本次增加主要来自同日并行回补的
exchange（84 → 21,756 行，WORK-578 归口）与 cnesa（2 → 33 行，WORK-577 归口）。

### 11.5 质量闸

`python -m db.checks.industry all` → **7/7 PASS**（C1–C7）。

需登记的一次瞬态：本次 daily 的第 1、2 次「装载+闸」尝试 C4 `unit_vocabulary` FAIL，
第 3 次 PASS。原因是同日并行运行的 WORK-577 正在改 CNESA 提取器：
`db/checks/industry.py` 的 CNESA 允许单位在 20:28:02 由 `{GW, %}` 扩为
`{GW, %, GWh, 个, 项, 元/kWh, 小时}`，而 raw 里的 33 行新单位在 20:27 已落盘。
即：失败来自并行单的中间状态，不是本单代码；用本单分支（不含 WORK-577 改动的 checks）单独跑闸，
C4 仍会因这 33 行报 FAIL——这与本单无关，归 WORK-577 收口时一并生效。

### 11.6 已知边界

- 日常档只补**窗口内**的缺日；窗口外的历史缺口要手工 `--mode backfill`。
- 月频 8 源的增量窗口只保证「最近一期能拿到」，不保证补全历史；历史由 §10 的回填一次性完成。
- 巨潮的截断告警存在既有误报：本次「有色金属/sse」上游 `totalpages=34`，但第 25 页返回不足
  30 条即正常结束，脚本按 `pages < totalpages` 记了 `truncated`（`failed.json` 里 2 条）。
  数据没丢（上一页到边界即停是上游分页的真实结尾），告警文字需要改成「提前结束」才准确；
  本单不动这段判定，登记在此。
- 国际 5 个快照源是**覆盖写**（`raw/industry_alt/<源>/data.csv` 只存当日快照），
  历史靠 DuckDB 分区保留；这是既有形态，本单未改。
- 抓取脚本本体不在 git（`code/industry_fetch/`、`raw/industry_alt/*/fetch.py`）；
  本单改动以 `meta/industry/work576-code-sha256.txt` 锁版，`work572-code-sha256.txt` 是改前基线。

### 11.7 产物摘要与回滚

```bash
cd /srv/share/quantdb && sha256sum \
  code/industry_fetch/_common.py \
  code/industry_fetch/fetch_{gfex_lithium,cninfo_ann,caam_output,cpcaauto_nev,mof_fiscal,nea_power,pbc_shrzgm,stats_gov}.py \
  code/quant-lab/db/timers/industry.sh \
  | tee meta/industry/work576-code-sha256.txt
```

回滚：

1. 代码：`git revert <WORK-576 commit>`（只动 `db/timers/industry.sh` 与本文档），
   抓取脚本按 `work572-code-sha256.txt` 的基线逐文件回退；
2. 定时任务不需要改：`industry.sh` 回退后仍按两档调用，只是重新依赖各脚本默认值；
3. 数据：本次没有删改历史行，不需要数据回滚；如需回到 20:05 基线，按 §10.5 ①的快照恢复。

## 12. 能源局换源与 2021–2023 回补（WORK-581，2026-11-25）

### 12.1 一句话

`industry_nea_power` 的列表发现从「站内搜索翻页」换成 `xwfb` 频道的静态数据源
`ds_<datasource>.json`；标题正则不再要求「YYYY年M月」；电力统计稿正文的 HTML 表逐行解析。
**回补 2021-01–2023-12 共 55 篇、1,071 行，表从 606 行 / 23 期扩到 1,677 行 / 54 期**（期覆盖 2021-03 起）。

### 12.2 数据源与解析口径

| 项 | 实施后 | 证据（命令见 12.6） |
|---|---|---|
| 列表 | 频道页 `#showData0` 现解析 datasource id → `GET /xwfb/ds_<id>.json`（一次拿到全量） | 400 条、2016-04-07..2026-11-25、412,442 B；`pageNo/pageSize` 被忽略（滚动快照） |
| 标题过滤 | 只判主题（`全社会用电量` / `电力工业统计数据`），期由「标题月份 + publishTime」推导 | 2021-01–2024-02 候选 56 篇，旧正则命中 **0**，新口径窗口内命中 **55**、期推导失败 **0** |
| 表格 | 表头含「指标/单位」即认表；数值列按表头含「累计」记累计、含「N月」记当月；同比列不入库 | 19 篇有 HTML 表、**663 物理数据行**（+19 表头 = 探针口径 682）、拆成 699 值行（6 列版当月+累计各一行） |
| 图片表 | 26 篇电力统计稿中 **6 篇的表是图片**（发布日 2021-07-16、2021-10-21、2022-01-26、2022-04-20、2022-07-19、2023-01-18；统计期 2021-06/09/12、2022-03/06/12），只取正文摘要数字 | `raw/industry/nea_power/failed.json` 逐条有 URL 与图片张数；未做 OCR（不在本单） |
| 指标名 | 表格里的「水电/火电/核电/风电/太阳能发电」跨小节重名，拼成 `全国发电装机容量/水电`；「第一产业用电量」这类完整指标名保持裸名，与散文行同名（实测 `第一产业用电量` 55 行散文 + 19 行表行同名） |
| 散文 | 按句推进口径状态机（累计/1-N月→累计，N月份→当月，年度稿→累计），分产业句沿用上一句 | 372 行正文指标行，`[当月]`/`[累计]` 前缀写在 raw 的 `context` |
| 单位 | 表内按上游原样：万千瓦、亿元、%、克/千瓦时、万百万千焦、万吨、万千伏安、千米；散文仍是 亿千瓦/亿千瓦时/小时 | C4 词表已按实测全集固定（11 个单位），出现新单位仍 FAIL |

### 12.3 实测结果（2026-11-25 21:22 与 21:37 两轮，均为同一 SHA）

| 指标 | 回补前 | 回补后 | Δ |
|---|---:|---:|---:|
| `industry_nea_power` 行数 | 606 | 1,677 | **+1,071** |
| 期数（非空 distinct period） | 23（2024-07..2026-10） | 54（**2021-03**..2026-10） | +31 |
| 来源 URL | 92 | 147 | +55 |
| 其中 2021–2023 行 | 0 | 1,071（2021: 197 / 2022: 382 / 2023: 492） | +1,071 |

期覆盖 31 个**可达**期里的 31 个：上游把 1 月并入「1–2 月合刊」（所以 2021–2026 从无 1 月期，可达期 = 33），真实缺稿只有 **2021-02、2021-10** 两期——400 条快照里没有对应条目，
不是脚本漏抓（探针与本次两轮一致）。请求量级：正式跑每轮 **57 次**（1 频道页 + 1 ds JSON + 55 正文，
`--sleep 2.0`，单轮 117 秒）；本单累计约 130 次（两轮正式跑 + dry-run + 抽样核对 + 复核脚本）。

### 12.4 质量闸（改动前后同一库、同一命令）

| 数据状态 | 旧 SQL（基线 `1029a1b`） | 收口后的 SQL |
|---|---|---|
| 第 1 轮（8 篇「达到 N 亿千瓦时」的当月全社会用电量还没抓到） | `pairs=55, ok=47` → **FAIL** | — |
| 最终库（1,677 行） | `pairs=47, ok=47` → **PASS，但 2021–2023 的 26 组被静默排除**（`n_parts≠4`） | **`pairs=73, ok=73`（PASS，26+47 组全覆盖）** |

`unit_vocabulary`：`10/11`（nea 8 个新单位报错）→ **`11/11`**；`checks all`：C4 FAIL → **7/7 PASS**。

> 旧 SQL 的**真实缺陷不是判 FAIL 而是判 PASS 时少算了 26 组**：2021–2023 每篇用电量稿同时给当月与累计两行，
> 旧写法把全社会用电量也一起 JOIN 进来，分产业行数翻倍成 `n_parts=8`，`FILTER (WHERE n_parts = 4)` 就把这些组
> 静默丢掉；再加上全社会用电量取 `max`（累计）、分产业取 `min`（当月），两侧口径本身也不一致。
> 复现（只读）：`git show 1029a1b:db/checks/industry.py` 里的 nea 子闸 SQL 直接在本库跑 → `(47, 47)`；
> 把 `max(t.value)` 改 `min`、并把全社会用电量先按 (文章, 期) 收敛 → `(73, 73)`。

两处闸改动都只针对 nea，且都不是放宽阈值：

1. **口径**：分产业取 `min`（当月）与全社会用电量取 `max`（累计）相比是笔误——注释写的就是「用 min() 取当月值」。
   回补后每条用电量稿同时有当月、累计两行，`max` 拿累计比当月必错；先把全社会用电量按 (文章, 期) 收敛成
   `min` 再连接，两侧才同口径（旧 SQL 的 JOIN 还会让分产业行数翻倍、`n_parts≠4` 被静默排除，55 组只剩 47 组）。
2. **词表**：8 个单位是 2021–2023 表里真实出现的单位，按实测全集写死；上游新增第九个单位仍判 FAIL。
   负向对照：把 2021-03 第二产业当月值手工 +5% 后同一 SQL 得 `ok=72, pairs=73`（闸能抓住错值）。

### 12.5 勾稽自检

- ① 列相加 = 合计：表格值行 **699** + 正文行 **372** = **1,071** = 2021–2023 raw 行数 ✅
- ② Δ 相加 = 合计 Δ：回补前 **606** + 新增 **1,071** = **1,677** = 库内现有行数 ✅
- 表行口径差：探针 682 = 物理数据行 **663** + 表头行 **19**；699 与 663 的差是 6 列版表格的当月/累计各存一行（36 行）
- 统计时点：2026-11-25 21:38（`meta/industry/load-20261125T213802.json`）；来源命令见 12.6 A/B 段

### 12.6 复跑与回滚

```bash
# A) 复核（只读库与 raw；ds 段 GET 2 次）
cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python scripts/work581_verify.py all
#   期望最后一行 RESULT PASS (0 项不满足)

# B) 重抓（57 次请求，约 2 分钟；raw 合并写，幂等）
cd /srv/share/quantdb/code/industry_fetch
/home/moyongkai/.venvs/quantdb/bin/python fetch_nea_power.py \
  --mode backfill --from-ym 2021-01 --to-ym 2023-12 --sleep 2.0

# C) 装载 + 全闸（与两档定时任务同一入口）
cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  flock -w 7200 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all
```

回滚：

1. 数据：`DELETE FROM industry_nea_power WHERE period < '2024-07-01'`（回补只新增 2021–2023 行，
   回补前的 606 行原样保留；raw 侧回补前的 `part.parquet`/`manifest.json`/`failed.json` 在
   `raw/industry/nea_power/.pre-work581/` 备份）。
2. 代码：抓取脚本不在 git（既有形态），按 `meta/industry/work581-code-sha256.txt` 核对；
   退回旧版本 = 用 `work572-code-sha256.txt` 里 `fetch_nea_power.py` 的基线文件覆盖，并恢复
   `industry.sh` 中 `[nea_power]` 一行的旧参数。
3. 闸：`git revert <WORK-581 commit>` 只影响 `db/checks/industry.py` 的 nea 两处与本文档。

## 13. 预测表 industry_forecast（WORK-584，2026-11-25）

### 13.1 一句话

CNESA 公开页里的 3 条未来期预测值（2030 年累计装机 3.7 亿千瓦、2030 年平均时长
3.47 小时、十五五工商业储能 30GW）单独进 `industry_forecast`，逐行带提出方
（source_org=CNESA）、原文 URL（source_doc）、原句（context，含「预计/有望」）与
提取时点；观测表 `industry_cnesa_storage` 与既有 C1..C7 判据一字未改，新增
C8_forecast 专查预测表。

### 13.2 为什么单建表

- 既有 C3 日期域是 `2000-01-01 .. 今天+1`，为观测表而定。预测的目标期在 2030 年，
  塞进观测表必然 FAIL；放宽 C3 又会让真正的错期数据混过闸。
- 观测值与预测值口径不同（已发生 vs 展望），混在一张表里只能靠列注释区分，查询
  容易把展望当事实。
- 因此：新表 + 新闸 + 新数据集，观测侧零改动。

### 13.3 表结构与编码规则

表定义见 `db/schema_industry.sql`（3.1 节，列注释写全）：

| 列 | 含义 |
|---|---|
| `dataset` | 来源数据集，本批 `cnesa_forecast` |
| `indicator` / `value` / `unit` | 指标、数值、**原单位**（3.7 亿千瓦不换算成 370GW） |
| `period_target` | 预测目标期 DATE，编码规则见下 |
| `period_target_raw` | 原文表述（「2030年」「十五五」），不丢 |
| `period_target_type` | `calendar_year_end` / `five_year_plan_end`；C8 校验类型与日期一致 |
| `period_published` | 报告口径期，本批 `2026-01` |
| `source_org` | 提出方机构，本批 `中关村储能产业技术联盟 CNESA` |
| `source_doc` | 原文 URL |
| `context` | 命中数值所在原句，逐字取自正文 |
| `caliber` / `published_date` / `extracted_at` / `rule_version` | 口径说明、文章发布日、提取时刻、规则版本 |

`period_target` 编码（非日历期照此写，不许自创）：

- `calendar_year_end`：日历年展望取该年 `12-31`（2030 年 → `2030-12-31`）；
- `five_year_plan_end`：五年规划期取期末年 `12-31`（十五五 = 2026–2030 → `2030-12-31`）。

主键 `(dataset, indicator, period_target, source_org)`，即「指标 + 目标期 + 提出方」
唯一；装载前强校验，冲突直接报错，不静默覆盖。

### 13.4 入库数据（2026-11-25 22:55 最终版实装）

| 指标 | 数值 | 单位 | 目标期 | 原文表述 | 提出方 | 原句 |
|---|---|---|---|---|---|---|
| 新型储能累计装机规模 | 3.7 | 亿千瓦 | 2030-12-31 | 2030年 | CNESA | 展望”十五五“，增速虽然放缓，但庞大的基数仍将催生可观的绝对增量，预计2030年累计装机有望达到3.7亿千瓦以上。 |
| 新型储能累计装机平均时长 | 3.47 | 小时 | 2030-12-31 | 2030年 | CNESA | 2026年起，时长提升明显加速，预计至2030年将达到3.47小时。 |
| 工商业储能累计装机规模 | 30 | GW | 2030-12-31 | 十五五 | CNESA | 展望“十五五”，工商业储能继续保持稳定增长态势，收益更加多元， 由单一的“固定价差套利”向“波动的市场价差套利+需量管理+需求响应”转变 ，累计装机有望突破30GW。 |

原文：`https://www.cnesa.org/information/detail/?column_id=1&id=7798`（HTTP 200，
341,562 字节），文章发布日 `2026-01-22`，报告口径期 `2026-01`。

### 13.5 链路

```text
fetch_cnesa_storage.py（live 树，不在 git）
  ├─ RULES / cnesa-extract-v2 ──> raw/industry/cnesa_storage/part.parquet（33 行观测）
  └─ FORECAST_RULES / cnesa-forecast-v1
       ├─> raw/industry/cnesa_forecast/part.parquet（3 行预测）
       ├─> raw/industry/cnesa_forecast/article.txt（正文快照，C8 离线回指用）
       └─> raw/industry/cnesa_forecast/failed.json（预测提取失败项，本次 0 条）
db.etl.industry load
  ├─ SOURCES（17 源）──────────> 17 张观测表（run_id 不带后缀）
  └─ FORECAST_SOURCES（1 源）──> industry_forecast（run_id 带 F 后缀，C5 的「17 表完整运行」不受影响）
db.checks.industry
  ├─ C1..C7（判据未改）────────> 17 张观测表
  └─ C8_forecast ─────────────> industry_forecast
```

预测句式必须在原文里带语气词（预计/有望/展望/预测），否则不认、记 failed.json；
宁可不提取，也不把「有望突破 30GW」洗成确定值。

### 13.6 质量闸 C8_forecast

`db/checks/industry.py::c8_forecast`，九项合取，任一不满足即 FAIL：

1. 表存在且行数 > 0（空表不算 PASS，避免假绿）；
2. 单位在词表 `{GW, 亿千瓦, 小时}` 内；
3. `source_org` / `source_doc` / `context` / `rule_version` / `period_target_raw` 非空；
4. `period_target` 全部晚于今天（预测表特征）；
5. `(indicator, period_target, source_org)` 无重复；
6. `period_target_type` 在允许集合内，且 12-31 编码一致；
7. `period_target_type` 与原文表述交叉一致：日历年必须是「YYYY年」，五年规划必须是
   「十…五」（两者都编码成 12-31，单看日期区分不了类型）；
8. 每行 context 含「预计/有望/展望/预测」语气词；
9. 每行 context 能在 `raw/industry/<dataset>/article.txt` 正文快照里逐字定位。

### 13.7 实测证据（2026-11-25 22:40–22:56，同一版本）

```bash
# 抓取（写 raw + 快照）
cd /srv/share/quantdb/code/industry_fetch && /home/moyongkai/.venvs/quantdb/bin/python fetch_cnesa_storage.py
# [cnesa_forecast] rows=3 bytes=12237 rule_version=cnesa-forecast-v1 sha256=d9cba67d916031cd… failed=0
# [cnesa_storage]  rows=33 bytes=12607 elapsed=1.9s failed=11

# 装载两次（同参数幂等）
flock /srv/share/quantdb/logs/.quantdb-db.lock … -m db.etl.industry load --sources cnesa_forecast
# [industry] industry_forecast  raw=3 staged=3 dedup=0 total=3 partitions=1
# 第二次同样 total=3

# 8 道闸
… -m db.checks.industry all
# C1 17 表 265,023 行；C2..C7 PASS；C8_forecast PASS（rows=3, duplicate_business_key=0,
# target_not_future=0, missing_modality=[], snapshot_failures=[]）

# 端到端核对（提取 → raw → 库 → 闸）
… scripts/work584_verify.py
# 结论：PASS（0 项失败）
```

raw 摘要（2026-11-25 22:55 最终版）：`part.parquet` 12,237 B，sha256
`d9cba67d916031cd1d25c018f4a9da3013882193d9588cd1abf0ba22a4b4407b`；`article.txt` sha256
`d10e399b1c4716501dd9c2f2a5f523edb90e579cd6f7197769f846f03791cb75`（正文未变，快照哈希稳定）。
每次抓取 `extracted_at` 不同，parquet 哈希随提取时刻变化，属预期；判断同一版正文看 `article.txt`。

### 13.8 回滚

1. 数据（只删预测表内容，观测表不动）：
   `DROP TABLE IF EXISTS industry_forecast;`，或保留表结构只清行 `DELETE FROM industry_forecast;`。
2. raw：`raw/industry/cnesa_forecast/` 整目录是新增的，直接删除即可回到 WORK-577 状态
   （观测 raw `raw/industry/cnesa_storage/` 的 `part.parquet` 会被本单的新 fetcher 重写一次，
   行数仍 33；回退 fetcher 后重跑一次可回到 14 条 failed 的旧清单）。
3. 代码：`git revert <WORK-584 commit>`（`db/schema_industry.sql`、`db/etl/industry.py`、
   `db/checks/industry.py`、测试与本文档）；live 树抓取脚本不在 git，按
   `meta/industry/work584-code-sha256.txt` 里 `fetch_cnesa_storage.py` 的基线 sha256 恢复旧版。
4. 闸：回退后 C8 从 `all` 里消失，C1..C7 行为不变。

### 13.9 变更文件

- `db/schema_industry.sql`：新增 `industry_forecast` 表与列注释；`industry_cnesa_storage` 表注释指向 §13。
- `db/etl/industry.py`：`transform_cnesa_forecast` / `parse_local_timestamp` / `FORECAST_SOURCES`；
  `load` 分两组装载（观测 run_id 无后缀，预测 run_id 带 `F`）；装载前先校验所有选中源
  的 raw 是否存在，缺文件整体拒绝，不会装一半。
- `db/checks/industry.py`：新增 `C8_forecast`；C1..C7 一字未改。
- `db/tests/test_industry_forecast.py`：装载幂等、撤行替换、主键冲突、raw 缺失前置拒绝、C8 九种判定、提取契约，共 16 项。
- `scripts/work584_verify.py`：端到端核对（提取 → raw → 库 → C8）。
- `scripts/work577_verify.py`：覆盖率统计把预测命中区间计入，避免误报「需补规则」。
- `scripts/work572_verify.py`：`shape` 的 17 张观测表口径排除 `industry_forecast` 并单独打印预测表行数，
  避免文档里的例行核对命令因第 18 张表静默返回 1。
- live 树 `/srv/share/quantdb/code/industry_fetch/fetch_cnesa_storage.py`：预测句式、预测数据集、
  正文快照与 failed.json（不在本仓库，既有形态；按 sha256 锁版）。

## 14. 装载变更留痕（WORK-585，2026-11-25）

### 14.1 一句话

每次装载替换之前，先算「库内现状 vs 本次快照」的逐行差异写进 `industry_load_diff`
（**四要素**：原值 `old_value` / 新值 `new_value` / 原因 `reason` / 对应 URL `source_doc`），
再执行替换（同事务，先记账后动手）；`cnesa_storage` 改为**整源替换**，WORK-577 遗留 F3
（上游删期、库内残留幽灵行）随之消除；raw 快照按内容版本化留档到各源 `history/`（只增不删）；
复核看 `v_industry_load_changes_recent`（近 30 天的值变化与被删行，列里带 `reason` / `source_doc`，
是大屏「数据变更」区块的数据源）。

### 14.2 表结构

`industry_load_diff`（12 列，**无主键**：一次变更一行，同一 run 同一表可有多行）：

| 列 | 类型 | 含义 |
|---|---|---|
| `run_id` | VARCHAR | 与 `industry_load_audit.run_id` 同一次运行（预测组带 `F` 后缀） |
| `source` | VARCHAR | 源名（`Source.name`） |
| `table_name` | VARCHAR | 目标表 |
| `period` | DATE | 分区日期；非日期分区源为 NULL |
| `indicator` | VARCHAR | 业务键文本 `k=v\|k=v`（row_hash 表用 `BUSINESS_KEYS` 渲染） |
| `change_type` | VARCHAR | `value_changed` / `row_added` / `row_dropped` / `no_change` |
| `column_name` | VARCHAR | `value_changed` 时变化的列名 |
| `old_value` | VARCHAR | 变化前列值 / 被删行整行文本；源侧为空记 `∅` |
| `new_value` | VARCHAR | 变化后列值 / 新增行整行文本；`no_change` 存汇总文本 |
| `source_doc` | VARCHAR | 被改动行的出处 URL：**行内 URL 列优先**（`source_url` / `url` / `adjunct_url`），行内没有则取 manifest 登记的出处页；都没有才为 NULL |
| `reason` | VARCHAR | 改动原因（人读一句话）：装载模式 + 本次快照抓取时间 + **具体改了什么** + `；原句：…`（上游原文那句）；**不允许为 NULL**，长度 ≤ 500 |
| `changed_at` | TIMESTAMP | 写入时刻（本地墙钟） |

`reason` 的取值形态（由 `db/etl/industry.py::change_reason` 生成，不手填）：

| change_type | reason 示例 |
|---|---|
| `value_changed` | `整源替换：本次快照（2026-11-25T22:00:00+0800）value 由 144.7 改为 159.17；原句：截至2025年12月底，中国新型储能累计装机规模达到 144.7GW，同比+85% 。` |
| `row_added` | `按期替换：本次快照（…）新增该行；原句：…` |
| `row_dropped` | `整源替换：本次快照（…）不再包含该行；原句：…` |
| `no_change` | `按期替换：源快照（…）与库内逐行一致，无变化` |
| 历史行（首版上线前写入） | `WORK-585 首版上线前写入，无原因字段（历史行）` |

`value_changed` 不再写「该字段取值变化」这种同义反复，而是**写明哪列、从什么改成什么，再接原句**（长文本列的变化值会截到 80 字符，保证原句不被挤掉）：

```
整源替换：本次快照（2026-11-25T22:00:00+0800）value 由 144.7 改为 159.17；
原句：截至2025年12月底，中国新型储能累计装机规模达到 144.7GW，同比+85% 。
```

原句的取法（`sentence_of()`，按优先级）：行内 `context` → `caliber` 里「｜原句：」之后那段 →
`article_title` / `title` / `key_figure`。**都没有就如实写「原句：无（结构化接口数据，来源见
source_doc）」**，不拿标题或链接冒充原句。exchange / trendforce / benchmark / gfex / ppi 这几个源
本来就是结构化接口（列里没有正文），生产库里它们的 reason 会带这句「无」。

**`source_doc` 的 manifest 兜底与多出处页的选择规则**（WORK-647 验收 #2）：

1. 先看行内 URL 列（8 个源有：gfex / lpr / cnesa / cninfo / caam / cpca / mof / nea / stats / pbc /
   sne / ppi 等，取 `source_url` / `url` / `adjunct_url`）；
2. 行内没有（exchange / trendforce / benchmark 三张表没有 URL 列）→ 读 manifest：
   `raw/industry/<源>/manifest.json` 的 **`source_urls`**、`raw/industry_alt/<源>/manifest.json` 的
   **`sources`**，两个键都读（只读一个会在这几个源上静默拿到 None）；
3. 一个源登记多条出处页时，**按行内特征挑命中条**：`exchange=GFEX/SHFE` → gfex/shfe 那条，
   `series` 含 DRAM/NAND/Flash → dram/flash 那条；挑不到取首条；
4. 用 manifest 兜底的行会在 `reason` 末尾标 `；页级出处（manifest 登记）`，不假装是行级出处。

**存量行回填**（`backfill_diff_provenance()`，每次装载前跑一次、幂等）：实现兜底之前写入的变更行
（上一次验收时 229 行无 URL、全部无原句）按各源**当前** manifest 与已存行文本补齐，已有内容不覆盖。
回填前的整表快照留在 `/srv/share/quantdb/staging/industry_load_diff-before-work585-rework-20261129.parquet`。

**存量表兼容**：生产库在本单首版落地时已有同表（75 行 10 列）。`apply_schema` 每次装载执行
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` 补 `source_doc` / `reason`（追加在物理末尾），并把
**迁移时点（2026-11-25 23:50）之前**写入的行回填为上面那句「无原因字段」——不用 NULL 冒充「有原因」；
时点之后的行即便 `reason` 为 NULL 也不回填，交给 C9 判负，避免把 bug 洗成「历史」。写入用**显式列名**
的 INSERT，不依赖列顺序。

新建库的 `reason` 是 `NOT NULL`，生产存量表是 `ALTER` 追加的可空列（加 `SET NOT NULL` 一旦有残留
NULL 会让每次装载直接失败，风险大于收益）；「不允许为空」由装载器与 C9 共同保证。

写入时机在 `db/etl/industry.py::load_source`：

```
BEGIN TRANSACTION
  1. 读库内现状（分区模式：本次 staged 覆盖的期；整源模式：全表）
  2. 算差异 → INSERT INTO industry_load_diff（含 reason / source_doc）
  3. DELETE（分区 / 整源）→ INSERT OR REPLACE
  4. INSERT industry_load_audit
COMMIT                （任一步失败 → ROLLBACK，库内保持装载前状态）
```

**为什么单独一张表**：`industry_load_audit` 只记行数与耗时，回答不了「这次替换改了什么、删了什么」；
复核要求逐行可读，因此差异单独落表，audit 结构与 17 张观测表都不动。

**为什么没有 `row_dropped` 的「配对」**：`row_hash` 主键的表内容改版时主键本身变了，库里看到的就是
「旧 hash 行没了 + 新 hash 行来了」。要不要把这两行认成「同一条业务记录被改了」需要推断（业务键不唯一，
146 行只对应 98 个业务键），推断错的代价比不推断大，因此保留 add/drop 两行，`indicator` 用同一业务键
文本渲染，人一眼能对上。

### 14.3 怎么查

```sql
-- 近 30 天需要人看一眼的变更（值变化 + 行被删），带原因与原文 URL
select * from v_industry_load_changes_recent order by changed_at desc limit 50;

-- 某一次运行改了什么（四要素齐全）
select change_type, table_name, period, indicator, column_name,
       old_value, new_value, reason, source_doc
from industry_load_diff where run_id = '20261126T000000'
order by table_name, indicator;

-- 本轮到底跑过没有（no_change 汇总行）
select table_name, reason from industry_load_diff
where run_id = '20261126T000000' and change_type = 'no_change';
```

`v_industry_load_changes_recent` 的列：`run_id / source / table_name / period / indicator /
change_type / column_name / old_value / new_value / source_doc / reason / changed_at`
——大屏「数据变更」区块直接读这张视图即可，不需要再回表拼 URL。

### 14.4 快照源整源替换（F3 修复）+ raw 版本化留档

`Source` 增 `replace_mode`：`"partition"`（默认，按期替换）与 `"source"`（整表替换）。
**本单只给 `cnesa_storage` 设 `"source"`**，其余 16 个观测源与预测源维持原模式不变。

| 场景 | 改前 | 改后 |
|---|---|---|
| 上游改版删掉某一期（如 2021 年末时长行） | 按期替换只删「本次出现的期」，被删的期在库内残留，7 闸全绿（F3） | 整源替换，被删期从库内消失，并落一行 `row_dropped` |
| 快照抓空/解析失败 | 整页 0 行 → 按期替换什么也不删（安全） | **拒绝清空**：`SystemExit` 报错，事务回滚，库内保持原状 |

安全阀的理由：整源替换一旦遇到空快照就会把整表抹掉，比 F3 更严重；宁可失败并告警，让人先查 raw。
阀门按**源自身的模式**判定，`--full` 也不豁免——否则「快照抓空 + `--full`」会清空整源，而随后的
正常装载又被同一个阀门挡住，形成「表空且装不进去」的卡死态。确需清空请手工 `DELETE` 并写明原因。

量级提醒：`--full` 会为被清掉的行各写一条 `row_dropped`（`industry_stats_gov_release` 13.7 万行
→ 约 27 万条 diff，`executemany` 实测约 2,450 行/秒），所以它只用于排查，不进定时任务。

**raw 快照版本化（`history/`）**：每次装载读 raw 之前，`archive_raw_snapshot()` 把该源当前快照复制到
`raw/<kind>/<source>/history/<sha256前12位>-<mtime><后缀>`。去重按**内容**判定（逐个比大小 + 数据文件
sha256）；文件名里的 `<mtime>` 只是给人看的时间戳，因此「内容相同、mtime 不同」（例如人工 `touch`）
会另存一份——这种重复不损坏数据，只多一点体积。
原始数据因此只增不删：上游改版、抓取脚本换解析口径，都能回到任意历史版本比对。

**膨胀口径要说清**：哈希覆盖数据文件 + `manifest.json`（+ `article.txt`），而 `manifest.fetched_at`
每次抓取都会变——所以**每个抓取批次都会新增一套**（约 1 份数据 + 1 份 manifest），不是「上游不变就不增长」。
按当前规模（最大原始文件 1.0 MB、17 个源）每天新增约 3–5 MB，一年约 1.5 GB；要限额需另做归档策略。

「快照」= **数据文件 + `manifest.json`（+ 预测源的 `article.txt`）**，三者的哈希合并算一个版本：
`manifest.fetched_at` 决定 `reason` 里的快照时间，`transform_trendforce` / `transform_benchmark` 的
`snapshot_date` 完全来自 `manifest.fetched_at`——只留数据文件的话，从 history 恢复旧数据会被安到今天。
复制走「临时文件 + rename」，拷贝中途崩溃不会留下截断文件而前缀已匹配、以后永不重拷。

过去「就地 `.bak` 覆盖」的做法退役——`.bak` 只在改动前手工留一份，下一次改动就被覆盖，等于没有历史。
（注：抓取器 `code/industry_fetch/` 内部写盘失败的一次性还原仍用 `.bak` 临时命名，成功即删，
不是留档机制，与本条不冲突；该目录不在本仓库。）

F3 回归的覆盖面：`db/tests/test_industry_diff.py::test_f3_regression_from_offline_html_fixture` 从
**离线 HTML fixture** 走抓取器正文解析（HTML → 文本 → 期与指标）再装载，覆盖「上游删句 → 少一期 →
库内清除并进日志」整条链路；抓取器本体在 `/srv/share/quantdb/code/industry_fetch/`（不在本仓库，
既有形态），测试在目录不存在时 skip。

### 14.5 质量闸 C9_load_diff

新加第 9 道闸（编号让开 WORK-584 的 `C8_forecast`；C1–C7 判据未改）：

1. 取「覆盖全部 17 张表」的最新一次运行（与 C5 同一查询口径）；
2. 该 run 的 17 张表**每张都要留过痕**（差异行或 `no_change` 汇总行）——「跑过必留痕」；
3. 每条 diff 的 `(run_id, table_name)` 在 `industry_load_audit` 里存在；
4. `change_type` ∈ 4 个合法值；**`reason` 不得为空**；`value_changed` 必须有 `column_name`
   与前后值，`row_added` 必须有 `new_value`，`row_dropped` 必须有 `old_value`，`no_change` 的
   `column_name` 与 `old_value` 必须为 NULL。

它只回答「替换有没有记账、记全没记全」，不判断变化是否符合业务预期——后者是人看 §14.3 视图的事。

### 14.6 复跑命令（绝对路径）

```bash
# 离线行为测试（不碰生产库：raw 与库都在 pytest tmp_path 里）
cd /srv/share/quantdb/code/quant-lab
PYTHONPATH=$PWD /home/moyongkai/.venvs/quantdb/bin/python -m pytest db/tests/test_industry_diff.py -q

# 只装载快照源，看 diff 落库与 history 归档
QUANTDB_HOME=/srv/share/quantdb flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load --sources cnesa_storage
ls -la /srv/share/quantdb/raw/industry/cnesa_storage/history/

# 复核视图（大屏「数据变更」区块同源）
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python -c "
import os, duckdb
con = duckdb.connect(os.environ['QUANTDB_HOME'] + '/db/quantdb.duckdb', read_only=True)
print(con.execute('select * from v_industry_load_changes_recent limit 20').df().to_string())
"
```

### 14.7 回滚

**回滚对象是提交，不是某一个文件。** 功能实现收口在 **`9078525`**
（`fix(WORK-585): 独立审查 1 blocking + 2 suggestion + 3 nit 处置`），同轮更早的 `1df4704` 是它的前一版
（`git log --oneline 59e5682..9078525` 可见）。回退区间固定写 `59e5682..9078525`。

**必须先 `git switch --detach 9078525`**：区间回退要在「与该区间末端一致的树」上跑，否则分支上后续的
docs 提交会让 `docs/…`、`DELIVERY.html` 之类文件冲突（实测：直接在交付 HEAD 上跑 `59e5682..9078525`
会因 `docs/DELIVERY-WORK-585.html` 一行冲突而失败）。不要用 `..HEAD`——HEAD 会继续前进。

```bash
# ⓪ 先看要回退的是什么（应看到 db/etl/industry.py、db/checks/industry.py、
#    db/schema_industry.sql、db/tests/test_industry_diff.py 等代码文件，不是只有 docs）
git -C /srv/share/quantdb/code/work-585 show --stat 90785255bb1462a26674fe4ffa66277e5a41a988

# ① 先切到功能提交再整段回退（不切会与后续 docs 提交冲突）
cd /srv/share/quantdb/code/work-585
git switch --detach 90785255bb1462a26674fe4ffa66277e5a41a988
git revert --no-commit 59e5682..9078525
git diff --cached --stat

# ② 同步到线上跑的那棵树（live 树在另一个分支，这些文件在那边未追踪，revert 不会自动生效）。
#    注意：db/tests/test_industry_diff.py、scripts/work585_verify.py、scripts/work585_build_delivery.py
#    在基线 59e5682 里【不存在】，`show … > $f` 会先截断再失败、留下 0 字节空文件，
#    所以必须先判存在：不存在就删除。
cd /srv/share/quantdb/code/quant-lab
for f in db/etl/industry.py db/checks/industry.py db/schema_industry.sql \
         db/tests/test_industry_diff.py scripts/work585_verify.py \
         scripts/work585_build_delivery.py; do
  if git -C /srv/share/quantdb/code/work-585 cat-file -e 59e5682:$f 2>/dev/null; then
    git -C /srv/share/quantdb/code/work-585 show 59e5682:$f > $f
  else
    rm -f $f          # 基线里没有 = 本单新增，回滚就是删掉它
  fi
done

# ③ 库对象（只删留痕对象，不动 17 张观测表）
flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python - <<'SQL'
import duckdb
con = duckdb.connect("/srv/share/quantdb/db/quantdb.duckdb")
con.execute("DROP VIEW IF EXISTS v_industry_load_changes_recent")
con.execute("DROP TABLE IF EXISTS industry_load_diff")
con.execute("CHECKPOINT"); con.close()
SQL
```

**PG / 大屏侧受影响面（回滚时必须一起处理，否则成孤儿）**：

| 位置 | 内容 | 回滚后症状 | 处理 |
|---|---|---|---|
| `code/metabase/schema_pg.sql:134` | `CREATE TABLE IF NOT EXISTS industry_load_diff` | DuckDB 侧删表后 PG 表仍在，数据停止更新 | 手工 `DROP TABLE industry_load_diff;`（PG） |
| `code/metabase/schema_pg.sql:152` | `industry_load_diff_changed_at_idx` 索引 | 随表一起成孤儿 | 随表 DROP |
| `code/metabase/sync_to_pg.py:432` | `OPTIONAL_TABLES = {"industry_load_diff"}` | sync 发现源表不存在会**静默跳过**，不报错 | 无需改代码，但要知道「不报错≠正常」 |
| `code/metabase/dashboard/lib.py:40`（WORK-586 大屏「数据变更」区块） | 直读该表 | 区块无数据（空表/查询失败） | 同步下线或改为占位 |

**数据侧**：`history/` 里的 raw 快照只增不删，回滚代码不用动它；回到某个历史快照要**数据文件与同前缀
`manifest.json`（预测源还有 `article.txt`）一起复制回去**——只还原数据文件会让 `snapshot_date` 与
reason 里的快照时间变成当前值。整源替换已经清掉的残留行不会因回滚代码复活，要恢复按 raw 或历史快照重装。

**回填可逆**：`backfill_diff_provenance()` 只改 `source_doc` 与 `reason` 两列，回填前整表快照在
`/srv/share/quantdb/staging/industry_load_diff-before-work585-rework-20261129.parquet`（`COPY ... TO` 导出，
`read_parquet()` 可直接比对）。
