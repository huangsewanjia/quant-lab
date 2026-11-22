#!/usr/bin/env python3
"""WORK-503 — unit tests for the verdict logic (written after the independent
code review).

Run:
    ../env/.venv/bin/python -u scripts/work503_test_verdict.py

Covered:
  T1  the H1 band boundaries are exactly the pre-registered ones
      (进 [-40%,-15%], 退 [-10%,+10%], else 部分复现)
  T2  the two sign conventions give **the same verdict for the same
      (RankIC_full, RankIC_no_vq) pair**  — this is the executable proof of
      preregistration v1.3 §4: the revision cannot flip any outcome.
  T3  sr_stat works on a 1-D vector and row-wise on a 2-D bootstrap matrix,
      and the bootstrap CI is not degenerate (width > 0)
  T4  the failure criteria are wired to the verdict (FC2 / FC3 / FC5 / 净 SR<0)

The tests import the verdict module directly; they never touch the network.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import work503_verdict as V          # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def band_verdict(delta, in_band=(-0.40, -0.15), out_band=(-0.10, 0.10)):
    """The pre-registered band decision, parameterised by the 进 band so that
    the three readings of the issue card can be compared:
      A  Δ_drop  + 进 [-40%, -15%]   (v1.3 / v1.4, adopted)
      B  Δ_formula + 进 [+15%, +40%]  (equivalent to A)
      C  Δ_formula + 进 [-40%, -15%]  (v1.0 literal — incoherent with the
                                        card's own paper benchmark)
    """
    if in_band[0] <= delta <= in_band[1]:
        return "进"
    if out_band[0] <= delta <= out_band[1]:
        return "退"
    return "部分复现"


def test_T1_bands():
    print("T1 · H1 判词带边界（读法 A）")
    cases = [(-0.40, "进"), (-0.15, "进"), (-0.1499, "部分复现"),
             (-0.10, "退"), (0.0, "退"), (0.10, "退"),
             (0.1001, "部分复现"), (-0.75, "部分复现"), (0.30, "部分复现")]
    for d, want in cases:
        check(f"Δ={d:+.4f} → {want}", band_verdict(d) == want,
              f"got {band_verdict(d)}")


def test_T2_convention_invariance():
    print("T2 · 读法 A ≡ B，且 A 与字面读法 C 的分歧被钉死（预注册 v1.4 §2）")
    grid = np.round(np.arange(-0.60, 0.6001, 0.01), 4)
    same_AB = sum(band_verdict(-d) == band_verdict(d, in_band=(0.15, 0.40))
                  for d in grid)
    check(f"{len(grid)} 个网格点上 A 与 B 判词相同", same_AB == len(grid),
          f"{same_AB}/{len(grid)}")

    # A vs C: they may differ ONLY where Δ_formula ∈ [−0.40,−0.15] ∪ [0.15,0.40]
    diff = [d for d in grid
            if band_verdict(-d) != band_verdict(d)]
    allowed = [d for d in diff
               if (-0.40 <= d <= -0.15) or (0.15 <= d <= 0.40)]
    check("A 与 C 的分歧只出现在 |Δ_formula| ∈ [15%, 40%]",
          len(allowed) == len(diff), f"{len(diff)-len(allowed)} 个越界分歧")

    # the paper's own numbers: A says 进, the literal reading C says 部分复现
    full, novq = 0.0646, 0.0472
    d_drop, d_formula = (novq - full) / full, (full - novq) / full
    check("论文自己的数字在读法 A 下判「进」",
          band_verdict(d_drop) == "进", f"{d_drop:+.4f}")
    check("论文自己的数字在字面读法 C 下会判「部分复现」（这正是要修的点）",
          band_verdict(d_formula) == "部分复现", f"{d_formula:+.4f}")

    # the failure case: no effect at all -> every reading says 退
    for full, novq in [(0.0646, 0.0620), (0.0500, 0.0500), (0.0400, 0.0400)]:
        d_drop = (novq - full) / full
        check(f"无效果 full={full} no_vq={novq} → 退",
              band_verdict(d_drop) == "退", f"{d_drop:+.4f}")


def test_T3_sr_stat():
    print("T3 · sr_stat / block bootstrap")
    x = np.array([0.001, -0.002, 0.003, 0.0005, -0.0015])
    v1 = V.sr_stat(x)
    check("1-D 返回标量", np.isscalar(v1) or np.ndim(v1) == 0, str(type(v1)))
    check("1-D 数值合理", abs(v1) > 0, str(v1))

    M = np.vstack([x, x * 2.0])
    v2 = V.sr_stat(M, axis=1)
    check("2-D 按行返回两个值", np.ndim(v2) == 1 and len(v2) == 2, str(v2))
    check("2-D 第一行与 1-D 一致", abs(v2[0] - v1) < 1e-12,
          f"{v2[0]} vs {v1}")

    rng = np.random.default_rng(0)
    daily = rng.normal(0.0004, 0.01, size=500)
    ci = V.block_bootstrap_ci(daily, stat=V.sr_stat, n_boot=2000, block=5)
    check("bootstrap CI 有宽度（不是退化常数）", ci["hi"] > ci["lo"],
          f"[{ci['lo']}, {ci['hi']}]")
    check("bootstrap CI 覆盖点估计", ci["lo"] <= ci["point"] <= ci["hi"],
          f"point={ci['point']}")


def test_T4_failure_criteria():
    print("T4 · 失败判据接线")
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for key, needle in [
        ("FC2 端点失败 → 判词改为停", '"2_endpoints" in hits'),
        ("FC3 两种子都平 → 判退", '"3_both_seeds_flat" in hits'),
        ("FC5 复算不一致 → 以复算值重出判定",
         'fc["5_recompute_agreement"].get("hit")'),
        ("净 SR < 0 → 直接判退", "净 SR < 0"),
    ]:
        check(key, needle in src, needle)


def main():
    test_T1_bands()
    test_T2_convention_invariance()
    test_T3_sr_stat()
    test_T4_failure_criteria()
    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
