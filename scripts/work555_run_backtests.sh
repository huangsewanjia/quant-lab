#!/usr/bin/env bash
# WORK-555 · 组合回测编排（全部臂 × 两种子 × 两档成本）
# 用法：bash scripts/work555_run_backtests.sh
set -u
cd /srv/share/quantdb/code/quant-lab
PY=/tmp/w555venv/bin/python
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

for arm in dee mlp_only xgb xgbfull; do
  for seed in 42 43; do
    if [ -f "experiments/WORK-555/preds_${arm}_s${seed}.npz" ]; then
      echo "== backtest arm=$arm seed=$seed $(date -Is)"
      "$PY" scripts/work555_backtest.py --arm "$arm" --seed "$seed" 2>&1 | tail -3
    else
      echo "== skip arm=$arm seed=$seed (no preds)"
    fi
  done
done

# 断言演示：刻意关闭掩码，必须报 UNTRADABLE_PRICE_READ（预注册 §7 失败判据第 4 条）
echo "== no-mask assertion demo $(date -Is)"
"$PY" scripts/work555_backtest.py --arm dee --seed 42 --no-mask > /tmp/w555logs/nomask_demo.log 2>&1
echo "no-mask exit code = $? (应为非 0，且日志含 UNTRADABLE_PRICE_READ)"
grep -m 3 "UNTRADABLE_PRICE_READ" /tmp/w555logs/nomask_demo.log || echo "!! 未捕获 UNTRADABLE_PRICE_READ"

echo "BACKTESTS DONE $(date -Is)"
