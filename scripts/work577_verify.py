#!/usr/bin/env python3
"""WORK-577 核对：CNESA 提取覆盖（原文数字 -> 库内行）。

    QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
      /home/moyongkai/.venvs/quantdb/bin/python scripts/work577_verify.py

只读：抓一次公开页、跑一次提取规则、只读打开主库比对；不写 raw、不写库。
退出码 0 = 全部断言通过。

断言分三组：
  A 提取层  —— 行数、唯一键、原句可回指、关键数值与符号方向
  B 库内层  —— 行数一致、主键唯一、单位在 C4 词表内、每行 caliber 里的原句能在正文里找到
  C 覆盖面  —— 候选池里未提取的数字必须带原因，不留「待补充」
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
FETCHER = Path("/srv/share/quantdb/code/industry_fetch/fetch_cnesa_storage.py")
RAW = Path("/srv/share/quantdb/raw/industry/cnesa_storage")
DB = Path("/srv/share/quantdb/db/quantdb.duckdb")
TABLE = "industry_cnesa_storage"
UNITS = {"GW", "%", "GWh", "个", "项", "元/kWh", "小时"}

# 原文数字 -> 期望库内行（指标名, 值, 单位, 期）。用于防止规则漂移。
EXPECTED = [
    ("电力储能累计装机规模", 213.3, "GW", "2026-01"),
    ("电力储能累计装机规模同比", 54.0, "%", "2026-01"),
    ("抽水蓄能装机占比", 31.3, "%", "2026-01"),
    ("新型储能累计装机规模", 144.7, "GW", "2026-01"),
    ("新型储能累计装机规模同比", 85.0, "%", "2026-01"),
    ("新型储能新增投运功率规模", 66.43, "GW", "2026-01"),
    ("新型储能新增投运能量规模", 189.48, "GWh", "2026-01"),
    ("新型储能新增投运功率规模同比", 52.0, "%", "2026-01"),
    ("新型储能新增投运能量规模同比", 73.0, "%", "2026-01"),
    ("独立储能应用场景占比", 58.0, "%", "2026-01"),
    ("火储调频应用场景占比", 1.4, "%", "2026-01"),
    ("用户侧应用场景占比", 8.0, "%", "2026-01"),
    ("储能系统招标标段数量", 690.0, "个", "2026-01"),
    ("储能系统招标标段数量同比", -10.4, "%", "2026-01"),
    ("EPC招标标段数量", 1536.0, "个", "2026-01"),
    ("EPC招标标段数量同比", 4.5, "%", "2026-01"),
    ("储能系统中标规模", 121.5, "GWh", "2026-01"),
    ("储能系统中标规模同比", 140.1, "%", "2026-01"),
    ("EPC中标规模", 206.3, "GWh", "2026-01"),
    ("EPC中标规模同比", 125.5, "%", "2026-01"),
    ("储能系统采购中标价格下限", 391.14, "元/kWh", "2026-01"),
    ("储能系统采购中标价格上限", 913.0, "元/kWh", "2026-01"),
    ("2h EPC中标均价", 1043.82, "元/kWh", "2026-01"),
    ("4h EPC中标均价", 935.4, "元/kWh", "2026-01"),
    ("2h EPC中标均价同比", -13.04, "%", "2026-01"),
    ("4h EPC中标均价同比", -8.19, "%", "2026-01"),
    ("新增储能相关政策数量", 869.0, "项", "2026-01"),
    ("新增储能相关政策数量同比", 13.0, "%", "2026-01"),
    ("各省新型储能“十四五”规划目标总规模", 91.6, "GW", "2026-01"),
    ("电网代购电平均价差", 0.616, "元/kWh", "2026-01"),
    ("电网代购电平均价差同比", -9.4, "%", "2026-01"),
    ("新型储能累计装机平均时长", 2.58, "小时", "2026-01"),
    ("新型储能累计装机平均时长", 2.11, "小时", "2022-01"),
]


def load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_cnesa_storage", FETCHER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 解析注解时需要模块已注册
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="不抓网，只核对库与 raw")
    args = ap.parse_args()

    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    mod = load_fetcher()
    rows, text = [], None
    if not args.offline:
        import requests  # noqa: F401  (走 fetcher 自己的会话)
        r, err = mod.http(mod.ARTICLE, sess=mod.session())
        if err:
            print(f"文章取不到: {err}")
            return 1
        text = mod.to_text(mod.decode(r))
        pub = (re.search(r"在\s*(20\d{2}-\d{2}-\d{2})\s*发布", text) or [None, ""])[1] or ""
        tm = re.search(r"(20\d{2}年度[^\s，。]{0,30}储能数据[^\s，。]{0,20})", text)
        title = tm.group(1) if tm else ""
        rows, spans, failures = mod.extract(text, pub, title)
        # WORK-584：预测值已分流到 cnesa_forecast / industry_forecast。覆盖率必须把
        # 预测命中区间一起算进来，否则这 3 个数字会被误报成「未提取、需补规则」。
        _frows, fspans, _ffailures = mod.extract_forecasts(
            text, pub, mod.report_period(text))
        uncovered, stats = mod.uncovered_failures(text, spans + fspans)

        print("\n## A 提取层")
        check(len(rows) >= 10, f"提取行数 {len(rows)} >= 10")
        keys = [(r["period"], r["indicator"]) for r in rows]
        check(len(keys) == len(set(keys)), f"「指标+期」唯一（{len(keys)} 行）")
        check(all(r["context"] and r["context"] in text for r in rows),
              "每行 context 非空且能在正文原文里定位")
        check(all(r["context"].count("。") <= 1 for r in rows), "每行 context 是单句（不含多个句号）")
        got = {(r["indicator"], round(float(r["value"]), 4), r["unit"], r["period"]) for r in rows}
        missing = [e for e in EXPECTED if (e[0], round(e[1], 4), e[2], e[3]) not in got]
        check(not missing, f"关键数值/单位/期全部命中（期望 {len(EXPECTED)} 行，缺 {missing}）")
        neg = {r["indicator"]: r["value"] for r in rows if r["value"] < 0}
        check(set(neg) == {"储能系统招标标段数量同比", "2h EPC中标均价同比",
                           "4h EPC中标均价同比", "电网代购电平均价差同比"},
              f"「减少/下降」一律记负值（{len(neg)} 行）")
        print("\n## C 覆盖面")
        check(stats["coverage"] is not None, f"覆盖率 {stats}")
        bad = [u for u in uncovered if "需补规则" in u["error"]]
        check(not bad, f"未提取数字都有原因（{len(uncovered)} 条，待补 {len(bad)} 条）")
        check(all(u["context"] for u in uncovered), "未提取数字都带原句")
        print(f"\n  正文候选池 {stats['distinct_tokens']} 个不同数字，覆盖 "
              f"{stats['covered_tokens']}，未提取 {stats['uncovered_tokens']}")

    print("\n## B 库内层")
    raw = None
    pq = RAW / "part.parquet"
    if pq.exists():
        import pandas as pd
        raw = pd.read_parquet(pq)
        check(len(raw) >= 10, f"raw parquet {len(raw)} 行 >= 10")
    con = duckdb.connect(str(DB), read_only=True)
    try:
        n = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        check(n >= 10, f"库内 {TABLE} {n} 行 >= 10")
        if raw is not None:
            check(int(n) == len(raw), f"库内行数 {n} == raw parquet 行数 {len(raw)}")
        dup = con.execute(
            f"SELECT count(*) FROM (SELECT period, indicator FROM {TABLE} "
            "GROUP BY 1,2 HAVING count(*) > 1)").fetchone()[0]
        check(int(dup) == 0, f"主键 (period, indicator) 无重复（重复组 {dup}）")
        units = {r[0] for r in con.execute(f"SELECT DISTINCT unit FROM {TABLE}").fetchall()}
        check(units <= UNITS, f"单位都在 C4 词表内：{sorted(units)}")
        nulls = con.execute(
            f"SELECT count(*) FROM {TABLE} WHERE value IS NULL OR caliber IS NULL "
            "OR caliber NOT LIKE '%｜原句：%'").fetchone()[0]
        check(int(nulls) == 0, f"每行都有数值与「｜原句：」（缺 {nulls} 行）")
        table_rows = con.execute(
            f"SELECT period, indicator, value, unit, caliber, published_date, source_url "
            f"FROM {TABLE} ORDER BY period, indicator").fetchall()
        if text is not None:
            notfound = [f"{p}/{i}" for p, i, _v, _u, cal, _pd, _s in table_rows
                        if (cal.split("｜原句：", 1)[1] if "｜原句：" in cal else "") not in text]
            check(not notfound, f"库内每行原句都能在正文定位（找不到 {len(notfound)} 行）")
        periods = con.execute(
            f"SELECT period, count(*) FROM {TABLE} GROUP BY 1 ORDER BY 1").fetchall()
        print(f"  期分布: {[(str(p), int(c)) for p, c in periods]}")
        print(f"  统计时点: {con.execute('SELECT max(published_date) FROM ' + TABLE).fetchone()[0]}")
    finally:
        con.close()

    failed = json.loads((RAW / "failed.json").read_text(encoding="utf-8"))
    print(f"\n  failed.json: {failed['count']} 条（含白皮书「未取到」1 条）")

    print("\n## 对照表（原文数字 -> 库内行）")
    print("| 指标 | 数值 | 单位 | 期 | 原文片段 |")
    print("|---|---|---|---|---|")
    for period, indicator, value, unit, caliber, _pd, _s in table_rows:
        ctx = caliber.split("｜原句：", 1)[1] if "｜原句：" in caliber else ""
        print(f"| {indicator} | {value:g} | {unit} | {period} | {ctx[:70]}… |")

    print(f"\n结论：{'PASS' if not fails else 'FAIL'}（{len(fails)} 项失败）")
    for f in fails:
        print(f"  - {f}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
