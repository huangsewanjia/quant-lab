#!/bin/bash
# Nightly quantdb cold copy, PostgreSQL dumps, then the borg snapshot.
#
# systemd --user starts this unit from a session that predates the docker group
# membership, so the supplementary docker group is missing there even though
# `id moyongkai` lists it. Every docker call therefore goes through `dk`
# (`sg docker -c ...`) and never through sudo or socket permission changes.
# A dump is written to <file>.part and renamed only after its header and archive
# directory check out, so a failed pg_dump cannot leave a 0-byte file that
# looks like a backup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"
init_task backup

SG_BIN="${SG_BIN:-/usr/bin/sg}"
DUMPS="${DUMPS:-/srv/backup/dumps}"
WORK_TMP="$(mktemp -d "${TMPDIR:-/tmp}/backup-nightly-XXXXXX")"

# Same line format as common.sh log(), but stdout only: run_backup already
# appends its stdout to LOG, so teeing here would write every line twice.
note() { printf '[%s] %s\n' "$(date -Iseconds)" "$*"; }

# Run a command with the supplementary docker group re-entered. Bash quoting via
# printf %q keeps the argv intact inside sg's shell word split.
dk() {
  local cmd
  printf -v cmd '%q ' "$@"
  "${SG_BIN}" docker -c "${cmd}"
}

# run_step <label> <stdout target: path or -> <command...>
# On failure logs the step, exit code and first stderr line, then returns it;
# on success forwards stderr so borg warnings and --stats stay in the log.
run_step() {
  local step="$1" out="$2"; shift 2
  local err="${WORK_TMP}/step.err" first code=0
  : >"${err}"
  if [ "${out}" = "-" ]; then
    "$@" 2>"${err}" || code=$?
  else
    "$@" >"${out}" 2>"${err}" || code=$?
  fi
  if [ "${code}" -ne 0 ]; then
    first=$(head -n 1 "${err}" 2>/dev/null || true)
    note "失败步骤：${step}；退出码=${code}；stderr 首行=${first:-<空>}"
    return "${code}"
  fi
  if [ -s "${err}" ]; then
    cat "${err}"
  fi
}

# dump_db <label> <compose workdir> <db> <user> [compose args...]
dump_db() {
  local label="$1" dir="$2" db="$3" user="$4"; shift 4
  local out="${DUMPS}/${label}-${STAMP}.dump"
  local part="${out}.part"
  local code=0 size magic toc_first
  (
    if ! cd "${dir}" 2>"${WORK_TMP}/${label}.cd.err"; then
      note "失败步骤：进入 ${dir}；退出码=1；stderr 首行=$(head -n 1 "${WORK_TMP}/${label}.cd.err" 2>/dev/null || true)"
      exit 1
    fi
    run_step "pg_dump ${label}（database=${db}）" "${part}" \
      dk docker compose "$@" exec -T postgres pg_dump -U "${user}" -Fc -d "${db}"
  ) || code=$?
  if [ "${code}" -ne 0 ]; then
    note "丢弃不完整文件 ${part}（$(stat -c%s "${part}" 2>/dev/null || echo 0) 字节）"
    rm -f "${part}"
    return "${code}"
  fi
  size=$(stat -c%s "${part}")
  magic=$(head -c 5 "${part}" || true)
  if [ "${size}" -lt 1024 ] || [ "${magic}" != "PGDMP" ]; then
    note "失败步骤：校验 ${label} dump；大小=${size} 字节；头部=${magic:-<空>}；文件=${part}"
    rm -f "${part}"
    return 1
  fi
  if command -v pg_restore >/dev/null 2>&1; then
    # No "unsupported version" escape hatch: the header version sits next to the
    # body, so garbage produces the same message as a newer format. A truncated
    # or corrupt archive must fail loudly; a real format mismatch (containers
    # upgraded past this host's client) is fixable by upgrading the client.
    if ! pg_restore -l "${part}" >/dev/null 2>"${WORK_TMP}/${label}.toc.err"; then
      toc_first=$(head -n 1 "${WORK_TMP}/${label}.toc.err" 2>/dev/null || true)
      note "失败步骤：pg_restore -l 校验 ${label}；退出码=1；stderr 首行=${toc_first:-<空>}；文件=${part}"
      rm -f "${part}"
      return 1
    fi
  else
    note "警告：本机没有 pg_restore，${label} 的归档目录未校验"
  fi
  mv -f "${part}" "${out}"
  note "dump 完成：${label} ${size} 字节 → ${out}"
}

# Keep the newest 7 dumps per label; a failing cleanup is reported, not hidden.
prune_dumps() {
  local label="$1" count=0
  count=$(ls -1 "${DUMPS}/${label}"-*.dump 2>/dev/null | wc -l || true)
  if [ "${count}" -le 7 ]; then
    return 0
  fi
  if ! ls -1t "${DUMPS}/${label}"-*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f; then
    note "警告：保留策略清理 ${label} 旧 dump 失败（${count} 份，应留 7 份）"
  fi
}

run_backup() {
  set -e
  "${SCRIPT_DIR}/backup-quantdb.sh"

  export BORG_REPO=/srv/backup/borg
  export BORG_PASSCOMMAND="systemd-creds --user decrypt $HOME/.config/multica/secrets/BORG_PASSPHRASE.cred"
  export BORG_RELOCATED_REPO_ACCESS_IS_OK=no

  STAMP=$(date +%Y%m%d-%H%M)
  mkdir -p "${DUMPS}"

  # Interrupted runs leave .part files and temp dirs that retention never sees.
  find "${DUMPS}" -maxdepth 1 -name '*.part*' -mtime +1 -print -delete
  find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'backup-nightly-*' -mtime +1 -exec rm -rf {} + || true

  dump_db multica /srv/data/multica multica_v2 multica \
    -f docker-compose.selfhost.yml

  dump_db weknora "$HOME/apps/weknora" WeKnora postgres \
    -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.server.yml

  dump_db langfuse "$HOME/apps/langfuse" postgres postgres

  prune_dumps multica
  prune_dumps weknora
  prune_dumps langfuse

  run_step "borg create snap-${STAMP}" - \
    borg create --stats --compression zstd,8 "::snap-$STAMP" \
    /srv/share /srv/data "${DUMPS}" \
    --exclude '/srv/data/multica/node_modules'

  run_step "borg prune" - borg prune --list --keep-daily=7 --keep-weekly=4 --keep-monthly=6
  run_step "borg compact" - borg compact
  note "backup done: snap-$STAMP"
}

cleanup_tmp() {
  rm -rf "${WORK_TMP}"
}

log "backup start"
set +e
( run_backup ) >>"${LOG}" 2>&1
code=$?
set -e
cleanup_tmp
if [ "${code}" -eq 0 ]; then
  log "backup ok"
  exit 0
fi
log "backup 失败 exit=${code}"
alert_failure backup "${code}" || true
exit "${code}"
