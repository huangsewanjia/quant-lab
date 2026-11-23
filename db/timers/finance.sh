#!/bin/bash
# 19:00 financial incremental refresh.
#
# Report months (Jan/Apr/Jul/Oct): every trading day.
# Other months: Monday only.  Announcements are selected by actual disclosure
# date with a seven-day overlap.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"

init_task finance
TODAY="$(date +%Y%m%d)"
MONTH="$(date +%m)"
DOW="$(date +%u)"

if [[ " 01 04 07 10 " == *" ${MONTH} "* ]]; then
  set +e
  check_trading_day "${TODAY}"
  day_code=$?
  set -e
  if [ "${day_code}" -eq 1 ]; then
    log "报告期但 ${TODAY} 非交易日，退出 0"
    exit 0
  fi
  if [ "${day_code}" -ne 0 ]; then
    log "交易日历检查失败 exit=${day_code}"
    alert_failure finance "${day_code}" || true
    exit "${day_code}"
  fi
else
  if [ "${DOW}" -ne 1 ]; then
    log "非报告期且非周一，退出 0"
    exit 0
  fi
fi

acquire_task_lock finance || exit 0
acquire_db_lock
log "finance start ${TODAY}"
set +e
run_py -m db.etl.fetch --incremental --group finance \
  --overlap-days 7 --concurrency 4 >>"${LOG}" 2>&1
code=$?
set -e
if [ "${code}" -eq 0 ]; then
  log "finance ok"
  exit 0
fi
log "finance 失败 exit=${code}"
alert_failure finance "${code}" || true
exit "${code}"
