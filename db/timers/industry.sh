#!/bin/bash
# 行业层定时更新（WORK-568）：抓取 -> 规范化装载 -> 质量闸。
#
#   industry.sh daily     日频档：碳酸锂期货、LPR、交易所快照、现货价、存储现货、Benchmark 指数
#   industry.sh monthly   月频档：装机、产量、出口、协会统计、财政、社融、统计局、公告、SNE、Comtrade
#
# 纪律：
#   * 抓取失败重试 2 次（共 3 次尝试），仍失败只记录、继续装载已有 raw（数据旧但不空）；
#   * 装载 + 质量闸整体再重试 2 次，全失败才告警；
#   * 与其它 quantdb timer 共用 /srv/share/quantdb/logs/.quantdb-db.lock，串行写库；
#   * 日志：/srv/share/quantdb/logs/industry-<YYYYMMDD>.log（common.sh init_task）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"

MODE="${1:-daily}"
case "${MODE}" in
  daily|monthly) ;;
  *) echo "用法: $0 daily|monthly" >&2; exit 2 ;;
esac

CN_FETCH_DIR="${QUANTDB_HOME}/code/industry_fetch"
ALT_RAW_DIR="${QUANTDB_HOME}/raw/industry_alt"

init_task industry
log "industry ${MODE} start"

acquire_task_lock "industry-${MODE}" || exit 0
if ! acquire_db_lock; then
  alert_failure industry 1 || true
  exit 1
fi

# 单次脚本 + 2 次重试。返回 0 = 成功。
run_with_retries() {
  local label="$1"
  shift
  local attempt=1 code=0
  while [ "${attempt}" -le 3 ]; do
    set +e
    "$@" >>"${LOG}" 2>&1
    code=$?
    set -e
    if [ "${code}" -eq 0 ]; then
      log "  ok   ${label} (attempt ${attempt})"
      return 0
    fi
    log "  fail ${label} (attempt ${attempt}/3, exit=${code})"
    attempt=$((attempt + 1))
  done
  return "${code}"
}

FETCH_FAILED=0
run_fetcher() {
  local label="$1"
  shift
  if ! run_with_retries "${label}" "$@"; then
    FETCH_FAILED=$((FETCH_FAILED + 1))
    log "  !!   ${label} 3 次均失败，装载沿用已有 raw"
  fi
}

if [ "${MODE}" = "daily" ]; then
  CNN_SOURCES=(gfex_lithium chinamoney_lpr)
  ALT_SOURCES=(benchmark_public_index exchange_daily_quotes ppi_spot_prices trendforce_spot_price)
else
  CNN_SOURCES=(caam_output cnesa_storage cninfo_ann cpcaauto_nev mof_fiscal nea_power pbc_shrzgm stats_gov)
  ALT_SOURCES=(comtrade_trade_values ow_iea_usgs_timeseries sne_public_battery)
fi

# 抓取窗口（WORK-576）：两档都显式传参，不再依赖各脚本 argparse 默认值。
#   * 定时任务只走 --mode incremental（最近窗口/缺日）；--mode backfill 是手工回填，
#     深度大、耗时长，绝不出现在本脚本里。
#   * 国际源的旋钮不叫 --mode：exchange 用 --days/--resume，comtrade 用 --start-year/--end-year，
#     owid 用 --min-year。三个都显式传，值等于「日常档」，改上游默认值不会再悄悄改变定时行为。
#     benchmark / ppi / sne / trendforce 是单页快照，没有深度旋钮，保持无参。
declare -A FETCH_ARGS=(
  [gfex_lithium]="--mode incremental --days 30 --resume"
  [chinamoney_lpr]="--start 2019-08-20"
  [caam_output]="--mode incremental --months 3 --articles 20 --scan 400"
  [cnesa_storage]=""
  [cninfo_ann]="--mode incremental --days 90 --max-pages 60"
  [cpcaauto_nev]="--mode incremental --articles 30 --pages 3"
  [mof_fiscal]="--mode incremental --articles 24 --pages 4"
  [nea_power]="--mode incremental --pages 3 --articles 20 --year-pages 1"
  [pbc_shrzgm]="--mode incremental"
  [stats_gov]="--mode incremental --pages 6 --articles 6"
  # 国际源（脚本在 raw/industry_alt/<源>/fetch.py）
  [exchange_daily_quotes]="--days 7 --resume"
  [comtrade_trade_values]="--start-year 2019 --end-year 2024"
  [ow_iea_usgs_timeseries]="--min-year 0"
)

log "抓取 ${#CNN_SOURCES[@]} 个国内源"
for name in "${CNN_SOURCES[@]}"; do
  script="${CN_FETCH_DIR}/fetch_${name}.py"
  if [ ! -f "${script}" ]; then
    FETCH_FAILED=$((FETCH_FAILED + 1))
    log "  !!   ${script} 不存在，跳过"
    continue
  fi
  src_args=()
  if [ -n "${FETCH_ARGS[${name}]:-}" ]; then
    read -r -a src_args <<<"${FETCH_ARGS[${name}]}"
  fi
  log "  args ${name}: ${FETCH_ARGS[${name}]:-(none)}"
  run_fetcher "${name}" "${PY}" "${script}" ${src_args[@]+"${src_args[@]}"}
done

log "抓取 ${#ALT_SOURCES[@]} 个国际源"
for name in "${ALT_SOURCES[@]}"; do
  script="${ALT_RAW_DIR}/${name}/fetch.py"
  if [ ! -f "${script}" ]; then
    FETCH_FAILED=$((FETCH_FAILED + 1))
    log "  !!   ${script} 不存在，跳过"
    continue
  fi
  src_args=()
  if [ -n "${FETCH_ARGS[${name}]:-}" ]; then
    read -r -a src_args <<<"${FETCH_ARGS[${name}]}"
  fi
  log "  args ${name}: ${FETCH_ARGS[${name}]:-(none)}"
  run_fetcher "${name}" "${PY}" "${script}" ${src_args[@]+"${src_args[@]}"}
done

# 装载 + 质量闸：整体重试 2 次。
load_and_check() {
  run_py -m db.etl.industry load || return $?
  run_py -m db.checks.industry all || return $?
  return 0
}

attempt=1
code=1
while [ "${attempt}" -le 3 ]; do
  set +e
  load_and_check >>"${LOG}" 2>&1
  code=$?
  set -e
  if [ "${code}" -eq 0 ]; then
    break
  fi
  log "装载/质量闸失败 attempt=${attempt}/3 exit=${code}"
  attempt=$((attempt + 1))
done

if [ "${code}" -ne 0 ]; then
  log "industry ${MODE} 失败 exit=${code}"
  alert_failure industry "${code}" || true
  exit "${code}"
fi

log "industry ${MODE} ok（抓取失败 ${FETCH_FAILED} 个，已在日志逐条列出）"
exit 0
