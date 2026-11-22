"""Run the quantdb quality gates.

  python -m db.checks.run all
  python -m db.checks.run G1_trading_day_reconciliation
  python -m db.checks.run all --sample-days 20 --tolerance 0.01

Any failed gate exits 1. A JSON report is written to meta/checks/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..etl import common
from . import checks

GATE_NAMES = [
    "G1_trading_day_reconciliation",
    "G2_primary_keys_and_parity",
    "G3_ohlc_consistency",
    "G4_adj_factor_positive",
    "G5_sina_crosscheck",
    "G6_manifest_integrity",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m db.checks.run",
        description="quantdb 质量闸（退出码非 0 即不通过）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("check", choices=GATE_NAMES + ["all"], help="检查项")
    parser.add_argument("--datasets", help="只检查这些数据集（逗号分隔），默认全部")
    parser.add_argument("--sample-days", type=int, default=20, help="跨源抽样交易日数")
    parser.add_argument("--tolerance", type=float, default=0.01, help="跨源收盘价容差（元）")
    parser.add_argument("--out", help="报告输出路径（默认 meta/checks/checks-<date>.json）")
    parser.add_argument("--quiet", action="store_true", help="只打印结论，不打印明细")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common.ensure_layout()
    datasets = [d.strip() for d in (args.datasets or "").split(",") if d.strip()] or None

    if args.check == "all":
        ok, report = checks.run_all(
            datasets=datasets, sample_days=args.sample_days, tolerance=args.tolerance
        )
        gates = {name: report[name] for name in GATE_NAMES}
    else:
        report = {"generated_at": common.utc_now()}
        try:
            con = common.duckdb_connect(read_only=True)
        except Exception as exc:  # noqa: BLE001
            # A missing/unopenable database is a FAIL, not a crash: the report
            # must still be written so the gate result is on record.
            gates = {
                args.check: {
                    "pass": False,
                    "error": f"DuckDB 无法打开: {type(exc).__name__}: {exc}",
                }
            }
            ok = False
            con = None
        if con is not None:
            with con:
                runner = {
                    "G1_trading_day_reconciliation": lambda: checks.check_trading_days(con),
                    "G2_primary_keys_and_parity": lambda: checks.check_primary_keys(con),
                    "G3_ohlc_consistency": lambda: checks.check_ohlc(con),
                    "G4_adj_factor_positive": lambda: checks.check_adj_factor(con),
                    "G5_sina_crosscheck": lambda: checks.check_sina_crosscheck(
                        con, sample_days=args.sample_days, tolerance=args.tolerance
                    ),
                    "G6_manifest_integrity": lambda: checks.check_manifest_integrity(
                        datasets or list(common.TABLE_PK)
                    ),
                }
                try:
                    gate_ok, detail = runner[args.check]()
                except Exception as exc:  # noqa: BLE001
                    gate_ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
            gates = {args.check: {"pass": bool(gate_ok), **detail}}
            ok = bool(gate_ok)

    out = (
        Path(args.out)
        if args.out
        else common.META_DIR / "checks" / f"checks-{common.today_tag()}.json"
    )
    payload = {"generated_at": common.utc_now(), "ok": ok, "gates": gates}
    common.write_json_atomic(out, payload)

    for name, detail in gates.items():
        flag = "PASS" if detail.get("pass") else "FAIL"
        print(f"[{flag}] {name}")
        if not args.quiet:
            print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
    print(f"report: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
