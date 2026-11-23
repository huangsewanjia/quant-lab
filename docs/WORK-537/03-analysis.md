# WORK-537 · 阶段 3 分析（宏观行业研究员 + 量化研究员）

输入=docs/WORK-537/02-data-contract.md、docs/WORK-537/02-data.csv（29 渠道实测）、raw/foreign_research/ 下 9 篇正文样例
本阶段产出什么=可抓性事实表、评级/目标价可得性的事实与推断、实测更新频率、采集频率建议、与 WORK-536 的合并口径
本阶段工具调用上限=抓正文 28 次（13 渠道 × robots+正文 = 26，另 DB 重取 2 次）；阶段内文本分析 0 请求

> 纪律：本文件区分**事实**（实测值，带测量日期/快照）与**推断**（带成立条件）。抽样为单页快照，
> 不代表长期可用性。

## 1. 事实层：29 个渠道的可抓性实测（测量日期 2026-11-23，UTC+8）

| 可抓性 | 数量 | 渠道 |
|---|---|---|
| 直连（列表页可解析出标题+日期） | **13** | Goldman Sachs Insights、Morgan Stanley Ideas、J.P. Morgan Insights、BofA Institute、HSBC GBM Insights、Macquarie Insights、dbInsights(德银)、CNBC Investing、SEC EDGAR(公开 JSON)、慧博投研、发现报告、格隆汇、巨潮资讯 |
| 需JS（列表未在 HTML 里渲染） | 8 | Citi Institute、Barclays Insights、Nomura、Daiwa、NYSE、萝卜投研、东方财富研报中心、雪球 |
| 403 | 1 | UBS Insights（Akamai 边缘拒绝，标题 `Access Denied`） |
| 需登录（落地登录页） | 1 | Bernstein Research（302 → `/brweb/Public/Login.aspx`） |
| 本机出口不可达 | 4 | Reuters、Bloomberg、FT、WSJ |
| robots.txt 禁止 | 1 | Barron's（`barrons.com/robots.txt` 对 `*` 为 Disallow） |
| robots.txt 不可读(HTTP 403) | 1 | FINRA |

**建议分布**：采 13 / 观察 14 / 不采 2（Barron's、FINRA 为合规侧不应抓）。

**分类别命中率**：投行官网 7/13 直连；财经媒体 1/6 直连（另 4 个是本机出口问题，不等于站点拒绝）；
监管披露 1/3 直连；聚合与社区 4/7 直连。

**与大管家 2026-11-23 基线的差异（全部已复核）**：

| 渠道 | 大管家基线 | 本轮实测 | 差异原因 |
|---|---|---|---|
| goldmansachs.com/insights | 200 | 200 直连 | 一致 |
| morganstanley.com/ideas | 301 | 301 → 200 直连 | 一致（301 落地页可解析） |
| jpmorgan.com/insights | 200 | 200 直连 | 一致 |
| ubs.com/.../insights.html | 403 | 403 | 一致 |
| citigroup.com/global/insights | 301 | 301 → 200，但列表需 JS | 基线只看状态码，未验证列表是否可解析 |
| BofA | 404（入口需重找） | 200 直连（institute.bankofamerica.com） | `/insights/` 超时、`/insights.html` 404，第三个候选入口可用 |
| nomuraholdings.com | 403 | 200 需JS（nomura.com/insights 301 到 nomuraholdings 首页） | 403 的具体路径与基线不同 |
| HSBC | — | 200 直连（business.hsbc.com/en-gb/insights） | `www.hsbc.com/insights` 已 404（基线未测） |
| Deutsche Bank | — | 200 直连（dbresearch.com dbInsights） | `db.com/insights` 等 3 个路径全部 404 |
| Barron's | — | robots 禁（**未发正文请求**） | robots.txt 对 `*` 为 Disallow |

## 2. 事实层：正文样例（Top-5 外资投行 + 4 个补充，全部 >500 字符、sha256 已核）

| # | 渠道 | 文章标题 | 页面日期 | 正文字符 | sha256 前 12 位 |
|---|---|---|---|---|---|
| 1 | Goldman Sachs Insights | The Global Economy Is Forecast to Grow 2.8% in 2027 as Oil Prices Ease | 2026-11-13 | 14,225 | d0257d795ca1 |
| 2 | Morgan Stanley Ideas | Healthcare Leaders Discuss the Forces Reshaping the Industry | 2026-10-05 | 26,064 | 852b53434889 |
| 3 | J.P. Morgan Insights | Global Government Bond Yields Hit Multi-Decade Highs | 2026-11-03 | 18,980 | bc638af32237 |
| 4 | dbInsights（德银） | Sea change: the market implications of the threat to the maritime commons | 2026-11-12 | 7,784 | 250a37bcd38b |
| 5 | HSBC GBM Insights | Financing the next wave of electrification | 2026-10-30 | 2,742 | 453d41597fac |
| 补 | Citi Institute | Tokenization 2030 | 2026-06-01 | 4,017 | 2065719ffac8 |
| 补 | CNBC | AMD stock dives on short-seller report of alleged China exposure | 2026-11-19 | 5,022 | 9ce7d9da8a1c |
| 补 | 发现报告 | 艾奇在线广告研究院《2026中国教育行业GEO与SEO优化研究报告》 | 2026-11-16 | 4,573 | ce799e739047 |
| 补 | 格隆汇 | 研报掘金丨国盛证券：首予富特科技「买入」评级 | 2026-11-19 | 5,800 | 37966e636803 |

落盘路径：`/srv/share/quantdb/raw/foreign_research/<channel>/<date>/<slug>.md` + 同目录 `meta.json`。
落盘的 5 个**不合格候选**（抓到的是栏目页而非文章）保留在 `_rejected/`，作为失败实验证据。

## 3. 核心问题：公开层能不能拿到评级/目标价

### 3.1 事实

- **列表页全文扫描**（29 个渠道，去标签后可见文本全文）：
  13 个直连渠道中 **11 个**全文 0 次出现「price target / target price / overweight / underweight /
  评级 / 目标价 / 增持 / 减持」等字样；2 个出现字样的渠道是 **格隆汇**（增持、目标价、评级）与
  **巨潮资讯**（减持），字样来自**转载/公告原文**。
- **正文全文扫描**（9 篇样例）：
  5 篇外资投行文章（GS / MS / JPM / DB / HSBC）+ Citi + CNBC + 发现报告 → **命中 0 次**。
  唯一疑似命中是 JPM 样例中的 1 处 `underperform`，上下文为
  “Treasury yields [are] retracing to multi-decade highs, and **underperforming** other developed market
  government bond markets”——是**债券市场表现**，不是个股评级。
- **格隆汇样例**命中 22 次（评级×14、增持×5、目标价×3），全部指向 **国盛证券**（中资券商）
  对富特科技的首次覆盖——即**中资研报的转述**，不是外资投行给出的评级。
- 东方财富研报中心列表页命中「减持、评级」二字，但该页为 JS 渲染，正文未取到，**不作为证据**。

### 3.2 推断（带成立条件）

- **成立条件**：所测 9 篇文章与 29 个列表页代表各渠道的公开层形态。
- **结论**：**外资投行在公开层给出的评级/目标价，实测为「基本不可得」**；
  公开层可稳定获得的是**主题观点、宏观与行业判断**（如 GS 的全球增长预测、JPM 的国债收益率判断、
  DB 的海洋公地研究、HSBC 的电气化融资）。
- **边界**：公开层**偶有**个股评级出现在**中资平台的转述**里（格隆汇/慧博/发现报告一类），
  这些是**二次转述**、有时效与准确性风险，不能当作外资投行的原始评级使用。
- **不成立的推论**：不能据此断言「外资投行不发评级」；事实是它们不在**可公开机器采集**的页面上发。

## 4. 事实层：更新频率（实测，2026-11-23 单页快照）

| 渠道 | 列表页可见日期数 | 相邻间隔中位数 |
|---|---|---|
| CNBC Investing | 6 | 1 天 |
| 慧博投研 | 20 | 3 天 |
| 发现报告 | 22 | 3 天 |
| SEC EDGAR（Apple 近 1000 份申报） | 595 | 4 天 |
| HSBC GBM Insights | 7 | 5 天 |
| J.P. Morgan Insights | 5 | 7 天 |
| dbInsights（德银） | 9 | 7 天 |
| BofA Institute | 10 | 30 天 |

其余 21 个渠道可见日期不足 3 个，**未实测**（CSV 中如实写「未实测」）。
**注意**：可见日期里混有事件/展望日期，间隔中位数是**描述性**指标，不等于发布频率承诺。

## 5. 采集频率建议（推断 + 依据）

| 集合 | 建议频率 | 依据 | 每周请求量 |
|---|---|---|---|
| 13 个直连渠道（投行 + 中文聚合 + SEC） | **每周一次增量** | 实测间隔中位数 3–7 天的占多数（HSBC 5d、JPM 7d、db 7d、慧博/发现报告 3d） | 13 渠道 × (robots+列表) ≈ 26 次/周 |
| CNBC、SEC EDGAR | **每日一次增量**（可选） | 实测 1 天 / 4 天 | ≈ 2–4 次/天 |
| 需 JS / 403 / 需登录的 10 个 | **人工看**，不跑自动采集 | 机器采不到；robots 禁的 2 个不采 | 0 |
| 本机出口不可达的 4 个 | 换出口后重测，不纳入本机流水线 | 本机网络层不可达，非站点策略 | 0 |

**总量控制**：按每周一次，投行与中文层稳定在 **≤30 次/周**，远低于本单的 200 次上限。

## 6. 与中资券商研报（WORK-536）合并成一张「研究内容表」

两层字段高度重合，**主键建议统一为 `(机构, 标题, 发布日期, 原文URL)`**，合并后增加
`语言 / 来源渠道 / 内容类型 / 是否含评级目标价 / 是否二次转述 / sha256 / 采集时间`：

| 字段 | 外资公开层（本单） | 中资层（WORK-536） | 合并后语义 |
|---|---|---|---|
| 机构 | 投行/媒体 | 券商 | 同一列，值域不同 |
| 标题 | 多为英文主题标题 | 中文标题 | 同列 |
| 发布日期 | 页面自身标注 | 研报页标注 | 同列，**时点口径统一为页面标注** |
| 原文 URL | 英文站点 | 中文站点 | 同列 |
| 内容类型 | 主题/宏观/行业观点 | 个股/行业深度 | 同列，**外资层以观点为主** |
| 是否含评级/目标价 | 实测基本「无」 | 多为「有」 | 同列，**两层互补** |
| 二次转述标识 | 否（原文） | 部分平台是转述 | 同列 |

**合并后的净效果**：一张表同时覆盖「观点层（外资原文）+ 估值结论层（中资研报）」；
外资层补**全球视角与宏观框架**，中资层补**评级/目标价与 A 股个股覆盖**。
**外资层不承担估值结论的补充责任**——实测证明它给不了。

## 7. 反证与冲突来源

- **反证 1**：HSBC GBM 列表页有「Global Research」条目（搜索结果中带 Global Investment Research 标签），
  说明**部分**外资投行的 Global Research 内容在公开页上以「文章」形式发布；
  若其正文里带评级，本单 1 篇样例未覆盖到。**成立条件**：需逐篇扫描 Global Research 条目正文。
  → 已列为后续动作（见 §8）。
- **反证 2**：格隆汇证明**中资平台的转述里含完整评级+目标价**（国盛证券案例），
  即「公开层完全没有评级」的说法过强。**成立条件**：转述时点、原文准确性无法核验。
- **冲突来源**：同一渠道「直连」与「需JS」并存的情况——Citi 的**入口列表**需 JS，
  但其**文章详情页**可直接抓（样例 `Tokenization 2030` 即取自详情页，4,017 字符）。
  CSV 的「可抓性」列测的是**入口列表页**，已在备注中标出该差异，避免被误读为整站不可采。

## 8. 未取到 / 未验证（明确列出）

1. **Reuters / Bloomberg / FT / WSJ 的站点侧行为未测**：本机出口不可达（DNS 返回污染地址 + 无 IPv6 路由），
   连 robots.txt 都取不到；`fetch` 服务侧同样失败。**不能**据此说这些站点拒绝机器访问。
2. **UBS 的 403 性质未定位**：Akamai 边缘拒绝（标题 `Access Denied`），换正常浏览器或换出口可能 200。
3. **HSBC Global Research 条目正文未逐篇扫描**：是否含评级/目标价**未验证**。
4. **Citi / Barclays / 东方财富研报中心 / 雪球 / 萝卜投研 / NYSE 的列表内容未取到**（JS 渲染），
   其文章详情页是否可直连**只验证了 Citi 一例**。
5. **Bernstein 的研究内容完全未取到**（落地登录页）；未验证其公开页是否另有可采入口。
6. **发现报告 / 慧博 的研报 PDF 正文未抓**（本单只收公开摘要，不抓 PDF）。
7. **样本量为 9 篇、1 个时间点**：不构成「公开层评级可得率」的统计估计，只能支持「实测 0 命中」这一事实陈述。

## 9. 本阶段工具调用体量（收尾报体量）

- 抓正文：28 次请求（13 渠道样例 26 次 + DB 重取 2 次）。
- 阶段内文本分析：0 请求（全部在本地对已落盘快照做正则/关键词扫描）。
- 进上下文的最大单次返回：单篇正文 ≤ 26,064 字符（Morgan Stanley 样例）。
- 本阶段未新增渠道探测请求（阶段 2 已完成 29 渠道实测）。
