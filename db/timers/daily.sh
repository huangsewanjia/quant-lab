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

# Check the calendar before creating a Tushare client.  A non-trading day is a
# normal exit 0, including when the token is absent.
set +e
check_trading_day "${TODAY}"
day_code=$?
set -e
if [ "${day_code}" -eq 1 ]; then
  log "非交易日，退出 0"
  exit 0
fi
if [ "${day_code}" -ne 0 ]; then
  log "交易日历检查失败 exit=${day_code}"
  alert_failure daily "${day_code}" || true
  exit "${day_code}"
fi

acquire_task_lock daily || exit 0
if ! acquire_db_lock; then
  alert_failure daily 1 || true
  exit 1
fi

run_index_weight() {
  [ "${HHMM}" -ge 1900 ] || return 0
  local marker="${STATE_DIR}/index-weight-done-${TODAY}"
  [ -f "${marker}" ] && return 0
  log "检查每月指数成分任务"
  local attempt_file="${STATE_DIR}/index-weight-attempt-${TODAY}"
  local attempt=0
  [ -f "${attempt_file}" ] && attempt=$(cat "${attempt_file}" 2>/dev/null || echo 0)
  attempt=$((attempt + 1))
  printf '%s' "${attempt}" >"${attempt_file}"
  local code
  set +e
  run_py -m db.etl.fetch --incremental --group index_weight --concurrency 4 >>"${LOG}" 2>&1
  code=$?
  if [ "${code}" -eq 0 ]; then
    touch "${marker}"
    rm -f "${attempt_file}"
    log "index_weight ok"
    return 0
  elif [ "${code}" -eq 75 ] && [ "${attempt}" -lt 2 ]; then
    log "index_weight 源端延迟，20:30 再试"
    return 75
  else
    log "index_weight 失败 exit=${code}"
    alert_failure index_weight "${code}" || true
    return 1
  fi
}

if [ -f "${STATE_DIR}/daily-ok-${TODAY}" ] && [ "${HHMM}" -ge 1900 ]; then
  log "今日行情已成功，跳过重复抓取"
  set +e
  run_index_weight
  index_only_code=$?
  set -e
  exit "${index_only_code}"
fi

merge_exit() {
  local market_code="$1"
  local index_code="$2"
  if [ "${market_code}" -ne 0 ] && [ "${market_code}" -ne 75 ]; then
    printf '%s\n' "${market_code}"
  elif [ "${index_code}" -ne 0 ] && [ "${index_code}" -ne 75 ]; then
    printf '%s\n' "${index_code}"
  elif [ "${market_code}" -eq 75 ] || [ "${index_code}" -eq 75 ]; then
    printf '75\n'
  else
    printf '0\n'
  fi
}

attempt_count
log "daily start attempt=${ATTEMPT} at ${HHMM}"
set +e
run_py -m db.etl.fetch --incremental --group market --concurrency 8 >>"${LOG}" 2>&1
market_code=$?
set -e

index_code=0
if [ "${HHMM}" -ge 1900 ]; then
  set +e
  run_index_weight
  index_code=$?
  set -e
fi

market_result="${market_code}"
if [ "${market_code}" -eq 0 ]; then
  touch "${STATE_DIR}/daily-ok-${TODAY}"
  reset_attempt
  log "market ok"
elif [ "${market_code}" -eq 75 ]; then
  if [ "${ATTEMPT}" -ge 3 ]; then
    log "源端连续 3 次无当日数据，转告警"
    alert_failure daily 75 || true
    market_result=1
  else
    log "源端当日数据未发布，保留 19:30/20:30 重试"
    market_result=75
  fi
else
  log "market 失败 exit=${market_code}"
  alert_failure daily "${market_code}" || true
  market_result="${market_code}"
fi

final_code=$(merge_exit "${market_result}" "${index_code}")
log "daily finished market=${market_code} index_weight=${index_code} final=${final_code}"
exit "${final_code}"
