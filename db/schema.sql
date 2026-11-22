-- =====================================================================
-- quantdb L1 schema (DuckDB)
-- 用法:
--   duckdb /srv/share/quantdb/db/quantdb.duckdb < quant-lab/db/schema.sql
-- 说明:
--   1) 所有日期列为 DATE，源端 YYYYMMDD 在 ETL 入口转换。
--   2) 金额单位以列 COMMENT 为准；来源口径见 data_contracts/field_dictionary.md。
--   3) 复权不落死：daily 只存原始 close，复权在查询期用 adj_factor 计算。
--   4) 财务表保留 ann_date / f_ann_date，回测按公告日入样。
--   5) raw_json 保留源端整行，避免未建模字段在同步时丢失。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 交易日历：唯一日期源。cal_date 含非交易日，is_open=1 才是交易日。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_cal (
    exchange      VARCHAR NOT NULL,
    cal_date      DATE    NOT NULL,
    is_open       INTEGER NOT NULL,
    pretrade_date DATE,
    PRIMARY KEY (exchange, cal_date)
);
COMMENT ON TABLE trade_cal IS '交易日历；分区与行数勾稽的唯一日期源';
COMMENT ON COLUMN trade_cal.exchange IS '交易所代码，如 SSE/SZSE/CFFEX';
COMMENT ON COLUMN trade_cal.cal_date IS '日历日期（含非交易日）';
COMMENT ON COLUMN trade_cal.is_open IS '是否交易日：1=是，0=否';
COMMENT ON COLUMN trade_cal.pretrade_date IS '上一交易日';

-- ---------------------------------------------------------------------
-- 股票基础信息：保留 L/D/P 三种状态，退市股不删。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code        VARCHAR NOT NULL,
    symbol         VARCHAR,
    name           VARCHAR,
    area           VARCHAR,
    industry       VARCHAR,
    fullname       VARCHAR,
    enname         VARCHAR,
    cnspell        VARCHAR,
    market         VARCHAR,
    exchange       VARCHAR,
    curr_type      VARCHAR,
    list_status    VARCHAR,
    list_date      DATE,
    delist_date    DATE,
    is_hs          VARCHAR,
    act_name       VARCHAR,
    act_ent_type   VARCHAR,
    raw_json       VARCHAR,
    PRIMARY KEY (ts_code)
);
COMMENT ON TABLE stock_basic IS '股票基础信息；list_status 取 L上市/D退市/P暂停上市，不得只留在市';
COMMENT ON COLUMN stock_basic.ts_code IS 'TS 代码，如 600000.SH';
COMMENT ON COLUMN stock_basic.list_status IS '上市状态：L上市、D退市、P暂停上市';
COMMENT ON COLUMN stock_basic.list_date IS '上市日期';
COMMENT ON COLUMN stock_basic.delist_date IS '退市日期';

-- ---------------------------------------------------------------------
-- 日线行情：原始价，不复权。vol 手、amount 千元。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily (
    ts_code    VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    pre_close  DOUBLE,
    change     DOUBLE,
    pct_chg    DOUBLE,
    vol        DOUBLE,
    amount     DOUBLE,
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE daily IS 'A 股日线原始行情（不复权）；复权在查询期用 adj_factor 计算';
COMMENT ON COLUMN daily.ts_code IS 'TS 代码，如 600000.SH';
COMMENT ON COLUMN daily.trade_date IS '交易日';
COMMENT ON COLUMN daily.open IS '开盘价，元';
COMMENT ON COLUMN daily.high IS '最高价，元';
COMMENT ON COLUMN daily.low IS '最低价，元';
COMMENT ON COLUMN daily.close IS '收盘价（原始），元';
COMMENT ON COLUMN daily.pre_close IS '昨收价，元';
COMMENT ON COLUMN daily.change IS '涨跌额，元';
COMMENT ON COLUMN daily.pct_chg IS '涨跌幅，%';
COMMENT ON COLUMN daily.vol IS '成交量，手（1 手 = 100 股）';
COMMENT ON COLUMN daily.amount IS '成交额，千元';

-- ---------------------------------------------------------------------
-- 复权因子：纯乘法因子，只与 daily.close 组合使用。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adj_factor (
    ts_code    VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    adj_factor DOUBLE,
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE adj_factor IS '复权因子；前复权价 = close * adj_factor / 基准日 adj_factor';
COMMENT ON COLUMN adj_factor.adj_factor IS '复权因子，无量纲';

-- ---------------------------------------------------------------------
-- 每日指标：估值、股本、市值。total_mv 万元。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_basic (
    ts_code         VARCHAR NOT NULL,
    trade_date      DATE    NOT NULL,
    close           DOUBLE,
    turnover_rate   DOUBLE,
    turnover_rate_f DOUBLE,
    volume_ratio    DOUBLE,
    pe              DOUBLE,
    pe_ttm          DOUBLE,
    pb              DOUBLE,
    ps              DOUBLE,
    ps_ttm          DOUBLE,
    dv_ratio        DOUBLE,
    dv_ttm          DOUBLE,
    total_share     DOUBLE,
    float_share     DOUBLE,
    free_share      DOUBLE,
    total_mv        DOUBLE,
    circ_mv         DOUBLE,
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE daily_basic IS '每日指标：估值、股本、市值';
COMMENT ON COLUMN daily_basic.close IS '当日收盘价，元';
COMMENT ON COLUMN daily_basic.turnover_rate IS '换手率，%';
COMMENT ON COLUMN daily_basic.turnover_rate_f IS '自由流通换手率，%';
COMMENT ON COLUMN daily_basic.volume_ratio IS '量比，倍';
COMMENT ON COLUMN daily_basic.pe IS '市盈率，倍';
COMMENT ON COLUMN daily_basic.pe_ttm IS '市盈率 TTM，倍';
COMMENT ON COLUMN daily_basic.pb IS '市净率，倍';
COMMENT ON COLUMN daily_basic.ps IS '市销率，倍';
COMMENT ON COLUMN daily_basic.ps_ttm IS '市销率 TTM，倍';
COMMENT ON COLUMN daily_basic.dv_ratio IS '股息率，%';
COMMENT ON COLUMN daily_basic.dv_ttm IS '股息率 TTM，%';
COMMENT ON COLUMN daily_basic.total_share IS '总股本，万股';
COMMENT ON COLUMN daily_basic.float_share IS '流通股本，万股';
COMMENT ON COLUMN daily_basic.free_share IS '自由流通股本，万股';
COMMENT ON COLUMN daily_basic.total_mv IS '总市值，万元';
COMMENT ON COLUMN daily_basic.circ_mv IS '流通市值，万元';

-- ---------------------------------------------------------------------
-- 涨跌停价格：up_limit/down_limit 元。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stk_limit (
    trade_date DATE    NOT NULL,
    ts_code    VARCHAR NOT NULL,
    up_limit   DOUBLE,
    down_limit DOUBLE,
    PRIMARY KEY (trade_date, ts_code)
);
COMMENT ON TABLE stk_limit IS '每日涨跌停价格';
COMMENT ON COLUMN stk_limit.up_limit IS '涨停价，元';
COMMENT ON COLUMN stk_limit.down_limit IS '跌停价，元';

-- ---------------------------------------------------------------------
-- 停牌信息：停牌日保留记录，不删行。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suspend_d (
    ts_code        VARCHAR NOT NULL,
    trade_date     DATE    NOT NULL,
    suspend_timing VARCHAR,
    suspend_type   VARCHAR,
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE suspend_d IS '每日停牌信息；停牌日保留，不删除行情行';
COMMENT ON COLUMN suspend_d.suspend_timing IS '停牌时间段，如 09:30-15:00';
COMMENT ON COLUMN suspend_d.suspend_type IS '停牌类型：S 停牌、R 复牌';

-- ---------------------------------------------------------------------
-- 指数日线：指数点位不复权。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS index_daily (
    ts_code    VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    close      DOUBLE,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    pre_close  DOUBLE,
    change     DOUBLE,
    pct_chg    DOUBLE,
    vol        DOUBLE,
    amount     DOUBLE,
    PRIMARY KEY (ts_code, trade_date)
);
COMMENT ON TABLE index_daily IS '指数日线；点位为编制定值，不做除权';
COMMENT ON COLUMN index_daily.close IS '收盘点位，点';
COMMENT ON COLUMN index_daily.open IS '开盘点位，点';
COMMENT ON COLUMN index_daily.high IS '最高点位，点';
COMMENT ON COLUMN index_daily.low IS '最低点位，点';
COMMENT ON COLUMN index_daily.pct_chg IS '涨跌幅，%';
COMMENT ON COLUMN index_daily.vol IS '成交量，手';
COMMENT ON COLUMN index_daily.amount IS '成交额，千元';

-- ---------------------------------------------------------------------
-- 指数成分与权重：weight 百分比。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS index_weight (
    index_code VARCHAR NOT NULL,
    con_code   VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    weight     DOUBLE,
    PRIMARY KEY (index_code, con_code, trade_date)
);
COMMENT ON TABLE index_weight IS '指数成分权重；生效日按 trade_date 使用，禁止提前使用';
COMMENT ON COLUMN index_weight.weight IS '成分权重，%';

-- ---------------------------------------------------------------------
-- 财务：利润表。PIT 以 ann_date / f_ann_date 为准。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS income (
    ts_code          VARCHAR NOT NULL,
    ann_date         DATE    NOT NULL,
    f_ann_date       DATE,
    end_date         DATE,
    report_type      VARCHAR,
    comp_type        VARCHAR,
    end_type         VARCHAR,
    basic_eps        DOUBLE,
    diluted_eps      DOUBLE,
    total_revenue    DOUBLE,
    revenue          DOUBLE,
    operate_profit   DOUBLE,
    non_oper_income  DOUBLE,
    non_oper_exp     DOUBLE,
    total_profit     DOUBLE,
    income_tax       DOUBLE,
    n_income         DOUBLE,
    n_income_attr_p  DOUBLE,
    minority_gain    DOUBLE,
    ebit             DOUBLE,
    ebitda           DOUBLE,
    rd_exp           DOUBLE,
    update_flag      VARCHAR,
    raw_json         VARCHAR,
    PRIMARY KEY (ts_code, end_date, report_type, ann_date)
);
COMMENT ON TABLE income IS '利润表；PIT 按 ann_date 入样，end_date 仅作报告期分区';
COMMENT ON COLUMN income.ann_date IS '公告日期（PIT 列）';
COMMENT ON COLUMN income.f_ann_date IS '实际公告日期（PIT 列）';
COMMENT ON COLUMN income.end_date IS '报告期';
COMMENT ON COLUMN income.basic_eps IS '基本每股收益，元/股';
COMMENT ON COLUMN income.diluted_eps IS '稀释每股收益，元/股';
COMMENT ON COLUMN income.total_revenue IS '营业总收入，元';
COMMENT ON COLUMN income.revenue IS '营业收入，元';
COMMENT ON COLUMN income.operate_profit IS '营业利润，元';
COMMENT ON COLUMN income.total_profit IS '利润总额，元';
COMMENT ON COLUMN income.n_income IS '净利润，元';
COMMENT ON COLUMN income.n_income_attr_p IS '归母净利润，元';
COMMENT ON COLUMN income.ebit IS '息税前利润，元';
COMMENT ON COLUMN income.ebitda IS '息税折旧摊销前利润，元';
COMMENT ON COLUMN income.raw_json IS '源端整行 JSON，保留未建模字段';

-- ---------------------------------------------------------------------
-- 财务：资产负债表。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS balancesheet (
    ts_code                  VARCHAR NOT NULL,
    ann_date                 DATE    NOT NULL,
    f_ann_date               DATE,
    end_date                 DATE,
    report_type              VARCHAR,
    comp_type                VARCHAR,
    end_type                 VARCHAR,
    total_share              DOUBLE,
    money_cap                DOUBLE,
    accounts_receiv          DOUBLE,
    inventories              DOUBLE,
    total_cur_assets         DOUBLE,
    fix_assets               DOUBLE,
    cip                      DOUBLE,
    intan_assets             DOUBLE,
    goodwill                 DOUBLE,
    total_nca                DOUBLE,
    total_assets             DOUBLE,
    st_borr                  DOUBLE,
    lt_borr                  DOUBLE,
    accounts_payable         DOUBLE,
    total_cur_liab           DOUBLE,
    total_ncl                DOUBLE,
    total_liab               DOUBLE,
    total_hldr_eqy_exc_min_int DOUBLE,
    total_hldr_eqy_inc_min_int DOUBLE,
    total_liab_hldr_eqy      DOUBLE,
    update_flag              VARCHAR,
    raw_json                 VARCHAR,
    PRIMARY KEY (ts_code, end_date, report_type, ann_date)
);
COMMENT ON TABLE balancesheet IS '资产负债表；PIT 按 ann_date 入样';
COMMENT ON COLUMN balancesheet.ann_date IS '公告日期（PIT 列）';
COMMENT ON COLUMN balancesheet.f_ann_date IS '实际公告日期（PIT 列）';
COMMENT ON COLUMN balancesheet.end_date IS '报告期';
COMMENT ON COLUMN balancesheet.total_share IS '期末总股本，万股';
COMMENT ON COLUMN balancesheet.money_cap IS '货币资金，元';
COMMENT ON COLUMN balancesheet.accounts_receiv IS '应收账款，元';
COMMENT ON COLUMN balancesheet.inventories IS '存货，元';
COMMENT ON COLUMN balancesheet.total_cur_assets IS '流动资产合计，元';
COMMENT ON COLUMN balancesheet.fix_assets IS '固定资产，元';
COMMENT ON COLUMN balancesheet.goodwill IS '商誉，元';
COMMENT ON COLUMN balancesheet.total_assets IS '资产总计，元';
COMMENT ON COLUMN balancesheet.total_cur_liab IS '流动负债合计，元';
COMMENT ON COLUMN balancesheet.total_liab IS '负债合计，元';
COMMENT ON COLUMN balancesheet.total_hldr_eqy_exc_min_int IS '归母股东权益合计，元';
COMMENT ON COLUMN balancesheet.raw_json IS '源端整行 JSON，保留未建模字段';

-- ---------------------------------------------------------------------
-- 财务：现金流量表。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cashflow (
    ts_code             VARCHAR NOT NULL,
    ann_date            DATE    NOT NULL,
    f_ann_date          DATE,
    end_date            DATE,
    report_type         VARCHAR,
    comp_type           VARCHAR,
    end_type            VARCHAR,
    net_profit          DOUBLE,
    c_fr_sale_sg        DOUBLE,
    c_inf_fr_operate_a  DOUBLE,
    c_outf_fr_operate_a DOUBLE,
    n_cashflow_act      DOUBLE,
    n_cashflow_inv_act  DOUBLE,
    n_cashflow_fin_act  DOUBLE,
    c_fr_invest         DOUBLE,
    c_paid_invest       DOUBLE,
    c_fr_fin_act        DOUBLE,
    c_outf_fr_fin_act   DOUBLE,
    update_flag         VARCHAR,
    raw_json            VARCHAR,
    PRIMARY KEY (ts_code, end_date, report_type, ann_date)
);
COMMENT ON TABLE cashflow IS '现金流量表；PIT 按 ann_date 入样';
COMMENT ON COLUMN cashflow.ann_date IS '公告日期（PIT 列）';
COMMENT ON COLUMN cashflow.f_ann_date IS '实际公告日期（PIT 列）';
COMMENT ON COLUMN cashflow.end_date IS '报告期';
COMMENT ON COLUMN cashflow.net_profit IS '净利润，元';
COMMENT ON COLUMN cashflow.c_fr_sale_sg IS '销售商品、提供劳务收到的现金，元';
COMMENT ON COLUMN cashflow.n_cashflow_act IS '经营活动产生的现金流量净额，元';
COMMENT ON COLUMN cashflow.n_cashflow_inv_act IS '投资活动产生的现金流量净额，元';
COMMENT ON COLUMN cashflow.n_cashflow_fin_act IS '筹资活动产生的现金流量净额，元';
COMMENT ON COLUMN cashflow.raw_json IS '源端整行 JSON，保留未建模字段';

-- ---------------------------------------------------------------------
-- 财务指标：ROE/毛利率/偿债等。财务指标本身不带单位，比率列统一 %。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fina_indicator (
    ts_code              VARCHAR NOT NULL,
    ann_date             DATE    NOT NULL,
    f_ann_date           DATE,
    end_date             DATE,
    eps                  DOUBLE,
    dt_eps               DOUBLE,
    total_revenue_ps     DOUBLE,
    revenue_ps           DOUBLE,
    capital_rese_ps      DOUBLE,
    surplus_rese_ps      DOUBLE,
    undist_profit_ps     DOUBLE,
    profit_dedt          DOUBLE,
    gross_margin         DOUBLE,
    current_ratio        DOUBLE,
    quick_ratio          DOUBLE,
    cash_ratio           DOUBLE,
    inventory_turnover   DOUBLE,
    receivable_turnover  DOUBLE,
    payable_turnover     DOUBLE,
    asset_turnover       DOUBLE,
    roe                  DOUBLE,
    roe_dt               DOUBLE,
    roa                  DOUBLE,
    npta                 DOUBLE,
    roic                 DOUBLE,
    grossprofit_margin   DOUBLE,
    netprofit_margin     DOUBLE,
    ocf_to_or            DOUBLE,
    ocf_to_opincome      DOUBLE,
    debt_to_assets       DOUBLE,
    assets_to_eqt        DOUBLE,
    dupont_roe           DOUBLE,
    raw_json             VARCHAR,
    PRIMARY KEY (ts_code, end_date, ann_date)
);
COMMENT ON TABLE fina_indicator IS '财务指标；PIT 按 ann_date 入样';
COMMENT ON COLUMN fina_indicator.ann_date IS '公告日期（PIT 列）';
COMMENT ON COLUMN fina_indicator.f_ann_date IS '实际公告日期（PIT 列）';
COMMENT ON COLUMN fina_indicator.end_date IS '报告期';
COMMENT ON COLUMN fina_indicator.eps IS '基本每股收益，元/股';
COMMENT ON COLUMN fina_indicator.dt_eps IS '稀释每股收益，元/股';
COMMENT ON COLUMN fina_indicator.total_revenue_ps IS '每股营业总收入，元/股';
COMMENT ON COLUMN fina_indicator.revenue_ps IS '每股营业收入，元/股';
COMMENT ON COLUMN fina_indicator.profit_dedt IS '扣非净利润，元';
COMMENT ON COLUMN fina_indicator.gross_margin IS '毛利，元';
COMMENT ON COLUMN fina_indicator.current_ratio IS '流动比率，倍';
COMMENT ON COLUMN fina_indicator.quick_ratio IS '速动比率，倍';
COMMENT ON COLUMN fina_indicator.cash_ratio IS '现金比率，倍';
COMMENT ON COLUMN fina_indicator.inventory_turnover IS '存货周转率，次';
COMMENT ON COLUMN fina_indicator.receivable_turnover IS '应收账款周转率，次';
COMMENT ON COLUMN fina_indicator.asset_turnover IS '总资产周转率，次';
COMMENT ON COLUMN fina_indicator.roe IS '净资产收益率，%';
COMMENT ON COLUMN fina_indicator.roe_dt IS '扣非净资产收益率，%';
COMMENT ON COLUMN fina_indicator.roa IS '总资产收益率，%';
COMMENT ON COLUMN fina_indicator.npta IS '净利润/总资产，%';
COMMENT ON COLUMN fina_indicator.roic IS '投入资本回报率，%';
COMMENT ON COLUMN fina_indicator.grossprofit_margin IS '毛利率，%';
COMMENT ON COLUMN fina_indicator.netprofit_margin IS '净利率，%';
COMMENT ON COLUMN fina_indicator.debt_to_assets IS '资产负债率，%';
COMMENT ON COLUMN fina_indicator.raw_json IS '源端整行 JSON，保留未建模字段';

-- ---------------------------------------------------------------------
-- 分红送转：金额/比例口径按列注释。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dividend (
    ts_code       VARCHAR NOT NULL,
    end_date      DATE    NOT NULL,
    ann_date      DATE    NOT NULL,
    div_proc      VARCHAR NOT NULL,
    stk_div       DOUBLE,
    stk_bo_rate   DOUBLE,
    stk_co_rate   DOUBLE,
    cash_div      DOUBLE,
    cash_div_tax  DOUBLE,
    record_date   DATE,
    ex_date       DATE,
    pay_date      DATE,
    div_listdate  DATE,
    imp_ann_date  DATE,
    base_date     DATE,
    base_share    DOUBLE,
    raw_json      VARCHAR,
    PRIMARY KEY (ts_code, end_date, div_proc, ann_date)
);
COMMENT ON TABLE dividend IS '分红送转；ann_date 为 PIT 列';
COMMENT ON COLUMN dividend.end_date IS '分红年度';
COMMENT ON COLUMN dividend.ann_date IS '公告日期（PIT 列）';
COMMENT ON COLUMN dividend.div_proc IS '实施进度';
COMMENT ON COLUMN dividend.stk_div IS '每股送转，股/股';
COMMENT ON COLUMN dividend.stk_bo_rate IS '每股送股比例，股/股';
COMMENT ON COLUMN dividend.stk_co_rate IS '每股转增比例，股/股';
COMMENT ON COLUMN dividend.cash_div IS '每股现金分红（税后），元/股';
COMMENT ON COLUMN dividend.cash_div_tax IS '每股现金分红（税前），元/股';
COMMENT ON COLUMN dividend.base_share IS '基准股本，万股';
COMMENT ON COLUMN dividend.raw_json IS '源端整行 JSON，保留未建模字段';

-- ---------------------------------------------------------------------
-- 名称变更：保留历史名称，避免用当前名称回填历史。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS namechange (
    ts_code       VARCHAR NOT NULL,
    name          VARCHAR NOT NULL,
    start_date    DATE    NOT NULL,
    end_date      DATE,
    ann_date      DATE,
    change_reason VARCHAR,
    raw_json      VARCHAR,
    PRIMARY KEY (ts_code, name, start_date)
);
COMMENT ON TABLE namechange IS '证券名称变更历史';
COMMENT ON COLUMN namechange.start_date IS '名称启用日期';
COMMENT ON COLUMN namechange.end_date IS '名称结束日期';
COMMENT ON COLUMN namechange.ann_date IS '公告日期（PIT 列）';
COMMENT ON COLUMN namechange.raw_json IS '源端整行 JSON，保留未建模字段';
