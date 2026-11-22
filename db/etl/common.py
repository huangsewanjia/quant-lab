"""Shared paths, manifests, rate limiting and DuckDB helpers.

Hard constraints from WORK-513:
  * data lives under /srv/share/quantdb (HDD), never under /;
  * DuckDB spill goes to staging/tmp;
  * tokens are read from env or the 0600 file and never logged.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

QDB_HOME = Path(os.environ.get("QUANTDB_HOME", "/srv/share/quantdb")).resolve()
RAW_DIR = QDB_HOME / "raw"
META_DIR = QDB_HOME / "meta"
DB_DIR = QDB_HOME / "db"
POLICY_DIR = QDB_HOME / "policy"
STAGING_DIR = QDB_HOME / "staging"
LOGS_DIR = QDB_HOME / "logs"
DB_PATH = DB_DIR / "quantdb.duckdb"
TMP_DIR = STAGING_DIR / "tmp"

TOKEN_ENV = "TUSHARE_TOKEN"
TOKEN_FILE = Path(
    os.environ.get("QUANTDB_TUSHARE_TOKEN_FILE", "/home/moyongkai/.config/quantdb/tushare.token")
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = PROJECT_ROOT / "db" / "schema.sql"

LAYOUT_DIRS = [RAW_DIR, DB_DIR, META_DIR, POLICY_DIR, STAGING_DIR, LOGS_DIR, TMP_DIR]

# Datasets partitioned by trade_date=YYYYMMDD.
TRADE_DATE_DATASETS = {
    "daily",
    "adj_factor",
    "daily_basic",
    "stk_limit",
    "suspend_d",
    "index_daily",
}

# Natural keys, kept in sync with schema.sql. Used for idempotent dedupe.
TABLE_PK: dict[str, tuple[str, ...]] = {
    "trade_cal": ("exchange", "cal_date"),
    "stock_basic": ("ts_code",),
    "daily": ("ts_code", "trade_date"),
    "adj_factor": ("ts_code", "trade_date"),
    "daily_basic": ("ts_code", "trade_date"),
    "stk_limit": ("trade_date", "ts_code"),
    "suspend_d": ("ts_code", "trade_date"),
    "index_daily": ("ts_code", "trade_date"),
    "index_weight": ("index_code", "con_code", "trade_date"),
    "income": ("ts_code", "end_date", "report_type", "ann_date"),
    "balancesheet": ("ts_code", "end_date", "report_type", "ann_date"),
    "cashflow": ("ts_code", "end_date", "report_type", "ann_date"),
    "fina_indicator": ("ts_code", "end_date", "ann_date"),
    "dividend": ("ts_code", "end_date", "div_proc", "ann_date"),
    "namechange": ("ts_code", "name", "start_date"),
}

DATE_COLUMNS = {
    "trade_cal": {"cal_date", "pretrade_date"},
    "stock_basic": {"list_date", "delist_date"},
    "daily": {"trade_date"},
    "adj_factor": {"trade_date"},
    "daily_basic": {"trade_date"},
    "stk_limit": {"trade_date"},
    "suspend_d": {"trade_date"},
    "index_daily": {"trade_date"},
    "index_weight": {"trade_date"},
    "income": {"ann_date", "f_ann_date", "end_date"},
    "balancesheet": {"ann_date", "f_ann_date", "end_date"},
    "cashflow": {"ann_date", "f_ann_date", "end_date"},
    "fina_indicator": {"ann_date", "f_ann_date", "end_date"},
    "dividend": {
        "ann_date",
        "end_date",
        "record_date",
        "ex_date",
        "pay_date",
        "div_listdate",
        "imp_ann_date",
        "base_date",
    },
    "namechange": {"start_date", "end_date", "ann_date"},
}

DATASETS_WITH_RAW_JSON = {
    "stock_basic",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "dividend",
    "namechange",
}


def ensure_layout() -> None:
    for path in LAYOUT_DIRS:
        path.mkdir(parents=True, exist_ok=True)


def today_tag() -> str:
    return dt.datetime.now().strftime("%Y%m%d")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_key(value: str) -> str:
    return str(value).replace("/", "_").replace(" ", "_")


def partition_dir(dataset: str, partition: str) -> Path:
    if dataset in TRADE_DATE_DATASETS:
        return RAW_DIR / dataset / f"trade_date={safe_key(partition)}"
    return RAW_DIR / dataset / f"partition={safe_key(partition)}"


def manifest_path(dataset: str, partition: str) -> Path:
    return META_DIR / dataset / f"{safe_key(partition)}.json"


def failure_path(tag: str | None = None) -> Path:
    return META_DIR / f"failures-{tag or today_tag()}.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def read_manifest(dataset: str, partition: str) -> dict[str, Any] | None:
    path = manifest_path(dataset, partition)
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def manifest_ok(dataset: str, partition: str, verify_hash: bool = True) -> bool:
    """A partition is complete only when manifest + parquet both exist and hash matches."""
    meta = read_manifest(dataset, partition)
    if not meta:
        return False
    if meta.get("dataset") != dataset or meta.get("partition") != safe_key(partition):
        return False
    file_path = QDB_HOME / str(meta.get("file", ""))
    if not meta.get("file") or not file_path.exists():
        return False
    if file_path.stat().st_size != int(meta.get("bytes", -1)):
        return False
    if verify_hash and sha256_file(file_path) != meta.get("sha256"):
        return False
    try:
        import pyarrow.parquet as pq

        actual_rows = int(pq.ParquetFile(file_path).metadata.num_rows)
    except Exception:  # noqa: BLE001
        return False
    if actual_rows != int(meta.get("rows", -1)):
        return False
    return True


def list_partitions(dataset: str) -> list[str]:
    base = META_DIR / dataset
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.json"))


def write_partition(
    df: pd.DataFrame,
    dataset: str,
    partition: str,
    source: str,
    pk: Iterable[str] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one immutable-by-convention partition atomically and record its sha256."""
    ensure_layout()
    key = tuple(pk or TABLE_PK.get(dataset, ()))
    work = df.copy()
    if key and all(col in work.columns for col in key):
        work = work.drop_duplicates(subset=list(key), keep="last")
        work = work.sort_values(list(key)).reset_index(drop=True)
    target_dir = partition_dir(dataset, partition)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "data.parquet"
    tmp = target_dir / f".data.parquet.tmp.{os.getpid()}"
    work.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, target)
    digest = sha256_file(target)
    meta = {
        "dataset": dataset,
        "partition": safe_key(partition),
        "rows": int(len(work)),
        "bytes": int(target.stat().st_size),
        "sha256": digest,
        "file": str(target.relative_to(QDB_HOME)),
        "source": source,
        "fetched_at": utc_now(),
        "columns": list(work.columns),
        "primary_key": list(key),
    }
    if extra_meta:
        meta.update(extra_meta)
    write_json_atomic(manifest_path(dataset, partition), meta)
    return meta


def append_failures(records: list[dict[str, Any]], tag: str | None = None) -> None:
    if not records:
        return
    path = failure_path(tag)
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            loaded = read_json(path)
            if isinstance(loaded, list):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = []
    write_json_atomic(path, existing + records)


class RateLimiter:
    """Global fixed-interval limiter. Default 9 requests/minute = 6.67 s spacing."""

    def __init__(self, rpm: float = 9.0) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.interval = 60.0 / float(rpm)
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                delay = self._next - now
                time.sleep(delay)
                now = time.monotonic()
            self._next = now + self.interval


class CallLogger:
    """One JSONL line per upstream call. Never write credentials here."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (LOGS_DIR / f"fetch-{today_tag()}.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        dataset: str,
        *,
        trade_date: str | None = None,
        ts_code: str | None = None,
        duration_ms: int,
        rows: int | None,
        status: str,
        error: str | None = None,
        attempt: int = 1,
    ) -> None:
        record = {
            "ts": utc_now(),
            "dataset": dataset,
            "trade_date": trade_date,
            "ts_code": ts_code,
            "duration_ms": int(duration_ms),
            "rows": rows,
            "status": status,
            "attempt": int(attempt),
        }
        if error:
            record["error"] = str(error)[:500]
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def read_token(required: bool = True) -> str | None:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token and required:
        raise RuntimeError(
            f"缺少 Tushare token：环境变量 {TOKEN_ENV} 或 {TOKEN_FILE}（权限 600）"
        )
    return token or None


def duckdb_connect(read_only: bool = False):
    import duckdb

    ensure_layout()
    DB_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "temp_directory": str(TMP_DIR),
        "memory_limit": os.environ.get("QUANTDB_MEMORY_LIMIT", "4GB"),
        "threads": int(os.environ.get("QUANTDB_THREADS", "4")),
    }
    con = duckdb.connect(str(DB_PATH), read_only=read_only, config=config)
    con.execute("SET preserve_insertion_order=false")
    return con


def apply_schema(con) -> None:
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    con.execute(sql)


def ensure_db() -> None:
    """Create the DuckDB file and apply schema if it does not exist yet."""
    if DB_PATH.exists():
        return
    with duckdb_connect() as con:
        apply_schema(con)
        con.execute("CHECKPOINT")


def schema_columns(con, table: str) -> list[str]:
    rows = con.execute(f"DESCRIBE {table}").fetchall()
    return [str(row[0]) for row in rows]


def normalize_ts_code(code: str) -> str:
    raw = str(code).strip().upper()
    if "." in raw:
        left, right = raw.split(".", 1)
        if right in {"SH", "SZ", "BJ"} and left.isdigit():
            return f"{left}.{right}"
        return raw
    if raw.startswith(("SH", "SZ", "BJ")) and raw[2:].isdigit():
        return f"{raw[2:]}.{raw[:2]}"
    if raw.isdigit() and len(raw) == 6:
        if raw[0] in {"6", "9"}:
            return f"{raw}.SH"
        if raw[0] in {"0", "3"}:
            return f"{raw}.SZ"
        if raw[0] in {"4", "8"}:
            return f"{raw}.BJ"
    return raw


def ts_code_to_sina(code: str) -> str:
    normalized = normalize_ts_code(code)
    try:
        left, right = normalized.split(".", 1)
    except ValueError as exc:
        raise ValueError(f"无法识别的代码: {code}") from exc
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(right)
    if not prefix:
        raise ValueError(f"无法识别的交易所后缀: {code}")
    return f"{prefix}{left}"


def fill_raw_json(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df["raw_json"] = []
        return df
    df = df.copy()
    records = []
    for row in df.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if key == "raw_json":
                continue
            if value is None:
                clean[key] = None
            elif isinstance(value, float) and pd.isna(value):
                clean[key] = None
            elif isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
                clean[key] = str(value)
            else:
                try:
                    if pd.isna(value):
                        clean[key] = None
                        continue
                except (TypeError, ValueError):
                    pass
                clean[key] = value
        records.append(json.dumps(clean, ensure_ascii=False, default=str))
    df["raw_json"] = records
    return df


def conform_frame(df: pd.DataFrame, table: str, con, source: str) -> pd.DataFrame:
    """Convert source columns to schema columns; keep full source row in raw_json."""
    if df is None:
        return pd.DataFrame()
    work = df.copy()
    work.columns = [str(col).lower() for col in work.columns]
    for col in DATE_COLUMNS.get(table, set()):
        if col in work.columns:
            raw = work[col].astype("string").str.replace(r"\.0$", "", regex=True)
            parsed = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
            if parsed.isna().all():
                parsed = pd.to_datetime(raw, errors="coerce")
            work[col] = parsed.dt.date
    if table in DATASETS_WITH_RAW_JSON:
        work = fill_raw_json(work)
    cols = schema_columns(con, table)
    for col in cols:
        if col not in work.columns:
            work[col] = None
    work = work[cols]
    if "source" in work.columns:
        work["source"] = source
    return work


def insert_dataframe(con, table: str, df: pd.DataFrame, replace: bool = True) -> int:
    """Insert by column name with PK upsert semantics."""
    if df is None or df.empty:
        return 0
    cols = schema_columns(con, table)
    work = df.copy()
    for col in cols:
        if col not in work.columns:
            work[col] = None
    work = work[cols]
    view_name = f"_incoming_{table}_{os.getpid()}"
    con.register(view_name, work)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    con.execute(f"{verb} INTO {table} BY NAME SELECT * FROM {view_name}")
    con.unregister(view_name)
    return len(work)


def read_partition_frame(dataset: str, partition: str) -> pd.DataFrame | None:
    """Read every Parquet file in a partition, including another writer's part.parquet."""
    folder = partition_dir(dataset, partition)
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    if not files:
        return None
    frames = [pd.read_parquet(path) for path in files]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def load_partitions(
    con,
    dataset: str,
    partitions: Iterable[str] | None = None,
    full_refresh: bool = False,
) -> int:
    """Load raw Parquet partitions into the DuckDB table."""
    if dataset not in TABLE_PK:
        raise ValueError(f"未知数据集: {dataset}")
    keys = list(partitions) if partitions is not None else list_partitions(dataset)
    if not keys:
        return 0
    complete = [key for key in keys if manifest_ok(dataset, key)]
    if not complete:
        return 0
    cols = schema_columns(con, dataset)
    if full_refresh:
        con.execute(f"DELETE FROM {dataset}")
    # Read every *.parquet in the partition: the main writer uses data.parquet,
    # while a concurrent backfill may use part.parquet.
    files = []
    for key in complete:
        files.extend(str(path) for path in sorted(partition_dir(dataset, key).glob("*.parquet")))
    if not files:
        return 0
    date_cols = DATE_COLUMNS.get(dataset, set())
    select_cols = ", ".join(
        (
            f'CAST("{col}" AS DATE) AS "{col}"'
            if col in date_cols
            else f'"{col}"'
        )
        for col in cols
    )
    # hive_partitioning=false is critical: the directory name trade_date=YYYYMMDD
    # would otherwise override the Parquet DATE column with a BIGINT partition key.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _batch AS "
        "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
        [files],
    )
    pk_cols = ", ".join(TABLE_PK[dataset])
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _dedup AS "
        f"SELECT * EXCLUDE (_rn) FROM ("
        f"SELECT *, row_number() OVER (PARTITION BY {pk_cols}) AS _rn FROM _batch"
        ") WHERE _rn = 1"
    )
    con.execute(
        f"INSERT OR REPLACE INTO {dataset} BY NAME SELECT {select_cols} FROM _dedup"
    )
    loaded = int(con.execute("SELECT count(*) FROM _dedup").fetchone()[0])
    con.execute("DROP TABLE _batch")
    con.execute("DROP TABLE _dedup")
    return loaded
