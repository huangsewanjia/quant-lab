"""装载变更留痕（WORK-585）：diff 日志、快照源整源替换与 C9 闸的行为测试。

全部离线：raw 用 tmp_path 造 parquet，绝不碰 /srv/share/quantdb 生产库。
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from db.checks import industry as checks_industry
from db.etl import industry as ind


CNESA_COLUMNS = ["period", "indicator", "value", "unit", "caliber",
                  "article_title", "published_date", "source_url"]


def _write_cnesa(rows):
    path = ind.raw_dir("parquet") / "cnesa_storage"
    path.mkdir(parents=True, exist_ok=True)
    if rows:
        frame = pd.DataFrame(rows)
    else:  # 空快照也要有列，否则写出的是零列 parquet，读不回来
        frame = pd.DataFrame({c: pd.Series([], dtype="object") for c in CNESA_COLUMNS})
    frame.to_parquet(path / "part.parquet", index=False)
    (path / "manifest.json").write_text(
        json.dumps({
            "dataset": "cnesa_storage",
            "fetched_at": "2026-11-25T00:00:00+0800",
        }),
        encoding="utf-8",
    )


def _row(period, indicator, value, unit="GW", **over):
    base = {
        "period": period,
        "indicator": indicator,
        "value": value,
        "unit": unit,
        "caliber": "原句：测试用",
        "article_title": "CNESA 2025 年度数据",
        "published_date": "2026-01-15",
        "source_url": "http://example/cnesa",
    }
    base.update(over)
    return base


def _write_gfex(rows):
    path = ind.raw_dir("parquet") / "gfex_lithium_carbonate"
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path / "part.parquet", index=False)
    (path / "manifest.json").write_text(
        json.dumps({"dataset": "gfex_lithium_carbonate", "rows": len(rows)}),
        encoding="utf-8",
    )


def _gfex_rows(day: str, close: float):
    base = dict(dataset="gfex_lithium_carbonate", source="gfex",
                source_url="http://example/gfex", published_date=day,
                contract="2606", variety="碳酸锂", period=day)
    return [
        dict(base, field="close", indicator="收盘价", value=close, unit="元/吨",
             trade_date=day),
        dict(base, field="low", indicator="最低价", value=close - 1000,
             unit="元/吨", trade_date=day),
    ]


def _prepare_db(qdb):
    with qdb.duckdb_connect() as con:
        con.execute(
            'CREATE TABLE IF NOT EXISTS data_catalog ('
            '"行业" VARCHAR, "区域" VARCHAR, "缺口优先级" VARCHAR, "指标名" VARCHAR, '
            '"频率" VARCHAR, "单位" VARCHAR, "一手来源" VARCHAR)'
        )
        ind.apply_schema(con)


def _diff_rows(con, run_id):
    return con.execute(
        "SELECT change_type, indicator, column_name, old_value, new_value, period "
        "FROM industry_load_diff WHERE run_id = ? "
        "ORDER BY change_type, indicator, column_name",
        [run_id],
    ).fetchall()


def test_value_change_and_added_row_are_logged(qdb):
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["cnesa_storage"]
    _write_cnesa([
        _row("2026-01", "新型储能累计装机规模", "144.7"),
        _row("2026-01", "新型储能新增投运功率规模", "66.43"),
    ])
    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        _write_cnesa([
            _row("2026-01", "新型储能累计装机规模", "150.9"),
            _row("2026-01", "新型储能新增投运功率规模", "66.43"),
            _row("2026-01", "新增储能相关政策数量", "869", unit="项"),
        ])
        ind.load_source(con, source, "run-2")
        rows = _diff_rows(con, "run-2")
        loaded = con.execute(
            "SELECT value FROM industry_cnesa_storage "
            "WHERE indicator = '新型储能累计装机规模'"
        ).fetchone()[0]

    changed = [r for r in rows if r[0] == "value_changed"]
    assert len(changed) == 1, rows
    assert changed[0][2] == "value"
    assert (changed[0][3], changed[0][4]) == ("144.7", "150.9")
    assert "新型储能累计装机规模" in changed[0][1]
    assert changed[0][5] == dt.date(2026, 1, 1)

    added = [r for r in rows if r[0] == "row_added"]
    assert len(added) == 1, rows
    assert "新增储能相关政策数量" in added[0][1]
    assert "value=869" in added[0][4]
    assert loaded == 150.9


def test_snapshot_source_drops_vanished_period_and_logs_it(qdb):
    """WORK-577 F3 回归：上游删掉某个期，整源替换后库内不得残留。"""
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["cnesa_storage"]
    assert source.replace_mode == "source"

    _write_cnesa([
        _row("2022-01", "新型储能累计装机平均时长", "2.11", unit="小时"),
        _row("2026-01", "新型储能累计装机规模", "144.7"),
    ])
    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        _write_cnesa([_row("2026-01", "新型储能累计装机规模", "144.7")])
        ind.load_source(con, source, "run-2")
        periods = [r[0] for r in con.execute(
            "SELECT DISTINCT period FROM industry_cnesa_storage ORDER BY 1"
        ).fetchall()]
        rows = _diff_rows(con, "run-2")

    assert periods == [dt.date(2026, 1, 1)]
    dropped = [r for r in rows if r[0] == "row_dropped"]
    assert len(dropped) == 1, rows
    assert "新型储能累计装机平均时长" in dropped[0][1]
    assert "value=2.11" in dropped[0][3]
    assert dropped[0][5] == dt.date(2022, 1, 1)


def test_rerun_without_changes_writes_one_no_change_row(qdb):
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["cnesa_storage"]
    data = [
        _row("2026-01", "新型储能累计装机规模", "144.7"),
        _row("2026-01", "新型储能新增投运功率规模", "66.43"),
    ]
    _write_cnesa(data)
    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        _write_cnesa(data)
        ind.load_source(con, source, "run-2")
        rows = _diff_rows(con, "run-2")
        total = con.execute("SELECT count(*) FROM industry_cnesa_storage").fetchone()[0]

    assert len(rows) == 1, rows
    assert rows[0][0] == "no_change"
    assert rows[0][3] is None and rows[0][5] is None
    assert "差异=0" in rows[0][4]
    assert total == 2


def test_partition_source_still_replaces_only_touched_periods(qdb):
    """其余 16 源维持按期替换：diff 只覆盖本次出现的期，未出现的期不落 row_dropped。"""
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["gfex_lithium_carbonate"]
    assert source.replace_mode == "partition"

    _write_gfex(_gfex_rows("20260528", 100000.0) + _gfex_rows("20260529", 101000.0))
    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        _write_gfex(_gfex_rows("20260528", 99000.0))
        ind.load_source(con, source, "run-2")
        rows = _diff_rows(con, "run-2")
        days = [r[0] for r in con.execute(
            "SELECT DISTINCT trade_date FROM industry_gfex_lithium_carbonate ORDER BY 1"
        ).fetchall()]

    assert [r for r in rows if r[0] == "row_dropped"] == []
    assert all(r[5] == dt.date(2026, 5, 28) for r in rows)
    assert {r[0] for r in rows} == {"value_changed"}
    assert days == [dt.date(2026, 5, 28), dt.date(2026, 5, 29)]


def test_source_replace_refuses_empty_snapshot(qdb):
    """整源模式下空快照不许抹表：抓空/解析失败要报错，不是静默清空。"""
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["cnesa_storage"]
    _write_cnesa([_row("2026-01", "新型储能累计装机规模", "144.7")])
    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        _write_cnesa([])
        with pytest.raises(SystemExit):
            ind.load_source(con, source, "run-2")
        # --full 也不能绕过：安全阀看源自身模式，不看派生出的 mode
        with pytest.raises(SystemExit):
            ind.load_source(con, source, "run-3", full=True)
        rows = con.execute("SELECT count(*) FROM industry_cnesa_storage").fetchone()[0]
        diff = con.execute(
            "SELECT count(*) FROM industry_load_diff WHERE run_id IN ('run-2','run-3')"
        ).fetchone()[0]

    assert rows == 1
    assert diff == 0  # 失败的那两次装载同事务回滚，不留痕迹


def test_c9_requires_a_diff_record_for_every_table(qdb):
    _prepare_db(qdb)
    with qdb.duckdb_connect() as con:
        now = dt.datetime(2026, 11, 25, 22, 30, 0)
        for table in checks_industry.TABLES:
            con.execute(
                "INSERT INTO industry_load_audit VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["R1", table, "src", 1, 1, 1, 1, "partition", now, now],
            )
            con.execute(
                "INSERT INTO industry_load_diff VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["R1", "src", table, None, "(全表)", "no_change", None, None,
                 "staged=1 库内=1 差异=0", now],
            )
        ok, detail = checks_industry.c9_load_diff(con)
        assert ok, detail

        con.execute(
            "DELETE FROM industry_load_diff WHERE table_name = ?",
            [checks_industry.TABLES[0]],
        )
        ok, detail = checks_industry.c9_load_diff(con)
    assert not ok
    assert detail["missing_diff_tables"] == [checks_industry.TABLES[0]]


def test_c9_rejects_malformed_diff_rows(qdb):
    _prepare_db(qdb)
    with qdb.duckdb_connect() as con:
        now = dt.datetime(2026, 11, 25, 22, 30, 0)
        for table in checks_industry.TABLES:
            con.execute(
                "INSERT INTO industry_load_audit VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["R1", table, "src", 1, 1, 1, 1, "partition", now, now],
            )
            con.execute(
                "INSERT INTO industry_load_diff VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["R1", "src", table, None, "(全表)", "no_change", None, None,
                 "staged=1 库内=1 差异=0", now],
            )
        con.execute(
            "INSERT INTO industry_load_diff VALUES (?,?,?,?,?,?,?,?,?,?)",
            ["R1", "src", checks_industry.TABLES[0], None, "x", "value_changed",
             None, "1", "2", now],
        )
        ok, detail = checks_industry.c9_load_diff(con)

    assert not ok
    assert detail["malformed_diff_rows"] == 1


def test_integer_values_are_not_reported_as_changes(qdb):
    """S1：库里是 DOUBLE、staged 是 int64，869 与 869.0 是同一个值。

    去掉 `_cell_text` 的数值归一后这条会红：同一份全整数快照重载会写出
    `value: 869.0 → 869` 一类假变化，复核视图被假变化淹没。
    """
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["cnesa_storage"]
    data = [
        _row("2026-01", "新增储能相关政策数量", "869", unit="项"),
        _row("2026-01", "EPC招标标段数量", "42", unit="个"),
    ]
    _write_cnesa(data)
    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        _write_cnesa(data)
        ind.load_source(con, source, "run-2")
        rows = _diff_rows(con, "run-2")

    assert len(rows) == 1, rows
    assert rows[0][0] == "no_change", rows


FETCH_DIR = Path("/srv/share/quantdb/code/industry_fetch")
SENTENCE_TOTAL = "截至2025年12月底，中国新型储能累计装机规模达到 144.7GW，同比+85% 。"
SENTENCE_DURATION = (
    "储能时长方面 ，CNESA统计显示，新型储能累计装机的平均时长在2021-2025年呈缓慢上升趋势，"
    "由2.11小时逐步增至2.58小时。"
)


def _cnesa_rows_from_html(sentences: list[str]) -> pd.DataFrame:
    """离线 HTML fixture -> 抓取器正文解析 -> raw 行（不联网、不写生产 raw）。"""
    if not (FETCH_DIR / "fetch_cnesa_storage.py").exists():
        pytest.skip("CNESA 抓取脚本不在本机（/srv/share/quantdb/code/industry_fetch）")
    sys.path.insert(0, str(FETCH_DIR))
    fetch = importlib.import_module("fetch_cnesa_storage")
    html = (
        "<html><body><h1>2025年度CNESA储能数据重磅发布</h1>"
        + "".join(f"<p>{s}</p>" for s in sentences)
        + "</body></html>"
    )
    rows, _spans, _fails = fetch.extract(
        fetch.to_text(html), "2026-01-22", "2025年度CNESA储能数据重磅发布"
    )
    return pd.DataFrame(rows)


def test_f3_regression_from_offline_html_fixture(qdb):
    """S3 / 规格第 2 条：上游删掉「2021 年末那句」→ 解析少一期 → 装载后库内清除并进日志。

    走的是抓取器的正文解析（HTML -> 文本 -> 期与指标），不是直接改 parquet，
    这样「句式漂移导致少解析一期」也能被这条测试抓住。
    """
    _prepare_db(qdb)
    source = ind.SOURCE_BY_NAME["cnesa_storage"]

    full = _cnesa_rows_from_html([SENTENCE_TOTAL, SENTENCE_DURATION])
    periods = sorted({str(p) for p in full["period"]})
    assert "2022-01" in periods and "2026-01" in periods, periods
    _write_cnesa(full.to_dict("records"))

    with qdb.duckdb_connect() as con:
        ind.load_source(con, source, "run-1")
        assert con.execute(
            "SELECT count(*) FROM industry_cnesa_storage WHERE period = DATE '2022-01-01'"
        ).fetchone()[0] == 1

        # 上游改版：那句「2021-2025 年由 2.11 小时增至 2.58 小时」被删掉
        shrunk = _cnesa_rows_from_html([SENTENCE_TOTAL])
        assert "2022-01" not in {str(p) for p in shrunk["period"]}
        _write_cnesa(shrunk.to_dict("records"))
        ind.load_source(con, source, "run-2")

        left = [r[0] for r in con.execute(
            "SELECT DISTINCT period FROM industry_cnesa_storage ORDER BY 1"
        ).fetchall()]
        rows = _diff_rows(con, "run-2")

    assert left == [dt.date(2026, 1, 1)]
    dropped = [r for r in rows if r[0] == "row_dropped"]
    # 那句时长原文同时给出 2021 年末 2.11 与 2025 年末 2.58 两个值，删句后两条都消失
    assert len(dropped) == 2, rows
    f3 = [r for r in dropped if r[5] == dt.date(2022, 1, 1)]
    assert len(f3) == 1, dropped
    assert "平均时长" in f3[0][1]
    assert "value=2.11" in f3[0][3]
