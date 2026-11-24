#!/usr/bin/env python3
"""WORK-557 段2 回帖生成器：四档 × 2 seed 全窗训练结果 + 回测指标。

数字全部从产物读，不手抄：
    experiments/WORK-557/training_log.json   训练侧（s/epoch、峰值显存、早停轮次、总秒数）
    experiments/WORK-557/metrics_table.csv   回测侧（RankIC / SR / AR / MDD / 换手）

用法：
    .venv\\Scripts\\python.exe experiments/WORK-557/work557_comment_stage2.py out.md
"""
from __future__ import annotations

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EXP = "WORK-557"
EXP_DIR = os.path.join(REPO, "experiments", EXP)

ARM_CN = {"full": "完整 full", "no_vq": "去码本 no_vq", "no_moe": "去 MoE no_moe",
          "no_prior": "去先验 no_prior", "gru": "GRU 基线"}
ORDER = ["full", "no_vq", "no_moe", "no_prior", "gru"]


def fmt(v, nd=4, dash="-"):
    if v is None or v == "":
        return dash
    if isinstance(v, (int,)) and not isinstance(v, bool):
        return str(v)
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)


def main():
    tlog_path = os.path.join(EXP_DIR, "training_log.json")
    tlog = json.load(open(tlog_path, encoding="utf-8")) if os.path.exists(tlog_path) else {}
    runs = tlog.get("runs", {})
    mtab_path = os.path.join(EXP_DIR, "metrics_table.csv")
    metrics = {}
    if os.path.exists(mtab_path):
        with open(mtab_path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                metrics[(row.get("arm"), row.get("seed"))] = row

    L = []
    P = L.append
    P("## 段2｜四档 × 2 seed 全窗 GPU 训练（2009–2019，≤50 轮）+ 回测")
    P("")
    P("**结论（一句）**：")
    P("")
    P("- 训练协议照预注册 `3b4a7df`：训练窗 **2009-01-01~2019-12-31**、每阶段 **≤50 轮**、"
      "patience **15**（上游值）、设备 `cuda`、种子 42 / 2024，四档 + GRU 基线。")
    P("")
    P("---")
    P("")
    P("### 1. 训练侧：每档每 seed 的 s/epoch、峰值显存、早停轮次、总秒数")
    P("")
    P("来源：`experiments/WORK-557/training_log.json`")
    P("")
    P("| 档 | seed | 训练轮数(实际) | 早停轮次 | stage1 轮数 | s/epoch | 峰值显存(GB) | 总秒数 | 设备 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for arm in ORDER:
        for seed in (42, 2024):
            key = "%s_s%d" % (arm, seed)
            r = runs.get(key)
            if not r:
                continue
            P("| %s | %d | %s | %s | %s | %s | %s | %s | %s |"
              % (ARM_CN.get(arm, arm), seed,
                 fmt(r.get("epochs_ran"), 0), fmt(r.get("early_stopped_epoch"), 0),
                 fmt(r.get("stage1_epochs_ran"), 0),
                 fmt(r.get("mean_seconds_per_epoch"), 1),
                 fmt(r.get("peak_vram_gb"), 2),
                 fmt(r.get("total_seconds"), 0),
                 r.get("device") or "-"))
    P("")
    P("### 2. 回测侧：每档每 seed 的 RankIC / SR / 年化 / MDD / 换手")
    P("")
    P("来源：`experiments/WORK-557/metrics_table.csv`；组合口径 TopK-DropN K=30 N=5 只做多，"
      "成本主档 5/15 bps（WORK-503 注册口径）。")
    P("")
    P("| 档 | seed | RankIC | RankICIR | SR | 年化 | MDD | 单边换手 | 交易日 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for arm in ORDER:
        for seed in (42, 2024):
            m = metrics.get((arm, str(seed)))
            if not m:
                continue
            P("| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
              % (ARM_CN.get(arm, arm), seed,
                 fmt(m.get("rankic")), fmt(m.get("rankicir")), fmt(m.get("sr")),
                 fmt(m.get("ar")), fmt(m.get("mdd")), fmt(m.get("turnover_1side")),
                 fmt(m.get("days"), 0)))
    P("")
    out = "\n".join(L)
    if len(sys.argv) > 1:
        dst = os.path.abspath(sys.argv[1])
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("wrote -> %s (%d chars)" % (dst, len(out)), file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
