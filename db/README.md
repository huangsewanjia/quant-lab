# quantdb — 20 年 P0 行情/财务库（WORK-513）

Tushare Pro → Parquet(L0，只追加) → DuckDB(L1 规范化层) 的全量回填实现。

- **研究窗口**：2006-01-01 ~ 2026-11-21，以 `trade_cal` 的实际交易日为准（5,072 个开市日）。
- **股票池**：5,924 只（上市 5,580 + 退市 344 + 待上市 0）。
- **数据落盘**：`/srv/share/quantdb`（HDD）。**不写 `/`（SSD 系统盘）**；DuckDB 溢写目录为 `/srv/share/quantdb/staging/tmp`。
- **代码**：`quant-lab/db/`。行情与政策原文不入 git。

---

## 1. 环境

```bash
/home/moyongkai/.venvs/quantdb/bin/python -V     # Python 3.14.4
```

| 包 | 版本 | 用途 |
|---|---|---|
| tushare | 1.4.29 | 官方 SDK（字段口径对照，见 `db/etl/introspect.py`） |
| pandas | 3.1.1 | 分区整理 |
| pyarrow | 26.0.1 | Parquet 落盘 |
| duckdb | 2.0.1 | L1 规范化层 |
| requests | 2.34.2 | 直连 `api.tushare.pro` |
| pytest | 9.2.0 | 单测 |

取数走**直连 HTTP**（`db/etl/tushare_client.py`），不用 SDK 包装，原因有二：

1. 需要上游原始错误码（`40203` 无权限 / `50101` 缺参数）来核对权限矩阵，SDK 的异常里没有；
2. 需要每次响应的**精确行数**来判定服务端截断，SDK 包装后就看不到了。

token 只从 `TUSHARE_TOKEN` 或 `/home/moyongkai/.config/quantdb/tushare.token`（0600）读，**不打印、不写日志、不进 manifest、不进评论**。

---

## 2. 落盘约定

```
/srv/share/quantdb/raw/<dataset>/trade_date=YYYYMMDD/part.parquet   # 行情类，按日分区
/srv/share/quantdb/raw/<dataset>/ts_code=<code>/part.parquet        # 财务类，按股票分区
/srv/share/quantdb/raw/index_weight/index_code=<code>/part.parquet  # 指数成分，按指数
/srv/share/quantdb/raw/<dataset>/partition=<key>/part.parquet       # 其余（trade_cal / stock_basic / index_classify）
/srv/share/quantdb/db/quantdb.duckdb                                # DuckDB 规范化层
/srv/share/quantdb/meta/<dataset>/<partition>.json                  # 行数 + sha256 + 抓取时间 + 参数
/srv/share/quantdb/meta/failed/<dataset>.json                       # 失败分区（重试 2 次后）
/srv/share/quantdb/meta/checks/checks-<date>.json                   # 质量闸报告
/srv/share/quantdb/logs/fetch-<date>.jsonl                          # 每次上游调用：接口/参数/耗时/行数/成败
/srv/share/quantdb/logs/run-full.log                                # 批次运行日志
/srv/share/quantdb/staging/tmp                                      # DuckDB 溢写
```

**manifest 字段**：`dataset` / `partition` / `rows` / `bytes` / `sha256` / `file` / `source` / `fetched_at` / `columns` / `column_types`（每列的 Parquet 物理类型）/ `primary_key` / `query`（本次取数用的参数）/ `empty_upstream`（该交易日确无数据）/ `rows_raw`（上游返回的原始行数）/ `rows_dropped_null_pk`（主键含 NULL 被丢的行数）/ `possible_truncation`（响应触到上游行数上限）/ `months_requested` + `months_with_rows`（`index_weight` 用）。

**写入顺序**：先写 `.part.parquet.tmp.<pid>.<tid>` → `os.replace` 原子改名 → 再写 manifest（manifest 本身也是临时文件 + 原子改名）。
因此：manifest 存在 ⇒ parquet 一定存在且完整；中断最多留下一个 `.tmp` 残留（下次启动清理），**不会**出现「manifest 指向不存在的文件」。

### 2.1 空分区的类型策略（WORK-518 修）

pandas/pyarrow 是**按值**推断 Parquet 列类型的，所以：

- 没有行的分区 → 所有列写成 Parquet `null` 类型；
- 有行但整列为 `None` 的分区 → 该列同样是 `null` 类型；
- 整列缺失的日期列 → pandas 保留 datetime dtype，写成 `timestamp[ms]` 而不是 `date32`。

这三种都会打穿 DuckDB 的多文件读取：`read_parquet()` 默认取**批内第一个文件**的 schema，再把其余文件往那个 schema 上转，
遇到 `null` 目标类型就抛 `failed to cast column "trade_date" from type DATE to "NULL"`（2026-11-22 实测：`stk_limit` 241 个、
`suspend_d` 2,541 个、财务四表各 5,924 个、`dividend` 4,663 个、`fina_indicator` 1,742 个、`index_weight` 8 个分区带 `null` 类型列）。

**两侧同时修**（只修一侧不够：库里的旧分区不能重写）：

| 侧 | 做法 | 为什么 |
|---|---|---|
| 落盘 `write_partition` | 用 `db/schema.sql` 声明的类型显式写 Parquet：列类型是 `null` → 换成声明类型；声明为 `DATE` 而推断成 timestamp → 转回 `date32`；其余列保留上游推断类型 | 新分区从一开始就和同数据集的其他分区同类型，manifest 另存 `column_types` 便于事后审计。**不改**有值列的上游类型，避免把上游的字符串/浮点语义改掉 |
| 装载 `load_partitions` | `read_parquet(?, hive_partitioning=false, union_by_name=true)` + 按**批内实际类型**做显式 CAST | `union_by_name=true` 让 DuckDB 按列名合并 schema 并把 `null` 提升为真实类型，不再取「第一个文件的 schema」。不重写库里的旧分区也能装进来 |

**取舍**：

- `union_by_name=true` 每批要做一次 schema 合并，比默认模式慢（实测全量装载 128 批，耗时见 §9）；换来的是**装载结果与批内文件顺序无关**。默认模式下只要批首文件是空分区就会炸，报错文件名每次都不同。
- 没有选「逐文件统一 schema 再合并」：那要打开 50,917 个文件的元数据，比 128 批的合并贵得多，而且解决不了「同一列在不同文件里类型不同」（`dividend.div_listdate` 实测 4,161 个 `date32` + 1,709 个 `timestamp[ms]` + 54 个 `null`）。
- DATE 列的投影按批内实际类型分三种：`DATE`/`TIMESTAMP` 直接 `CAST(... AS DATE)`（**这一步很关键**：合并后混合列被提升成 `TIMESTAMP`，如果只按字符串解析，这些分区的日期会被静默变成 NULL）；`VARCHAR` 走 `try_cast` + `try_strptime('%Y%m%d')` + `try_strptime('%Y-%m-%d')`；数值类型只走字符串解析（避免被当成儒略日）。
- 库里的旧分区**不重写**（`raw/` 只追加），所以旧分区仍然带 `null`/`timestamp` 类型列，由装载器兜住。

### 2.2 主键过时的表会被重建（WORK-518 修，装载时实测发现）

DuckDB **改不了已有表的主键**，`CREATE TABLE IF NOT EXISTS` 也不会更新旧表。于是：

- 财务四表 + `dividend` 的主键后来加过 `dup_seq`（见 3.0 节：上游同一份报表会给原始版和更正版两行）；
- 但 2026-11-23 的 `quantdb.duckdb` 是在那之前建的，这 5 张表**仍带着没有 `dup_seq` 的旧主键**；
- 装载用 `INSERT OR REPLACE`：主键冲突时**旧行被替换掉，不报错**，于是同一份报表的两个版本只留一个 —— **静默丢行**。

实测（2026-11-23 装载，Parquet 读到的行数 vs 表内行数）：

| 表 | Parquet 行数 | 表内行数 | 丢失 | 丢失率 |
|---|---:|---:|---:|---:|
| `income` | 353,491 | 282,482 | 71,009 | 20.1% |
| `balancesheet` | 398,898 | 276,102 | 122,796 | 30.8% |
| `cashflow` | 362,711 | 282,078 | 80,633 | 22.2% |
| `fina_indicator` | 484,520 | 273,114 | 211,406 | 43.6% |
| `dividend` | 248,990 | 248,155 | 835 | 0.3% |

修法：**在装载该数据集之前**比对该表在库里的主键与 `db/schema.sql` 声明的主键，不一致就先 `DROP` 再按当前 schema 重建
（重建的表名会打印出来，不静默），然后立刻装载这个数据集把它填满。DuckDB 层是从 Parquet 层派生的，所以「重建 + 填满」是一体的。

几条边界：

- **只重建本次要装的那个数据集的表**。否则 `--dataset daily --load-only` 这种子集装载会把 5 张财务表重建成空表
  （它们不在装载范围内，没人来填），等于用一次装载抹掉 180 万行。第一轮独立审查判这是 blocking，就是这个原因。
- 不在装载范围内的过时表**不动**，只在 stderr 提示一行：「这些表的主键过时，但不在本次装载范围内，未重建」。
- 重建 + 建视图放在**一个事务**里，中途崩了不会留下「表没了、视图也没了」的中间态。
- 主键一致的表不动，行数也不动；主键比较按**列集合**而不是列顺序，避免仅仅换个顺序就触发一次破坏性重建。
- 装载中途崩了，表是空的（Parquet 层完好），重跑一次就填满；G2「Parquet 行数 = DuckDB 行数」这道闸会把这种状态判 FAIL。

这就是 G2 存在的意义：它把静默丢行变成 FAIL。

---

## 3. 数据集与拉法（13 类 + 1 个辅助）

| # | 数据集 | 接口 | 拉法 | 分区键 | 说明 |
|---|---|---|---|---|---|
| 1 | `trade_cal` | `trade_cal` | 一次/交易所（SSE、SZSE），全窗口 | `partition=<exchange>` | 5,072 个开市日 |
| 2 | `stock_basic` | `stock_basic` | 一次/状态（L/D/P） | `partition=<status>` | 主档 + 上市日期 |
| 3 | `daily` | `daily` | 日期窗口（按日分区） | `trade_date=` | 未复权 OHLCV |
| 4 | `daily_basic` | `daily_basic` | 日期窗口 | `trade_date=` | 估值/市值/换手 |
| 5 | `adj_factor` | `adj_factor` | 日期窗口 | `trade_date=` | 复权因子 |
| 6 | `stk_limit` | `stk_limit` | 日期窗口 | `trade_date=` | 涨跌停价 |
| 7 | `suspend_d` | `suspend_d` | 日期窗口 | `trade_date=` | 停复牌 |
| 8 | `index_daily` | `index_daily` | **按交易日一次** | `trade_date=` | 该接口不接受日期窗口（实测 `参数校验失败`），且单日 9,345 行不截断 |
| 9 | `index_weight` | `index_weight` | 按指数 × 月 | `index_code=` | 8 只宽基：上证综指/上证50/沪深300/中证500/中证1000/深证成指/创业板指/科创50 |
| 10 | `index_classify` | `index_classify` | 一次（`src=SW2021`） | `partition=SW2021` | 申万行业分类，511 行 |
| 11 | `namechange` | `namechange` | 按年窗口（21 次）后按 `ts_code` 拆分 | `ts_code=` | 见下方「为什么不是按 ts_code」 |
| 12 | `dividend` | `dividend` | 按 `ts_code`（5,924 次） | `ts_code=` | 见下方「为什么不是按日期窗口」 |
| 13 | `income` | `income` | 按 `ts_code` + 公告日窗口 | `ts_code=` | 85 列 |
| 13b | `balancesheet` | `balancesheet` | 同上 | `ts_code=` | 152 列 |
| 13c | `cashflow` | `cashflow` | 同上 | `ts_code=` | 97 列 |
| 13d | `fina_indicator` | `fina_indicator` | 同上 | `ts_code=` | 108 列 |

> 编号沿用 issue 的「13 类」口径（第 13 项是财务四表 + 指标）。

### 3.0 财务表主键里的 `dup_seq`：同一份报表会有两个版本

上游对**同一份报表**会返回两行——原始披露版（`update_flag=1`）和更正版（`update_flag=0`），
两行的 `ann_date` 相同但**数值可能不同**（实测：`ebit`、`ebitda`、`total_share`、`free_cashflow` 被更正过）。
`fina_indicator` 没有 `update_flag`，同一个 `(ts_code, end_date, ann_date)` 会返回两次计算口径不同的行
（`fcff`、`fcfe`、`bps` 等现金流派生字段有差异）；`dividend` 也可能对同一方案返回两行。

如果按 `(ts_code, end_date, report_type, ann_date)` 做主键去重，这些行会被**静默丢掉**：

| 数据集 | 上游行数 | 旧主键保留 | 丢失 | 丢失率 |
|---|---:|---:|---:|---:|
| `income` | 58 | 40 | 18 | 31% |
| `balancesheet` | 67 | 40 | 27 | 40% |
| `cashflow` | 66 | 40 | 26 | 39% |
| `fina_indicator` | 53 | 40 | 13 | 25% |

（样本：10 只股票 2023 年报，实测 2026-11-22。`600000.SH` 全部 `income` 历史：上游 150 行，旧主键只留 84 行。）

因此这 5 张表的主键在自然键后面加了合成列 **`dup_seq`**：

- 自然键：`income`/`balancesheet`/`cashflow` = `(ts_code, end_date, report_type, ann_date)`，
  `fina_indicator` = `(ts_code, end_date, ann_date)`，`dividend` = `(ts_code, end_date, div_proc, ann_date)`；
- `dup_seq`：同一自然键组内的行号（0、1、…），**按行内容排序后编号**，所以与上游返回顺序无关、可复现；
- 组内只有一行时 `dup_seq = 0`；上游真给不出区别的完全重复行也会保留（不静默丢数据）。

查财务表时通常取 `dup_seq = 0`（内容序最前的那一版），要两版都看就不要加这个过滤。

### 3.1 为什么行情用日期窗口而不是按日一次

issue 口径是「按 `trade_date`」，**分区**仍然是按日；但**取数**用日期窗口，原因是服务端单次返回有行数上限
（`daily`/`daily_basic`/`adj_factor` = 6,000 行），按日一次会命中上限并**静默截断**。窗口取数在命中上限时自动折半重取，
直到响应行数严格小于上限为止，因此**每行数据都取全**，而上游调用次数从 ~19,400 次降到 ~4,000 次。

折半的扇出由**上市密度**决定：`stock_basic` 给了每只股票的 `list_date`，据此可估出任意窗口的行数
（2026 年 ~5,600 行/日，2006 年 ~1,400 行/日），扇出按 `ceil(估计行数 / (上限 × 0.9))` 计算。
实测该改动把日线的上游调用从 3.0 次/分区降到 0.82 次/分区。

### 3.2 财务四表为什么必须按 `ts_code`

`income`/`balancesheet`/`cashflow`/`fina_indicator` 的横截面查询一律被拒：
`ann_date`、`period`、`start_date/end_date` 单独传都返回 `50101 必填参数 ts_code`（2026-11-22 实测）。
因此只能逐股取，且这四个接口的单次返回被硬顶在 **100 行**。

处理办法：用 `stock_basic` 的 `list_date` 把每只股票的公告日区间切成 2015/2016 两段
（`fetch.code_windows`），每段 ≤ ~90 行，落在上限之内；2016 年后上市的股票只取后一段。
日期过滤作用在 **`ann_date`**（实测：2010-2015 窗口返回的行 `ann_date` 全在窗口内，而 `end_date` 更宽）。
`income` 无 100 行上限（观测最长 178 行），故只取一段，并以 170 行为阈值做兜底拆分。

### 3.3 `namechange` 为什么按年窗口

整窗（2006-2026）会撞上 10,000 行上限并**丢行**：实测 30 只样本股逐股取到的行里，有 30 行不在整窗响应中。
逐年取（每年都低于上限）后，样本股 2006/2010/2020 三个年度窗口内的行 **零缺失**。
取回的年结果合并后再按 `ts_code` 拆成分区，落盘形态与「按 ts_code」一致。

### 3.4 `dividend` 为什么按 `ts_code`

该接口的日期窗口语义不可靠：2023 年 1-3 月窗口返回 20 行，1-6 月窗口返回 2,000 行（撞上限），
2023-2026 窗口只返回 1 行。逐股取（不加日期参数）稳定返回该股完整分红记录（78-89 行），
再用 `ann_date` 落到研究窗口内，manifest 里同时记 `rows_raw`（未过滤行数）。

---

## 4. 复跑命令

```bash
export QUANTDB_HOME=/srv/share/quantdb
PY=/home/moyongkai/.venvs/quantdb/bin/python
cd quant-lab

# 取数（断点续跑：manifest + sha256 校验通过的分区直接跳过）
$PY -m db.etl.fetch --dataset all --start 20060101 --end 20261121 --concurrency 8
$PY -m db.etl.fetch --dataset daily --start 20060101 --end 20261121 --concurrency 8   # 单数据集
$PY -m db.etl.fetch --dataset all --time-budget 480                                   # 单次跑 8 分钟后干净退出

# 状态
$PY -m db.etl.fetch --status
$PY -m db.etl.stats                       # 台账统计（分区/行数/字节/调用数）

# 装入 DuckDB（L1）
$PY -m db.etl.fetch --dataset all --load-only --full-refresh   # 全量重建（推荐）
$PY -m db.etl.fetch --dataset all --load-only                   # 增量追加
$PY -m db.etl.fetch --dataset daily --load-only --full-refresh # 单数据集重建

# 质量闸（退出码非 0 即不通过）
$PY -m db.checks.run all
$PY -m db.checks.run G5_sina_crosscheck --sample-days 20 --tolerance 0.01

# 查询
$PY -m db.ql daily --code 600000.SH --start 20200101 --end 20201231
$PY -m db.ql fin   --code 600000.SH --table fina_indicator --period 2023
$PY -m db.ql sql "SELECT count(*) FROM v_daily_qfq"

# 单测
$PY -m pytest db/tests -q
```

`workdir/run_fetch.sh` 是整轮驱动脚本：每次跑 ≤8 分钟的切片，靠 manifest 续跑，直到 13 类数据集全部齐全且无失败分区。

---

## 5. 质量闸（6 条）

| 闸 | 内容 | 判定 |
|---|---|---|
| G1 | 每个开市日都有 `daily` 分区；逐日行数与 ±5 日滚动中位数对比，偏差 >5% 逐日列出；偏差 **>5% 的日子一律记 anomaly**，再把当天预期行数按**超额停牌家数**下调（预期 = 滚动中位数 − 当日超额停牌家数），与调整后预期偏差仍 ≤5% 才判「停牌可解释例外」；行数**偏多永远不解释**（见 §5.1） | 缺失/空/日历外日期 = 0 且**未解释**异常 = 0；可解释例外单列，不影响闸门结论 |
| G2 | Parquet 层主键唯一；**每张 DuckDB 表行数 = 其 Parquet 行数**（主键冲突会在装载时静默丢行，所以行数勾稽才是真检查） | 重复组 = 0 且行数差 = 0 |
| G3 | `high ≥ max(open, close)`、`low ≤ min(open, close)` | 违例 = 0 |
| G4 | `adj_factor` 无 0/负值/缺失 | 违例 = 0 |
| G5 | 抽样 20 个开市日 × 5 只股票，与新浪源（akshare 1.20.65）比 `close`，容差 0.01 元 | 比对数 > 0 且违例 = 0 |
| G6 | 分区/manifest 完整性：manifest 数 = 分区数、sha256 全验、无「有分区无 manifest」 | 坏数 = 0 |

G5 的新浪源用 `stock_zh_a_hist_sina`（新浪），一次请求取回个股全历史，因此 20 个交易日 × 5 只股票只需 5 次上游请求。
交叉校验方向是「tushare 服务端 ↔ 本机源」，符合数据源路由纪律。

**空数据不判通过**：G1/G2/G3/G4/G6 在「表是空的」或「数据集一个分区都没有」时一律判 FAIL——缺数据不是数据干净。

### 5.1 G1 的停牌口径（WORK-518 修）

原规则只看 `daily` 逐日行数与 11 日居中滚动中位数的偏差，>5% 就记异常并把闸门判 FAIL。
2015-07-08/09/10/13 这四天日线行数比中位数少 13%~33%，但同日 `suspend_d` 有 1,027~1,439 只股票停牌——
这是**真实市况**（千股停牌），不是数据缺失。规则没看停牌数，也没有例外清单，所以只能判 FAIL。

现在的判定（`db/checks/checks.py`）：

1. 偏差 >5% 的日子**一律记为 anomaly**，一条都不删；
2. 停牌数据来自 `suspend_d`，只认 `suspend_type='S'`（真正停牌的），`R`（复牌）另计，不用来开脱；
3. **把当天的预期行数按停牌调整**：`预期 = 滚动中位数 - 当日超额停牌家数`，
   其中「超额停牌」= 当日停牌家数 − 邻近日停牌家数的滚动中位数。
   滚动中位数本身已经含了平常那几十上百家的例行停牌，所以**只有超额停牌才可能解释偏差**；
4. 行数与**调整后预期**的偏差仍 ≤5% → 判为「停牌可解释例外」；否则 → 未解释异常，闸门 FAIL；
5. **行数比中位数多（多出来）永远不解释**：缺口为负，超额停牌只会把预期压得更低，判定只会更严；
6. 没有停牌记录 → 没有例外可讲 → 判 FAIL。

所以这条闸**只可能被停牌「解释」，不可能被停牌「制造」**——调整只会让原本达标的日更不达标，不会反过来。

闸门报告里每一天都给出 `rows / rolling_median / deviation / shortfall_rows / surplus_rows / suspend_rows / suspend_rows_s / suspend_rows_r / suspend_baseline_median / excess_suspensions / expected_rows / adjusted_deviation / judgement`，
可解释例外另列在 `exceptions`，未解释异常在 `unexplained_anomalies`，结论字段 `conclusion` 取
`PASS` / `PASS_WITH_EXCEPTIONS` / `FAIL`。**阈值仍是 5%，没有放宽。**

实测（2026-11-23，真实库 5,072 个开市日）：未调整口径下偏差 >5% 的正好是那 6 天，
调整后它们的偏差是 0.4%~1.4%（全部落在 5% 内）→ 6 条可解释例外、0 条未解释异常 → G1 判 **PASS-with-exceptions**；
其余 5,066 天的未调整偏差本来就 ≤5%，不受影响（调整只用于「解释」，不用于「制造」异常）。

### 本轮闸门实测结果

<!-- gates:begin -->
实测于 2026-11-23 09:22，命令 `/home/moyongkai/.venvs/quantdb/bin/python -m db.checks.run all` → **EXIT_CODE=0**，
报告 `/srv/share/quantdb/meta/checks/checks-20261123.json`，日志 `/srv/share/quantdb/logs/work518-checks.log`。

| 闸 | 结论 | 实测值 |
|---|---|---|
| G1 交易日勾稽 | **PASS_WITH_EXCEPTIONS** | 开市日 5,072，有行天数 5,072，缺失日 0、空日 0、日历外日期 0；anomaly 6 → 可解释例外 6、**未解释异常 0**；偏差阈值仍是 5%（未放宽） |
| G2 主键与行数勾稽 | **PASS** | 重复主键组 0；行数不一致的数据集 0（12 张表逐表 Parquet 行数 = DuckDB 行数） |
| G3 OHLC 一致性 | **PASS** | 检查 15,966,167 行，违例 0，OHLC 有空值 0 |
| G4 复权因子为正 | **PASS** | 检查 16,722,519 行，非正 0、空值 0 |
| G5 新浪源交叉校验 | **PASS** | 抽样 20 个开市日 × 5 只股票：请求 100 对，实际比对 96 对（4 对新浪源缺失），容差 0.01 元，违例 0，最大绝对偏差 0.0 |
| G6 manifest 完整性 | **PASS** | 坏分区/坏 manifest 合计 0 |

G1 的 6 条可解释例外（未调整偏差 >5%，停牌调整后偏差 0.38%~1.37%，全部落在 5% 内）：
2015-07-08、2015-07-09、2015-07-10、2015-07-13、2007-05-18、2006-06-30。逐日的
`rows / rolling_median / deviation / shortfall_rows / surplus_rows / suspend_rows / suspend_baseline_median /
excess_suspensions / expected_rows / adjusted_deviation / judgement` 都在报告的 `days` 里，可解释例外另列在 `exceptions`，
未解释异常在 `unexplained_anomalies`（本次为空）。

同轮交付的其他实测：

- **全量装载**：`python -m db.etl.fetch --dataset all --load-only --full-refresh` → **EXIT_CODE=0**
  （日志 `/srv/share/quantdb/logs/work518-final-load.log`，代码提交 `1010512`）。16 张表逐表「读入行数 = 表内行数」。
- **16 表逐表 Parquet ↔ DuckDB 行数对照**：差值全为 0，`MISMATCH_TABLES=0`（日志 `/srv/share/quantdb/logs/work518-final-verify.log`）。
- **复权视图**：`v_daily_qfq` 与 `v_daily_hfq` 各 15,964,248 行；600000.SH 抽查 `close_hfq = close × adj_factor / min(adj_factor)`
  与 `close_qfq = close × adj_factor / max(adj_factor)` 的最大偏差 5.0e-05（视图按 4 位小数取整的舍入残差）。
- **单测**：`python -m pytest db/tests -q` → **132 passed**（本轮新增 25 个用例，见 §10）。
<!-- gates:end -->

---

## 6. 单位与口径

- `daily.vol` 单位**手**（1 手 = 100 股），`daily.amount` 单位**千元**；`daily_basic.total_mv`/`circ_mv` 单位**万元**，`total_share`/`float_share`/`free_share` 单位**万股**。这些注释写进了 DuckDB 的列注释里（`COMMENT ON COLUMN`）。
- `daily` 的 `open/high/low/close` 是**未复权**价；复权价由 `v_daily_qfq` / `v_daily_hfq` 视图**查询时现算**，不落盘。
  - qfq：`close × adj_factor / 该股历史最大 adj_factor`
  - hfq：`close × adj_factor / 该股历史最小 adj_factor`
- `income`/`balancesheet`/`cashflow`/`fina_indicator` 保留 `ann_date`（公告日）与 `f_ann_date`（正式公告日），可直接做 PIT 过滤。
  `fina_indicator` 上游没有 `f_ann_date`，只有 `ann_date`。
- `dividend` 的 `ann_date` 落在研究窗口内的行被保留；`ann_date` 为空的行也保留（manifest 记 `rows_raw`）。

---

## 7. 实测的上游约束（2026-11-22，2000 分档）

| 事实 | 实测值 | 后果与对策 |
|---|---|---|
| 全局限流 | 250 次顺序调用耗时 75.0 s，**无报错**（服务端延迟而非报错） | 限速器 195 次/分；并发 8 只是为了掩盖延迟 |
| `daily` 单次行数上限 | 6,000（静默截断） | 窗口取数 + 命中上限即折半 |
| `daily_basic` / `adj_factor` | 6,000 | 同上 |
| `stk_limit` | 7,800 | 同上 |
| `suspend_d` | 5,000 | 同上 |
| `namechange` | 10,000 | 逐年取 |
| `dividend` | 2,000（按日期窗口时） | 改逐股取 |
| `balancesheet`/`cashflow`/`fina_indicator` | 100（硬顶） | 2015/2016 切两段 |
| `income` | 无 100 行上限（最长 178） | 单段 + 170 行阈值兜底 |
| 横截面财务查询 | `50101 必填参数 ts_code` | 只能逐股 |
| 多代码 `ts_code` 批量化 | 返回 0 行（不支持） | 不能批量化 |
| `index_daily` 日期窗口 | `参数校验失败` | 只能逐日取 |
| `stk_limit` 早期年份 | 2006 年整年返回 0 行（上游无历史） | 对应分区记为 0 行空分区（manifest 带 `empty_upstream: true`） |

---

## 8. 已知缺口与限制

1. **`stk_limit` / `suspend_d` 的早年数据上游就没有**：`stk_limit` 2006 年整年为 0 行；这类交易日会落成 0 行空分区（manifest 标 `empty_upstream: true`），**不是漏抓**。
   - 空分区是**已完整取到、确认无数据**的记录，和「没取到」严格区分：上游窗口失败时**不写任何分区**（manifest 缺失 → 下轮重试），只有完整取回的窗口里没有数据的交易日才落空分区。否则这些交易日会被当成「还没取过」在每一轮切片里重拉一遍，回填永远跑不完。
2. **`index_daily` 是全量指数横截面**（单日 9,345 个指数），体量最大（约 4,700 万行 / 数 GB），装载 DuckDB 耗时最长。
3. **`index_weight` 只覆盖 8 只宽基指数**，申万行业指数成分上游不返回（`801010.SI` 实测 0 行），所以**没有行业指数成分**。
4. **`index_classify` 只取 `src=SW2021`**（511 行）；`src=SW` 实测返回 0 行。
5. **财务四表只到 2015/2016 两段窗口**：靠 `stock_basic.list_date` 切段，理论上 `ann_date` 落在两段之间的行不会丢（两段连续覆盖 2006-01-01 ~ 2026-11-21），但如果某只股票的 `list_date` 缺失，切段会退化成整窗 → 命中 100 行上限 → 由兜底拆分处理。
6. **上游是持续修订的**：历史财务数据会被上市公司更正，本库是**抓取时点的快照**，manifest 里的 `fetched_at` 是取数时刻。要做 PIT 回测必须按 `ann_date` 过滤，而不是假设今天的数据就是当年可见的数据。
   - 同一份报表的原始版与更正版都在库里，用 `dup_seq` 区分（见 3.0 节）；要复现「当时可见的数」，按 `ann_date` + `dup_seq` 一起过滤。
7. **`stock_basic` 没有 `delist_date` 字段**（上游该接口返回 10 列，不含退市日），所以退市股票的财务窗口右端只能取到研究窗口末端，多出的查询返回 0 行。
8. **前一轮同单运行留下的新浪单股冒烟数据已移出**：`/srv/share/quantdb/_superseded/20261122-sina-smoke/`（`move` 而非删除，数据保留），避免与本轮 `part.parquet` 布局混读。
9. **`db/policy/` 是另一单（政策采集）的代码**，不在本单范围，本次未运行也未验收。
10. **空分区是数据形态，不是缺陷**：上游窗口完整取回但没有数据的交易日，会落成 0 行空分区（manifest 标 `empty_upstream: true`）。
    这类分区在 Parquet 里**也带完整列结构**（声明类型，不是 `null` 类型），见 §2.1。
    库里的旧空分区仍是旧类型（`null` / `timestamp`），由装载器的 `union_by_name` 兜住，**不重写**（`raw/` 只追加）。
11. **`stk_limit` 2006 年整年 0 行**：上游 2007-01-04 起才有涨跌停价历史，2006-01-04~2006-12-29 的 241 个交易日是**真实上游缺口**，不是漏抓。
12. **G1 的 5% 阈值没有放宽**，但闸门现在把「千股停牌」和「数据缺失」分开判（见 §5.1）：可解释例外仍然逐日列出证据，未解释异常照样 FAIL。
    如果下游需要「只看硬失败」，读报告里的 `unexplained_anomalies`；如果要「一条异常都没有」，读 `anomaly_count`。
13. **`index_weight` 只到 2026-11-21**：月度窗口的上游数据本身有滞后，最近几个月的成分权重可能还没发布。
14. **独立审查第 3 轮遗留 2 条 suggestion + 3 条 nit**（不影响本轮交付结论，均未阻塞）：
    - **N-1（suggestion）**：`common.load_partitions()` 是公共函数，若调用方只装载某数据集的**部分**分区、而该表主键又过时，
      重建后只会填回这部分分区 → 其余分区的行被清空。**CLI 路径不可达**（`load_duckdb` 总是装载该数据集的全部分区），
      且这种状态会被 G2 的行数勾稽判 FAIL，不会静默。修复方向：只在本次装载覆盖该数据集全部分区时才重建。
    - **N-2（suggestion）**：`rebuild_stale_table()` 自己开事务，无法在外层事务里运行（DuckDB 不支持嵌套事务），
      所以「重建 + 回填」目前**不是一个原子单元**；崩在中间时表是空的、Parquet 层完好，重跑一次即恢复，G2 会判 FAIL。
    - **N-3（nit）**：重建会打断引用了该表旧列的**外部视图**（本库自带的 `v_daily_qfq`/`v_daily_hfq` 会在同一事务里按 schema 重建，不受影响）。
    - **N-4（nit）**：R-3 / R-4 / R-6 / R-8 四项修复和「范围外表未重建」的 stderr 警告**各自没有独立的回归测试**
      （变异测试：把它们改回旧行为，132 个用例仍全绿）。
    - **N-5（nit）**：`Fetcher.resumable()` 与 `common.manifest_resumable()` 逻辑重复，两处各自判断「分区能否跳过」，存在漂移风险。
15. **上游调用报错 1 次（已自愈）**：`balancesheet` 2026-11-22T15:42:53Z 一次读超时（`Read timed out`，120.4 s），
    重试后成功。最终 Parquet 行数 = DuckDB 行数（398,898 = 398,898），**无数据缺失**。全库 76,126 次上游调用中仅此 1 次报错。
16. **`index_weight` 的 8 个分区是本轮删掉旧分区后重抓的**：旧分区因为参数拼接 bug（见 §10）全部 0 行，
    已删除并重抓——本次 2,008 次上游调用、0 失败、618 s，合计 734,422 行。`raw/` 里其他数据集的历史分区**一个没动**。

---

## 9. 交付统计

由 `python -m db.etl.stats` 生成（分区数 / manifest 数 / 行数 / 字节 / 空分区 / 上游调用 / 上游耗时）：

<!-- stats:begin -->
| 数据集 | 接口 | 拉法 | 分区键 | 分区数 | manifest | 行数 | 字节 | 空分区 | 上游调用 | 调用报错 | 上游耗时 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| trade_cal | `trade_cal` | calendar | exchange= | 2 | 2 | 15,260 | 114,321 | 0 | 6 | 0 | 2 s |
| stock_basic | `stock_basic` | stock_basic | status= | 3 | 3 | 5,924 | 213,454 | 1 | 7 | 0 | 2 s |
| daily | `daily` | range | trade_date= | 5072 | 5072 | 15,966,167 | 694,116,859 | 0 | 5,459 | 0 | 794 s |
| daily_basic | `daily_basic` | range | trade_date= | 5072 | 5072 | 15,875,303 | 1,694,222,643 | 0 | 5,636 | 0 | 874 s |
| adj_factor | `adj_factor` | range | trade_date= | 5074 | 5074 | 16,722,519 | 136,773,507 | 0 | 5,712 | 0 | 587 s |
| index_daily | `index_daily` | trade_date | trade_date= | 5072 | 5072 | 34,474,216 | 1,412,160,293 | 0 | 5,076 | 0 | 826 s |
| stk_limit | `stk_limit` | range | trade_date= | 5072 | 5072 | 16,155,227 | 170,384,459 | 241 | 4,851 | 0 | 567 s |
| suspend_d | `suspend_d` | range | trade_date= | 5072 | 5072 | 581,366 | 16,418,812 | 0 | 3,752 | 0 | 301 s |
| income | `income` | financial | ts_code= | 5924 | 5924 | 353,491 | 359,478,005 | 15 | 8,901 | 0 | 786 s |
| balancesheet | `balancesheet` | financial | ts_code= | 5924 | 5924 | 398,898 | 625,699,521 | 15 | 8,913 | 1 | 1171 s |
| cashflow | `cashflow` | financial | ts_code= | 5924 | 5924 | 362,711 | 451,550,114 | 15 | 8,907 | 0 | 853 s |
| fina_indicator | `fina_indicator` | financial | ts_code= | 5924 | 5924 | 484,520 | 648,971,737 | 24 | 8,909 | 0 | 819 s |
| dividend | `dividend` | per_code | ts_code= | 5924 | 5924 | 248,990 | 56,667,441 | 54 | 5,932 | 0 | 415 s |
| namechange | `namechange` | namechange | ts_code= | 5851 | 5851 | 11,542 | 22,969,491 | 0 | 38 | 0 | 4 s |
| index_weight | `index_weight` | index_weight | index_code= | 8 | 8 | 734,422 | 1,122,384 | 0 | 4,022 | 0 | 265 s |
| index_classify | `index_classify` | classify | src= | 1 | 1 | 511 | 12,753 | 0 | 5 | 0 | 1 s |
| **合计** | | | | **65,919** | | **102,391,067** | **6,290,875,794** | | **76,126** | **1** | **8265 s** |
<!-- stats:end -->

---

## 10. WORK-518 返工交付台账（2026-11-23）

WORK-513 拆出的返工单：把「Parquet 齐但查不了库」修成「DuckDB 全表可查 + 六项闸 exit 0」。
分支 `agent/work-518-fix`，基线 `agent/work-513-backfill`（`52090b5`）。

### 10.1 阻塞项与修法

| 编号 | 现象 | 修法 | 提交 |
|---|---|---|---|
| B1 | 空分区 / 整列全 `None` → Parquet 列类型是 `null` → 装载器崩（`failed to cast column "trade_date" from type DATE to "NULL"`） | 两侧同修：落盘按 `schema.sql` 声明类型显式写 Parquet；装载用 `read_parquet(?, hive_partitioning=false, union_by_name=true)` 并按批内实际类型投影（详见 §2.1） | `a1783de` |
| B2 | `index_weight` 参数拼接 bug → 8 个分区全是 0 行 | `hi = min(_month_end(year, month), self.end)`，并删掉那 8 个 0 行分区重抓（2,008 次上游调用 / 0 失败 / 618 s / 734,422 行） | `a1783de` |
| B3 | G2 随 B1 一起崩 | 装载器修好后 G2 自然可跑；G2 现在是「Parquet 行数 = DuckDB 行数」的勾稽闸 | `a1783de` |
| B4 | G1 未纳入停牌口径 → 6 个交易日 >5% 判 FAIL | G1 改为「停牌调整后预期」口径（详见 §5.1），阈值仍是 5% | `a1783de` |
| B5 | **装载实测新发现**：DuckDB 改不了已有表的主键，`INSERT OR REPLACE` 静默丢行（income −20.1%、balancesheet −30.8%、cashflow −22.2%、fina_indicator −43.6%、dividend −0.3%） | 装载该数据集**之前**比对库内主键与声明主键，不一致就重建该表（详见 §2.2） | `21cb6b7` |

### 10.2 独立审查（主循环不参与审查，由独立子代理执行）

| 轮次 | 结论 | 处置 |
|---|---|---|
| 第 1 轮 | **FAIL**：F1 blocking（G1 规则会让所有行数偏多的日子被无理由放行）+ 5 suggestion + 3 nit | F1–F9 全部修复 |
| 第 2 轮 | **FAIL**：R-1 blocking（子集装载会把 5 张财务表抹空约 185 万行）+ R-2–R-8 | R-1–R-8 全部修复 |
| 第 3 轮 | **PASS**：无 blocking；R-1–R-8 全部确认修复，「new regressions introduced: no」；遗留 2 suggestion + 3 nit | 见 §8 第 14 条 |

第 3 轮审查用变异测试逐条确认「哪个修复有测试保护」：R-1 与重建顺序有测试保护，
R-3 / R-4 / R-6 / R-8 与 stderr 警告**没有**独立测试（N-4）。

### 10.3 回归用例

`db/tests/test_work518_blockers.py` 新增 **25** 个用例（B1 写/装两侧、B2 窗口、B4 G1 口径、B5 主键重建、
子集装载不抹表），单测总数 **106 → 132**，`python -m pytest db/tests -q` → **132 passed**。

### 10.4 复跑命令

```bash
export QUANTDB_HOME=/srv/share/quantdb
cd <repo>
/home/moyongkai/.venvs/quantdb/bin/python -m pytest db/tests -q                      # 132 passed
/home/moyongkai/.venvs/quantdb/bin/python -m db.etl.fetch --dataset all --load-only --full-refresh   # EXIT_CODE=0
/home/moyongkai/.venvs/quantdb/bin/python -m db.checks.run all                      # 六项闸全 PASS，EXIT_CODE=0
/home/moyongkai/.venvs/quantdb/bin/python -m db.etl.stats --out /tmp/stats.md
/home/moyongkai/.venvs/quantdb/bin/python -m db.etl.delivery --out /tmp/delivery.json
./db/bin/ql sql "SELECT count(*) FROM v_daily_qfq"                                # 15,964,248
```

### 10.5 数据处置说明

本轮只删改过 **`index_weight` 的 8 个旧分区**（全是 0 行的坏分区，issue 明确授权），其余 `raw/` 分区**一个没动**；
`raw/` 保持只追加语义。库里的旧空分区**没有重写**（仍是旧类型），由装载器的 `union_by_name` 兜住。

---

## 11. 定时任务（WORK-529，2026-11-23）

### 11.1 稳定代码路径

调度只认一个路径，不再从任务 workdir 运行：

```bash
/srv/share/quantdb/code/quant-lab      # git clone；更新用 git pull --ff-only
```

代码、`db/timers/` 与 systemd 单元都在这个仓库里。单元源文件是
`db/timers/systemd/quantdb-*.{service,timer}`，安装到 `~/.config/systemd/user/`。

### 11.2 时间表（Asia/Shanghai）

| 任务 | 频率 / 时间 | 内容 | 单元 |
|---|---|---|---|
| 行情增量 | 每交易日 18:30；19:30 / 20:30 重试 | `daily`、`daily_basic`、`adj_factor`、`stk_limit`、`suspend_d`、`index_daily` + `stock_basic` | `quantdb-daily.timer` |
| 财务增量 | 报告期（1/4/7/10 月）每交易日 19:00；其余周一 19:00 | `income`、`balancesheet`、`cashflow`、`fina_indicator`、`dividend` | `quantdb-finance.timer` |
| 指数成分 | 每月第一个交易日 19:30（由 daily 的第 2/3 次触发执行） | `index_weight` | `quantdb-daily.timer` |
| 政策抓取 | 每天 09:00 / 18:00 | 六源白名单增量 → 分类 → 入库 → 打标 → 元数据 | `quantdb-policy.timer` |
| 政策对账 | 每周一 06:00 | 列表条数 vs 库内，缺的补抓，出统计 | `quantdb-weekly.timer` |
| 周报 | 每周一 09:00 | 成功率、缺失分区、重试、行数变化 | `quantdb-weekly.timer` |
| 冷备 | 每天 03:30（并入现有 `backup-nightly`） | `/srv/share/quantdb` → `/srv/backup/quantdb`，rsync + `SHA256SUMS` | `backup-nightly.timer` |

### 11.3 增量规则

```bash
# 行情：max(raw trade_date)+1 .. 最近已完成收盘日
python -m db.etl.fetch --incremental --group market

# 财务：最近 7 天实际披露，按 disclosure_date 找代码
python -m db.etl.fetch --incremental --group finance --overlap-days 7

# 指数成分：只在每月第一个交易日 19:30 后执行
python -m db.etl.fetch --incremental --group index_weight

# 只装载水位之后的分区（重复装载幂等）
python -m db.etl.fetch --load-only --incremental --dataset daily
```

要点：

- 收盘前手动跑行情会以**上一交易日**为终点，不会把当日未发布的数据写成空分区。
- 18:30 后如果上游当日仍无数据，返回退出码 `75`，不写空分区；19:30 / 20:30 再试，
  第三次仍无数据才建告警单。
- 财务四张表的日期过滤口径不完全一致：`income` / `balancesheet` / `cashflow` 按公告日，
  `fina_indicator` 按报告期。增量窗口同时覆盖 `actual_date` 与 `end_date`。
- 财务和指数成分是「读旧分区 + 合并 + 按主键/内容去重 + 原子写回」，不会用 7 天窗口覆盖历史。

### 11.4 手动触发与看日志

```bash
systemctl --user start quantdb-daily.service
systemctl --user start quantdb-finance.service
systemctl --user start quantdb-policy.service
systemctl --user start quantdb-weekly.service

tail -f /srv/share/quantdb/logs/daily-$(date +%Y%m%d).log
tail -f /srv/share/quantdb/logs/finance-$(date +%Y%m%d).log
tail -f /srv/share/quantdb/logs/policy-$(date +%Y%m%d).log
tail -f /srv/share/quantdb/logs/weekly-$(date +%Y%m%d).log
```

查看下次触发与最近状态：

```bash
systemctl --user list-timers --all | grep quantdb
systemctl --user status quantdb-daily.service
journalctl --user -u quantdb-daily.service -n 100 --no-pager
```

### 11.5 失败告警

wrapper 失败时调用 `MULTICA_CLI_PATH issue create`：

- 标题：`数据更新失败｜<task>｜<YYYY-MM-DD>`
- 正文：任务、时间、主机、退出码、仓库、日志路径、日志尾部 50 行
- 状态 `backlog`、不指派；同一任务同一天只建一张，单号写入
  `/srv/share/quantdb/logs/state/<task>-alert-<date>.id`

### 11.6 停用与回滚

```bash
# 停用全部四个 timer（保留单元文件）
systemctl --user disable --now quantdb-daily.timer quantdb-finance.timer \
  quantdb-policy.timer quantdb-weekly.timer

# 只停某个任务
systemctl --user disable --now quantdb-finance.timer

# 代码回滚：在稳定路径 git revert <SHA>，再 daemon-reload
cd /srv/share/quantdb/code/quant-lab
git revert <SHA>
cp db/timers/systemd/quantdb-* ~/.config/systemd/user/
systemctl --user daemon-reload
```

数据回滚见 `docs/RUNBOOK.md` §10；冷备在 `/srv/backup/quantdb`，校验清单是同目录
`SHA256SUMS`，可用 `sha256sum -c SHA256SUMS` 验证。

### 11.7 失败排查顺序

1. 先看 `/srv/share/quantdb/logs/<task>-<date>.log` 的最后 50 行和 `meta/failed/`。
2. 查交易日历：`python -m db.etl.incremental check-day --date YYYYMMDD`；返回 2 表示日历没覆盖，先
   `refresh-calendar`。
3. 查增量计划：`python -m db.etl.incremental plan --group market --now <ISO>`。
4. 查数据库锁：`/srv/share/quantdb/logs/.quantdb-db.lock`；同一时间只允许一个写库任务。
5. 源端延迟：看退出码 `75` 和 `[source-delay]` 行；不要手工写空分区。

### 11.8 已知限制

- 政策库目前只入库了少量样本；周一对账单轮默认补抓 200 条，`remaining` 会逐周下降。
- 非报告期的周一财务任务会处理最近 7 天实际披露的代码；披露密集时可能跑几十分钟。
- 现有 `backup-nightly.service` 在 WORK-529 之前就是 failed 状态；本单只在其前面增加 quantdb
  冷备，没有改 borg 逻辑。borg 的原始错误仍需单独处理。
- issue 描述里的 `M = 15M` 与 `AGENTS.md` 第一部分「一·五」的「不设 issue 级 token 总额」冲突；
  本单按 `AGENTS.md` 执行，不因额度停手。
