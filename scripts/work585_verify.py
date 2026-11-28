#!/usr/bin/env python3
"""WORK-585 复核脚本：行业装载变更留痕（diff 日志 + 快照源整源替换）的实测证据。

用法（仓库根目录）：
  QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
    /home/moyongkai/.venvs/quantdb/bin/python scripts/work585_verify.py all

分项：
  demo   离线演示：把生产 cnesa_raw 复制到 staging/work585-demo，造「值修改 + 期消失」，
         连跑三次装载，dump 表内行与 diff 日志。全程不碰 /srv/share/quantdb/db 主库。
  db     主库现状：industry_load_diff 按 (table, change_type) 计数、本轮 no_change 覆盖、
         近 30 天复核视图前 N 行。
  arith  分项计数自检：各 change_type 相加 = 合计，17 表相加 = 合计。
  gate   只跑 C9_load_diff（只读）。

结论写到 meta/industry/work585-demo.json，供交付页引用。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

HOME = Path(os.environ.get("QUANTDB_HOME", "/srv/share/quantdb"))
REPO = Path(__file__).resolve().parents[1]
# 演示库落在项目自己的 staging 下（机械盘、每次重建、约 12MB，不进 /tmp）
DEMO = Path(os.environ.get("WORK585_DEMO_DIR", HOME / "staging/work585-demo"))
DB_PATH = HOME / "db/quantdb.duckdb"
META = HOME / "meta/industry"
FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def run_loader(home: Path, *args: str) -> str:
    env = dict(os.environ, QUANTDB_HOME=str(home), PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-m", "db.etl.industry", "load", *args],
        cwd=str(REPO), env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"装载失败 {args}: {proc.returncode}\n{proc.stderr[-2000:]}")
    return proc.stdout


# --------------------------------------------------------------------------- #
# demo：离线造「值修改 + 期消失」，看留痕
# --------------------------------------------------------------------------- #

def cnesa_raw_frame() -> pd.DataFrame:
    src = HOME / "raw/industry/cnesa_storage/part.parquet"
    return pd.read_parquet(src)


def write_demo_raw(rows: pd.DataFrame) -> None:
    target = DEMO / "raw/industry/cnesa_storage"
    target.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(target / "part.parquet", index=False)
    (target / "manifest.json").write_text(
        json.dumps({"dataset": "cnesa_storage", "fetched_at": "2026-11-25T22:00:00+0800"},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def demo_connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DEMO / "db/quantdb.duckdb"))


def cmd_demo() -> dict:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    (DEMO / "db").mkdir(parents=True)
    (DEMO / "meta").mkdir(parents=True)
    (DEMO / "logs").mkdir(parents=True)
    (DEMO / "staging/tmp").mkdir(parents=True)

    # schema_industry.sql 里的覆盖视图会读 data_catalog；演示库里先建空表
    seed = duckdb.connect(str(DEMO / "db/quantdb.duckdb"))
    seed.execute(
        'CREATE TABLE IF NOT EXISTS data_catalog ('
        '"行业" VARCHAR, "区域" VARCHAR, "缺口优先级" VARCHAR, "指标名" VARCHAR, '
        '"频率" VARCHAR, "单位" VARCHAR, "一手来源" VARCHAR)'
    )
    seed.close()

    base = cnesa_raw_frame()
    write_demo_raw(base)
    run_loader(DEMO, "--sources", "cnesa_storage")

    # 第 2 轮：改 5 个值（覆盖不同指标，带上游原句）+ 上游撤掉一期（2022-01 平均时长）
    v2 = base.copy()
    current = v2[v2["period"].astype(str) == "2026-01"].head(5)
    targets = list(current.index)
    before = [float(v2.loc[i, "value"]) for i in targets]
    for i in targets:
        v2.loc[i, "value"] = round(float(v2.loc[i, "value"]) * 1.1, 2)
    dropped_period = "2022-01"
    v2 = v2[v2["period"].astype(str) != dropped_period].reset_index(drop=True)
    write_demo_raw(v2)
    out2 = run_loader(DEMO, "--sources", "cnesa_storage")

    # 第 3 轮：同一份 raw 再跑一次（幂等）
    write_demo_raw(v2)
    out3 = run_loader(DEMO, "--sources", "cnesa_storage")

    con = demo_connect()
    runs = [r[0] for r in con.execute(
        "SELECT run_id FROM industry_load_audit ORDER BY finished_at"
    ).fetchall()]
    diff2 = con.execute(
        "SELECT change_type, period, indicator, column_name, old_value, new_value, "
        "reason, source_doc "
        "FROM industry_load_diff WHERE run_id = ? ORDER BY change_type, indicator",
        [runs[1]],
    ).df()
    diff3 = con.execute(
        "SELECT change_type, indicator, new_value, reason FROM industry_load_diff "
        "WHERE run_id = ?", [runs[2]],
    ).df()
    after = con.execute(
        "SELECT period, indicator, value FROM industry_cnesa_storage ORDER BY 1, 2"
    ).df()
    con.close()

    check(int(diff3.shape[0]) == 1 and diff3.loc[0, "change_type"] == "no_change",
          f"第 3 轮无变化只写 1 行 no_change（实际 {diff3.shape[0]} 行）")
    changed = diff2[diff2["change_type"] == "value_changed"].reset_index(drop=True)
    dropped = diff2[diff2["change_type"] == "row_dropped"].reset_index(drop=True)
    check(changed.shape[0] == 5 and set(changed["column_name"]) == {"value"},
          f"值修改落 value_changed 5 行（实际 {changed.shape[0]} 行）")
    check(all("；原句：" in r and "｜原句" not in r for r in changed["reason"]),
          "value_changed 的 reason 都带「；原句：」段")
    check(all(len(r) <= 500 for r in changed["reason"]),
          f"reason 长度均 ≤ 500（最长 {int(max((len(r) for r in changed['reason']), default=0))}）")
    check(all(bool(d) for d in changed["source_doc"]),
          "value_changed 的 source_doc 均非空")
    check(dropped.shape[0] == 1, f"期消失落 row_dropped 1 行（实际 {dropped.shape[0]} 行）")
    periods_after = sorted({str(p)[:7] for p in after["period"]})
    check("2022-01" not in periods_after and "2026-01" in periods_after,
          f"整源替换后 2022-01 消失、2026-01 仍在（F3），现存期 {periods_after}"
          f"，{after.shape[0]} 行")

    payload = {
        "demo_home": str(DEMO),
        "round1_rows": int(base.shape[0]),
        "runs": runs,
        "before_value": before,
        "changed": changed.to_dict("records"),
        "dropped": dropped.to_dict("records"),
        "no_change": diff3.to_dict("records"),
        "rows_after": int(after.shape[0]),
        "periods_after": periods_after,
        "loader_out": [out2[-400:], out3[-400:]],
    }
    META.mkdir(parents=True, exist_ok=True)
    (META / "work585-demo.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(f"  demo 证据 -> {META / 'work585-demo.json'}")
    return payload


# --------------------------------------------------------------------------- #
# db：主库现状
# --------------------------------------------------------------------------- #

def latest_full_run(con) -> str | None:
    row = con.execute(
        "SELECT run_id FROM industry_load_audit GROUP BY 1 "
        "HAVING count(*) = 17 ORDER BY max(finished_at) DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def cmd_db() -> dict:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    run = latest_full_run(con)
    counts = con.execute(
        "SELECT table_name, change_type, count(*) AS n FROM industry_load_diff "
        "WHERE run_id = ? GROUP BY 1, 2 ORDER BY 1, 2",
        [run],
    ).df()
    total = int(con.execute(
        "SELECT count(*) FROM industry_load_diff WHERE run_id = ?", [run]
    ).fetchone()[0])
    view = con.execute(
        "SELECT * FROM v_industry_load_changes_recent ORDER BY changed_at DESC LIMIT 20"
    ).df()
    sample = con.execute(
        "SELECT change_type, reason, source_doc FROM industry_load_diff "
        "WHERE run_id = ? ORDER BY table_name LIMIT 2", [run]
    ).df()
    no_reason = int(con.execute(
        "SELECT count(*) FROM industry_load_diff WHERE reason IS NULL"
    ).fetchone()[0])
    view_total = int(con.execute(
        "SELECT count(*) FROM v_industry_load_changes_recent"
    ).fetchone()[0])
    tables_with = int(con.execute(
        "SELECT count(DISTINCT table_name) FROM industry_load_diff WHERE run_id = ?", [run]
    ).fetchone()[0])
    con.close()

    print(f"  run={run} diff 行合计={total} 覆盖表数={tables_with}")
    check(tables_with == 17, f"最新完整运行覆盖 17 张表留痕（实际 {tables_with}）")
    check(int(counts["n"].sum()) == total, "分项相加 = diff 行合计")
    check(no_reason == 0, f"每行都有 reason（空值 {no_reason} 行）")
    check(all(sample["reason"].str.len() > 0), "样本行的 reason 非空")
    check(list(view.columns)[-3:] == ["source_doc", "reason", "changed_at"],
          f"视图末三列为 source_doc/reason/changed_at（实际 {list(view.columns)[-3:]}）")
    archived = sorted(HOME.glob("raw/industry/*/history/*")) + sorted(HOME.glob("raw/industry_alt/*/history/*"))
    check(len(archived) >= 18, f"raw 快照已归档 ≥18 份（实际 {len(archived)}）")
    payload = {
        "run_id": run,
        "diff_total": total,
        "tables_with_diff": tables_with,
        "by_table_type": counts.to_dict("records"),
        "view_rows": view.to_dict("records"),
        "view_total": view_total,
        "no_reason_rows": no_reason,
        "reason_sample": sample.to_dict("records"),
        "history_files": len(archived),
    }
    META.mkdir(parents=True, exist_ok=True)
    (META / "work585-db.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(f"  主库证据 -> {META / 'work585-db.json'}")
    return payload


# --------------------------------------------------------------------------- #
# arith：分项相加=合计
# --------------------------------------------------------------------------- #

def cmd_arith() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    run = latest_full_run(con)
    rows = con.execute(
        "SELECT change_type, count(*) FROM industry_load_diff WHERE run_id = ? "
        "GROUP BY 1 ORDER BY 1", [run],
    ).fetchall()
    total = con.execute(
        "SELECT count(*) FROM industry_load_diff WHERE run_id = ?", [run]
    ).fetchone()[0]
    print(f"  run={run}")
    for kind, n in rows:
        print(f"    {kind:14s} {n:>6}")
    print(f"    {'合计':14s} {sum(n for _, n in rows):>6}  (=表内 {total})")
    check(sum(n for _, n in rows) == total, "各 change_type 相加 = diff 行合计")

    per_table = con.execute(
        "SELECT table_name, count(*) FROM industry_load_diff WHERE run_id = ? GROUP BY 1", [run]
    ).fetchall()
    check(sum(n for _, n in per_table) == total, "各表相加 = diff 行合计")
    check(len(per_table) == 17, f"留痕表数 = 17（实际 {len(per_table)}）")
    con.close()


def cmd_gate() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    from db.checks.industry import c9_load_diff  # noqa: PLC0415 - 只在 gate 分项需要

    ok, detail = c9_load_diff(con)
    con.close()
    print(json.dumps({k: v for k, v in detail.items() if k != "diff_rows"},
                     ensure_ascii=False, indent=1))
    check(ok, "C9_load_diff PASS")


def main() -> int:
    parser = argparse.ArgumentParser(prog="work585_verify")
    parser.add_argument("part", nargs="?", default="all",
                        choices=["all", "demo", "db", "arith", "gate"])
    args = parser.parse_args()
    if args.part in ("all", "demo"):
        print("[demo] 离线演示：值修改 + 期消失 + 幂等")
        cmd_demo()
    if args.part in ("all", "db"):
        print("[db] 主库留痕现状")
        cmd_db()
    if args.part in ("all", "arith"):
        print("[arith] 分项相加=合计")
        cmd_arith()
    if args.part in ("all", "gate"):
        print("[gate] C9_load_diff")
        cmd_gate()
    print("\n结论：" + ("PASS" if not FAILS else f"FAIL {len(FAILS)} 项"))
    for f in FAILS:
        print("  - " + f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
