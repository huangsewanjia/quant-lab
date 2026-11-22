"""Schema contract: the DuckDB layer carries the 13+ P0 tables with PIT columns."""

from __future__ import annotations

from db.etl import common


P0_TABLES = {
    "trade_cal",
    "stock_basic",
    "daily",
    "daily_basic",
    "adj_factor",
    "stk_limit",
    "suspend_d",
    "index_daily",
    "index_weight",
    "index_classify",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "dividend",
    "namechange",
}


def test_schema_is_idempotent_and_complete(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        qdb.apply_schema(con)
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
            ).fetchall()
        }
        assert P0_TABLES.issubset(tables)


def test_financial_tables_keep_pit_columns(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        for table in ("income", "balancesheet", "cashflow", "fina_indicator"):
            cols = {
                row[0]
                for row in con.execute(
                    "SELECT column_name FROM duckdb_columns() WHERE table_name = ?",
                    [table],
                ).fetchall()
            }
            assert "ann_date" in cols
        # fina_indicator does not carry f_ann_date upstream, the other three do.
        for table in ("income", "balancesheet", "cashflow"):
            cols = {
                row[0]
                for row in con.execute(
                    "SELECT column_name FROM duckdb_columns() WHERE table_name = ?",
                    [table],
                ).fetchall()
            }
            assert "f_ann_date" in cols


def test_market_tables_are_keyed_by_code_and_date(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        for table in ("daily", "daily_basic", "adj_factor", "index_daily"):
            keys = [
                (row[0], row[1])
                for row in con.execute(
                    "SELECT constraint_column_names, constraint_type FROM duckdb_constraints() "
                    "WHERE table_name = ?",
                    [table],
                ).fetchall()
            ]
            primary = [cols for cols, kind in keys if kind == "PRIMARY KEY"]
            assert primary, f"{table} has no primary key"
            assert set(primary[0]) == {"ts_code", "trade_date"}


def test_unit_comments_are_present(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        comments = {
            row[0]: (row[1] or "")
            for row in con.execute(
                "SELECT column_name, comment FROM duckdb_columns() WHERE table_name = 'daily'"
            ).fetchall()
        }
        assert "手" in comments["vol"]
        assert "千元" in comments["amount"]


def test_adjusted_price_views_exist_and_do_not_materialise(qdb):
    with qdb.duckdb_connect() as con:
        qdb.apply_schema(con)
        views = {
            row[0]
            for row in con.execute(
                "SELECT view_name FROM duckdb_views() WHERE schema_name = 'main'"
            ).fetchall()
        }
        assert {"v_daily_qfq", "v_daily_hfq"} <= views
        # the views must be computed, not stored: no table holds the adjusted price
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
            ).fetchall()
        }
        assert not any("qfq" in name or "hfq" in name for name in tables)
