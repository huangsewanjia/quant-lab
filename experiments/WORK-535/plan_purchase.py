"""Build docs/procurement_plan.csv for WORK-535.

One row per 「指标 × 数据商」combination:
  - the 16 P0 rows that need procurement (each with >=1 vendor)
  - the 61 P0 rows that are only partially free (vendor = the paid source, plus the free alternative)

Every price in here is transcribed from a page that was actually opened on 2026-11-23.
Where no public price exists the cell reads exactly 「未公开，需询价」 — never a guess.
"""
import csv
import os

COLUMNS = [
    '指标名', '行业', '区域', '数据商', '覆盖度(全覆盖/部分)', '官网价格页URL', '公开价格',
    '计价方式(年费/单报告/按席位)', '免费替代源', '替代源能力差距', '建议(买/等/不买)', '备注',
]

NP = '未公开，需询价'

# ---------------------------------------------------------------- P0 需采购（16 条）
# key: (指标名, 数据商) -> dict
PURCHASE = [
    # ---------- Benchmark Mineral Intelligence ----------
    dict(
        指标名='全球储能系统与组件出货量', 行业='储能与电力设备', 区域='其他',
        数据商='Benchmark Mineral Intelligence', 覆盖度='全覆盖',
        官网价格页URL='https://www.benchmarkminerals.com/battery-energy-stationary-storage',
        公开价格=NP, 计价方式='未公开（订阅制，需询价）',
        免费替代源='IEA《Batteries and Secure Energy Transitions》公开页（储能装机 GW/GWh 与系统成本要点）',
        替代源能力差距='IEA 公开页只给全球装机与成本结论，不给「系统 vs 组件」分项出货量，也没有项目级明细',
        建议='买',
        备注='官网明示交付物原文：「Forecast and assessment in PDF with supporting Excel workbook」「Database as Excel workbook」「A monthly database providing a project-by-project status update of grid scale stationary storage projects」。'
             '覆盖度依据 = 该产品页实读（HTTP 200）。询价入口：**官网联系表单** https://www.benchmarkminerals.com/contact （实测 200；表单第一项即「Using Benchmark prices in contracts」，正对本单用途；**页面内无任何公开邮箱**——`prices@benchmarkminerals.com` 未能在官网复核到，**不作询价入口**）'),
    dict(
        指标名='全球电芯价格与产能利用率', 行业='储能与电力设备', 区域='全球',
        数据商='Benchmark Mineral Intelligence', 覆盖度='部分',
        官网价格页URL='https://www.benchmarkminerals.com/battery-gigafactory/supply-chain-data',
        公开价格=NP, 计价方式='未公开（订阅制，需询价）',
        免费替代源='无同口径免费源；上游金属价可用 World Bank Pink Sheet（月度 USD/t）',
        替代源能力差距='免费源只到上游金属价，没有电芯成品价，也没有产能/利用率',
        建议='等',
        备注='官网覆盖电芯产能、产能管线与生产成本：「Benchmark tracks global gigafactory build-outs, cell production capacity, and associated production costs」；'
             '更新频率原文「Our cell prices and gigafactory capacity is updated monthly, while supply chain forecasts and price forecasts are updated on a quarterly basis」。'
             '**「产能利用率/utilisation」未在任何公开页出现 → 须向销售确认是否单列该字段**。询价入口同 Benchmark（官网联系表单 https://www.benchmarkminerals.com/contact ；**页面内无公开邮箱**）。'),
    dict(
        指标名='全球电芯价格', 行业='新能源车', 区域='全球',
        数据商='Benchmark Mineral Intelligence', 覆盖度='全覆盖',
        官网价格页URL='https://www.benchmarkminerals.com/battery-gigafactory/prices',
        公开价格=NP, 计价方式='未公开（订阅制，需询价）',
        免费替代源='SMM 电池材料现货报价（元/吨）；World Bank Pink Sheet（锂/镍/钴，月度 USD/t）',
        替代源能力差距='免费源只有上游金属价，没有「电芯成品价」；且 SMM 为国内报价、Benchmark 为全球多化学体系；公开页亦未写明计价单位',
        建议='买',
        备注='官网原文「Benchmark publishes battery cell price assessments monthly along with capacity assessments of lithium ion and sodium ion batteries」；'
             '「You can also integrate our prices data via API, Excel add-in」。公开页列出 NCM111/523/622/811、NCA、LFP、LCO 七种电芯体系，但**未出现 kWh 字样 → 单位需确认**。'
             '询价入口：**官网联系表单** https://www.benchmarkminerals.com/contact （实测 200；表单第一项即「Using Benchmark prices in contracts」，正对本单用途；**页面内无任何公开邮箱**——`prices@benchmarkminerals.com` 未能在官网复核到，**不作询价入口**）'),
    dict(
        指标名='全球锂价与长期合约价', 行业='有色与锂', 区域='其他',
        数据商='Benchmark Mineral Intelligence', 覆盖度='部分',
        官网价格页URL='https://www.benchmarkminerals.com/lithium/prices',
        公开价格=NP, 计价方式='未公开（订阅制，需询价）',
        免费替代源='World Bank Pink Sheet 月度历史数据（锂，USD/t）；SMM 国内锂盐现货（元/吨）',
        替代源能力差距='免费源为月度基准价、非评估价，且不含长期合约价；Pink Sheet 滞后约 1 个月',
        建议='等',
        备注='官网原文「Benchmark publishes Lithium Price Assessments daily, weekly and every two weeks」，品种列 Lithium Carbonate / Lithium Hydroxide / Spodumene。'
             '**「长期合约价」无对应产品页**：页面 FAQ 只解释现货与合约价的机制差异，未说明是否提供长期合约价序列 → 须向销售确认。询价入口同 Benchmark（官网联系表单 https://www.benchmarkminerals.com/contact ；**页面内无公开邮箱**）。'),
    dict(
        指标名='全球电芯价格与产能利用率', 行业='储能与电力设备', 区域='全球',
        数据商='Benchmark Mineral Intelligence', 覆盖度='全覆盖',
        官网价格页URL='https://www.benchmarkminerals.com/price-assessments',
        公开价格=NP, 计价方式='未公开（订阅制，需询价）',
        免费替代源='无同口径免费源',
        替代源能力差距='免费源不存在；本行是价格评估产品族入口，覆盖度作为「电芯价格 + 锂钴镍等原料价」的佐证',
        建议='等',
        备注='官网原文「Bringing together over 2,500 price assessments, covering a broad range of energy transition raw materials and chemistries」；'
             '方法学 PDF 公开（含锂、钴、镍、石墨、稀土等）。询价入口：**官网联系表单** https://www.benchmarkminerals.com/contact （实测 200；表单第一项即「Using Benchmark prices in contracts」，正对本单用途；**页面内无任何公开邮箱**——`prices@benchmarkminerals.com` 未能在官网复核到，**不作询价入口**）'),
    dict(
        指标名='全球电芯价格', 行业='新能源车', 区域='全球',
        数据商='Benchmark Mineral Intelligence', 覆盖度='全覆盖',
        官网价格页URL='https://www.benchmarkminerals.com/api',
        公开价格=NP, 计价方式='未公开（订阅制 API，需询价）',
        免费替代源='无同口径免费源',
        替代源能力差距='免费源不存在；本行是取数通道（API）而非新指标，列出用于说明「同一份数据可走 API 而非 PDF」',
        建议='等',
        备注='官网原文「Unlock the full potential of Benchmark\'s price data platform with your unique API access」「Your API key is available in the subscription section of your account」。'
             '**/api/docs 实测 HTTP 200 但为前端渲染，未能读到接口清单** → 接口规格需向销售索取。'),
    # ---------- S&P Global Commodity Insights ----------
    dict(
        指标名='全球锂盐与镍钴价格', 行业='储能与电力设备', 区域='全球',
        数据商='S&P Global Commodity Insights（Platts）', 覆盖度='部分',
        官网价格页URL='https://www.spglobal.com/commodityinsights/en/our-methodology/price-assessments/metals/battery-metals',
        公开价格=NP, 计价方式='未公开，需询价',
        免费替代源='World Bank Pink Sheet 月度历史数据（锂/镍/钴，USD/t）；SMM 国内报价（元/吨）',
        替代源能力差距='Platts 为日频评估且分交付地（CIF North Asia / DDP China / FOB Australia），'
                      '免费源仅月度基准价、非评估价；且免费源不含电池级分品级价格',
        建议='等',
        备注='**本机访问 spglobal.com 全域 HTTP 403（Akamai Access Denied），覆盖度与品种清单来自搜索引擎索引副本，未经我方直接打开验证 → 引用前须人工复核。**'
             '索引副本所列评估代码：BATLC04 / BATCP04 / BATCA04 / BATLH04 / BATHY04 / BATSP03 / BATNS04 / BATCO04 / BATCS04 / BATCH04。'
             '索引副本对发布频率有「daily」与「weekly on Fridays」两种表述，**互相矛盾 → 频率须确认**；'
             '镍以硫酸镍(DDP China)覆盖、钴以硫酸钴/氢氧化钴覆盖，**未见金属镍/金属钴评估**。'
             '询价入口：https://www.spglobal.com/commodityinsights/en/contact-us ；pricegroup@spglobal.com（索引副本，未直接验证）。'),
    # ---------- Wood Mackenzie ----------
    dict(
        指标名='储能变流器 PCS 与逆变器出货量', 行业='储能与电力设备', 区域='全球',
        数据商='Wood Mackenzie', 覆盖度='部分',
        官网价格页URL='https://www.woodmac.com/press-releases/pcs-inverter-market-share-rankings-2026',
        公开价格=NP, 计价方式='未公开（付费排名报告；新闻稿免费）',
        免费替代源='WoodMac 免费新闻稿本身（份额与结构结论）；IEA 公开页（仅储能装机，无 PCS 口径）',
        替代源能力差距='免费新闻稿只给份额排名与结构结论（2025 年 utility-scale 占全球 PCS 出货 81%，'
                      '中央式约占 utility-scale 的 75%），不给逐厂商出货量序列与历史',
        建议='买',
        备注='**口径关键**：官网原文「Market share is measured in AC inverter capacity (MWac), not battery energy capacity (MWh)」'
             '——与本库「功率口径」一致；但本行指标频率写「季度」，该排名报告为**年度** → 频率须确认。'
             '官网另称「leveraging direct data from vendor shipment surveys」。'
             '询价入口：https://www.woodmac.com/about/contact-us/ ；support@woodmac.com'),
    dict(
        指标名='储能产业链利润分布（电芯/PCS/系统/EPC/运营）', 行业='储能与电力设备', 区域='全球',
        数据商='Wood Mackenzie', 覆盖度='部分',
        官网价格页URL='https://www.woodmac.com/lens/lens-energy-storage/',
        公开价格=NP, 计价方式='未公开（Lens 订阅，需询价）',
        免费替代源='WoodMac 免费新闻稿（APAC 储能 EPC 成本区间 US$59–117/kWh；2026 BESS 集成商排名中的研发投入与盈利家数）',
        替代源能力差距='免费稿只给成本区间与排名结论，不给「电芯/PCS/系统/EPC/运营」五环节的利润率或价值占比序列',
        建议='等',
        备注='官网原文「Access to more than 80 GW of operational utility battery storage assets and 300 GW of assets in development」「Quarterly US-focused Energy Storage Monitor」'
             '「Benchmark the leading storage supply chain companies…」。**官网无「五环节利润率」单一产品页** → 须向销售确认可否单列。'
             '询价入口：https://www.woodmac.com/about/contact-us/ ；support@woodmac.com'),
    dict(
        指标名='有色与锂产业链利润分布（矿山/冶炼/材料/回收）', 行业='有色与锂', 区域='全球',
        数据商='Wood Mackenzie', 覆盖度='部分',
        官网价格页URL='https://www.woodmac.com/industry/metals-and-mining/lithium-product-suite/',
        公开价格=NP, 计价方式='未公开（订阅制，需询价）',
        免费替代源='中国有色金属工业协会行业统计（公开，年度）',
        替代源能力差距='Lithium Cost Service 公开描述只覆盖**锂**链成本拆解，不含镍钴等其余有色环节，'
                      '且协会公开统计无「各环节毛利率/价值占比」分解',
        建议='等',
        备注='官网原文「Access insights into the cost makeup of the entire lithium supply chain and operating costs associated with production」'
             '「Analysis and commentary on macroeconomic trends, supply, demand and price fundamentals…across different stages of the supply chain from mine to brine, intermediates and refined lithium」。'
             '**提供的是成本（cost）拆解，需求方要的是利润率/价值占比 → 口径不完全对得上，须确认**。'
             '询价入口：https://www.woodmac.com/about/contact-us/ ；support@woodmac.com'),
    dict(
        指标名='全球储能系统与组件出货量', 行业='储能与电力设备', 区域='其他',
        数据商='Wood Mackenzie', 覆盖度='部分',
        官网价格页URL='https://www.woodmac.com/reports/power-markets-us-energy-storage-monitor-q3-2026-150505404/',
        公开价格='$5,000（单份报告）', 计价方式='单报告',
        免费替代源='IEA 公开页（全球储能装机与成本要点）',
        替代源能力差距='该报告是**美国口径**的季度装机监测，不覆盖全球，也不拆系统与组件',
        建议='不买',
        备注='报告页原文「Get this report* … $5,000」「Annual US energy storage installations to grow over 50% in MW terms over the next five years」；'
             '页面 JSON-LD 为 "price":"$5,000"。**价格可在该 URL 查到，但口径不满足本行「全球」要求**。'
             '另：Lens Energy Storage 为订阅制、无公开价。询价入口：https://www.woodmac.com/about/contact-us/ ；support@woodmac.com'),
    # ---------- TrendForce 集邦 ----------
    dict(
        指标名='存储芯片现货价与合约价', 行业='半导体', 区域='全球',
        数据商='TrendForce 集邦', 覆盖度='全覆盖',
        官网价格页URL='https://www.trendforce.com/membership',
        公开价格='Silver Membership USD 5,000/年（当前标价）；限时促销价 USD 4K/year',
        计价方式='年费（会员订阅）',
        免费替代源='TrendForce 自家公开现货价表：DRAM https://www.trendforce.com/price/dram/dram_spot 、'
                   'NAND https://www.trendforce.com/price/flash/flash_spot 与 /price/flash/flash_contract（均 HTTP 200）',
        替代源能力差距='公开页只给当次 session 的 High/Low/Average 与 Change，**5 年历史 Excel 下载、合约价明细与趋势图对比为会员功能**；'
                      '且报价单位是「每颗芯片规格」（如 DDR5 16Gb 4800/5600 → 72.00），**不是本库要求的 USD/GB**，需自行按容量折算',
        建议='买',
        备注='会员价逐字「USD 5,000」；促销逐字「Limited-Time Flash Sale: Instant Access to Memory Pricing (Silver Membership: Only USD 4K/year)」。'
             '同页可选：Gold USD 11,000、DRAM Platinum USD 18,000、Memory Platinum USD 30,000、NAND Flash Platinum USD 30,000、Memory Diamond USD 55,000。'
             '单份报告示例「PV Monthly Price Trend Plus - Nov. 2026」schema.org 报价逐字 "price":"8000.00","priceCurrency":"USD"。'
             '询价入口：https://www.trendforce.com/contact/reporting ；service@trendforce.com ；SR_MI@trendforce.com（半导体研究）；+886-2-7702-6888'),
    # ---------- 米内网 ----------
    dict(
        指标名='中国医药市场规模与增速', 行业='医药', 区域='中国',
        数据商='米内网 Menet', 覆盖度='全覆盖',
        官网价格页URL='https://www.menet.com.cn/menetDatabase/database.html',
        公开价格=NP, 计价方式='未公开（数据库在线订阅，年费待询）',
        免费替代源='国家统计局·国家数据（医药制造业工业口径）；米内网公开文章与新闻稿',
        替代源能力差距='统计局是**工业口径营业收入**，不等于终端市场规模；米内网免费内容只有定性描述，没有终端金额序列',
        建议='买',
        备注='覆盖度依据 = 格局库产品页实读（HTTP 200）：「中国城市实体药店【化学药】终端竞争格局…2015至今，年度呈现，季度更新」；'
             '医院端「2013年至今，季度呈现，季度更新」；工具库含销售格局库、招投标、国家集采报量、医保目录、上市公司业绩与毛利率等 35 个库。'
             '产品页逐字只有「咨询价格」「申请试用」两个按钮，全站无任何金额 → 未公开，需询价。'
             '询价入口：https://www.menet.com.cn/home/contact （13 位业务代表 + 座机，**该页无 email**）。'
             '**「零售连锁率」是否为其格局库字段未在产品页列明 → 须确认**'),
    # ---------- IQVIA ----------
    dict(
        指标名='医药产业链利润分布（创新药/CXO/原料药/流通）', 行业='医药', 区域='全球',
        数据商='IQVIA', 覆盖度='部分',
        官网价格页URL='https://www.iqvia.com/solutions/commercialization/brand-and-portfolio-strategy',
        公开价格=NP, 计价方式='未公开（授权订阅，需询价）',
        免费替代源='IFPMA 全球药品市场统计（免费）；EFPIA（免费）；中国医药统计网（免费）',
        替代源能力差距='免费源给的是**销售额/市场规模总量**，没有创新药/CXO/原料药/流通四环节的**利润率**与研发投入强度对比',
        建议='等',
        备注='覆盖度依据 = MIDAS 与 Analytics Link 产品页实读：MIDAS「delivers harmonized global pharmaceutical market intelligence by integrating '
             'industry-leading sales, medical, disease, and patient-level data」；Analytics Link「adds 10-year sales value, volume, patient day and implied price '
             'forecasts for 10,000 drugs, 600 diseases and 80+ countries」。'
             '**在已打开的 IQVIA 产品页中未找到「产业链各环节利润率 / 研发投入强度」口径的产品 → 本行覆盖度存疑，须向销售确认**。'
             'IQVIA 不发布公开价目表（已核 /sitemap HTTP 200）。询价入口：https://www.iqvia.com/contact'),
    # ---------- 赛迪顾问 CCID ----------
    dict(
        指标名='半导体设备与材料国产化率', 行业='半导体', 区域='中国',
        数据商='赛迪顾问 / 中国电子信息产业发展研究院（CCID）', 覆盖度='部分',
        官网价格页URL='https://api.mtx.cn/report?command=detail&report_id=10022650',
        公开价格='¥10,000（单份报告，报告商店标价；接口字段 "price": 10000.0）', 计价方式='单报告',
        免费替代源='中国半导体行业协会公开文章（如 https://web.csia.net.cn/newsinfo/11304672.html 、'
                   'https://web.csia.net.cn/newsinfo/11310429.html ）；国家统计局集成电路行业利润与投资月度数据',
        替代源能力差距='免费源只给行业总量、增速与利润总额，不给光刻/刻蚀/薄膜/清洗设备的**分环节国产供货比例**，且无统一统计方法说明',
        建议='买',
        备注='赛迪顾问报告商店 mtx.cn 的自有接口 https://api.mtx.cn/report?command=detail&report_id=10022650 逐字返回 "price": 10000.0（元）；'
             '同接口 report_id=10023052 同为 10000.0。**报告为 PDF 年报，不是可查询的结构化数据集**（须在备注区分「数据」与「报告」）。'
             '历史中标价（参考，非现行官网标价）：《合肥市"十五五"化工产业发展规划》逐字「中标（成交）金额：人民币 陆拾伍万元整（大写）（650000.00元）」'
             'https://www.ccgp.gov.cn/cggg/dfgg/cjgg/202507/t20250703_24899406.htm'
             '询价入口：https://www.ccidconsulting.com/contact ；service@ccidconsulting.com'),
    # ---------- McKinsey ----------
    dict(
        指标名='新能源汽车产业链利润分布', 行业='新能源车', 区域='全球',
        数据商='McKinsey 麦肯锡', 覆盖度='部分',
        官网价格页URL='https://www.mckinsey.com/featured-insights',
        公开价格='无标价/免费阅读（内容免费，不存在价格）', 计价方式='不适用（免费阅读）',
        免费替代源='本行即免费源（Our Insights 文章与报告 PDF）',
        替代源能力差距='报告是**特定年份的静态价值链拆分**，不能当时间序列取用；不同文章的口径与年份不统一',
        建议='不买',
        备注='**McKinsey 不销售结构化数据集**——页面内容类型标签为 Report/Article/Book/Video，元数据逐字 "IsPageRestricted":"false"、enableRegWall:false，'
             '多数文章免注册可读，季度数字版「available with a free Quarterly membership」。'
             '因此本行不写「未公开，需询价」：该机构没有标价，内容免费。'
             '询价入口：https://www.mckinsey.com/contact-us （本机直连超时，本轮仅 /featured-insights 实测 HTTP 200）'),
    dict(
        指标名='半导体产业链价值分布（设计/制造/封测/设备/材料）', 行业='半导体', 区域='全球',
        数据商='McKinsey 麦肯锡', 覆盖度='部分',
        官网价格页URL='https://www.mckinsey.com/featured-insights',
        公开价格='无标价/免费阅读（内容免费，不存在价格）', 计价方式='不适用（免费阅读）',
        免费替代源='本行即免费源（Our Insights 文章与报告 PDF）',
        替代源能力差距='同上行：静态拆分、不可作序列、口径不统一',
        建议='不买',
        备注='与「新能源汽车产业链利润分布」同源同结论：**卖报告不卖结构化数据**。'
             '询价入口：https://www.mckinsey.com/contact-us （本机直连超时）'),
    # ---------- BloombergNEF ----------
    dict(
        指标名='储能产业链利润分布（电芯/PCS/系统/EPC/运营）', 行业='储能与电力设备', 区域='全球',
        数据商='BloombergNEF（BNEF）', 覆盖度='部分',
        官网价格页URL='https://about.bnef.com/insights/clean-energy/new-energy-outlook/',
        公开价格=NP, 计价方式='未公开（订阅制）',
        免费替代源='IEA《Batteries and Secure Energy Transitions》公开页；WoodMac APAC 储能 EPC 成本区间新闻稿'
                   '（https://www.woodmac.com/press-releases/apac-utility-scale-energy-storage-pricing-report-2025/）',
        替代源能力差距='免费源给全球装机与成本曲线要点、区域 EPC 成本区间，不给五环节利润率拆分',
        建议='等',
        备注='官网逐字「BNEF\'s premium research, invite-only events and analyst access are available exclusively to subscribers.」'
             '**在已打开的公开页面中未找到「储能产业链利润分布（五环节）」的独立产品页 → 覆盖度存疑，须向销售确认**。'
             '询价入口：https://about.bnef.com/contact-us/ ；sales.bnef@bloomberg.net'),
    # ---------- 中国有色金属工业协会 ----------
    dict(
        指标名='有色与锂产业链利润分布（矿山/冶炼/材料/回收）', 行业='有色与锂', 区域='全球',
        数据商='中国有色金属工业协会', 覆盖度='部分',
        官网价格页URL='https://www.chinania.org.cn/html/hangyetongji/jqzs/',
        公开价格='免费全文，无标价', 计价方式='不适用（免费）',
        免费替代源='本行即免费源：景气指数月度报告与行业数据统计（免费全文网页）',
        替代源能力差距='免费景气指数只给景气与利润总额的**定性分化**与指数值（如逐字「铜企业利润总额指数为172.8」「铝…利润总额指数为441.3」'
                      '「铜价高位抬升上游矿山企业盈利水平，但冶炼加工企业受原料成本挤压，产业链利润分化格局凸显」），'
                      '不给各环节毛利率/吨利润/价值占比的结构化数值表，也**无「回收」环节数据、无锂专题月度指数**',
        建议='不买',
        备注='**该协会不卖结构化数据集**，景气指数与行业数据统计为免费全文网页 → 本行不需要采购，缺口靠自建。'
             '站内未出现统计年鉴/出版物/会员的商品入口，站内无任何公开价目表。'
             '询价入口：https://www.chinania.org.cn/html/lianxiwomen/ （逐字只有「联系电话：010-63971807」，**无 email**）'),
    # ---------- 中国医药工业信息中心 ----------
    dict(
        指标名='医药产业链利润分布（创新药/CXO/原料药/流通）', 行业='医药', 区域='全球',
        数据商='中国医药工业信息中心', 覆盖度='部分',
        官网价格页URL='https://www.yytj.org.cn/subscribeReportList.aspx',
        公开价格=NP, 计价方式='未公开（《中国医药工业统计年报》按年征订）',
        免费替代源='中国医药统计网「图说数据/经济运行」（全国医药工业主营业务收入增速、地区分布、出口交货值）'
                   'https://www.yytj.org.cn/',
        替代源能力差距='免费统计给工业端总量与增速，**不含流通/CXO 环节的利润分布与研发投入强度对比**',
        建议='等',
        备注='覆盖度依据 = 《中国医药工业统计年报》征订页实读（HTTP 200，列出 2014–2020 年度征订通知）+ 商业数据库页 '
             'http://www.cphiic.com/ （PDB 药物综合数据库 / RPDB 药品零售数据库 / Pharma ONE 药物研发大数据平台 / 中国儿童用药数据库）。'
             '**未覆盖流通/CXO 环节利润分布；创新药 vs CXO vs 原料药的分环节毛利率口径未在公开页确认**。'
             '询价入口：https://www.yytj.org.cn/ContactUs.aspx ；baohf@pharmadl.com（联系人 包海峰，021-62897079）；yaoxl@pharmadl.com'),
    # ---------- SNE Research ----------
    dict(
        指标名='储能变流器 PCS 与逆变器出货量', 行业='储能与电力设备', 区域='全球',
        数据商='SNE Research', 覆盖度='不覆盖',
        官网价格页URL='https://www.sneresearch.com/en/insight/release/36',
        公开价格='不适用（无该产品）', 计价方式='不适用',
        免费替代源='WoodMac 免费新闻稿「Global Energy Storage System PCS Market Share 2026」'
                   '（https://www.woodmac.com/press-releases/pcs-inverter-market-share-rankings-2026 ）',
        替代源能力差距='WoodMac 免费稿只给份额与结构结论，不给逐厂商出货量序列',
        建议='不买',
        备注='**否定性结论（有据）**：SNE 的公开产品线为 EV/电池/半导体市场统计（装机量、份额、材料耗量），'
             '其储能条目逐字为「Global ESS Shipments Reach 461GWh in 1H26, Up 71%」——**口径是 GWh 能量，不是本行要求的 PCS 功率口径 GW**；'
             '已打开的 SNE 报告目录中未见 PCS/逆变器出货量数据集 → **SNE 不覆盖本行**。'
             '本行的可用供应商是 Wood Mackenzie（见本表另一行）。',
    ),
    dict(
        指标名='新能源汽车产业链利润分布', 行业='新能源车', 区域='全球',
        数据商='SNE Research', 覆盖度='部分',
        官网价格页URL='https://www.sneresearch.com/en/business/report_view/148/page/0',
        公开价格='$15,000（Global EV & Battery Quarterly Forecast，单份报告）',
        计价方式='单报告',
        免费替代源='McKinsey Our Insights（免费阅读）；IEA Global EV Outlook 报告 PDF（CC BY 4.0）',
        替代源能力差距='SNE 付费报告给的是**出货量/份额/产能预测**，价值链利润率分解要另找；'
                      '而 McKinsey 免费文章只有特定年份的静态拆分',
        建议='等',
        备注='最接近的产品是《Automotive OEMs and the Battery Value Chain: Response Status and Strategy to Global and Regional ESG Requirements (~2040)》'
             '（https://www.sneresearch.com/en/business/report_view/268/page/0 ，200）——但主题是 **ESG 合规与价值链策略，不是各环节利润率分布**；'
             'EN 目录未检出以「各环节利润率」为核心的产品 → **覆盖度存疑，须向销售确认**。'
             '询价入口：https://www.sneresearch.com/en/about/contact/ ；sales@sneresearch.com ；+82-70-4006-0355。',
    ),
    dict(
        指标名='半导体产业链价值分布（设计/制造/封测/设备/材料）', 行业='半导体', 区域='全球',
        数据商='SNE Research', 覆盖度='不覆盖',
        官网价格页URL='https://www.sneresearch.com/en/business/report_list/0?s_cat=&s_keyword=Semiconductor',
        公开价格='不适用（无该产品）', 计价方式='不适用',
        免费替代源='McKinsey Our Insights（免费阅读）；SEMI 新闻稿（设备与材料账单免费部分）；SIA 月度销售统计',
        替代源能力差距='SNE 检索页报告列表为空；McKinsey 免费文章只有特定年份的静态拆分；'
                      'SEMI 免费部分只到设备与材料账单总量，不含设计/封测环节',
        建议='不买',
        备注='**否定性结论（有据，两种语言站都验过）**：SNE 官网自有检索 `Semiconductor`（EN）与 `반도체`（KO）'
             '**均返回空报告列表**；EN/KO 的「Emerging Industry」目录标题只有氢能、SMR、人形机器人、钠离子电池、回收、BMS，'
             '**无半导体、无 PCS/逆变器** → **本行不要向 SNE 采购**。'
             '询价入口（若仍要问）：https://www.sneresearch.com/en/about/contact/ ；sales@sneresearch.com。',
    ),
    # ---------- 高工锂电 / GGII ----------
    dict(
        指标名='电池产能与产能利用率', 行业='储能与电力设备', 区域='中国',
        数据商='高工锂电 / 高工产业研究院 GGII', 覆盖度='全覆盖',
        官网价格页URL='https://www.gg-lb.com/',
        公开价格=NP, 计价方式='未公开（单份报告 + 会员年费）',
        免费替代源='GGII 在高工锂电平台公开文章与峰会材料中免费披露的分环节产能利用率区间；'
                   '巨潮资讯网上市公司定期报告（可自建产能与开工率）',
        替代源能力差距='免费公开的是**分档区间与时点快照**（如 TOP5 磷酸铁锂正极 95%–100%、负极 100%–120%、隔膜 85%–100%），'
                      '**不是连续的产能/开工率时间序列**；口径（是否含在建、规划产能是否计入）须逐份核原文；'
                      'GGII 公开说明其蓝皮书数据更新存在时点滞后',
        建议='等',
        备注='**GGII 官网本机不可达（按本单停手条件如实登记，未做绕过）**：'
             'www.gg-lb.com → DNS 解析失败（Errno -2 Name or service not known）；'
             'www.gaogong123.com → TCP 连接被拒（Errno 111 Connection refused，80 与 443 均是）；'
             'gaogong123.com → 连接超时；www.ggii.com → 连接超时。'
             '**本轮未能打开 GGII 任何官方页面，因此其产品名、报告 vs 数据库形态、是否存在公开标价、询价入口全部「未取到」**；'
             '第三方站点转载的订购信息（电话 0755-2683569x / research1@gaogong123.com）**未获官网确认，本单不据此写价格**。'
             '域名状态：`gg-lb.com` 在公共解析器 8.8.8.8 亦无 A 记录（疑失效或已迁移），需确认新域名。',
    ),
    dict(
        指标名='动力电池产能与开工率', 行业='新能源车', 区域='中国',
        数据商='高工锂电 / 高工产业研究院 GGII', 覆盖度='全覆盖',
        官网价格页URL='https://www.gg-lb.com/',
        公开价格=NP, 计价方式='未公开（单份报告 + 会员年费）',
        免费替代源='GGII 公开文章与峰会材料中的分环节产能利用率区间；巨潮资讯网上市公司定期报告（可自建）',
        替代源能力差距='同上游：免费的是分档区间与时点快照，不是连续序列；口径须逐份核原文',
        建议='等',
        备注='**GGII 官网本机不可达**（DNS 解析失败 / TCP 连接被拒，见本表另一行备注）；'
             '询价入口**未取到**，须先确认现行的官方域名与联系渠道。',
    ),
]
