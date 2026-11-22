from __future__ import annotations

import datetime as dt

import pandas as pd

from db.etl import common
from db.ql import cli


def _seed(qdb, n=250):
    rows = []
    base = dt.date(2020, 1, 1)
    for i in range(n):
        rows.append(
            {
                "ts_code": "600000.SH",
                "trade_date": base + dt.timedelta(days=i),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 1000.0,
                "amount": 10500.0,
            }
        )
    common.write_partition(pd.DataFrame(rows), "daily", "20200101", "test")
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        common.load_partitions(con, "daily", ["20200101"])
        con.execute("CHECKPOINT")


def test_default_output_is_truncated(qdb, capsys):
    _seed(qdb)
    code = cli.main(
        [
            "--db",
            str(common.DB_PATH),
            "--limit",
            "10",
            "--format",
            "csv",
            "sql",
            "SELECT * FROM daily ORDER BY trade_date",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert len([line for line in captured.out.splitlines() if line.strip()]) == 11
    assert "截断" in captured.err


def test_out_writes_all_rows(qdb, tmp_path, capsys):
    _seed(qdb, n=25)
    target = tmp_path / "all.csv"
    code = cli.main(
        [
            "--db",
            str(common.DB_PATH),
            "--out",
            str(target),
            "sql",
            "SELECT * FROM daily ORDER BY trade_date",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert target.exists()
    assert len(target.read_text(encoding="utf-8").splitlines()) == 26
    assert "已写出 25 行" in captured.out


def test_daily_subcommand_filters_by_code_and_date(qdb, capsys):
    _seed(qdb, n=25)
    code = cli.main(
        [
            "--db",
            str(common.DB_PATH),
            "--format",
            "csv",
            "daily",
            "--code",
            "600000",
            "--start",
            "20200101",
            "--end",
            "20200110",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    body = [line for line in captured.out.splitlines() if line.strip()]
    assert body[0].startswith("ts_code")
    # 2020-01-01 .. 2020-01-10 inclusive -> 10 rows
    assert len(body) == 11
