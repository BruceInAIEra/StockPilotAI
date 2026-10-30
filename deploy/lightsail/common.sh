#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run with sudo on the Lightsail server.' >&2
  exit 1
fi
DEPLOY_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$DEPLOY_DIR/../.." && pwd)
RUNTIME_ENV=/etc/stockpilot/runtime.env
RELEASE_ENV=/etc/stockpilot/release.env
STOCKPILOT_ACCESS_MODE=$(python3 "$DEPLOY_DIR/validate_env.py" --print-mode "$RUNTIME_ENV")
STOCKPILOT_COMPOSE_FILES=(-f "$DEPLOY_DIR/compose.yaml")
if [[ $STOCKPILOT_ACCESS_MODE == ssh ]]; then
  STOCKPILOT_COMPOSE_FILES+=(-f "$DEPLOY_DIR/compose.ssh.yaml")
fi
compose() {
  docker compose --project-name stockpilot --profile "$STOCKPILOT_ACCESS_MODE" \
    --env-file "$RUNTIME_ENV" --env-file "$RELEASE_ENV" \
    "${STOCKPILOT_COMPOSE_FILES[@]}" "$@"
}
