from __future__ import annotations

import pytest

from db.etl import common


@pytest.fixture()
def qdb(tmp_path, monkeypatch):
    home = tmp_path / "quantdb"
    raw = home / "raw"
    meta = home / "meta"
    db_dir = home / "db"
    policy = home / "policy"
    staging = home / "staging"
    logs = home / "logs"
    tmp = staging / "tmp"
    for name, value in {
        "QDB_HOME": home,
        "RAW_DIR": raw,
        "META_DIR": meta,
        "DB_DIR": db_dir,
        "POLICY_DIR": policy,
        "STAGING_DIR": staging,
        "LOGS_DIR": logs,
        "TMP_DIR": tmp,
        "DB_PATH": db_dir / "quantdb.duckdb",
        "LAYOUT_DIRS": [raw, db_dir, meta, policy, staging, logs, tmp],
    }.items():
        monkeypatch.setattr(common, name, value)
    common.ensure_layout()
    common.ensure_db()
    yield common
