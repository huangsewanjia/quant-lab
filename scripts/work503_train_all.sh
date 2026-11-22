#!/usr/bin/env bash
# WORK-503 stage 2 driver — train every arm for one seed, in sequence.
#
#   bash scripts/work503_train_all.sh <seed> <torch_threads> <epochs>
#
# Order matters: the first vq arm (full) trains the stage-1 VQ-VAE checkpoint
# for this seed; the other two vq arms (no_moe, no_prior) reuse the same
# checkpoint, exactly as the upstream ablation scripts do
# (preregistration.md §8 / preregistration_v1.1.md).
set -uo pipefail
cd "$(dirname "$0")/.."

SEED="${1:?seed required}"
THREADS="${2:-4}"
EPOCHS="${3:-6}"
PY=../env/.venv/bin/python
LOG=data/WORK-503/logs
mkdir -p "$LOG"

echo "[$(date -Is)] seed=$SEED threads=$THREADS epochs=$EPOCHS start"

for ARM in full no_moe no_prior no_vq; do
  echo "[$(date -Is)] seed=$SEED arm=$ARM begin"
  $PY -u scripts/work503_run_arms.py --arm "$ARM" --seed "$SEED" \
      --epochs "$EPOCHS" --threads "$THREADS" --workers 1 \
      > "$LOG/${SEED}_${ARM}.log" 2>&1
  rc=$?
  echo "[$(date -Is)] seed=$SEED arm=$ARM rc=$rc"
  if [ "$rc" -ne 0 ]; then
    echo "---- tail of $LOG/${SEED}_${ARM}.log ----"
    tail -25 "$LOG/${SEED}_${ARM}.log"
    exit "$rc"
  fi
done

echo "[$(date -Is)] seed=$SEED arm=gru begin"
$PY -u scripts/work503_baseline.py --kind gru --seed "$SEED" \
    --epochs "$EPOCHS" --threads "$THREADS" \
    > "$LOG/${SEED}_gru.log" 2>&1
rc=$?
echo "[$(date -Is)] seed=$SEED arm=gru rc=$rc"
if [ "$rc" -ne 0 ]; then
  tail -25 "$LOG/${SEED}_gru.log"
fi
echo "[$(date -Is)] seed=$SEED done rc=$rc"
exit "$rc"
