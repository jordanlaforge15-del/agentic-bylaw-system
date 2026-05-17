#!/usr/bin/env bash
# Stop the FastAPI + Next.js processes spawned by ./scripts/e2e-up.sh.
# Does not drop the test database (cheap to keep around between runs;
# pass --drop-db if you want a clean reset).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

DROP_DB=0
if [[ "${1:-}" == "--drop-db" ]]; then
  DROP_DB=1
fi

E2E_TEST_DB="${E2E_TEST_DB:-layer1_test}"
PG_USER="${PG_USER:-layer1}"
STATE_DIR="${REPO_ROOT}/.e2e"
PID_DIR="${STATE_DIR}/pids"

log() { printf '\n==> %s\n' "$1"; }

stop_pid() {
  local pidfile="$1"
  local label="$2"
  if [[ ! -f "$pidfile" ]]; then
    echo "${label}: no pidfile (${pidfile})"
    return 0
  fi
  local pid
  pid="$(cat "$pidfile")"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "${label}: PID ${pid} not running"
    rm -f "$pidfile"
    return 0
  fi
  # Send the kill to the whole process group so npx-spawned children
  # also exit (next dev is a tree, not a single process).
  if kill -- "-$pid" 2>/dev/null; then
    echo "${label}: sent SIGTERM to process group -${pid}"
  else
    kill "$pid" 2>/dev/null || true
    echo "${label}: sent SIGTERM to PID ${pid}"
  fi
  for _ in $(seq 1 15); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    echo "${label}: SIGKILLed PID ${pid}"
  fi
  rm -f "$pidfile"
}

log "Stopping Next.js"
stop_pid "${PID_DIR}/web.pid" "web"

log "Stopping FastAPI"
stop_pid "${PID_DIR}/fastapi.pid" "fastapi"

if [[ "$DROP_DB" -eq 1 ]]; then
  log "Dropping test database ${E2E_TEST_DB}"
  docker_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
      docker compose "$@"
    else
      docker-compose "$@"
    fi
  }
  docker_compose_cmd exec -T postgres psql -U "$PG_USER" -d postgres -c \
    "DROP DATABASE IF EXISTS \"${E2E_TEST_DB}\""
fi

log "E2E stack is down"
