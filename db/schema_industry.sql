-- WORK-568 行业层规范化 schema
--
-- 源：/srv/share/quantdb/raw/industry/<source>/part.parquet（10 个国内源）
--     /srv/share/quantdb/raw/industry_alt/<source>/data.csv（7 个国际源）
-- 共 17 个已落盘源 -> 17 张 industry_* 观测表；另有 1 张预测表
-- industry_forecast（WORK-584，来源不是独立抓取源，而是从文章正文分流出的未来期预测）。
-- 本文件只建行业层对象，不触碰既有 20 张 A 股表与 v_daily_qfq / v_daily_hfq。
--
-- 约定（与 db/schema.sql 一致）：
--   * 列名 snake_case，全小写；
--   * 每张表有 PRIMARY KEY；键不唯一的源用内容哈希 row_hash 作主键
--     （原因写在对应表注释里，本层不丢行）；
--   * 日期列用 DATE，月份取当月 1 日，原始文本另存 <col>_raw；
--   * 单位写进 COMMENT ON COLUMN；单位冲突（如 gfex 成交额）按实测口径写。

PRAGMA threads=4;

-- ---------------------------------------------------------------------------
-- 1. 广州期货交易所·碳酸锂期货日行情（日频）
--    源：http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_gfex_lithium_carbonate (
    "trade_date"     DATE    NOT NULL,
    "contract"       VARCHAR NOT NULL,
    "variety"        VARCHAR,
    "field"          VARCHAR NOT NULL,
    "indicator"      VARCHAR,
    "value"          DOUBLE,
    "unit"           VARCHAR,
    "published_date" DATE,
    "source_url"     VARCHAR,
    PRIMARY KEY ("trade_date", "contract", "field")
);
COMMENT ON TABLE industry_gfex_lithium_carbonate IS '碳酸锂期货日行情，合约×交易日×字段；主键在实测 12960 行上唯一';
COMMENT ON COLUMN industry_gfex_lithium_carbonate.trade_date IS '交易日（DATE，源为 YYYYMMDD）';
COMMENT ON COLUMN industry_gfex_lithium_carbonate.contract IS '合约月份，如 2606';
COMMENT ON COLUMN industry_gfex_lithium_carbonate.field IS '源字段名：open/high/low/close/lastClear/clearPrice/volumn/openInterest/turnover';
COMMENT ON COLUMN industry_gfex_lithium_carbonate.value IS '字段数值，单位见 unit 列';
COMMENT ON COLUMN industry_gfex_lithium_carbonate.unit IS '开盘/最高/最低/收盘/前结算/结算：元/吨；成交量、持仓量：手（1 手 = 1 吨）；成交额：万元（源标注「元」有误，实测 turnover*10000/volume 落在当日 low..high 内，见 db/checks/industry.py C4）';

-- ---------------------------------------------------------------------------
-- 2. 全国银行间同业拆借中心·LPR（每月 20 日报价；按日频调度无副作用）
--    源：https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis?lang=CN
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_chinamoney_lpr (
    "quote_date" DATE    NOT NULL,
    "tenor"      VARCHAR,
    "indicator"  VARCHAR NOT NULL,
    "value"      DOUBLE,
    "unit"       VARCHAR,
    "source_url" VARCHAR,
    PRIMARY KEY ("quote_date", "indicator")
);
COMMENT ON TABLE industry_chinamoney_lpr IS '贷款市场报价利率（LPR），报价日×期限';
COMMENT ON COLUMN industry_chinamoney_lpr.quote_date IS '报价日（每月 20 日，遇节假日顺延）';
COMMENT ON COLUMN industry_chinamoney_lpr.tenor IS '期限：1Y / 5Y+';
COMMENT ON COLUMN industry_chinamoney_lpr.value IS 'LPR 利率，单位 %';

-- ---------------------------------------------------------------------------
-- 3. 中关村储能产业技术联盟（CNESA）年度储能数据
--    源：https://www.cnesa.org/information/detail/?column_id=1&id=7798
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_cnesa_storage (
    "period"         DATE    NOT NULL,
    "period_raw"     VARCHAR,
    "indicator"      VARCHAR NOT NULL,
    "value"          DOUBLE,
    "unit"           VARCHAR,
    "caliber"        VARCHAR,
    "article_title"  VARCHAR,
    "published_date" DATE,
    "source_url"     VARCHAR,
    PRIMARY KEY ("period", "indicator")
);
COMMENT ON TABLE industry_cnesa_storage IS 'CNESA 年度储能数据（公开页摘要）；WORK-577 起按句式规则提取 33 行（2025 年末 32 行 + 2021 年末时长 1 行）；WORK-584 起 3 条未来期预测值分流到 industry_forecast，不写本表；其余未提取项逐条登记在 raw/industry/cnesa_storage/failed.json，见 docs/industry-ingest-2026.md §4.2 / §13';
COMMENT ON COLUMN industry_cnesa_storage.period IS '报告月（当月 1 日）：2026-01 = 截至 2025-12 年底；2022-01 = 2021 年末值';
COMMENT ON COLUMN industry_cnesa_storage.value IS '数值，单位见 unit（GW / GWh / % / 个 / 项 / 元/kWh / 小时）';
COMMENT ON COLUMN industry_cnesa_storage.caliber IS '口径说明 + 原文句子（「｜原句：」之后），用于回指出处；原文句子同存 raw parquet 的 context 列';

-- ---------------------------------------------------------------------------
-- 3.1 预测表：行业预测/展望值（WORK-584）
--
-- 为什么单列一张表：观测表只放已发生的统计值（C3 日期域 = 2000-01-01..今天+1），
-- 预测的目标期在未来，塞进观测表会被 C3 判 FAIL；放宽 C3 又会让真正的错期数据混过闸。
-- 因此预测值单独建表、单独一道闸（C8_forecast），观测表结构与既有 7 闸判据都不动。
--
-- 与观测表的关系：同一篇文章同一指标可能既有观测值（industry_cnesa_storage）又有
-- 预测值（本表），两表各自按自己的口径存；本表不参与 v_industry_catalog_coverage
-- 的「已覆盖」判定，覆盖口径仍只认已发生的数据。
--
-- period_target 编码规则（非日历期必须照此写，不许自创）：
--   * calendar_year_end —— 日历年展望，取该年 12-31；
--   * five_year_plan_end —— 五年规划期，取期末年 12-31（十五五 = 2026–2030 → 2030-12-31）。
--   原文表述（如「2030年」「十五五」）一律另存 period_target_raw，不丢。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_forecast (
    "dataset"            VARCHAR NOT NULL,
    "indicator"          VARCHAR NOT NULL,
    "value"              DOUBLE  NOT NULL,
    "unit"               VARCHAR NOT NULL,
    "period_target"      DATE    NOT NULL,
    "period_target_raw"  VARCHAR NOT NULL,
    "period_target_type" VARCHAR NOT NULL,
    "period_published"   DATE    NOT NULL,
    "source_org"         VARCHAR NOT NULL,
    "source_doc"         VARCHAR NOT NULL,
    "context"            VARCHAR NOT NULL,
    "caliber"            VARCHAR,
    "published_date"     DATE,
    "extracted_at"       TIMESTAMP NOT NULL,
    "rule_version"       VARCHAR NOT NULL,
    PRIMARY KEY ("dataset", "indicator", "period_target", "source_org")
);
COMMENT ON TABLE industry_forecast IS '行业预测/展望值（WORK-584）：逐行保留提出方、原文 URL、原句与提取时点；与观测表 industry_* 分表，目标期允许未来。首批 = CNESA 3 条（2030 年累计装机 3.7 亿千瓦、2030 年平均时长 3.47 小时、十五五工商业储能 30GW）';
COMMENT ON COLUMN industry_forecast.dataset IS '来源数据集（raw/industry/<dataset>/）；本批 cnesa_forecast';
COMMENT ON COLUMN industry_forecast.indicator IS '指标名；同一指标不同目标期分行';
COMMENT ON COLUMN industry_forecast.value IS '数值，单位见 unit（原样保留原文单位，不做换算；如 3.7 亿千瓦不写成 370GW）';
COMMENT ON COLUMN industry_forecast.period_target IS '预测目标期（DATE）。日历年取 12-31；五年规划取期末年 12-31，见上方编码规则';
COMMENT ON COLUMN industry_forecast.period_target_raw IS '目标期原文表述（如「2030年」「十五五」），不丢原文';
COMMENT ON COLUMN industry_forecast.period_target_type IS 'calendar_year_end / five_year_plan_end；C8 校验取值与日期编码一致';
COMMENT ON COLUMN industry_forecast.period_published IS '报告口径期（当月 1 日）：本批 2026-01 = 2025 年度数据发布';
COMMENT ON COLUMN industry_forecast.source_org IS '提出方机构（用户点名必存），本批 = 中关村储能产业技术联盟 CNESA';
COMMENT ON COLUMN industry_forecast.source_doc IS '原文 URL；C8 要求 context 能在该文档的正文快照里逐字定位';
COMMENT ON COLUMN industry_forecast.context IS '命中数值所在原句，逐字取自正文（「有望/预计」等语气词必须原样保留，不许洗成确定值）';
COMMENT ON COLUMN industry_forecast.caliber IS '口径说明 + 原文句子（「｜原句：」之后）';
COMMENT ON COLUMN industry_forecast.published_date IS '文章发布日期（提出时点的可核对证据）';
COMMENT ON COLUMN industry_forecast.extracted_at IS '提取时刻（Asia/Shanghai 本地墙钟，无时区）';
COMMENT ON COLUMN industry_forecast.rule_version IS '提取规则版本，如 cnesa-forecast-v1；规则改动必须升版本';

-- ---------------------------------------------------------------------------
-- 4. 巨潮资讯网·指定关键词公告索引（事件驱动）
--    源：http://www.cninfo.com.cn/new/hisAnnouncement/query
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_cninfo_announcements (
    "announcement_id" VARCHAR NOT NULL,
    "keyword"         VARCHAR NOT NULL,
    "category"        VARCHAR NOT NULL,
    "published_date"  DATE,
    "sec_code"        VARCHAR,
    "sec_name"        VARCHAR,
    "title"           VARCHAR,
    "adjunct_url"     VARCHAR,
    "source_url"      VARCHAR,
    PRIMARY KEY ("announcement_id", "keyword", "category")
);
COMMENT ON TABLE industry_cninfo_announcements IS '半导体/锂业/有色金属关键词公告索引；同一公告按关键词与交易所分类各存一行（749 行 / 394 个公告 id，唯一键 = 公告×关键词×分类）';
COMMENT ON COLUMN industry_cninfo_announcements.title IS '公告标题（源字段 indicator）；正文未入库，只有元数据与 PDF 链接';

-- ---------------------------------------------------------------------------
-- 5. 中国汽车工业协会（CAAM）汽车产销
--    源：http://www.auto-stats.org.cn/ReadArticle.asp?NewsID=...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_caam_auto_output (
    "period"         DATE    NOT NULL,
    "period_raw"     VARCHAR,
    "indicator"      VARCHAR NOT NULL,
    "value"          DOUBLE,
    "unit"           VARCHAR,
    "scope"          VARCHAR,
    "subject"        VARCHAR,
    "published_date" DATE,
    "article_title"  VARCHAR,
    "source_url"     VARCHAR,
    "row_hash"       VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_caam_auto_output IS 'CAAM 汽车/乘用车/商用车/新能源产销，当月与累计分指标；同一 期×指标 在多篇文章里重复出现（146 行 / 98 个业务键），用 row_hash 保留全部原文行';
COMMENT ON COLUMN industry_caam_auto_output.value IS '产量/销量，单位 万辆；单位见 unit 列';
COMMENT ON COLUMN industry_caam_auto_output.scope IS '当月 / 累计';

-- ---------------------------------------------------------------------------
-- 6. 乘用车市场信息联席分会（CPCA）新能源月报与数字
--    源：http://www.cpcaauto.com/newslist.php?types=...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_cpcaauto_nev (
    "period"         DATE    NOT NULL,
    "period_raw"     VARCHAR,
    "kind"           VARCHAR,
    "indicator"      VARCHAR,
    "value"          DOUBLE,
    "unit"           VARCHAR,
    "published_date" DATE,
    "article_title"  VARCHAR,
    "context"        VARCHAR,
    "source_url"     VARCHAR,
    "row_hash"       VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_cpcaauto_nev IS 'CPCA 新能源乘用车零售/批发/渗透率 + 报告索引；kind=value 为数值，kind=report_index 只记报告存在（value=1 条），52 行 / 35 个业务键，用 row_hash 保留原文';
COMMENT ON COLUMN industry_cpcaauto_nev.value IS '数值；unit 为 万辆 / % / 条';

-- ---------------------------------------------------------------------------
-- 7. 财政部·财政收支
--    源：https://gks.mof.gov.cn/tongjishuju/...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_mof_fiscal (
    "period"             DATE,
    "period_raw"         VARCHAR,
    "indicator"          VARCHAR,
    "value"              DOUBLE,
    "unit"                VARCHAR,
    "published_date"     DATE,
    "published_date_raw" VARCHAR,
    "article_title"      VARCHAR,
    "source_url"         VARCHAR,
    "row_hash"           VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_mof_fiscal IS '财政部一般公共预算/政府性基金收支（累计）；抓取层 published_date 全为 2017年11月21日（解析错误），且存在 period=2017-11 与 2026 年文章混排，故 published_date 另由 source_url 中的 tYYYYMMDD 派生，原始文本保留在 published_date_raw';
COMMENT ON COLUMN industry_mof_fiscal.period IS '累计期（当月 1 日）；源 period 有解析错误（12 行 2017-11 与 2026 年文章冲突），不推断真实期，引用前须回原文核对，见 docs/industry-ingest-2026.md §4.3';
COMMENT ON COLUMN industry_mof_fiscal.value IS '金额，单位 亿元';

-- ---------------------------------------------------------------------------
-- 8. 国家能源局·全国电力统计数据
--    源：https://www.nea.gov.cn/...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_nea_power (
    "period"         DATE,
    "period_raw"     VARCHAR,
    "indicator"      VARCHAR,
    "value"          DOUBLE,
    "unit"           VARCHAR,
    "published_date" DATE,
    "article_title"  VARCHAR,
    "source_url"      VARCHAR,
    "row_hash"       VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_nea_power IS '国家能源局装机/用电量/利用小时；同一 期×指标 有当月与累计两个值共用同一指标名（如城乡居民生活用电量 1883 与 11132），且部分期为空，故用 row_hash 作主键';
COMMENT ON COLUMN industry_nea_power.value IS '数值；unit 为 亿千瓦 / 亿千瓦时 / 小时';
COMMENT ON COLUMN industry_nea_power.period IS '统计期（当月 1 日）；源 period 为空的行保留 NULL，不推断';

-- ---------------------------------------------------------------------------
-- 9. 中国人民银行·社会融资规模增量
--    源：http://www.pbc.gov.cn/diaochatongjisi/...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_pbc_shrzgm (
    "period"     DATE    NOT NULL,
    "indicator"  VARCHAR NOT NULL,
    "en_name"    VARCHAR,
    "value"      DOUBLE,
    "unit"       VARCHAR,
    "source_url"   VARCHAR,
    PRIMARY KEY ("period", "indicator")
);
COMMENT ON TABLE industry_pbc_shrzgm IS '社融增量分项（月度，110 行，业务键唯一）；存量口径未入库';
COMMENT ON COLUMN industry_pbc_shrzgm.value IS '增量，单位 亿元';

-- ---------------------------------------------------------------------------
-- 10. 国家统计局·数据发布（六组）
--     源：https://www.stats.gov.cn/sj/zxfb/...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_stats_gov_release (
    "period"        DATE,
    "period_raw"    VARCHAR,
    "group_name"    VARCHAR NOT NULL,
    "indicator"     VARCHAR,
    "row_label"     VARCHAR,
    "col_header"    VARCHAR,
    "value"         DOUBLE,
    "unit"          VARCHAR,
    "table_index"   INTEGER NOT NULL,
    "row_index"     INTEGER NOT NULL,
    "col_index"     INTEGER NOT NULL,
    "published_date" DATE,
    "source_url"    VARCHAR NOT NULL,
    PRIMARY KEY ("source_url", "group_name", "table_index", "row_index", "col_index")
);
COMMENT ON TABLE industry_stats_gov_release IS '统计局六组发布（CPI/PPI/PMI/工业增加值/固投/工业利润）的原表单元格；2468 行、业务键唯一。已知缺陷：利润表的表头列与数值列错位一列（row_index=2 的 1062066 应为营业收入亿元），本层原样保留、不猜值，见 docs/industry-ingest-2026.md §4';
COMMENT ON COLUMN industry_stats_gov_release.group_name IS '六组之一：居民消费价格 / 工业生产者出厂价格 / 制造业采购经理指数 / 规模以上工业增加值 / 固定资产投资 / 规模以上工业企业利润';
COMMENT ON COLUMN industry_stats_gov_release.value IS '单元格数值；单位随表头变化（% / 亿元 / 万吨 / 亿块 …），以 unit 与 col_header 为准';

-- ---------------------------------------------------------------------------
-- 11. Benchmark Mineral Intelligence 公开指数
--     源：https://www.benchmarkminerals.com/price-assessments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_benchmark_public_index (
    "snapshot_date" DATE    NOT NULL,
    "kind"          VARCHAR NOT NULL,
    "name"          VARCHAR NOT NULL,
    "value"         DOUBLE,
    "change_pct"    DOUBLE,
    "target_url"    VARCHAR,
    "row_hash"      VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_benchmark_public_index IS 'Benchmark 公开指数与简报索引；CSV 内指数块重复三遍（值相同），row_hash 去重后保留 1 份；snapshot_date 取自 manifest.fetched_at（源无日期列）';
COMMENT ON COLUMN industry_benchmark_public_index.value IS '指数点位（非美元/吨价格）';
COMMENT ON COLUMN industry_benchmark_public_index.change_pct IS '涨跌幅，单位 %';

-- ---------------------------------------------------------------------------
-- 12. UN Comtrade 双边贸易额
--     源：https://comtradeapi.un.org/public/v1/preview/C/A/HS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_comtrade_trade_values (
    "hs_code"      VARCHAR NOT NULL,
    "label"        VARCHAR,
    "reporter"     VARCHAR NOT NULL,
    "period_year"  INTEGER NOT NULL,
    "flow"         VARCHAR NOT NULL,
    "value_usd"    DOUBLE,
    "netweight_kg" DOUBLE,
    "status"       VARCHAR,
    PRIMARY KEY ("hs_code", "reporter", "period_year", "flow")
);
COMMENT ON TABLE industry_comtrade_trade_values IS 'Comtrade 年度出口额（5 个 HS 编码，4 个取到、1 个 HTTP 500 以 NULL 行保留状态）';
COMMENT ON COLUMN industry_comtrade_trade_values.value_usd IS '贸易额，单位 美元';
COMMENT ON COLUMN industry_comtrade_trade_values.netweight_kg IS '净重，单位 千克';

-- ---------------------------------------------------------------------------
-- 13. 交易所日行情（GFEX/SHFE 快照）
--     源：http://www.gfex.com.cn/... 与 https://www.shfe.com.cn/data/tradedata/...
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_exchange_daily_quotes (
    "exchange"   VARCHAR NOT NULL,
    "contract"   VARCHAR NOT NULL,
    "month"      VARCHAR NOT NULL,
    "trade_date" DATE    NOT NULL,
    "close"      DOUBLE,
    "settle"     DOUBLE,
    "volume"     DOUBLE,
    "oi"         DOUBLE,
    "status"     VARCHAR,
    PRIMARY KEY ("exchange", "contract", "month", "trade_date")
);
COMMENT ON TABLE industry_exchange_daily_quotes IS '交易所快照日行情（一次抓取一个交易日）';
COMMENT ON COLUMN industry_exchange_daily_quotes.close IS '收盘价，元/吨';
COMMENT ON COLUMN industry_exchange_daily_quotes.settle IS '结算价，元/吨';
COMMENT ON COLUMN industry_exchange_daily_quotes.volume IS '成交量，手';
COMMENT ON COLUMN industry_exchange_daily_quotes.oi IS '持仓量，手';

-- ---------------------------------------------------------------------------
-- 14. Our World in Data / IEA / USGS 年度序列
--     源：https://ourworldindata.org/grapher/<slug>.csv
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_ow_iea_usgs_timeseries (
    "slug"   VARCHAR NOT NULL,
    "entity" VARCHAR NOT NULL,
    "year"   INTEGER NOT NULL,
    "value"  DOUBLE,
    PRIMARY KEY ("slug", "entity", "year")
);
COMMENT ON TABLE industry_ow_iea_usgs_timeseries IS 'OWID 年度序列（电动车销量/渗透率、锂/钴产量、CO2、光伏累计装机）';
COMMENT ON COLUMN industry_ow_iea_usgs_timeseries.value IS '数值；单位随 slug：辆、%、吨、百万吨 CO2、GW';

-- ---------------------------------------------------------------------------
-- 15. 生意社（100ppi）现货/出厂挂牌价
--     源：https://www.100ppi.com/mprice/plist-1-<goods_id>-1.html
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_ppi_spot_prices (
    "goods_id"  VARCHAR NOT NULL,
    "goods"     VARCHAR,
    "spec"      VARCHAR,
    "market"    VARCHAR,
    "supplier"  VARCHAR,
    "as_of"     DATE,
    "price"     DOUBLE,
    "price_unit" VARCHAR,
    "price_raw" VARCHAR,
    "url"       VARCHAR,
    "row_hash"  VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_ppi_spot_prices IS '厂商挂牌价（非地区均价）；同一 goods/spec/supplier/date 有完全重复行（33 行 / 31 个键），row_hash 去重';
COMMENT ON COLUMN industry_ppi_spot_prices.price IS '解析后的价格数值（元），单位见 price_unit';
COMMENT ON COLUMN industry_ppi_spot_prices.price_unit IS '价格单位（源 unit 列原值）：吨 / 公斤 / 千克；跨单位换算需自行乘 1000';
COMMENT ON COLUMN industry_ppi_spot_prices.price_raw IS '原始价格文本，如 174000元/吨、190元/公斤';

-- ---------------------------------------------------------------------------
-- 16. SNE Research 公开新闻稿索引
--     源：https://www.sneresearch.com/en/insight/release/
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_sne_public_battery (
    "release_id"   VARCHAR NOT NULL,
    "title"        VARCHAR,
    "publish_date" DATE,
    "key_figure"   VARCHAR,
    "url"          VARCHAR,
    "status"       VARCHAR,
    PRIMARY KEY ("release_id")
);
COMMENT ON TABLE industry_sne_public_battery IS 'SNE 公开新闻稿标题与关键数字文本（未解析成结构化数值；status 记录 1 条抓取失败）';

-- ---------------------------------------------------------------------------
-- 17. TrendForce 现货价（DRAM / NAND Flash）
--     源：https://www.trendforce.cn/price/dram/dram_spot
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_trendforce_spot_price (
    "snapshot_date" DATE    NOT NULL,
    "series"        VARCHAR NOT NULL,
    "spec"          VARCHAR,
    "value_text"    VARCHAR,
    "value"         DOUBLE,
    "unit"          VARCHAR,
    "unit_kind"     VARCHAR,
    "row_hash"      VARCHAR NOT NULL,
    PRIMARY KEY ("row_hash")
);
COMMENT ON TABLE industry_trendforce_spot_price IS 'TrendForce 公开现货表；value 列混有价格与涨跌幅（▲ 1.85 %）、并夹带「项目/盘涨跌幅」表头行，故保留 value_text 原文，unit_kind=price/pct_change/header/unparsed';
COMMENT ON COLUMN industry_trendforce_spot_price.value IS '解析后的数值；unit_kind=price 时单位 USD，pct_change 时单位 %';
COMMENT ON COLUMN industry_trendforce_spot_price.snapshot_date IS '抓取日（源无日期列，取 manifest.fetched_at 的日期）';

-- ---------------------------------------------------------------------------
-- 装载审计（每次 ETL 一段，保留历史）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_load_audit (
    "run_id"       VARCHAR NOT NULL,
    "table_name"   VARCHAR NOT NULL,
    "raw_source"   VARCHAR,
    "raw_rows"     BIGINT,
    "staged_rows"  BIGINT,
    "loaded_rows"  BIGINT,
    "partitions"   BIGINT,
    "mode"         VARCHAR,
    "started_at"   TIMESTAMP,
    "finished_at"  TIMESTAMP,
    PRIMARY KEY ("run_id", "table_name")
);
COMMENT ON TABLE industry_load_audit IS '行业层装载审计：每次运行每表一段，行数与分区数可复核';

-- ---------------------------------------------------------------------------
-- 目录覆盖映射：data_catalog 指标名 -> 实存表 / 字段
--
-- 只读 data_catalog（405 条不改），映射由人工逐条核对：catalog_indicator 必须
-- 与 data_catalog 原文完全一致，否则视图落成未覆盖。completeness 区分
-- 「完整」与「部分」（部分 = 指标定义中的某个维度/品种/频率缺失，表里有
-- 可查数值）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_catalog_map (
    "catalog_industry"  VARCHAR NOT NULL,
    "catalog_indicator" VARCHAR NOT NULL,
    "table_name"        VARCHAR NOT NULL,
    "field_name"        VARCHAR NOT NULL,
    "completeness"      VARCHAR NOT NULL,
    "note"              VARCHAR,
    PRIMARY KEY ("catalog_industry", "catalog_indicator", "table_name", "field_name")
);
COMMENT ON TABLE industry_catalog_map IS 'data_catalog 指标名到 industry_* 表的人工映射；completeness ∈ 完整/部分';

DELETE FROM industry_catalog_map;
INSERT OR REPLACE INTO industry_catalog_map
SELECT * FROM (VALUES
  -- 宏观
  ('宏观','CPI 与 PPI 同比环比','industry_stats_gov_release','value','部分','有 CPI/PPI 同比；环比列未提取'),
  ('宏观','制造业 PMI 与新订单/生产分项','industry_stats_gov_release','value','完整','PMI 及 5 个分项列同表'),
  ('宏观','固定资产投资分项（制造业/基建/地产）','industry_stats_gov_release','value','部分','只有同比 %，无投资额亿元'),
  ('宏观','规模以上工业企业利润总额与利润率','industry_stats_gov_release','value','部分','有利润总额与利润率，但表头与数值错位一列'),
  ('宏观','财政支出与政府性基金收入','industry_mof_fiscal','value','完整','一般公共预算收支 + 政府性基金收支'),
  ('宏观','贷款加权平均利率与 LPR','industry_chinamoney_lpr','value','部分','只有 1Y/5Y+ LPR，无贷款加权平均利率'),
  ('宏观','社会融资规模存量与增量结构','industry_pbc_shrzgm','value','部分','只有增量分项，无存量同比'),
  ('宏观','全社会用电量与单位 GDP 电耗','industry_nea_power','value','部分','有用电量，无单位 GDP 电耗'),
  ('宏观','全球大宗商品价格指数','industry_benchmark_public_index','value','部分','只有电池金属指数点位，非美元/吨'),
  ('宏观','全球贸易量与双边贸易结构','industry_comtrade_trade_values','value_usd','部分','4 个 HS 编码、1 个年度、中国出口单向'),
  -- 储能与电力设备
  ('储能与电力设备','全国发电装机容量与结构','industry_nea_power','value','部分','只有总量/太阳能/风电，缺火电核电水电分项'),
  ('储能与电力设备','全社会用电量与分产业用电量','industry_nea_power','value','完整','分产业用电量 + 全社会用电量'),
  ('储能与电力设备','发电设备平均利用小时数','industry_nea_power','value','完整','累计利用小时'),
  ('储能与电力设备','新型储能累计装机规模（功率/能量）','industry_cnesa_storage','value','部分','已提取新型储能累计 144.7GW、新增投运 66.43GW/189.48GWh（WORK-577）；仍缺累计装机的能量口径（GWh）与新型储能占比精确值（原文仅“超过 2/3”）'),
  ('储能与电力设备','碳酸锂期货结算价、持仓与仓单','industry_gfex_lithium_carbonate','value','部分','有结算价/持仓/成交，仓单日报为 JS 页面未取'),
  ('储能与电力设备','电池级碳酸锂与氢氧化锂现货价','industry_ppi_spot_prices','price','部分','有电池级碳酸锂，无氢氧化锂'),
  ('储能与电力设备','全球电池原材料供需平衡','industry_ow_iea_usgs_timeseries','value','部分','只有锂/钴产量，无需求与平衡表'),
  -- 有色与锂
  ('有色与锂','碳酸锂期货结算价、持仓与仓单','industry_gfex_lithium_carbonate','value','部分','有结算价/持仓/成交，仓单未取'),
  ('有色与锂','碳酸锂现货价（电池级/工业级）','industry_ppi_spot_prices','price','部分','有电池级，工业级未单列'),
  ('有色与锂','铜/铝/铅/锌现货价与升贴水','industry_ppi_spot_prices','price','部分','有铜/铅/锌挂牌价，无铝与升贴水'),
  ('有色与锂','全球锂价与长期合约价','industry_benchmark_public_index','value','部分','只有指数点位，无美元/吨合约价'),
  ('有色与锂','锂盐与碳酸锂进出口量','industry_comtrade_trade_values','value_usd','部分','只有 HS 284180 一个年度出口额与净重'),
  ('有色与锂','碳酸锂产量与产能过剩率','industry_ow_iea_usgs_timeseries','value','部分','有全球锂产量，无产能与过剩率'),
  ('有色与锂','全球锂供需与产能过剩','industry_ow_iea_usgs_timeseries','value','部分','只有产量，无供需平衡'),
  ('有色与锂','有色上市公司产销量与库存披露','industry_cninfo_announcements','title','部分','只有公告索引与 PDF 链接，正文未解析'),
  ('有色与锂','有色金属行业营收、利润与利润率','industry_stats_gov_release','value','部分','有色金属冶炼和压延加工业分项；表头错位一列'),
  ('有色与锂','全球交易所库存与注册仓单','industry_gfex_lithium_carbonate','value','部分','只有持仓量，无仓单'),
  ('有色与锂','钴镍中间品与硫酸锂进口来源结构','industry_comtrade_trade_values','value_usd','部分','只有锂化合物一项，无钴镍与来源结构'),
  -- 新能源车
  ('新能源车','汽车与新能源汽车产销量','industry_caam_auto_output','value','完整','当月与累计、汽车/乘用车/商用车/新能源'),
  ('新能源车','新能源乘用车批发、零售与渗透率','industry_cpcaauto_nev','value','完整','零售/批发/渗透率月度'),
  ('新能源车','全球 EV 销量与新车渗透率','industry_ow_iea_usgs_timeseries','value','完整','electric-car-sales + share，年度分国家'),
  ('新能源车','全球 EV 销量与中国渗透率','industry_ow_iea_usgs_timeseries','value','完整','OWID 全球销量 + CPCA 中国渗透率'),
  ('新能源车','新能源汽车出口量与单车均价','industry_caam_auto_output','value','部分','有出口量，无单车均价'),
  ('新能源车','全球汽车产量与出口','industry_caam_auto_output','value','部分','只有中国口径'),
  ('新能源车','全球动力电池装机量与厂商份额','industry_sne_public_battery','key_figure','部分','只有新闻稿标题文本，无厂商份额结构化数值'),
  ('新能源车','动力电池装机量与厂商份额','industry_sne_public_battery','key_figure','部分','同上（月度条目）'),
  ('新能源车','碳酸锂/钴/镍/石墨价格（电池成本链）','industry_ppi_spot_prices','price','部分','有碳酸锂/铜/铅/锌挂牌价，无镍/石墨'),
  -- 半导体
  ('半导体','存储芯片现货价与合约价','industry_trendforce_spot_price','value','部分','只有 DRAM/NAND 现货，合约价需会员'),
  ('半导体','韩国存储芯片产量与合约价','industry_trendforce_spot_price','value','部分','只有现货价，无韩国产量'),
  ('半导体','集成电路产量（设计/制造/封测）','industry_stats_gov_release','value','部分','只有统计局口径总产量（亿块）'),
  ('半导体','全球电子元件贸易流与关税','industry_comtrade_trade_values','value_usd','部分','只有 4 个 HS 编码、无关税'),
  ('半导体','SNE Research 全球电池与半导体市场数据','industry_sne_public_battery','key_figure','部分','只有新闻稿索引'),
  ('半导体','半导体上市公司订单与合同负债（自建）','industry_cninfo_announcements','title','部分','只有公告索引'),
  -- 医药
  ('医药','医药制造业营业收入与利润总额','industry_stats_gov_release','value','部分','统计局利润表按行业分项，表头错位一列'),
  ('医药','医药工业增加值与出口交货值','industry_stats_gov_release','value','部分','有医药制造业增加值，出口交货值未单列'),
  ('医药','原料药与中间体出口量价','industry_comtrade_trade_values','value_usd','部分','只有 HS 3004 制剂出口，非原料药'),
  ('医药','全球药品产量与跨境供应','industry_comtrade_trade_values','value_usd','部分','只有中国出口、单年度')
) AS t(catalog_industry, catalog_indicator, table_name, field_name, completeness, note);

-- ---------------------------------------------------------------------------
-- 覆盖视图：目录 405 条逐条判定
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_industry_catalog_coverage AS
WITH mapped AS (
    SELECT catalog_industry, catalog_indicator,
           string_agg(DISTINCT table_name, ',' ORDER BY table_name)   AS tables,
           string_agg(DISTINCT table_name || '.' || field_name, ',') AS fields,
           min(completeness)                                           AS completeness_min
    FROM industry_catalog_map
    GROUP BY 1, 2
)
SELECT c."行业"                                   AS industry,
       c."区域"                                   AS region,
       c."缺口优先级"                             AS gap_priority,
       c."指标名"                                 AS indicator,
       c."频率"                                   AS frequency,
       c."单位"                                   AS catalog_unit,
       CASE WHEN m.catalog_indicator IS NULL THEN '未覆盖' ELSE '已覆盖' END AS coverage,
       CASE WHEN m.catalog_indicator IS NULL THEN NULL
            WHEN m.completeness_min = '完整' THEN '完整'
            ELSE '部分' END                       AS completeness,
       m.tables                                   AS covered_by_table,
       m.fields                                   AS covered_by_field,
       c."一手来源"                               AS primary_source
FROM data_catalog c
LEFT JOIN mapped m
       ON c."行业" = m.catalog_industry
      AND c."指标名" = m.catalog_indicator;

CREATE OR REPLACE VIEW v_industry_catalog_coverage_summary AS
SELECT industry,
       count(*)                                        AS catalog_rows,
       count(*) FILTER (WHERE coverage = '已覆盖')      AS covered,
       count(*) FILTER (WHERE completeness = '完整')    AS complete,
       count(*) FILTER (WHERE completeness = '部分')    AS partial,
       count(*) FILTER (WHERE coverage = '未覆盖')      AS not_covered
FROM v_industry_catalog_coverage
GROUP BY 1
ORDER BY 1;

-- ---------------------------------------------------------------------------
-- 装载变更留痕（WORK-585）
--
-- 为什么单独一张表：industry_load_audit 只记行数与耗时，回答不了「这次替换
-- 到底改了什么、删了什么」。复核要求逐行可读（原值 → 新值 + 改动原因 + 对应 URL），
-- 因此差异单独落表，audit 结构与 17 张观测表都不动。
--
-- 四要素（用户 2026-11-25 点名）：old_value（原始值）、new_value（新值）、
-- reason（改动原因）、source_doc（对应 URL）。source_doc 取被改动行自己的
-- source_url / url 列；没有 URL 列的表为 NULL。
--
-- 写入时机：load_source 在同一事务内「先写差异、后执行替换」；替换失败回滚时
-- 差异一起回滚，不会留下「说有变化但库没变」的记录。
--
-- 不建主键：一次变更一行，同一 run 同一表可以有多行；DuckDB 是列存、这张表只
-- 按 run_id / 时间窗扫最近若干行，不建二级索引（DuckDB 上建了也不会被用）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS industry_load_diff (
    "run_id"       VARCHAR NOT NULL,
    "source"       VARCHAR NOT NULL,
    "table_name"   VARCHAR NOT NULL,
    "period"       DATE,
    "indicator"    VARCHAR NOT NULL,
    "change_type"  VARCHAR NOT NULL,
    "column_name"  VARCHAR,
    "old_value"    VARCHAR,
    "new_value"    VARCHAR,
    "source_doc"   VARCHAR,
    "reason"       VARCHAR NOT NULL,
    "changed_at"   TIMESTAMP NOT NULL
);
-- 生产库在「首版 12 列之前」已有同表（WORK-585 首轮 9 次装载写入 75 行）。
-- ALTER 只补列不改数据；历史行按诚实口径回填「上线前写入」标记，不留 NULL 冒充有原因。
ALTER TABLE industry_load_diff ADD COLUMN IF NOT EXISTS "source_doc" VARCHAR;
ALTER TABLE industry_load_diff ADD COLUMN IF NOT EXISTS "reason" VARCHAR;
-- 只回填「12 列上线（2026-11-25 23:50）」之前写入的行：apply_schema 每次装载都跑，
-- 不加时点会把这个时点之后因 bug 产生的 NULL reason 也洗成「历史行」，既伪造来源、
-- 又让 C9 的 reason 非空判据在两次装载之间失效。
UPDATE industry_load_diff
SET reason = 'WORK-585 首版上线前写入，无原因字段（历史行）'
WHERE reason IS NULL
  AND changed_at < TIMESTAMP '2026-11-25 23:50:00';

COMMENT ON TABLE industry_load_diff IS '行业层装载逐行差异（WORK-585）：每次装载替换前记录「库内现状 vs 本次快照」的四要素（原值/新值/原因/URL）；0 差异的装载写一行 no_change 汇总';
COMMENT ON COLUMN industry_load_diff.run_id IS '与 industry_load_audit.run_id 同一次运行的 id（预测表一组带 F 后缀）';
COMMENT ON COLUMN industry_load_diff.source IS '源名（db/etl/industry.py 的 Source.name），便于按源复核';
COMMENT ON COLUMN industry_load_diff.table_name IS '目标表 industry_*';
COMMENT ON COLUMN industry_load_diff.period IS '分区日期（源第一分区列能解析成日期时）；非日期分区源为 NULL';
COMMENT ON COLUMN industry_load_diff.indicator IS '业务键文本 k=v|k=v：自然主键表用主键列，row_hash 表用 etl.BUSINESS_KEYS 渲染';
COMMENT ON COLUMN industry_load_diff.change_type IS 'value_changed（某列值变了）| row_added（新增行）| row_dropped（被删行）| no_change（本轮 0 差异汇总）';
COMMENT ON COLUMN industry_load_diff.column_name IS 'value_changed 时发生变化的列名；其余类型为 NULL';
COMMENT ON COLUMN industry_load_diff.old_value IS '变化前列值 / 被删行整行文本（k=v|k=v，超 4000 字符截断）；源侧为空记 ∅；row_added 与 no_change 为 NULL';
COMMENT ON COLUMN industry_load_diff.new_value IS '变化后列值 / 新增行整行文本；no_change 为汇总文本（staged/库内/差异计数）；row_dropped 为 NULL';
COMMENT ON COLUMN industry_load_diff.source_doc IS '被改动行自己的原文 URL（取 source_url / url / adjunct_url 列）；没有 URL 列的表为 NULL';
COMMENT ON COLUMN industry_load_diff.reason IS '改动原因（人读一句话）：装载模式 + 本次快照抓取时间 + 变更性质；不允许为 NULL';
COMMENT ON COLUMN industry_load_diff.changed_at IS '写入时刻（本地墙钟，与 industry_load_audit 同口径）';

-- ---------------------------------------------------------------------------
-- 复核视图：近 30 天的「值变化 / 行被删」
--
-- 只列这两类：新增行是常态（源在扩），值变化与行消失才需要人去看一眼。
-- 列里带 reason / source_doc：这是大屏「数据变更」区块的数据源，四要素与表内同源。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_industry_load_changes_recent AS
SELECT run_id,
       source,
       table_name,
       period,
       indicator,
       change_type,
       column_name,
       old_value,
       new_value,
       source_doc,
       reason,
       changed_at
FROM industry_load_diff
WHERE change_type IN ('value_changed', 'row_dropped')
  AND changed_at >= current_localtimestamp() - INTERVAL 30 DAY
ORDER BY changed_at DESC, table_name, period, indicator;
