#!/usr/bin/env bash
# WORK-566 · Stage 2 全链驱动（先验 → 线性基线 → 表征/读出 → 回测 → 主检验 → 独立复算 → 报告）
# 每一步失败即停（set -e），不做静默重试。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=../.venv/bin/python
SEEDS=${SEEDS:-"42 2024"}
ARMS="s1_only s3_only s1_s2 full r0 r1 r2 r3 fmb"
COSTS="0.0005 0.0015 0.0030"

echo "=== [1/6] S1 先验 + 样本构造 ==="
$PY scripts/work566_prior.py

echo "=== [2/6] FMB-style 线性基线（最便宜的口径校验）==="
for s in $SEEDS; do $PY scripts/work566_baselines.py --seed "$s"; done

echo "=== [3/6] S2 表征 + S3 读出 ==="
$PY scripts/work566_train.py --seeds $SEEDS

echo "=== [4/6] 组合回测（各臂 × 种子 × 成本档）==="
for arm in $ARMS; do
  for s in $SEEDS; do
    for c in $COSTS; do
      $PY scripts/work566_backtest.py --arm "$arm" --seed "$s" --cost "$c" > /dev/null
    done
  done
  echo "  臂 $arm 完成"
done

echo "=== [5/6] 主检验量与判词 ==="
$PY scripts/work566_primary_test.py --seeds $SEEDS

echo "=== [6/6] 独立复算 + 报告 ==="
$PY audit/recompute_work566.py --seeds $SEEDS
$PY scripts/work566_report.py

echo "=== 全部完成 ==="
