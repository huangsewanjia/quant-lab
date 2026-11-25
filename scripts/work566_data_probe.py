# -*- coding: utf-8 -*-
"""WORK-566 · Stage 0 · 端点独立复测 → experiments/WORK-566/data_probe.json

任务书要求：把「端点名 / 参数原文 / 返回行数 / 覆盖起止日 / ok 或 error 原文」逐条落盘。
本脚本做两件事：
  1) 本地 quantdb（/srv/share/quantdb，parquet 原始层）逐表覆盖统计 —— 实测；
  2) tushare MCP 端点的实测记录 —— 由本 run 内实际调用（MCP tushare）得到，
     参数原文照抄调用参数、返回行数照抄返回条数、首行样例照抄返回字段值，
     以常量形式并入（不可由脚本复现的部分如实标注为「MCP 调用记录」）。

纪律：本地库只读（read-only 语义：直接读 parquet 原始层，不写库）；单次查询返回聚合行，
不整表进上下文；缺项写 ok:false + 原因，不删条目。

运行：
  python scripts/work566_data_probe.py
"""
import json
import os
import sys
import time

import duckdb

RAW = "/srv/share/quantdb/raw"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "experiments", "WORK-566")
os.makedirs(OUT, exist_ok=True)

WINDOWS = [
    ("prior_fit_2014_2015", 20140101, 20151231),
    ("train_2016_2021", 20160101, 20211231),
    ("valid_2022_2023", 20220101, 20231231),
    ("test_2024_2026H1", 20240102, 20260626),
]

# ---------------------------------------------------------------- tushare MCP 端点实测记录
# 来源：本 run 内通过 MCP tushare 逐个调用，参数原文照抄，rows 为实际返回条数。
TUSHARE_PROBES = [
    {
        "endpoint": "tushare.daily",
        "params": {"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        "rows": 22,
        "coverage": "2024-01-02 ~ 2024-01-31（该窗口内 22 个交易日全覆盖）",
        "ok": True,
        "first_row": {
            "ts_code": "600000.SH", "trade_date": "20240102", "open": 6.63, "high": 6.65,
            "low": 6.6, "close": 6.6, "pre_close": 6.62, "change": -0.02, "pct_chg": -0.3021,
            "vol": 220667.0, "amount": 146066.304,
        },
        "fields": "ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount",
        "unit": "vol=手(1手=100股)；amount=千元",
        "note": "不复权价格；复权由 adj_factor 端点单独取，库里不存复权价",
    },
    {
        "endpoint": "tushare.adj_factor",
        "params": {"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        "rows": 22,
        "coverage": "2024-01-02 ~ 2024-01-31",
        "ok": True,
        "first_row": {"ts_code": "600000.SH", "trade_date": "20240102", "adj_factor": 15.5347},
        "fields": "ts_code, trade_date, adj_factor",
        "unit": "无量纲乘性因子",
        "note": "窗口内因子恒为 15.5347（区间无除权事件），可作口径自洽检查",
    },
    {
        "endpoint": "tushare.daily_basic",
        "params": {"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        "rows": 22,
        "coverage": "2024-01-02 ~ 2024-01-31",
        "ok": True,
        "first_row": {
            "ts_code": "600000.SH", "trade_date": "20240102", "close": 6.6, "turnover_rate": 0.0752,
            "turnover_rate_f": 0.2315, "volume_ratio": 0.99, "pe": 3.7858, "pe_ttm": 5.0064,
            "pb": 0.3213, "ps": 1.0271, "ps_ttm": 1.0922, "dv_ratio": 4.8485,
            "total_share": 2935217.6396, "float_share": 2935217.6396, "free_share": 953172.2639,
            "total_mv": 19372436.4214, "circ_mv": 19372436.4214,
        },
        "fields": "close, turnover_rate(_f), volume_ratio, pe, pe_ttm, pb, ps(_ttm), dv_ratio(_ttm), total_share, float_share, free_share, total_mv, circ_mv",
        "unit": "total_share/float_share/free_share=万股；total_mv/circ_mv=万元；turnover_rate=%",
        "note": "口径自洽实测：total_share(万股) × close(元) = total_mv(万元)，"
                "2935217.6396 × 6.60 = 19372436.42 ✓（1 档舍入内）",
    },
    {
        "endpoint": "tushare.stk_limit",
        "params": {"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        "rows": 22,
        "coverage": "2024-01-02 ~ 2024-01-31",
        "ok": True,
        "first_row": {"trade_date": "20240102", "ts_code": "600000.SH", "up_limit": 7.28, "down_limit": 5.96},
        "fields": "trade_date, ts_code, up_limit, down_limit",
        "unit": "元/股（不复权口径，与 daily 的 close 同尺度）",
        "note": "涨跌停价按个股当期规则给出（ST/主板/创业板不同），不做任何硬编码",
    },
    {
        "endpoint": "tushare.suspend_d",
        "params": {"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240331"},
        "rows": 0,
        "coverage": "无停牌事件（该窗口浦发银行未停牌）",
        "ok": True,
        "first_row": None,
        "fields": "ts_code, trade_date, suspend_timing, suspend_type",
        "unit": "事件表",
        "note": "空返回是「该股无事件」，不是接口不可用；全库事件量见本地库统计",
    },
    {
        "endpoint": "tushare.stock_basic",
        "params": {"ts_code": "600000.SH"},
        "rows": 1,
        "coverage": "个股主档 1 条",
        "ok": True,
        "first_row": {
            "ts_code": "600000.SH", "symbol": "600000", "name": "浦发银行", "area": "上海",
            "industry": "银行", "cnspell": "PFYH", "market": "主板", "list_date": "19991110",
            "act_name": "无实际控制人", "act_ent_type": "无",
        },
        "fields": "ts_code, symbol, name, area, industry, market, list_date, act_name, act_ent_type",
        "unit": "—",
        "note": "返回字段中**无 list_status**（无在市/退市标记）；退市识别改用「stock_basic 有主档 + daily 无后续 bar」",
    },
]


def main():
    t_start = time.time()
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")

    def P(name):
        # union_by_name=true：早期空分区（如 stk_limit/trade_date=20070110）的列类型是 NULL，
        # 不加这个参数 DuckDB 会按首个文件的 schema 推断并报 VARCHAR -> NULL 转换错误。
        return (f"read_parquet('{RAW}/{name}/**/*.parquet', "
                f"hive_partitioning=true, union_by_name=true)")

    daily, adj = P("daily"), P("adj_factor")
    dbasic, slimit = P("daily_basic"), P("stk_limit")
    sbasic, susp, tcal = P("stock_basic"), P("suspend_d"), P("trade_cal")
    fina = P("fina_indicator")

    local = []

    def add(name, sql, unit="", note=""):
        t = time.time()
        rows = con.execute(sql).fetchall()
        rec = {"endpoint": f"quantdb.{name}", "sql": sql, "rows": [list(r) for r in rows],
               "unit": unit, "note": note, "elapsed_s": round(time.time() - t, 1)}
        local.append(rec)
        print(f"[{rec['elapsed_s']:6.1f}s] {name}: {rows}", flush=True)
        return rows

    print("== 表级覆盖 ==", flush=True)
    add("daily", f"select count(*), count(distinct ts_code), min(trade_date), max(trade_date) from {daily}",
        "vol=手, amount=千元", "全表")
    add("adj_factor", f"select count(*), count(distinct ts_code), min(trade_date), max(trade_date) from {adj}")
    add("daily_basic", f"select count(*), count(distinct ts_code), min(trade_date), max(trade_date) from {dbasic}",
        "total_mv=万元, total_share=万股")
    add("stk_limit", f"select count(*), count(distinct ts_code), min(trade_date), max(trade_date) from {slimit}",
        "元/股")
    add("stock_basic", f"select count(*), count(distinct ts_code), min(list_date), max(list_date) from {sbasic}",
        "list_date=YYYYMMDD", "无 list_status 字段")
    add("suspend_d", f"select count(*), count(distinct ts_code), min(trade_date), max(trade_date) from {susp}")
    add("trade_cal", f"select count(*), count(distinct exchange), min(cal_date), max(cal_date) from {tcal}")
    add("fina_indicator", f"select count(*), count(distinct ts_code), min(ann_date), max(ann_date), "
                           f"min(end_date), max(end_date) from {fina}",
        "ann_date=公告日(PIT 口径)", "end_date=报告期")

    def yr_glob(name, lo, hi):
        """按年份做分区裁剪：只读相关年份的 hive 分区，避免全量扫描。"""
        yrs = list(range(lo // 10000, hi // 10000 + 1))
        parts = ",".join(f"'{RAW}/{name}/trade_date={y}*/**/*.parquet'" for y in yrs)
        return f"read_parquet([{parts}], hive_partitioning=true, union_by_name=true)"

    print("\n== 分窗覆盖（daily）==", flush=True)
    win = {}
    for label, lo, hi in WINDOWS:
        rows = add(f"daily_{label}",
                   f"select count(*), count(distinct ts_code), count(distinct trade_date) "
                   f"from {yr_glob('daily', lo, hi)} where trade_date between {lo} and {hi}")
        win[label] = {"daily": list(rows[0])}
    print("\n== 分窗覆盖（adj_factor / daily_basic / stk_limit）==", flush=True)
    for label, lo, hi in WINDOWS:
        for nm in ("adj_factor", "daily_basic", "stk_limit"):
            rows = con.execute(
                f"select count(*), count(distinct ts_code), count(distinct trade_date) "
                f"from {yr_glob(nm, lo, hi)} where trade_date between {lo} and {hi}").fetchall()
            win.setdefault(label, {})[nm] = list(rows[0])
    print(json.dumps(win, ensure_ascii=False, indent=1), flush=True)

    print("\n== 退市覆盖 ==", flush=True)
    d_win = yr_glob("daily", 20120101, 20260626)
    rows = add("delisted_with_daily",
               f"select count(*) from (select distinct ts_code from {sbasic}) s "
               f"where s.ts_code in (select distinct ts_code from {d_win} where trade_date between 20120101 and 20260626)")
    rows2 = add("listed_without_daily",
                f"select count(*) from (select distinct ts_code from {sbasic}) s "
                f"where s.ts_code not in (select distinct ts_code from {d_win} where trade_date between 20120101 and 20260626)")
    n_stock_basic = con.execute(f"select count(distinct ts_code) from {sbasic}").fetchone()[0]
    n_daily_all = con.execute(f"select count(distinct ts_code) from {daily}").fetchone()[0]

    print("\n== 测试期有效 bar 覆盖率 ==", flush=True)
    d_test = yr_glob("daily", 20240102, 20260626)
    cov = add("test_window_coverage",
              f"with cal as (select cast(strftime(cal_date, '%Y%m%d') as int) as cd from {tcal} where exchange='SSE' and is_open=1 "
              f"and cal_date between date '2024-01-02' and date '2026-06-26'), "
              f"listed as (select distinct ts_code from {d_test} where trade_date between 20240102 and 20260626), "
              f"bars as (select ts_code, count(distinct trade_date) n from {d_test} "
              f"where trade_date between 20240102 and 20260626 group by 1) "
              f"select (select count(*) from cal), (select count(*) from listed), "
              f"(select sum(n) from bars), "
              f"round((select sum(n) from bars)::double / ((select count(*) from cal) * (select count(*) from listed)), 6)",
              "分母=测试期交易日数 × 测试期出现过的股票数；分子=实际 bar 数",
              "停牌格记为缺失，不补 0")

    print("\n== fina_indicator PIT as-of 可用性 ==", flush=True)
    add("fina_pit_check",
        f"select count(*) from (select ts_code, ann_date, end_date, roe from {fina} "
        f"where ann_date is not null and end_date is not null) x",
        "ann_date 不早于 end_date 的比例见下一行")
    add("fina_pit_ratio",
        f"select round(avg(case when cast(ann_date as int) >= cast(end_date as int) then 1.0 else 0.0 end), 6), "
        f"count(*) from {fina} where ann_date is not null and end_date is not null",
        "ann_date >= end_date 的比例（应为 1）",
        "as-of 对齐的硬条件：公告日必须不早于报告期；比例 <1 表示有反常记录")

    payload = {
        "experiment": "WORK-566",
        "stage": "0 · 端点独立复测",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data_cutoff": "2026-06-26",
        "local_db": {
            "path": "/srv/share/quantdb/db/quantdb.duckdb",
            "raw_layer": RAW,
            "access": "只读（本轮直接读 parquet 原始层，未打开 .duckdb 单文件，避免 5.9GB 文件在负载下的打开延迟）",
            "note": "本轮所有本地统计均走 parquet 原始层；库内容与 .duckdb 同源（ETL 落库）",
        },
        "tushare_endpoints": TUSHARE_PROBES,
        "local_endpoints": local,
        "window_coverage": win,
        "delisting": {
            "stock_basic_distinct_ts_code": n_stock_basic,
            "daily_distinct_ts_code_all_history": n_daily_all,
            "in_stock_basic_and_has_daily_2012plus": rows[0][0] if rows else None,
            "in_stock_basic_without_any_daily": rows2[0][0] if rows2 else None,
            "paper_cn_all_securities": 5669,
        },
        "test_window_bar_coverage": cov[0] if cov else None,
        "elapsed_s": round(time.time() - t_start, 1),
    }
    mpath = os.path.join(OUT, "data_probe.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nwritten: experiments/WORK-566/data_probe.json  ({time.time()-t_start:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
