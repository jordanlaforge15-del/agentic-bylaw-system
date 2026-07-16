#!/usr/bin/env bash
# Tear down an isolated clone DB created by scripts/clone-dev-db.sh.
#
# Usage:
#   scripts/destroy-clone-db.sh <experiment-name>
#
# Removes the Docker Compose project and its associated volumes.
# Safe to run even if the containers are already stopped.

set -euo pipefail

EXPERIMENT="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"

if [[ -z "$EXPERIMENT" ]]; then
  echo "Usage: $(basename "$0") <experiment-name>" >&2
  exit 1
fi

echo "==> Destroying clone '${EXPERIMENT}' (containers + volumes)..."
docker compose -p "$EXPERIMENT" -f "$COMPOSE_FILE" down -v
echo "==> Done."
