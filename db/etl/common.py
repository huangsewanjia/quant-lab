"""Shared paths, manifests, rate limiting and DuckDB helpers for the quantdb ETL.

Hard constraints from WORK-513 (P1 full backfill):
  * data lives under /srv/share/quantdb (HDD), never under / (SSD system disk);
  * DuckDB spill goes to staging/tmp;
  * the Tushare token is read from the env or the 0600 file and never logged.

Layout (issue-mandated):
  raw/<dataset>/trade_date=YYYYMMDD/part.parquet   -- 行情类，按日分区
  raw/<dataset>/ts_code=<code>/part.parquet        -- 财务类，按股票分区
  db/quantdb.duckdb                                -- DuckDB 规范化层
  meta/<dataset>/<partition>.json                  -- 行数 + sha256 + 抓取时间 + 参数
  logs/fetch-<date>.jsonl                          -- 每次调用：接口/参数/耗时/行数/成败
  staging/                                         -- 临时区，可清
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

QDB_HOME = Path(os.environ.get("QUANTDB_HOME", "/srv/share/quantdb")).resolve()
RAW_DIR = QDB_HOME / "raw"
META_DIR = QDB_HOME / "meta"
DB_DIR = QDB_HOME / "db"
STAGING_DIR = QDB_HOME / "staging"
LOGS_DIR = QDB_HOME / "logs"
TMP_DIR = STAGING_DIR / "tmp"
DB_PATH = DB_DIR / "quantdb.duckdb"
FAILED_DIR = META_DIR / "failed"

TOKEN_ENV = "TUSHARE_TOKEN"
TOKEN_FILE = Path(
    os.environ.get("QUANTDB_TUSHARE_TOKEN_FILE", "/home/moyongkai/.config/quantdb/tushare.token")
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = PROJECT_ROOT / "db" / "schema.sql"

LAYOUT_DIRS = [RAW_DIR, DB_DIR, META_DIR, STAGING_DIR, LOGS_DIR, TMP_DIR, FAILED_DIR]

PART_FILE = "part.parquet"

# Datasets partitioned by trade_date=YYYYMMDD (行情类).
TRADE_DATE_DATASETS = {
    "daily",
    "daily_basic",
    "adj_factor",
    "stk_limit",
    "suspend_d",
    "index_daily",
}

# Datasets partitioned by ts_code=<code> (财务类).
TS_CODE_DATASETS = {
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "dividend",
    "namechange",
}

# Natural keys, kept in sync with schema.sql. Used for idempotent dedupe.
TABLE_PK: dict[str, tuple[str, ...]] = {
    "trade_cal": ("exchange", "cal_date"),
    "stock_basic": ("ts_code",),
    "daily": ("ts_code", "trade_date"),
    "daily_basic": ("ts_code", "trade_date"),
    "adj_factor": ("ts_code", "trade_date"),
    "stk_limit": ("trade_date", "ts_code"),
    "suspend_d": ("ts_code", "trade_date"),
    "index_daily": ("ts_code", "trade_date"),
    "index_weight": ("index_code", "con_code", "trade_date"),
    "index_classify": ("index_code", "level"),
    "income": ("ts_code", "end_date", "report_type", "ann_date", "dup_seq"),
    "balancesheet": ("ts_code", "end_date", "report_type", "ann_date", "dup_seq"),
    "cashflow": ("ts_code", "end_date", "report_type", "ann_date", "dup_seq"),
    "fina_indicator": ("ts_code", "end_date", "ann_date", "dup_seq"),
    "dividend": ("ts_code", "end_date", "div_proc", "ann_date", "dup_seq"),
    "namechange": ("ts_code", "name", "start_date"),
}

# Natural key of the datasets whose upstream key is NOT unique.
#
# Measured 2026-11-22 against the live API: the financial statements return
# two versions of the same report (the original filing and the restatement,
# distinguished by `update_flag`) under the same (ts_code, end_date,
# report_type, ann_date), and for a few codes the restatement actually
# changed the numbers (ebit, ebitda, total_share, free_cashflow).
# `fina_indicator` returns two computation passes of the same report that
# differ in the cash-flow-derived fields (fcff, fcfe, bps, ...), and
# `dividend` can report the same plan twice. Dropping one of those rows
# would silently discard ~25-30% of the financial data, so every row is kept
# and numbered inside its group by `dup_seq`.
BASE_PK: dict[str, tuple[str, ...]] = {
    "income": ("ts_code", "end_date", "report_type", "ann_date"),
    "balancesheet": ("ts_code", "end_date", "report_type", "ann_date"),
    "cashflow": ("ts_code", "end_date", "report_type", "ann_date"),
    "fina_indicator": ("ts_code", "end_date", "ann_date"),
    "dividend": ("ts_code", "end_date", "div_proc", "ann_date"),
}

# Datasets that carry the synthetic `dup_seq` discriminator.
DUP_SEQ_DATASETS = frozenset(BASE_PK)

# Datasets whose partition CONTENT depends on the run window (--start/--end).
# A narrow run writes partitions that only cover that window; resuming them
# for a wider study window would silently keep the narrow data, so the window
# is recorded in the manifest and a mismatch forces a re-fetch.
WINDOW_SCOPED_DATASETS = frozenset(
    {
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
        "dividend",
        "namechange",
        "index_weight",
    }
)

# Columns that must be read as DATE in DuckDB.
DATE_COLUMNS: dict[str, set[str]] = {
    "trade_cal": {"cal_date", "pretrade_date"},
    "stock_basic": {"list_date", "delist_date"},
    "daily": {"trade_date"},
    "daily_basic": {"trade_date"},
    "adj_factor": {"trade_date"},
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
    """Partition directory per the layout mandated by WORK-513."""
    key = safe_key(partition)
    if dataset in TRADE_DATE_DATASETS:
        return RAW_DIR / dataset / f"trade_date={key}"
    if dataset in TS_CODE_DATASETS:
        return RAW_DIR / dataset / f"ts_code={key}"
    if dataset == "index_weight":
        return RAW_DIR / dataset / f"index_code={key}"
    return RAW_DIR / dataset / f"partition={key}"


def partition_file(dataset: str, partition: str) -> Path:
    return partition_dir(dataset, partition) / PART_FILE


def manifest_path(dataset: str, partition: str) -> Path:
    return META_DIR / dataset / f"{safe_key(partition)}.json"


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
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
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
    """A partition is complete only when manifest + parquet exist and the hash matches."""
    meta = read_manifest(dataset, partition)
    if not meta:
        return False
    file_path = QDB_HOME / str(meta.get("file", ""))
    if not meta.get("file") or not file_path.exists():
        return False
    if file_path.stat().st_size != int(meta.get("bytes", -1)):
        return False
    if verify_hash and sha256_file(file_path) != meta.get("sha256"):
        return False
    return True


def list_partitions(dataset: str) -> list[str]:
    base = META_DIR / dataset
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.json"))


def normalize_date_columns(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Turn the upstream YYYYMMDD strings into real dates before they are stored.

    Tushare returns dates as ``YYYYMMDD`` strings; DuckDB's DATE type expects
    ``YYYY-MM-DD`` and rejects the compact form, so the conversion happens once
    in the L0 writer instead of on every load.
    """
    work = df.copy()
    for column in DATE_COLUMNS.get(dataset, set()):
        if column not in work.columns:
            continue
        raw = work[column].astype("string").str.replace(r"\.0$", "", regex=True)
        parsed = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
        if parsed.isna().all() and len(raw):
            parsed = pd.to_datetime(raw, errors="coerce")
        work[column] = parsed.dt.date
    return work


def assign_dup_seq(df: pd.DataFrame, base_key: list[str]) -> pd.DataFrame:
    """Number the rows inside each base-key group deterministically.

    The upstream returns the same report twice (the original filing and the
    restatement). Both rows are kept: the restatement changed the reported
    numbers for a few codes, so dropping one of them would silently discard
    a third of the financial data. ``dup_seq`` makes the primary key unique
    and stays reproducible - the rows inside a group are ordered by their own
    content, not by the order the API happened to return them in.
    """
    work = df.copy()
    if work.empty:
        work["dup_seq"] = pd.Series([], dtype="int64")
        return work
    colliding = work.duplicated(subset=base_key, keep=False)
    work["dup_seq"] = 0
    if not colliding.any():
        return work
    subset = work.loc[colliding]
    # astype(str) is not enough: object columns can hold floats, dates and
    # None at the same time, so the row is serialised cell by cell.
    content = pd.Series(
        ["\x1f".join(map(str, values)) for values in subset.to_numpy()],
        index=subset.index,
    )
    subset = subset.assign(_content=content)
    subset = subset.sort_values(base_key + ["_content"], kind="mergesort")
    subset["dup_seq"] = subset.groupby(base_key, sort=False).cumcount()
    work.loc[subset.index, "dup_seq"] = subset["dup_seq"].astype("int64")
    return work


def write_partition(
    df: pd.DataFrame,
    dataset: str,
    partition: str,
    source: str,
    pk: Iterable[str] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one partition atomically (tmp file + rename) and record its sha256.

    The parquet is written first and the manifest second, so a crash can never leave
    a manifest pointing at a file that does not exist.
    """
    ensure_layout()
    key = tuple(pk or TABLE_PK.get(dataset, ()))
    work = df.copy()
    work = normalize_date_columns(work, dataset)
    base_key = [column for column in BASE_PK.get(dataset, ()) if column in work.columns]
    if base_key:
        work = assign_dup_seq(work, base_key)
    if key and all(col in work.columns for col in key):
        work = work.drop_duplicates(subset=list(key), keep="last")
        work = work.sort_values(list(key)).reset_index(drop=True)
    target_dir = partition_dir(dataset, partition)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / PART_FILE
    tmp = target_dir / f".{PART_FILE}.tmp.{os.getpid()}.{threading.get_ident()}"
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


def drop_null_pk_rows(df: pd.DataFrame, dataset: str) -> tuple[pd.DataFrame, int]:
    """Drop rows whose primary-key components are NULL and report the count.

    A DuckDB PRIMARY KEY column is NOT NULL, so a row with a NULL key cannot
    be stored. The rows are dropped explicitly and the count is written to the
    manifest, so the loss is auditable instead of silent.
    """
    pk = [column for column in TABLE_PK.get(dataset, ()) if column in df.columns]
    if not pk or df.empty:
        return df, 0
    keep = df[pk].notna().all(axis=1)
    dropped = int((~keep).sum())
    if dropped:
        df = df[keep].reset_index(drop=True)
    return df, dropped


def clear_failures(dataset: str, partitions: Iterable[str]) -> None:
    """Remove ledger entries for partitions that have been fetched successfully."""
    path = FAILED_DIR / f"{safe_key(dataset)}.json"
    if not path.exists():
        return
    try:
        existing = read_json(path)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, list):
        return
    resolved = set(partitions)
    remaining = [r for r in existing if r.get("partition") not in resolved]
    if len(remaining) != len(existing):
        write_json_atomic(path, remaining)


def append_failures(records: list[dict[str, Any]], dataset: str | None = None) -> None:
    """Persist failed partitions to meta/failed/<dataset>.json (issue requirement)."""
    if not records:
        return
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    path = FAILED_DIR / f"{safe_key(dataset or 'all')}.json"
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            loaded = read_json(path)
            if isinstance(loaded, list):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = []
    known = {(r.get("dataset"), r.get("partition")) for r in existing}
    for record in records:
        if (record.get("dataset"), record.get("partition")) not in known:
            existing.append(record)
            known.add((record.get("dataset"), record.get("partition")))
    write_json_atomic(path, existing)


def read_failures(dataset: str | None = None) -> list[dict[str, Any]]:
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    path = FAILED_DIR / f"{safe_key(dataset or 'all')}.json"
    if not path.exists():
        return []
    try:
        loaded = read_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


class RateLimiter:
    """Global token bucket shared by every worker thread.

    Tushare's 2000-point tier enforces ~200 calls/minute for the whole token
    (measured 2026-11-22: 250 sequential calls took 75.0 s, no error raised).
    The bucket keeps the aggregate call rate under the cap while allowing
    several requests in flight.
    """

    def __init__(self, rpm: float = 195.0) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.rpm = float(rpm)
        self.interval = 60.0 / self.rpm
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = max(now, self._next) + self.interval


class CallLogger:
    """One JSONL line per upstream call. Never writes credentials here."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (LOGS_DIR / f"fetch-{today_tag()}.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        dataset: str,
        *,
        api: str | None = None,
        partition: str | None = None,
        params: dict[str, Any] | None = None,
        duration_ms: int,
        rows: int | None,
        status: str,
        error: str | None = None,
        attempt: int = 1,
    ) -> None:
        record = {
            "ts": utc_now(),
            "dataset": dataset,
            "api": api,
            "partition": partition,
            "params": params or {},
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


def retry_call(
    fn: Callable[[], Any],
    *,
    retries: int = 2,
    base_sleep: float = 1.0,
) -> tuple[bool, Any, str | None, int]:
    """Run fn with at most `retries` retries. Returns (ok, value, error, attempts)."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            return True, fn(), None, attempt
        except Exception as exc:  # noqa: BLE001 - upstream raises many types
            last_error = exc
            if attempt <= retries:
                time.sleep(base_sleep * (2 ** (attempt - 1)) + random.uniform(0, 0.2))
    return False, None, f"{type(last_error).__name__}: {last_error}", retries + 2


def read_token(required: bool = True) -> str | None:
    """Read the token from TUSHARE_TOKEN or the 0600 token file. Never logged."""
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
        "memory_limit": os.environ.get("QUANTDB_MEMORY_LIMIT", "6GB"),
        "threads": int(os.environ.get("QUANTDB_THREADS", "4")),
    }
    con = duckdb.connect(str(DB_PATH), read_only=read_only, config=config)
    con.execute("SET preserve_insertion_order=false")
    return con


def apply_schema(con) -> None:
    con.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


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


def ts_code_to_sina(code: str) -> str:
    """600000.SH -> sh600000, the symbol format akshare's Sina source expects."""
    normalised = normalize_ts_code(code)
    left, right = normalised.split(".", 1)
    return f"{right.lower()}{left}"


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


def frame_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """JSON-safe records; NaN/NaT become null."""
    payload = df.astype(object).where(pd.notna(df), None)
    return payload.to_dict(orient="records")


def partition_globs(dataset: str) -> list[str]:
    """Absolute parquet paths for every partition of a dataset."""
    base = RAW_DIR / dataset
    if not base.exists():
        return []
    return sorted(str(path) for path in base.glob(f"*/{PART_FILE}"))


def parquet_glob(dataset: str) -> str:
    """Glob pattern matching every partition file of a dataset."""
    return str(RAW_DIR / dataset / "*" / PART_FILE)


def load_partitions(
    con,
    dataset: str,
    partitions: Iterable[str] | None = None,
    full_refresh: bool = False,
    batch_size: int = 400,
) -> int:
    """Load raw Parquet partitions into the DuckDB table.

    hive_partitioning=false is critical: the directory name trade_date=YYYYMMDD
    would otherwise override the Parquet DATE column with a BIGINT partition key.
    """
    if dataset not in TABLE_PK:
        raise ValueError(f"未知数据集: {dataset}")
    keys = list(partitions) if partitions is not None else list_partitions(dataset)
    if not keys:
        return 0
    cols = schema_columns(con, dataset)
    files = [str(partition_file(dataset, key)) for key in keys]
    files = [path for path in files if Path(path).exists()]
    if not files:
        return 0
    if full_refresh:
        con.execute(f"DELETE FROM {dataset}")
    # Cast every column to its declared type: the Parquet files keep the raw
    # upstream types, the DuckDB layer is the normalised one. Only the columns
    # that actually exist in the batch are projected; INSERT ... BY NAME fills
    # the rest with NULL.
    types = schema_types(con, dataset)
    loaded = 0
    for start in range(0, len(files), batch_size):
        chunk = files[start : start + batch_size]
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _batch AS "
            "SELECT * FROM read_parquet(?, hive_partitioning=false)",
            [chunk],
        )
        batch_columns = {row[0] for row in con.execute("DESCRIBE _batch").fetchall()}
        select_cols = ", ".join(
            _select_expression(column, types.get(column, "VARCHAR"))
            for column in cols
            if column in batch_columns
        )
        # BY NAME tolerates Parquet columns that the schema does not declare.
        con.execute(
            f"INSERT OR REPLACE INTO {dataset} BY NAME SELECT {select_cols} FROM _batch"
        )
        loaded += int(con.execute("SELECT count(*) FROM _batch").fetchone()[0])
        con.execute("DROP TABLE _batch")
    return loaded


def _select_expression(column: str, dtype: str) -> str:
    """SQL projection that coerces one raw Parquet column to the schema type."""
    if dtype == "DATE":
        text = f'CAST("{column}" AS VARCHAR)'
        return (
            f'CAST(COALESCE(try_strptime({text}, \'%Y%m%d\'), '
            f"try_strptime({text}, '%Y-%m-%d')) AS DATE) AS \"{column}\""
        )
    return f'CAST("{column}" AS {dtype}) AS "{column}"'


def schema_types(con, table: str) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE {table}").fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


_SCHEMA_COLUMNS: dict[str, list[str]] = {}


def canonical_columns(dataset: str) -> list[str]:
    """Column list of a dataset, parsed from schema.sql (no DB connection needed)."""
    if not _SCHEMA_COLUMNS:
        import re

        text = SCHEMA_SQL.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+) \((.*?)\n\);", text, re.S
        ):
            body = match.group(2)
            _SCHEMA_COLUMNS[match.group(1)] = re.findall(r'^\s*"([^"]+)"\s+\w+', body, re.M)
    return list(_SCHEMA_COLUMNS.get(dataset, []))
