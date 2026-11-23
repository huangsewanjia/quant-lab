# 研报元数据（P1）——东方财富研报列表

> **状态：部分验收。** 元数据回填已完成（68,874 条），评级变动表可用；但上游对 2023 年及更早研报的 EPS/PE 预测字段做了截断，**三年一致预期时间序列无法从本接口完整重建**。该现象命中 issue 的停手条件「EPS 预测为空比例 > 30%」，详见 §6。

本目录只做**研报列表元数据**：机构、分析师、日期、标的、评级、评级变动、目标价、当年/次年/后年 EPS 与 PE 预测。**不下载 PDF**；只保存 `encode_url` 与 `attach_pages`。数据仅限内部研究，**不得进入 public 仓库或对外发布**。

---

## 1. 数据源与边界

| 项 | 值 |
|---|---|
| 接口 | `https://reportapi.eastmoney.com/report/list` |
| 范围 | `qType=0`（个股研报），A 股（沪/深/北） |
| 分页 | `pageSize=100`；上游会把更大的 `pageSize` 静默截到 100 |
| 实测总量 | 2023-01-01 ~ 2026-11-23：**68,874 条**，3,818 只标的，68 家机构，175 个行业 |
| 日期口径 | `publishDate`，按 `trade_date=YYYYMMDD` 落分区；周末也可能有研报 |
| 原始层 | `/srv/share/quantdb/raw/reports/meta/trade_date=YYYYMMDD/part.parquet` |
| 清单 | `/srv/share/quantdb/meta/reports/trade_date=YYYYMMDD.json` |
| DuckDB | `/srv/share/quantdb/db/quantdb.duckdb` |
| 日志 | `/srv/share/quantdb/logs/reports-fetch-YYYYMMDD.jsonl` |

**不做的**：不抓海外投行、不抓公众号解读、不抓 PDF 全文、不做评级迁移矩阵。评级是各券商自己的口径，`rating_norm` 只是五级序数归一化，**不可跨券商直接比较**。

---

## 2. 复跑命令

环境：`/home/moyongkai/.venvs/quantdb/bin/python`（Python 3.14，duckdb 2.0.1，pyarrow 26.0.1）。所有命令在仓库根目录执行。

```bash
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-536-fd8955b65de3/workdir/quant-lab

# 1) 抓取元数据（失败自动重试 2 次；--resume 跳过已校验分区）
/home/moyongkai/.venvs/quantdb/bin/python db/reports/fetch_em.py fetch \
  --start 2023-01-01 --end 2026-11-23 --resume

# 2) 重建 DuckDB 表与派生表（同一条命令可重复跑）
/home/moyongkai/.venvs/quantdb/bin/python db/reports/fetch_em.py build \
  --start 2023-01-01 --end 2026-11-23 --window-days 90

# 3) 抽样校验 + 统计
/home/moyongkai/.venvs/quantdb/bin/python db/reports/fetch_em.py verify \
  --start 2023-01-01 --end 2026-11-23 --sample-size 10

# 4) 只重算统计（不联网）
/home/moyongkai/.venvs/quantdb/bin/python db/reports/fetch_em.py stats
```

`build` 和 `verify` 的默认窗口是 **90 天**（`--window-days` 可改）。本文所有实测值均来自 90 天窗口。

依赖：`db/reports/requirements.txt`。不要用系统 `/usr/bin/python3`，它没有 duckdb/pyarrow。

---

## 3. 表结构

### 3.1 `report_meta`（主键 `info_code`）

| 列 | 含义 |
|---|---|
| `info_code` | 东财研报 ID，主键 |
| `title` | 标题 |
| `stock_code` / `stock_name` | 标的代码/名称 |
| `ts_code` | `000001.SZ` 形式；优先用 `stock_basic` 映射，缺失时按交易所后缀推断 |
| `exchange` | `SH` / `SZ` / `BJ` |
| `org_code` / `org_name` / `org_short_name` | 机构 |
| `researcher` / `author_ids` | 分析师姓名串 / 作者 ID 串 |
| `publish_date` | 发布日（DATE） |
| `publish_year` | 发布日年份 |
| `fiscal_year_this` / `fiscal_year_next` / `fiscal_year_two` | 预测财年：发布年 +0/+1/+2 |
| `rating_raw` / `rating_norm` / `rating_rank` | 原始评级 / 五级归一 / 序数 1–5 |
| `rating_value` / `last_rating_value` | 东财 `emRatingValue` / `lastEmRatingValue` |
| `last_rating_raw` | 上次评级原文 |
| `rating_change_code` / `rating_change_type` | 东财 `ratingChange` / 推断方向 |
| `aim_price_high` / `aim_price_low` | 目标价上限/下限（`indvAimPriceT/L`） |
| `predict_this_year_eps` / `predict_next_year_eps` / `predict_next_two_year_eps` | 三年 EPS 预测 |
| `predict_this_year_pe` / `predict_next_year_pe` / `predict_next_two_year_pe` | 三年 PE 预测 |
| `predict_last_year_eps` / `predict_last_year_pe` | 上一年预测（上游常为空） |
| `actual_last_year_eps` / `actual_last_two_year_eps` | 实际值（上游常为空） |
| `indv_indu_code` / `indv_indu_name` | 东财行业 |
| `industry_code` / `industry_name` / `em_industry_code` | 其他行业字段（上游常为空） |
| `report_type` / `attach_type` / `attach_size` / `attach_pages` | 报告/附件元数据 |
| `encode_url` / `pdf_available` | PDF 标识与页数；**不下载 PDF** |
| `market` / `org_type` / `s_rating_name` / `s_rating_code` / `column_code` | 其他原始字段 |
| `new_listing_date` / `new_purchase_date` / `new_issue_price` / `new_pe_issue_a` / `indv_is_new` | 新股相关字段 |
| `fetched_at` / `source_url` | 抓取时间与来源 |

原始层 Parquet 比表多一列 `raw_json`（上游整条记录），便于以后复核；DuckDB 表不重复存。

### 3.2 `consensus_eps`（主键 `ts_code, fiscal_year, as_of_date`）

按「标的 × 财年 × 交易日」聚合：

| 列 | 含义 |
|---|---|
| `eps_median` | 窗口内各机构**最新一条**预测的中位数 |
| `eps_mean` / `eps_std` / `eps_min` / `eps_max` | 均值/标准差/极值 |
| `eps_q25` / `eps_q75` | 四分位 |
| `n_orgs` | 参与机构数 |
| `latest_publish_date` | 窗口内最新研报日 |
| `window_days` | 窗口天数（本轮 90） |

口径：

1. 只取 `predict_{this,next,next_two}_year_eps` 非空；
2. 对每个 `(标的, 财年, as_of_date, 机构)` 取窗口内 `publish_date` 最新的一条，`info_code` 最大者打破同日并列；
3. 窗口为 `as_of_date - 89 天 ~ as_of_date`；`as_of_date` 取 `trade_cal` 的 SSE 开市日；
4. 只做描述统计，不做投资判断。

### 3.3 `rating_change`（主键 `info_code`）

只保留 `upgrade` / `downgrade` / `initiate` 三类事件，`maintain` 不入表。

| 列 | 含义 |
|---|---|
| `old_rating_raw` / `new_rating_raw` | 旧/新评级原文 |
| `old_rating_norm` / `new_rating_norm` | 归一评级 |
| `direction` | `upgrade` / `downgrade` / `initiate` |
| `rank_delta` | 新序数 − 旧序数；旧评级缺失时为空 |
| `stock_basic_name` / `stock_industry` | 与 `stock_basic` 左连接得到的名称/行业 |

`ratingChange` 码值**不是官方文档**，是按返回样本推断的：`0=上调`、`1=下调`、`2=首次覆盖`、`3=维持`、空=未知。遇到无法判断时回退到 `rating_rank` 比较。这个推断需要在后续拿到官方口径时复核。

---

## 4. 评级归一规则

`rating_norm` 是五级序数，不是跨券商可比的「买入/卖出」标签：

| 归一 | 序数 | 原始词（示例） |
|---|---:|---|
| 强买 | 5 | 买入、强烈推荐、强推、BUY |
| 买入 | 4 | 增持、推荐、谨慎推荐、优于大市、跑赢行业、outperform |
| 中性 | 3 | 中性、持有、观望、同步大市、HOLD |
| 减持 | 2 | 减持、回避、弱于大市、underperform |
| 卖出 | 1 | 卖出、SELL |
| 未知 | — | 空值或无法识别 |

为什么把「买入」放在「强买」、把「增持」放在「买入」：东财的 `emRatingValue` 是 3/2/1 三档（买入/增持/中性），本表把 3 映射到 5、2 映射到 4，保持序数单调。各券商对「增持」「推荐」的定义差异很大，**不要用这张表做跨券商评级比较**。

---

## 5. 落盘、幂等与清单

- 每个自然日一个分区：`/srv/share/quantdb/raw/reports/meta/trade_date=YYYYMMDD/part.parquet`。
- 空分区也写（例如春节/周末），用显式 Arrow schema 保证类型稳定；0 条日期可解释为「上游当日无研报」。
- 写入顺序：`part.parquet.tmp.<pid>.<ns>` → `os.replace` → manifest。manifest 存在则 parquet 完整。
- manifest 字段：`dataset / partition / rows / bytes / sha256 / file / source / fetched_at / columns / column_types / primary_key / query / pages / upstream_hits / rows_raw / duplicates_dropped / impl_version / empty_upstream`。注意 `upstream_hits` 是**整月**上游总数，`rows_raw / rows / duplicates_dropped` 是**该日分区**口径。
- 同区间重跑：分区覆盖写，行数不变；`--resume` 会先比对 manifest 的 sha256，再决定是否跳过。
- 网络失败重试 2 次（共 3 次尝试）；401/403 立即停止并保留原始响应片段。token/凭据不写入日志、manifest 或评论。

---

## 6. 实测结果与关键缺口

### 6.1 规模

| 指标 | 实测 |
|---|---:|
| `report_meta` | 68,874 |
| `consensus_eps` | 1,705,783 |
| `rating_change` | 10,403 |
| 覆盖标的 | 3,818 |
| 覆盖机构 | 68 |
| 覆盖行业 | 175 |
| 有研报的自然日 | 1,331 |
| 零条日期 | 92（全部为 `trade_cal` 非开市日） |

### 6.2 抽 10 只标的比对（东财同接口 `code` 过滤）

10 只跨行业标的，库内条数与东财 `hits` **全部为 0 偏差**：贵州茅台 362/362、比亚迪 301/301、东鹏饮料 213/213、宁德时代 205/205、盐津铺子 196/196、珀莱雅 185/185、青岛啤酒 182/182、爱美客 162/162、中国中免 162/162、中际旭创 157/157。证据：`/srv/share/quantdb/meta/reports/validation/verify-20261123T061503Z.json`。

### 6.3 三个标的一致预期与东财「近六月平均」对照

| 标的 | 财年 | 东财 EPS | 本表中位数 | 偏差 |
|---|---:|---:|---:|---:|
| 贵州茅台 | 2026 | 66.8542 | 66.9750 | 0.18% |
| 贵州茅台 | 2027 | 70.8126 | 70.9200 | 0.15% |
| 贵州茅台 | 2028 | 74.8502 | 75.0400 | 0.25% |
| 比亚迪 | 2026 | 4.0701 | 4.0460 | 0.59% |
| 比亚迪 | 2027 | 5.4188 | 5.2300 | 3.48% |
| 比亚迪 | 2028 | 6.5573 | 6.7150 | 2.41% |
| 东鹏饮料 | 2026 | 6.7327 | 6.5750 | 2.34% |
| 东鹏饮料 | 2027 | 7.2990 | 7.0750 | 3.07% |
| 东鹏饮料 | 2028 | 8.2550 | 8.1450 | 1.33% |

全部 ≤5%。注意：东财「近六月平均」的机构集合比免费研报列表更大，所以中位数不可能逐位相同；这里比的是量级与方向，不是逐条相等。

### 6.4 关键缺口：历史 EPS 字段被上游截断

按发布年统计 `predict_*_eps` 的空值率：

| 发布年 | 条数 | 当年空 | 次年空 | 后年空 |
|---|---:|---:|---:|---:|
| 2023 | 22,074 | 99.97% | 100% | 100% |
| 2024 | 19,934 | 14.31% | 99.99% | 100% |
| 2025 | 16,233 | 4.53% | 12.33% | 100% |
| 2026 | 10,633 | 3.56% | 3.56% | 11.92% |

例：2023-04 的研报在列表接口里三年 EPS 全为空；2024-04 只有当年 EPS；2025-04 有当年+次年；2026-04 才有完整三年。**这不是随机缺失，而是上游对不同年份研报的字段保留策略不同。**

后果：

- `report_meta` 的三年元数据回填达标（68,874 ≥ 3 万），但 2023 年的 EPS 预测基本不可恢复；
- `consensus_eps` 的 2023 财年只有 7 只标的、424 行，不能称为「三年一致预期序列」；
- 2024/2025/2026 财年有可用序列（2024: 2,318 只，2025: 2,444 只，2026: 2,751 只）。

这是 issue 明确列出的停手条件之一（「EPS 预测为空比例 > 30%」）。是否需要补历史预测，需要用户选路线（见 §9），**本轮没有擅自扩大范围去解析研报正文或 PDF**。

### 6.5 其他已知缺口

- 免费列表不含海外投行、部分中小券商；东财「盈利预测」页的机构集合比本表大。
- 不存 PDF 全文；`encode_url` 只作标识。
- 评级口径各券商不同；`rating_norm` 只是序数归一。
- 只保留「当前快照」，没有研报修订历史；同一机构多次发报告时，`report_meta` 保留多行，`consensus_eps` 只取窗口内最新一条。
- `ratingChange` 码值是按样本推断的，未拿到官方字段说明。

---

## 7. 回滚

| 层级 | 动作 |
|---|---|
| DuckDB 表 | `DROP TABLE report_meta; DROP TABLE consensus_eps; DROP TABLE rating_change;` |
| 原始层 | 删除 `/srv/share/quantdb/raw/reports/` |
| 清单/日志 | 删除 `/srv/share/quantdb/meta/reports/`、`/srv/share/quantdb/logs/reports-fetch-*.jsonl` |
| 代码 | 回退 `db/reports/` 与 `.gitignore` 的对应提交 |
| 影响面 | 只新增表与目录，不改既有 16 张 P0 表 |

---

## 8. 测试

```bash
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-536-fd8955b65de3/workdir/quant-lab
/home/moyongkai/.venvs/quantdb/bin/python -m pytest db/reports/tests -q
```

覆盖：评级归一、`ratingChange` 码值、字段归一、月份切分、分区/空分区往返、manifest sha256、`report_meta` → `consensus_eps` 中位数、`rating_change` 与 `stock_basic` 连接。

---

## 9. 需要用户决定的一件事（范围）

2023 年及更早的 EPS 预测无法从列表接口恢复。可选路线：

| 路线 | 做什么 | 代价 | 默认 |
|---|---|---|---|
| A（推荐） | 接受 P1 元数据范围：保留 68,874 条列表元数据 + 2024–2026 可用一致预期；README 明示 2023 缺口 | 本轮即可收口 | ✅ |
| B | 新增「历史预测恢复」子任务：抓研报详情页正文，用规则/NLP 抽 EPS（不碰 PDF 正文） | 约 4 万篇详情页、解析质量与合规风险，需另立规格 | — |
| C | 换付费数据源（Wind/Choice/聚源）补历史一致预期 | 采购与授权，超出本轮预算 | — |

不决策的后果：`consensus_eps` 仍可用，但只能覆盖 2024 年及以后；2023 年不能用于回测或时序研究。
