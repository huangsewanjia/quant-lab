#!/usr/bin/env python3
"""WORK-505 · primary test — ΔSharpe + paired block bootstrap, two-seed merge.

Protocol: experiments/WORK-505/preregistration.md §2 (WORK-449 §9.4 判定词) and
§2.1 (两 seed 合并口径, written for this experiment).

  DeltaSharpe = Sharpe(no_mask) - Sharpe(full_mask)                (primary)
  DeltaIC     = IC(no_mask) - IC(full_mask)                        (corroboration)

  reproduced ("进")  <=> DeltaSharpe < 0 AND the 95% CI excludes 0
  not reproduced ("退") <=> otherwise

Two-seed merged rule (prereg §2.1, quoted):
  the two seeds' daily net-return series are each turned into a Sharpe under
  §8, the two Sharpes are averaged arithmetically; the bootstrap resamples the
  two seeds' PAIRED daily net returns SYNCHRONOUSLY (one set of block starts is
  applied to both seeds and both arms), 1000 times / block 20 / seed 42, and
  reports the 95% percentile CI.

Verdict table (prereg §2.1):
  two seeds same sign          -> verdict from §9.4 on the merged statistics
  two seeds opposite signs     -> "未复现（退）· 结论不稳定" (the sign of the
                                  primary quantity is decided by the training
                                  seed, not by the data => insufficient power)

Reads   experiments/<exp>/metrics_<arm>_<seed>.json   (6 files, written by
       scripts/work449_backtest.py with --tag _<seed>)
Writes  experiments/<exp>/primary_test.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

RF = 0.015
N_BOOT = 1000
BLOCK = 20
SEED = 42
ARMS = ("no_mask", "halt_only", "full_mask")
WORK449_SEEDS = (42, 43)


def sharpe(net: np.ndarray, days: float) -> float:
    cum = np.cumprod(1.0 + net)
    total = float(cum[-1] - 1.0)
    ann = (1.0 + total) ** (365.25 / days) - 1.0
    vol = float(np.std(net, ddof=1) * np.sqrt(252))
    if vol <= 0:
        return float("nan")
    return (ann - RF) / vol


def block_indices(n: int, block: int, nb: int, rng) -> np.ndarray:
    starts = rng.integers(0, nb, size=nb)
    return np.concatenate([np.arange(s * block, (s + 1) * block) for s in starts])


def calendar_days(start: str, end: str) -> int:
    """Calendar days spanned by the test window.

    The backtest annualises the compounded return with 365.25 / *calendar*
    days (scripts/work449_backtest.py::metrics) while the volatility uses
    sqrt(252) trading days.  The primary test MUST use the same denominator,
    otherwise delta_sharpe would not equal
    sharpe(no_mask) - sharpe(full_mask) as reported by the arms.
    """
    return int((np.datetime64(end) - np.datetime64(start)).astype(int))


def load_arms(exp_dir, seeds):
    out = {}
    for s in seeds:
        out[s] = {}
        for arm in ARMS:
            p = os.path.join(exp_dir, f"metrics_{arm}_{s}.json")
            out[s][arm] = json.load(open(p, encoding="utf-8"))
    return out


def per_seed_result(arms, cost_key, rng):
    """DeltaSharpe / CI / DeltaIC for one seed (WORK-449 §9.4 mechanics)."""
    a = np.array(arms["no_mask"][cost_key]["net_returns"])
    b = np.array(arms["full_mask"][cost_key]["net_returns"])
    meta = arms["no_mask"][cost_key]
    n_trading = int(meta["test_days"])
    cal_days = calendar_days(meta["start"], meta["end"])
    d_sharpe = float(sharpe(a, cal_days) - sharpe(b, cal_days))
    d_ic = float(arms["no_mask"][cost_key]["apparent_ic_mean"] -
                 arms["full_mask"][cost_key]["apparent_ic_mean"])
    nb = len(a) // BLOCK
    stats = np.empty(N_BOOT)
    for i in range(N_BOOT):
        sel = block_indices(len(a), BLOCK, nb, rng)
        boot_days = cal_days * len(sel) / n_trading
        stats[i] = sharpe(a[sel], boot_days) - sharpe(b[sel], boot_days)
    lo, hi = np.percentile(stats, [2.5, 97.5])
    excludes_zero = bool(lo > 0 or hi < 0)
    reproduced = bool(d_sharpe < 0 and excludes_zero)
    rep_delta = (float(arms["no_mask"][cost_key]["sharpe_rf1.5"]) -
                 float(arms["full_mask"][cost_key]["sharpe_rf1.5"]))
    return {
        "cost_bps": arms["no_mask"][cost_key]["cost_bps"],
        "delta_sharpe": d_sharpe,
        "delta_sharpe_ci95": [float(lo), float(hi)],
        "ci_excludes_zero": excludes_zero,
        "delta_ic": d_ic,
        "sharpe_no_mask": arms["no_mask"][cost_key]["sharpe_rf1.5"],
        "sharpe_halt_only": arms["halt_only"][cost_key]["sharpe_rf1.5"],
        "sharpe_full_mask": arms["full_mask"][cost_key]["sharpe_rf1.5"],
        "delta_sharpe_vs_reported_sharpes": rep_delta,
        "delta_sharpe_consistency_abs_err": abs(d_sharpe - rep_delta),
        "annualisation": {
            "calendar_days": cal_days,
            "trading_days": n_trading,
            "return_annualiser": "365.25 / calendar_days",
            "vol_annualiser": "sqrt(252) trading days",
        },
        "apparent_ic_no_mask": arms["no_mask"][cost_key]["apparent_ic_mean"],
        "apparent_ic_halt_only": arms["halt_only"][cost_key]["apparent_ic_mean"],
        "apparent_ic_full_mask": arms["full_mask"][cost_key]["apparent_ic_mean"],
        "verdict": "复现成立" if reproduced else "未复现",
        "bootstrap": {"n": N_BOOT, "block": BLOCK, "seed": SEED, "paired": True},
    }


def merged_result(arms_by_seed, cost_key, seeds):
    """Two-seed merged statistics (prereg §2.1).

    The block starts are drawn ONCE per bootstrap iteration and applied to
    every seed and both arms, so the resampling stays paired across seeds and
    across arms.
    """
    net = {s: (np.array(arms_by_seed[s]["no_mask"][cost_key]["net_returns"]),
               np.array(arms_by_seed[s]["full_mask"][cost_key]["net_returns"]))
           for s in seeds}
    # The merged bootstrap applies ONE set of block starts to every seed and
    # both arms, so all series must have the same length; otherwise the
    # resampling would silently mix different test windows.
    lens = {s: len(net[s][0]) for s in seeds}
    lens.update({f"{s}:full": len(net[s][1]) for s in seeds})
    if len(set(lens.values())) != 1:
        raise SystemExit(f"merged bootstrap needs equal-length series, got {lens}")
    meta = arms_by_seed[seeds[0]]["no_mask"][cost_key]
    n_trading = int(meta["test_days"])
    cal_days = calendar_days(meta["start"], meta["end"])
    per_seed_d = {}
    for s in seeds:
        a, b = net[s]
        per_seed_d[s] = float(sharpe(a, cal_days) - sharpe(b, cal_days))
    d_merged = float(np.mean([per_seed_d[s] for s in seeds]))
    rng = np.random.default_rng(SEED)
    nb = len(net[seeds[0]][0]) // BLOCK
    stats = np.empty(N_BOOT)
    for i in range(N_BOOT):
        sel = block_indices(len(net[seeds[0]][0]), BLOCK, nb, rng)
        boot_days = cal_days * len(sel) / n_trading
        vals = []
        for s in seeds:
            a, b = net[s]
            vals.append(sharpe(a[sel], boot_days) - sharpe(b[sel], boot_days))
        stats[i] = float(np.mean(vals))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    excludes_zero = bool(lo > 0 or hi < 0)
    signs = [np.sign(per_seed_d[s]) for s in seeds]
    opposite = bool(len(set(signs)) > 1)
    merged_ok = bool(d_merged < 0 and excludes_zero)
    if opposite:
        verdict = "未复现（退）· 结论不稳定"
    else:
        verdict = "复现成立" if merged_ok else "未复现"
    d_ic = {s: float(arms_by_seed[s]["no_mask"][cost_key]["apparent_ic_mean"] -
                     arms_by_seed[s]["full_mask"][cost_key]["apparent_ic_mean"])
            for s in seeds}
    rep_delta = float(np.mean([
        float(arms_by_seed[s]["no_mask"][cost_key]["sharpe_rf1.5"]) -
        float(arms_by_seed[s]["full_mask"][cost_key]["sharpe_rf1.5"])
        for s in seeds]))
    return {
        "seeds": list(seeds),
        "cost_bps": meta["cost_bps"],
        "delta_sharpe_per_seed": {str(s): per_seed_d[s] for s in seeds},
        "delta_sharpe_merged": d_merged,
        "delta_sharpe_merged_ci95": [float(lo), float(hi)],
        "ci_excludes_zero": excludes_zero,
        "seeds_opposite_sign": opposite,
        "delta_ic_per_seed": {str(s): d_ic[s] for s in seeds},
        "delta_ic_merged": float(np.mean(list(d_ic.values()))),
        "delta_sharpe_vs_reported_sharpes": rep_delta,
        "delta_sharpe_consistency_abs_err": abs(d_merged - rep_delta),
        "annualisation": {
            "calendar_days": cal_days,
            "trading_days": n_trading,
            "return_annualiser": "365.25 / calendar_days",
            "vol_annualiser": "sqrt(252) trading days",
        },
        "verdict": verdict,
        "bootstrap": {"n": N_BOOT, "block": BLOCK, "seed": SEED, "paired": True,
                      "synchronised_across_seeds": True},
    }


def work449_signs(repo):
    """DeltaSharpe signs of WORK-449 (MLP), for the 'did the verdict flip?' question."""
    out = {}
    missing = []
    for s in WORK449_SEEDS:
        p = os.path.join(repo, "experiments", "WORK-449",
                         "primary_test.json" if s == 42 else f"primary_test_s{s}.json")
        if not os.path.exists(p):
            missing.append(p)
            continue
        d = json.load(open(p, encoding="utf-8"))
        m = d.get("main", d)
        out[s] = {"delta_sharpe": m.get("delta_sharpe"),
                  "delta_sharpe_ci95": m.get("delta_sharpe_ci95"),
                  "verdict": m.get("verdict")}
    if missing:
        print("WARNING: WORK-449 comparison incomplete, missing:",
              "; ".join(missing))
    return out or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="WORK-505")
    ap.add_argument("--seeds", default="42,43")
    args = ap.parse_args()
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exp_dir = os.path.join(repo, "experiments", args.exp)
    seeds = [int(x) for x in args.seeds.split(",")]

    arms_by_seed = load_arms(exp_dir, seeds)

    out = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "exp": args.exp,
        "seeds": seeds,
        "primary_cost_bps": 8.0,
        "per_seed": {},
        "rng_note": (
            "同一个 seed 内，主成本档（8 bps）与 +5 bps 敏感性档**共用同一个 numpy "
            "随机流（seed 42）**，因此两档抽到的 block 起点序列**不同**。这与 WORK-449 的 "
            "work449_primary_test.py 是同款写法，不对任一档产生偏向（每档仍是 1000 次、"
            "block 长度 20 的配对重采样给出的 95% 百分位区间），但两档的重采样集合并不相同。"),
    }
    for s in seeds:
        rng = np.random.default_rng(SEED)
        out["per_seed"][str(s)] = {
            "main": per_seed_result(arms_by_seed[s], "main", rng),
            "sensitivity_5bps": per_seed_result(arms_by_seed[s], "sensitivity_5bps", rng),
        }
    out["merged"] = {
        "main": merged_result(arms_by_seed, "main", seeds),
        "sensitivity_5bps": merged_result(arms_by_seed, "sensitivity_5bps", seeds),
    }

    m = out["merged"]["main"]
    out["verdict"] = m["verdict"]
    out["divergence_complete"] = bool(m["delta_ic_merged"] > 0)

    # 'did the verdict flip?' — compare the sign pattern with WORK-449 (MLP)
    w449 = work449_signs(repo)
    if w449:
        w449_signs_set = {np.sign(v["delta_sharpe"]) for v in w449.values()
                          if v["delta_sharpe"] is not None}
        w449_opposite = bool(len(w449_signs_set) > 1)
        out["comparison_work449_mlp"] = {
            "work449_per_seed": {str(k): v for k, v in w449.items()},
            "work449_seeds_opposite_sign": w449_opposite,
            "work505_seeds_opposite_sign": bool(m["seeds_opposite_sign"]),
            "merged_verdict_vs_work449_seed_verdicts": None,
            "reading": None,
        }
        c = out["comparison_work449_mlp"]
        c["scope_note"] = ("只有在本单真实快照上跑出的数字才构成研究结论；"
                           "在合成面板上，本节仅用于验证脚本链路，不可用作任何判词。")
        if w449_opposite and m["seeds_opposite_sign"]:
            c["reading"] = ("两单都是两 seed 异号 ⇒ 符号不确定性**不是**模型容量带来的；"
                            "模型容量不是来源。")
        elif w449_opposite and not m["seeds_opposite_sign"]:
            c["reading"] = ("WORK-449 两 seed 异号、本单两 seed 同号 ⇒ 换模型后符号"
                            "稳定性**改善**了。")
        elif (not w449_opposite) and m["seeds_opposite_sign"]:
            c["reading"] = ("WORK-449 两 seed 同号、本单两 seed 异号 ⇒ 换模型**引入/放大**"
                            "了符号不确定性，这是一条新的发现。")
        else:
            c["reading"] = "两单都是两 seed 同号 ⇒ 符号稳定性与模型无关。"
        # NOTE on the口径 of this boolean: WORK-505's verdict is the MERGED
        # two-seed verdict (prereg §2.1), while WORK-449's is a SINGLE-seed
        # verdict.  The comparison is therefore indicative, not a like-for-like
        # test; the rigorous statement is `reading` above, which compares the
        # SIGN PATTERN of the two seeds.
        if w449_opposite and m["seeds_opposite_sign"]:
            c["merged_verdict_vs_work449_seed_verdicts"] = False
        elif m["verdict"] != "复现成立" and any(
                v.get("verdict") == "复现成立" for v in w449.values()):
            c["merged_verdict_vs_work449_seed_verdicts"] = True
        else:
            c["merged_verdict_vs_work449_seed_verdicts"] = None
        c["verdict_flip_note"] = ("本单是两 seed 合并判词，WORK-449 是单 seed 判词；"
                                  "口径不同，此布尔值仅供定位，严谨结论看 reading。")

    path = os.path.join(exp_dir, "primary_test.json")
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out, indent=1))
    print("written:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
