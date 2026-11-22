"""Rule-based Shenwan level-1 industry and topic tagging.

Rules are intentionally explicit: keyword hits plus a small agency prior.
When nothing matches, the document is tagged ``unlabeled`` instead of being
assigned a guessed industry.
"""

from __future__ import annotations

import re
from typing import Any

# (industry, strong keywords). Ordering does not matter; all hits are kept.
INDUSTRY_RULES: dict[str, tuple[str, ...]] = {
    "电力设备": (
        "储能", "新型储能", "抽水蓄能", "电化学储能", "光伏", "风电", "风力发电",
        "电网", "特高压", "配电网", "充电桩", "换电站", "电力设备", "发电装机",
        "虚拟电厂", "绿电", "电力现货", "电力市场", "输配电", "核电", "氢能",
        "锂离子电池", "电池", "逆变器", "分时电价", "容量电价",
    ),
    "汽车": (
        "新能源汽车", "新能源车", "汽车产业", "乘用车", "商用车", "双积分",
        "汽车以旧换新", "智能网联汽车", "充换电", "汽车下乡", "整车", "汽车零部件",
    ),
    "电子": (
        "半导体", "集成电路", "芯片", "晶圆", "光刻", "封测", "存储芯片",
        "EDA", "第三代半导体", "碳化硅", "氮化镓", "显示面板", "消费电子",
    ),
    "医药生物": (
        "药品", "医疗器械", "医保", "医药", "疫苗", "创新药", "仿制药",
        "中药", "生物医药", "临床", "药品监管", "药品集采", "医疗服务",
        "卫生健康", "医院", "药监局", "中医药",
    ),
    "有色金属": (
        "锂矿", "锂资源", "碳酸锂", "稀土", "铜矿", "电解铝", "铝土矿", "镍矿",
        "钴矿", "钨矿", "有色金属", "稀有金属", "矿产资源", "关键矿产", "锑矿", "钼矿",
    ),
    "银行": (
        "商业银行", "存款准备金", "LPR", "贷款市场报价利率", "银行业", "信贷",
        "存款", "贷款", "资本充足率", "流动性", "不良贷款", "政策性银行",
    ),
    "非银金融": (
        "证券", "保险", "期货", "资本市场", "股票", "债券市场",
        "公募基金", "私募基金", "证券投资基金", "基金管理人", "基金公司",
        "上市公司", "并购重组", "IPO", "注册制", "信托", "融资融券", "金融监管总局",
    ),
    "计算机": (
        "人工智能", "算力", "数据中心", "数据要素", "工业软件", "信创",
        "云计算", "大模型", "区块链", "网络安全", "数字经济",
    ),
    "公用事业": (
        "电价", "居民用电", "燃气", "供水", "供热", "污水处理", "环保电价",
    ),
    "煤炭": ("煤炭", "煤电", "煤矿", "焦煤", "动力煤"),
    "钢铁": ("钢铁", "粗钢", "钢材", "铁矿石", "炼钢", "电弧炉"),
    "基础化工": ("化工", "石化", "新材料", "氟化工", "磷化工", "农药", "化肥"),
    "机械设备": ("工程机械", "机床", "机器人", "高端装备", "轨道交通装备"),
    "国防军工": ("国防", "军工", "航天", "航空发动机", "卫星", "军民融合"),
    "农林牧渔": ("粮食", "农业", "种业", "生猪", "农产品", "农机", "农牧渔业", "森林采伐", "林区"),
    "房地产": ("房地产", "住房", "商品房", "保障性住房", "土地出让", "公积金"),
    "建筑装饰": ("基础设施", "基建", "建筑工程", "城市更新", "专项债项目", "水利工程", "国土空间规划", "工程建设"),
    "交通运输": ("铁路", "公路", "港口", "航运", "民航", "物流", "收费公路", "交通运输"),
    "食品饮料": ("食品", "白酒", "乳制品", "食品安全", "饮料"),
    "家用电器": ("家电", "以旧换新", "空调", "冰箱"),
    "纺织服饰": ("纺织", "服装", "棉花", "化纤"),
    "轻工制造": ("造纸", "家具", "包装", "玩具", "文教用品"),
    "商贸零售": ("零售", "电子商务", "商贸", "免税", "消费券"),
    "社会服务": ("旅游", "教育", "养老", "家政", "文化娱乐", "体育", "学位", "学生资助", "职业教育"),
    "传媒": ("出版", "影视", "游戏", "广告", "互联网内容", "新闻出版"),
    "通信": ("5G", "6G", "光通信", "运营商", "通信网络", "卫星互联网", "号码保护", "电信业务", "通信管理局"),
    "石油石化": ("石油", "天然气", "炼油", "油气", "成品油"),
    "环保": ("污染防治", "碳达峰", "碳中和", "碳排放", "生态保护", "固废"),
    "美容护理": ("化妆品", "医美", "日化"),
    "综合": ("国民经济和社会发展", "五年规划"),
}

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "货币政策": ("货币政策", "存款准备金率", "公开市场操作", "MLF", "逆回购", "LPR"),
    "财政政策": ("财政政策", "专项债", "国债", "转移支付", "减税降费", "财政补贴"),
    "资本市场": ("资本市场", "证券发行", "上市公司", "退市", "信息披露"),
    "产业政策": ("产业结构调整", "产业政策", "制造业", "产业链", "供应链"),
    "监管规则": ("管理办法", "管理规定", "实施细则", "监管", "行政许可"),
    "价格机制": ("价格机制", "定价", "电价", "气价", "收费"),
    "补贴与税收": ("补贴", "税收优惠", "增值税", "所得税", "关税", "出口退税"),
    "规划计划": ("规划", "行动计划", "实施方案", "指导意见"),
    "标准规范": ("国家标准", "行业标准", "技术规范", "标准体系"),
    "对外开放": ("自由贸易", "外资", "进出口", "跨境", "一带一路"),
}

AGENCY_PRIOR: dict[str, str] = {
    "国家能源局": "电力设备",
    "国家药监局": "医药生物",
    "国家卫生健康委": "医药生物",
    "国家医保局": "医药生物",
    "中国证监会": "非银金融",
    "中国人民银行": "银行",
}

# Words that are too common in unrelated policy text. They only count when the
# title names the industry; otherwise one speech about "教育" would tag every
# party-building document as 社会服务.
WEAK_KEYWORDS = {
    "教育",
    "养老",
    "家政",
    "体育",
    "文化娱乐",
    "粮食",
    "农业",
    "农产品",
    "基础设施",
    "基建",
    "城市更新",
    "住房",
    "物流",
    "新闻出版",
    "综合",
    "国民经济和社会发展",
}


def _hits(
    text: str, rules: dict[str, tuple[str, ...]], title: str = "", limit: int | None = None
) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for label, keywords in rules.items():
        body_hits = 0
        strong_hits = 0
        title_hit = False
        for keyword in keywords:
            if keyword in text:
                body_hits += text.count(keyword)
                if keyword not in WEAK_KEYWORDS:
                    strong_hits += text.count(keyword)
            if title and keyword in title:
                title_hit = True
        # One or two passing body mentions are not enough: require a title hit
        # or three body mentions. This keeps single-word false positives out.
        if title_hit or (body_hits >= 3 and strong_hits >= 3):
            scored.append((1 if title_hit else 0, body_hits, label))
    scored.sort(reverse=True)
    found = [label for _, _, label in scored]
    return found[:limit] if limit else found


def tag_document(title: str, text: str, agency: str) -> dict[str, Any]:
    """Return industry_tags / topic_tags / tag_basis.

    Title hits weigh the same as body hits, but the basis string records
    whether the match came from the title or the body so a reviewer can audit
    false positives.
    """
    head = f"{title}\n{(text or '')[:3000]}"
    industries = _hits(head, INDUSTRY_RULES, title, limit=3)
    basis: list[str] = []
    if industries:
        basis.append("keyword")
    prior = AGENCY_PRIOR.get((agency or "").strip())
    if prior and prior not in industries:
        industries.append(prior)
        basis.append(f"agency:{prior}")
    if not industries:
        industries = ["unlabeled"]
        basis = ["none"]
    topics = _hits(head, TOPIC_RULES, title, limit=4)
    return {
        "industry_tags": industries,
        "topic_tags": topics,
        "tag_basis": basis,
    }
