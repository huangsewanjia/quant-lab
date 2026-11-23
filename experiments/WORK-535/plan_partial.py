"""The 61 P0 「部分免费」 rows: the paid vendor, the free alternative, and the gap.

Keyed by the exact 一手来源 string in data_catalog, so every one of the 61 catalog rows
maps to exactly one enriched row (1 catalog source may cover several indicators).
"""

NP = '未公开，需询价'

# 一手来源原文 -> enriched fields
PARTIAL_BY_SOURCE = {
    # ---------------- IEA ----------------
    '国际能源署 IEA·全球 EV Outlook https://www.iea.org/reports/global-ev-outlook-2024（IEA·Global EV Outlook）': dict(
        数据商='国际能源署 IEA', 覆盖度='全覆盖',
        官网价格页URL='https://www.iea.org/reports/global-ev-outlook-2026',
        公开价格=NP + '（报告本体 CC BY 4.0 免费下载；数据集订阅另计）', 计价方式='未公开（数据集订阅）',
        免费替代源='《Global EV Outlook 2026》报告 PDF（CC BY 4.0，可免费下载）+ Global EV Data Explorer 在线工具',
        替代源能力差距='报告给出各区域/车型的年度销量与份额结论与部分图表，但**逐国别-逐车型的完整历史序列与月度数据在付费数据服务内**；'
                      '且**增程（EREV）是否计入 BEV/PHEV 须逐版核原文**（本库 G-03）',
        建议='等',
        备注='引用格式逐字「IEA (2026), Global EV Outlook 2026, IEA, Paris https://www.iea.org/reports/global-ev-outlook-2026, Licence: CC BY 4.0」。'
             '**库内 URL 指向 2024 版，已过期 → 改为 2026 版**。本机直连 iea.org 全站 HTTP 403（Cloudflare 质询），'
             'URL 与许可信息取自搜索引擎索引副本，人工浏览器可核对。询价入口：IEA Data Products 目录 https://www.iea.org/data-and-statistics/data-products ；press@iea.org（数据服务）。',
    ),
    '国际能源署 IEA《Global EV Outlook》 https://www.iea.org/': dict(
        数据商='国际能源署 IEA', 覆盖度='全覆盖',
        官网价格页URL='https://www.iea.org/reports/global-ev-outlook-2026',
        公开价格=NP + '（报告本体 CC BY 4.0 免费下载；数据集订阅另计）', 计价方式='未公开（数据集订阅）',
        免费替代源='《Global EV Outlook 2026》报告 PDF（CC BY 4.0）+ Global EV Data Explorer 在线工具',
        替代源能力差距='同族：报告有全球与中国的年度销量与渗透率结论，**逐国别逐月序列需付费数据服务**；'
                      '增程（EREV）是否计入 BEV/PHEV 须逐版核原文（G-03）',
        建议='等',
        备注='IEA 报告本体为 CC BY 4.0，可直接下载引用。询价入口：IEA Data Products 目录；press@iea.org。',
    ),
    '国际能源署 IEA https://www.iea.org/data-and-statistics（IEA·数据与统计（Critical Minerals））': dict(
        数据商='国际能源署 IEA', 覆盖度='全覆盖',
        官网价格页URL='https://www.iea.org/data-and-statistics',
        公开价格=NP, 计价方式='未公开（数据集订阅）',
        免费替代源='USGS《Mineral Commodity Summaries 2026》**单矿种 PDF 免费下载**（93 个矿种章节）：'
                   'https://pubs.usgs.gov/periodicals/mcs2026/ （实测 200）；'
                   '锂章 https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-lithium.pdf 、'
                   '稀土章 https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-rare-earths.pdf',
        替代源能力差距='IEA 是供需**模型推算**、USGS 是**统计量**，两者口径不同不可混列（本库 G-01）；'
                      'USGS 只给矿产量与储量，不给需求拆分与产能过剩测算；且为 PDF，**须人工取数**',
        建议='等',
        备注='**USGS 单矿种 PDF 直链实测 HTTP 200**（本机直连 usgs.gov 官网首页为 403/405，PDF 目录可用）；'
             'iea.org 全站 403 需真实浏览器。询价入口：IEA Data Products 目录；press@iea.org。',
    ),
    '国际能源署 IEA·电池与储能 https://www.iea.org/reports/batteries-and-secure-energy-transitions（IEA·Batteries and Secure Energy Transitions）': dict(
        数据商='国际能源署 IEA', 覆盖度='全覆盖',
        官网价格页URL='https://www.iea.org/reports/batteries-and-secure-energy-transitions',
        公开价格=NP, 计价方式='未公开（数据集订阅）',
        免费替代源='同报告的免费摘要页与部分图表；BNEF 成本曲线为付费内容',
        替代源能力差距='IEA 为模型推算与各国上报混合，**误差带须记录**；成本口径须与本库 G-01 一致；'
                      '全球储能系统成本曲线（USD/kWh）不在 IEA 免费公开部分',
        建议='等',
        备注='BNEF 的成本曲线是本行的替代付费源，两者不可混列。询价入口：IEA Data Products 目录；press@iea.org。',
    ),
    '国际能源署 IEA https://www.iea.org/data-and-statistics（IEA·数据与统计（Global Energy Balance））': dict(
        数据商='国际能源署 IEA', 覆盖度='全覆盖',
        官网价格页URL='https://www.iea.org/data-and-statistics',
        公开价格=NP, 计价方式='未公开（数据集订阅）',
        免费替代源='IEA 公开页给摘要与部分图表；国际能源统计（UN Energy Statistics）免费数据库',
        替代源能力差距='**IEA 完整能源平衡数据集需订阅**；UN Statistics 给的是各国上报口径的能源平衡，'
                      '与 IEA 的合并平衡口径不同（同样不可混列）',
        建议='等',
        备注='本机直连 HTTP 403。询价入口：IEA Data Products 目录；press@iea.org。',
    ),
    'IEA https://www.iea.org/ （Critical Minerals）；SNE Research（需采购）': dict(
        数据商='国际能源署 IEA + SNE Research', 覆盖度='部分',
        官网价格页URL='https://www.sneresearch.com/en/insight/release_view/',
        公开价格=NP, 计价方式='未公开（数据集订阅）',
        免费替代源='IEA 关键矿产章节免费公开部分结论；USGS MCS 2026 锂章 PDF（免费，储量统计量）：'
                   'https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-lithium.pdf （实测 200）',
        替代源能力差距='IEA 公开页只有部分结论，**细分需求结构（动力/储能/工业 拆分与增速）需订阅**；'
                      'USGS 只有矿产量与储量、无需求侧；两者口径不同不可混列（G-01）',
        建议='等',
        备注='本行两家覆盖度都只到部分：IEA 付费部分=SNE 付费部分。询价入口：IEA Data Products 目录；SNE 站内 Contact。',
    ),
    # ---------------- SMM / SHFE ----------------
    '上海有色网 SMM https://www.smm.cn/': dict(
        数据商='上海有色网 SMM', 覆盖度='全覆盖',
        官网价格页URL='https://www.smm.cn/',
        公开价格=NP, 计价方式='未公开（数据服务订阅）',
        免费替代源='World Bank Pink Sheet 月度+年度 XLS/PDF（锂/镍/钴/铜/铝，USD/t）：'
                   'https://www.worldbank.org/en/research/commodity-markets （实测 200）；'
                   'SHFE 免费日/周/月/年行情 https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/ （实测 200）；'
                   'SMM 公开行情页（渲染为「未登录」，值在 HTML 属性里）：'
                   '电池级碳酸锂 https://hq.smm.cn/new-energy/category/201102250059 、'
                   '新能源链总表 https://hq.smm.cn/new-energy',
        替代源能力差距='SMM 逐日报价的历史序列为**登录订阅**；**Pink Sheet 是月度基准价、非现货评估**，滞后约 1 个月，'
                      '且无电池级/工业级分档、无地区均价；SHFE 只有期货结算价与升贴水，无现货均价',
        建议='等',
        备注='**SMM 公开行情页示例（2026-11-23 电池级碳酸锂：low 88,700 / high 92,300 / avg 90,500 元/吨）——这是当日行情快照，不是 SMM 的订阅价**，'
             '订阅价未公开；钴/镍/石墨等其余品种在各自行情页（hq.smm.cn/copper、/aluminum、/lead、/zinc 同构）。'
             'SMM 行情页实测 HTTP 200（https://www.smm.cn/ 、https://hq.smm.cn/new-energy ）。'
             'SMM 数据服务页 https://data-pro.smm.cn/terminal 实测 200，**无任何价格数字** → 订阅价未公开。'
             '询价入口：https://www.smm.cn/about/contact （200）；kefu@smm.cn、service@smm.cn、service.en@smm.cn；021-31330333。',
    ),
    '上海有色网 SMM https://www.smm.cn/ ；上海期货交易所 https://www.shfe.com.cn/': dict(
        数据商='上海有色网 SMM + 上海期货交易所 SHFE', 覆盖度='全覆盖',
        官网价格页URL='https://www.smm.cn/',
        公开价格=NP, 计价方式='未公开（数据服务订阅）',
        免费替代源='SHFE 免费日/周/月/年行情与下载入口（实测 200）：'
                   'https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/ ；'
                   'SMM 各品种页（现货+升贴水）：铜 https://hq.smm.cn/copper/category/201102250376 、'
                   '铝 https://hq.smm.cn/aluminum/category/201102250311 、铅 https://hq.smm.cn/lead/category/201102250211 、'
                   '锌 https://hq.smm.cn/zinc/category/201102250173 （均 200）',
        替代源能力差距='SHFE 给**期货结算价**与升贴水，**无现货均价、无进口盈亏**（本行口径要求现货价+升贴水+进口盈亏）；'
                      'SMM 的现货与升贴水需登录看值、且历史序列为订阅；Pink Sheet 是月度基准价、滞后约 1 个月',
        建议='等',
        备注='本行是 SMM 与 SHFE 的拼接，两者口径不同不可混列。'
             '询价入口：https://www.smm.cn/about/contact （200）；kefu@smm.cn、service@smm.cn；021-31330333。',
    ),
    # ---------------- SEMI ----------------
    '国际半导体产业协会 SEMI https://www.semi.org/en/semi-press-release/（SEMI·World Fab Forecast）': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='World Fab Forecast Subscription Member $7,550 / Non-Member $11,100；'
                 'World Fab Forecast Single Edition Member $3,800 / Non-Member $5,600',
        计价方式='年费（订阅）或单报告',
        免费替代源='SEMI 新闻稿免费公开部分产能表 https://www.semi.org/en/semi-press-release/',
        替代源能力差距='新闻稿只给部分产能表与结论，**完整 Fab Forecast（按尺寸与地区的千片/月、含在建）属付费订阅**；'
                      '且为设计产能（installed capacity），与实际开工率分列（G-02）',
        建议='买',
        备注='**store-us.semi.org 与 semi.org 对本机均被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见；人工浏览器可核对**。'
             '来源行例逐字「World Fab Forecast Subscription Sale price Member Price: $7,550.00 Non-Member Price: $11,100.00」。',
    ),
    '国际半导体产业协会 SEMI https://www.semi.org/en/semi-press-release/（SEMI·全球半导体设备市场统计）': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='Equipment Market Data Subscription Member $3,350 / Non-Member $7,050；'
                 'Historical WWSEMS Report (1991–2025) Member $3,050 / Non-Member $4,100',
        计价方式='年费（EMDS）或单报告',
        免费替代源='SEMI 新闻稿免费公开设备账单总额与分地区表',
        替代源能力差距='新闻稿只有总额与分地区表，**完整历史序列与 22 个细分市场属付费订阅**；'
                      '账单按客户所在地统计（G-04）',
        建议='买',
        备注='**SEMI store 对本机被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见**。',
    ),
    '国际半导体产业协会 SEMI https://www.semi.org/en/semi-press-release/（SEMI·全球设备市场统计）': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='Equipment Market Data Subscription Member $3,350 / Non-Member $7,050；'
                 'Historical WWSEMS Report (1991–2025) Member $3,050 / Non-Member $4,100',
        计价方式='年费（EMDS）或单报告',
        免费替代源='SEMI 新闻稿免费公开设备账单总额与分地区表',
        替代源能力差距='新闻稿只有账单总额与分地区表，**完整历史序列与 22 个细分市场属 Equipment Market Data Subscription**',
        建议='买',
        备注='**SEMI store 对本机被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见**。'
             '来源行例逐字「Equipment Market Data Subscription (EMDS) Sale price Member Price: $3,350.00 Non-Member Price: $7,050.00」。',
    ),
    '国际半导体产业协会 SEMI https://www.semi.org/en/semi-press-release/（SEMI·晶圆厂产能与利用率）': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='World Fab Forecast Premium Member $10,300 / Non-Member $15,000',
        计价方式='年费（订阅）',
        免费替代源='SEMI 新闻稿免费公开部分产能与利用率表',
        替代源能力差距='新闻稿只给部分表；**利用率的完整历史序列属订阅**，且利用率分母为设计产能（G-02）',
        建议='买',
        备注='**SEMI store 对本机被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见**。'
             '来源行例逐字「World Fab Forecast Premium Sale price Member Price: $10,300.00 Non-Member Price: $15,000.00」。',
    ),
    '国际半导体产业协会 SEMI https://www.semi.org/en/semi-press-release/（SEMI·设备与材料市场统计）': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='Equipment Market Data Subscription Member $3,350 / Non-Member $7,050；'
                 'Global Semiconductor Packaging Materials Outlook Member $7,500 / Non-Member $9,500',
        计价方式='年费（订阅）或单报告',
        免费替代源='SEMI 新闻稿免费公开设备账单与材料出货的年度结论',
        替代源能力差距='新闻稿只给年度总量与分项结论，**完整设备账单与材料出货金额序列属付费订阅**',
        建议='买',
        备注='**SEMI store 对本机被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见**。',
    ),
    'SEMI《Worldwide Semiconductor Equipment Market Statistics (WWSEMS)》 https://www.semi.org/': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='Equipment Market Data Subscription Member $3,350 / Non-Member $7,050；'
                 'Historical WWSEMS Report (1991–2025) Member $3,050 / Non-Member $4,100',
        计价方式='年费（EMDS）或单报告',
        免费替代源='SEMI 新闻稿免费公开设备账单总额与分地区表',
        替代源能力差距='新闻稿只有总额与分地区表，**完整历史序列属付费订阅**；账单按客户所在地统计（G-04）',
        建议='买',
        备注='**SEMI store 对本机被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见**。',
    ),
    'SEMI《World Fab Forecast》 https://www.semi.org/': dict(
        数据商='国际半导体产业协会 SEMI', 覆盖度='全覆盖',
        官网价格页URL='https://store-us.semi.org/collections/market-intelligence',
        公开价格='World Fab Forecast Subscription Member $7,550 / Non-Member $11,100；'
                 'World Fab Forecast Single Edition Member $3,800 / Non-Member $5,600',
        计价方式='年费（订阅）或单报告',
        免费替代源='SEMI 新闻稿免费公开部分产能表',
        替代源能力差距='新闻稿只给部分产能表；**完整 Fab Forecast 属付费订阅**；为设计产能（G-02）',
        建议='买',
        备注='**SEMI store 对本机被限流（实测 429 / 403），价格为该 URL 的搜索引擎索引副本所见**。',
    ),
    # ---------------- WSTS / ESIA / SIA / CSIA ----------------
    '全球半导体贸易统计 WSTS https://www.wsts.org/76/Recent-News-Release（WSTS·市场统计新闻稿）': dict(
        数据商='全球半导体贸易统计 WSTS（经 ESIA 独家许可订阅）', 覆盖度='部分',
        官网价格页URL='https://www.eusemiconductors.eu/esia/industry-statistics/wsts',
        公开价格='Full WSTS Package € 11,500；Blue and Green Books € 10,500',
        计价方式='年费（年度订阅，自 1 月起）',
        免费替代源='WSTS 新闻稿免费（https://www.wsts.org/76/Recent-News-Release ）；SIA 月度销售统计（免费）',
        替代源能力差距='新闻稿只给全球总量与分品类（Memory/Logic/Analog/Micro/Discrete/Sensors）的年度预测与增速；'
                      '**按地区与 190+ 细分品类的月度账单属会员付费**',
        建议='买',
        备注='**WSTS 新闻稿实测可达**（当前为「Global Semiconductor Market Surges Beyond $1.5T 2026」，含 PDF 下载）；'
             'WSTS 官网 Contact 页列 Administrator and CEO Tobias Pröttel（tp@wsts.org）。'
             '口径：按市场所在地统计、销售额为出厂价口径（G-04）。',
    ),
    'WSTS 世界半导体贸易统计组织 https://www.wsts.org/ ；中国半导体行业协会转载 https://web.csia.net.cn/': dict(
        数据商='WSTS + 中国半导体行业协会 CSIA', 覆盖度='部分',
        官网价格页URL='https://www.eusemiconductors.eu/esia/industry-statistics/wsts',
        公开价格='Full WSTS Package € 11,500；Blue and Green Books € 10,500',
        计价方式='年费（年度订阅）',
        免费替代源='WSTS 新闻稿免费；CSIA 协会运行情况报告免费转载全球总量与分项',
        替代源能力差距='免费源给全球总量与主要分项，**半年度分品类明细与完整历史序列需会员订阅**；'
                      'CSIA 转载为协会口径，与 WSTS 原表的品类划分不完全对应',
        建议='买',
        备注='WSTS 新闻稿实测可达。询价入口：WSTS 官网 Contact（tp@wsts.org）；ESIA 订阅表单。',
    ),
    '欧洲半导体产业协会 ESIA https://www.esia.eu/（ESIA·欧洲半导体产业统计）': dict(
        数据商='欧洲半导体产业协会 ESIA', 覆盖度='部分',
        官网价格页URL='https://www.eusemiconductors.eu/esia/industry-statistics/wsts',
        公开价格='Full WSTS Package € 11,500；Blue and Green Books € 10,500',
        计价方式='年费（年度订阅）',
        免费替代源='ESIA 季度新闻稿免费给欧洲半导体销售额与同比（WSTS 口径）',
        替代源能力差距='季度新闻稿只给总额与增速，**设计/制造/封测结构与 24 国明细属订阅**；'
                      '且欧洲口径含欧洲注册企业的全球产值（按总部归属），与中国按产地口径错位（G-04）',
        建议='买',
        备注='**esia.eu 已停用 —— 实测跳转至域名停放售卖页「esia.eu is for sale!」，不可再作来源**；'
             'ESIA 现行站点为 https://www.eusemiconductors.eu/esia/ （实测 HTTP 200），并持有 WSTS 独家许可、公开标价。'
             '订阅价逐字「Full WSTS Package: € 11,500」「Blue and Green Books: € 10,500」。',
    ),
    '美国半导体行业协会 SIA https://www.semiconductors.org/（SIA·全球半导体销售统计（WSTS 口径））': dict(
        数据商='美国半导体行业协会 SIA', 覆盖度='全覆盖',
        官网价格页URL='https://www.semiconductors.org/data-resources/market-data/',
        公开价格='免费公开发布（月度新闻稿）', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（SIA 月度全球半导体销售额统计）',
        替代源能力差距='SIA 采用 WSTS 销售额口径（按总部归属），**与中国 IC 产业销售额（按产地）不可直接并列**（G-04）；'
                      '只有总量与品类同比，无分国别明细',
        建议='不买',
        备注='实测 https://www.semiconductors.org/ 及其 /data-resources/market-data/ 均 HTTP 200。**本行不需要采购**。',
    ),
    '中国半导体行业协会运行情况系列 https://web.csia.net.cn/ ；上市公司定期报告（上市公司）': dict(
        数据商='中国半导体行业协会 CSIA + 上市公司定期报告', 覆盖度='部分',
        官网价格页URL='https://web.csia.net.cn/',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（CSIA 公开文章 + 巨潮资讯网 cninfo.com.cn 定期报告）',
        替代源能力差距='协会文章给的是**定性描述与总量**（如「国内主要晶圆代工企业产能利用率均保持在90%以上」），'
                      '**没有厂商级产能利用率字段**；上市公司公告需解析正文，且各家口径不一',
        建议='不买',
        备注='**库内 URL https://web.csia.net.cn/newsinfo/ 实测 HTTP 404（列表页需带文章 id）**；'
             '可用单篇地址示例：https://web.csia.net.cn/newsinfo/11310429.html 、https://web.csia.net.cn/newsinfo/11304672.html （均实测 HTTP 200）。',
    ),
    '中国半导体行业协会·集成电路产业运行情况系列 https://web.csia.net.cn/newsinfo/（中国半导体行业协会·集成电路产业运行情况系列）（未核到：2026-11-23 实测 HTTP 404）': dict(
        数据商='中国半导体行业协会 CSIA', 覆盖度='部分',
        官网价格页URL='https://web.csia.net.cn/newsinfo/11310429.html',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（CSIA 公开文章）；国家统计局集成电路行业利润与投资月度数据',
        替代源能力差距='协会文章给产量、出口、增速与投资等总量，**全产业链分环节自给率与进口依存度无免费完整公开源**，须自建口径',
        建议='不买',
        备注='**已修正库内死链**：https://web.csia.net.cn/newsinfo/ 实测 HTTP 404 —— 列表页不带文章 id 打不开；'
             '改用单篇地址（实测 HTTP 200）。引用前仍须人工核原文。',
    ),
    # ---------------- SNE / CBEA ----------------
    'SNE Research https://www.sneresearch.com/en/insight/release_view/（SNE Research）': dict(
        数据商='SNE Research', 覆盖度='全覆盖',
        官网价格页URL='https://www.sneresearch.com/en/business/report_view/148/page/0',
        公开价格='$15,000（Global EV & Battery Quarterly Forecast，单份报告，'
                 'https://www.sneresearch.com/en/business/report_view/148/page/0）；'
                 '$5,000（Global xEV market and Battery supply & demand outlook，单份报告，'
                 'https://sneresearch.com/en/business/report_view/38/page/130）；'
                 'Monthly Tracker 订阅费未公开，需询价',
        计价方式='单报告（订阅另计）',
        免费替代源='SNE Insight 摘要页免费给月度装机量与厂商份额结论'
                   '（https://www.sneresearch.com/en/insight/release_view/706/page/12 ）；电池网 CBEA 免费给中国口径',
        替代源能力差距='摘要页只给当期结论（如 2026 年 1–6 月全球 EV 电池装机 608.5GWh、同比 +20.0%，CATL 39.9%、BYD 14.4%），'
                      '**完整历史序列与可选分项（四种主材 SCM）属付费 Monthly Tracker**；'
                      '且为装机（装车）口径，与电池联盟装机口径样本范围不同',
        建议='买',
        备注='价格逐字「Page Price $15,000」（report_view/148）与「Page Price $5,000」（report_view/38/page/130），两页实测 HTTP 200。'
             '订阅侧逐字「※ Subscription pricing is available upon request」；Monthly Tracker 产品页逐字「Please contact Sales Team '
             '(sales@sneresearch.com) for any inquiries on Global EV & Battery Monthly Tracker … including service subscription fee」——**订阅价未公开**。'
             '结构化产品页（均实测 200）：Global EV & Battery Monthly Tracker (Web App) '
             'https://www.sneresearch.com/en/business/report_view/272/page/0 ；'
             'Global LIB ESS Quarterly Tracker（明文「Provided in Excel format」）'
             'https://www.sneresearch.com/en/business/report_view/265/page/0 。'
             '免费新闻稿含月度装机与 ESS 出货结论（如 2026 年 1–9 月全球 EV 电池用量 994.5GWh、同比 +19.7%）。'
             '询价入口：https://www.sneresearch.com/en/about/contact/ ；sales@sneresearch.com ；+82-70-4006-0355。',
    ),
    '电池网 CBEA（电池联盟《动力电池月度信息》发布渠道）http://www.cbea.com/ ；SNE Research（需采购）': dict(
        数据商='电池网 CBEA + SNE Research', 覆盖度='部分',
        官网价格页URL='https://www.sneresearch.com/en/insight/release_view/',
        公开价格=NP, 计价方式='未公开（订阅）',
        免费替代源='电池网 CBEA《动力电池月度信息》免费公开发布 http://www.cbea.com/ （实测 HTTP 200）',
        替代源能力差距='CBEA 只覆盖中国且为月度信息简报，**全球口径与厂商份额需 SNE 付费订阅**',
        建议='买',
        备注='本行是 CBEA（中国免费）+ SNE（全球付费）的拼接，两家样本范围不同不可混列。询价入口：SNE 站内 Contact。',
    ),
    # ---------------- 医药 ----------------
    '国际制药协会联盟 IFPMA 统计 https://www.ifpma.org/（IFPMA·医药市场统计）': dict(
        数据商='国际制药协会联盟 IFPMA', 覆盖度='全覆盖',
        官网价格页URL='https://www.ifpma.org/',
        公开价格='免费公开发布（协会统计免费）', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（IFPMA 医药市场统计与 Facts & Figures 出版物）',
        替代源能力差距='会员自报汇总、**样本偏大药企**；金额需换算（G-07）；与 IQVIA 口径不同',
        建议='不买',
        备注='实测 IFPMA 首页 HTTP 200；**库内 https://www.ifpma.org/publications/data-center/ 实测 404**，'
             '现行出版物入口为 https://www.ifpma.org/news-and-resources/#publications （302 跳转到该页）。**本行不需要采购**。',
    ),
    '欧洲制药工业协会 EFPIA https://www.efpia.eu/（EFPIA·医药市场统计）': dict(
        数据商='欧洲制药工业协会 EFPIA', 覆盖度='全覆盖',
        官网价格页URL='https://www.efpia.eu/',
        公开价格='免费公开发布（协会统计免费）', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（EFPIA 医药市场统计与数据中心）',
        替代源能力差距='会员上报汇总，公开页含摘要；金额需换算（G-07）；报销机制的国别细节需逐国读',
        建议='不买',
        备注='实测 EFPIA 首页与 /publications/data-center/ 均 HTTP 200。**本行不需要采购**。',
    ),
    '美国 CMS 医疗保健与 Medicare https://www.cms.gov/（CMS·Medicare 与处方药统计）': dict(
        数据商='美国 CMS（Medicare 与处方药统计）', 覆盖度='全覆盖',
        官网价格页URL='https://www.cms.gov/',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（CMS Medicare Part D 处方药覆盖与支出数据）',
        替代源能力差距='美国药价为**净价/WAC 口径**，与中国集采价不可并列（G-05）；金额需换算（G-07）',
        建议='不买',
        备注='本机直连 cms.gov 实测 HTTP 403（需真实浏览器）。**本行不需要采购**。',
    ),
    '美国 CMS 医疗保健与 Medicare https://www.cms.gov/（CMS·处方药支出统计）': dict(
        数据商='美国 CMS（免费）+ IQVIA（付费源）', 覆盖度='部分',
        官网价格页URL='https://www.cms.gov/',
        公开价格='CMS 部分免费公开发布；IQVIA 未公开，需询价',
        计价方式='不适用（免费）＋ IQVIA 授权订阅',
        免费替代源='本行即免费源（CMS 处方药支出统计）',
        替代源能力差距='CMS 给美国医保口径的处方药支出，**不含商业渠道与院外零售结构**；'
                      '渠道结构与终端明细需 IQVIA 付费源（IQVIA 未取到可读摘要页）',
        建议='等',
        备注='库内备注已写明「IQVIA 未取到可读摘要页，仅登记需采购，本条以 CMS 官方口径作免费替代」。'
             '本机直连 cms.gov 实测 HTTP 403。',
    ),
    '商务部药品流通行业运行统计分析报告（需采购）；米内网（需采购）': dict(
        数据商='商务部 + 米内网 Menet', 覆盖度='部分',
        官网价格页URL='https://www.menet.com.cn/menetDatabase/database.html',
        公开价格=NP, 计价方式='未公开（数据库订阅）',
        免费替代源='商务部年度《药品流通行业运行统计分析报告》免费摘要；国家统计局药品类零售数据',
        替代源能力差距='摘要只给总量与增速，**终端月度销售额、连锁门店数与百强连锁集中度需付费数据库**',
        建议='买',
        备注='米内网格局库覆盖口径：城市实体药店终端「2015至今，年度呈现，季度更新」、医院端「2013年至今」。'
             '产品页逐字只有「咨询价格」「申请试用」，无金额 → 未公开，需询价。'
             '询价入口：https://www.menet.com.cn/home/contact （13 位业务代表 + 座机，无 email）。',
    ),
    '巨潮资讯网·上市公司定期报告与临时公告 https://www.cninfo.com.cn/（巨潮资讯网·上市公司定期报告与临时公告）': dict(
        数据商='巨潮资讯网 CNINFO', 覆盖度='部分',
        官网价格页URL='https://www.cninfo.com.cn/',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（巨潮资讯网公告全文）',
        替代源能力差距='公告是**一手披露但非结构化**，汇总口径（ANDA/ANDA 获批数量）须人工逐条统计，'
                      '无免费完整数据库',
        建议='不买',
        备注='实测 cninfo.com.cn HTTP 200。**本行不需要采购**，缺口在人工统计而非数据源。',
    ),
    # ---------------- 有色与锂 ----------------
    '欧洲有色金属协会 Eurometaux https://eurometaux.eu/（Eurometaux·行业统计）': dict(
        数据商='欧洲有色金属协会 Eurometaux', 覆盖度='部分',
        官网价格页URL='https://european-metals.eu/',
        公开价格=NP, 计价方式='未公开（协会统计/会员）',
        免费替代源='Eurostat 采掘业与外贸统计（免费）',
        替代源能力差距='Eurometaux 公开统计给协会口径的成员国总量，**欧洲锂矿开采量小、精度低**；'
                      '进口依赖度需 Eurostat 口径拼接，两者不可混列',
        建议='等',
        备注='**eurometaux.eu 实测跳转至 https://european-metals.eu/ （已更名/换域名），引用新域名**。',
    ),
    '欧洲有色金属协会 Eurometaux https://eurometaux.eu/（Eurometaux·有色金属统计）': dict(
        数据商='欧洲有色金属协会 Eurometaux', 覆盖度='部分',
        官网价格页URL='https://european-metals.eu/',
        公开价格=NP, 计价方式='未公开（协会统计/会员）',
        免费替代源='Eurostat 采掘业产量与能源价格统计（免费）',
        替代源能力差距='协会统计给原铝产量与冶炼成本结论，**电力成本到 EUR/kWh 的口径与欧盟电价指标不一致**，'
                      '跨源比较须换算（G-07）',
        建议='等',
        备注='**eurometaux.eu 实测跳转至 https://european-metals.eu/ ，引用新域名**。',
    ),
    'UNIDO 工业统计 https://stat.unido.org/（UNIDO·INDSTAT）': dict(
        数据商='UNIDO 工业统计（INDSTAT）', 覆盖度='全覆盖',
        官网价格页URL='https://stat.unido.org/',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（INDSTAT）+ USGS + ICSG/INSG',
        替代源能力差距='各国上报口径、**纯度分档不同**；仅年度、滞后约 1 年；不能与月度交易所口径并列',
        建议='不买',
        备注='本机直连 stat.unido.org 实测 HTTP 403（需真实浏览器）。**本行不需要采购**。',
    ),
    '国际铜业研究组织 ICSG http://icsg.org/（ICSG 与交易所公开数据入口）': dict(
        数据商='国际铜业研究组织 ICSG + 交易所（LME/SHFE/COMEX）', 覆盖度='全覆盖',
        官网价格页URL='https://icsg.org/',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（ICSG 月度公报 + LME/SHFE/COMEX 仓库库存页）',
        替代源能力差距='**交易所库存 ≠ 社会库存**（G-06）；注册仓单与注销仓单需逐交易所抓取，无单一汇总库',
        建议='不买',
        备注='实测 icsg.org HTTP 200（http 跳 https）、/publications-list/ 200、SHFE 日周数据 200。'
             '**ICSG Copper Bulletin 年刊公开标价 €600（会员）/ €1,200（非会员）——本表唯一的协会类公开标价，'
             '可作询价基准；但该年刊给的是精炼铜产量与用量，不含本行要的交易所注册仓单。**'
             'ICSG 月度新闻稿免费，Copper Bulletin 是报告不是结构化数据。**本行不需要采购**。',
    ),
    # ---------------- 储能与光伏 ----------------
    '中关村储能产业技术联盟 CNESA《储能产业研究白皮书》（EPC 成本章节） https://www.cnesa.org/information/detail/?column_id=1&id=7798': dict(
        数据商='中关村储能产业技术联盟 CNESA', 覆盖度='全覆盖',
        官网价格页URL='https://www.cnesa.org/information/detail/?column_id=1&id=7798',
        公开价格=NP, 计价方式='未公开（白皮书订阅/会员）',
        免费替代源='CNESA 白皮书摘要页与官网公开图表（免费）：'
                   'https://www.cnesa.org/information/detail/?column_id=1&id=7798',
        替代源能力差距='摘要免费公开单位造价要点，**按 2h/4h 分档的逐期明细在付费白皮书内**；'
                      '且免费文给的单位是**元/kWh**，本库口径是**元/Wh** → 换算须除以 1000，'
                      '不可直接并列（免费文原话「根据 CNESA DataLink 全球储能数据库的不完全统计」→ 样本不完整）',
        建议='等',
        备注='**该免费文章实测 HTTP 200，页内含真实数字（2025 年口径）**：'
             '「2h EPC 的中标均价已降至 1043.82 元/kWh，同比下降 13.04%；4h EPC 的中标均价为 935.40 元/kWh，同比下降 8.19%」；'
             '「2025 年储能系统（磷酸铁锂系统，不含用户侧应用）采购中标价格在 391.14 元/kWh-913.00 元/kWh 之间」；'
             '**单位是 元/kWh（元/Wh 在页内 0 处）**。'
             '询价入口：https://www.cnesa.org/ 站内联系方式 / 白皮书订阅。',
    ),
    '中关村储能产业技术联盟 CNESA《储能产业研究白皮书》（招标章节） https://www.cnesa.org/': dict(
        数据商='中关村储能产业技术联盟 CNESA', 覆盖度='全覆盖',
        官网价格页URL='https://www.cnesa.org/',
        公开价格=NP, 计价方式='未公开（白皮书订阅/会员）',
        免费替代源='CNESA 公开招标量与中标均价图（免费）',
        替代源能力差距='摘要公开招标量与中标均价走势图，**逐项目容量、逐月明细与厂商格局在付费白皮书内**',
        建议='等',
        备注='实测 cnesa.org HTTP 200。询价入口：站内联系方式。',
    ),
    '中关村储能产业技术联盟 CNESA《储能产业研究白皮书》（中标价格章节） https://www.cnesa.org/': dict(
        数据商='中关村储能产业技术联盟 CNESA', 覆盖度='全覆盖',
        官网价格页URL='https://www.cnesa.org/',
        公开价格=NP, 计价方式='未公开（白皮书订阅/会员）',
        免费替代源='CNESA 公开中标均价走势图（免费）：'
                   'https://www.cnesa.org/information/detail/?column_id=1&id=7798',
        替代源能力差距='摘要公开中标均价走势图，**逐月中标均价明细需订阅**；'
                      '免费文给的区间（391.14–913.00 元/kWh，LFP 不含用户侧）是**年度不完全统计**，不是逐月中标序列',
        建议='等',
        备注='实测该详情页 HTTP 200，页内含 2025 年真实数字：'
             'LFP 储能系统采购中标价区间 391.14–913.00 元/kWh；2h EPC 1043.82 元/kWh、4h EPC 935.40 元/kWh。'
             '询价入口：站内联系方式。',
    ),
    '欧洲储能协会 EASE https://ease-storage.eu/（EASE·年度市场报告）': dict(
        数据商='欧洲储能协会 EASE（European Energy Storage Association）', 覆盖度='全覆盖',
        官网价格页URL='https://energystorageeurope.eu/',
        公开价格=NP, 计价方式='未公开（会员/报告）',
        免费替代源='EASE 年度市场报告摘要页（免费）',
        替代源能力差距='摘要公开、完整报告需会员；**各国 GW/GWh 换算时长假设不统一，须注明**',
        建议='等',
        备注='**ease-storage.eu 实测 301 跳转至 https://energystorageeurope.eu/ （EASE 已换域名），引用新域名**。',
    ),
    'SolarPower Europe 洞察与展望 https://www.solarpowereurope.org/（SolarPower Europe·Outlooks）': dict(
        数据商='SolarPower Europe', 覆盖度='全覆盖',
        官网价格页URL='https://www.solarpowereurope.org/',
        公开价格=NP, 计价方式='未公开（报告购买/会员）',
        免费替代源='SolarPower Europe Outlooks 摘要与部分图表（免费）',
        替代源能力差距='完整数据在付费报告内；**EUR/W 与中国元/W 不可直接并列，需换算**（G-07）；'
                      '组件价格含运费与条款差异',
        建议='等',
        备注='**库内 /insights/outlooks 实测 HTTP 404 → 改为首页 https://www.solarpowereurope.org/ （实测 200）**。'
             '询价入口：站内 Contact。',
    ),
    # ---------------- 其他 ----------------
    '国际可再生能源署 IRENA https://www.irena.org/Data（IRENA·Renewable Capacity Statistics）': dict(
        数据商='国际可再生能源署 IRENA', 覆盖度='全覆盖',
        官网价格页URL='https://www.irena.org/Data',
        公开价格='免费公开发布（无需采购）', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（Renewable Capacity Statistics 与 Renewable Generation Statistics 年度 PDF + CSV）',
        替代源能力差距='为**名义装机**，含在建未投产，须看状态分栏；与中国「新增发电装机」并网口径不同（G-01）；'
                      '规划值 ≠ 实际值',
        建议='不买',
        备注='本机直连 irena.org/Data 实测 HTTP 403（需真实浏览器）。**本行不需要采购**。',
    ),
    '欧盟 JRC 联合研究中心 http://joint-research-centre.ec.europa.eu/（JRC·工业与能源数据集）': dict(
        数据商='欧盟 JRC 联合研究中心', 覆盖度='全覆盖',
        官网价格页URL='https://joint-research-centre.ec.europa.eu/',
        公开价格='免费公开发布（欧盟开放数据门户）', 计价方式='不适用（免费）',
        免费替代源='欧盟委员会 CRMA 战略项目清单（第二批，2026-10-09 通过）免费公开项目/国家/阶段/产能：'
                   'https://single-market-economy.ec.europa.eu/sectors/raw-materials/areas-specific-interest/'
                   'critical-raw-materials/strategic-projects-under-crma_en （实测 200）；'
                   'JRC 联合研究中心（301 → /select-language?destination=/node/10050，实测 200）',
        替代源能力差距='**不是全量 gigafactory 清单**（仅 CRMA 战略项目）；产能为企业自报、口径不统一（G-01）；'
                      '**项目状态必须分栏（规划/在建/投产）**；事件驱动更新、无固定刷新节奏；JRC 数据门户自动访问被拒',
        建议='不买',
        备注='**库内 https://data.jrc.ec.europa.eu/ 实测 Request Rejected** → 改用欧盟委员会 CRMA 战略项目页（实测 200）。'
             '**本行不需要采购**。',
    ),
    '欧盟 JRC 联合研究中心 http://joint-research-centre.ec.europa.eu/（JRC·工业与供应链数据集）': dict(
        数据商='欧盟 JRC 联合研究中心', 覆盖度='全覆盖',
        官网价格页URL='https://joint-research-centre.ec.europa.eu/',
        公开价格='免费公开发布（欧盟开放数据门户）', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（JRC 工业与供应链数据集 https://joint-research-centre.ec.europa.eu/ ；'
                   'Eurostat PRODCOM https://ec.europa.eu/eurostat/databrowser/view/prodcom_nace_ind/default/table ）',
        替代源能力差距='给的是**欧盟口径的金额与进口依赖度**，**不含中国/美国对照**；金额需换算（G-07）',
        建议='不买',
        备注='实测 HTTP 200。**本行不需要采购**。',
    ),
    '国际汽车制造商组织 OICA https://www.oica.net/（OICA·世界汽车产量统计 + IEA·Global EV Outlook）': dict(
        数据商='国际汽车制造商组织 OICA + IEA', 覆盖度='全覆盖',
        官网价格页URL='https://oica.net/production-statistics/',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（OICA 产量统计 + IEA Global EV Outlook 报告）',
        替代源能力差距='**两源拼接**：OICA 产量作分母、IEA 渗透率作分子，**须分别标注来源与年份，不得当作单一来源**；分母口径见 G-03',
        建议='不买',
        备注='实测 oica.net/production-statistics/ HTTP 200（**库内 https://www.oica.net/ 会跳到该页**）。**本行不需要采购**。',
    ),
    '国际货币基金组织 IMF·Primary Commodity Prices https://www.imf.org/en/Publications/WEO（IMF·World Economic Outlook 数据库）': dict(
        数据商='国际货币基金组织 IMF', 覆盖度='全覆盖',
        官网价格页URL='https://www.imf.org/en/Publications/WEO/weo-database',
        公开价格='免费公开发布（下载需免费注册账号）', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（WEO 数据库 + Primary Commodity Prices 页）',
        替代源能力差距='IMF 全球聚合**含估算与插值**，与 OECD/世界银行口径不同，须注明来源；'
                      '贸易量增速为季度数据年化口径，与年度统计不可并列',
        建议='不买',
        备注='本机直连 imf.org 实测 HTTP 403。**库内 URL 指向 /Publications/WEO（403），实际数据库页为 /Publications/WEO/weo-database**。'
             '**本行不需要采购**。',
    ),
    '美国 FDA 药品审评 https://www.fda.gov/drugs（美国商务部工业与安全局官方政策入口）': dict(
        数据商='美国商务部工业与安全局 BIS（经 Federal Register 公示）', 覆盖度='部分',
        官网价格页URL='https://www.federalregister.gov/agencies/industry-and-security-bureau',
        公开价格='免费公开发布', 计价方式='不适用（免费）',
        免费替代源='本行即免费源（Federal Register 的 BIS 规则与清单公告）',
        替代源能力差距='公告是**规则原文**，**无结构化的清单条目字段表**；须按规则版本与生效日人工跟踪',
        建议='不买',
        备注='**库内 URL https://www.fda.gov/drugs 实测 HTTP 404，且与本行口径错位（FDA 药品审评 ≠ BIS 出口管制）**；'
             '已改用 Federal Register 的 BIS 机构页（实测 HTTP 200）。**本行不需要采购**。',
    ),
}
