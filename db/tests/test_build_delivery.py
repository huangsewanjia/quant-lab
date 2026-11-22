from __future__ import annotations

from pathlib import Path

import pytest

from db.reports.build_delivery import REQUIRED_CHECKS, validate_checks


def _payload(home: Path, day: str = "20261122"):
    return {
        "home": str(home),
        "day": day,
        "checks": {name: {"ok": True, "detail": {}} for name in REQUIRED_CHECKS},
    }


def test_valid_checks_pass(tmp_path):
    validate_checks(_payload(tmp_path), tmp_path, "20261122")


def test_wrong_home_rejected(tmp_path):
    with pytest.raises(SystemExit):
        validate_checks(_payload(tmp_path / "other"), tmp_path, "20261122")


def test_wrong_day_rejected(tmp_path):
    with pytest.raises(SystemExit):
        validate_checks(_payload(tmp_path, "20261121"), tmp_path, "20261122")


def test_missing_check_rejected(tmp_path):
    payload = _payload(tmp_path)
    payload["checks"].pop("rowcount")
    with pytest.raises(SystemExit):
        validate_checks(payload, tmp_path, "20261122")


def test_failed_check_rejected(tmp_path):
    payload = _payload(tmp_path)
    payload["checks"]["calendar"]["ok"] = False
    with pytest.raises(SystemExit):
        validate_checks(payload, tmp_path, "20261122")
