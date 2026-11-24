#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 字节级复现性核对（沙箱重跑 vs 仓库产物）

背景
----
第一轮独立审查自述未能核实「DEE 臂与 MLP-only seed 43 的端到端字节级重跑」，
第三轮要把这项补上。

做法
----
在**独立沙箱**（/tmp 下自建，脚本副本 + 只读符号链接的 sample.npz / sample_meta.json）
用**同一份代码、同一 seed、同一份面板**重跑若干臂，与仓库产物逐份比 sha256：
完全一致 = 该臂的端到端结果可由committed 代码逐字节复现。

用法：
  /tmp/w555venv/bin/python scripts/work555_repro_check.py --sandbox /tmp/w555_repro
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "experiments", "WORK-555")
RES = os.path.join(OUT, "repro_check.json")


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True, help="沙箱根目录（内含 experiments/WORK-555/）")
    args = ap.parse_args()

    sb_out = os.path.join(args.sandbox, "experiments", "WORK-555")
    rows = []
    if not os.path.isdir(sb_out):
        print(f"[FAIL] 沙箱不存在: {sb_out}", file=sys.stderr)
        return 1
    for fn in sorted(os.listdir(sb_out)):
        if not (fn.startswith("preds_") and fn.endswith(".npz")):
            continue
        a = os.path.join(sb_out, fn)
        b = os.path.join(OUT, fn)
        if not os.path.exists(b):
            rows.append({"artifact": fn, "status": "repo_missing"})
            continue
        ha, hb = sha256_of(a), sha256_of(b)
        rows.append({"artifact": fn, "sandbox_sha256": ha, "repo_sha256": hb,
                     "status": "identical" if ha == hb else "DIFFERENT"})
    res = {
        "experiment": "WORK-555",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sandbox": args.sandbox,
        "method": "同一份代码 / 同 seed / 同一面板，在独立沙箱重跑，与仓库产物逐份比 sha256",
        "n_checked": len(rows),
        "n_identical": sum(1 for r in rows if r.get("status") == "identical"),
        "rows": rows,
    }
    with open(RES, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    for r in rows:
        print(f"  {r['status']:14s} {r['artifact']}  "
              f"{str(r.get('sandbox_sha256', ''))[:16]}")
    print(json.dumps({"n_checked": res["n_checked"], "n_identical": res["n_identical"]},
                     ensure_ascii=False))
    return 0 if res["n_identical"] == res["n_checked"] else 1


if __name__ == "__main__":
    sys.exit(main())
