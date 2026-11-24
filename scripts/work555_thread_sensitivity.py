#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · BLAS 线程数的可复现性实测（证据脚本）

背景
----
独立代码审查第一轮指出：单臂复现命令没有固定 BLAS 线程数，
而 float32 GEMM 的归约顺序随线程数变化 -> 同命令同 seed 的结果不可复现。
第二轮审查方用「单个 forward GEMM 形状」的探针**没能复现**该差异，
于是建议把「float32 GEMM 归约顺序随线程数变化」从已确证根因里降级。

本脚本就是把这个争议一次性钉死的受控实验：

  · 同一份代码、同一 seed、同一数据、同一 60 步训练任务，
    分别在 OMP/OPENBLAS/MKL 线程数 = 1 与 = 4 下各跑一遍；
  · 逐字段比对两次的 bench 产物（训练损失曲线、验证损失曲线、最优快照损失）；
  · 只允许 `elapsed_sec` 不同（墙钟时间）。

安全约束
--------
本脚本**不写被审仓库**：它在 /tmp 下自建一个沙箱（脚本副本 + 只读符号链接的
sample.npz / sample_meta.json），训练产物落在沙箱里。

运行：
  /tmp/w555venv/bin/python scripts/work555_thread_sensitivity.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "experiments", "WORK-555")
PY = "/tmp/w555venv/bin/python"
ARM = "mlp_only"
SEED = 42
STEPS = 60


def run_bench(sandbox, threads):
    env = dict(os.environ)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS"):
        env[k] = str(threads)
    cmd = [PY, os.path.join(sandbox, "scripts", "work555_dee.py"),
           "--arm", ARM, "--seed", str(SEED), "--bench", str(STEPS)]
    r = subprocess.run(cmd, cwd=sandbox, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit(f"bench(T={threads}) 失败 rc={r.returncode}")
    p = os.path.join(sandbox, "experiments", "WORK-555", f"bench_{ARM}_s{SEED}.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def strip(d):
    """去掉墙钟字段，只留数值结果。"""
    d = dict(d)
    d.pop("elapsed_sec", None)
    tr = dict(d.get("train") or {})
    tr.pop("elapsed_sec", None)
    d["train"] = tr
    return d


def main():
    sb = tempfile.mkdtemp(prefix="w555_thread_")
    try:
        os.makedirs(os.path.join(sb, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(sb, "experiments", "WORK-555"), exist_ok=True)
        shutil.copy(os.path.join(HERE, "work555_dee.py"),
                    os.path.join(sb, "scripts", "work555_dee.py"))
        for fn in ("sample.npz", "sample_meta.json"):
            src = os.path.join(OUT, fn)
            if os.path.exists(src):
                os.symlink(src, os.path.join(sb, "experiments", "WORK-555", fn))

        a = run_bench(sb, 1)
        b = run_bench(sb, 4)
        sa, sb_ = strip(a), strip(b)
        diff = [k for k in sa
                if json.dumps(sa[k], sort_keys=True, default=str)
                != json.dumps(sb_.get(k), sort_keys=True, default=str)]
        tail_a = (sa.get("val_loss_tail") or [{}])[0]
        tail_b = (sb_.get("val_loss_tail") or [{}])[0]
        best_a = (sa.get("train") or {}).get("best_ens_val_mse")
        best_b = (sb_.get("train") or {}).get("best_ens_val_mse")

        print("=" * 78)
        print(f"线程敏感性实测：同代码 / 同 seed({SEED}) / 同 {STEPS} 步训练，线程数 1 vs 4")
        print("=" * 78)
        print(f"沙箱: {sb}")
        print(f"不同的顶层字段（除 elapsed_sec 外）: {diff or '无'}")
        print(f"验证损失曲线首条  T=1: {tail_a.get('ens_val_mse')!r}")
        print(f"验证损失曲线首条  T=4: {tail_b.get('ens_val_mse')!r}")
        print(f"逐条相同? {tail_a.get('ens_val_mse') == tail_b.get('ens_val_mse')}")
        print(f"最优快照集成级验证损失  T=1: {best_a!r}")
        print(f"最优快照集成级验证损失  T=4: {best_b!r}")
        rel = (abs(best_a - best_b) / abs(best_a)) if (best_a and best_b) else float("nan")
        print(f"相对差: {rel:.3e}")
        print()
        if diff:
            print("RESULT: PASS —— 线程数会改变训练数值结果，**固定线程是复现前置条件**。")
            print("注：第二轮审查方用「单个 forward GEMM 形状」的探针未复现，")
            print("    该探针覆盖不到本脚本这条路径（前后向多个 GEMM + float32 累加）。")
            return 0
        print("RESULT: 不一致 —— 本机在该配置下未复现线程敏感性；"
              "固定线程仍保留（无害），但不应写成已确证根因。")
        return 1
    finally:
        shutil.rmtree(sb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
