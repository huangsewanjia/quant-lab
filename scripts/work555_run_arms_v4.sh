#!/usr/bin/env bash
# WORK-555 · 第四次跑数（v4）：产物落盘改为 float64（预测无损）
#
# 为什么必须再跑一次（不是只改落盘代码就交付）
#   ----------------------------------------------------
#   v3 的产物 `preds_*.npz` 把预测存成 **float32**，而 `run_*.json` 里的
#   `test_rankic_mean` 是用内存里的 **float64** `pred_te` 算出来的。
#   float32 量化会在截面里偶尔翻转两个几乎相等的预测的秩位，使
#   「审计脚本从落盘产物复算的 RankIC」与「建模侧报告值」出现相对差 ~1e-6 的差：
#
#     dee s43        相对差 2.526e-06  -> 超预注册 §7.2 的 1e-6 判据 -> blocking
#     dee s42        相对差 7.26e-07   -> 侥幸通过
#     mlp_only s42   相对差 0          -> 侥幸通过（恰好没有秩位翻转）
#
#   预注册 §7.2 的处置是「以复算值为准，回退到对应阶段重做」，
#   **不是**放宽审计阈值。处置方式 = 让产物无损：
#   预测按 float64 落盘后，审计复算值与建模侧**逐位相等**（gap = 0）。
#
#   关键性质：**训练轨迹与指标计算完全未动**，只改落盘精度。
#   因此本轮重跑产出的建模侧数值与 v3 逐位相同（可对照验证），
#   变的只是 npz 里的预测精度，以及由此带来的「审计复算 == 建模侧」。
#
# 线程固定：float32 GEMM 在不同 BLAS 线程数下规约顺序不同 -> 结果不可复现。
#   必须 export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1。
#
# 覆盖范围：4 臂 × 2 种子（dee / mlp_only / xgb / xgbfull）全部重跑，
#   保证**所有**落盘预测都是 float64，产物 schema 一致。
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
LOGDIR=/tmp/w555logs
mkdir -p "$LOGDIR"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

PROG="$LOGDIR/progress_v4.txt"
echo "RUNNER V4 START $(date -Is)" >> "$PROG"

# 先起两个最慢的 DEE 臂（它们决定整轮墙钟），其余轻臂随后起，结束后把核让给 DEE
for seed in 42 43; do
  (
    "$PY" scripts/work555_dee.py --arm dee --seed "$seed" \
      > "$LOGDIR/v4_run_dee_s${seed}.log" 2>&1
    echo "DONE arm=dee seed=$seed rc=$? at $(date -Is)" >> "$PROG"
  ) &
done

for seed in 42 43; do
  (
    "$PY" scripts/work555_dee.py --arm mlp_only --seed "$seed" \
      > "$LOGDIR/v4_run_mlp_only_s${seed}.log" 2>&1
    echo "DONE arm=mlp_only seed=$seed rc=$? at $(date -Is)" >> "$PROG"
  ) &
done

for seed in 42 43; do
  (
    "$PY" scripts/work555_dee.py --arm xgb --seed "$seed" \
      > "$LOGDIR/v4_run_xgb_s${seed}.log" 2>&1
    echo "DONE arm=xgb seed=$seed rc=$? at $(date -Is)" >> "$PROG"
  ) &
done

for seed in 42 43; do
  (
    "$PY" scripts/work555_xgb_diag.py --seed "$seed" \
      > "$LOGDIR/v4_run_xgbfull_s${seed}.log" 2>&1
    echo "DONE arm=xgbfull seed=$seed rc=$? at $(date -Is)" >> "$PROG"
  ) &
done

wait
echo "ALL RUNS FINISHED at $(date -Is)" >> "$PROG"
