#!/usr/bin/env bash
# Run from the reviewed release on Lightsail; reuse its protected runtime settings.
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
exec 9>/var/lock/stockpilot-deploy.lock
flock -s -n 9 || { echo 'A release or backup is running. Retry afterward.' >&2; exit 1; }
compose exec -T web python -m app.manage "$@"
