#!/bin/bash
# Nightly quantdb cold copy, then the existing borg backup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"
init_task backup

run_backup() {
  set -e
  "${SCRIPT_DIR}/backup-quantdb.sh"

  export BORG_REPO=/srv/backup/borg
  export BORG_PASSCOMMAND="systemd-creds --user decrypt $HOME/.config/multica/secrets/BORG_PASSPHRASE.cred"
  export BORG_RELOCATED_REPO_ACCESS_IS_OK=no

  STAMP=$(date +%Y%m%d-%H%M)
  DUMPS=/srv/backup/dumps
  mkdir -p "$DUMPS"

  cd /srv/data/multica
  docker compose -f docker-compose.selfhost.yml exec -T postgres \
    pg_dump -U multica -Fc -d multica_v2 > "$DUMPS/multica-$STAMP.dump"

  cd "$HOME/apps/weknora"
  docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.server.yml \
    exec -T postgres pg_dump -U postgres -Fc -d WeKnora > "$DUMPS/weknora-$STAMP.dump"

  cd "$HOME/apps/langfuse"
  docker compose exec -T postgres pg_dump -U postgres -Fc -d postgres > "$DUMPS/langfuse-$STAMP.dump"

  ls -1t "$DUMPS"/multica-*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f
  ls -1t "$DUMPS"/weknora-*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f
  ls -1t "$DUMPS"/langfuse-*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f

  borg create --stats --compression zstd,8 "::snap-$STAMP" \
    /srv/share /srv/data "$DUMPS" \
    --exclude '/srv/data/multica/node_modules' >/dev/null

  borg prune --list --keep-daily=7 --keep-weekly=4 --keep-monthly=6
  borg compact
  echo "backup done: snap-$STAMP"
}

log "backup start"
set +e
run_backup >>"${LOG}" 2>&1
code=$?
set -e
if [ "${code}" -eq 0 ]; then
  log "backup ok"
  exit 0
fi
log "backup 失败 exit=${code}"
alert_failure backup "${code}" || true
exit "${code}"
