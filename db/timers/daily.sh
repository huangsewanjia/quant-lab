#!/bin/bash
# 18:30 / 19:30 / 20:30 Asia/Shanghai market refresh.
#
# - 18:30: refresh trade_cal, then fetch any new completed trading days.
# - 19:30 / 20:30: retry a source-delay failure; otherwise only run the
#   monthly index-weight sweep when today is the first trading day.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"

init_task daily
TODAY="$(date +%Y%m%d)"
HHMM="$(date +%H%M)"
acquire_task_lock daily || exit 0
acquire_db_lock
attempt_count
log "daily start attempt=${ATTEMPT} at ${HHMM}"

run_index_weight() {
  [ "${HHMM}" -ge 1900 ] || return 0
  local marker="${STATE_DIR}/index-weight-done-${TODAY}"
  [ -f "${marker}" ] && return 0
  log "检查每月指数成分任务"
  local code
  set +e
  run_py -m db.etl.fetch --incremental --group index_weight --concurrency 4 >>"${LOG}" 2>&1
  code=$?
  set -e
  if [ "${code}" -eq 0 ]; then
    touch "${marker}"
    log "index_weight ok"
  else
    log "index_weight 失败 exit=${code}"
    alert_failure index_weight "${code}" || true
  fi
}

if [ -f "${STATE_DIR}/daily-ok-${TODAY}" ] && [ "${HHMM}" -ge 1900 ]; then
  log "今日行情已成功，跳过重复抓取"
  run_index_weight
  exit 0
fi

set +e
run_py -m db.etl.fetch --incremental --group market --concurrency 8 >>"${LOG}" 2>&1
code=$?
set -e

if [ "${code}" -eq 0 ]; then
  touch "${STATE_DIR}/daily-ok-${TODAY}"
  reset_attempt
  log "market ok"
  run_index_weight
  exit 0
fi

if [ "${code}" -eq 75 ]; then
  if [ "${ATTEMPT}" -ge 3 ]; then
    log "源端连续 3 次无当日数据，转告警"
    alert_failure daily 75 || true
    exit 1
  fi
  log "源端当日数据未发布，保留 19:30/20:30 重试"
  exit 75
fi

log "market 失败 exit=${code}"
alert_failure daily "${code}" || true
exit "${code}"
