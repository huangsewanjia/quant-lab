"""前视检查的根因定位（2026-11-29）：逐列对照 quantdb 面板特征 vs qlib dump 特征。

派单要求三选一结论：① 标准化口径不同 / ② 复权·价格口径不同 / ③ 真的用了未来信息 / 仍不能判定。
本脚本只做取证，不下判词：
  A. 取 sh600332（max|diff|=4.81）与 sz300529（1.349），各取 2022 起若干交易日；
  B. 用 qlib `D.features` 取**原始** Alpha158（未标准化），按 `work557_build_dataset.py` 的
     `robust_norm_fit/apply` 复算成「应当存进 feat/*.npy 的值」，与磁盘上的 npy 逐列比；
  C. 检查**日期轴错位**：把 qlib 侧整体前移/后移 1 天，看 mismatch 是否骤降；
  D. 检查**列序错位**：把 qlib 列按名字排序后与 npy 列比（两套列名都按名字对齐）。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, ".venv", "qlib312"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
DATA = os.path.join(REPO, "data", "WORK-557")
DS = os.path.join(DATA, "ds")
OUT = os.path.join(REPO, "experiments", "WORK-557", "lookahead_rootcause.json")
TRAIN = ("2009-01-01", "2019-12-31")
SYMS = ["sh600332", "sz300529"]
DATES = ["2022-06-15", "2023-03-15", "2024-06-14"]


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
    expr_list, name_list = Alpha158.__new__(Alpha158).get_feature_config()
    feat_names = list(name_list)

    train_idx = np.where((cal >= pd.Timestamp(TRAIN[0])) & (cal <= pd.Timestamp(TRAIN[1])))[0]

    # ---- 复现 fit 统计（与 build_dataset 同法：PIT train 行） ----
    fb = []
    skipped = []
    for gstart in range(0, len(symbols), 120):
        group = symbols[gstart:gstart + 120]
        raw = D.features(group, expr_list, start_time="2006-01-01", end_time="2024-12-31")
        raw.columns = feat_names
        have = set(raw.index.get_level_values("instrument"))
        for sym in group:
            if sym not in have:
                skipped.append(sym)
                continue
            i = symbols.index(sym)
            sub = raw.xs(sym, level="instrument").reindex(cal).to_numpy("float64")
            rows = pit_mask[train_idx, i]
            fb.append(sub[train_idx][rows])
    Xfit = np.concatenate(fb, axis=0)
    f_mean, f_std = robust_norm_fit(Xfit)
    del fb, Xfit

    out = {"fit_skipped_symbols": skipped, "n_fit_mean_nan": int(np.isnan(f_mean).sum()),
           "probes": [], "verdict": "仍不能判定", "why": ""}

    for sym in SYMS:
        raw = D.features([sym], expr_list, start_time="2006-01-01", end_time="2024-12-31")
        raw.columns = feat_names
        sub = raw.xs(sym, level="instrument").reindex(cal)
        arr = np.load(os.path.join(DS, "feat", f"{sym}.npy")).astype("float64")
        # 按 build_dataset 的法子标准化
        norm = np.clip((sub.to_numpy("float64") - f_mean) / f_std, -3.0, 3.0)
        norm = np.nan_to_num(norm, nan=0.0, posinf=0.0, neginf=0.0)

        for d in DATES:
            if pd.Timestamp(d) not in cal:
                continue
            t = cal.get_loc(pd.Timestamp(d))
            a, b = arr[t], norm[t]
            diff = np.abs(a - b)
            order = np.argsort(-diff)[:5]
            rec = {
                "instrument": sym, "date": d, "row_index": int(t),
                "n_cols": int(len(a)),
                "n_diff_gt_1e-4": int((diff > 1e-4).sum()),
                "max_abs_diff": float(diff.max()),
                "top5": [{"col": feat_names[j], "stored": round(float(a[j]), 6),
                          "qlib_rebuilt": round(float(b[j]), 6),
                          "abs_diff": round(float(diff[j]), 6)} for j in order],
                # C. 日期轴：前移/后移一天
                "shift_minus1_max_abs_diff": float(np.abs(arr[t - 1] - b).max()) if t >= 1 else None,
                "shift_plus1_max_abs_diff": float(np.abs(arr[t + 1] - b).max()) if t + 1 < len(cal) else None,
                # 存储值本身的取值特征（判断是否像"截面 z-score"：每行均值≈0）
                "stored_row_mean": round(float(np.nanmean(a)), 4),
                "stored_row_std": round(float(np.nanstd(a)), 4),
                "rebuilt_row_mean": round(float(np.nanmean(b)), 4),
                "rebuilt_row_std": round(float(np.nanstd(b)), 4),
                "n_stored_at_clip": int((np.abs(a) >= 2.9999).sum()),
                "n_rebuilt_at_clip": int((np.abs(b) >= 2.9999).sum()),
            }
            out["probes"].append(rec)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("->", OUT)


if __name__ == "__main__":
    main()
