#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WORK-555 · 事实层缓存（配图脚本的性能补丁）

问题
----
`scripts/work555_figures.py` 的 `daily_rankic()` 会被调用 9 次
（3 臂 × 2 种子 + 面板 b 的 3 次），每次都用 `csv.reader` 重新解析
95 MB / 750,589 行的 `panel_backtest.csv`。实测**单次解析约 233 秒**，
整轮配图因此要 ~30 分钟；实测有一次跑了 29 分钟仍未产出 fig2。

处置
----
事实层是**只读常量**，解析一次、落盘一次，之后所有进程直接读缓存。
本脚本用 **pandas**（venv 里）做解析——比 `csv` 模块快一个量级；
标签列对非测试段行可能是空串，一律按 **NaN** 落盘（与原「只对测试段行
做 float()」的语义一致：非测试段的空标签不进任何统计）。

输出：experiments/WORK-555/facts_cache.npz
  ts_code   <U9   750,589
  trade_date <U10  750,589
  label     float64（空串 -> NaN）

运行：/tmp/w555venv/bin/python scripts/work555_facts_cache.py
"""
import json
import os
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXP = "WORK-555"
OUT = os.path.join(REPO, "experiments", EXP)
DATA = os.path.join(REPO, "data", EXP)
CACHE = os.path.join(OUT, "facts_cache.npz")


def main():
    t0 = time.time()
    src = os.path.join(DATA, "panel_backtest.csv")
    df = pd.read_csv(src, dtype={"ts_code": "string", "trade_date": "string",
                                 "label": "string"}, usecols=["ts_code", "trade_date", "label"])
    lab = pd.to_numeric(df["label"], errors="coerce").to_numpy(dtype="float64")
    ts = df["ts_code"].fillna("").to_numpy(dtype=str)
    dt = df["trade_date"].fillna("").to_numpy(dtype=str)
    np.savez_compressed(CACHE, ts_code=ts, trade_date=dt, label=lab)
    meta = {
        "cache": CACHE,
        "source": src,
        "n_rows": int(len(df)),
        "n_label_nan": int(np.isnan(lab).sum()),
        "label_nan_note": "非测试段行的空标签（不在任何统计口径内）",
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT, "facts_cache_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
