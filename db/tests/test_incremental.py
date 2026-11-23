"""WORK-529 incremental planning and idempotent-load contracts."""

from __future__ import annotations

import datetime as dt
import argparse
import pytest

import duckdb
import pandas as pd

from db.etl import common, fetch, incremental


def _calendar(qdb, days: list[str]) -> None:
    qdb.write_partition(
        pd.DataFrame(
            {
                "exchange": ["SSE"] * len(days),
                "cal_date": days,
                "is_open": [1] * len(days),
                "pretrade_date": days,
            }
        ),
        "trade_cal",
        "SSE",
        "test",
    )


def _calendar_flags(qdb, rows: list[tuple[str, int]]) -> None:
    qdb.write_partition(
        pd.DataFrame(
            {
                "exchange": ["SSE"] * len(rows),
                "cal_date": [day for day, _ in rows],
                "is_open": [flag for _, flag in rows],
                "pretrade_date": [day for day, _ in rows],
            }
        ),
        "trade_cal",
        "SSE",
        "test",
    )


def _daily(qdb, day: str) -> None:
    qdb.write_partition(
        pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": [day], "close": [10.5]}),
        "daily",
        day,
        "test",
    )


class EmptyClient:
    def __init__(self) -> None:
        self.calls = []

    def call(self, api, params, **kwargs):
        self.calls.append((api, dict(params)))
        return [], []

    def frame(self, api, params, **kwargs):
        self.calls.append((api, dict(params)))
        return pd.DataFrame()


def test_empty_database_uses_full_backfill_start(qdb):
    _calendar(qdb, ["20200102", "20200103"])
    assert incremental._market_start("daily") == incremental.DEFAULT_START


def test_normal_plan_advances_one_trading_day(qdb):
    _calendar(qdb, ["20200102", "20200103", "20200106"])
    _daily(qdb, "20200102")
    _daily(qdb, "20200103")
    plan = incremental.market_plans(
        incremental.parse_now("2020-01-06T19:00:00+08:00")
    )["daily"]
    assert plan.start == "20200106"
    assert plan.end == "20200106"
    assert plan.days == ("20200106",)


def test_holiday_crossing_keeps_only_open_days(qdb):
    _calendar(qdb, ["20200120", "20200121", "20200122", "20200123", "20200203", "20200204"])
    _daily(qdb, "20200123")
    plan = incremental.market_plans(
        incremental.parse_now("2020-02-04T19:00:00+08:00")
    )["daily"]
    assert plan.days == ("20200203", "20200204")


def test_before_close_is_a_noop(qdb):
    _calendar(qdb, ["20200106", "20200107"])
    _daily(qdb, "20200106")
    plan = incremental.market_plans(
        incremental.parse_now("2020-01-07T10:00:00+08:00")
    )["daily"]
    assert plan.is_noop


def test_source_delay_does_not_write_an_empty_partition(qdb):
    _calendar(qdb, ["20200106"])
    client = EmptyClient()
    fetcher = fetch.Fetcher(
        client,
        "20200106",
        "20200106",
        resume=True,
        concurrency=1,
        strict_empty_dates={"20200106"},
    )
    _, failures = fetcher.fetch_range(fetch.DATASETS["daily"], concurrency=1)
    assert failures and failures[0]["source_delay"] is True
    assert qdb.read_manifest("daily", "20200106") is None
    assert not qdb.partition_file("daily", "20200106").exists()


def test_financial_merge_preserves_existing_history(qdb):
    old = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "end_date": "20231231",
                "report_type": "1",
                "ann_date": "20240101",
                "dup_seq": 0,
                "total_revenue": 1.0,
            }
        ]
    )
    qdb.write_partition(old, "income", "600000.SH", "test")
    new = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "end_date": "20240630",
                "report_type": "1",
                "ann_date": "20240801",
                "total_revenue": 2.0,
            }
        ]
    )
    merged = fetch.merge_existing_frame("income", "600000.SH", new)
    assert len(merged) == 2
    assert set(pd.to_datetime(merged["end_date"]).dt.strftime("%Y-%m-%d")) == {
        "2023-12-31",
        "2024-06-30",
    }
    kept, dropped = common.drop_null_pk_rows(merged, "income")
    assert dropped == 0
    assert len(kept) == 2
    qdb.write_partition(merged, "income", "600000.SH", "test")
    merged_again = fetch.merge_existing_frame("income", "600000.SH", new)
    assert len(merged_again) == 2


def test_mixed_date_formats_are_both_normalized():
    frame = pd.DataFrame(
        {"end_date": [dt.date(2026, 9, 30), "20260930"], "ann_date": ["20261113", "20261113"]}
    )
    out = common.normalize_date_columns(frame, "cashflow")
    assert out["end_date"].tolist() == [dt.date(2026, 9, 30), dt.date(2026, 9, 30)]
    assert out["ann_date"].tolist() == [dt.date(2026, 11, 13), dt.date(2026, 11, 13)]


def test_load_is_idempotent_and_advances_max(qdb):
    _calendar(qdb, ["20200102", "20200103", "20200106"])
    _daily(qdb, "20200102")
    _daily(qdb, "20200103")
    incremental.load_selected({"daily": ["20200102", "20200103"]})
    con = duckdb.connect(str(qdb.DB_PATH), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM daily").fetchone()[0] == 2
        assert str(con.execute("SELECT max(trade_date) FROM daily").fetchone()[0]) == "2020-01-03"
    finally:
        con.close()
    incremental.load_selected({"daily": ["20200102", "20200103"]})
    con = duckdb.connect(str(qdb.DB_PATH), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM daily").fetchone()[0] == 2
    finally:
        con.close()
    _daily(qdb, "20200106")
    incremental.load_selected({"daily": ["20200106"]})
    con = duckdb.connect(str(qdb.DB_PATH), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM daily").fetchone()[0] == 3
        assert str(con.execute("SELECT max(trade_date) FROM daily").fetchone()[0]) == "2020-01-06"
    finally:
        con.close()


def test_finance_plan_has_seven_day_overlap():
    plan = incremental.finance_plan(
        incremental.parse_now("2026-11-23T19:00:00+08:00"), overlap_days=7
    )
    assert plan.start == "20261117"
    assert plan.end == "20261123"
    assert len(plan.days) == 7


def test_index_weight_only_runs_on_first_trading_day(qdb):
    _calendar(qdb, ["20261102", "20261103"])
    first = incremental.index_weight_plan(
        incremental.parse_now("2026-11-02T19:35:00+08:00")
    )
    later = incremental.index_weight_plan(
        incremental.parse_now("2026-11-03T19:35:00+08:00")
    )
    assert not first.is_noop
    assert first.start == "20261001"
    assert first.end == "20261102"
    assert later.is_noop


def test_calendar_beyond_coverage_is_unknown(qdb):
    _calendar(qdb, ["20200102"])
    assert incremental.check_day("20200103") == 2
    assert incremental.check_day("20200102") == 0


def test_strict_empty_dates_protects_critical_datasets_and_allows_empty_events():
    now = incremental.parse_now("2020-01-07T19:00:00+08:00")
    plan = incremental.Plan("daily", "20200106", "20200107", "test", ("20200106", "20200107"))
    assert incremental.strict_empty_dates_for("daily", plan, now) == {"20200106", "20200107"}
    assert incremental.strict_empty_dates_for("suspend_d", plan, now) == {"20200107"}


def test_holiday_returns_zero_before_creating_a_client(qdb, monkeypatch):
    _calendar_flags(qdb, [("20200103", 1), ("20200104", 0)])

    class FailClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("client must not be created on a holiday")

    import db.etl.tushare_client as client_mod

    monkeypatch.setattr(client_mod, "TushareClient", FailClient)
    args = argparse.Namespace(
        dataset="daily",
        group="market",
        now="2020-01-04T10:00:00+08:00",
        concurrency=1,
        rpm=60,
        retries=0,
    )
    assert incremental.run(args) == 0


def test_load_only_picks_up_raw_partitions_ahead_of_duckdb(qdb):
    _calendar(qdb, ["20200102", "20200103"])
    _daily(qdb, "20200102")
    _daily(qdb, "20200103")
    incremental.load_selected({"daily": ["20200102"]})
    incremental.load_only(["daily"])
    con = duckdb.connect(str(qdb.DB_PATH), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM daily").fetchone()[0] == 2
        assert str(con.execute("SELECT max(trade_date) FROM daily").fetchone()[0]) == "2020-01-03"
    finally:
        con.close()


def test_index_weight_runner_passes_concurrency_and_propagates_delay(qdb, monkeypatch):
    _calendar(qdb, ["20261102"])
    seen: list[int] = []

    def fake_fetch(self, spec, concurrency):
        seen.append(concurrency)
        return 0, [{"source_delay": True, "error": "not published"}]

    monkeypatch.setattr(fetch.Fetcher, "fetch_index_weight", fake_fetch)
    monkeypatch.setattr(incremental, "load_only", lambda datasets: {})
    summary = {"datasets": {}}
    args = argparse.Namespace(concurrency=3)
    code = incremental._run_index_weight(
        object(),
        incremental.parse_now("2026-11-02T19:35:00+08:00"),
        args,
        summary,
    )
    assert seen == [3]
    assert code == 75


def test_corrupt_old_partition_is_not_silently_replaced(qdb):
    new = pd.DataFrame(
        [{"ts_code": "600000.SH", "end_date": "20240630", "report_type": "1", "ann_date": "20240801"}]
    )
    old = pd.DataFrame(
        [{"ts_code": "600000.SH", "end_date": "20231231", "report_type": "1", "ann_date": "20240101"}]
    )
    qdb.write_partition(old, "income", "600000.SH", "test")
    target = qdb.partition_file("income", "600000.SH")
    target.write_bytes(target.read_bytes() + b"broken")
    with pytest.raises(RuntimeError, match="拒绝覆盖历史"):
        fetch.merge_existing_frame("income", "600000.SH", new)
