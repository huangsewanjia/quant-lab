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

**manifest 字段**：`dataset` / `partition` / `rows` / `bytes` / `sha256` / `file` / `source` / `fetched_at` / `columns` / `primary_key` / `query`（本次取数用的参数）/ `empty_upstream`（该交易日确无数据）/ `rows_raw`（上游返回的原始行数）/ `rows_dropped_null_pk`（主键含 NULL 被丢的行数）/ `possible_truncation`（响应触到上游行数上限）/ `months_requested` + `months_with_rows`（`index_weight` 用）。

**写入顺序**：先写 `.part.parquet.tmp.<pid>.<tid>` → `os.replace` 原子改名 → 再写 manifest（manifest 本身也是临时文件 + 原子改名）。
因此：manifest 存在 ⇒ parquet 一定存在且完整；中断最多留下一个 `.tmp` 残留（下次启动清理），**不会**出现「manifest 指向不存在的文件」。

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
| G1 | 每个开市日都有 `daily` 分区；逐日行数与 ±5 日滚动中位数对比，偏差 >5% 逐日列出 | 缺失/空/日历外日期 = 0 且异常 = 0 |
| G2 | Parquet 层主键唯一；**每张 DuckDB 表行数 = 其 Parquet 行数**（主键冲突会在装载时静默丢行，所以行数勾稽才是真检查） | 重复组 = 0 且行数差 = 0 |
| G3 | `high ≥ max(open, close)`、`low ≤ min(open, close)` | 违例 = 0 |
| G4 | `adj_factor` 无 0/负值/缺失 | 违例 = 0 |
| G5 | 抽样 20 个开市日 × 5 只股票，与新浪源（akshare 1.20.65）比 `close`，容差 0.01 元 | 比对数 > 0 且违例 = 0 |
| G6 | 分区/manifest 完整性：manifest 数 = 分区数、sha256 全验、无「有分区无 manifest」 | 坏数 = 0 |

G5 的新浪源用 `stock_zh_a_hist_sina`（新浪），一次请求取回个股全历史，因此 20 个交易日 × 5 只股票只需 5 次上游请求。
交叉校验方向是「tushare 服务端 ↔ 本机源」，符合数据源路由纪律。

**空数据不判通过**：G1/G2/G3/G4/G6 在「表是空的」或「数据集一个分区都没有」时一律判 FAIL——缺数据不是数据干净。

### 本轮闸门实测结果

<!-- gates:begin -->
（待跑：回填完成后由 `finalize.sh` 写入）
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
10. **复权视图比 `daily` 少 1,919 行**：有 1,919 个「交易日 × 股票」在 `daily` 里有行情、但没有对应的 `adj_factor`，`v_daily_qfq` / `v_daily_hfq` 直接把它们丢掉（视图覆盖 15,964,248 / 15,966,167 行）。这是上游复权因子本身缺项，不是装载丢行。
11. **参考表 `trade_cal` 必须与研究窗口一致**。它按 `--start/--end` 取数：如果用别的窗口重新抓过日历（例如 `--end` 取到今天），Parquet 层的日历会比 `daily` 多出研究窗口之外的交易日，DuckDB 层若不同步重装，G1 会报「缺交易日」、G2 会报行数不一致。**改了取数窗口就必须同步重装 DuckDB**（`--load-only --full-refresh`）。现在 G2 也把 `trade_cal` / `stock_basic` / `index_classify` 纳入行数勾稽，这类漂移不会再被漏掉。
12. **空分区的 Parquet 类型**：早期版本的空分区所有列都写成 `null` 类型（空 DataFrame 没有值，pyarrow 推不出类型），会让「统一多个文件 schema」的读取器报 `failed to cast column X from type DATE to NULL`（实测 G2 在 `stk_limit` 上崩过）。现在空分区按 `schema.sql` 的声明类型写出（`date32[day]` / `large_string` / `double`），读取侧同时加 `union_by_name=true`，两层都不再依赖「扫描先碰到哪个文件」。
    - 已落盘的旧空分区仍是 `null` 类型（27k 个文件量级，未批量重写），靠读取侧的 `union_by_name=true` 保证可读；下次重抓对应分区会自动写成正确类型。

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
| adj_factor | `adj_factor` | range | trade_date= | 361 | 361 | 499,786 | 4,206,601 | 0 | 215 | 0 | 19 s |
| index_daily | `index_daily` | trade_date | trade_date= | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 1 s |
| stk_limit | `stk_limit` | range | trade_date= | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 s |
| suspend_d | `suspend_d` | range | trade_date= | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 s |
| income | `income` | financial | ts_code= | 0 | 0 | 0 | 0 | 0 | 46 | 0 | 4 s |
| balancesheet | `balancesheet` | financial | ts_code= | 0 | 0 | 0 | 0 | 0 | 45 | 0 | 4 s |
| cashflow | `cashflow` | financial | ts_code= | 0 | 0 | 0 | 0 | 0 | 45 | 0 | 4 s |
| fina_indicator | `fina_indicator` | financial | ts_code= | 0 | 0 | 0 | 0 | 0 | 46 | 0 | 5 s |
| dividend | `dividend` | per_code | ts_code= | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 1 s |
| namechange | `namechange` | namechange | ts_code= | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 s |
| index_weight | `index_weight` | index_weight | index_code= | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 s |
| index_classify | `index_classify` | classify | src= | 1 | 1 | 511 | 12,753 | 0 | 5 | 0 | 1 s |
| **合计** | | | | **10,511** | | **32,362,951** | **2,392,886,631** | | **11,542** | **0** | **1711 s** |

<!-- stats:end -->
