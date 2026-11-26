#!/usr/bin/env python3
"""把 10 个 alt 脚本按固定窗口跑一遍，再跑第二遍验幂等，产出 docs/alt_run_log.json。

用途：验收时「10 个脚本实跑过 + 连跑两次行数/sha 一致」这两条要求的一次性证据。
不改脚本、不改数据；只读 manifest.json 与 data.csv 的 sha256。

用法：
  python3 scripts/alt/run_all.py                 # 跑两遍并比对
  python3 scripts/alt/run_all.py --once          # 只跑一遍
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW = os.environ.get("ALT_RAW_ROOT", "/srv/share/quantdb/raw/industry_alt")
DOCS = os.path.join(REPO, "docs")

# 固定窗口：以「能覆盖本期样本」为准，不追全历史（全历史回补属入库后 ETL 的事）
PLAN = [
    ("trendforce_spot_price", ["--start", "2026-11-01"]),
    ("benchmark_public_index", ["--start", "2026-11-01"]),
    ("sne_research_releases", ["--max-pages", "2", "--start", "2026-10-01"]),
    ("shfe_gfex_quotes", ["--start", "2026-11-17", "--end", "2026-11-27"]),
    ("eurostat_trade", ["--start", "2020", "--end", "2024"]),
    ("owid_ev_sales", ["--start", "2010", "--end", "2025"]),
    ("owid_energy_co2", ["--start", "2010", "--end", "2024"]),
    ("worldbank_wdi", ["--start", "2010", "--end", "2025"]),
    ("irena_re_capacity", ["--start", "2015", "--end", "2025"]),
    ("emmes_storage", []),
]


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else ""


def run_one(slug: str, extra: list[str]) -> dict:
    script = os.path.join(HERE, slug, "fetch.py")
    data = os.path.join(RAW, slug, "data.csv")
    t0 = time.time()
    p = subprocess.run([sys.executable, script, *extra], capture_output=True, text=True,
                       cwd=REPO, timeout=1800)
    dt = round(time.time() - t0, 1)
    man = {}
    mpath = os.path.join(RAW, slug, "manifest.json")
    if os.path.exists(mpath):
        man = json.load(open(mpath, encoding="utf-8"))
    return {"slug": slug, "args": extra, "exit": p.returncode, "seconds": dt,
            "rows": man.get("rows"), "csv_sha256": man.get("csv_sha256"),
            "data_sha256": sha(data),
            "date_range": man.get("date_range") or man.get("years"),
            "stderr_tail": (p.stderr or "")[-300:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    log = {"raw_root": RAW, "plan": PLAN, "runs": []}
    first = {}
    for slug, extra in PLAN:
        r1 = run_one(slug, extra)
        first[slug] = r1
        print(f"[1] {slug:24s} exit={r1['exit']} rows={r1['rows']} {r1['seconds']}s")
        if r1["exit"] != 0:
            print(f"    FAIL: {r1['stderr_tail']}", file=sys.stderr)
        log["runs"].append({"pass": 1, **r1})
    if a.once:
        json.dump(log, open(os.path.join(DOCS, "alt_run_log.json"), "w"), ensure_ascii=False, indent=1)
        return 0 if all(r["exit"] == 0 for r in log["runs"]) else 1

    idem = []
    for slug, extra in PLAN:
        r2 = run_one(slug, extra)
        same_rows = r2["rows"] == first[slug]["rows"]
        same_sha = r2["data_sha256"] == first[slug]["data_sha256"]
        idem.append({"slug": slug, "rows_1": first[slug]["rows"], "rows_2": r2["rows"],
                     "sha_equal": same_sha, "rows_equal": same_rows,
                     "sha_1": first[slug]["data_sha256"][:16],
                     "sha_2": r2["data_sha256"][:16]})
        print(f"[2] {slug:24s} rows {first[slug]['rows']}→{r2['rows']} sha_equal={same_sha}")
    log["idempotency"] = idem
    json.dump(log, open(os.path.join(DOCS, "alt_run_log.json"), "w"), ensure_ascii=False, indent=1)
    bad = [i for i in idem if not (i["sha_equal"] and i["rows_equal"])]
    print(f"幂等：{len(idem) - len(bad)}/{len(idem)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
