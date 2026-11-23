#!/bin/bash
# Plain rsync cold copy + checksum manifest for the quantdb tree.
# Kept in the stable repository so a clean clone can restore the scheduled job.

set -euo pipefail

QDB_SRC="${QUANTDB_HOME:-/srv/share/quantdb}"
QDB_DST="${QUANTDB_BACKUP_DIR:-/srv/backup/quantdb}"
mkdir -p "${QDB_DST}" "${QDB_SRC}/logs"

exec 7>"${QDB_SRC}/logs/.quantdb-db.lock"
if ! flock -w "${QUANTDB_LOCK_WAIT:-3600}" 7; then
  echo "quantdb cold copy skipped: database lock not acquired" >&2
  exit 1
fi

rsync -a --delete --exclude 'staging/tmp/' "${QDB_SRC}/" "${QDB_DST}/"
(
  cd "${QDB_SRC}"
  {
    [ -d raw ] && find raw -type f -print0
    [ -d db ] && find db -type f -print0
    [ -d meta ] && find meta -type f -print0
    [ -d policy ] && find policy -type f -print0
  } | sort -z | xargs -0 -r sha256sum > "${QDB_DST}/SHA256SUMS"
)
(
  cd "${QDB_DST}"
  sha256sum -c SHA256SUMS --quiet
)
echo "quantdb cold copy done: $(date -Iseconds) files=$(wc -l < "${QDB_DST}/SHA256SUMS")" | tee -a "${QDB_SRC}/logs/backup-cold.log"
