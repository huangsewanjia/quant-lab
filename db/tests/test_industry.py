"""行业层（WORK-568）：解析、去重、分区幂等与主键冲突的行为测试。

测试用 tmp_path 造 raw 文件，不碰真实 /srv/share/quantdb。
"""

from __future__ import annotations

import datetime as dt
import json

import duckdb
import pandas as pd
import pytest

from db.etl import industry as ind


def _write_gfex_raw(rows):
    path = ind.raw_dir("parquet") / "gfex_lithium_carbonate"
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path / "part.parquet", index=False)
    (path / "manifest.json").write_text(
        json.dumps({"dataset": "gfex_lithium_carbonate", "rows": len(rows)}),
        encoding="utf-8",
    )


def _gfex_rows(day: str, close: float = 100000.0):
    base = dict(dataset="gfex_lithium_carbonate", source="gfex",
                source_url="http://example/gfex", published_date=day,
                contract="2606", variety="碳酸锂", period=day)
    return [
        dict(base, field="close", indicator="收盘价", value=close, unit="元/吨",
             trade_date=day),
        dict(base, field="low", indicator="最低价", value=close - 1000,
             unit="元/吨", trade_date=day),
        dict(base, field="high", indicator="最高价", value=close + 1000,
             unit="元/吨", trade_date=day),
        dict(base, field="volumn", indicator="成交量", value=10, unit="手",
             trade_date=day),
        dict(base, field="turnover", indicator="成交额", value=close * 10 / 10000,
             unit="元", trade_date=day),
    ]


def _prepare_db(qdb):
    with qdb.duckdb_connect() as con:
        con.execute(
            'CREATE TABLE IF NOT EXISTS data_catalog (' 
            '"行业" VARCHAR, "区域" VARCHAR, "缺口优先级" VARCHAR, "指标名" VARCHAR, '
            '"频率" VARCHAR, "单位" VARCHAR, "一手来源" VARCHAR)'
        )
        ind.apply_schema(con)


def test_parse_month_and_date():
    assert ind.parse_month("2026-09") == dt.date(2026, 9, 1)
    assert ind.parse_month("2026年9月") == dt.date(2026, 9, 1)
    assert ind.parse_month("2026-13") is None
    assert ind.parse_month("") is None
    assert ind.parse_date("20261120") == dt.date(2026, 11, 20)
    assert ind.parse_date("2026-11-20") == dt.date(2026, 11, 20)
    assert ind.parse_date("2026.11.11") == dt.date(2026, 11, 11)
    assert ind.parse_date("2026年11月20日27日") is None


def test_parse_number_handles_percent_and_thousands():
    assert ind.parse_number("1,865.00") == 1865.0
    assert ind.parse_number("▲ 1.85 %") == 1.85
    assert ind.parse_number("— 0.00 %") == 0.0
    assert ind.parse_number("盘涨跌幅") is None
    assert ind.parse_number("174000元/吨") == 174000.0


def test_mof_published_date_from_url_and_raw_kept():
    raw = pd.DataFrame([{
        "dataset": "mof_fiscal", "source": "财政部",
        "source_url": "https://gks.mof.gov.cn/tongjishuju/202610/t20261023_4003380.htm",
        "published_date": "2017年11月21日", "period": "2017-11",
        "indicator": "全国一般公共预算收入", "value": 172292.0, "unit": "亿元",
        "context": "", "article_title": "2026年1-10月财政收支情况",
    }])
    out = ind.transform_mof(raw, {})
    assert out.loc[0, "published_date"] == dt.date(2026, 10, 23)
    assert out.loc[0, "published_date_raw"] == "2017年11月21日"
    assert out.loc[0, "period"] == dt.date(2017, 11, 1)


def test_trendforce_classifies_rows():
    raw = pd.DataFrame([
        {"series": "DRAM 现货价", "spec": "项目", "value": "盘涨跌幅",
         "unit": "USD", "as_of": "", "status": "OK"},
        {"series": "DRAM 现货价", "spec": "DDR5 16Gb", "value": "60.70",
         "unit": "USD", "as_of": "", "status": "OK"},
        {"series": "DRAM 现货价", "spec": "DDR4 16GB SO-DIMM", "value": "▲ 1.85 %",
         "unit": "USD", "as_of": "", "status": "OK"},
    ])
    out = ind.transform_trendforce(raw, {"fetched_at": "2026-11-23T17:27:01+0800"})
    assert list(out["unit_kind"]) == ["header", "price", "pct_change"]
    assert out.loc[1, "value"] == pytest.approx(60.70)
    assert out.loc[2, "value"] == pytest.approx(1.85)
    assert out.loc[0, "snapshot_date"] == dt.date(2026, 11, 23)


def test_gfex_turnover_unit_is_corrected_to_wan():
    raw = pd.DataFrame(_gfex_rows("20260528"))
    out = ind.transform_gfex(raw, {})
    unit = out.loc[out["field"] == "turnover", "unit"].iloc[0]
    assert unit == "万元"


def test_hash_key_dedupes_exact_repeats_and_keeps_revisions(qdb):
    _prepare_db(qdb)
    raw = pd.DataFrame([
        {"goods_id": "1275", "goods": "碳酸锂", "spec": "碳酸锂", "market": "市场价",
         "supplier": "A", "as_of": "2026-11-23", "price": "174000元/吨", "unit": "吨",
         "url": "u1", "status": "OK"},
        {"goods_id": "1275", "goods": "碳酸锂", "spec": "碳酸锂", "market": "市场价",
         "supplier": "A", "as_of": "2026-11-23", "price": "174000元/吨", "unit": "吨",
         "url": "u1", "status": "OK"},
        {"goods_id": "1275", "goods": "碳酸锂", "spec": "碳酸锂", "market": "市场价",
         "supplier": "A", "as_of": "2026-11-23", "price": "180000元/吨", "unit": "吨",
         "url": "u1", "status": "OK"},
    ])
    out = ind.transform_ppi(raw, {})
    deduped, dropped = ind._dedupe_hash_rows(out)
    assert dropped == 1
    assert len(deduped) == 2
    assert deduped["price"].tolist() == [174000.0, 180000.0]


def test_load_partition_idempotent_and_replaces_removed_day(qdb):
    _prepare_db(qdb)
    _write_gfex_raw(_gfex_rows("20260528") + _gfex_rows("20260529"))

    with qdb.duckdb_connect() as con:
        source = ind.SOURCE_BY_NAME["gfex_lithium_carbonate"]
        first = ind.load_source(con, source, "run-1")
        second = ind.load_source(con, source, "run-2")
        assert first["loaded_rows"] == 10
        assert second["loaded_rows"] == 10
        assert second["staged_rows"] == 10

    # 上游只剩一天：分区替换只删该分区，另一天保留
    _write_gfex_raw(_gfex_rows("20260528"))
    with qdb.duckdb_connect() as con:
        third = ind.load_source(con, source, "run-3")
        days = con.execute(
            "SELECT count(DISTINCT trade_date) FROM industry_gfex_lithium_carbonate"
        ).fetchone()[0]
        assert third["loaded_rows"] == 10  # 5 字段 × 2 天，删除的是 20260529 的 5 行
        assert days == 2

    audit = qdb.META_DIR / "industry"
    assert not audit.exists()  # load_source 本身不写报告


def test_natural_key_conflict_is_loud(qdb):
    _prepare_db(qdb)
    rows = _gfex_rows("20260528")
    rows.append(dict(rows[0]))  # 同一 (trade_date, contract, field) 再来一行
    _write_gfex_raw(rows)
    with qdb.duckdb_connect() as con:
        source = ind.SOURCE_BY_NAME["gfex_lithium_carbonate"]
        with pytest.raises(SystemExit):
            ind.load_source(con, source, "run-conflict")


def test_catalog_map_points_at_existing_catalog_rows():
    """映射表的指标名必须与 data_catalog 原文一致（防止手打错字）。"""
    db_path = ind.common.DB_PATH
    if not db_path.exists():
        pytest.skip("主库不存在")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        orphans = con.execute(
            """
            SELECT count(*) FROM industry_catalog_map m
            WHERE NOT EXISTS (
                SELECT 1 FROM data_catalog c
                WHERE c."行业" = m.catalog_industry
                  AND c."指标名" = m.catalog_indicator
            )
            """
        ).fetchone()[0]
    finally:
        con.close()
    assert orphans == 0
