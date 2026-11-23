#!/bin/bash
# 09:00 / 18:00 policy whitelist refresh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"

init_task policy
acquire_task_lock policy || exit 0
log "policy start"
set +e
run_py -m db.policy.fetch_policy --source all --max-items 50 --rpm 60 >>"${LOG}" 2>&1
code=$?
set -e
if [ "${code}" -eq 0 ]; then
  log "policy ok"
  exit 0
fi
log "policy 失败 exit=${code}"
alert_failure policy "${code}" || true
exit "${code}"
