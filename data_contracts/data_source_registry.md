# 数据源可用性登记表

> **作用**：回答一个问题——「要取这类数据，去哪个源、能不能用、为什么不能用、拿什么证据证明」。
> 本表是跨实验复用的资产：新实验先查本表，再决定要不要自己重探。
>
> **采样日期**：2026-11-15（WORK-404 快照生成时间，见 `experiments/WORK-404/snapshot_manifest.json → generated_at`）。
> **数据窗口**：2025-01-02 ~ 2026-11-13（451 个交易日 × 11 条指数）。
> **状态口径**：`可用` = 本轮实测取到并通过交叉校验；`部分可用` = 能取到但口径/覆盖有硬约束；`未取到` = 本轮实测失败，附原始报错。

---

## 1. 接口级：9 个候选源（S-A…S-I）

| 源 ID | 来源 | URL / 接口 | 抓取方式 | 可用性 | 限制 | 采样日期 | 证据 |
|---|---|---|---|---|---|---|---|
| **S-A** | 腾讯行情（主源） | `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<code>,day,<YYYY-MM-DD>,<YYYY-MM-DD>,640,qfq` | 本机 Python `requests` | **可用**（主源） | ① `param` 的日期**必须带连字符**（`YYYY-MM-DD`），用 `YYYYMMDD` 返回 40 字节错误体；② 缺个别指数代码（中证2000）；③ 不提供成交额 `amount`；④ 单点失败不影响其余（脚本按指数独立 try） | 2026-11-15 | 11 条指数各 451 行，`sha256` 前 12 位见 `experiments/WORK-404/snapshot_manifest.json`；与 S-C 逐点交叉校验见 §3 |
| **S-B** | 东方财富 push2 | `https://push2his.eastmoney.com/api/qt/stock/kline/get`（`secid,klt=101,fqt=1`） | 本机 Python `requests` | **未取到**（本机 IP 被限流） | 本机出口 IP 被 push2 限流，12 条指数记录**全部**记为 `skipped`，不是部分失败；原计划用它取成交额 `amount`，因此成交额被迫改用代理口径 | 2026-11-15 | manifest 中 12 条 `B_eastmoney` 记录 `ok:false`，`error: "skipped: eastmoney push2 对本机 IP 限流（preflight 失败），成交额改用代理口径"` |
| **S-C** | Tushare（交叉校验源） | MCP `mcp__tushare__index_daily` | MCP 服务端拉取（**与本机出口 IP 不同**，构成独立校验） | **可用**（仅作校验） | 需 MCP 凭证；本轮只取到 3 个指数 × 2~3 个日期的点值，未取全历史；`vol` 单位为手（1 手 = 100 股） | 2026-11-15 | 成交量换算已验证：S-A 中证1000 2026-11-09 = 19,233,413,800 股 vs S-C `vol` = 192,334,138 手 × 100 |
| **S-D** | 知藏知识库 | MCP `search_knowledge` / `read_note` / `list_notes` | MCP | **可用但无命中** | 只读；本轮检索该篇原文未入库（库内 37 篇量化笔记均为其他文章） | 2026-11-15 | `02-data-contract.md §1` S-D 行 |
| **S-E** | 乐咕乐股赚钱效应 | `akshare.stock_market_activity_legu` | Python | **部分可用** | **只有当日截面**（12 行 item/value），无历史序列 → 不满足历史检验；「指数涨、个股跌」因此改用指数层面横截面近似 | 2026-11-15 | WORK-404 工作目录下的 `data/breadth_legu_market_activity.csv`（12 行；**未入库**——`data/` 属 gitignore 范围） |
| **S-F** | 网易财经 CSV | `https://quotes.money.163.com/service/chddata.html` | Python | **未取到** | 稳定返回 `HTTP 502 Bad Gateway`，无法作为成交额备选源 | 2026-11-15 | `02-data-contract.md §1` S-F 行原文记录 |
| **S-G** | 微信公众号原文 | `https://mp.weixin.qq.com/s/c_N2gd4xZg6LSQWYmli7RQ` | harness `web_fetch` / MCP `fetch` / 真实 Chrome | **可用（需真实浏览器）** | 前两条路线被拦：落到 `wappoc_appmsgcaptcha` 验证页 / robots.txt；**只有真实 Chrome 渲染能取到全文**（41 段 2863 字） | 2026-11-15 | WORK-404 工作目录下的 `raw_snapshot.txt`（22608 字节，真实 Chrome 渲染快照；**未入库**）；标题/署名/时间与评论一致 |
| **S-H** | 原文配图 CDN | `https://mmbiz.qpic.cn/...` | Python 多 UA 探测 | **未取到真身** | 所有 UA（含 MicroMessenger）均返回同一张 48864 字节占位图（sha256 前缀 `96d03490381006e3`）；原文图表数据不可读 | 2026-11-15 | `02-data-contract.md §1` S-H 行；`article_images/*.png` 实为占位图 |
| **S-I** | 新浪行情（akshare 后端） | `akshare.stock_zh_index_daily(symbol="sh000852")` | Python（akshare 1.20.40） | **可用**（独立交叉校验源） | ① 只能按**代码**取，接口内部再转新浪；② 返回**全历史**（sh000852 约 2936 行、sh000510 约 5309 行），需要自截窗口；③ 字段为 `date/open/high/low/close/volume`，**无成交额**；④ `volume` 单位未在文档中写明，跨源比较前需先验 | 2026-11-16 | 与 S-A 同日同口径交叉校验：**131 / 131 一致**（窗口 **2026-05-06~2026-11-13**，即 S2 快照窗口），最大绝对差 **0.005**、平均 **0.0025**（源A 落盘 2 位小数 vs 新浪原始精度，在四舍五入半格内）。ARM B 复验段另在 **126 天**窗口（2026-05-06~2026-11-06）上做过一次同口径比对，数值见 `experiments/WORK-413-S2/armB_result.json`，口径对照见 `docs/second_article_trial.md` §九 |

---

## 2. 指数级：12 条代码的实际取数结果

| # | 指数 | 代码 | 风格标签 | 可用性 | 限制 | 采样日期 | 证据（行数 / 区间 / sha256 前 12 位） |
|---|---|---|---|---|---|---|---|
| 1 | 上证指数 | `sh000001` | 全市场 | 可用 | — | 2026-11-15 | 451 / 2025-01-02~2026-11-13 / `7e1af260c85f` |
| 2 | 深证成指 | `sz399001` | 全市场 | 可用 | — | 2026-11-15 | 451 / 同上 / `d37dd1b89903` |
| 3 | 上证50 | `sh000016` | 大盘价值 | 可用 | — | 2026-11-15 | 451 / 同上 / `3c8e8da56053` |
| 4 | 沪深300 | `sh000300` | 大盘 | 可用 | 基准指数 | 2026-11-15 | 451 / 同上 / `2f0dc93aeedf` |
| 5 | 中证500 | `sh000905` | 中盘 | 可用 | — | 2026-11-15 | 451 / 同上 / `70eb878da64c` |
| 6 | 中证1000 | `sh000852` | 小盘 | 可用 | — | 2026-11-15 | 451 / 同上 / `1cb1dad62421` |
| 7 | **中证2000** | `sh932000` | 超小盘 | **未取到** | **S-A 无该代码**（见坑 §3.4）；仅用 S-C 补了两个月末点（09-30 / 10-30），**未构造日频序列**，不能进净值曲线 | 2026-11-15 | manifest `error: "ValueError: empty kline for sh932000: keys=['day','qt','mx_price','prec','version']"` |
| 8 | 国证2000 | `sz399303` | 超小盘 | 可用 | 与中证2000 是**不同指数**，不可互换 | 2026-11-15 | 451 / 同上 / `55010ccf4be1` |
| 9 | 创业板指 | `sz399006` | 科技成长 | 可用 | — | 2026-11-15 | 451 / 同上 / `c40700c85025` |
| 10 | 科创50 | `sh000688` | 科技成长 | 可用 | — | 2026-11-15 | 451 / 同上 / `c0143198defc` |
| 11 | 中证全指 | `sh000985` | 全市场 | 可用 | 全市场成交额代理的载体 | 2026-11-15 | 451 / 同上 / `63996dbd7612` |
| 12 | 中证红利 | `sh000922` | 防御 | 可用 | — | 2026-11-15 | 451 / 同上 / `260b82d3b7c6` |
| 13 | **中证A500** | `sh000510` | 中盘 | 可用 | 2026-11-16 新增；腾讯源**有**该代码（与中证2000 的情况相反，别混为一谈）；S-I 新浪后端亦可用 | 2026-11-16 | 131 / 2026-05-06~2026-11-13 / `4b81736c68f8`；快照见 `experiments/WORK-413-S2/snapshot_manifest.json` |

**可用条数**：11 / 12（`sh932000` 缺失）。**S-B 东财**：0 / 12（全部限流跳过）。**S-I 新浪**：可作 S-A 的独立交叉校验源（131/131 一致）。

---

## 3. 踩过的六个坑（省下的是别人的时间，3.1–3.6）

### 3.1 东财 push2 对本机 IP 限流
- **现象**：S-B 的 12 次调用全部失败，报 `Push2RateLimitError` / `RemoteDisconnected`。
- **根因**：按指数批量调用触发 push2 的同 IP 频控。
- **已验证的处置**：`scripts/fetch_market_data.py` 对东财做 **1 次 preflight** 探活，失败则整段跳过（12 条记录写成 `skipped`），**不再空跑 12×45 秒重试**——这一步把一轮空跑压到约 1 次请求。
- **对本仓库的影响**：成交额 `amount` 取不到 → 全市场成交额改用**代理口径** `volume × close`，其绝对水平系统性高于真实值 **3.3–3.6 倍**，只能用于趋势比较（详见 `metric_definitions.md` §6 成交额代理口径）。
- **下一次实验怎么做**：**不要重试东财**。要么换出口 IP，要么继续用代理口径并写明倍数。

### 3.2 乐咕只有当日截面
- **现象**：`akshare.stock_market_activity_legu` 返回 12 行 `item`/`value`，是**今日快照**，没有历史序列。
- **根因**：该接口设计上只提供当日赚钱效应指标。
- **已验证的处置**：需要历史涨跌家数 → 改用**指数层面横截面**（大盘 vs 超小盘）近似「指数涨、个股跌」，并明确声明这是近似、不是个股涨跌比例。
- **下一次实验怎么做**：要「多少比例个股下跌」这类结论，乐咕给不了，**记为未取到**，不要用指数横截面冒充分解度更高的指标。

### 3.3 网易 502
- **现象**：`https://quotes.money.163.com/service/chddata.html` 稳定返回 `HTTP 502 Bad Gateway`。
- **根因**：服务端故障（非本机网络问题）。
- **已验证的处置**：放弃该源作为成交额备选。
- **下一次实验怎么做**：先花 1 次请求探活；连续 2 次 502 即放弃，不要连续重试。

### 3.4 腾讯缺中证2000
- **现象**：`param=sh932000` 返回 `empty kline`，返回体只含 `['day','qt','mx_price','prec','version']` 键。
- **根因**：该代码在腾讯行情接口上不存在（与 `sh932000` 在东财/Tushare 的代码体系不同）。
- **已验证的处置**：中证2000 只保留 S-C 的两个月末点（2026-09-30 = 3107.2648、2026-10-30 = 2616.7385），**不构造日频序列**；画图用**国证2000（`sz399303`）**替代，但正文必须写明两者是不同指数。
- **下一次实验怎么做**：需要中证2000 日频 → 直接走 S-C（Tushare `932000.CSI`），并预判只有月末点可用。

### 3.5 腾讯的日期格式坑（2026-11-16 实测补记：这是最贵的一个坑）
- **现象**：`param=sh000510,day,20260501,20260605,640,qfq` 返回 **HTTP 200**，但 body 只有 **40 字节**：`{"code":0,"msg":"param error","data":[]}`。
- **危险之处**：**HTTP 状态码是 200**，不抛异常，很容易被误判成「该指数没有数据」。本次对照实验里，这条假失败把「腾讯缺中证A500」写成了结论——**结论是错的**：换成 `YYYY-MM-DD` 格式后 `sh000510` 正常返回 126 行。
- **已验证的处置**：`param` 的日期**必须带连字符**。解析结果时**必须判 `data` 是否为空**，不能只看状态码。
- **代价实测**：一次探测请求（0.16 秒）+ 一次判断返工。低成本，但**结论方向会错**，所以比慢更贵。

### 3.6 新浪后端是可用源（2026-11-16 新增）
- **现象**：`akshare.stock_zh_index_daily(symbol="sh000510")` → 5309 行；`sh000852` → 约 2936 行。
- **价值**：与 S-A 腾讯**不同出口 IP**，可直接做跨源交叉校验——实测同日同口径 **131/131 一致**（最大绝对差 0.005、平均 0.0025，在 2 位小数舍入半格内）。
- **限制**：只按代码取、返回全历史（需自截窗口）、无成交额字段、`volume` 单位未在文档写明。
- **下一次实验怎么做**：需要跨源独立校验时优先用新浪，不必再去找第四个源；但**必须先过交叉校验**才能进登记表。

---

## 4. 跨源交叉校验结论（S-A vs S-C）

本轮存在**两个不同的「9」**，口径不同、不可混用。对外表述必须说清指哪一个。

### 4.1 脚本层：`metrics.json → cross_check`（9 个，两侧都有值）

| 指数 | 日期 | 源A 收盘 | 源C 收盘 | 绝对差 | 判定 |
|---|---|---|---|---|---|
| 中证1000 | 2026-09-30 | 7298.82 | 7298.8208 | −0.0008 | 一致 |
| 中证1000 | 2026-10-30 | 6161.23 | 6161.2275 | +0.0025 | 一致 |
| 中证1000 | 2026-11-13 | 6357.26 | 6357.2582 | +0.0018 | 一致 |
| 沪深300 | 2026-09-30 | 4357.62 | 4357.6155 | +0.0045 | 一致 |
| 沪深300 | 2026-10-30 | 3985.50 | 3985.5010 | −0.0010 | 一致 |
| 中证全指 | 2026-09-30 | 5648.25 | 5648.2517 | −0.0017 | 一致 |
| 中证全指 | 2026-10-30 | 4952.50 | 4952.4953 | +0.0047 | 一致 |
| 国证2000 | 2026-09-30 | 9648.50 | 9648.5035 | −0.0035 | 一致 |
| 国证2000 | 2026-10-30 | 8171.97 | 8171.9678 | +0.0022 | 一致 |

- **9 / 9 一致**，最大绝对差 **0.0047**（判据：≤ 0.005，源A 落盘 2 位小数 vs 源C 原始精度，差在四舍五入半格内，相对差 ≤ 约 1.2e-6）。
- 这 9 个观测**全部两侧都有值**，因此每个都构成真正的跨源比较。

### 4.2 数据契约 §5 表层：9 行，其中 7 行两侧都有值

`02-data-contract.md §5` 的表按「同日观测」列出 9 行，但其中 2 行是**中证2000（932000.CSI）**，源A 无该代码 → **仅源C 单侧**，不存在「一致与否」的问题。

| 口径 | 总行数 | 两侧都有值 | 仅单侧 | 可比一致率 |
|---|---|---|---|---|
| 数据契约 §5 表 | 9 | **7** | 2（中证2000） | **7 / 7** |
| `metrics.json → cross_check` | 9 | **9** | 0 | **9 / 9** |

两个集合的成员也不同：§5 表的两行单侧是**中证2000**；脚本的 9 行里那两行是**国证2000**（源A 有数据，`sz399303`）。

> **口径提醒**：写「9/9 一致」时必须带限定语——「9 个**两侧都有值的**同日观测」；写「7/7」时必须指明是「与数据契约 §5 表重合的 7 个」。不限定就出现两种读法，本轮验收即因此提出过表述异议。

---

## 5. 下一次实验的取数探测清单（照此顺序，别再乱试）

1. 查本表 §2 → 需要的指数代码、风格标签、已知缺失，直接抄。
2. 查本表 §3 → 已知失败的源**直接跳过**，不要重试。
3. 需要新源 → 按 §5 顺序试，**每个源先探活再放量**（≤5 行样例 → 确认字段与单位 → 才取窗口）。
4. 成交额类需求 → 默认走代理口径并标注倍数；除非换 IP 重试东财成功。
5. 取完 → 落 `data/<EXP>/*.csv` + `experiments/<EXP>/snapshot_manifest.json`（含 sha256）。

---

## 6. 行业数据（付费源）可用性登记 —— WORK-535

> **作用**：把「哪些行业数据要花钱、哪家能拿到、官网价格公开与否、免费替代源在哪」固定下来，
> 避免下一单重复探测。**本节只登记可用性与价格出处，不含任何购买决策。**
>
> **采样日期**：2026-11-23 ｜ **探测方式**：本机 `urllib` + 浏览器 UA 探测；`web_fetch` 与子代理复读。
> **状态口径**：`可直连` = 本轮实测 HTTP 200；`被限流` = 429/403（需真实浏览器）；`不可达` = DNS 失败或连接被拒。

### 6.1 库内 URL 的修正（下次取数前先改用这一列）

| 原 `data_catalog` URL | 实测 | 正确地址 |
|---|---|---|
| `https://www.esia.eu/` | 200，但页面是**域名停放售卖页**「esia.eu is for sale!」 | `https://www.eusemiconductors.eu/esia/industry-statistics/wsts` |
| `https://web.csia.net.cn/newsinfo/` | **404**（列表页需带文章 id） | `https://web.csia.net.cn/newsinfo/<id>.html`（例 `11310429`、`11304672`） |
| `https://www.fda.gov/drugs` | **404**，且口径错位（FDA 药品审评 ≠ BIS 出口管制） | `https://www.federalregister.gov/agencies/industry-and-security-bureau` |
| `https://www.ifpma.org/publications/data-center/` | **404** | `https://www.ifpma.org/news-and-resources/#publications` |
| `https://www.iea.org/reports/global-ev-outlook-2024` | 200，但为**过期版本** | `https://www.iea.org/reports/global-ev-outlook-2026`（CC BY 4.0） |
| `https://ease-storage.eu/` | 301 → | `https://energystorageeurope.eu/` |
| `https://eurometaux.eu/` | 301 → | `https://european-metals.eu/` |
| `https://www.oica.net/` | 跳到 | `https://oica.net/production-statistics/` |
| `https://www.imf.org/en/Publications/WEO` | 403 | `https://www.imf.org/en/Publications/WEO/weo-database` |
| `https://www.solarpowereurope.org/insights/outlooks` | **404** | `https://www.solarpowereurope.org/`（首页，实测 200） |
| `https://data.jrc.ec.europa.eu` | Request Rejected | EU CRMA 战略项目页 |

### 6.2 付费源的官网可达性与价格公开性

| 数据商 | 官网状态 | 公开价格？ | 价格出处 |
|---|---|---|---|
| SEMI 国际半导体产业协会 | `semi.org` 403、`store-us.semi.org` **429** | **有**（索引副本） | EMDS `$3,350/$7,050`；Fab Forecast 订阅 `$7,550/$11,100`；Fab Forecast Premium `$10,300/$15,000`；Historical WWSEMS `$3,050/$4,100` |
| Wood Mackenzie | 可直连 | **有**（报告单份价） | US Energy Storage Monitor `$5,000`；Global copper mine cost summary `$6,750`；ES battery component `$5,990`；Europe LCOE 2026 `$9,990`；US Solar Market Insight `$7,500` |
| ESIA / WSTS | 可直连 | **有** | Full WSTS Package `€ 11,500`；Blue and Green Books `€ 10,500`（年费） |
| SNE Research | 可直连 | **有**（报告单份价） | Global EV & Battery Quarterly Forecast `$15,000`；Global xEV market and Battery supply & demand outlook `$5,000`；Monthly Tracker 订阅费未公开 |
| TrendForce 集邦 | 可直连 | **有** | Silver `USD 5,000/年`（促销 `USD 4K/year`）；Gold `USD 11,000`；DRAM Platinum `USD 18,000`；Memory Platinum `USD 30,000`；Memory Diamond `USD 55,000` |
| 赛迪顾问 CCID | `ccidgroup.com` 可直连；`mtx.cn` 需 hash 路由 | **有** | `api.mtx.cn/report?command=detail&report_id=…` 返回 `"price": 10000.0`（元/份） |
| Benchmark Mineral Intelligence | 可直连 | **无** | 六个产品页 grep 命中 0 个价格串；`/store`、`/shop`、`/pricing` 全 404 |
| S&P Global Commodity Insights | **全域 403**（Akamai） | **无** | 未观测到任何价格 |
| IQVIA | 可直连 | **无** | 已核 `/sitemap`，全站无价目表 |
| 米内网 Menet | 可直连 | **无** | 产品页只有「咨询价格」「申请试用」 |
| BNEF | `about.bnef.com` 可直连；`/data/` 403 | **无** | 订阅制 |
| 中国医药工业信息中心 | `yytj.org.cn`、`cphiic.com` 可直连 | **无** | 征订页无金额 |
| 高工锂电 / GGII | **不可达**（`gg-lb.com` DNS 失败、`gaogong123.com` TCP 拒绝、`ggii.com` 超时） | **无** | 无法核价 |
| SMM / CNESA / EASE / SolarPower Europe / Eurometaux / IEA | 首页可直连，明细需登录 | **无** | 订阅制 |

### 6.3 已确认可直连的免费源（先查这些，再考虑买）

IRENA（Renewable Capacity Statistics）｜ IFPMA（医药市场统计）｜ EFPIA（医药市场统计）｜ CMS（Medicare 与处方药统计）｜
中国半导体行业协会 CSIA（单篇文章）｜ 中国有色金属工业协会（景气指数，免费全文）｜ 巨潮资讯网 CNINFO ｜
OICA（世界汽车产量）｜ 欧盟 JRC（工业与能源数据集）｜ UNIDO INDSTAT ｜ ICSG + LME/SHFE/COMEX（交易所库存）｜
US Federal Register（BIS 规则与清单）｜ SHFE（免费日/周/月/年数据）｜ World Bank Pink Sheet（月度 USD/t 锂/镍/钴/铜/铝）｜
SEMI 新闻稿（设备账单总额与分地区表）｜ SIA 月度销售统计｜ TrendForce DRAM/NAND 现货价页（当次 session 高/低/均价与涨跌）｜
CNESA 白皮书摘要 ｜ 电池网 CBEA 月度信息 ｜ McKinsey Our Insights（多数文章免注册可读）｜ IEA Global EV Outlook 2026（CC BY 4.0 PDF）

### 6.4 踩坑记录

1. **「有公开价」≠「价格就是订阅价」**：WoodMac 的公开价全部是**单份 PDF 报告**，其订阅（Lens / Lithium Product Suite / EV & Battery Supply Chain Service）一律无公开价。
2. **McKinsey 不卖结构化数据**：价值链与利润率分析以 Report/Article 形式发布，页面元数据 `"IsPageRestricted":"false"`、`enableRegWall:false`，**多数文章免注册可读** → 属「免费」，不是「需询价」。
3. **TrendForce 的报价单位是每颗芯片规格**（如 `DDR5 16Gb (2Gx8) 4800/5600` → `72.00`），**不是 USD/GB**；换算前不能与本库口径并列。
4. **SNE 不覆盖 PCS 出货量**：其储能条目是 GWh 能量口径（`Global ESS Shipments Reach 461GWh in 1H26`），不是本库要求的 GW 功率口径。
5. **IQVIA 没有「产业链各环节利润率 / 研发投入强度」产品页**；WoodMac、BNEF 同样没有「五环节利润率」产品页 —— 这三条指标的覆盖度都存疑，须向销售确认。
6. **WoodMac 的免费稿 ≠ 免费数据**：WoodMac 的 PCS 份额、储能 EPC 成本区间、BESS 集成商排名都是**免费新闻稿**，底下的付费报告无公开标价。
7. **SNE 不覆盖半导体产业链价值分布**：官网自有检索 EN（`Semiconductor`）与 KO（`반도체`）**均返回空报告列表**，「Emerging Industry」目录只有氢能、SMR、人形机器人、钠离子电池、回收、BMS → 覆盖度记「不覆盖」、建议「不买」。**PCS 与半导体这两条否定性结论，都是靠官网检索为空拿到的，不是印象。**
8. **CNESA 免费文的单位是 元/kWh，不是 元/Wh**：实测 `cnesa.org/information/detail/?column_id=1&id=7798`（HTTP 200）页内为 2025 年口径 —— 2h EPC 中标均价 **1,043.82 元/kWh**（同比 −13.04%）、4h EPC **935.40 元/kWh**（同比 −8.19%）、LFP 系统（不含用户侧）采购中标价区间 **391.14–913.00 元/kWh**；页内「元/Wh」**0 处**。本库口径是元/Wh，**换算须 ÷1000**，且原文自述「根据 CNESA DataLink 全球储能数据库的**不完全统计**」。
9. **协会里也有公开标价，别一概当「无价」**：ICSG Copper Bulletin 年刊 **€600（会员）/ €1,200（非会员）** —— 本单唯一的协会类公开标价，可作询价基准；但它给的是精炼铜产量与用量，不含交易所注册仓单。
10. **SMM 的公开行情页有单日报价，但不是订阅价**：行情页渲染为「未登录」，值藏在 HTML 属性里（示例 2026-11-23 电池级碳酸锂 low 88,700 / high 92,300 / avg 90,500 元/吨）；`data-pro.smm.cn/terminal` 实测 200 但**无任何价格数字** → 订阅价未公开。

### 6.5 P0 采购面结论索引（结论本身在 docs/，此处只做指针）

- 主清单 `docs/procurement_plan.csv`（**84 行** = 23 个「需采购指标 × 数据商」+ 61 个「部分免费指标 × 付费源」；12 列；0 空值）
- 决策摘要 `docs/procurement_brief.md`（覆盖度排序 + 贪心集合覆盖算式 + 「先买哪 2–3 家」+ 横向对比 + 逐家缺口）
- 询价模板 `docs/procurement_inquiry_templates.md`（13 家）
- 证据底稿 `docs/procurement_evidence_log.md`（价格分级 / 已修死链 / 停手登记 / 本单 8 处坑）
- 可复现资产与独立复算闸门 `experiments/WORK-535/`、`audit/recompute_work535.py`（**40 项检查、blocking 0 → PASS**）

**取数前先看 §6.2**：Benchmark / S&P Global / IQVIA / 米内网 / BNEF / 医药工业信息中心 / GGII 的价格**官网不公开**，必须询价；SEMI 的价格**本机被限流（429）**，需人工浏览器复核。

