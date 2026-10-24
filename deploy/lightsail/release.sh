#!/usr/bin/env bash
# Run on the host after copying a reviewed source release into /opt/stockpilot/app.
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
if [[ $# -ne 1 || ! $1 =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Usage: sudo bash deploy/lightsail/release.sh <full-source-commit-sha>' >&2
  exit 1
fi
exec 9>/var/lock/stockpilot-deploy.lock
flock -n 9 || { echo 'Another release or backup is running.' >&2; exit 1; }
umask 077
docker build --target runtime -t "stockpilot:$1" "$REPO_DIR"
if [[ -f $RELEASE_ENV ]]; then
  cp -- "$RELEASE_ENV" /etc/stockpilot/previous-release.env
fi
printf 'APP_IMAGE=stockpilot:%s\n' "$1" >"$RELEASE_ENV"
compose config --quiet
compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
compose up -d --wait db
# App downtime is intentional during this small-host release. No new requests
# while migrating; interrupted analyses must be resubmitted.
compose stop caddy web
bash "$DEPLOY_DIR/backup.sh" --already-locked
compose run --rm migrate
compose up -d --wait web
compose up -d caddy
install -m 0644 "$DEPLOY_DIR/stockpilot-backup.service" /etc/systemd/system/
install -m 0644 "$DEPLOY_DIR/stockpilot-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now stockpilot-backup.timer
echo 'App readiness passed. Check HTTPS from the allowed IP and run a complete analysis.'
echo 'If a step failed after stopping the app, inspect logs and restore/release explicitly.'
