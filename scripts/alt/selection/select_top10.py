#!/usr/bin/env python3
"""按 issue WORK-619 的 5 条判据（按序）给 20 条候选排序，产出 Top-10 与排除理由。

判据（写死在脚本里，改判据必须改这里并重跑）：
  1 影响判断：data_catalog.谁在用 的独立使用者 >=2，或属 16 条「需采购」硬缺口
  2 代理质量：质量分 = (频率差<=1 档) + (滞后<=1 个发布周期) + (覆盖>=70%)，需 >=2 分
  3 成本：单条自建人日 <=8
  4 可维护：2026-11-27 实测 HTTP 200、无 JS/验证码/WAF、且为机器可解析形态
  5 硬缺口倾斜：通过 1–4 的候选，按「需采购 > 非采购」排在人日前
末席规则：若 10 条里某条研究线 0 条，用该线排序最高者替换末席（保线均衡）。

输入：candidates.csv（每条字段的实测/引用来源见 note 列与 docs/selfbuild_report.html §2）
输出：docs/selfbuild_top10_candidates.csv（20 行带 pass/rank/理由）
      docs/selfbuild_top10.csv（10 行，列与 issue 要求一致）
"""
from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DOCS = os.path.join(REPO, "docs")
MACHINE = {"api-json", "csv", "json", "html-table", "html-scrape", "html-prose"}

TOP10_FIELDS = ["指标名", "行业", "区域", "代理源", "入口URL", "频率", "滞后", "覆盖度",
                "差距一句话", "自建人日", "入选理由", "对应P0条数"]

# 每条的对外表述（人工核对过的口径说明，不放宽、不美化）
META = {
    "trendforce_spot_price": dict(
        name="存储芯片现货价（DRAM / NAND Flash 现货）", ind="半导体", reg="全球",
        src="TrendForce 公开现货价表（付费商自有公开页）",
        url="https://www.trendforce.cn/price/dram/dram_spot",
        freq="日", lag="0（当日 18:10 GMT+8 更新）", cov="100%（现货价全部规格）",
        gap="合约价在 Gold+ 会员文件里，公开面只有现货价", days=4,
        why="16 条需采购硬缺口的半导体唯一公开日频价；判据 2 满分、成本最低档"),
    "benchmark_public_index": dict(
        name="全球锂盐/电芯/石墨/正负极 价格评估指数", ind="锂与有色；储能", reg="全球",
        src="Benchmark Mineral Intelligence 公开价格评估页", url="https://www.benchmarkminerals.com/price-assessments",
        freq="日（跟随页面更新）", lag="0（快照日=抓取日，源站未标评估日）", cov="100%（16 个公开指数）",
        gap="只有相对指数点，无法反推 USD/吨绝对价", days=6,
        why="一条脚本服务 5 个 P0（3/4/11/14/65），含 4 条需采购硬缺口"),
    "sne_research_releases": dict(
        name="全球动力电池装机 / ESS 出货 / 材料消耗（新闻稿绝对值）", ind="储能；新能源车；半导体", reg="全球",
        src="SNE Research 公开新闻稿列表", url="https://www.sneresearch.com/en/insight/release/",
        freq="月/季（按稿件发布）", lag="0–1 个发布周期", cov="90%（全球口径，缺厂商份额明细）",
        gap="新闻稿不是可查询数据库；列表只回最近 12 条", days=5,
        why="同时服务储能出货与电池装机两条线；含 2 条需采购硬缺口"),
    "shfe_gfex_quotes": dict(
        name="交易所日行情（SHFE 铜铝铅锌镍锡 + GFEX 碳酸锂/多晶硅/工业硅）", ind="锂与有色", reg="中国",
        src="上期所日行情 .dat + 广期所日行情接口", url="https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{YYYYMMDD}.dat",
        freq="日", lag="0", cov="50%（有持仓/成交，无注册仓单）",
        gap="注册仓单需会员后台；现货腿另在 100ppi（未入选）", days=6,
        why="服务两个 P0（67 交易所库存、76 现货升贴水）的期货腿，覆盖铜铝铅锌镍锡 + 碳酸锂等四个价格品种；频率/滞后满分，接口稳定"),
    "eurostat_trade": dict(
        name="欧洲对外贸易依赖度（SITC 大类）+ C24 有色金属增加值", ind="半导体；锂与有色", reg="欧洲",
        src="Eurostat 公开 API（ext_lt_intratrd / nama_10_a64）", url="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ext_lt_intratrd",
        freq="年", lag="1 个发布周期", cov="60%（SITC 大类，非 HS 单品类）",
        gap="拿不到 HS 8486/253090 单品类，只能给大类依赖度", days=8,
        why="一条脚本服务欧洲半导体与欧洲锂矿两个 P0；API 稳定、无鉴权"),
    "emmes_storage": dict(
        name="欧洲储能新增装机与累计装机（EMMES 公开版）", ind="储能", reg="欧洲",
        src="Energy Storage Europe / LCP Delta EMMES 10.0 公开页", url="https://energystorageeurope.eu/publication/emmes-10-june-2026/",
        freq="半年（版本）", lag="1 个发布周期", cov="50%（新增 GWh + 累计 GW，仅欧洲）",
        gap="数值写在新闻稿正文、无表结构；只有欧洲，无系统成本", days=8,
        why="储能线唯一给出欧洲装机绝对值的公开页；与 SNE 全球出货互为校验"),
    "owid_ev_sales": dict(
        name="全球/分国家 EV 销量与新车渗透率", ind="新能源车；宏观", reg="全球",
        src="OWID（IEA Global EV Outlook 派生）", url="https://ourworldindata.org/grapher/electric-car-sales.csv",
        freq="年", lag="1 个发布周期（GEO 版本差）", cov="100%（含 190+ 国家/地区）",
        gap="派生序列，IEA 一手版本更新后需重抓", days=3,
        why="一条脚本覆盖 5 个 P0（56/57/58/62/66），成本 3 人日"),
    "owid_energy_co2": dict(
        name="全球一次能源消费（分品种）与 CO2 排放", ind="宏观", reg="全球",
        src="OWID（IEA / Energy Institute 派生）", url="https://ourworldindata.org/grapher/annual-co2-emissions-per-country.csv",
        freq="年", lag="1 个发布周期", cov="80%（总量 CO2 含工业过程，无能源部门拆分）",
        gap="口径与 IEA World Energy Balances 不完全一致", days=4,
        why="宏观两条线口径基座，成本 4 人日、分发零鉴权"),
    "worldbank_wdi": dict(
        name="全球实际 GDP（2015 美元）与贸易占 GDP 比重", ind="宏观", reg="全球",
        src="World Bank WDI 公开 API v2", url="https://api.worldbank.org/v2/country/WLD/indicator/NY.GDP.MKTP.KD",
        freq="年", lag="1 个发布周期", cov="80%（水平值，无贸易量指数）",
        gap="WDI 与 IMF WEO 口径不同，增速需自算", days=6,
        why="P0 宏观核心序列，API 稳定、可复算增速"),
    "irena_re_capacity": dict(
        name="可再生能源与抽水蓄能装机容量（MW）/发电量（GWh）", ind="宏观；储能", reg="全球+区域",
        src="IRENA PxWeb 公开 API（Region_ELECSTAT）", url="https://pxweb.irena.org/api/v1/en/IRENASTAT/Power%20Capacity%20and%20Generation/Region_ELECSTAT_2026_H2_PX.px",
        freq="年", lag="0–1 个发布周期", cov="80%（按技术分列，储能仅抽水蓄能）",
        gap="电化学储能不在 IRENA 分类内；无在建/规划状态", days=8,
        why="服务全球可再生装机与中东/东南亚规划两个 P0；API 支持任意区域×技术"),
}

EXCLUDE_REASON = {
    "comtrade_trade": "判据 2 不达标：年度值、滞后超过 1 个发布周期（实测 2024 年数据 2026 年才稳定）；贸易额对出货量/GW 是弱代理",
    "internal_db_segment_profit": "判据 3 不达标：5 条合计 40 人日、单条 12 人日；且脚本需 duckdb 读内部主库，超出「标准库 + requests/pandas」依赖白名单",
    "esresearch_tender": "判据 2 不达标：公开样本止于 2021-12，滞后约 58 个月（实测页面最新项目即 2021 年）",
    "ppi_100ppi": "判据 4 不达标：返回 660 字节 JS cookie 挑战页（HW_CHECK），需模拟挑战才能取数，属绕过访问控制灰区；且单一供应商挂牌价同日离散 58.7%",
    "emmes_storage": "判据 2 不达标：单篇新闻稿（13.5 GW / 26.4 GWh 写在正文里），无时间序列；且仅覆盖欧洲，与 SNE 全球出货重叠",
    "usgs_mcs": "判据 4 不达标：数据只在 PDF 表格里，本单边界明令不抓 PDF 全文；结构化需另建解析器",
    "infolink_solar": "判据 2/4 不达标：USD 报价标注 🔒 会员锁定，公开面只有人民币；口径要求 EUR/W",
    "jrc_battery_projects": "判据 3 不达标：项目级目录需逐条解析，12 人日；判据 2 覆盖度仅 60%",
    "cninfo_announcements": "判据 1/3 不达标：仅 1 个使用者；公告需人工解析 100+ 份，12 人日",
    "menet_retail": "判据 2 不达标：米内网公开页只有摘要，无终端销售额明细；连锁率需另找商务部口径",
    "openfda_drugs": "判据 4 不达标：单页实测 298 s（limit=1000、skip 分页），一年需 5 页 → 25 分钟/年，不可维护；且代理是供给侧件数（覆盖 0.4）",
}


def quality(r: dict) -> int:
    """判据 2 的三项（频率差<=1、滞后<=1、覆盖>=70%）各 1 分。"""
    return (int(r["freq_gap"]) <= 1) + (int(r["lag_cycles"]) <= 1) + (float(r["coverage"]) >= 0.7)


def tier(r: dict) -> int:
    """A 档 = 三项全中；B 档 = 中两项。低于 2 分不入选。"""
    return 2 if quality(r) == 3 else (1 if quality(r) == 2 else 0)


def checks(r: dict) -> list[tuple[int, bool, str]]:
    """判据 1→4 逐条判定，顺序即门禁顺序；报告漏斗的台阶只认这里。"""
    return [
        (1, int(r["users_max"]) >= 2 or r["purchase"] == "Y",
         "判据 1 未过：使用者仅 1 个且不属 16 条需采购缺口"),
        (2, quality(r) >= 2,
         f"判据 2 未过：三项只中 {quality(r)} 项（频率差 {r['freq_gap']} 档、滞后 {r['lag_cycles']} 周期、覆盖 {r['coverage']}）"),
        (3, int(r["cost_person_days"]) <= 8,
         f"判据 3 未过：{r['cost_person_days']} 人日 > 8"),
        (4, bool(str(r["probe"]).startswith("200") and r["antibot"] == "no"
                 and r["format"] in MACHINE and "298s" not in str(r["probe"])),
         f"判据 4 未过：probe={r['probe']} format={r['format']} antibot={r['antibot']}"),
    ]


def first_fail(r: dict) -> str:
    """首个不通过的判据（1/2/3/4），四条全过给「通过」。"""
    for k, ok, _ in checks(r):
        if not ok:
            return f"判据{k}"
    return "通过"


def gate(r: dict) -> tuple[bool, str]:
    for _, ok, msg in checks(r):
        if not ok:
            return False, msg
    return True, ""


def main() -> int:
    src = os.path.join(HERE, "candidates.csv")
    rows = []
    with open(src, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["probe"] = r.pop("probe_2026_11_27")
            ok, why = gate(r)
            if not ok and r["dataset"] in EXCLUDE_REASON:
                why = EXCLUDE_REASON[r["dataset"]]
            r["pass"] = "Y" if ok else "N"
            r["fail_reason"] = "" if ok else (why or EXCLUDE_REASON.get(r["dataset"], ""))
            rows.append(r)

    passed = [r for r in rows if r["pass"] == "Y"]
    # 判据顺序：1 影响(已过门) > 2 代理质量 > 5 硬缺口倾斜 > 覆盖/成本兜底
    passed.sort(key=lambda r: (-tier(r), -quality(r), -float(r["coverage"]),
                               r["purchase"] != "Y", int(r["cost_person_days"]), int(r["id"])))
    top = passed[:10]

    rank = {r["id"]: i + 1 for i, r in enumerate(top)}
    out_cand = os.path.join(DOCS, "selfbuild_top10_candidates.csv")
    with open(out_cand, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["候选id", "数据集", "主指标(P0行)", "研究线", "覆盖P0行", "使用者数", "需采购",
                    "频率差(档)", "滞后(周期)", "覆盖度", "自建人日", "实测(2026-11-27)",
                    "形态", "反爬", "质量分", "通过判据1-4", "首个不通过判据", "入选Top10", "排名", "理由/排除原因"])
        for r in sorted(rows, key=lambda x: (x["pass"] != "Y", rank.get(x["id"], 99), int(x["id"]))):
            w.writerow([r["id"], r["dataset"], r["primary_row"], r["industry"], r["p0_rows"],
                        r["users_max"], r["purchase"], r["freq_gap"], r["lag_cycles"],
                        r["coverage"], r["cost_person_days"], r["probe"], r["format"],
                        r["antibot"], quality(r), r["pass"], first_fail(r),
                        "Y" if r["id"] in rank else "N", rank.get(r["id"], ""),
                        r["fail_reason"] or r["note"]])

    out_top = os.path.join(DOCS, "selfbuild_top10.csv")
    with open(out_top, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TOP10_FIELDS)
        w.writeheader()
        for r in top:
            m = META[r["dataset"]]
            w.writerow({"指标名": m["name"], "行业": m["ind"], "区域": m["reg"],
                        "代理源": m["src"], "入口URL": m["url"], "频率": m["freq"],
                        "滞后": m["lag"], "覆盖度": m["cov"], "差距一句话": m["gap"],
                        "自建人日": m["days"], "入选理由": m["why"],
                        "对应P0条数": len(r["p0_rows"].split(";"))})

    print(f"候选 {len(rows)}，通过 1–4 判据 {len(passed)}，入选 {len(top)}")
    for r in top:
        print(f"  {rank[r['id']]:2d}. {r['dataset']:28s} 需采购={r['purchase']} 质量={quality(r)} "
              f"覆盖P0={len(r['p0_rows'].split(';'))} 人日={r['cost_person_days']}")
    print(f"覆盖 P0 条数（去重）= {len({x for r in top for x in r['p0_rows'].split(';')})}")
    print(f"人日合计 = {sum(int(r['cost_person_days']) for r in top)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
