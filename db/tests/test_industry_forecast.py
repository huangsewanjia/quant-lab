"""预测表（WORK-584）：提取分流、装载幂等与 C8_forecast 闸的行为测试。

两条线：
* 装载 + 闸：用 tmp_path 造 raw 与快照，不碰真实 /srv/share/quantdb；
* 提取契约：直接 import live 树的 fetch_cnesa_storage.py（既有形态，脚本不在本仓库），
  目录不存在时整体 skip；不联网、不写 raw。
"""

from __future__ import annotations

import importlib.util
import sys
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from db.checks import industry as chk
from db.etl import industry as ind

FETCH_DIR = Path("/srv/share/quantdb/code/industry_fetch")
ARTICLE = "https://www.cnesa.org/information/detail/?column_id=1&id=7798"


def _forecast_rows():
    base = dict(
        dataset="cnesa_forecast", unit="小时",
        period_target="2030-12-31", period_target_raw="2030年",
        period_target_type="calendar_year_end", period_published="2026-01",
        source_org="中关村储能产业技术联盟 CNESA", source_doc=ARTICLE,
        published_date="2026-01-22", extracted_at="2026-11-25 22:40:00+0800",
        rule_version="cnesa-forecast-v1",
    )
    return [
        dict(base, indicator="新型储能累计装机规模", value=3.7, unit="亿千瓦",
             context="预计2030年累计装机有望达到3.7亿千瓦以上。",
             caliber="2030 年末展望｜原句：预计2030年累计装机有望达到3.7亿千瓦以上。"),
        dict(base, indicator="新型储能累计装机平均时长", value=3.47,
             context="2026年起，时长提升明显加速，预计至2030年将达到3.47小时。",
             caliber="2030 年平均时长预测｜原句：预计至2030年将达到3.47小时。"),
        dict(base, indicator="工商业储能累计装机规模", value=30.0, unit="GW",
             period_target_raw="十五五", period_target_type="five_year_plan_end",
             context="展望“十五五”，工商业储能累计装机有望突破30GW。",
             caliber="「十五五」末展望｜原句：展望“十五五”，工商业储能累计装机有望突破30GW。"),
    ]


def _write_raw(rows):
    root = ind.raw_dir("parquet") / "cnesa_forecast"
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(root / "part.parquet", index=False)


def _prepare_db(qdb):
    with qdb.duckdb_connect() as con:
        con.execute(
            'CREATE TABLE IF NOT EXISTS data_catalog ('
            '"行业" VARCHAR, "区域" VARCHAR, "缺口优先级" VARCHAR, "指标名" VARCHAR, '
            '"频率" VARCHAR, "单位" VARCHAR, "一手来源" VARCHAR)'
        )
        ind.apply_schema(con)


def _snapshot(qdb, rows):
    root = qdb.RAW_DIR / "industry" / "cnesa_forecast"
    root.mkdir(parents=True, exist_ok=True)
    (root / "article.txt").write_text(
        "\n".join(r["context"] for r in rows), encoding="utf-8")


def test_forecast_load_is_idempotent_and_keeps_provenance(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)

    ind.run_load(names=["cnesa_forecast"])
    ind.run_load(names=["cnesa_forecast"])

    with qdb.duckdb_connect(read_only=True) as con:
        assert con.execute("SELECT count(*) FROM industry_forecast").fetchone()[0] == 3
        # 原单位保留（3.7 亿千瓦不换算成 370GW），原句与提出方逐行可查。
        got = con.execute(
            "SELECT value, unit, period_target, period_target_type, source_org, "
            "context, rule_version, extracted_at FROM industry_forecast "
            "WHERE indicator = '新型储能累计装机规模'"
        ).fetchone()
        assert got == (
            3.7, "亿千瓦", dt.date(2030, 12, 31), "calendar_year_end",
            "中关村储能产业技术联盟 CNESA", rows[0]["context"],
            "cnesa-forecast-v1", dt.datetime(2026, 11, 25, 22, 40),
        ), got
        # 观测表不受影响：预测行没有写进 industry_cnesa_storage。
        assert con.execute(
            "SELECT count(*) FROM industry_cnesa_storage").fetchone()[0] == 0


def test_forecast_duplicate_business_key_is_loud(qdb):
    rows = _forecast_rows()
    rows.append(dict(rows[0]))  # 同 指标+目标期+提出方
    _write_raw(rows)
    _prepare_db(qdb)
    with pytest.raises(SystemExit, match="自然主键冲突"):
        ind.run_load(names=["cnesa_forecast"])


def test_forecast_load_replaces_retracted_rows(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])

    _write_raw(rows[:2])  # 上游撤掉一条：按 dataset 分区整组替换，不留残行
    ind.run_load(names=["cnesa_forecast"])
    with qdb.duckdb_connect(read_only=True) as con:
        assert con.execute("SELECT count(*) FROM industry_forecast").fetchone()[0] == 2


def test_c8_passes_on_provenance_complete_rows(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows)

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert ok, detail
    assert detail["rows"] == 3
    assert detail["snapshots"]["cnesa_forecast"]["located"] == 3


def test_c8_fails_when_context_not_in_snapshot(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows[:2])  # 快照缺第三条的原句

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["snapshots"]["cnesa_forecast"]["missing"]


def test_c8_fails_when_modality_word_is_washed_out(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows)
    with qdb.duckdb_connect() as con:
        con.execute(
            "UPDATE industry_forecast SET context = '2030年新型储能累计装机3.7亿千瓦' "
            "WHERE indicator = '新型储能累计装机规模'")

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["missing_modality"]


def test_c8_fails_when_target_period_is_not_in_the_future(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows)
    with qdb.duckdb_connect() as con:
        con.execute(
            "UPDATE industry_forecast SET period_target = DATE '2020-12-31', "
            "period_target_type = 'calendar_year_end'")

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["target_not_future"] == 3


def test_c8_fails_when_table_missing(qdb):
    _prepare_db(qdb)
    with qdb.duckdb_connect() as con:
        con.execute("DROP TABLE industry_forecast")
    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert "不存在" in detail["error"]


# --------------------------------------------------------------------------- #
# 提取契约：live 树脚本不在本仓库，目录不存在时 skip
# --------------------------------------------------------------------------- #

pytestmark_fetch = pytest.mark.skipif(
    not (FETCH_DIR / "fetch_cnesa_storage.py").exists(),
    reason="行业层抓取脚本不在本机（/srv/share/quantdb/code/industry_fetch）",
)


def _load_fetcher():
    spec = importlib.util.spec_from_file_location(
        "fetch_cnesa_storage", FETCH_DIR / "fetch_cnesa_storage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 解析注解时需要模块已注册
    spec.loader.exec_module(mod)
    return mod


@pytestmark_fetch
def test_fetch_forecast_extraction_keeps_modality_and_source():
    mod = _load_fetcher()
    text = (
        "展望”十五五“，增速虽然放缓，但庞大的基数仍将催生可观的绝对增量，"
        "预计2030年累计装机有望达到3.7亿千瓦以上。\n"
        "2026年起，时长提升明显加速，预计至2030年将达到3.47小时。\n"
        "展望“十五五”，工商业储能继续保持稳定增长态势，收益更加多元， "
        "累计装机有望突破30GW。\n"
    )
    rows, spans, failures = mod.extract_forecasts(text, "2026-01-22", "2026-01")

    assert failures == [], failures
    assert len(rows) == 3 and len(spans) == 3
    by_indicator = {r["indicator"]: r for r in rows}
    assert by_indicator["新型储能累计装机规模"]["value"] == 3.7
    assert by_indicator["新型储能累计装机规模"]["unit"] == "亿千瓦"
    assert by_indicator["新型储能累计装机平均时长"]["value"] == 3.47
    assert by_indicator["工商业储能累计装机规模"]["value"] == 30.0
    assert by_indicator["工商业储能累计装机规模"]["period_target_raw"] == "十五五"
    assert by_indicator["工商业储能累计装机规模"]["period_target_type"] == "five_year_plan_end"
    for row in rows:
        assert row["source_org"] == mod.SOURCE_ORG
        assert row["source_doc"] == mod.ARTICLE
        assert row["period_published"] == "2026-01"
        assert row["rule_version"] == "cnesa-forecast-v1"
        assert row["context"] in text
        assert any(w in row["context"] for w in ("预计", "有望", "展望"))


@pytestmark_fetch
def test_fetch_forecast_extraction_records_drift_instead_of_guessing():
    """句式漂移（上游改词）时：不产出预测行，不猜值，交给 failed.json 暴露。"""
    mod = _load_fetcher()
    text = "2030年新型储能累计装机3.7亿千瓦。\n"
    rows, spans, failures = mod.extract_forecasts(text, "2026-01-22", "2026-01")
    assert rows == [] and spans == [] and failures == []
    # 报告期推不出时显式失败，不硬编码 period_published。
    rows2, _spans2, failures2 = mod.extract_forecasts(text, "2026-01-22", None)
    assert rows2 == []
    assert any("报告期" in f["error"] for f in failures2)


def test_c8_fails_on_empty_forecast_table(qdb):
    _prepare_db(qdb)
    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["rows"] == 0


def test_c8_fails_when_article_snapshot_is_missing(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert "快照不存在" in detail["snapshots"]["cnesa_forecast"]["error"]


def test_c8_fails_on_unit_outside_vocabulary(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows)
    with qdb.duckdb_connect() as con:
        con.execute("UPDATE industry_forecast SET unit = 'MWh'")

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["unit_vocabulary"]["unexpected"] == 3


def test_c8_fails_on_bad_target_date_encoding(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows)
    with qdb.duckdb_connect() as con:
        con.execute("UPDATE industry_forecast SET period_target = DATE '2030-06-30'")

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["target_type"]["bad_date_encoding"] == 3


def test_c8_fails_when_target_type_contradicts_raw_label(qdb):
    rows = _forecast_rows()
    _write_raw(rows)
    _prepare_db(qdb)
    ind.run_load(names=["cnesa_forecast"])
    _snapshot(qdb, rows)
    with qdb.duckdb_connect() as con:
        # 十五五被错标成日历年：日期编码仍是 12-31，单看日期发现不了。
        con.execute(
            "UPDATE industry_forecast SET period_target_type = 'calendar_year_end' "
            "WHERE period_target_raw = '十五五'")

    with qdb.duckdb_connect(read_only=True) as con:
        ok, detail = chk.c8_forecast(con)
    assert not ok
    assert detail["target_type_raw_mismatch"]


def test_load_refuses_all_groups_when_a_raw_file_is_missing(qdb):
    _prepare_db(qdb)
    with pytest.raises(SystemExit, match="raw 缺失"):
        ind.run_load(names=["cnesa_forecast"])
    with qdb.duckdb_connect(read_only=True) as con:
        assert con.execute("SELECT count(*) FROM industry_forecast").fetchone()[0] == 0
        assert con.execute(
            "SELECT count(*) FROM industry_load_audit "
            "WHERE table_name = 'industry_forecast'").fetchone()[0] == 0
