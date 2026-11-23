#!/bin/bash
# Monday 06:00 policy reconciliation; Monday 09:00 weekly health report.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"

init_task weekly
HOUR="$(date +%H)"
acquire_task_lock weekly || exit 0
code=0

if [ "${HOUR}" -lt 8 ]; then
  log "weekly 06:00 policy reconcile"
  set +e
  run_py -m db.policy.fetch_policy \
    --reconcile --max-fetch 200 --rpm 60 >>"${LOG}" 2>&1
  code=$?
  set -e
else
  acquire_db_lock
  log "weekly 09:00 health report"
  set +e
  run_py -m db.etl.weekly >>"${LOG}" 2>&1
  code=$?
  set -e
fi

if [ "${code}" -eq 0 ]; then
  log "weekly ok"
  exit 0
fi
log "weekly 失败 exit=${code}"
alert_failure weekly "${code}" || true
exit "${code}"
