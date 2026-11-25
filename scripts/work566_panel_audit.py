#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WORK-566 面板数据质量审计：逐维 NaN/分布统计 + 目标缺失 + 连续性抽查 + bar 覆盖。

产出 experiments/WORK-566/panel_audit.json
实现约束：全程 float32、分块处理，避免把 3.1GB 面板转成 float64 常驻内存。
"""
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "WORK-566")
OUT = os.path.join(ROOT, "experiments", "WORK-566")

meta = json.load(open(os.path.join(DATA, "meta.json"), encoding="utf-8"))
NS, ND = meta["shape"]["stocks"], meta["shape"]["dates"]
FN = meta["feat_names"]
TN = meta["target_names"]
NF = len(FN)

feat = np.load(os.path.join(DATA, "feat.npy"), mmap_mode="r")
tgt = np.load(os.path.join(DATA, "targets.npy"), mmap_mode="r")
has_bar = np.load(os.path.join(DATA, "base", "has_bar.npy"), mmap_mode="r")

TOT = NS * ND
CH = 96

# ---------------------------------------------------------------- 逐维统计
nan_cnt = np.zeros(NF, dtype=np.int64)
n_fin = np.zeros(NF, dtype=np.int64)
s1 = np.zeros(NF, dtype=np.float64)
s2 = np.zeros(NF, dtype=np.float64)
fmin = np.full(NF, np.inf, dtype=np.float64)
fmax = np.full(NF, -np.inf, dtype=np.float64)

for t0 in range(0, ND, CH):
    t1 = min(t0 + CH, ND)
    blk = np.asarray(feat[:, t0:t1, :])                 # float32 副本
    fin = np.isfinite(blk)
    nf = fin.sum(axis=(0, 1)).astype(np.int64)
    n_fin += nf
    nan_cnt += (blk.shape[0] * blk.shape[1]) - nf
    z = np.where(fin, blk, np.float32(0.0))
    s1 += z.sum(axis=(0, 1), dtype=np.float64)
    s2 += np.einsum("ijk,ijk->k", z, z, dtype=np.float64)
    big = np.where(fin, blk, np.float32(np.inf))
    small = np.where(fin, blk, np.float32(-np.inf))
    fmin = np.minimum(fmin, big.min(axis=(0, 1)))
    fmax = np.maximum(fmax, small.max(axis=(0, 1)))
    del blk, fin, z, big, small

dims = []
for i, nm in enumerate(FN):
    nf = int(n_fin[i])
    mu = s1[i] / nf if nf else float("nan")
    var = max(s2[i] / nf - mu * mu, 0.0) if nf else float("nan")
    dims.append({
        "idx": i, "name": nm,
        "nan_ratio": round(float(nan_cnt[i]) / TOT, 8),
        "n_nonfinite": int(nan_cnt[i]),
        "mean": round(float(mu), 6) if nf else None,
        "std": round(float(np.sqrt(var)), 6) if nf else None,
        "min": round(float(fmin[i]), 6) if np.isfinite(fmin[i]) else None,
        "max": round(float(fmax[i]), 6) if np.isfinite(fmax[i]) else None,
    })

# ---------------------------------------------------------------- 目标统计
tgt_nan = np.zeros(len(TN), dtype=np.int64)
tgt_nfin = np.zeros(len(TN), dtype=np.int64)
for t0 in range(0, ND, CH):
    t1 = min(t0 + CH, ND)
    blk = np.asarray(tgt[:, t0:t1, :])
    fin = np.isfinite(blk)
    tgt_nfin += fin.sum(axis=(0, 1))
    tgt_nan += (blk.shape[0] * blk.shape[1]) - fin.sum(axis=(0, 1))
    del blk, fin
targets = [{"idx": i, "name": nm, "nan_ratio": round(float(tgt_nan[i]) / TOT, 8),
            "n_nonfinite": int(tgt_nan[i])} for i, nm in enumerate(TN)]

# ---------------------------------------------------------------- bar 覆盖 + 连续性
hb = np.asarray(has_bar)
bar_by_date = hb.sum(axis=0)
bc = bar_by_date[bar_by_date > 0]
pct = np.percentile(bc, [0, 1, 5, 50, 95, 99, 100]) if bc.size else np.array([])

import duckdb
con = duckdb.connect()
con.execute("PRAGMA threads=4")
cal = con.execute(
    "select distinct cast(strftime(cal_date,'%Y%m%d') as int) as cd from "
    "read_parquet('/srv/share/quantdb/raw/trade_cal/**/*.parquet', hive_partitioning=true, "
    "union_by_name=true) where exchange='SSE' and is_open=1 "
    "and cal_date between date '2013-01-01' and date '2026-06-26' order by cd").df()["cd"].to_numpy(np.int64)
con.close()
cal_pos = {int(c): i for i, c in enumerate(cal)}

TEST_LO, TEST_HI = 20240102, 20260626
test_pos = np.array([i for i, c in enumerate(cal) if TEST_LO <= c <= TEST_HI])
test_bars = bar_by_date[test_pos]
cov = float((test_bars > 0).mean())

rng = np.random.default_rng(20241125)
checks = []
for _ in range(20):
    si = int(rng.integers(0, NS))
    ti = int(rng.integers(147, ND))
    need = np.arange(ti - 125, ti + 22)   # [t-125, t+21]，与全量口径一致
    present = int(hb[si, need].sum())
    checks.append({"stock": si, "date_idx": ti, "date": int(cal[ti]),
                   "need": int(len(need)), "with_real_bar": present,
                   "ok": present == len(need)})
n_ok = sum(c["ok"] for c in checks)

# 连续性全量统计（与 work566_prior.py 的断言同口径，供交叉核对）
csum = np.zeros((NS, ND + 1), dtype=np.int32)
acc = np.asarray(hb).view(np.int8).astype(np.int32)
csum[:, 1:] = np.cumsum(acc, axis=1)
idx = np.arange(ND)
lo_i = np.clip(idx - 125, 0, ND)
hi_i = np.clip(idx + 22, 0, ND)
cnt = csum[:, hi_i] - csum[:, lo_i]
valid = (cnt == 147) & (idx[None, :] >= 125)
n_valid = int(valid.sum())
del csum, acc, cnt

res = {
    "panel": {"shape": [NS, ND, NF], "n_dates": ND, "n_stocks": NS,
              "window": meta["window"], "built_at": meta["built_at"],
              "adjust": meta["adjust"]},
    "dims": dims,
    "targets": targets,
    "has_bar": {
        "total_bar_cells": int(hb.sum()),
        "dates_with_zero_bar": int((bar_by_date == 0).sum()),
        "bars_per_date_min": int(bc.min()) if bc.size else 0,
        "bars_per_date_pct": {str(p): int(v) for p, v in
                              zip([0, 1, 5, 50, 95, 99, 100], pct)} if bc.size else {},
    },
    "test_window_bar_coverage": {
        "window": [TEST_LO, TEST_HI], "n_dates": int(len(test_pos)),
        "frac_dates_with_bar": round(cov, 6),
        "mean_bars_per_date": round(float(test_bars.mean()), 1),
        "min_bars_per_date": int(test_bars.min()),
    },
    "continuity": {
        "rule": "窗口 [t-125, t+21] 共 147 个交易日每天都要有真实 bar",
        "n_cells_total": int(NS * ND),
        "n_cells_valid": n_valid,
        "valid_ratio": round(n_valid / (NS * ND), 6),
        "spot_check": {"n": len(checks), "n_ok": n_ok, "all_ok": n_ok == len(checks),
                       "samples": checks[:6]},
    },
    "feature_contract_dim": NF,
    "contract_match": NF == 40,
}
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "panel_audit.json"), "w", encoding="utf-8") as fh:
    json.dump(res, fh, ensure_ascii=False, indent=2)

print(f"dims={NF} contract_match={res['contract_match']}")
print("--- 非有限比例 >0 的维度 ---")
for d in dims:
    if d["nan_ratio"] > 0:
        print(f"  {d['idx']:2d} {d['name']:<16} nan_ratio={d['nan_ratio']:.6f} n={d['n_nonfinite']}")
print("--- 目标 ---")
for t in targets:
    print(f"  {t['idx']} {t['name']:<6} nan_ratio={t['nan_ratio']:.6f}")
print("--- has_bar ---")
print(" ", res["has_bar"])
print("--- 测试窗 bar 覆盖 ---")
print(" ", res["test_window_bar_coverage"])
print(f"--- 连续性: 全量有效 {n_valid:,} / {NS*ND:,} ({res['continuity']['valid_ratio']:.4%})；"
      f"抽查 {n_ok}/{len(checks)}")
print("written:", os.path.join(OUT, "panel_audit.json"))
