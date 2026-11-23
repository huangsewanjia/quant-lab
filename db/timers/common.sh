#!/bin/bash
# Shared shell helpers for the quantdb systemd timers.
#
# Every wrapper writes one log file per task/day, holds a shared DuckDB lock,
# and turns a non-zero exit into one Multica issue per task/day. Secrets are
# never echoed: the token is read by the Python ETL from its 0600 file.

set -euo pipefail

QUANTDB_HOME="${QUANTDB_HOME:-/srv/share/quantdb}"
QUANTDB_REPO="${QUANTDB_REPO:-/srv/share/quantdb/code/quant-lab}"
PY="${QUANTDB_PYTHON:-/home/moyongkai/.venvs/quantdb/bin/python}"
MULTICA_CLI="${MULTICA_CLI_PATH:-/home/moyongkai/bin/multica}"
PROJECT_ID="${QUANTDB_PROJECT_ID:-046339f9-d253-4eb8-a9db-764b26e67b1a}"
LOG_DIR="${QUANTDB_HOME}/logs"
STATE_DIR="${LOG_DIR}/state"
DB_LOCK="${LOG_DIR}/.quantdb-db.lock"

mkdir -p "${LOG_DIR}" "${STATE_DIR}"

init_task() {
  TASK="$1"
  LOG="${LOG_DIR}/${TASK}-$(date +%Y%m%d).log"
  touch "${LOG}"
}

log() {
  local line="[$(date -Iseconds)] $*"
  printf '%s\n' "${line}" | tee -a "${LOG}"
}

run_py() {
  ( cd "${QUANTDB_REPO}" && PYTHONPATH="${QUANTDB_REPO}" QUANTDB_HOME="${QUANTDB_HOME}" "${PY}" "$@" )
}

acquire_db_lock() {
  exec 9>"${DB_LOCK}"
  if ! flock -w "${QUANTDB_LOCK_WAIT:-7200}" 9; then
    log "等待数据库锁超时（${QUANTDB_LOCK_WAIT:-7200}s），本次不执行"
    return 1
  fi
  log "已获得数据库锁"
}

acquire_task_lock() {
  local name="$1"
  exec 8>"${STATE_DIR}/${name}.lock"
  if ! flock -n 8; then
    log "同类任务仍在运行，本次退出 0"
    return 1
  fi
}

refresh_calendar() {
  run_py -m db.etl.incremental refresh-calendar >>"${LOG}" 2>&1
}

check_trading_day() {
  local day="$1"
  local code
  set +e
  run_py -m db.etl.incremental check-day --date "${day}" >>"${LOG}" 2>&1
  code=$?
  set -e
  if [ "${code}" -eq 2 ]; then
    log "交易日历未覆盖 ${day}，刷新后重查"
    refresh_calendar
    set +e
    run_py -m db.etl.incremental check-day --date "${day}" >>"${LOG}" 2>&1
    code=$?
    set -e
  fi
  return "${code}"
}

attempt_count() {
  local file="${STATE_DIR}/${TASK}-attempt-$(date +%Y%m%d)"
  local value=0
  if [ -f "${file}" ]; then
    value=$(cat "${file}" 2>/dev/null || echo 0)
  fi
  value=$((value + 1))
  printf '%s' "${value}" >"${file}"
  ATTEMPT="${value}"
}

reset_attempt() {
  rm -f "${STATE_DIR}/${TASK}-attempt-$(date +%Y%m%d)"
}

alert_failure() {
  local task="$1"
  local code="$2"
  local day
  day=$(date +%Y-%m-%d)
  local marker="${STATE_DIR}/${task}-alert-${day}.id"
  if [ -f "${marker}" ]; then
    log "告警已在 ${marker} 发出，本次不重复建单"
    return 0
  fi
  if [ ! -x "${MULTICA_CLI}" ]; then
    log "告警失败：MULTICA_CLI_PATH 不可执行: ${MULTICA_CLI}"
    return 1
  fi
  local body="${STATE_DIR}/${task}-alert-${day}.md"
  {
    printf '## 数据更新失败\n\n'
    printf -- '- 任务：`%s`\n' "${task}"
    printf -- '- 时间：%s\n' "$(date -Iseconds)"
    printf -- '- 主机：%s\n' "$(hostname)"
    printf -- '- 退出码：%s\n' "${code}"
    printf -- '- 仓库：`%s`\n' "${QUANTDB_REPO}"
    printf -- '- 日志：`%s`\n\n' "${LOG}"
    printf '```text\n'
    tail -n 50 "${LOG}" 2>/dev/null || true
    printf '```\n'
  } >"${body}"
  local output
  if output=$("${MULTICA_CLI}" issue create \
      --title "数据更新失败｜${task}｜${day}" \
      --description-file "${body}" \
      --allow-external-file \
      --project "${PROJECT_ID}" \
      --status backlog \
      --priority high \
      --output json 2>>"${LOG}"); then
    local identifier
    identifier=$(printf '%s' "${output}" | "${PY}" -c \
      'import json,sys; o=json.load(sys.stdin); print(o.get("identifier") or o.get("id") or "")' \
      2>/dev/null || true)
    printf '%s\n' "${identifier}" >"${marker}"
    log "已建告警单：${identifier:-unknown}"
  else
    log "告警建单失败（multica CLI 非 0）"
    return 1
  fi
}
