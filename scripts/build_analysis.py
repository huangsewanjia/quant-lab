# -*- coding: utf-8 -*-
"""
分析与最小复现实验：行情快照 → 指标 → experiments/<EXP>/metrics.json + metrics_table.csv

输入：data/<EXP>/A_tencent_*.csv（由 scripts/fetch_market_data.py 生成）
口径：全部指标定义见 data_contracts/metric_definitions.md
     字段含义见 data_contracts/field_dictionary.md
     源可用性见 data_contracts/data_source_registry.md

纪律：
- 只用真实行情快照，不生成任何合成/随机数据。
- 不调参、不挑最优：口径敏感性按预先写死的多套口径全量报出。
- 成交额：东财源对本机 IP 限流未取到，改用 volume×close 代理，并用 Tushare 的真实
  amount 做独立校验；代理绝对水平高 3.3–3.6 倍，只用于趋势比较。

运行：
  python scripts/build_analysis.py --exp WORK-404
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- 路径（仓库相对，无写死的任务目录）
EXP = os.environ.get("QUANTLAB_EXP", "WORK-404")
DATA = os.path.join(REPO, "data", EXP)
OUT = os.path.join(REPO, "experiments", EXP)
FIG = os.path.join(REPO, "figures")

# 数据截止点：原文发布日 2026-11-15 前最后一个完整交易日
DATA_CUTOFF = "2026-11-13"

# ---------------------------------------------------------------- 源C：Tushare 独立交叉校验
# 取自 MCP tushare index_daily（服务端拉取，与本机腾讯源出口 IP 不同），字段 ts_code/trade_date/close/amount
# amount 单位 = 千元
TUSHARE = {
    "000852.SH": {"20260930": {"close": 7298.8208}, "20261030": {"close": 6161.2275}, "20261113": {"close": 6357.2582}},
    "000300.SH": {"20260930": {"close": 4357.6155, "amount": 356491548.6214},
                  "20261030": {"close": 3985.501, "amount": 456855790.6144}},
    "000985.CSI": {"20260930": {"close": 5648.2517, "amount": 1381583055.927},
                   "20261030": {"close": 4952.4953, "amount": 1618310771.754}},
    "932000.CSI": {"20260930": {"close": 3107.2648, "amount": 337238308.581},
                   "20261030": {"close": 2616.7385, "amount": 362050766.605}},
    "399303.SZ": {"20260930": {"close": 9648.5035, "amount": 434874636.96968},
                  "20261030": {"close": 8171.9678, "amount": 511603429.99668}},
}
TS_MAP = {"sh000852": "000852.SH", "sh000300": "000300.SH", "sh000985": "000985.CSI",
          "sh932000": "932000.CSI", "sz399303": "399303.SZ"}

STYLE = {
    "sh000001": "全市场", "sz399001": "全市场", "sh000985": "全市场",
    "sh000016": "大盘价值", "sh000300": "大盘", "sh000905": "中盘",
    "sh000852": "小盘", "sz399303": "超小盘", "sz399006": "科技成长",
    "sh000688": "科技成长", "sh000922": "防御",
}
NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sh000016": "上证50", "sh000300": "沪深300",
         "sh000905": "中证500", "sh000852": "中证1000", "sz399303": "国证2000", "sz399006": "创业板指",
         "sh000688": "科创50", "sh000985": "中证全指", "sh000922": "中证红利"}


def load_all(data_dir):
    frames = {}
    for code in STYLE:
        p = os.path.join(data_dir, f"A_tencent_{code}.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df["turnover_proxy"] = df["volume"] * df["close"]  # 成交额代理（元）：成交量(股)×收盘价(元)
        frames[code] = df
    return frames


def close_on(df, ymd):
    """返回该日期收盘价；若非交易日取之前最近一个交易日。"""
    ts = pd.Timestamp(ymd)
    s = df["close"][df.index <= ts]
    if s.empty:
        return None, None
    return float(s.iloc[-1]), s.index[-1].strftime("%Y-%m-%d")


def period_return(df, d0, d1):
    a, da = close_on(df, d0)
    b, db = close_on(df, d1)
    if a is None or b is None:
        return None
    return b / a - 1, da, db, a, b


def maxdd(df, start="2025-12-31"):
    s = df.loc[df.index >= pd.Timestamp(start), "close"]
    return float((s / s.cummax() - 1).min())


def ann_stats(df, start="2026-01-01", rf=0.015):
    """区间统计：基准取 start 之前最后一个交易日收盘（start 当日若为交易日则含入）。"""
    base_date = pd.Timestamp(start) - pd.Timedelta(days=1)
    s = df.loc[df.index >= pd.Timestamp(start), "close"]
    base_series = df.loc[df.index <= base_date, "close"]
    if base_series.empty or s.empty:
        return {}
    base = float(base_series.iloc[-1])
    r = s.pct_change().dropna()
    total = float(s.iloc[-1] / base - 1)
    yrs = (s.index[-1] - df.loc[df.index <= base_date].index[-1]).days / 365.25
    ann = (1 + total) ** (1 / yrs) - 1
    vol = float(r.std() * np.sqrt(252))
    sharpe = (ann - rf) / vol if vol else None
    return {"total_return": total, "ann_return": float(ann), "ann_vol": vol,
            "sharpe_rf1p5": float(sharpe) if sharpe else None,
            "maxdd": maxdd(df, base_date.strftime("%Y-%m-%d")), "n_days": int(len(r)),
            "base_date": df.loc[df.index <= base_date].index[-1].strftime("%Y-%m-%d"),
            "base_close": base, "end_close": float(s.iloc[-1])}


def _force_utf8_stdio():
    """把 stdout/stderr 显式切到 UTF-8（Windows 默认控制台编码是 GBK/cp936）。

    脚本输出里只要出现非 GBK 字符（如 U+2212 数学减号），在「输出被重定向到文件
    或管道」时就会抛 UnicodeEncodeError、进程 exit 1——结果文件根本产不出来。
    强制重设编码后，README 里的命令不需要任何环境变量即可跑完并返回真实退出码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="快照 → 指标")
    ap.add_argument("--exp", default=EXP, help="实验 ID")
    args = ap.parse_args()
    exp = args.exp
    data_dir = os.path.join(REPO, "data", exp)
    out_dir = os.path.join(REPO, "experiments", exp)
    os.makedirs(out_dir, exist_ok=True)

    frames = load_all(data_dir)
    if not frames:
        raise SystemExit(f"没有找到快照：{data_dir}；先跑 scripts/fetch_market_data.py --exp {exp}")
    out = {"experiment": exp,
           "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
           "data_source": "腾讯行情源A（IP 与 Tushare 服务端不同，构成独立交叉校验）",
           "data_cutoff": DATA_CUTOFF, "cross_check": {}, "windows": {}, "indices": {}, "notes": []}

    # ---- 1. Tushare 交叉校验（腾讯 vs Tushare，同日同口径）
    cc = []
    for code, ts_code in TS_MAP.items():
        if code not in frames:
            continue
        df = frames[code]
        for ymd, rec in TUSHARE.get(ts_code, {}).items():
            got, used = close_on(df, ymd)
            if got is None:
                continue
            diff = got - rec["close"]
            rel = abs(diff) / rec["close"]
            # 源A 快照点位按 2 位小数落盘，源C 为原始精度；判据用「四舍五入半格」：
            # 绝对差 ≤ 0.005 即为同一数值的舍入差异（相对差自然 ≤ 0.005/点位 ≈ 1.2e-6）
            verdict = "一致（2位小数舍入内）" if abs(diff) <= 0.005 + 1e-9 else "不一致"
            cc.append({"tencent_code": code, "tushare_code": ts_code, "date": ymd,
                       "tencent_close": got, "tushare_close": rec["close"],
                       "abs_diff": diff, "rel_diff": rel, "verdict": verdict})
    out["cross_check"] = cc
    bad = [c for c in cc if c["verdict"].startswith("不一致")]
    # 可比口径 = 两侧都有值的观测（仅单侧有值的另计，不计入「一致」分母）
    comparable = [c for c in cc if c["tencent_close"] is not None and c["tushare_close"] is not None]
    out["cross_check_summary"] = {
        "总观测数": len(cc),
        "可比观测数": len(comparable),
        "可比一致数": sum(1 for c in comparable if c["verdict"].startswith("一致")),
        "仅单侧有值": len(cc) - len(comparable),
        "不一致数": len(bad),
        "判据": "绝对差 ≤ 0.005（源A 落盘 2 位小数 vs 源C 原始精度，四舍五入半格）",
    }
    out["notes"].append(f"跨源交叉校验 {len(cc)} 个观测（其中可比 {len(comparable)}），不一致 {len(bad)} 个")

    # ---- 2. 窗口收益
    WINDOWS = {
        "2026H1累计(01-01~06-30)": ("2025-12-31", "2026-06-30"),
        "2026年7月": ("2026-06-30", "2026-07-31"),
        "2026年8月": ("2026-07-31", "2026-08-31"),
        "2026年9月": ("2026-08-31", "2026-09-30"),
        "2026年10月": ("2026-09-30", "2026-10-30"),
        "2026年11月1-13日": ("2026-10-30", "2026-11-13"),
        "10月以来(09-30~11-13)": ("2026-09-30", "2026-11-13"),
        "2026年初至今": ("2025-12-31", "2026-11-13"),
    }
    for code, df in frames.items():
        out["indices"].setdefault(code, {"name": NAMES.get(code, code), "style": STYLE.get(code, "")})
        for wname, (d0, d1) in WINDOWS.items():
            res = period_return(df, d0, d1)
            if res is None:
                continue
            r, da, db, a, b = res
            out["windows"].setdefault(wname, {})[code] = {"ret": r, "start_date": da, "end_date": db,
                                                          "start_close": a, "end_close": b}
        st = ann_stats(df)
        out["indices"][code].update(st)
        # 成交额代理：月均值
        tp = df["turnover_proxy"]
        monthly = tp.resample("ME").mean()
        out["indices"][code]["turnover_proxy_month_mean_2026"] = {k.strftime("%Y-%m"): float(v)
                                                                   for k, v in monthly.items() if k.year == 2026}
        out["indices"][code]["turnover_proxy_peak_2026"] = float(monthly[monthly.index.year == 2026].max())

    # ---- 3. 成交额代理 vs Tushare 真实 amount 校验
    allidx = frames["sh000985"]
    proxy_checks = []
    for ymd, rec in TUSHARE["000985.CSI"].items():
        if "amount" not in rec:
            continue
        ts = pd.Timestamp(ymd)
        row = allidx.loc[allidx.index <= ts].iloc[-1]
        proxy = float(row["turnover_proxy"])
        real = rec["amount"] * 1000.0  # 千元 → 元
        proxy_checks.append({"date": row.name.strftime("%Y-%m-%d"), "proxy_yuan": proxy,
                             "tushare_amount_yuan": real, "ratio": proxy / real})
    out["turnover_proxy_validation"] = proxy_checks

    # ---- 4. 风格相对收益（原文「风格极致反转」的检验）
    spreads = {}
    pairs = [("sh000852", "sh000300", "中证1000 − 沪深300"),
             ("sz399303", "sh000300", "国证2000 − 沪深300"),
             ("sz399006", "sh000016", "创业板指 − 上证50"),
             ("sh000688", "sh000300", "科创50 − 沪深300"),
             ("sh000852", "sh000922", "中证1000 − 中证红利"),
             ("sh000905", "sh000300", "中证500 − 沪深300")]
    for wname in ("2026年7月", "2026年10月", "2026年初至今"):
        spreads[wname] = {}
        for a, b, label in pairs:
            if a in frames and b in frames:
                ra = out["windows"][wname][a]["ret"]
                rb = out["windows"][wname][b]["ret"]
                spreads[wname][label] = ra - rb
    out["style_spreads"] = spreads

    # ---- 5. 原文量化主张 → 实测对照（每条都带出处与判定）
    c1000_oct = out["windows"]["2026年10月"]["sh000852"]["ret"]
    c1000_since = out["windows"]["10月以来(09-30~11-13)"]["sh000852"]["ret"]
    ts_oct_amount_ratio = [c["ratio"] for c in proxy_checks]
    monthly_all = out["indices"]["sh000985"]["turnover_proxy_month_mean_2026"]
    h1 = [v for k, v in monthly_all.items() if k < "2026-07"]
    jul = monthly_all.get("2026-07")
    oct_ = monthly_all.get("2026-10")
    claims = [
        {"id": "C1", "claim": "新财富1000指增私募指数 2026-10 跌幅超 17%，跑输中证1000指数",
         "原文出处": "正文第4段（大岩资本/中国证券报）",
         "实测": f"中证1000 2026-10 实测 {c1000_oct*100:.2f}%；若原文成立，10 月超额亏损 ≥ {(0.17 + c1000_oct)*100:.2f} 个百分点",
         "判定": "指数侧可复现（指数跌幅可核）；私募指数侧不可复现（该指数无公开行情）"},
        {"id": "C2", "claim": "10 月以来量化反而跑不赢指数，多家头部产品超额连续多周告负",
         "原文出处": "正文第3-4段",
         "实测": f"中证1000 2026-10-30→2026-11-13 {(out['windows']['2026年11月1-13日']['sh000852']['ret'])*100:+.2f}%；"
                  f"指数自身 9-30→11-13 {c1000_since*100:.2f}%",
         "判定": "方向上『跑输指数』无法用公开数据确认；需产品净值，超额序列不可复现"},
        {"id": "C3", "claim": "今年新发量化多头基金中逾七成收益为负",
         "原文出处": "正文第4段",
         "实测": "无公开样本（未取到产品级净值与新发清单）",
         "判定": "不可复现：样本定义（'新发'起算日、'量化多头'界定、净值口径、样本量）均未给出"},
        {"id": "C4", "claim": "部分产品距年内高点回撤超 30%",
         "原文出处": "正文第4段",
         "实测": f"公开可测口径下，2026 年内最大回撤（指数）：" +
                  "；".join(f"{out['indices'][c]['name']} {out['indices'][c]['maxdd']*100:.1f}%"
                            for c in ("sh000852", "sz399303", "sh000300") if c in out["indices"]),
         "判定": "不可复现（产品级）；指数级可测但不能替代产品级回撤"},
        {"id": "C5", "claim": "量化多头规模约 1.83 万亿元；私募量化全行业规模突破 3 万亿元；过去一年半超万亿元资金涌入股票量化",
         "原文出处": "正文第9段（格上基金研究员焦冰）",
         "实测": "未取到公开可核口径（AMAC 私募基金登记备案月报只到私募全行业，不拆『量化』；私募产品数量口径 2026 年起不披露）",
         "判定": "口径含糊 + 不可独立复现；『量化』细分无公开权威口径"},
        {"id": "C6", "claim": "下半年超额回撤由 7 月与 10 月两轮调整行情冲击；7 月拥挤方向抛售并向防御/微盘切换；10 月『指数涨、个股跌』；成交缩量抬高冲击成本",
         "原文出处": "正文第7-8段（大岩资本、正仁量化、焦冰）",
         "实测": "价格与成交层面可核（见 metrics）；因果归因（拥挤→抛售）无识别策略",
         "判定": "价格/成交层：可复现；拥挤归因：不可复现（无外生冲击、无反事实）"},
    ]
    out["claims"] = claims
    out["turnover_summary"] = {
        "2026H1月均代理": float(np.mean(h1)) if h1 else None,
        "2026-07月均代理": jul, "2026-10月均代理": oct_,
        "10月相对H1变化": (oct_ / np.mean(h1) - 1) if (h1 and oct_) else None,
        "代理与Tushare真实成交额比值区间": [min(ts_oct_amount_ratio), max(ts_oct_amount_ratio)] if ts_oct_amount_ratio else None,
        "口径限制": "代理绝对水平系统性高于真实成交额 3.3–3.6 倍，只用于趋势比较，不用于绝对成交额结论",
    }

    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    rows = []
    for code, rec in out["indices"].items():
        row = {"code": code, "名称": rec["name"], "风格": rec["style"],
               "2026年初至今": out["windows"]["2026年初至今"].get(code, {}).get("ret"),
               "2026年7月": out["windows"]["2026年7月"].get(code, {}).get("ret"),
               "2026年10月": out["windows"]["2026年10月"].get(code, {}).get("ret"),
               "11月1-13日": out["windows"]["2026年11月1-13日"].get(code, {}).get("ret"),
               "年化收益": rec["ann_return"], "年化波动": rec["ann_vol"],
               "夏普(rf=1.5%)": rec["sharpe_rf1p5"], "年内最大回撤": rec["maxdd"]}
        rows.append(row)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "metrics_table.csv"), index=False, encoding="utf-8")
    print(f"metrics.json / metrics_table.csv -> experiments/{exp}/")
    print(f"交叉校验：{len(cc)} 观测（可比 {len(comparable)}，一致 {out['cross_check_summary']['可比一致数']}），不一致 {len(bad)}")
    for r in rows:
        print(f"  {r['名称']:<8} YTD={r['2026年初至今']*100:>7.2f}%  7月={r['2026年7月']*100:>7.2f}%  "
              f"10月={r['2026年10月']*100:>7.2f}%  MDD={r['年内最大回撤']*100:>7.2f}%  Sharpe={r['夏普(rf=1.5%)']}")
    print("风格差：", json.dumps(out["style_spreads"], ensure_ascii=False, indent=1))
    print("成交额代理校验：", json.dumps(proxy_checks, ensure_ascii=False))
    print("成交额摘要：", json.dumps(out["turnover_summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
