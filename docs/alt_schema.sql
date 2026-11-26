-- =============================================================================
-- WORK-619 · 自建 Top-10 的入库 DDL 建议（只新建 alt_* 表，不改现有任何表）
--
-- 执行方式（在 ETL 单进程里跑，不允许并发写库）：
--   duckdb /srv/share/quantdb/db/quantdb.duckdb < docs/alt_schema.sql
-- 已验证：在临时库上整份执行通过（见 docs/selfbuild_report.html §6）。本单**不写生产库**。
--
-- 三件套约定的统一形状：
--   1) 主键 = 脚本的主键（下方每表单独写明）；
--   2) source_url 逐行留痕，fetched_at 记入库时刻（UTC+8 本地时间带偏移）；
--   3) 数值列一律 DOUBLE（原始字符串由 raw 层 CSV 保留，可回溯改精度）。
--
-- 增量口径（每表通用三条，具体水位键见各表注释）：
--   * 水位键：每张表自有业务时间列（snapshot_date / trade_date / time / year / publish_date /
--     edition_date）。ETL 取 max(水位键) 作为起点。
--   * 回溯窗口：按源站修订特性给固定回看天数；脚本支持 --start/--end，回看时把两端口径对齐即可。
--   * 去重键：INSERT ... ON CONFLICT (主键) DO UPDATE，保证重放幂等（脚本本身也按主键合并 CSV）。
-- =============================================================================

-- 0) 入库水位登记：一行 = 一个数据集的一次成功装载
CREATE TABLE IF NOT EXISTS alt_ingest_state (
    dataset        VARCHAR PRIMARY KEY,   -- 与 raw 目录同名，如 alt_shfe_gfex_quotes
    watermark_key  VARCHAR NOT NULL,      -- 业务时间列名
    watermark      VARCHAR,               -- 已入库的最大业务时间（文本，按各源格式）
    backfill_days  INTEGER NOT NULL,      -- 每次增量回看天数
    last_rows      INTEGER,               -- 上次装载行数
    last_sha256    VARCHAR,               -- 上次 raw CSV 的 sha256（与 manifest 对齐才算成功）
    loaded_at      TIMESTAMP
);

-- 1) TrendForce 现货价（日快照，源站无历史接口，只能日更累积）
--    水位键 snapshot_date ｜ 回溯 0 天（快照型：重放同一日即覆盖同键）｜ 去重键 (snapshot_date, market, item)
CREATE TABLE IF NOT EXISTS alt_trendforce_spot_price (
    snapshot_date DATE    NOT NULL,
    market        VARCHAR NOT NULL,       -- DRAM / NAND Flash
    item          VARCHAR NOT NULL,       -- 规格，如 "DDR5 16Gb (2Gx8) 4800/5600"
    high          DOUBLE,
    low           DOUBLE,
    avg_usd       DOUBLE,                 -- 盘平均，USD
    change_pct    DOUBLE,
    unit          VARCHAR,                -- 恒为 USD（现货价）
    source_url    VARCHAR,
    fetched_at    TIMESTAMP,
    PRIMARY KEY (snapshot_date, market, item)
);

-- 2) Benchmark 公开价格评估指数（日快照）
--    水位键 snapshot_date ｜ 回溯 0 ｜ 去重键 (snapshot_date, index_name)
--    口径：index 为相对指数点，**不是** USD/吨；snapshot_date = 抓取日（源站未标评估日）
CREATE TABLE IF NOT EXISTS alt_benchmark_public_index (
    snapshot_date DATE    NOT NULL,
    index_name    VARCHAR NOT NULL,
    index_value   DOUBLE,
    change_pct    DOUBLE,
    unit          VARCHAR,
    source_url    VARCHAR,
    fetched_at    TIMESTAMP,
    PRIMARY KEY (snapshot_date, index_name)
);

-- 3) SNE Research 公开新闻稿（一条稿件一行，取头条绝对值）
--    水位键 publish_date ｜ 回溯 30 天（稿件可能被修订/补数）｜ 去重键 release_id
CREATE TABLE IF NOT EXISTS alt_sne_research_releases (
    release_id   VARCHAR PRIMARY KEY,     -- 源站数字 id
    publish_date DATE,
    title        VARCHAR,
    metric       VARCHAR,                 -- ESS shipments / EV battery installation / Material consumption / EV sales / other
    value        DOUBLE,
    unit         VARCHAR,                 -- GWh / Ktons / Million / Units
    yoy_pct      DOUBLE,
    period       VARCHAR,                 -- 如 "Jan-Sep 2026"、"1-3Q26"
    evidence     VARCHAR,                 -- 原句，复核用
    source_url   VARCHAR,
    fetched_at   TIMESTAMP
);

-- 4) 交易所日行情（SHFE + GFEX）
--    水位键 trade_date ｜ 回溯 5 个交易日（交易所会补发/修订）｜ 去重键 (trade_date, exchange, contract)
--    口径：close/settle 为交易所公布值；open_interest 为持仓量，**不是**注册仓单
CREATE TABLE IF NOT EXISTS alt_shfe_gfex_quotes (
    trade_date    DATE    NOT NULL,
    exchange      VARCHAR NOT NULL,       -- SHFE / GFEX
    product       VARCHAR,                -- CU_F / Lithium Carbonate …
    contract      VARCHAR NOT NULL,       -- 如 CU_F2612
    close         DOUBLE,
    settle        DOUBLE,
    volume        DOUBLE,
    open_interest DOUBLE,
    source_url    VARCHAR,
    fetched_at    TIMESTAMP,
    PRIMARY KEY (trade_date, exchange, contract)
);

-- 5) Eurostat 贸易依赖度 + C24 增加值
--    水位键 time（年度）｜ 回溯 365 天（Eurostat 常回改上年值）｜ 去重键 (dataset, geo, partner, flow, sitc, nace_r2, time)
--    口径：sitc= SITC 大类（SITC2_4/SITC5/SITC6/SITC7），不是 HS 8486/253090 单品类
CREATE TABLE IF NOT EXISTS alt_eurostat_trade (
    dataset   VARCHAR NOT NULL,           -- ext_lt_intratrd / nama_10_a64
    geo       VARCHAR NOT NULL,
    partner   VARCHAR NOT NULL,
    flow      VARCHAR NOT NULL,           -- indic_et / na_item
    sitc      VARCHAR NOT NULL,
    nace_r2   VARCHAR NOT NULL,
    unit      VARCHAR,
    time      VARCHAR NOT NULL,           -- 年度
    value     DOUBLE,
    source_url VARCHAR,
    fetched_at TIMESTAMP,
    PRIMARY KEY (dataset, geo, partner, flow, sitc, nace_r2, time)
);

-- 6) OWID EV 销量与渗透率
--    水位键 year ｜ 回溯 365 天（IEA GEO 版本更新会回改历史）｜ 去重键 (indicator, entity, year)
CREATE TABLE IF NOT EXISTS alt_owid_ev_sales (
    indicator VARCHAR NOT NULL,           -- ev_sales_units / ev_sales_share
    entity    VARCHAR NOT NULL,
    code      VARCHAR,
    year      INTEGER NOT NULL,
    value     DOUBLE,
    unit      VARCHAR,                    -- vehicles / %
    source_url VARCHAR,
    fetched_at TIMESTAMP,
    PRIMARY KEY (indicator, entity, year)
);

-- 7) OWID 能源消费（分品种）与 CO2
--    水位键 year ｜ 回溯 365 天 ｜ 去重键 (indicator, entity, year, series)
CREATE TABLE IF NOT EXISTS alt_owid_energy_co2 (
    indicator VARCHAR NOT NULL,           -- co2_annual / primary_energy_by_source
    entity    VARCHAR NOT NULL,
    code      VARCHAR,
    year      INTEGER NOT NULL,
    series    VARCHAR NOT NULL,           -- 能源品种列名（宽表转长表后）
    value     DOUBLE,
    unit      VARCHAR,                    -- tonnes / TWh
    source_url VARCHAR,
    fetched_at TIMESTAMP,
    PRIMARY KEY (indicator, entity, year, series)
);

-- 8) World Bank WDI
--    水位键 year ｜ 回溯 365 天（WDI 每次更新回改近两年）｜ 去重键 (indicator, country, year)
CREATE TABLE IF NOT EXISTS alt_worldbank_wdi (
    indicator      VARCHAR NOT NULL,      -- real_gdp_constant_2015_usd / real_gdp_growth / trade_pct_gdp
    indicator_name VARCHAR,               -- 源站指标码 NY.GDP.MKTP.KD …
    country        VARCHAR NOT NULL,      -- WLD / CHN / USA / EMU
    country_name   VARCHAR,
    year           INTEGER NOT NULL,
    value          DOUBLE,
    unit           VARCHAR,
    source_url     VARCHAR,
    fetched_at     TIMESTAMP,
    PRIMARY KEY (indicator, country, year)
);

-- 9) IRENA 装机容量 / 发电量
--    水位键 year ｜ 回溯 365 天（IRENA 半年版会重述去年）｜ 去重键 (region, technology, data_type, year)
--    口径：technology 是 IRENA 分类码（"0".."10"），储能只有抽水蓄能
CREATE TABLE IF NOT EXISTS alt_irena_re_capacity (
    region          VARCHAR NOT NULL,
    region_name     VARCHAR,
    technology      VARCHAR NOT NULL,
    technology_name VARCHAR,
    data_type       VARCHAR NOT NULL,     -- Electrical Installed Capacity (MW) / Electricity Generation (GWh)
    year            INTEGER NOT NULL,
    value           DOUBLE,
    unit            VARCHAR,              -- MW / GWh
    source_url      VARCHAR,
    fetched_at      TIMESTAMP,
    PRIMARY KEY (region, technology, data_type, year)
);

-- 10) EMMES 欧洲储能公开版
--     水位键 edition_date ｜ 回溯 0（版本型数据，一版一行）｜ 去重键 (edition, metric)
CREATE TABLE IF NOT EXISTS alt_emmes_storage (
    edition      VARCHAR NOT NULL,        -- "EMMES 10.0 - June 2026"
    edition_date DATE,
    metric       VARCHAR NOT NULL,        -- additions_gw / additions_gwh / installed_gw / growth_to_2030_pct
    value        DOUBLE,
    unit         VARCHAR,
    evidence     VARCHAR,
    source_url   VARCHAR,
    fetched_at   TIMESTAMP,
    PRIMARY KEY (edition, metric)
);

-- -----------------------------------------------------------------------------
-- 装载模板（每个数据集一段，ETL 侧照抄；raw CSV 列名与表列名一一对应）
-- -----------------------------------------------------------------------------
-- INSERT INTO alt_shfe_gfex_quotes
--   SELECT trade_date, exchange, product, contract,
--          try_cast(close AS DOUBLE), try_cast(settle AS DOUBLE),
--          try_cast(volume AS DOUBLE), try_cast(open_interest AS DOUBLE),
--          source_url, now() AT TIME ZONE 'Asia/Shanghai'
--   FROM read_csv_auto('/srv/share/quantdb/raw/industry_alt/shfe_gfex_quotes/data.csv')
--   WHERE trade_date >= (SELECT coalesce(max(watermark), '1970-01-01') FROM alt_ingest_state
--                        WHERE dataset = 'alt_shfe_gfex_quotes')
--   ON CONFLICT (trade_date, exchange, contract) DO UPDATE SET
--          close = excluded.close, settle = excluded.settle, volume = excluded.volume,
--          open_interest = excluded.open_interest, source_url = excluded.source_url,
--          fetched_at = excluded.fetched_at;
--
-- INSERT OR REPLACE INTO alt_ingest_state VALUES (...);  -- 装载成功后写水位 + sha256
-- -----------------------------------------------------------------------------

-- 自检（执行后应为 11 张表：10 个数据集 + 1 张水位登记）
SELECT table_name FROM information_schema.tables
WHERE table_name LIKE 'alt\_%' ESCAPE '\' ORDER BY table_name;
