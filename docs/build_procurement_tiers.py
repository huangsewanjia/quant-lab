#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-618 · 采购三档（10/20/30 万）建模与产物生成。

输入（只读，不改）
  1. docs/procurement_plan.csv            —— WORK-535 供应商 × 指标覆盖清单（12 列）
  2. WORK-541 的 paid_data_selfbuild.csv  —— 77 条 P0 的 A/B/C 判定（默认绝对路径）
  3. 价格页现场直读结果                   —— 见 PRICE_EVIDENCE，每行带 URL + 抓取时间 + 原文摘录
  4. CFETS 人民币汇率中间价               —— tushare fx_midrate，2026-11-26

输出
  docs/procurement_tiers.csv               —— 主表（一行 = 一档 × 一家 × 一个产品）
  docs/figures/fig1_budget_coverage.png    —— 图 1 预算–覆盖曲线
  docs/figures/fig2_coverage_structure.png —— 图 2 覆盖结构
  docs/figures/fig3_dataflow.png           —— 图 3 数据流
  docs/procurement_tiers_report.html       —— 单文件报告（图片 base64 内嵌）

约束
  * 每个产品覆盖的指标必须能在 procurement_plan.csv 里找到同一数据商、覆盖度为「全覆盖/部分」的行，
    否则构建失败（禁止凭产品名推断覆盖）。
  * 同一档内不允许两个产品覆盖同一指标（保证「覆盖条数」可从 CSV 直接相加复算）。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
FIGDIR = DOCS / "figures"
SELFBUILD_CSV = Path(
    os.environ.get(
        "SELFBUILD_CSV",
        "/srv/data/multica/workspaces/workspace-c8faa36a018a/work-541-66a10df14690"
        "/workdir/docs/paid_data_selfbuild.csv",
    )
)

# ---------------------------------------------------------------- 汇率（CFETS）
RATE_DATE = "2026-11-26"
RATES = {"CNY": 1.0, "USD": 6.7096, "EUR": 7.3380}
RATE_SOURCE = (
    "中国外汇交易中心人民币汇率中间价，2026-11-26：USD/CNY 6.7096（100 USD=670.96 CNY）、"
    "EUR/CNY 7.3380（100 EUR=733.80 CNY）；取数接口 tushare fx_midrate（SAFE/CFETS），"
    "trade_date=20261126。"
)

BUDGET = {"10万": 100_000, "20万": 200_000, "30万": 300_000}

# ------------------------------------------------- 价格证据（现场直读，2026-11-26T16:30Z）
FETCHED_AT = {
    "hibor": ("2026-11-26T16:30:08Z", "HTTP 200"),
    "trendforce": ("2026-11-26T16:30:11Z", "HTTP 200"),
    "woodmac": ("2026-11-26T16:30:17Z", "HTTP 200"),
    "sne": ("2026-11-26T16:30:45Z", "HTTP 200"),
    "esia": ("2026-11-26T16:30:51Z", "HTTP 200"),
    "ccid": ("2026-11-26T16:30:53Z", "HTTP 200"),
    "semi": ("2026-11-26T16:30:58Z", "HTTP 200"),
}

# ---------------------------------------------------------------- 产品目录
HARD_FIVE_RESEARCH = [
    "储能产业链利润分布（电芯/PCS/系统/EPC/运营）",
    "有色与锂产业链利润分布（矿山/冶炼/材料/回收）",
    "医药产业链利润分布（创新药/CXO/原料药/流通）",
    "半导体产业链价值分布（设计/制造/封测/设备/材料）",
    "新能源汽车产业链利润分布",
]
# indicators：必须与 docs/procurement_plan.csv 同数据商、覆盖度为 全覆盖/部分 的行逐条对上。
P = [
    dict(
        pid="hibor_pro",
        supplier="慧博投研（研报聚合平台）",
        plan_supplier="慧博投研（研报聚合平台）",
        product="专业版会员 1 年（每日 150 篇阅读上限，单席位）",
        pricing="年费（按版本与年限）",
        quote="¥3,996/年",
        cur="CNY",
        amount=3996,
        level="A",
        url="https://www.hibor.com.cn/price",
        ev="hibor",
        excerpt="【专业版】/ 1年 / 3996 / 333 / 12个月（基础版同页：1年 1668、单月 139）",
        indicators=list(HARD_FIVE_RESEARCH),
        caveat="研报是 PDF 与图表，不是结构化数据；「各环节利润率」拆分须先索取样张核对，"
        "核不到则本行作废（改为 0 覆盖）。合同限内部阅读，禁止转载与二次分发。",
    ),
    dict(
        pid="hibor_basic",
        supplier="慧博投研（研报聚合平台）",
        plan_supplier="慧博投研（研报聚合平台）",
        product="基础版会员 1 年（每日 50 篇阅读上限，单席位）",
        pricing="年费（按版本与年限）",
        quote="¥1,668/年",
        cur="CNY",
        amount=1668,
        level="A",
        url="https://www.hibor.com.cn/price",
        ev="hibor",
        excerpt="【基础版】/ 1年 / 1668 / 139 / 12个月",
        indicators=list(HARD_FIVE_RESEARCH),
        caveat="同专业版口径，仅阅读上限不同；本行只作为 10 万档的降配备选，不进三档组合。",
    ),
    dict(
        pid="ccid",
        supplier="赛迪顾问 / 中国电子信息产业发展研究院（CCID）",
        plan_supplier="赛迪顾问 / 中国电子信息产业发展研究院（CCID）",
        product="单份年度研究报告（PDF，非结构化数据集）",
        pricing="单报告",
        quote="假设 ¥10,000/份",
        cur="CNY",
        amount=10000,
        level="C",
        url="https://api.mtx.cn/report?command=detail&report_id=10022650",
        ev="ccid",
        excerpt="接口逐字返回 \"price\": 10000.0 —— 但该 report_id 对应《2024-2025年中国PCB市场研究年度报告》，"
        "不是半导体国产化报告",
        indicators=["半导体设备与材料国产化率"],
        caveat="假设 ¥10,000/份（依据：赛迪报告商店同类单份报告标价 ¥10,000，接口字段 price=10000.0，"
        "2026-11-26T16:30:53Z 直读；目标报告《半导体设备与材料国产化率》的存在性与标价须站内检索确认）。"
        "±50%（¥5,000–¥15,000）：10 万档仍成立（¥99,847 或 ¥89,847，均在预算内）。",
    ),
    dict(
        pid="tf_silver",
        supplier="TrendForce 集邦",
        plan_supplier="TrendForce 集邦",
        product="DRAMeXchange Silver Membership（DRAM/NAND 现货价 + 月度合约价，年度）",
        pricing="年费（会员订阅）",
        quote="USD 5,000/年",
        cur="USD",
        amount=5000,
        level="A",
        url="https://www.trendforce.com/membership",
        ev="trendforce",
        excerpt="Silver 权益块收尾逐字：… DRAM/NAND Market View / Daily News Alert / Contact Us / USD 5,000；"
        "下一档 Gold 标 USD 11,000",
        indicators=["存储芯片现货价与合约价"],
        caveat="报价单位是每颗芯片规格（如 DDR5 16Gb 4800 → 72.00 美元），不是本库要求的 USD/GB，入库须按容量折算；"
        "5 年以上历史与合约价明细属会员功能。",
    ),
    dict(
        pid="semi_emds",
        supplier="SEMI 国际半导体产业协会",
        plan_supplier="国际半导体产业协会 SEMI",
        product="Equipment Market Data Subscription (EMDS)，非会员年费",
        pricing="年费（订阅）",
        quote="$7,050/年（非会员价）",
        cur="USD",
        amount=7050,
        level="A",
        url="https://store-us.semi.org/collections/market-intelligence",
        ev="semi",
        excerpt="Equipment Market Data Subscription (EMDS) Sale price Member Price: $700 --> $3,350.00 "
        "Non-Member Price: $7,050.00",
        indicators=[
            "全球半导体设备与材料出货额",
            "全球半导体设备市场账单",
            "全球半导体设备市场账单（按地区）",
            "美国半导体设备市场账单",
        ],
        caveat="按非会员价计。会员价 $3,350 需另付 SEMI 会员费（金额未公开），未计入。"
        "「材料出货额」的明细部分需 Material Market Data Subscription（MMDS，非会员 $9,850）补齐，"
        "本行只覆盖设备口径。",
    ),
    dict(
        pid="semi_wff",
        supplier="SEMI 国际半导体产业协会",
        plan_supplier="国际半导体产业协会 SEMI",
        product="World Fab Forecast Subscription，非会员年费",
        pricing="年费（订阅）",
        quote="$11,100/年（非会员价）",
        cur="USD",
        amount=11100,
        level="A",
        url="https://store-us.semi.org/collections/market-intelligence",
        ev="semi",
        excerpt="World Fab Forecast Subscription Sale price Member Price: $700 --> $7,550.00 "
        "Non-Member Price: $11,100.00",
        indicators=[
            "全球晶圆制造产能与地区集中度",
            "晶圆产能与晶圆厂开工率",
            "欧洲晶圆厂与封测产能",
            "美国晶圆厂产能与地区分布",
        ],
        caveat="三档均不选：与 EMDS 合计 $18,150（≈¥121,764），10 万档装不下，20/30 万档的边际预算"
        "给了硬缺口供应商。见「为什么这么定」。",
    ),
    dict(
        pid="semi_wff_premium",
        supplier="SEMI 国际半导体产业协会",
        plan_supplier="国际半导体产业协会 SEMI",
        product="World Fab Forecast Premium，非会员年费",
        pricing="年费（订阅）",
        quote="$15,000/年（非会员价）",
        cur="USD",
        amount=15000,
        level="A",
        url="https://store-us.semi.org/collections/market-intelligence",
        ev="semi",
        excerpt="World Fab Forecast Premium Sale price Member Price: $10,300 --> Non-Member Price: $15,000.00",
        indicators=["全球晶圆产能与设备利用率"],
        caveat="只为 1 条指标（设备利用率），单条成本 ¥100,644，是三档候选里最贵的单点，不买。",
    ),
    dict(
        pid="semi_mmds",
        supplier="SEMI 国际半导体产业协会",
        plan_supplier="国际半导体产业协会 SEMI",
        product="Material Market Data Subscription (MMDS)，非会员年费",
        pricing="年费（订阅）",
        quote="$9,850/年（非会员价）",
        cur="USD",
        amount=9850,
        level="A",
        url="https://store-us.semi.org/collections/market-intelligence",
        ev="semi",
        excerpt="Material Market Data Subscription (MMDS) Sale price Member Price: $700 --> $4,450.00 "
        "Non-Member Price: $9,850.00",
        indicators=[],
        caveat="只补「全球半导体设备与材料出货额」的材料明细，不新增指标；单买性价比低，不买。",
    ),
    dict(
        pid="woodmac_esm",
        supplier="Wood Mackenzie",
        plan_supplier="Wood Mackenzie",
        product="Power Markets US Energy Storage Monitor Q3 2026（单份报告）",
        pricing="单报告",
        quote="$5,000/份",
        cur="USD",
        amount=5000,
        level="A",
        url="https://www.woodmac.com/reports/power-markets-us-energy-storage-monitor-q3-2026-150505404/",
        ev="woodmac",
        excerpt="storage monitor: Q3 2026 / 21 September 2026 / Get this report* $5,000 / You can pay by card or invoice",
        indicators=["全球储能系统与组件出货量"],
        caveat="美国口径季度监测，不覆盖全球；同一指标 Benchmark 订阅为全覆盖，因此 20/30 万档被替代，"
        "只在 10 万档作为「不加假设价」的备选。",
    ),
    dict(
        pid="benchmark",
        supplier="Benchmark Mineral Intelligence",
        plan_supplier="Benchmark Mineral Intelligence",
        product="电芯/锂价与产能订阅（含锂价评估、电芯价格、产能与管线）",
        pricing="订阅（需询价）",
        quote="假设 $12,000/年",
        cur="USD",
        amount=12000,
        level="C",
        url="https://www.benchmarkminerals.com/contact",
        ev=None,
        excerpt="官网全站无价目表，仅有联系表单（2026-11-23 核，页面 200，无公开报价）",
        indicators=[
            "全球储能系统与组件出货量",
            "全球电芯价格与产能利用率",
            "全球电芯价格",
            "全球锂价与长期合约价",
        ],
        caveat="假设 $12,000/年（依据：同类年订阅公开价带 SEMI $3,350–$15,000、TrendForce Silver $5,000、"
        "WoodMac 单报告 $5,000–$9,990、SNE 单报告 $15,000；Benchmark 为电芯价与锂价的定价基准商，取带内偏上值）。"
        "±50%（$6,000–$18,000）：$18,000 时 20 万档超预算 ¥15,620 → 处置＝缓买 TrendForce（−¥33,548，"
        "存储价先用公开现货价表过渡）；$6,000 时结余 ¥64,895，档位仍成立。",
    ),
    dict(
        pid="ggii",
        supplier="高工锂电 / 高工产业研究院 GGII",
        plan_supplier="高工锂电 / 高工产业研究院 GGII",
        product="中国电池产能与开工率数据服务（蓝皮书 + 会员）",
        pricing="年费（单份报告 + 会员年费）",
        quote="假设 ¥50,000/年",
        cur="CNY",
        amount=50000,
        level="C",
        url="https://www.gg-lb.com/",
        ev=None,
        excerpt="官网本机 DNS 解析失败（Could not resolve host，2026-11-23 实测），无公开价目表",
        indicators=["电池产能与产能利用率", "动力电池产能与开工率"],
        caveat="假设 ¥50,000/年（依据：同类中文产业数据库年费量级 ¥3–8 万，No.1 半区取 ¥5 万；"
        "无任何公开标价可核，属纯假设）。±50%（¥25,000–¥75,000）：¥75,000 时 30 万档 ¥280,362 仍成立。"
        "注意：这两条是 77 条里唯一被判 C（公开层确实薄）的指标，也是 30 万档唯一新增的硬缺口。",
    ),
    dict(
        pid="metnet",
        supplier="米内网 Menet",
        plan_supplier="米内网 Menet",
        product="中国三大终端六大市场格局库（医院端 + 药店端）年费",
        pricing="年费（数据库在线订阅）",
        quote="假设 ¥80,000/年",
        cur="CNY",
        amount=80000,
        level="C",
        url="https://www.menet.com.cn/",
        ev=None,
        excerpt="产品页只有「咨询价格」「申请试用」，无公开标价（2026-11-23 核）",
        indicators=["中国医药市场规模与增速", "药品零售终端销售额与连锁率"],
        caveat="假设 ¥80,000/年（依据：医药终端数据库（医院端+药店端）年费量级 ¥5–15 万，取带中值偏下；"
        "无公开标价）。±50%（¥40,000–¥120,000）：¥120,000 时 30 万档 ¥295,362 仍成立（但会挤掉 CNESA）。"
        "本行不进三档，只进 30 万档的「加钱清单」。",
    ),
    dict(
        pid="cnesa",
        supplier="中关村储能产业技术联盟 CNESA",
        plan_supplier="中关村储能产业技术联盟 CNESA",
        product="《储能产业研究白皮书》+ 联盟会员（中标价 / 单位造价 / 招标量三章）",
        pricing="年费（白皮书订阅/会员）",
        quote="假设 ¥30,000/年",
        cur="CNY",
        amount=30000,
        level="C",
        url="https://www.cnesa.org/",
        ev=None,
        excerpt="白皮书为会员/订阅制，无公开标价（2026-11-23 核）",
        indicators=[
            "储能系统与 EPC 中标均价",
            "储能系统单位造价（元/Wh）",
            "储能系统招标量与中标容量",
        ],
        caveat="假设 ¥30,000/年（依据：中文行业协会年鉴/白皮书年费量级 ¥1–5 万，取中值；无公开标价）。"
        "±50%（¥15,000–¥45,000）：¥45,000 时 30 万档 ¥270,362 仍成立。",
    ),
    dict(
        pid="wsts",
        supplier="WSTS 世界半导体贸易统计组织（经 ESIA 独家许可）",
        plan_supplier="WSTS + 中国半导体行业协会 CSIA",
        product="Full WSTS Package（年度订阅，含 Blue/Green Books 明细）",
        pricing="年费（年度订阅）",
        quote="€11,500/年",
        cur="EUR",
        amount=11500,
        level="A",
        url="https://www.eusemiconductors.eu/esia/industry-statistics/wsts",
        ev="esia",
        excerpt="The following packages are available: Full WSTS Package: € 11,500 Blue and Green Books: € 10,500",
        indicators=[
            "全球半导体市场规模与品类结构",
            "全球半导体市场规模（WSTS 口径）",
            "欧洲半导体产业规模与结构",
        ],
        caveat="总量（1.51 万亿美元）已由 WSTS 新闻稿免费发布，€11,500 买的是品类与地区明细；"
        "30 万档的边际预算给了 GGII（硬缺口）与本项二选一，选中 GGII。",
    ),
    dict(
        pid="sne",
        supplier="SNE Research",
        plan_supplier="SNE Research",
        product="Global EV & Battery Quarterly Forecast（单份报告）",
        pricing="单报告",
        quote="$15,000/份",
        cur="USD",
        amount=15000,
        level="A",
        url="https://www.sneresearch.com/en/business/report_view/148/page/0",
        ev="sne",
        excerpt="Price $15,000 (VAT Separate)",
        indicators=["全球动力电池装机量与厂商份额"],
        caveat="只覆盖 1 条（且该条已被 SNE 公开新闻稿部分覆盖，属 A 可自建 3 人日）；"
        "PCS 出货与半导体价值分布已核实为「不覆盖」，不能拿来充数。",
    ),
    dict(
        pid="spglobal",
        supplier="S&P Global Commodity Insights（Platts）",
        plan_supplier="S&P Global Commodity Insights（Platts）",
        product="锂/镍/钴价格评估订阅",
        pricing="订阅（需询价）",
        quote="假设 $25,000/年",
        cur="USD",
        amount=25000,
        level="C",
        url="https://www.spglobal.com/commodityinsights/",
        ev=None,
        excerpt="官网全域 403（Akamai，2026-11-23 实测），无公开价目表",
        indicators=["全球锂盐与镍钴价格"],
        caveat="假设 $25,000/年（依据：三大价格评估机构年订阅量级 $2–5 万，取带中值；无公开标价）。"
        "±50%（$12,500–$37,500）：$37,500 时 30 万档超预算 ¥22,092 → 需砍掉 Benchmark 或改用月度单点采购。"
        "本行不进三档，只进「加钱清单」。",
    ),
    dict(
        pid="pharmcenter",
        supplier="中国医药工业信息中心",
        plan_supplier="中国医药工业信息中心",
        product="《中国医药工业统计年报》+ PDB 样本医院数据（年费）",
        pricing="年费（按年征订）",
        quote="假设 ¥50,000/年",
        cur="CNY",
        amount=50000,
        level="C",
        url="https://www.cphi.cn/",
        ev=None,
        excerpt="征订页只写「订阅」，无金额（2026-11-23 核）",
        indicators=["医药产业链利润分布（创新药/CXO/原料药/流通）"],
        caveat="假设 ¥50,000/年（依据：同口径年鉴+数据库年费 ¥3–8 万，取中值；无公开标价）。"
        "§±50%：¥75,000 时若替换慧博则 10 万档超预算。与本行指标重叠的慧博已覆盖，不买（重叠即浪费）。",
    ),
    dict(
        pid="iqvia",
        supplier="IQVIA",
        plan_supplier="IQVIA",
        product="MIDAS / Analytics Link 授权订阅",
        pricing="订阅（需询价）",
        quote="假设 $60,000/年",
        cur="USD",
        amount=60000,
        level="C",
        url="https://www.iqvia.com/",
        ev=None,
        excerpt="全站无价目表（已核 /sitemap，2026-11-23）",
        indicators=["医药产业链利润分布（创新药/CXO/原料药/流通）"],
        caveat="假设 $60,000/年（依据：全球医药市场数据库机构授权量级 $5–8 万，取带中值；无公开标价）。"
        "±50%：$90,000 时 30 万档必超预算。与慧博指标重叠，不买。",
    ),
]

BY_ID = {p["pid"]: p for p in P}

# ------------------------------------------------- 三档组合（pid 顺序 = 表内顺序）
TIERS = {
    "10万": ["hibor_pro", "ccid", "tf_silver", "semi_emds"],
    "20万": ["hibor_pro", "ccid", "tf_silver", "semi_emds", "benchmark"],
    "30万": ["hibor_pro", "ccid", "tf_silver", "semi_emds", "benchmark", "ggii", "cnesa"],
}

# 对照组合（只用于「为什么这么定」）
CONTRASTS = {
    "只买一家（慧博基础版）": ["hibor_basic"],
    "只买 SEMI 全家桶（EMDS+WFF+Premium）": ["semi_emds", "semi_wff", "semi_wff_premium"],
    "硬缺口优先（无 SEMI/WSTS）": ["benchmark", "hibor_pro", "ggii", "metnet", "ccid", "tf_silver"],
    "全买最贵档（17 个候选产品）": [p["pid"] for p in P if p["pid"] != "hibor_basic"],
    "三档推荐（30万档）": TIERS["30万"],
}

# 不买清单（每档）
NOT_BUY = {
    "10万": [
        ("全球储能系统与组件出货量（WoodMac $5,000）", "¥33,548 只买 1 条且为美国口径；Benchmark 订阅为全覆盖，留到 20 万档一次买对。"),
        ("全球晶圆产能与设备利用率（WFF Premium $15,000）", "单条 ¥100,644，是全网最贵单点；免费新闻稿已能给到「利用率区间」，缺口是连续序列。"),
        ("半导体材料明细（MMDS $9,850）", "不新增指标，只加「设备与材料出货额」的材料明细；等设备口径先用一年再补。"),
        ("电池产能与开工率（GGII，假设 ¥50,000）", "加进来就超预算（¥94,847+¥50,000=¥144,847）；且 GGII 无公开价，10 万档不宜引入最大单笔假设。"),
        ("中国医药市场（米内网，假设 ¥80,000）", "单条 ¥80,000，占 10 万档 80%；先用上市公司定期报告与商务部年报近似。"),
        ("S&P 锂盐/镍钴价（假设 $25,000）", "¥167,740 已超本档预算，且 WSTS/ESIA 等 A 级项会被挤掉。"),
        ("外资投行研报（GS/MS/JPM/UBS/Citi/BofA）", "全部机构协议、量级未知；先用官网 Insights 免费摘要 + 巨潮公告替代。"),
    ],
    "20万": [
        ("World Fab Forecast $11,100", "与 EMDS 合计 ¥121,764；20 万档的增量预算给 Benchmark（4 条硬缺口）而不是晶圆产能（0 条硬缺口）。"),
        ("WSTS Full Package €11,500", "3 条指标均为「部分免费」，总量已免费；¥84,387 换 0 条硬缺口，排在硬缺口之后。"),
        ("电池产能与开工率（GGII，假设 ¥50,000）", "本档留给 30 万；先确认 GGII 能用商务渠道给出报价再进。"),
        ("中国医药市场（米内网，假设 ¥80,000）", "¥175,362+¥80,000=¥255,362 落在 30 万档区间，本档不装。"),
        ("S&P 锂盐/镍钴价（假设 $25,000）", "¥167,740 单条，买它就装不下 Benchmark（4 条硬缺口）。"),
    ],
    "30万": [
        ("S&P 锂盐/镍钴价（假设 $25,000）", "¥167,740 会挤掉 Benchmark + GGII（6 条硬缺口）；锂价先用 GFEX/生意社/广期所公开日行情 + Benchmark 现货评估替代。"),
        ("中国医药市场（米内网，假设 ¥80,000）", "¥255,362+¥80,000=¥335,362 超预算 ¥35,362；若医药是下一阶段主线，砍 CNESA（−¥30,000）仍超 ¥5,362，需连 TrendForce 一起砍。"),
        ("中金/中信研究部（机构协议）", "与慧博指标 100% 重叠且无公开标价；慧博 ¥3,996 已覆盖同一 5 条。"),
        ("中国医药工业信息中心 / IQVIA（假设 ¥50,000 / $60,000）", "与慧博「医药产业链利润分布」重叠，重叠即浪费。"),
        ("外资投行研报", "机构协议、量级未知、禁转载；三档都不进。"),
    ],
}


def load_universe():
    """77 条 P0，按 WORK-541 判定表；前 16 条 = 需采购（硬缺口），后 61 条 = 部分免费。"""
    rows = list(csv.DictReader(open(SELFBUILD_CSV, encoding="utf-8")))
    assert len(rows) == 77, len(rows)
    hard = [r["指标名"] for r in rows if "需采购" in r["数据商"]]
    partial = [r["指标名"] for r in rows if "需采购" not in r["数据商"]]
    assert len(hard) == 16 and len(partial) == 61, (len(hard), len(partial))
    return rows, hard, partial


MATCH = {
    "hibor_pro": ["慧博投研"], "hibor_basic": ["慧博投研"],
    "ccid": ["赛迪"], "tf_silver": ["TrendForce"], "semi_emds": ["SEMI"],
    "semi_wff": ["SEMI"], "semi_wff_premium": ["SEMI"], "semi_mmds": ["SEMI"],
    "woodmac_esm": ["Wood Mackenzie", "伍德麦肯兹"],
    "benchmark": ["Benchmark"], "ggii": ["GGII", "高工"], "metnet": ["米内网"],
    "cnesa": ["CNESA", "中关村储能"], "wsts": ["WSTS", "ESIA"], "sne": ["SNE Research"],
    "spglobal": ["S&P"], "pharmcenter": ["中国医药工业信息中心"], "iqvia": ["IQVIA"],
}


def load_plan():
    rows = list(csv.DictReader(open(DOCS / "procurement_plan.csv", encoding="utf-8")))
    by_ind = {}
    for r in rows:
        by_ind.setdefault(r["指标名"], []).append((r["数据商"], r["覆盖度(全覆盖/部分)"], r["建议(买/等/不买)"]))
    return rows, by_ind


def validate_products(by_ind, hard, partial):
    """每个产品声明的指标，必须在 535 清单里有同一数据商、覆盖度为 全覆盖/部分 的行。"""
    universe = set(hard) | set(partial)
    for p in P:
        keys = MATCH[p["pid"]]
        for ind in p["indicators"]:
            assert ind in universe, f"{p['pid']} 指标不在 77 条 P0 内：{ind}"
            hits = [(s, c) for s, c, _ in by_ind.get(ind, [])
                    if c in ("全覆盖", "部分") and any(k in s for k in keys)]
            assert hits, f"{p['pid']} 在 535 清单里找不到「{keys} × {ind}」的覆盖行"
    for pid in [x for t in TIERS.values() for x in t]:
        assert pid in BY_ID


def cny(p):
    return round(p["amount"] * RATES[p["cur"]])


def tier_rows(tier):
    """返回该档的行 + 去重后的指标集合。"""
    seen, out = set(), []
    for pid in TIERS[tier]:
        p = dict(BY_ID[pid])
        p["cny"] = cny(p)
        new = [i for i in p["indicators"] if i not in seen]
        assert len(new) == len(p["indicators"]), f"{tier}/{pid} 与同档其他产品覆盖重叠：{set(p['indicators']) & seen}"
        seen.update(new)
        out.append(p)
    return out, seen


def coverage_of(pids, hard):
    ind = set()
    for pid in pids:
        ind |= set(BY_ID[pid]["indicators"])
    return ind


def money(x):
    return f"{x:,.0f}"


def build_csv(hard):
    cols = [
        "档位(10万/20万/30万)", "供应商", "产品/订阅档", "计价方式", "报价", "币种",
        "折人民币", "价格证据等级", "价格来源URL", "该产品覆盖的P0指标(分号分隔)",
        "覆盖条数", "是否硬缺口", "备注",
    ]
    rows = []
    for tier in ["10万", "20万", "30万"]:
        prs, _ = tier_rows(tier)
        for p in prs:
            nh = len([i for i in p["indicators"] if i in hard])
            if nh == len(p["indicators"]):
                flag = f"是（{nh}/{len(p['indicators'])}）"
            elif nh == 0:
                flag = f"否（{len(p['indicators'])} 条均为部分免费）"
            else:
                flag = f"部分（硬缺口 {nh}/{len(p['indicators'])}）"
            if p["level"] == "C":
                note = "【C 级假设价，非成交价】" + p["caveat"]
            else:
                ts, code = FETCHED_AT[p["ev"]]
                note = (
                    f"【{p['level']} 级·官网直读】抓取时间 {ts}（{code}）；原文摘录：{p['excerpt']}。"
                    f"口径：含税年费/单席位/不含一次性实施费。{p['caveat']}"
                )
            rows.append({
                "档位(10万/20万/30万)": tier,
                "供应商": p["supplier"],
                "产品/订阅档": p["product"],
                "计价方式": p["pricing"],
                "报价": p["quote"],
                "币种": p["cur"],
                "折人民币": p["cny"],
                "价格证据等级": p["level"],
                "价格来源URL": p["url"],
                "该产品覆盖的P0指标(分号分隔)": ";".join(p["indicators"]),
                "覆盖条数": len(p["indicators"]),
                "是否硬缺口": flag,
                "备注": note,
            })
    path = DOCS / "procurement_tiers.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    return path, rows


def reconcile(hard, partial):
    lines = []
    tot_a = tot_b = tot_c = 0
    for tier in ["10万", "20万", "30万"]:
        prs, ind = tier_rows(tier)
        spend = sum(p["cny"] for p in prs)
        assert spend <= BUDGET[tier], (tier, spend)
        nh = len(ind & set(hard))
        cols = [p["level"] for p in prs]
        tot_a += cols.count("A"); tot_b += cols.count("B"); tot_c += cols.count("C")
        sum_rows = sum(len(p["indicators"]) for p in prs)
        lines.append(
            f"{tier}档：{len(prs)} 家、合计 ¥{money(spend)}（预算 ¥{money(BUDGET[tier])}，"
            f"结余 ¥{money(BUDGET[tier] - spend)}）；覆盖 {len(ind)}/77 条 P0"
            f"（硬缺口 {nh}/16、部分免费 {len(ind) - nh}/61）；行内覆盖条数之和 {sum_rows} = 去重 {len(ind)}；"
            f"单条年均 ¥{money(spend / len(ind))}；A/B/C = {cols.count('A')}/{cols.count('B')}/{cols.count('C')}"
        )
    n = tot_a + tot_b + tot_c
    lines.append(f"三档合计行数 {n}；A 级 {tot_a}（{tot_a / n:.1%}）、B 级 {tot_b}、C 级 {tot_c}。")
    return lines


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


if __name__ == "__main__":
    rows541, hard, partial = load_universe()
    plan_rows, by_ind = load_plan()
    validate_products(by_ind, hard, partial)
    path, csv_rows = build_csv(hard)
    print(f"[out] {path}  rows={len(csv_rows)}")
    for l in reconcile(hard, partial):
        print("[chk]", l)
    print(f"[in ] 541 csv sha256={sha(SELFBUILD_CSV)}  535 csv sha256={sha(DOCS / 'procurement_plan.csv')}")
