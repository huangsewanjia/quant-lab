#!/usr/bin/env bash
# WORK-555 · 三档对照臂 x 两种子 的跑数编排（一次性）
# 三段交付顺序：预注册已冻结（commit 9a85409）→ 本脚本跑数 → 独立复算 → 报告
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
LOGDIR=/tmp/w555logs
mkdir -p "$LOGDIR"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

for arm in dee mlp_only xgb; do
  for seed in 42 43; do
    (
      "$PY" scripts/work555_dee.py --arm "$arm" --seed "$seed" \
        > "$LOGDIR/run_${arm}_s${seed}.log" 2>&1
      echo "DONE arm=$arm seed=$seed rc=$? at $(date -Is)" >> "$LOGDIR/progress.txt"
    ) &
  done
done
wait
echo "ALL RUNS FINISHED at $(date -Is)" >> "$LOGDIR/progress.txt"
