"""ql: read-only DuckDB queries with a 200-row default guard."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

from ..etl import common

DEFAULT_LIMIT = 200
FORMATS = ("table", "json", "csv")


def _connect(db_path: Path):
    if not db_path.exists():
        raise SystemExit(
            f"数据库不存在: {db_path}\n先运行: python -m db.etl.fetch init-db"
        )
    import duckdb

    return duckdb.connect(
        str(db_path),
        read_only=True,
        config={
            "temp_directory": str(common.TMP_DIR),
            "memory_limit": "2GB",
        },
    )


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        return f"{value:.6g}"
    text = str(value)
    return text.replace("\n", " ")


def format_table(df: pd.DataFrame, max_width: int = 48) -> str:
    headers = [str(col) for col in df.columns]
    rows = [[_cell(value) for value in row] for row in df.itertuples(index=False, name=None)]
    widths = []
    for idx, header in enumerate(headers):
        cells = [row[idx] for row in rows] if rows else []
        width = max([len(header)] + [len(cell) for cell in cells]) if cells else len(header)
        widths.append(min(width, max_width))
    sep = "+".join("-" * (width + 2) for width in widths)
    out = [sep]
    out.append("| " + " | ".join(header.ljust(widths[i])[: widths[i]] for i, header in enumerate(headers)) + " |")
    out.append(sep)
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            if len(cell) > widths[idx]:
                cell = cell[: max(0, widths[idx] - 3)] + "..."
            cells.append(cell.ljust(widths[idx]))
        out.append("| " + " | ".join(cells) + " |")
    out.append(sep)
    return "\n".join(out)


def _emit(df: pd.DataFrame, fmt: str, out: Path | None, overflow: bool, limit: int) -> None:
    display = df.copy()
    float_cols = list(display.select_dtypes(include="float").columns)
    if float_cols:
        display[float_cols] = display[float_cols].round(8)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".json":
            out.write_text(
                display.to_json(
                    orient="records", date_format="iso", force_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        else:
            display.to_csv(out, index=False, encoding="utf-8")
        print(f"已写出 {len(display)} 行 -> {out}")
        return
    if fmt == "json":
        print(
            display.to_json(
                orient="records", date_format="iso", force_ascii=False, indent=2
            )
        )
    elif fmt == "csv":
        sys.stdout.write(display.to_csv(index=False))
    else:
        print(format_table(display))
    if overflow:
        print(
            f"[截断] 结果超过 {limit} 行，仅显示前 {limit} 行；用 --limit N 或 --out FILE 获取全部。",
            file=sys.stderr,
        )


def run_sql(con, sql: str, *, limit: int | None, fmt: str, out: Path | None) -> None:
    query = sql.strip().rstrip(";").lstrip("; \n\t")
    if not query:
        raise SystemExit("SQL 为空")
    overflow = False
    result = con.execute(query)
    if limit is not None and out is None:
        columns = [str(item[0]) for item in (result.description or [])]
        rows = result.fetchmany(limit + 1)
        df = pd.DataFrame(rows, columns=columns)
        if len(df) > limit:
            overflow = True
            df = df.head(limit)
    else:
        df = result.fetchdf()
    _emit(df, fmt, out, overflow, int(limit or 0))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ql",
        description="quantdb 只读查询 CLI（默认最多 200 行）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default=str(common.DB_PATH), help="DuckDB 文件")
    parser.add_argument("--format", choices=FORMATS, default="table", help="输出格式")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="表格输出最多行数")
    parser.add_argument("--out", help="把完整结果写入文件（csv 或 json）")
    sub = parser.add_subparsers(dest="command", required=True)

    def output_opts(p):
        # Allow `ql daily ... --limit 500` as well as `ql --limit 500 daily ...`.
        p.add_argument("--format", dest="sub_format", choices=FORMATS)
        p.add_argument("--limit", dest="sub_limit", type=int)
        p.add_argument("--out", dest="sub_out")

    p = sub.add_parser("sql", help="执行任意只读 SQL")
    p.add_argument("sql", help="SQL 语句")
    output_opts(p)

    p = sub.add_parser("daily", help="查询单只股票日线")
    p.add_argument("--code", required=True, help="600000.SH / sh600000 / 600000")
    p.add_argument("--start", help="YYYYMMDD")
    p.add_argument("--end", help="YYYYMMDD")
    output_opts(p)

    p = sub.add_parser("fin", help="查询单只股票财务数据")
    p.add_argument("--code", required=True)
    p.add_argument("--period", help="报告期，YYYYMMDD 或 YYYY")
    p.add_argument(
        "--table",
        choices=("fina_indicator", "income", "balancesheet", "cashflow"),
        default="fina_indicator",
    )
    p.add_argument("--ann-before", help="PIT 过滤：ann_date <= YYYYMMDD")
    output_opts(p)
    return parser


def _date_arg(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("-", "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if len(text) == 4 and text.isdigit():
        return text
    raise SystemExit(f"日期需为 YYYYMMDD 或 YYYY: {value}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_value = getattr(args, "sub_out", None) or args.out
    fmt = getattr(args, "sub_format", None) or args.format
    raw_limit = getattr(args, "sub_limit", None)
    if raw_limit is None:
        raw_limit = args.limit
    if raw_limit is not None and raw_limit <= 0:
        raise SystemExit("--limit 必须是正整数；要导出全部结果请用 --out")
    out = Path(out_value).expanduser() if out_value else None
    limit = raw_limit if raw_limit and raw_limit > 0 else None
    con = _connect(Path(args.db).expanduser())
    try:
        if args.command == "sql":
            run_sql(con, args.sql, limit=limit, fmt=fmt, out=out)
        elif args.command == "daily":
            code = common.normalize_ts_code(args.code)
            clauses = ["ts_code = ?"]
            params: list[str] = [code]
            start = _date_arg(args.start)
            end = _date_arg(args.end)
            if start:
                clauses.append("trade_date >= ?")
                params.append(start)
            if end:
                clauses.append("trade_date <= ?")
                params.append(end)
            query = f"SELECT * FROM daily WHERE {' AND '.join(clauses)} ORDER BY trade_date"
            run_sql(con, _param_sql(query, params), limit=limit, fmt=fmt, out=out)
        elif args.command == "fin":
            code = common.normalize_ts_code(args.code)
            clauses = ["ts_code = ?"]
            params = [code]
            if args.period:
                clauses.append("CAST(end_date AS VARCHAR) LIKE ?")
                params.append(f"{args.period}%")
            if args.ann_before:
                clauses.append("ann_date <= ?")
                params.append(_date_arg(args.ann_before))
            query = (
                f"SELECT * FROM {args.table} WHERE {' AND '.join(clauses)} "
                "ORDER BY end_date, ann_date"
            )
            run_sql(con, _param_sql(query, params), limit=limit, fmt=fmt, out=out)
    finally:
        con.close()
    return 0


def _param_sql(query: str, params: list[str]) -> str:
    """Inline parameters only for the local read-only paths.

    DuckDB's Python API accepts ? placeholders, but re-using the row-limit wrapper
    would need a second parameter set. Values here are normalized codes/dates, so
    a strict quote helper keeps the SQL explicit and auditable.
    """
    out = query
    for value in params:
        escaped = str(value).replace("'", "''")
        out = out.replace("?", f"'{escaped}'", 1)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
