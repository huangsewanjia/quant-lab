#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 不可成交断言「全组合实测」记录器

为什么要有这个脚本
------------------
预注册 §7 失败判据第 4 条要求「刻意关闭可成交掩码必须报错」。
之前只实测了 dee/s42 一组，而交付评论里却写了「8 个臂/种子组合逐一验证：全部触发、
全部退出码非 0」——这是**没有产物支撑的叙述性断言**（第三轮审查 S5 潜在硬编码段同型问题）。

本脚本把 8 个组合（4 臂 × 2 种子）全部真跑一遍，每组记录：
  - 子进程退出码（必须非 0）
  - 命中的第一条 UNTRADABLE_PRICE_READ 原文（ts_code / trade_date / reason / purpose）
  - stderr 全文 sha256（留痕，便于第三方核对同一份报错）

结果落盘到 `experiments/WORK-555/nomask_assert.json`，交付评论与报告从该文件读取，
不再手写断言。任一组合未触发或退出码为 0 → 本脚本非 0 退出（作为流水线闸门）。

用法：
  /tmp/w555venv/bin/python scripts/work555_nomask_all.py            # 跑 + 落盘
  /tmp/w555venv/bin/python scripts/work555_nomask_all.py --check    # 只校验已落盘记录
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "experiments", "WORK-555")
REC = os.path.join(OUT, "nomask_assert.json")

ARMS = ("dee", "mlp_only", "xgb", "xgbfull")
SEEDS = (42, 43)

MSG_RE = re.compile(r"UNTRADABLE_PRICE_READ\s+ts_code=(\S+)\s+trade_date=(\S+)"
                    r"(?:\s+(\S+))?\s+reason=(\S+)\s+purpose=(\S+)")


def run_one(arm, seed):
    """跑一次 --no-mask 回测，返回该组合的实测记录。"""
    cmd = [sys.executable, os.path.join("scripts", "work555_backtest.py"),
           "--arm", arm, "--seed", str(seed), "--no-mask"]
    env = dict(os.environ)
    env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True)
    blob = (p.stdout or "") + (p.stderr or "")
    hit = None
    for line in blob.splitlines():
        if "UNTRADABLE_PRICE_READ" in line:
            hit = line.strip()
            break
    fields = {}
    m = MSG_RE.search(blob)
    if m:
        fields = {"ts_code": m.group(1), "trade_date": m.group(2),
                  "reason": m.group(4), "purpose": m.group(5)}
    stderr = p.stderr or ""
    # 第四轮审查 S4-2 实测发现：traceback 全文内嵌**脚本的绝对路径**，
    # 因此 stderr_sha256 跨 checkout 路径不可复现（审查方实测：把路径替换回去可
    # 逐位复现提交值，证明差异 100% 来自路径，不是断言本身不可复现）。
    # 这里额外记一个**路径无关**的归一化摘要，供跨路径核对。
    stderr_norm = stderr.replace(REPO, "<REPO>")
    return {
        "arm": arm,
        "seed": seed,
        "exit_code": int(p.returncode),
        "triggered": bool(hit) and int(p.returncode) != 0,
        "message_line": hit or "",
        "fields": fields,
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stderr_sha256_normalized": hashlib.sha256(stderr_norm.encode("utf-8")).hexdigest(),
        "stderr_sha256_note": ("stderr_sha256 含本 checkout 的绝对路径，跨路径不可比；"
                               "stderr_sha256_normalized 把仓库路径替换成 <REPO> 后计算，跨路径可比"),
        "cmd": " ".join(["/tmp/w555venv/bin/python"] + cmd[1:]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验已落盘记录，不重跑")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(REC):
            print("[FAIL] 缺 experiments/WORK-555/nomask_assert.json", file=sys.stderr)
            return 1
        with open(REC, encoding="utf-8") as fh:
            rec = json.load(fh)
        rows = rec.get("rows", [])
        bad = [r for r in rows if not r.get("triggered")]
        n_ok = rec.get("n_triggered", 0)
        print(f"nomask_assert: {n_ok}/{rec.get('n_combinations', '?')} 组合触发不可成交断言")
        for r in rows:
            print(f"  {'OK  ' if r.get('triggered') else 'FAIL'} {r.get('arm'):9s} s{r.get('seed')} "
                  f"exit={r.get('exit_code')} {r.get('fields', {}).get('reason', '?')}/"
                  f"{r.get('fields', {}).get('purpose', '?')}")
        if bad or n_ok != rec.get("n_combinations"):
            print("[FAIL] 存在未触发的组合或组合数不符", file=sys.stderr)
            return 1
        print("[PASS] 全部组合触发不可成交断言且退出码非 0")
        return 0

    rows = []
    for arm in ARMS:
        for seed in SEEDS:
            if not os.path.exists(os.path.join(OUT, f"preds_{arm}_s{seed}.npz")):
                print(f"  SKIP {arm} s{seed}（无 preds）", file=sys.stderr)
                continue
            r = run_one(arm, seed)
            rows.append(r)
            print(f"  {'OK  ' if r['triggered'] else 'FAIL'} {arm:9s} s{seed} "
                  f"exit={r['exit_code']} {r['message_line'][:110]}")

    rec = {
        "purpose": "预注册 §7 失败判据第 4 条：刻意关闭可成交掩码必须报错",
        "protocol": {"arms": list(ARMS), "seeds": list(SEEDS),
                     "threads": {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                                 "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}},
        "n_combinations": len(rows),
        "n_triggered": sum(1 for r in rows if r["triggered"]),
        "all_triggered": all(r["triggered"] for r in rows) and len(rows) == len(ARMS) * len(SEEDS),
        "rows": rows,
    }
    with open(REC, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(f"  -> {os.path.relpath(REC, REPO)}  "
          f"{rec['n_triggered']}/{rec['n_combinations']} 组合触发，全部触发={rec['all_triggered']}")
    if not rec["all_triggered"]:
        print("[FAIL] 不可成交断言未覆盖全部组合", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
