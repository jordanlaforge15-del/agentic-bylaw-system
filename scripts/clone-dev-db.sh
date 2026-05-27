#!/usr/bin/env bash
# Clone the local dev DB into an isolated Docker Compose project for
# experiment workflows (new-city intake, schema testing, expensive runs).
#
# Usage:
#   scripts/clone-dev-db.sh <experiment-name> [<host-port>]
#
# Examples:
#   scripts/clone-dev-db.sh mainland-intake        # auto-picks a free port
#   scripts/clone-dev-db.sh mainland-intake 5440   # use a specific port
#
# What it does:
#   1. Picks a free host port (5440-5480) if not supplied.
#   2. Starts an isolated Postgres via `docker compose -p <name>`.
#   3. Waits for pg_isready (up to 30s).
#   4. Restores the most recent ~/backups/agentic-bylaw-system/layer1-*.dump.
#   5. Prints DATABASE_URL + teardown one-liner.
#   6. Writes .env.clone.<name> in the current directory for easy sourcing.
#
# Env overrides:
#   BYLAW_BACKUP_DIR   dump directory (default: $HOME/backups/agentic-bylaw-system)
#
# Counterpart: scripts/destroy-clone-db.sh <experiment-name>

set -euo pipefail

EXPERIMENT="${1:-}"
PORT_ARG="${2:-}"

DB="layer1"
DB_USER="layer1"
DB_PASS="layer1"
BACKUP_DIR="${BYLAW_BACKUP_DIR:-$HOME/backups/agentic-bylaw-system}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"

if [[ -z "$EXPERIMENT" ]]; then
  echo "Usage: $(basename "$0") <experiment-name> [<host-port>]" >&2
  exit 1
fi

find_free_port() {
  local port
  for port in $(seq 5440 5480); do
    if ! lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
      echo "$port"
      return 0
    fi
  done
  echo "ERROR: no free port found in range 5440-5480" >&2
  return 1
}

if [[ -n "$PORT_ARG" ]]; then
  PORT="$PORT_ARG"
else
  PORT="$(find_free_port)"
fi

DUMP="$(ls -t "$BACKUP_DIR"/layer1-*.dump 2>/dev/null | head -1 || true)"
if [[ -z "$DUMP" ]]; then
  echo "ERROR: no dump files found in $BACKUP_DIR" >&2
  echo "       Run scripts/backup-dev-db.sh first to create a backup." >&2
  exit 1
fi

echo "==> Dump:       $DUMP"
echo "==> Experiment: $EXPERIMENT"
echo "==> Port:       $PORT"
echo ""
echo "==> Starting Postgres..."

POSTGRES_HOST_PORT="$PORT" docker compose -p "$EXPERIMENT" \
  -f "$COMPOSE_FILE" up -d postgres

CONTAINER="${EXPERIMENT}-postgres-1"
echo -n "==> Waiting for pg_isready"
for i in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U "$DB_USER" -d "$DB" -q 2>/dev/null; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 1
  if [[ "$i" -eq 30 ]]; then
    echo ""
    echo "ERROR: Postgres did not become ready within 30s" >&2
    docker compose -p "$EXPERIMENT" -f "$COMPOSE_FILE" down -v >/dev/null 2>&1 || true
    exit 1
  fi
done

echo "==> Restoring dump (this may take a moment)..."
docker exec -i "$CONTAINER" dropdb -U "$DB_USER" --if-exists "$DB"
docker exec -i "$CONTAINER" createdb -U "$DB_USER" "$DB"
docker exec -i "$CONTAINER" \
  pg_restore -U "$DB_USER" -d "$DB" --no-owner < "$DUMP"

DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASS}@localhost:${PORT}/${DB}"
ENV_FILE=".env.clone.${EXPERIMENT}"

cat > "$ENV_FILE" <<EOF
export DATABASE_URL="${DATABASE_URL}"
EOF

echo ""
echo "====================================================="
echo "  Clone '${EXPERIMENT}' is ready."
echo "====================================================="
echo ""
echo "  DATABASE_URL=${DATABASE_URL}"
echo ""
echo "  Load env:  source ${ENV_FILE}"
echo "  Teardown:  scripts/destroy-clone-db.sh ${EXPERIMENT}"
echo ""
