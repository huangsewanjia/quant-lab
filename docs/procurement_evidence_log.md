# WORK-535 · 证据与停手登记

> **工单** WORK-535 ｜ **编制日期** 2026-11-23
> 配套：`docs/procurement_plan.csv`（**109 行**主清单）｜ `docs/procurement_brief.md`（决策摘要）｜ `docs/procurement_inquiry_templates.md`（**14 段**询价模板）
> 本文件是摘要的底稿：**每个数字的出处、每次核对的原始输出、每一处停手**。摘要里的结论都可以在这里追到证据。
> **原始产物**：`experiments/WORK-535/`（脚本 + JSON + txt 原始输出）；**独立复算**：`audit/recompute_work535.py`（43 项检查，blocking 0 → PASS）。
> **采购范围增补**：工单评论「采购范围增补：外资投行研报」要求把研报订阅逐家登记 —— 已落进主清单（25 行「研报通道 × 战略层指标」）、摘要 §六、询价模板 §14 与本文件 §五。

---

## 一、价格核对（工单验收：抽 10 条价格核对）

**方法**（`experiments/WORK-535/verify_prices.py`）：对每一行带货币数字的价格，重新抓取该行引用的 URL，**按数值比对**（`"10,000" ≡ "10000.0"`），数字带千分位必须逐位命中——页面上出现 `$5` 不等于命中 `$5,000`。价格格里写了多个 URL 时，逐个 URL 联合比对。

**结果**：**109 行**里 **23 行带可核对数字**，去重后 **11 组唯一「(价格, URL)」组合**；**7 组在页面上直接命中**，**4 组取不到**（全部是 SEMI，同一个 URL）。

| # | 价格 | URL | 复核结果 |
|---|---|---|---|
| 1 | `$5,000` | `https://www.woodmac.com/reports/power-markets-us-energy-storage-monitor-q3-2026-150505404/` | ✅ HTTP 200，页面命中 |
| 2 | `¥10,000` | `https://api.mtx.cn/report?command=detail&report_id=10022650` | ✅ HTTP 200，接口字段 `"price": 10000.0` |
| 3 | `Silver Membership USD 5,000/年`（含 `USD 4K/year` 促销） | `https://www.trendforce.com/membership` | ✅ HTTP 200，页面命中 |
| 4 | `$15,000` | `https://www.sneresearch.com/en/business/report_view/148/page/0` | ✅ HTTP 200，逐字「Page Price $15,000」 |
| 5 | `€ 11,500` / `€ 10,500` | `https://www.eusemiconductors.eu/esia/industry-statistics/wsts` | ✅ HTTP 200，页面命中 |
| 6 | `$15,000` + `$5,000` | `.../report_view/148/page/0` + `.../report_view/38/page/130` | ✅ HTTP 200（跨 URL，已拆成各自带 URL 的价格格） |
| 7 | 慧博 基础版 1年 `¥1,668` / 2年 `¥2,376` / 专业版 1年 `¥3,996` / 2年 `¥5,592` | `https://www.hibor.com.cn/price` | ✅ HTTP 200，页面逐字命中（标题「价格套餐」） |
| 8–11 | SEMI 四组价格串（EMDS / WFF / WFF Premium / Historical WWSEMS） | `https://store-us.semi.org/collections/market-intelligence` | ⚠️ **3 次 429 限流 + 1 次 200 但价格不在服务端 HTML 里（前端渲染）** → 全部降为 **B 级证据**（该 URL 的搜索引擎索引副本），人工浏览器需复核 |

> **⚠️ 口径说明（必须一起读）**：11 组里有 **4 组没能直连复核**（全部是 SEMI store：3 次 429 限流、1 次 200 但价格在前端渲染、不在服务端 HTML）。这 4 组的价格**不是本轮直接读到的数字**，清单备注与摘要的证据分级表都已标注为 B 级。除此之外，**没有任何一行价格是估算的**；拿不到的写 `未公开，需询价` 并给询价入口。

**其它已公开价（未列入上表，因为同一 URL 下价格串不同、或属报告而非订阅）**：WoodMac `$6,750` / `$5,990` / `$9,990` / `$7,500`；SNE `$5,000`；TrendForce `USD 11,000` / `USD 18,000` / `USD 30,000` / `USD 55,000`；ICSG Copper Bulletin `€600`（会员）/ `€1,200`（非会员）。

**研报通道（§五）的价格核对**：**只有慧博投研是 A 级**（官网 `https://www.hibor.com.cn/price` 实测 200，四个数逐字命中）；发现报告与萝卜投研的价格页虽然**能打开（200）**，但价格由前端脚本渲染，**本机取不到任何数字** → 按「未公开，需询价」登记；萝卜投研的索引副本线索（金卡 ¥1,299/年、银卡 ¥599/年）**单列为 B 级、未直接复核**。

---

## 二、全表「官网价格页URL」可达性体检（52 个去重 URL）

脚本 `experiments/WORK-535/check_plan_urls.py`，原始输出 `experiments/WORK-535/plan_url_status.json`（本轮为加入 5 家研报通道后的全量重测）。

| 状态 | 数量 | 说明 |
|---|---|---|
| **200** | 41 | 直接可开（含 5 家研报通道的价格页/产品页） |
| **403** | 8 | S&P Global（Akamai）、IEA ×3（Cloudflare）、IRENA、CMS、IMF、UNIDO —— **本机被拒，URL 本身存在，人工浏览器可开** |
| **429** | 1 | `store-us.semi.org` 限流 |
| **404** | 0 | 本轮 **0 个 404**（`solarpowereurope.org/insights/outlooks` 上一轮 404 → 已改为首页 `https://www.solarpowereurope.org/`，实测 200） |
| **ERR** | 2 | `gg-lb.com` DNS 解析失败（Errno -2）；`mckinsey.com/featured-insights` 连接超时 |

**验收条款核对**：标「未公开」的行共 **65 行**，其中 URL 未返回 200 的 **18 行**——IEA ×12（S&P Global、GGII、SolarPower Europe、CMS 各若干），**全部是本机被风控拒绝或域名失效，不是链接本身写错**；每一条都在行内备注里写明了状态。

**每一条 CSV 行的备注末尾都带了自己那个 URL 的实测状态**（`【链接实测 200】` / `【链接实测 403：…】` 等），验收条款「标『未公开』的，URL 必须真实存在（能打开）」可**逐行自证**。

**免费替代源里的 URL**（`免费替代源` 列内出现的链接，25 个去重）：**24 个 200**，1 个 403（`semi.org/en/semi-press-release/`）。其余 42 行的免费源链接写在 `官网价格页URL` 列（那些行「本行即免费源」），不在 `免费替代源` 列里重复。

---

## 三、已修正的库内 URL（清单里用的是修正后的地址）

| 原库内 URL | 实测 | 改为 |
|---|---|---|
| `https://www.esia.eu/` | 200，但页面是**域名停放售卖页**「esia.eu is for sale!」 | `https://www.eusemiconductors.eu/esia/industry-statistics/wsts` |
| `https://web.csia.net.cn/newsinfo/` | **404**（列表页需带文章 id） | `https://web.csia.net.cn/newsinfo/11310429.html` 等单篇地址 |
| `https://www.fda.gov/drugs` | **404**，且口径错位（FDA 药品审评 ≠ BIS 出口管制） | `https://www.federalregister.gov/agencies/industry-and-security-bureau` |
| `https://www.ifpma.org/publications/data-center/` | **404** | `https://www.ifpma.org/news-and-resources/#publications` |
| `https://www.iea.org/reports/global-ev-outlook-2024` | 200 但为**过期版本** | `https://www.iea.org/reports/global-ev-outlook-2026`（CC BY 4.0） |
| `https://ease-storage.eu/` | 301 → | `https://energystorageeurope.eu/` |
| `https://eurometaux.eu/` | 301 → | `https://european-metals.eu/` |
| `https://www.oica.net/` | 跳到 | `https://oica.net/production-statistics/` |
| `https://www.imf.org/...`（WEO 数据库） | 路径错位 | `https://www.imf.org/en/Publications/WEO/weo-database` |
| `https://data.jrc.ec.europa.eu` | Request Rejected | 改用 EU CRMA 战略项目页 |
| `https://www.solarpowereurope.org/insights/outlooks` | **404** | `https://www.solarpowereurope.org/` |

> **本单对 `data_catalog` 是只读的**：修正只写进 `data_contracts/data_source_registry.md` §6，**没有回写目录表**。

---

## 四、停手登记（按工单停手条件如实记录，未做绕过）

1. **多数数据商官网需注册/需询价才能看价格**：Benchmark、S&P Global、IQVIA、米内网、BNEF、GGII、SMM、CNESA、EASE、SolarPower Europe、Eurometaux、SNE 的 Monthly Tracker、IEA 的 Data Products —— 一律登记「需注册 / 未公开，需询价」并给出询价入口。
   **本单未注册任何账号、未提交任何询价表单、未代用户联系销售。**
2. **官网不可达（贴状态码）**：
   - S&P Global **全域 403**（Akamai Access Denied）→ 其覆盖度与品种清单**只能来自搜索索引副本，未经我方直接打开验证**；
   - SEMI store **429**（限流）→ 价格降为 B 级；
   - `gg-lb.com` **DNS 解析失败（Errno -2）**，8.8.8.8 亦无 A 记录 → GGII 的现域名**未取到**，询价入口**未取到**；`gaogong123.com` **TCP 连接被拒（Errno 111）**；`ggii.com` **连接超时**；
   - McKinsey 除文章页外直连超时；
   - IEA / IRENA / CMS / IMF / UNIDO / USGS 首页 / S&P Global **403**（需真实浏览器）。
3. **第三方转载的信息不当作官网事实**：GGII 的订购电话/邮箱只在第三方站点见到，**未获官网确认**，因此不据此写任何价格或联系结论。

---

## 五、询价入口的邮箱回环核对（大管家点名项：模板邮箱未验证真实性）

**方法**（`experiments/WORK-535/verify_contacts.py`）：把主清单里出现的每个邮箱，逐个回到**厂商自己的页面**重新抓取，确认页面 HTML 里确实出现该邮箱串。原始输出 `experiments/WORK-535/contact_verification.json`。

**结果：清单里 22 个邮箱，核对 21 个 → 命中 18，未复核到 3**；另 1 个（`research1@gaogong123.com`）是第三方转载域名，按设计不核对（清单里已标「未获官网确认」）。

| 邮箱 | 回环结果 |
|---|---|
| `prices@benchmarkminerals.com` | ❌ **未复核到 → 已从询价入口撤掉**。Benchmark 联系页实测 200，但**页内没有任何邮箱**（是分类表单，第一项即「Using Benchmark prices in contracts」）；sitemap 里也没有第二个联系页。**清单、摘要、询价模板三处已改为「官网联系表单」并写明原因** |
| `press@iea.org` | ❌ 未复核到 —— `iea.org` 全站对本机 **403**（非邮箱不存在），清单已按停手登记处理 |
| `pricegroup@spglobal.com` | ❌ 未复核到 —— `spglobal.com` **全域 403**；清单里本就标注「索引副本，未直接验证」 |
| `tp@wsts.org` / `sh@wsts.org` | ✅ `https://www.wsts.org/65/CONTACT` 与 `https://www.wsts.org/135/263/Disclosure` 实测 200 且页内含这两个邮箱（**WSTS 首页是 JS 渲染，所以第一轮抓不到**） |
| `sales@sneresearch.com` | ✅ `https://www.sneresearch.com/en/about/contact/` |
| `support@woodmac.com` | ✅ `https://www.woodmac.com/about/contact-us/` |
| `service@trendforce.com` / `sr_mi@trendforce.com` | ✅ `https://www.trendforce.com/contact/reporting` |
| `service@ccidconsulting.com` | ✅ `https://www.ccidconsulting.com/contact` |
| `sales.bnef@bloomberg.net` | ✅ `https://about.bnef.com/contact-us/` |
| `baohf@pharmadl.com` / `yaoxl@pharmadl.com` | ✅ `https://www.yytj.org.cn/ContactUs.aspx` |
| `kefu@smm.cn` / `service@smm.cn` / `service.en@smm.cn` | ✅ `https://www.smm.cn/about/contact` |
| `hbzixun@126.com` | ✅ `https://www.hibor.com.cn/`（首页公示） |
| `cs@hufangde.com` / `hfd04@hufangde.com` | ✅ `https://www.fxbaogao.com/`（首页公示） |
| `info@datayes.com` / `team.marketing@datayes.com` | ✅ `https://www.datayes.com/`（通联数据官网公示） |

> **本节的价值**：把「询价入口」从「抄来的邮箱」变成**逐个回环核过、可当场复跑**的证据；核不到的不留，改为可核的表单入口并写明原因。

---

## 六、研报订阅通道（工单评论增补）：实测结果与停手

**为什么要单列**：工单评论指出，用户要的「著名投行行业研究报告」在**公开源里几乎不存在** —— 东财研报接口抽样 900 条、95 家机构，高盛/摩根士丹利/摩根大通/瑞银/花旗/美银/野村/大和**零覆盖**，只有「中信证券经纪(香港)」。所以这一层要么走机构协议、要么走聚合平台，不能指望免费源。

**逐家实测**（脚本 `experiments/WORK-535/probe_research_vendors*.py`，原始输出 `research_vendor_probe.json` / `research_vendor_probe2.json`）：

| 数据商 | 类型 | 官网价格页 URL | 实测 | 公开价格 | 询价入口 |
|---|---|---|---|---|---|
| **慧博投研** | 聚合平台 | `https://www.hibor.com.cn/price` | **200，标题「价格套餐」** | ✅ **A 级**：基础版 1年 **¥1,668**（单月 ¥139）／2年 ¥2,376（单月 ¥99）；专业版 1年 **¥3,996**（单月 ¥333）／2年 ¥5,592（单月 ¥233） | `hbzixun@126.com`（官网首页公示） |
| 发现报告 | 聚合平台 | `https://www.fxbaogao.com/vip-benefits` | **200，但正文为空**（价格由前端脚本渲染，本机未取到数字） | **未公开，需询价** | `cs@hufangde.com`、`hfd04@hufangde.com`（**官网未公开销售邮箱**） |
| 萝卜投研（通联数据） | 聚合平台 | `https://m-robo.datayes.com/membership/open` | **200，标题「萝卜投研会员中心」**，只渲染出「￥ 0」 | **未公开，需询价**（⚠️ 索引副本显示金卡 ¥1,299/年、银卡 ¥599/年 → **B 级，未直接复核，不得当报价**） | 通联数据官网 `https://www.datayes.com/`（200）公示 `info@datayes.com`、`team.marketing@datayes.com` |
| 中金公司研究部 | 中资头部 | `https://www.cicc.com/services/research`（200）＋ 中金点睛 `https://www.research.cicc.com/zh_CN/home`（200） | 业务介绍页逐字：研究团队覆盖「宏观经济、市场策略、固定收益、金融工程、资产配置、股票、大宗商品及外汇」 | **未公开，需询价** | `https://www.research.cicc.com/zh_CN/contact`（200），**无公开销售邮箱** |
| 中信证券研究部 | 中资头部 | `https://www.citics.com/newsite/business/research_sales/research/research_yjfw/` | **200，但正文为空**（前端渲染） | **未公开，需询价** | 站内咨询，**无公开销售邮箱** |
| Goldman Sachs Research | 外资投行 | `https://www.goldmansachs.com/what-we-do/research` | **200**，只给摘要与图表 | **未公开，需询价（量级未知）** | **官网无公开销售邮箱**，走机构授权 |
| Morgan Stanley Research | 外资投行 | `https://www.morganstanley.com/what-we-do/research`（200） | 研究入口 `matrix.ms.com/research` 返回 **Access Verification**（需登录） | **未公开，需询价（量级未知）** | `https://www.morganstanley.com/contact-us`（200，零售客服口径） |
| J.P. Morgan Research | 外资投行 | `https://www.jpmorgan.com/insights/research` | **200**，只给摘要与图表 | **未公开，需询价（量级未知）** | **官网无公开销售邮箱** |
| UBS Evidence Lab | 外资投行 | `https://www.ubs.com/global/en/investment-bank/insights/evidence-lab.html` | ⚠️ **403**，页面未取到 | **未公开，需询价（量级未知）** | `ubs.com/global/en/contact-us.html` 亦 **403** |
| Citi Research | 外资投行 | `https://www.citigroup.com/global/insights/research` | **200** | **未公开，需询价（量级未知）** | `https://www.citigroup.com/global/contact-us`（200，零售客服口径） |
| BofA Global Research | 外资投行 | `https://ideas.bankofamerica.com/` | ⚠️ **DNS 解析失败（Errno -2）** | **未公开，需询价（量级未知）** | 页面未取到 |

**口径纪律（本节最要紧的一条）**：这 5 家能服务的是「产业链利润/价值分布」这 5 条战略层硬缺口，但**研报是 PDF 与图表、不是结构化数据** —— 「各环节利润率」是否真的落在里面**必须索取样张逐页核对**。因此主清单里这 25 行的覆盖度一律记 **「部分」（只到「覆盖行业研究域」）**，建议一律记 **「等」**，不记「买」。

**免费替代与能力差距（逐个来源，按工单硬要求第 3 条列全）**：① **投行官网 Insights 页**（GS / MS Ideas / JPM Insights / Citi Insights；UBS 403、BofA DNS 失败）→ 只有公开摘要与图表、没有正文，且不定时更新；② **监管披露**（巨潮资讯网 CNINFO 公告与定期报告全文 https://www.cninfo.com.cn/ 、交易所与监管机构公告）→ 只有法定披露的原始事实，没有第三方测算的利润率拆分；③ **路透 / 彭博新闻**（https://www.reuters.com/markets/ 、https://www.bloomberg.com/markets/ ）→ 只给结论与当时的市场反应，不给口径定义、不给可复算的历史序列，正文多有付费墙、引用须付费订阅；④ **SSRN** → 学术工作论文口径，与卖方口径不同，且常是未经验证的预印本；⑤ **协会/统计局免费源**（CNESA 白皮书、IFPMA、Eurostat、USGS、CSIA 等）→ 公开频率降级、口径变窄、滞后。**共性差距：研报不可直接入库、不可复算，卖方口径随分析师与样本期变动，跨期不可比。**

**转载红线（硬要求第 2 条）**：外资投行研报合同通常**禁止转载与二次分发**，只限内部阅读；中资研报经聚合平台分发同样受版权约束（港美股研报因版权限制不托管 PDF）。询价模板 §14 已把「① 内部阅读范围与席位 ② 是否允许内部报告引用结论与数字并注明来源 ③ 是否允许图表复算 ④ 合同终止后的留存与销毁」写成必问项。

**如果只能买一家**：**慧博投研基础版 1 年 ¥1,668**（唯一有官网公开标价、可比价、可做预算；外资投行是机构协议，量级未知）。**前提是先要 3–5 份样张**，核得到「各环节利润率」就下单，核不到就**别买研报**，转向结构化数据源（WoodMac / Benchmark，均为询价制）。

---

## 七、已核实的否定性结论（写下来是为了不再踩）

| 结论 | 证据 |
|---|---|
| **SNE Research 不覆盖 PCS / 逆变器出货量** | 其官网自有检索 EN + KO 两种语言搜「PCS」「Semiconductor」「반도체」**均返回空报告列表**；「Emerging Industry」目录只有氢能、SMR、人形机器人、钠离子电池、回收、BMS |
| **SNE Research 不覆盖半导体产业链价值分布** | 同上，官网检索为空 → 该行覆盖度记「不覆盖」、建议「不买」 |
| **McKinsey 卖文章不卖结构化数据** | 页面 `"IsPageRestricted":"false"`，多数文章免注册可读 → 建议「不买」，走自建 |
| **中国有色金属工业协会、中国医药工业信息中心 不卖结构化数据集** | 公开页只有统计年报/景气指数全文 → 建议「不买」 |
| **Benchmark 公开页没有「产能利用率 / utilisation」字段** | 所有产品页只见产能与产能管线 → 「全球电芯价格与产能利用率」覆盖度按保守口径记「部分」 |
| **Benchmark 没有「长期合约价」产品页** | 页面 FAQ 只解释现货与合约价的机制差异 → 同上 |
| **IQVIA / WoodMac / BNEF 没有「各环节利润率」产品页** | 已打开的产品页中均未找到该口径 → 这 5 条战略指标全部标「等」 |
| **米内网「零售连锁率」是否为其字段未确认** | 产品页只列库名，未列字段 |

---

## 八、未取到、须人工确认的字段

- Benchmark：**产能利用率**字段、**长期合约价**产品。
- S&P Global：全部覆盖度结论仅来自索引副本；索引副本对发布频率有「daily」与「weekly on Fridays」**两种互相矛盾的表述**。
- IQVIA：**「产业链各环节利润率 / 研发投入强度」**无对应产品页。
- WoodMac：**「五环节利润率」**、**「有色全品种（非锂）利润分布」**无单一产品页。
- BNEF：**「储能产业链五环节利润分布」**无独立产品页。
- 米内网：**「零售连锁率」**。
- 中国医药工业信息中心：**流通 / CXO 环节利润分布**、分环节毛利率口径。
- GGII：现域名、联系渠道、产品形态、是否存在公开标价 —— **全部未取到**。
- SNE：Monthly Tracker 订阅费（逐字「※ Subscription pricing is available upon request」）。
- 全部 SEMI 价格：**未直连复核**（429），需人工浏览器核对。
- `press@iea.org`、`pricegroup@spglobal.com`：**官网对本机 403**，邮箱无法回环复核（不是邮箱不存在）；需人工浏览器确认。

---

## 九、61 条「部分免费」行的免费替代源（能替代到哪一步）

- **61/61 行都给了免费替代源**（门槛 80%）。替代源分三类：**① 协会/政府/交易所的官方统计**（IEA、IRENA、IFPMA、EFPIA、CMS、CNINFO、OICA、ICSG、BIS、Eurostat、World Bank、SHFE、USGS、ACEA、AFDC、CSIA、CNESA、EASE、SolarPower Europe、Eurometaux、SIA）｜**② 厂商自己免费放出的部分**（SEMI 新闻稿、SMM 公开行情页、WoodMac 公开新闻稿、McKinsey 文章）｜**③ 二手公开报告中的可引用数字**（如 CNESA 白皮书免费文章 `id=7798` 实测 HTTP 200，页内带真实数字：2025 年 2h EPC 中标均价 **1,043.82 元/kWh**（同比 −13.04%）、4h EPC **935.40 元/kWh**（同比 −8.19%）；LFP 储能系统（不含用户侧）采购中标价区间 **391.14–913.00 元/kWh** —— **单位是 元/kWh，不是 元/Wh**（页内「元/Wh」0 处），本库口径是元/Wh，换算须除以 1000；原文自述「根据 CNESA DataLink 全球储能数据库的**不完全统计**」）。
- **能力差距逐条写在清单的「替代源能力差距」列**，主要三类：**频率降级**（月度→年度、年度→一次性）、**口径变窄**（总量有、分环节没有；全球有、中国没有）、**滞后**（Pink Sheet 滞后约 1 个月；IEA/EV 数据为年度）。
- 明确**不可混列**的两组：SMM 现货 vs SHFE 期货结算价；TrendForce「每颗芯片规格报价」vs 本库要求的 **USD/GB**（换算前不可并列）。

---

## 十、本单自身踩过的坑（失败记录）

| # | 坑 | 怎么发现 | 怎么改 |
|---|---|---|---|
| 1 | 汇总表字段名与 CSV 表头名不一致（`覆盖度` vs `覆盖度(全覆盖/部分)`），**三列被静默写成空** | 行数对但列全空 | 加别名映射；现在 12 列 **0 空值** |
| 2 | 价格核对初版按文本匹配，`$5,000` 被页面上任意一个 `5` 命中 | 假阳性 | 改为数值比对（`10,000 ≡ 10000.0`）+ 千分位逐位 |
| 3 | 同一「指标 × 数据商」出现两行（Benchmark 的价格页 + API 页） | 规格要求一行一组合 | 合并；**覆盖度取保守值**、建议取强、冲突写进备注 |
| 4 | 合并时把「价格评估产品族入口」的**全覆盖**并进「电芯价格与产能利用率」→ **高估覆盖度** | 独立复算对比发现 | 覆盖度改为取保守值（部分），该行由「全覆盖」降为「部分」 |
| 5 | 覆盖度统计初版**把「不覆盖」的行也计进覆盖数** | SNE 被算成 3/16 | 排除「不覆盖」，SNE 回落为 1/16；摘要与登记表数字全部重算 |
| 6 | 赛迪 `$5,000` 挂在 SNE 的另一份报告上，只核对主 URL → 假阴性 | 核对表 NOT-FOUND | 价格格改为**每个价格挂自己的 URL**，核对器按行内全部 URL 联合比对 |
| 7 | `solarpowereurope.org/insights/outlooks` 404（自己写错的） | 全表 URL 体检 | 改为首页（实测 200） |
| 8 | 复算脚本把摘要 §四 的横向对比表也当成 §二 的覆盖表解析 → 报假阻断 | 复算报 BLOCKING | 复算只切 §二 段落；并加厂商简称别名表 |
| 9 | **第三方内容站转述的价格被当成了厂商价**（搜索结果里有「慧博 328/5980 元/年」「Wind 约 4 万/年」等说法） | 本单的价格纪律要求「必须来自实际打开的厂商页面」 | 一律回到厂商自己的价格页取：慧博 → `https://www.hibor.com.cn/price`（200，有公开标价）；取不到就写「未公开，需询价」；索引副本只能算 B 级并注明未直接复核 |
| 10 | 摘要 §五「未覆盖 5 条」一度写错（把已被 GGII 覆盖的 2 条列了进去） | 独立复算比对出比例对不上 | 按复算结果改正：未覆盖的是中国医药市场规模与增速、PCS 与逆变器出货量、全球锂盐与镍钴价格、半导体设备与材料国产化率、存储芯片现货价与合约价 |
| 11 | **硬要求第 3 条漏了「路透/彭博新闻」这个免费替代源** | 大管家验收时点出该清单未列全 | 25 行的「免费替代源」与「替代源能力差距」列补入路透/彭博（并把五个来源的差距逐个写清：Insights 页无正文、监管披露无测算、路透/彭博只给结论、SSRN 口径不同、协会源频率降级） |
| 12 | **清单里写了 `prices@benchmarkminerals.com`，但该邮箱在 Benchmark 官网任何页面都核不到**（联系页是分类表单，页内无邮箱） | 询价邮箱回环核对（`verify_contacts.py`） | 撤掉该邮箱，改为「官网联系表单 https://www.benchmarkminerals.com/contact （实测 200，表单第一项即 Using Benchmark prices in contracts）」，并在清单/摘要/模板三处写明原因 |

> 这些坑都写进了 `experiments/registry.md` 的 WORK-535 行「失败 / 被证伪项」栏。

---

## 十一、复现方式

```bash
# 依赖：duckdb pandas numpy（本机 venv 在 workdir 下）
# 1) 从 data_catalog 抽出两个采购面（只读）
python experiments/WORK-535/extract_catalog.py

# 2) 重建主清单（逐字节可复现）
cd experiments/WORK-535 && python build_plan.py ../../docs/procurement_plan.csv

# 3) 价格复核（会打厂商网站，SEMI 会 429）
python experiments/WORK-535/verify_prices.py docs/procurement_plan.csv

# 4) 全表 URL 可达性体检
python experiments/WORK-535/check_plan_urls.py docs/procurement_plan.csv

# 5) 覆盖度算式与贪心集合覆盖
python experiments/WORK-535/final_stats.py docs/procurement_plan.csv

# 6a) 询价入口邮箱的回环核对（每个邮箱回厂商页面确认）
python experiments/WORK-535/verify_contacts.py docs/procurement_plan.csv

# 6b) 研报通道厂商的价格页与联系入口探测（增补节用）
python experiments/WORK-535/probe_research_vendors.py  research_vendor_probe.json
python experiments/WORK-535/probe_research_vendors2.py research_vendor_probe2.json

# 6) 独立复算闸门（不看上面任何脚本的函数，自己从 CSV + DuckDB 重算，
#    再把摘要里写的数字解析出来逐个比对；退出码 0 = 无阻断）
python audit/recompute_work535.py
```

**已知缺口**：§七 里那些「须人工确认」的字段本单无法自证——它们的依据是「已打开的页面里没有」，**属于否定性证据**，必须由人在真实浏览器里再查一遍才能定案。
