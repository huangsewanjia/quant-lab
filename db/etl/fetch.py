"""Incremental Tushare -> Parquet -> DuckDB ETL.

Examples:
  python -m db.etl.fetch init-db
  python -m db.etl.fetch calendar --start 20200101 --end 20201231
  python -m db.etl.fetch daily --start 20200101 --end 20201231
  python -m db.etl.fetch financials --codes 600000.SH --datasets income,fina_indicator
  python -m db.etl.fetch load --datasets all

Every upstream call is rate-limited (default 9/min) and logged to
logs/fetch-YYYYMMDD.jsonl. A partition is skipped only when its manifest and
sha256 both verify.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from . import common


class FetchError(RuntimeError):
    pass


class TushareFetcher:
    def __init__(
        self,
        *,
        rpm: float = 9.0,
        retries: int = 2,
        concurrency: int = 1,
    ) -> None:
        if concurrency < 1 or concurrency > 19:
            raise ValueError("--concurrency 必须在 1..19")
        self.token = common.read_token(required=True)
        self.rpm = rpm
        self.retries = retries
        self.concurrency = concurrency
        self.limiter = common.RateLimiter(rpm)
        self.log = common.CallLogger()
        self._local = threading.local()

    @property
    def pro(self):
        import tushare as ts

        if not getattr(self._local, "pro", None):
            self._local.pro = ts.pro_api(self.token)
        return self._local.pro

    def call(
        self,
        method: str,
        dataset: str,
        *,
        trade_date: str | None = None,
        ts_code: str | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 2):
            self.limiter.wait()
            started = time.monotonic()
            try:
                fn = getattr(self.pro, method)
                df = fn(**kwargs)
                if df is None:
                    df = pd.DataFrame()
                elapsed = int((time.monotonic() - started) * 1000)
                self.log.log(
                    dataset,
                    trade_date=trade_date,
                    ts_code=ts_code,
                    duration_ms=elapsed,
                    rows=len(df),
                    status="ok",
                    attempt=attempt,
                )
                return df
            except Exception as exc:  # noqa: BLE001 - Tushare raises many types
                last_error = exc
                elapsed = int((time.monotonic() - started) * 1000)
                self.log.log(
                    dataset,
                    trade_date=trade_date,
                    ts_code=ts_code,
                    duration_ms=elapsed,
                    rows=None,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    attempt=attempt,
                )
                if attempt <= self.retries:
                    time.sleep(2 ** (attempt - 1))
        raise FetchError(f"{dataset} {trade_date or ts_code or ''} 失败: {last_error}")


def parse_date(value: str) -> str:
    text = str(value).replace("-", "").strip()
    if len(text) != 8 or not text.isdigit():
        raise argparse.ArgumentTypeError(f"日期需为 YYYYMMDD: {value}")
    return text


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def read_codes(value: str | None, file_path: str | None) -> list[str]:
    codes = split_csv(value)
    if file_path:
        text = Path(file_path).read_text(encoding="utf-8")
        codes.extend([line.strip() for line in text.splitlines() if line.strip()])
    return [common.normalize_ts_code(code) for code in codes]


def open_dates(fetcher: TushareFetcher | None, start: str, end: str) -> list[str]:
    """Use local trade_cal; fall back to daily partitions; only then call Tushare."""
    cal_file = common.partition_dir("trade_cal", "calendar") / "data.parquet"
    if cal_file.exists():
        df = pd.read_parquet(cal_file)
        dates = df[(df["is_open"] == 1)].copy()
        dates["trade_date"] = pd.to_datetime(dates["cal_date"]).dt.strftime("%Y%m%d")
        return sorted(dates.loc[(dates["trade_date"] >= start) & (dates["trade_date"] <= end), "trade_date"].tolist())

    daily_parts = common.list_partitions("daily")
    if daily_parts:
        return [d for d in daily_parts if start <= d <= end]

    if fetcher is None:
        raise FetchError("没有本地 trade_cal/daily 分区，且未提供 Tushare token")
    ensure_calendar(fetcher, start, end)
    return open_dates(None, start, end)


def ensure_calendar(fetcher: TushareFetcher, start: str, end: str) -> dict[str, Any]:
    df = fetcher.call(
        "trade_cal",
        "trade_cal",
        exchange="SSE",
        start_date=start,
        end_date=end,
    )
    if df.empty:
        raise FetchError(f"trade_cal 返回空: {start}..{end}")
    with common.duckdb_connect(read_only=True) as con:
        work = common.conform_frame(df, "trade_cal", con, "tushare")
    return common.write_partition(work, "trade_cal", "calendar", "tushare")


def fetch_stock_basic(fetcher: TushareFetcher) -> dict[str, Any]:
    frames = []
    for status in ("L", "D", "P"):
        df = fetcher.call("stock_basic", "stock_basic", list_status=status)
        if not df.empty:
            frames.append(df)
    if not frames:
        raise FetchError("stock_basic 返回空")
    raw = pd.concat(frames, ignore_index=True)
    with common.duckdb_connect(read_only=True) as con:
        work = common.conform_frame(raw, "stock_basic", con, "tushare")
    return common.write_partition(work, "stock_basic", "latest", "tushare")


def fetch_by_trade_date(
    fetcher: TushareFetcher,
    dataset: str,
    method: str,
    dates: Iterable[str],
    *,
    kwargs: dict[str, Any] | None = None,
    concurrency: int = 1,
) -> tuple[int, list[dict[str, Any]]]:
    todo = [d for d in dates if not common.manifest_ok(dataset, d)]
    failures: list[dict[str, Any]] = []
    done = 0

    with common.duckdb_connect() as con:
        common.apply_schema(con)

    def one(trade_date: str) -> dict[str, Any]:
        extra = dict(kwargs or {})
        if dataset == "suspend_d":
            call_kwargs = {"suspend_date": trade_date, **extra}
        else:
            call_kwargs = dict(extra)
        df = fetcher.call(
            method,
            dataset,
            trade_date=trade_date,
            **call_kwargs,
        )
        if df.empty:
            raise FetchError(f"{dataset} {trade_date} 返回空")
        with common.duckdb_connect(read_only=True) as con:
            work = common.conform_frame(df, dataset, con, "tushare")
        return common.write_partition(work, dataset, trade_date, "tushare")

    if concurrency <= 1:
        for trade_date in todo:
            try:
                one(trade_date)
                done += 1
                print(f"[ok] {dataset} {trade_date}")
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    {"dataset": dataset, "partition": trade_date, "error": str(exc)}
                )
                print(f"[fail] {dataset} {trade_date}: {exc}", file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(one, d): d for d in todo}
            for future in as_completed(futures):
                trade_date = futures[future]
                try:
                    future.result()
                    done += 1
                    print(f"[ok] {dataset} {trade_date}")
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        {"dataset": dataset, "partition": trade_date, "error": str(exc)}
                    )
                    print(f"[fail] {dataset} {trade_date}: {exc}", file=sys.stderr)
    common.append_failures(failures)
    return done, failures


def fetch_index_daily(
    fetcher: TushareFetcher,
    codes: list[str],
    start: str,
    end: str,
    concurrency: int,
) -> tuple[int, list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    done = 0
    for code in codes:
        try:
            df = fetcher.call(
                "index_daily",
                "index_daily",
                ts_code=code,
                start_date=start,
                end_date=end,
            )
            if df.empty:
                raise FetchError(f"index_daily {code} 返回空")
            with common.duckdb_connect(read_only=True) as con:
                work = common.conform_frame(df, "index_daily", con, "tushare")
            for trade_date, group in work.groupby("trade_date", sort=True):
                key = pd.Timestamp(trade_date).strftime("%Y%m%d")
                # Each date partition holds all requested index codes.
                target = common.partition_dir("index_daily", key) / "data.parquet"
                if target.exists():
                    old = pd.read_parquet(target)
                    old = old[old["ts_code"] != code]
                    group = pd.concat([old, group], ignore_index=True)
                common.write_partition(group, "index_daily", key, "tushare")
            done += 1
            print(f"[ok] index_daily {code}")
        except Exception as exc:  # noqa: BLE001
            failures.append({"dataset": "index_daily", "partition": code, "error": str(exc)})
            print(f"[fail] index_daily {code}: {exc}", file=sys.stderr)
    common.append_failures(failures)
    return done, failures


def fetch_index_weight(
    fetcher: TushareFetcher, index_code: str, start: str, end: str
) -> dict[str, Any]:
    df = fetcher.call(
        "index_weight",
        "index_weight",
        index_code=index_code,
        start_date=start,
        end_date=end,
    )
    if df.empty:
        raise FetchError(f"index_weight {index_code} 返回空")
    with common.duckdb_connect(read_only=True) as con:
        work = common.conform_frame(df, "index_weight", con, "tushare")
    return common.write_partition(work, "index_weight", index_code, "tushare")


FIN_DATASETS = {
    "income": "income",
    "balancesheet": "balancesheet",
    "cashflow": "cashflow",
    "fina_indicator": "fina_indicator",
    "dividend": "dividend",
}


def fetch_financials(
    fetcher: TushareFetcher,
    codes: list[str],
    datasets: list[str],
    start: str | None,
    end: str | None,
    concurrency: int,
) -> tuple[int, list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    done = 0
    unknown = [d for d in datasets if d not in FIN_DATASETS]
    if unknown:
        raise FetchError(f"未知财务数据集: {unknown}")

    def one(code: str, dataset: str) -> dict[str, Any]:
        method = FIN_DATASETS[dataset]
        kwargs: dict[str, Any] = {"ts_code": code}
        if start:
            kwargs["start_date"] = start
        if end:
            kwargs["end_date"] = end
        df = fetcher.call(method, dataset, ts_code=code, **kwargs)
        if df.empty:
            raise FetchError(f"{dataset} {code} 返回空")
        with common.duckdb_connect(read_only=True) as con:
            work = common.conform_frame(df, dataset, con, "tushare")
        return common.write_partition(work, dataset, code, "tushare")

    jobs = [(code, dataset) for code in codes for dataset in datasets]
    if concurrency <= 1:
        for code, dataset in jobs:
            try:
                one(code, dataset)
                done += 1
                print(f"[ok] {dataset} {code}")
            except Exception as exc:  # noqa: BLE001
                failures.append({"dataset": dataset, "partition": code, "error": str(exc)})
                print(f"[fail] {dataset} {code}: {exc}", file=sys.stderr)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(one, code, dataset): (code, dataset) for code, dataset in jobs}
            for future in as_completed(futures):
                code, dataset = futures[future]
                try:
                    future.result()
                    done += 1
                    print(f"[ok] {dataset} {code}")
                except Exception as exc:  # noqa: BLE001
                    failures.append({"dataset": dataset, "partition": code, "error": str(exc)})
                    print(f"[fail] {dataset} {code}: {exc}", file=sys.stderr)
    common.append_failures(failures)
    return done, failures


def load_duckdb(datasets: list[str], full_refresh: bool) -> int:
    if "all" in datasets:
        datasets = [name for name in common.TABLE_PK]
    loaded = 0
    with common.duckdb_connect() as con:
        common.apply_schema(con)
        for dataset in datasets:
            if dataset not in common.TABLE_PK:
                print(f"[skip] 未知数据集 {dataset}", file=sys.stderr)
                continue
            parts = common.list_partitions(dataset)
            if not parts:
                print(f"[skip] {dataset}: 无分区")
                continue
            rows = common.load_partitions(con, dataset, parts, full_refresh=full_refresh)
            loaded += rows
            print(f"[ok] {dataset}: {len(parts)} 分区 / {rows} 行")
        con.execute("CHECKPOINT")
    return loaded


def cmd_status(_: argparse.Namespace) -> int:
    print(f"QUANTDB_HOME={common.QDB_HOME}")
    for dataset in common.TABLE_PK:
        parts = common.list_partitions(dataset)
        if parts:
            print(f"{dataset:16s} partitions={len(parts):5d}")
    if common.DB_PATH.exists():
        print(f"duckdb={common.DB_PATH} ({common.DB_PATH.stat().st_size} bytes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.etl.fetch",
        description="quantdb ETL：Tushare -> Parquet(L0) -> DuckDB(L1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--rpm", type=float, default=9.0, help="全局请求速率，默认 9 次/分钟")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数")
    parser.add_argument("--concurrency", type=int, default=1, help="并发数，上限 19")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="执行 schema.sql 建库")
    sub.add_parser("status", help="查看分区与 DuckDB 状态")
    sub.add_parser("stock-basic", help="拉取 L/D/P 三种状态的股票基础信息")

    p = sub.add_parser("calendar", help="拉取交易日历")
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, required=True)

    for name, help_text in [
        ("daily", "按 trade_date 拉全市场日线"),
        ("adj-factor", "按 trade_date 拉复权因子"),
        ("daily-basic", "按 trade_date 拉每日指标"),
        ("stk-limit", "按 trade_date 拉涨跌停价"),
        ("suspend", "按 trade_date 拉停牌信息"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--start", type=parse_date, required=True)
        p.add_argument("--end", type=parse_date, required=True)

    p = sub.add_parser("index-daily", help="按指数代码拉日线")
    p.add_argument("--codes", required=True, help="逗号分隔，如 000300.SH,399006.SZ")
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, required=True)

    p = sub.add_parser("index-weight", help="拉指数成分权重")
    p.add_argument("--index-code", required=True)
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, required=True)

    p = sub.add_parser("financials", help="按 ts_code 拉财务数据")
    p.add_argument("--codes", help="逗号分隔代码")
    p.add_argument("--codes-file", help="每行一个代码的文件")
    p.add_argument(
        "--datasets",
        default="income,balancesheet,cashflow,fina_indicator,dividend",
        help="逗号分隔",
    )
    p.add_argument("--start", type=parse_date)
    p.add_argument("--end", type=parse_date)

    p = sub.add_parser("load", help="把 Parquet 分区装入 DuckDB")
    p.add_argument("--datasets", default="all", help="逗号分隔或 all")
    p.add_argument("--full-refresh", action="store_true", help="先清空表再装载")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.concurrency > 19:
        parser.error("--concurrency 上限为 19")
    common.ensure_layout()

    if args.command == "init-db":
        with common.duckdb_connect() as con:
            common.apply_schema(con)
            con.execute("CHECKPOINT")
        print(f"[ok] schema applied: {common.DB_PATH}")
        return 0
    if args.command == "status":
        return cmd_status(args)
    if args.command == "load":
        load_duckdb(split_csv(args.datasets), args.full_refresh)
        return 0

    common.ensure_db()
    fetcher = TushareFetcher(
        rpm=args.rpm, retries=args.retries, concurrency=args.concurrency
    )

    if args.command == "stock-basic":
        meta = fetch_stock_basic(fetcher)
        print(f"[ok] stock_basic rows={meta['rows']} sha256={meta['sha256'][:12]}")
        return 0
    if args.command == "calendar":
        meta = ensure_calendar(fetcher, args.start, args.end)
        print(f"[ok] trade_cal rows={meta['rows']} sha256={meta['sha256'][:12]}")
        return 0

    if args.command in {"daily", "adj-factor", "daily-basic", "stk-limit", "suspend"}:
        mapping = {
            "daily": ("daily", "daily"),
            "adj-factor": ("adj_factor", "adj_factor"),
            "daily-basic": ("daily_basic", "daily_basic"),
            "stk-limit": ("stk_limit", "stk_limit"),
            "suspend": ("suspend_d", "suspend_d"),
        }
        dataset, method = mapping[args.command]
        dates = open_dates(fetcher, args.start, args.end)
        done, failures = fetch_by_trade_date(
            fetcher, dataset, method, dates, concurrency=args.concurrency
        )
        print(f"[done] {dataset}: {done} partitions, {len(failures)} failures")
        return 1 if failures else 0

    if args.command == "index-daily":
        done, failures = fetch_index_daily(
            fetcher,
            read_codes(args.codes, None),
            args.start,
            args.end,
            args.concurrency,
        )
        print(f"[done] index_daily: {done} codes, {len(failures)} failures")
        return 1 if failures else 0

    if args.command == "index-weight":
        meta = fetch_index_weight(fetcher, args.index_code, args.start, args.end)
        print(f"[ok] index_weight {args.index_code} rows={meta['rows']}")
        return 0

    if args.command == "financials":
        codes = read_codes(args.codes, args.codes_file)
        if not codes:
            parser.error("financials 需要 --codes 或 --codes-file")
        done, failures = fetch_financials(
            fetcher,
            codes,
            split_csv(args.datasets),
            args.start,
            args.end,
            args.concurrency,
        )
        print(f"[done] financials: {done} partitions, {len(failures)} failures")
        return 1 if failures else 0

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
