#!/usr/bin/env bash
# WORK-555 · 统一代码版本下重跑三档对照臂 x 两种子
# 原因：scripts/work555_dee.py 于 2026-11-24 11:43 修订（族子集 IC 过滤 + per-expert 预测落盘），
#      而 mlp_only 两臂（11:19 启动、11:51 落盘）跑的是 11:43 之前的代码版本，
#      产出 npz 缺 per_expert_pred、family_subsets 未按预注册 §4.6 过滤。
#      为保证「同一份代码版本」可复现，dee 与 mlp_only 两臂各 2 种子全部重跑。
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
LOGDIR=/tmp/w555logs
mkdir -p "$LOGDIR"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

echo "RUNNER START $(date -Is)" >> "$LOGDIR/progress_v2.txt"
for arm in dee mlp_only; do
  for seed in 42 43; do
    (
      "$PY" scripts/work555_dee.py --arm "$arm" --seed "$seed" \
        > "$LOGDIR/v2_run_${arm}_s${seed}.log" 2>&1
      echo "DONE arm=$arm seed=$seed rc=$? at $(date -Is)" >> "$LOGDIR/progress_v2.txt"
    ) &
  done
done
wait
echo "ALL RUNS FINISHED at $(date -Is)" >> "$LOGDIR/progress_v2.txt"
