#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
if [[ ${1:-} != --already-locked ]]; then
  exec 9>/var/lock/stockpilot-deploy.lock
  flock -n 9 || { echo 'A release or backup is running.' >&2; exit 1; }
fi
umask 077
install -d -m 0700 /var/lib/stockpilot/backups
BACKUP_FILE="/var/lib/stockpilot/backups/$(date -u +%Y%m%dT%H%M%SZ).dump"
trap 'rm -f -- "$BACKUP_FILE.partial"' EXIT
compose exec -T db pg_dump -U stockpilot -d stockpilot -Fc >"$BACKUP_FILE.partial"
mv -- "$BACKUP_FILE.partial" "$BACKUP_FILE"
# Remove old logical dumps only after a successful backup, never Docker volumes.
find /var/lib/stockpilot/backups -type f -name '*.dump' -mtime +7 -delete
echo 'Database backup completed. Copy logical dumps off the server regularly.'
