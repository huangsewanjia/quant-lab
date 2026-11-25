"""Load the 17 landed industry sources into DuckDB.

    python -m db.etl.industry load                 # 全部 17 个源
    python -m db.etl.industry load --sources caam_auto_output,gfex_lithium_carbonate
    python -m db.etl.industry load --full          # 清表重装（只在排查时用）

设计要点（WORK-568）：

* 只读 ``raw/industry/*/part.parquet`` 与 ``raw/industry_alt/*/data.csv``，
  不重写抓取逻辑；
* 幂等：按「源 × 期」分区先删后插，同参数重跑行数不变；``row_hash`` 为
  主键的源在装载前按内容去重（上游重复块），不丢不同内容；
* 主键为自然键的源，装前做一次唯一性校验，发现冲突直接报错而不是静默覆盖；
* 每次装载写一段 ``industry_load_audit``（raw/staged/loaded/分区数），
  可复跑核对；
* 所有日期/月份解析都保留原文列（``*_raw``），解析失败写 NULL，不推断。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

from . import common

SCHEMA_INDUSTRY_SQL = common.PROJECT_ROOT / "db" / "schema_industry.sql"


def raw_dir(kind: str) -> Path:
    """按 common.RAW_DIR 现算，便于测试替换 QUANTDB_HOME。"""
    return common.RAW_DIR / ("industry" if kind == "parquet" else "industry_alt")


def audit_dir() -> Path:
    return common.META_DIR / "industry"


_HTML_TAG = re.compile(r"<[^>]+>")
_NUM_PREFIX = re.compile(r"^[▲▼\-—\s]*([0-9][0-9,]*\.?[0-9]*)")
_MONTH = re.compile(r"^\s*(\d{4})\s*[-/年.]\s*(\d{1,2})\s*月?\s*$")
_DATE_YMD = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")
_DATE_CN = re.compile(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?\s*$")
_DATE_8 = re.compile(r"^\s*(\d{8})\s*$")
_URL_DATE = re.compile(r"(?:t|/)(\d{8})(?:_|/|\.)")


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def parse_month(value: Any) -> dt.date | None:
    """'2026-09' / '2026年9月' -> 2026-09-01; 其它 -> None."""
    text = _text(value)
    m = _MONTH.match(text)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    return dt.date(year, month, 1)


def parse_date(value: Any) -> dt.date | None:
    """'2026-11-20' / '20261120' / '2026.11.11' / '2026年11月20日' -> date."""
    text = _text(value)
    if not text:
        return None
    m = _DATE_8.match(text)
    if m:
        try:
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    for pattern in (_DATE_YMD, _DATE_CN):
        m = pattern.match(text)
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def date_from_url(url: Any) -> dt.date | None:
    """财政部文章 URL 带 tYYYYMMDD，可确定发布日期；取不到返回 None。"""
    m = _URL_DATE.search(_text(url))
    if not m:
        return None
    return parse_date(m.group(1))


def parse_number(value: Any) -> float | None:
    """'1,865.00' / '-8.43' / '▲ 1.85 %' / '▼ 0.72 %' / '— 0.00 %' -> float。

    前导符号必须保留：'-8.43' 是下跌 8.43%，吞掉负号会把涨跌方向写反（WORK-568
    复审 blocking 1）。'—'（破折号）与 '▼' 只在 prefix 里表示方向，不参与数值。
    """
    text = _text(value)
    if not text:
        return None
    m = _NUM_PREFIX.match(text)
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    prefix = text[: m.start(1)]
    return -number if "-" in prefix or "▼" in prefix else number


def strip_tags(value: Any) -> str:
    return _HTML_TAG.sub("", _text(value))


def read_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_date(manifest: dict[str, Any]) -> dt.date | None:
    """manifest.fetched_at 的日期部分；这是唯一可证的快照日期。"""
    return parse_date(_text(manifest.get("fetched_at"))[:10])


def row_hash(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    """内容哈希：所有业务列的规范文本拼接后 md5。"""
    def one(row: pd.Series) -> str:
        payload = "|".join(_text(row[c]) for c in columns)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()
    return frame.apply(one, axis=1)


# ---------------------------------------------------------------------------
# 每个源的规范化：raw DataFrame -> 目标表列
# ---------------------------------------------------------------------------

def transform_gfex(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "trade_date": raw["trade_date"].map(parse_date),
        "contract": raw["contract"].astype(str),
        "variety": raw["variety"].astype(str),
        "field": raw["field"].astype(str),
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw.apply(
            lambda r: "万元" if r["field"] == "turnover" else r["unit"], axis=1
        ),
        "published_date": raw["published_date"].map(parse_date),
        "source_url": raw["source_url"],
    })
    return out


def transform_lpr(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "quote_date": raw["quote_date"].map(parse_date),
        "tenor": raw["tenor"],
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "source_url": raw["source_url"],
    })


def transform_cnesa(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "period_raw": raw["period"],
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "caliber": raw["caliber"],
        "article_title": raw["article_title"],
        "published_date": raw["published_date"].map(parse_date),
        "source_url": raw["source_url"],
    })


def transform_cninfo(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "announcement_id": raw["announcement_id"].astype(str),
        "keyword": raw["keyword"],
        "category": raw["category"],
        "published_date": raw["published_date"].map(parse_date),
        "sec_code": raw["sec_code"],
        "sec_name": raw["sec_name"].map(strip_tags),
        "title": raw["indicator"],
        "adjunct_url": raw["adjunct_url"],
        "source_url": raw["source_url"],
    })


def transform_caam(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "period_raw": raw["period"],
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "scope": raw["scope"],
        "subject": raw["subject"],
        "published_date": raw["published_date"].map(parse_date),
        "article_title": raw["article_title"],
        "source_url": raw["source_url"],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


def transform_cpca(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "period_raw": raw["period"],
        "kind": raw["kind"],
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "published_date": raw["published_date"].map(parse_date),
        "article_title": raw["article_title"],
        "context": raw["context"],
        "source_url": raw["source_url"],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


def transform_mof(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    raw_pub = raw["published_date"]
    derived_pub = raw["source_url"].map(date_from_url)
    parsed_pub = raw_pub.map(parse_date)
    out = pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "period_raw": raw["period"],
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "published_date": [
            derived if derived is not None else parsed
            for derived, parsed in zip(derived_pub, parsed_pub)
        ],
        "published_date_raw": raw_pub,
        "article_title": raw["article_title"],
        "source_url": raw["source_url"],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


def transform_nea(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "period_raw": raw["period"],
        "indicator": raw["indicator"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "published_date": raw["published_date"].map(parse_date),
        "article_title": raw["article_title"],
        "source_url": raw["source_url"],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


def transform_pbc(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "indicator": raw["indicator"],
        "en_name": raw["en_name"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "source_url": raw["source_url"],
    })


def transform_stats(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "period": raw["period"].map(parse_month),
        "period_raw": raw["period"],
        "group_name": raw["group"],
        "indicator": raw["indicator"],
        "row_label": raw["row_label"],
        "col_header": raw["col_header"],
        "value": pd.to_numeric(raw["value"], errors="coerce"),
        "unit": raw["unit"],
        "table_index": pd.to_numeric(raw["table_index"], errors="coerce").astype("Int64"),
        "row_index": pd.to_numeric(raw["row_index"], errors="coerce").astype("Int64"),
        "col_index": pd.to_numeric(raw["col_index"], errors="coerce").astype("Int64"),
        "published_date": raw["published_date"].map(parse_date),
        "source_url": raw["source_url"],
    })


def transform_benchmark(raw: pd.DataFrame, manifest: dict) -> pd.DataFrame:
    snapshot = manifest_date(manifest)
    status = raw["status"].astype(str)
    out = pd.DataFrame({
        "snapshot_date": [snapshot] * len(raw),
        "kind": raw["kind"],
        "name": raw["name"],
        "value": raw["value"].map(parse_number),
        "change_pct": raw["change_pct"].map(parse_number),
        "target_url": [
            s if s.startswith("/") or s.startswith("http") else None for s in status
        ],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


def transform_comtrade(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "hs_code": raw["hs_code"].astype(str),
        "label": raw["label"],
        "reporter": raw["reporter"],
        "period_year": pd.to_numeric(raw["period"], errors="coerce").astype("Int64"),
        "flow": raw["flow"],
        "value_usd": pd.to_numeric(raw["value_usd"], errors="coerce"),
        "netweight_kg": pd.to_numeric(raw["netweight_kg"], errors="coerce"),
        "status": raw["status"],
    })


def transform_exchange(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "exchange": raw["exchange"],
        "contract": raw["contract"].astype(str),
        "month": raw["month"].astype(str),
        "trade_date": raw["trade_date"].map(parse_date),
        "close": pd.to_numeric(raw["close"], errors="coerce"),
        "settle": pd.to_numeric(raw["settle"], errors="coerce"),
        "volume": pd.to_numeric(raw["volume"], errors="coerce"),
        "oi": pd.to_numeric(raw["oi"], errors="coerce"),
        "status": raw["status"],
    })


def transform_owid(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "slug": raw["slug"],
        "entity": raw["entity"],
        "year": pd.to_numeric(raw["year"], errors="coerce").astype("Int64"),
        "value": pd.to_numeric(raw["value"], errors="coerce"),
    })


def transform_ppi(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    out = pd.DataFrame({
        "goods_id": raw["goods_id"].astype(str),
        "goods": raw["goods"],
        "spec": raw["spec"],
        "market": raw["market"],
        "supplier": raw["supplier"],
        "as_of": raw["as_of"].map(parse_date),
        "price": raw["price"].map(parse_number),
        "price_unit": raw["unit"],
        "price_raw": raw["price"],
        "url": raw["url"],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


def transform_sne(raw: pd.DataFrame, _: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "release_id": raw["release_id"].astype(str),
        "title": raw["title"],
        "publish_date": raw["publish_date"].map(parse_date),
        "key_figure": raw["key_figure"],
        "url": raw["url"],
        "status": raw["status"],
    })


def transform_trendforce(raw: pd.DataFrame, manifest: dict) -> pd.DataFrame:
    snapshot = manifest_date(manifest)

    def classify(text: str, spec: str) -> str:
        if _text(spec) == "项目":
            return "header"
        if "%" in text or "涨跌" in text:
            return "pct_change"
        if parse_number(text) is not None:
            return "price"
        return "unparsed"

    out = pd.DataFrame({
        "snapshot_date": [snapshot] * len(raw),
        "series": raw["series"],
        "spec": raw["spec"],
        "value_text": raw["value"],
        "value": raw["value"].map(parse_number),
        "unit": raw["unit"],
        "unit_kind": [
            classify(_text(v), _text(s)) for v, s in zip(raw["value"], raw["spec"])
        ],
    })
    out["row_hash"] = row_hash(out, list(out.columns))
    return out


class Source:
    def __init__(
        self,
        name: str,
        table: str,
        kind: str,
        partition: list[str],
        transform: Callable[[pd.DataFrame, dict], pd.DataFrame],
        pk_is_hash: bool = False,
        tier: str = "monthly",
    ) -> None:
        self.name = name
        self.table = table
        self.kind = kind
        self.partition = partition
        self.transform = transform
        self.pk_is_hash = pk_is_hash
        self.tier = tier

    @property
    def root(self) -> Path:
        return raw_dir(self.kind) / self.name

    def read(self, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
        if self.kind == "parquet":
            path = str(self.root / "part.parquet")
            return con.execute(
                "SELECT * FROM read_parquet(?)", [path]
            ).df()
        path = str(self.root / "data.csv")
        return con.execute(
            "SELECT * FROM read_csv(?, header=true, all_varchar=true)", [path]
        ).df()


SOURCES: list[Source] = [
    Source("gfex_lithium_carbonate", "industry_gfex_lithium_carbonate", "parquet",
           ["trade_date"], transform_gfex, tier="daily"),
    Source("chinamoney_lpr", "industry_chinamoney_lpr", "parquet",
           ["quote_date"], transform_lpr, tier="daily"),
    Source("cnesa_storage", "industry_cnesa_storage", "parquet",
           ["period"], transform_cnesa),
    Source("cninfo_announcements", "industry_cninfo_announcements", "parquet",
           ["published_date"], transform_cninfo),
    Source("caam_auto_output", "industry_caam_auto_output", "parquet",
           ["period"], transform_caam, pk_is_hash=True),
    Source("cpcaauto_nev", "industry_cpcaauto_nev", "parquet",
           ["period"], transform_cpca, pk_is_hash=True),
    Source("mof_fiscal", "industry_mof_fiscal", "parquet",
           ["period"], transform_mof, pk_is_hash=True),
    Source("nea_power", "industry_nea_power", "parquet",
           ["period"], transform_nea, pk_is_hash=True),
    Source("pbc_shrzgm", "industry_pbc_shrzgm", "parquet",
           ["period"], transform_pbc),
    Source("stats_gov_release", "industry_stats_gov_release", "parquet",
           ["source_url"], transform_stats),
    Source("benchmark_public_index", "industry_benchmark_public_index", "csv",
           ["snapshot_date"], transform_benchmark, pk_is_hash=True, tier="daily"),
    Source("comtrade_trade_values", "industry_comtrade_trade_values", "csv",
           ["period_year"], transform_comtrade),
    Source("exchange_daily_quotes", "industry_exchange_daily_quotes", "csv",
           ["trade_date"], transform_exchange, tier="daily"),
    Source("ow_iea_usgs_timeseries", "industry_ow_iea_usgs_timeseries", "csv",
           ["year"], transform_owid),
    Source("ppi_spot_prices", "industry_ppi_spot_prices", "csv",
           ["as_of"], transform_ppi, pk_is_hash=True, tier="daily"),
    Source("sne_public_battery", "industry_sne_public_battery", "csv",
           ["release_id"], transform_sne),
    Source("trendforce_spot_price", "industry_trendforce_spot_price", "csv",
           ["snapshot_date"], transform_trendforce, pk_is_hash=True, tier="daily"),
]

SOURCE_BY_NAME = {s.name: s for s in SOURCES}


def _primary_key(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = con.execute(
        """
        SELECT k.column_name
        FROM duckdb_constraints() c,
             LATERAL unnest(c.constraint_column_names) AS k(column_name)
        WHERE c.table_name = ? AND c.constraint_type = 'PRIMARY KEY'
        ORDER BY k.column_name
        """,
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def _null_safe_join(cols: list[str], alias_l: str, alias_r: str) -> str:
    return " AND ".join(
        f"{alias_l}.{c} IS NOT DISTINCT FROM {alias_r}.{c}" for c in cols
    )


def _dedupe_hash_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    before = len(frame)
    frame = frame.drop_duplicates(subset=["row_hash"], keep="first").reset_index(drop=True)
    return frame, before - len(frame)


def _assert_unique(frame: pd.DataFrame, keys: list[str]) -> None:
    dup = frame[frame.duplicated(subset=keys, keep=False)]
    if len(dup):
        sample = dup.head(3).to_dict("records")
        raise SystemExit(
            f"[industry] 自然主键冲突 {keys}: {len(dup)} 行重复，样例 {sample}"
        )


def load_source(
    con: duckdb.DuckDBPyConnection,
    source: Source,
    run_id: str,
    full: bool = False,
) -> dict[str, Any]:
    started = dt.datetime.now()
    manifest = read_manifest(source.root)
    raw = source.read(con)
    staged = source.transform(raw, manifest)

    dropped = 0
    if source.pk_is_hash:
        staged, dropped = _dedupe_hash_rows(staged)
    else:
        _assert_unique(staged, _primary_key(con, source.table))

    partitions = (
        staged[source.partition].drop_duplicates().shape[0] if source.partition else 0
    )

    # duckdb 对同一个 connection 上未取完的结果会报 ResourceInUse，因此每一步
    # 都显式 fetchall()；注册的 DataFrame 不在这里 unregister（下一次 register
    # 同名会替换），避免在结果未清理时做连接级操作。
    con.register("_staged", staged)
    if full:
        con.execute(f"DELETE FROM {source.table}").fetchall()
    else:
        cond = _null_safe_join(source.partition, "t", "s")
        con.execute(
            f"DELETE FROM {source.table} AS t WHERE EXISTS "
            f"(SELECT 1 FROM (SELECT DISTINCT {', '.join(source.partition)} "
            f"FROM _staged) AS s WHERE {cond})"
        ).fetchall()
    con.execute(
        f"INSERT OR REPLACE INTO {source.table} BY NAME SELECT * FROM _staged"
    ).fetchall()
    loaded = int(con.execute(f"SELECT count(*) FROM {source.table}").fetchall()[0][0])

    finished = dt.datetime.now()
    con.execute(
        "INSERT OR REPLACE INTO industry_load_audit VALUES (?,?,?,?,?,?,?,?,?,?)",
        [run_id, source.table, source.name, len(raw), len(staged), loaded,
         partitions, "full" if full else "partition", started, finished],
    )
    return {
        "source": source.name,
        "table": source.table,
        "raw_rows": len(raw),
        "staged_rows": len(staged),
        "dropped_duplicates": dropped,
        "loaded_rows": loaded,
        "partitions": partitions,
        "seconds": round((finished - started).total_seconds(), 2),
    }


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_INDUSTRY_SQL.read_text(encoding="utf-8"))


def run_load(
    names: list[str] | None = None,
    full: bool = False,
    tier: str | None = None,
) -> int:
    selected = SOURCES
    if names:
        unknown = sorted(set(names) - set(SOURCE_BY_NAME))
        if unknown:
            raise SystemExit(f"[industry] 未知源: {unknown}")
        selected = [SOURCE_BY_NAME[n] for n in names]
    if tier:
        selected = [s for s in selected if s.tier == tier]

    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    results: list[dict[str, Any]] = []
    with common.duckdb_connect() as con:
        apply_schema(con)
        for source in selected:
            result = load_source(con, source, run_id, full=full)
            results.append(result)
            print(
                f"[industry] {result['table']:<42} raw={result['raw_rows']:>6} "
                f"staged={result['staged_rows']:>6} dedup={result['dropped_duplicates']:>3} "
                f"total={result['loaded_rows']:>6} partitions={result['partitions']:>4} "
                f"{result['seconds']:>6.2f}s",
                flush=True,
            )
        con.execute("CHECKPOINT")

    audit = audit_dir()
    audit.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "generated_at": common.utc_now(),
        "tier": tier,
        "sources": results,
        "raw_rows_total": sum(r["raw_rows"] for r in results),
        "staged_rows_total": sum(r["staged_rows"] for r in results),
        "loaded_rows_total": sum(r["loaded_rows"] for r in results),
    }
    out = audit / f"load-{run_id}.json"
    common.write_json_atomic(out, report)
    print(f"[industry] 报表: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.etl.industry", description="行业层规范化装载"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load", help="把 raw 装进 industry_* 表")
    load.add_argument("--sources", help="逗号分隔的源名，默认全部")
    load.add_argument("--tier", choices=["daily", "monthly"], help="只装该调度档的源")
    load.add_argument("--full", action="store_true", help="清表重装（排查用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "load":
        names = [s.strip() for s in (args.sources or "").split(",") if s.strip()] or None
        return run_load(names=names, full=args.full, tier=args.tier)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
