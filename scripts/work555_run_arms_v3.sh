#!/usr/bin/env bash
# WORK-555 · 第三次跑数：修复 S2（早停监控量改为预注册 §4.1/§4.3 写死的「集成级验证 MSE」）
#
# 为什么必须重跑（不是只改日志）：
#   训练轨迹本身不变（梯度更新不依赖监控量），但 **恢复哪个参数快照由监控量决定**：
#   `ex.opt.restore(best_snap[k])` 用的是 vloss 最优步的快照。
#   旧实现监控 mean_k MSE(f_k, y)（逐专家损失的平均），预注册写死的是
#   MSE(mean_k f_k(x), y)（集成输出对标签的损失）。
#   两种口径的 argmin 步在 8000 步训练里几乎不可能落在同一步
#   （旧 run: dee best_step=7740/8000、mlp_only best_step=7580/7820），
#   恢复的权重不同 -> 测试段预测不同 -> RankIC/Δ_ic/判词都可能变。
#   故 dee 与 mlp_only 各 2 种子全部重跑，保证「同一份代码版本」。
#
# 线程固定：float32 GEMM 在不同 BLAS 线程数下规约顺序不同 -> 结果不可复现。
#   必须 export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1。
#
# XGBoost 臂不走 train_ensemble（自带 sklearn 早停），不受本次改动影响，无需重跑。
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
LOGDIR=/tmp/w555logs
mkdir -p "$LOGDIR"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

PROG="$LOGDIR/progress_v3.txt"
echo "RUNNER V3 START $(date -Is)" >> "$PROG"
for arm in dee mlp_only; do
  for seed in 42 43; do
    (
      "$PY" scripts/work555_dee.py --arm "$arm" --seed "$seed" \
        > "$LOGDIR/v3_run_${arm}_s${seed}.log" 2>&1
      echo "DONE arm=$arm seed=$seed rc=$? at $(date -Is)" >> "$PROG"
    ) &
  done
done
wait
echo "ALL RUNS FINISHED at $(date -Is)" >> "$PROG"
