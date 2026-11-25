#!/usr/bin/env bash
# WORK-566 · Stage 3：组合回测 → 主检验与判词 → 独立复算 → 报告 → 渲染 → 快照 manifest
# 前置：Stage 1（先验/样本）与 Stage 2（训练）已产出 preds_seed{seed}_{arm}.npz
# 每一步失败即停（set -e），不做静默重试。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
SEEDS=${SEEDS:-"42 2024"}
ARMS="s1_only s3_only s1_s2 full r0 r1 r2 r3 fmb"
COSTS="0.0005 0.0015 0.0030"

echo "=== [1/6] 组合回测（各臂 × 种子 × 成本档）==="
for arm in $ARMS; do
  for s in $SEEDS; do
    for c in $COSTS; do
      $PY scripts/work566_backtest.py --arm "$arm" --seed "$s" --cost "$c" > /dev/null
    done
  done
  echo "  臂 $arm 完成"
done

echo "=== [2/6] 主检验量与判词（H1/H2/H3/H4 + 基准调整 alpha + 论文对照表）==="
$PY scripts/work566_primary_test.py --seeds $SEEDS

echo "=== [3/6] 独立复算（audit/，不 import 建模阶段任何函数）==="
$PY audit/recompute_work566.py --seeds $SEEDS

echo "=== [4/6] 单文件 HTML 报告 ==="
$PY scripts/work566_report.py

echo "=== [5/6] 渲染交付件（1440×900 首屏 PNG + A4 打印首页 PDF）==="
$PY scripts/work566_render.py
cp -f figures/WORK-566/first_screen_1440x900.png experiments/WORK-566/preview_first_screen.png
cp -f figures/WORK-566/report_first_page.pdf  experiments/WORK-566/report_print.pdf
cp -f figures/WORK-566/report_full.pdf         experiments/WORK-566/report_print_full.pdf

echo "=== [6/6] 快照 manifest ==="
$PY scripts/work566_snapshot_manifest.py

echo "=== Stage 3 全部完成 ==="
