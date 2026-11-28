"""逐列定位：sh600332 全时间轴上，qlib 重建 vs 磁盘 npy 的差异分布在哪一年。

用来区分「日期轴错位」「早期数据缺失」「口径不同」三类原因。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, ".venv", "qlib312"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
DATA = os.path.join(REPO, "data", "WORK-557")
DS = os.path.join(DATA, "ds")


def main():
    import qlib
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D
    qlib.init(provider_uri=os.path.join(DATA, "qlib_cn"), region="cn")
    from work503_build_dataset import robust_norm_fit

    cal = pd.DatetimeIndex(pd.to_datetime(
        pd.read_csv(os.path.join(DS, "calendar.txt"), header=None)[0]))
    pit = pd.read_parquet(os.path.join(DATA, "panel", "pit.parquet")).reindex(index=cal)
    pit_mask = pit.to_numpy(dtype=bool)
    symbols = sorted(pit.columns)
    expr, name = Alpha158.__new__(Alpha158).get_feature_config()
    feat = list(name)
    tr = np.where((cal >= pd.Timestamp("2009-01-01")) & (cal <= pd.Timestamp("2019-12-31")))[0]

    fb = []
    for g0 in range(0, len(symbols), 120):
        g = symbols[g0:g0 + 120]
        raw = D.features(g, expr, start_time="2006-01-01", end_time="2024-12-31")
        raw.columns = feat
        have = set(raw.index.get_level_values("instrument"))
        for sym in g:
            if sym not in have:
                continue
            i = symbols.index(sym)
            sub = raw.xs(sym, level="instrument").reindex(cal).to_numpy("float64")
            fb.append(sub[tr][pit_mask[tr, i]])
    X = np.concatenate(fb, axis=0)
    f_mean, f_std = robust_norm_fit(X)
    print("fit rows", X.shape, "| nan in f_mean", int(np.isnan(f_mean).sum()))

    for sym in ("sh600332", "sz300529"):
        raw = D.features([sym], expr, start_time="2006-01-01", end_time="2024-12-31")
        raw.columns = feat
        sub = raw.xs(sym, level="instrument").reindex(cal).to_numpy("float64")
        norm = np.clip((sub - f_mean) / f_std, -3, 3)
        norm = np.nan_to_num(norm, nan=0.0, posinf=0.0, neginf=0.0)
        arr = np.load(os.path.join(DS, "feat", sym + ".npy")).astype("float64")
        bad = np.abs(norm - arr) > 1e-4
        days_bad = bad.any(axis=1)
        by_year = pd.Series(days_bad, index=cal).groupby(cal.year).agg(["sum", "count"])
        print(f"\n== {sym}: 坏格 {bad.sum()} / {bad.size}；有坏列的天 {int(days_bad.sum())} / {len(cal)}")
        print(by_year.rename(columns={"sum": "bad_days", "count": "days"}).to_string())
        idx = np.where(days_bad)[0]
        if len(idx):
            print("最早坏日", cal[idx[0]].date(), "最晚坏日", cal[idx[-1]].date())
            t = idx[0]
            cols = np.argsort(-np.abs(norm[t] - arr[t]))[:5]
            print("该日 top5:", [(feat[j], round(float(arr[t, j]), 4), round(float(norm[t, j]), 4)) for j in cols])


if __name__ == "__main__":
    main()
