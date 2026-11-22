"""Run quantdb quality gates.

  python -m db.checks.run all
  python -m db.checks.run calendar
  python -m db.checks.run pk --datasets daily,adj_factor
  python -m db.checks.run cross-source --sample 5

Any failed gate exits 1. A JSON report is written to meta/checks/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..etl import common
from . import checks


def split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def run_one(name: str, args, con) -> tuple[bool, object]:
    if name == "manifest":
        return checks.check_manifest(verify_hash=not args.no_verify_hash)
    if name == "calendar":
        return checks.check_calendar(con)
    if name == "pk":
        return checks.check_pk(con, split_csv(args.datasets))
    if name == "ohlc":
        return checks.check_ohlc(con)
    if name == "cross-source":
        return checks.check_cross_source(
            con, sample=args.sample, tolerance=args.tolerance
        )
    if name == "rowcount":
        return checks.check_rowcount(con)
    raise SystemExit(f"未知检查: {name}")


CHECKS = ["manifest", "calendar", "pk", "ohlc", "cross-source", "rowcount"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.checks.run",
        description="quantdb 质量闸",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("check", choices=CHECKS + ["all"], help="检查项")
    parser.add_argument("--datasets", help="pk 检查的数据集，逗号分隔")
    parser.add_argument("--sample", type=int, default=5, help="跨源抽样股票数")
    parser.add_argument("--tolerance", type=float, default=0.01, help="跨源收盘价容差（元）")
    parser.add_argument("--no-verify-hash", action="store_true", help="manifest 检查跳过 sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common.ensure_layout()
    common.ensure_db()
    names = CHECKS if args.check == "all" else [args.check]
    report = {"ts": common.utc_now(), "checks": {}}
    any_failed = False
    con = common.duckdb_connect(read_only=True)
    try:
        for name in names:
            try:
                ok, detail = run_one(name, args, con)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
            report["checks"][name] = {"ok": bool(ok), "detail": detail}
            any_failed = any_failed or not ok
            flag = "PASS" if ok else "FAIL"
            print(f"[{flag}] {name}")
            print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
    finally:
        con.close()
    report["ok"] = not any_failed
    out = common.META_DIR / "checks" / f"checks-{common.today_tag()}.json"
    common.write_json_atomic(out, report)
    print(f"report: {out}")
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
