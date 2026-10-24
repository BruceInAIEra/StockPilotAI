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
python3 "$DEPLOY_DIR/validate_env.py" "$RUNTIME_ENV"
compose() {
  docker compose --env-file "$RUNTIME_ENV" --env-file "$RELEASE_ENV" \
    -f "$DEPLOY_DIR/compose.yaml" "$@"
}
