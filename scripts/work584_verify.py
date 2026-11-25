#!/usr/bin/env python3
"""WORK-584 核对：CNESA 预测值 -> cnesa_forecast raw -> industry_forecast 表。

    QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
      /home/moyongkai/.venvs/quantdb/bin/python scripts/work584_verify.py
    ... --offline    # 不抓网，只核对已有 raw/快照与库

只读：抓一次公开页（可 --offline 跳过）、跑一次预测提取规则、只读打开主库比对；
不写 raw、不写库。退出码 0 = 全部断言通过。

断言分三组：
  A 提取层 —— 3 条预测、数值/单位/目标期/提出方逐条命中，语气词保留在原句里
  B 库内层 —— 行数、主键唯一、必填非空、目标期在未来、原句能在正文快照定位
  C 隔离性 —— 观测表 industry_cnesa_storage 33 行不受影响；C8_forecast 闸 PASS
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FETCHER = Path("/srv/share/quantdb/code/industry_fetch/fetch_cnesa_storage.py")
RAW = Path("/srv/share/quantdb/raw/industry/cnesa_forecast")
DB = Path("/srv/share/quantdb/db/quantdb.duckdb")
TABLE = "industry_forecast"
OBS_TABLE = "industry_cnesa_storage"
SNAPSHOT = RAW / "article.txt"

# 原文预测 -> 期望库内行。用于防止规则漂移时悄悄改数。
EXPECTED = [
    ("新型储能累计装机规模", 3.7, "亿千瓦", "2030-12-31", "calendar_year_end", "2030年"),
    ("新型储能累计装机平均时长", 3.47, "小时", "2030-12-31", "calendar_year_end", "2030年"),
    ("工商业储能累计装机规模", 30.0, "GW", "2030-12-31", "five_year_plan_end", "十五五"),
]
MODALITY = ("预计", "有望", "展望", "预测")


def load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_cnesa_storage", FETCHER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 解析注解时需要模块已注册
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="不抓网，只核对 raw/快照与库")
    args = ap.parse_args()

    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    mod = load_fetcher()
    want = {(i, round(v, 4), u, p, t, raw) for i, v, u, p, t, raw in EXPECTED}
    text = None
    if not args.offline:
        r, err = mod.http(mod.ARTICLE, sess=mod.session())
        if err:
            print(f"文章取不到: {err}")
            return 1
        text = mod.to_text(mod.decode(r))
        pub = ""
        dm = re.search(r"在\s*(20\d{2}-\d{2}-\d{2})\s*发布", text)
        if dm:
            pub = dm.group(1)
        frows, _fspans, ffailures = mod.extract_forecasts(
            text, pub, mod.report_period(text))
        print("\n## A 提取层")
        check(len(frows) == 3, f"预测提取行数 {len(frows)} == 3")
        got = {(r["indicator"], round(float(r["value"]), 4), r["unit"],
                r["period_target"], r["period_target_type"], r["period_target_raw"])
               for r in frows}
        check(got == want, f"数值/单位/目标期/原文表述逐条命中（缺 {sorted(want - got)}，多 {sorted(got - want)}）")
        check(not ffailures, f"预测提取无失败项（失败 {len(ffailures)}）")
        check(all(r["source_org"] for r in frows), "每行都有提出方 source_org")
        check(all(r["source_doc"].startswith("http") for r in frows), "每行都有原文 URL source_doc")
        check(all(any(w in r["context"] for w in MODALITY) for r in frows),
              "每行原句保留「预计/有望/展望」语气词（未被洗成确定值）")
        check(all(r["context"] in text for r in frows), "每行原句能在正文原样定位")

    if text is None and SNAPSHOT.exists():
        text = SNAPSHOT.read_text(encoding="utf-8")

    print("\n## B 库内层")
    con = duckdb.connect(str(DB), read_only=True)
    try:
        n = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        check(n == 3, f"库内 {TABLE} {n} 行 == 3")
        dup = con.execute(
            f"SELECT count(*) FROM (SELECT dataset, indicator, period_target, source_org "
            f"FROM {TABLE} GROUP BY 1,2,3,4 HAVING count(*) > 1)").fetchone()[0]
        check(int(dup) == 0, f"主键 (dataset, indicator, period_target, source_org) 无重复（重复组 {dup}）")
        blank = con.execute(
            f"SELECT count(*) FROM {TABLE} WHERE coalesce(trim(source_org),'')='' "
            "OR coalesce(trim(source_doc),'')='' OR coalesce(trim(context),'')='' "
            "OR coalesce(trim(rule_version),'')='' OR period_published IS NULL "
            "OR published_date IS NULL OR extracted_at IS NULL").fetchone()[0]
        check(int(blank) == 0, f"提出方/原文/原句/时点逐行非空（缺 {blank} 行）")
        past = con.execute(
            f"SELECT count(*) FROM {TABLE} WHERE period_target <= current_date").fetchone()[0]
        check(int(past) == 0, f"目标期全部晚于今天（不晚于今天 {past} 行）")
        rows = con.execute(
            f"SELECT indicator, value, unit, period_target, period_target_type, "
            f"period_target_raw, period_published, source_org, source_doc, context "
            f"FROM {TABLE} ORDER BY indicator").fetchall()
        got_db = {(i, round(float(v), 4), u, str(p), t, raw)
                  for i, v, u, p, t, raw, _pp, _so, _sd, _c in rows}
        check(got_db == want, f"库内数值与原文预测一致（缺 {sorted(want - got_db)}）")
        check(all(str(pp) == "2026-01-01" for *_x, pp, _so, _sd, _c in rows),
              "period_published 逐行 = 2026-01（报告口径期）")
        check(all(so == "中关村储能产业技术联盟 CNESA" for *_x, so, _sd, _c in rows),
              "source_org 逐行 = CNESA")
        if text is not None:
            missing = [i for i, *_x, c in rows if c not in text]
            check(not missing, f"库内每行原句能在正文定位（找不到 {missing}）")

        print("\n## C 隔离性")
        obs = con.execute(f"SELECT count(*) FROM {OBS_TABLE}").fetchone()[0]
        check(int(obs) == 33, f"观测表 {OBS_TABLE} 仍是 33 行（实际 {obs}）")
        obs_future = con.execute(
            f"SELECT count(*) FROM {OBS_TABLE} WHERE period > current_date").fetchone()[0]
        check(int(obs_future) == 0, f"观测表没有未来期行（{obs_future} 行）")

        from db.checks.industry import c8_forecast
        ok, detail = c8_forecast(con)
        check(ok, f"C8_forecast PASS（rows={detail.get('rows')}）")
        if not ok:
            print(f"  C8 明细: {detail}")
    finally:
        con.close()

    print(f"\n结论：{'PASS' if not fails else 'FAIL'}（{len(fails)} 项失败）")
    for f in fails:
        print(f"  - {f}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
