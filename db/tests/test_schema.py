from __future__ import annotations

from db.etl import common


P0_TABLES = {
    "trade_cal",
    "stock_basic",
    "daily",
    "adj_factor",
    "daily_basic",
    "stk_limit",
    "suspend_d",
    "index_daily",
    "index_weight",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "dividend",
    "namechange",
}


def test_schema_is_idempotent_and_complete(qdb):
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        common.apply_schema(con)
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
            ).fetchall()
        }
        assert P0_TABLES.issubset(tables)


def test_financial_tables_keep_pit_columns(qdb):
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        for table in ("income", "balancesheet", "cashflow", "fina_indicator"):
            cols = {
                row[0]
                for row in con.execute(
                    "SELECT column_name FROM duckdb_columns() WHERE table_name = ?",
                    [table],
                ).fetchall()
            }
            assert {"ann_date", "f_ann_date"} <= cols


def test_unit_comments_are_present(qdb):
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        comments = {
            row[0]: (row[1] or "")
            for row in con.execute(
                "SELECT column_name, comment FROM duckdb_columns() WHERE table_name = 'daily'"
            ).fetchall()
        }
        assert "手" in comments["vol"]
        assert "千元" in comments["amount"]
