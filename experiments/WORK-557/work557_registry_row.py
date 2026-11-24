#!/usr/bin/env python3
"""WORK-557：把本实验登记进 experiments/registry.md 主表（段1 之后先占一行，段4 出结果后更新）。

规则（照 registry.md「填表规则」）：
- 一行一实验；失败留痕不写空；未取到的写「未取到」+ 原因；数字只抄 artifacts。
- 逐年缺失率区间由 data_probe.json 算出，不手抄。

用法：
    python experiments/WORK-557/work557_registry_row.py          # 插入/更新 WORK-557 行
    python experiments/WORK-557/work557_registry_row.py --dry    # 只打印行文本
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
REG = os.path.join(REPO, "experiments", "registry.md")
PROBE = os.path.join(HERE, "data_probe.json")

ROW_ID = "`WORK-557`"


def build_row():
    probe = json.load(open(PROBE, encoding="utf-8"))
    miss = probe["missing_rate"]["daily"]["missing_rate_by_year"]
    train_years = [str(y) for y in range(2009, 2020)]
    lo = min(miss[y] for y in train_years)
    hi = max(miss[y] for y in train_years)
    pit = probe["pit_by_year"]
    act = {r["year"]: r["active_mean"] for r in pit}

    cols = [
        ROW_ID,
        "2026-11-24",
        "复现 PRISM-VQ（arXiv:2605.13407v1，官方仓库 `finxlab/PRISM-VQ`，MIT）的**离散码本增量**："
        "把 `WORK-503` 的「本机无 GPU → 训练窗压到 2015–2019 + 每阶段 ≤6 轮」妥协**撤掉**，"
        "在 RTX 3060 上按论文口径**全窗重跑**（训练 2009–2019、每阶段 ≤50 轮）",
        "数据截止点：同 `WORK-503`（测试段 2022-01-04~2024-12-31）；库源 `Z:\\quantdb`（只读，"
        "CSI300 PIT 成分取自 `index_weight` 按生效日向后对齐）",
        "`experiments/WORK-557/preregistration.md`（预注册 commit `3b4a7df`，**早于任何结果产物**）"
        "+ `experiments/WORK-557/data_probe.json`（PIT 逐年 active 数 + member-day 缺失率）",
        "训练窗 **2009-01-01~2019-12-31**（论文/上游 `configs/config.yaml` 的 `data.train_period`）；"
        "每阶段 **≤50 轮**；patience **15**（上游值；撤掉 `WORK-503` 因压轮数而收紧到 3 的妥协）；"
        "四档消融（完整 / 去码本 / 去 MoE / 去先验）× 种子 42 / 2024 + GRU 基线；"
        "组合 TopK-DropN K=30 N=5 只做多；成本主档 5/15 bps（`WORK-503` 注册口径）+ 8 / 30 bps"
        "（派单点名要看的敏感档）；特征 Alpha158 + RobustZScoreNorm+Fillna + 6 个自建滞后先验；"
        "标签 `open(t+1)→close(t+k)`，k=5；设备 `cuda`；`WORK-503` 其余口径**逐项照抄**",
        "**段1 已交付**：环境/数据自检通过（RTX 3060 12 GB、torch 2.11.0+cu128、"
        "`cuda.is_available()=True`；CSI300 PIT 逐年 active %.1f~%.1f，2009–2019 member-day "
        "缺失率 %.2f%%~%.2f%% 且经核为**停牌**非数据缺口）；**停手条件①（2009–2013 缺口 >5%%）未触发**"
        "（最差 2.92%%）。段2–4（四档 × 2 seed GPU 训练 + 回测、H1/H2/H3 主检验 + 两张对照表、"
        "独立复算 + 新报告）**未取到**：本 run 按派单方《排队纪律》只做段1，GPU 被 `WORK-538` 占用"
        "（显存 3341 MiB > 3 GB 阈值），训练未启动"
        % (min(act[y] for y in range(2009, 2025)),
           max(act[y] for y in range(2009, 2025)), 100 * lo, 100 * hi),
        "**进行中**（段1 已交付；段2–4 待 `WORK-538` 结束后由派单方在本单通知后开工）",
        "① **pyqlib 0.9.7 无 cp313 wheel**（官方仅 cp38–cp312），本机训练解释器 py3.13.15 → "
        "用 wheel 内自带 `.pyx` 逐行转写纯 numpy 替身，与真 cp39 `.pyd` 对拍 max_abs_diff = "
        "**0.000e+00**（位级一致），已在预注册 §5 与报告中显式披露<br>"
        "② **SMB 上 duckdb glob 扫 5000+ 文件卡死**（实测 60 s 读计数增量 0）→ 改为并行把 "
        "`Z:\\quantdb\\raw` 拷到本地 `data/WORK-557/cache/raw_local/` 后本地跑 SQL；拷贝脚本**可续跑**<br>"
        "③ **WORK-503 的数据快照本机不存在**（`data/` 为空、`pool_membership.csv` 被 gitignore）→ "
        "本轮输入全部由 `Z:\\quantdb` 经适配层 `scripts/work557_fetch_panel.py` 重建为 WORK-503 兼容布局；"
        "**WORK-503 产物只读、未改动**<br>"
        "④ **`stk_limit` / `suspend_d` 两表本地副本未拷完**（不影响主链路："
        "`work503_build_panel.py` 自价格带推导涨跌停板、以「无行 / volume==0」判停牌），"
        "到位后补一次一致性交叉核对",
    ]
    return "| " + " | ".join(cols) + " |"


def main():
    row = build_row()
    if "--dry" in sys.argv:
        print(row)
        return
    with io.open(REG, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    # 找主表里的模板行，插在它前面
    idx = next((i for i, l in enumerate(lines)
                if l.startswith("| `（模板行）`")), None)
    if idx is None:
        raise SystemExit("registry.md: 未找到模板行，未改动")
    # 若已有 WORK-557 行则替换
    existing = [i for i, l in enumerate(lines) if l.startswith("| " + ROW_ID + " |")]
    if existing:
        lines[existing[0]] = row
        print("replaced existing row at line %d" % (existing[0] + 1))
    else:
        lines.insert(idx, row)
        print("inserted row at line %d" % (idx + 1))
    with io.open(REG, "w", encoding="utf-8", newline="") as fh:
        fh.write(nl.join(lines))
    print("wrote -> %s" % REG)


if __name__ == "__main__":
    main()
