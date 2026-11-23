from __future__ import annotations

import pytest

from db.etl import common


@pytest.fixture()
def qdb(tmp_path, monkeypatch):
    home = tmp_path / "quantdb"
    raw = home / "raw"
    meta = home / "meta"
    db_dir = home / "db"
    staging = home / "staging"
    logs = home / "logs"
    tmp = staging / "tmp"
    failed = meta / "failed"
    policy = home / "policy"
    for name, value in {
        "QDB_HOME": home,
        "RAW_DIR": raw,
        "META_DIR": meta,
        "DB_DIR": db_dir,
        "STAGING_DIR": staging,
        "LOGS_DIR": logs,
        "TMP_DIR": tmp,
        "FAILED_DIR": failed,
        "DB_PATH": db_dir / "quantdb.duckdb",
        "POLICY_DIR": policy,
        "LAYOUT_DIRS": [
            raw,
            db_dir,
            meta,
            staging,
            logs,
            tmp,
            failed,
            policy,
            policy / "missing",
            policy / "meta",
        ],
    }.items():
        monkeypatch.setattr(common, name, value)
    common.ensure_layout()
    yield common
