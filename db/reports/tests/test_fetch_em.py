from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db.reports.fetch_em import (  # noqa: E402
    Paths,
    build_tables,
    classify_change,
    month_windows,
    normalize_rating,
    normalize_record,
    partition_file,
    partition_ok,
    write_partition,
)


def _raw(**overrides):
    base = {
        "title": "测试报告",
        "stockName": "测试股份",
        "stockCode": "000001",
        "orgCode": "10000001",
        "orgName": "测试证券股份有限公司",
        "orgSName": "测试证券",
        "publishDate": "2026-11-20 00:00:00.000",
        "infoCode": "AP202611201831000001",
        "predictThisYearEps": "1.20",
        "predictNextYearEps": "1.50",
        "predictNextTwoYearEps": "1.80",
        "predictThisYearPe": "10.0",
        "predictNextYearPe": "8.0",
        "predictNextTwoYearPe": "6.0",
        "emRatingName": "买入",
        "emRatingValue": "3",
        "lastEmRatingName": "增持",
        "lastEmRatingValue": "2",
        "ratingChange": 0,
        "indvInduName": "银行",
        "indvInduCode": "480",
        "attachPages": "12",
        "encodeUrl": "abc",
        "market": "SHENZHEN",
        "researcher": "张三,李四",
        "authorID": ["1.张三", "2.李四"],
    }
    base.update(overrides)
    return base


def test_rating_normalisation_is_ordinal_only():
    assert normalize_rating("买入") == ("强买", 5)
    assert normalize_rating("增持") == ("买入", 4)
    assert normalize_rating("优于大市") == ("买入", 4)
    assert normalize_rating("持有") == ("中性", 3)
    assert normalize_rating("减持") == ("减持", 2)
    assert normalize_rating("卖出") == ("卖出", 1)
    assert normalize_rating("") == ("未知", None)


@pytest.mark.parametrize(
    "code,old,new,old_rank,new_rank,expected",
    [
        ("0", "增持", "买入", 4, 5, "upgrade"),
        ("1", "买入", "增持", 5, 4, "downgrade"),
        ("2", "", "买入", None, 5, "initiate"),
        ("3", "买入", "买入", 5, 5, "maintain"),
        ("", "买入", "买入", 5, 5, "maintain"),
    ],
)
def test_rating_change_codes(code, old, new, old_rank, new_rank, expected):
    direction, _ = classify_change(code, old, new, old_rank, new_rank)
    assert direction == expected


def test_normalize_record_keeps_raw_and_no_pdf_bytes():
    fetched_at = dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc)
    row = normalize_record(_raw(), fetched_at)
    assert row["info_code"] == "AP202611201831000001"
    assert row["ts_code"] == "000001.SZ"
    assert row["fiscal_year_this"] == 2026
    assert row["fiscal_year_two"] == 2028
    assert row["rating_norm"] == "强买"
    assert row["rating_change_type"] == "upgrade"
    assert row["pdf_available"] is True
    assert row["attach_pages"] == 12
    assert json.loads(row["raw_json"])["stockCode"] == "000001"
    assert "data" not in row  # no PDF payload is ever stored


def test_month_windows_clip_partial_first_and_last_month():
    got = month_windows(dt.date(2026, 1, 15), dt.date(2026, 3, 2))
    assert got == [
        (dt.date(2026, 1, 15), dt.date(2026, 1, 31)),
        (dt.date(2026, 2, 1), dt.date(2026, 2, 28)),
        (dt.date(2026, 3, 1), dt.date(2026, 3, 2)),
    ]


def test_partition_roundtrip_including_empty_day(tmp_path: Path):
    paths = Paths(tmp_path)
    day = dt.date(2026, 11, 20)
    fetched_at = dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc)
    recs = [normalize_record(_raw(), fetched_at)]
    man = write_partition(paths, day, recs, {"beginTime": "2026-11-01"}, 1, 1, 1)
    assert man["rows"] == 1
    assert partition_ok(paths, day)
    table = pq.read_table(partition_file(paths, day))
    assert table.num_rows == 1
    assert table.column("info_code").to_pylist() == ["AP202611201831000001"]

    empty_day = dt.date(2026, 11, 21)
    man2 = write_partition(paths, empty_day, [], {"beginTime": "2026-11-01"}, 1, 0, 0)
    assert man2["rows"] == 0
    assert partition_ok(paths, empty_day)
    assert pq.read_table(partition_file(paths, empty_day)).num_rows == 0


def test_build_tables_computes_median_and_change(tmp_path: Path):
    paths = Paths(tmp_path)
    db_path = tmp_path / "db" / "quantdb.duckdb"
    db_path.parent.mkdir(parents=True)
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE stock_basic(ts_code VARCHAR PRIMARY KEY, symbol VARCHAR, name VARCHAR, industry VARCHAR)"
    )
    con.execute("INSERT INTO stock_basic VALUES ('000001.SZ','000001','测试股份','银行')")
    con.execute(
        "CREATE TABLE trade_cal(exchange VARCHAR, cal_date DATE, is_open DOUBLE, pretrade_date DATE)"
    )
    for d in (19, 20, 21):
        con.execute(
            "INSERT INTO trade_cal VALUES ('SSE', ?, 1, NULL)",
            [dt.date(2026, 11, d)],
        )
    con.close()

    fetched_at = dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc)
    rec1 = normalize_record(_raw(), fetched_at)
    rec2 = normalize_record(
        _raw(
            infoCode="AP202611201831000002",
            orgCode="10000002",
            orgSName="另一证券",
            predictThisYearEps="1.40",
            emRatingName="买入",
            lastEmRatingName="",
            ratingChange=2,
        ),
        fetched_at,
    )
    write_partition(paths, dt.date(2026, 11, 20), [rec1, rec2], {}, 1, 2, 2)
    result = build_tables(paths, dt.date(2026, 11, 19), dt.date(2026, 11, 21), window_days=180)
    assert result["tables"]["report_meta"] == 2
    con = duckdb.connect(str(db_path), read_only=True)
    median, n_orgs = con.execute(
        "SELECT eps_median, n_orgs FROM consensus_eps "
        "WHERE ts_code='000001.SZ' AND fiscal_year=2026 AND as_of_date='2026-11-20'"
    ).fetchone()
    assert median == pytest.approx(1.3)
    assert n_orgs == 2
    changes = con.execute(
        "SELECT direction, count(*) FROM rating_change GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert dict(changes) == {"initiate": 1, "upgrade": 1}
    assert con.execute(
        "SELECT stock_basic_name FROM rating_change LIMIT 1"
    ).fetchone()[0] == "测试股份"
    con.close()
