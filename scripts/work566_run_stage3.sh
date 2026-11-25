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

echo "=== [1/7] 组合回测（各臂 × 种子 × 成本档）==="
# 54 次回测串行约 1 小时（每次 ~65s）；回测是单线程 numpy + 读小面板，
# 用 JOBS 路并行（默认 3 路，产物按臂/种子/成本分文件，互不冲突）
JOBS=${JOBS:-3}
JOBFILE=$(mktemp /tmp/w566_bt_jobs.XXXXXX)
for arm in $ARMS; do
  for s in $SEEDS; do
    for c in $COSTS; do
      echo "$arm $s $c" >> "$JOBFILE"
    done
  done
done
export PY
xargs -P "$JOBS" -n 3 bash -c '
  arm=$1; s=$2; c=$3
  "$PY" scripts/work566_backtest.py --arm "$arm" --seed "$s" --cost "$c" > /dev/null
  echo "  完成 arm=$arm seed=$s cost=$c"
' _ < "$JOBFILE"
rm -f "$JOBFILE"
echo "  回测全部完成"

echo "=== [2/7] 主检验量与判词（H1/H2/H3/H4 + 基准调整 alpha + 论文对照表）==="
$PY scripts/work566_primary_test.py --seeds $SEEDS

echo "=== [3/7] 独立复算（audit/，不 import 建模阶段任何函数）==="
$PY audit/recompute_work566.py --seeds $SEEDS

echo "=== [4/7] 单文件 HTML 报告 ==="
$PY scripts/work566_report.py

echo "=== [5/7] 渲染交付件（1440×900 首屏 PNG + A4 打印首页 PDF）==="
$PY scripts/work566_render.py
cp -f figures/WORK-566/first_screen_1440x900.png experiments/WORK-566/preview_first_screen.png
cp -f figures/WORK-566/report_first_page.pdf  experiments/WORK-566/report_print.pdf
cp -f figures/WORK-566/report_full.pdf         experiments/WORK-566/report_print_full.pdf

echo "=== [6/7] 快照 manifest ==="
$PY scripts/work566_snapshot_manifest.py

echo "=== [7/7] 交付物自检（齐备性 + 单文件约束 + 判词口径一致性）==="
$PY scripts/work566_delivery_check.py

echo "=== Stage 3 全部完成 ==="
