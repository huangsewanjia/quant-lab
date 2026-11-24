#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 产物清单（把审计的直接输入也纳入 sha256 覆盖）

为什么要有这个脚本
------------------
第一轮独立审查的 B2 指出：快照 manifest 只锁了 5~6 份数据 CSV，
**没锁事实层 `panel_backtest.csv`**（已修）。第二轮审查进一步指出：
审计脚本的**直接输入**——`preds_*.npz`（预测）、`bt_daily_*.csv`（逐日账）、
`sample.npz`（标签与切分）——**全部不在版本库、也不在任何 sha256 覆盖内**，
第三方无法判断「复算用的输入是不是同一份」。

本脚本把这些产物逐份 sha256 锁定成 `experiments/WORK-555/artifact_manifest.json`：

  · 审计的直接输入：preds_*.npz、bt_daily_*.csv、sample.npz、sample_meta.json
  · 建模与回测的汇总：run_*.json、backtest_*.json

**范围刻意不含交付件**（report.html / PDF / PNG / figures）：它们在审计之后才生成，
纳入清单会与「审计后重生成报告」的流程互相打架。交付件的 sha256 由
`scripts/work555_final_comment.py` 在发布时实时计算。

注意：`.npz` / `.csv` 按仓库规则不入库（体积大、可由脚本重建），
但**清单入库**，因此第三方重建后可以逐份比对 sha256。

用法：
  /tmp/w555venv/bin/python scripts/work555_artifact_manifest.py --build   # 重建清单
  /tmp/w555venv/bin/python scripts/work555_artifact_manifest.py --check   # 只校验（漂移即退出码 1）
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)
MAN = os.path.join(OUT, "artifact_manifest.json")

# 分类 -> (匹配函数, 说明)
GROUPS = [
    ("audit_input", lambda f: f.startswith("preds_") and f.endswith(".npz"),
     "审计的直接输入：测试段预测"),
    ("audit_input", lambda f: f.startswith("bt_daily_") and f.endswith(".csv"),
     "审计的直接输入：逐日组合账"),
    ("audit_input", lambda f: f in ("sample.npz", "sample_meta.json"),
     "审计的直接输入：样本与切分/标签"),
    ("model_output", lambda f: f.startswith("run_") and f.endswith(".json"),
     "建模汇总：训练日志与统计"),
    ("model_output", lambda f: f.startswith("backtest_") and f.endswith(".json"),
     "建模汇总：组合回测指标"),
]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    items = []
    seen = set()
    for group, match, note in GROUPS:
        for fn in sorted(os.listdir(OUT)):
            if fn in seen or not match(fn):
                continue
            p = os.path.join(OUT, fn)
            if not os.path.isfile(p) or os.path.getsize(p) == 0:
                continue                      # 半成品 / 空文件不锁
            seen.add(fn)
            items.append({"group": group, "name": fn,
                          "path": f"experiments/{EXP}/{fn}",
                          "bytes": os.path.getsize(p),
                          "sha256": sha256_of(p), "note": note})
    return {"experiment": EXP,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": "审计的直接输入 + 建模汇总 + 对外交付件的逐份 sha256 锁定。",
            "n_items": len(items), "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="重建并写回清单")
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()

    man = collect()
    if args.check:
        if not os.path.exists(MAN):
            print("[FAIL] 缺 artifact_manifest.json，先跑 --build")
            return 1
        with open(MAN, encoding="utf-8") as fh:
            old = json.load(fh)
        old_map = {i["path"]: i["sha256"] for i in old.get("items", [])}
        drift, missing, extra = [], [], []
        for i in man["items"]:
            if i["path"] not in old_map:
                missing.append(i["path"])
            elif old_map[i["path"]] != i["sha256"]:
                drift.append((i["path"], old_map[i["path"]][:16], i["sha256"][:16]))
        new_paths = {i["path"] for i in man["items"]}
        extra = [p for p in old_map if p not in new_paths]
        if drift or missing or extra:
            print(f"[FAIL] 产物清单与磁盘不一致：漂移 {len(drift)} / 新增未登记 {len(missing)} / "
                  f"清单里有但磁盘没有 {len(extra)}")
            for d in drift[:10]:
                print(f"   DRIFT {d[0]}  manifest={d[1]}…  disk={d[2]}…")
            for m in missing[:10]:
                print(f"   NEW   {m}")
            for e in extra[:10]:
                print(f"   GONE  {e}")
            return 1
        print(f"[OK] 产物清单 {man['n_items']} 份逐份 sha256 一致")
        return 0

    with open(MAN, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"artifact_manifest": MAN, "n_items": man["n_items"],
                      "groups": sorted({i["group"] for i in man["items"]})},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
