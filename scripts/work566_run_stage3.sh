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

echo "=== [0/7] 前置检查：训练产物齐备性（缺产物直接停，不空跑 54 次回测）==="
# 失败即停（set -e）：任何种子/臂的 preds npz 缺失就立刻报错退出
for s in $SEEDS; do
  for arm in s1_only s3_only s1_s2 full r0 r1 r2 r3; do
    p="data/WORK-566/preds_seed${s}_${arm}.npz"
    if [ ! -s "$p" ]; then
      echo "缺少训练产物：$p —— Stage 3 不启动" >&2
      exit 3
    fi
  done
done
echo "  训练产物齐备：$(echo $SEEDS | wc -w) 种子 × 8 臂"

# FMB 基线臂的产物由 work566_baselines.py 产出（**不是**训练脚本产出）——
# 回测对任意 --arm 都要求 preds_seed{seed}_{arm}.npz，漏掉 fmb 会让回测在
# 跑到 fmb 时才崩（2026-11-25 第 3 轮复审 B4）。缺失就在这里补齐。
for s in $SEEDS; do
  p="data/WORK-566/preds_seed${s}_fmb.npz"
  if [ ! -s "$p" ]; then
    echo "  补齐 FMB 基线产物：$p"
    $PY scripts/work566_baselines.py --seed "$s"
  fi
  if [ ! -s "$p" ]; then
    echo "FMB 基线产物生成失败：$p —— Stage 3 不启动" >&2
    exit 4
  fi
done
echo "  FMB 基线产物齐备"

echo "=== [1/7] 组合回测（各臂 × 种子 × 成本档）==="
# 54 次回测串行约 1 小时（每次 ~65s）；回测是单线程 numpy + 读小面板，
# 用 JOBS 路并行（默认 3 路，产物按臂/种子/成本分文件，互不冲突）
JOBS=${JOBS:-3}
# jobfile 放 data/WORK-566/（gitignored）：/tmp 配额被同机其他 agent 占满，不再依赖 /tmp
mkdir -p data/WORK-566
JOBFILE="data/WORK-566/bt_jobs.txt"
: > "$JOBFILE"     # 先截断：重跑时不要把上次的 job 追加进来（否则任务翻倍）
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
