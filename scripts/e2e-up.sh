#!/usr/bin/env bash
# Boot the end-to-end UI test stack on ports 3001 (Next.js) + 8001
# (FastAPI), wired against a dedicated Postgres database named
# ``layer1_test`` on the local docker-compose Postgres container.
#
# Idempotent: re-running while the stack is already up is a no-op for
# already-healthy components. Use scripts/e2e-down.sh to tear down.
#
# Env vars consumed:
#   E2E_TEST_DB    — DB name to create/migrate (default ``layer1_test``)
#   E2E_FASTAPI_PORT — port for the test FastAPI (default 8001)
#   E2E_WEB_PORT    — port for the Next.js dev server (default 3001)
#   PG_PORT         — host port that the postgres container publishes
#                     (default 5432). Override per worktree to allow
#                     parallel `make e2e` runs; the compose file reads
#                     POSTGRES_HOST_PORT which this script exports below.
#
# State written:
#   .e2e/pids/fastapi.pid  — uvicorn PID
#   .e2e/pids/web.pid      — next dev PID
#   .e2e/logs/fastapi.log  — uvicorn stderr+stdout
#   .e2e/logs/web.log      — next dev stderr+stdout

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

E2E_TEST_DB="${E2E_TEST_DB:-layer1_test}"
E2E_FASTAPI_PORT="${E2E_FASTAPI_PORT:-8001}"
E2E_WEB_PORT="${E2E_WEB_PORT:-3001}"
E2E_USER_ID="${E2E_USER_ID:-demo-user-1}"
PG_USER="${PG_USER:-layer1}"
PG_PASSWORD="${PG_PASSWORD:-layer1}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"

DATABASE_URL_E2E="postgresql+psycopg://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${E2E_TEST_DB}"
PSQL_BASE_URL="postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/postgres"

# Compose reads this for the postgres `ports:` host-side binding. Keep
# it aligned with PG_PORT so a worktree overriding one always overrides
# the other consistently.
export POSTGRES_HOST_PORT="$PG_PORT"

STATE_DIR="${REPO_ROOT}/.e2e"
PID_DIR="${STATE_DIR}/pids"
LOG_DIR="${STATE_DIR}/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

log() { printf '\n==> %s\n' "$1"; }

docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "error: Docker Compose required" >&2
    exit 1
  fi
}

require_venv() {
  if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    echo "error: ${REPO_ROOT}/.venv missing. Run ./scripts/dev-setup.sh first." >&2
    exit 1
  fi
}

ensure_postgres() {
  log "Ensuring Postgres container is up"
  docker_compose up -d postgres
  for _ in $(seq 1 60); do
    if docker_compose exec -T postgres pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "error: Postgres did not become ready" >&2
  exit 1
}

ensure_test_db() {
  log "Ensuring test database ${E2E_TEST_DB} exists"
  local exists
  exists=$(docker_compose exec -T postgres psql -U "$PG_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${E2E_TEST_DB}'" || true)
  if [[ "$exists" != "1" ]]; then
    docker_compose exec -T postgres psql -U "$PG_USER" -d postgres -c \
      "CREATE DATABASE \"${E2E_TEST_DB}\""
    echo "created database ${E2E_TEST_DB}"
  else
    echo "database ${E2E_TEST_DB} already exists"
  fi
}

run_migrations() {
  log "Running Alembic migrations against ${E2E_TEST_DB}"
  # Pre-create alembic_version with a wider column. The default
  # VARCHAR(32) is one char too short for the revision id
  # ``0008_advisor_billing_subscription`` (33 chars), which makes a
  # fresh migration chain fail. Pre-creating with VARCHAR(255) is the
  # least invasive fix and only affects fresh databases; existing
  # databases keep their column as-is.
  docker_compose exec -T postgres psql -U "$PG_USER" -d "$E2E_TEST_DB" \
    -c "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(255) PRIMARY KEY)" >/dev/null
  DATABASE_URL="$DATABASE_URL_E2E" \
    PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
    "${REPO_ROOT}/.venv/bin/alembic" -c "${REPO_ROOT}/alembic.ini" upgrade head
}

seed_demo_user() {
  log "Seeding demo user (${E2E_USER_ID}) + credits"
  DATABASE_URL="$DATABASE_URL_E2E" \
    PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
    "${REPO_ROOT}/.venv/bin/python" \
    "${REPO_ROOT}/scripts/seed_e2e_user.py" --user-id "$E2E_USER_ID"
}

is_listening() {
  local port="$1"
  # nc -z would be cleaner but isn't installed everywhere on macOS; use lsof.
  lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1
}

wait_for_port() {
  local port="$1"
  local label="$2"
  local timeout="${3:-30}"
  for _ in $(seq 1 "$timeout"); do
    if is_listening "$port"; then
      echo "${label} on :${port} is up"
      return 0
    fi
    sleep 1
  done
  echo "error: ${label} on :${port} did not become ready in ${timeout}s" >&2
  return 1
}

start_fastapi() {
  if is_listening "$E2E_FASTAPI_PORT"; then
    echo "fastapi already listening on :${E2E_FASTAPI_PORT} — leaving it"
    return 0
  fi
  log "Starting FastAPI test server (advisor.api.e2e_server) on :${E2E_FASTAPI_PORT}"
  # PYTHONPATH ensures we run the worktree's src/, not whatever the
  # venv's editable install points at — important when the venv was
  # provisioned against a sibling worktree (e.g. main).
  ( cd "$REPO_ROOT" && \
    DATABASE_URL="$DATABASE_URL_E2E" \
    PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
    ADVISOR_HOST=127.0.0.1 \
    ADVISOR_PORT="$E2E_FASTAPI_PORT" \
    ADVISOR_E2E_CORS_ORIGINS="http://localhost:${E2E_WEB_PORT}" \
    nohup "${REPO_ROOT}/.venv/bin/uvicorn" advisor.api.e2e_server:app \
      --host 127.0.0.1 --port "$E2E_FASTAPI_PORT" \
      >"${LOG_DIR}/fastapi.log" 2>&1 &
    echo $! >"${PID_DIR}/fastapi.pid"
  )
  wait_for_port "$E2E_FASTAPI_PORT" "FastAPI"
}

start_web() {
  if is_listening "$E2E_WEB_PORT"; then
    echo "next dev already listening on :${E2E_WEB_PORT} — leaving it"
    return 0
  fi
  log "Starting Next.js dev server on :${E2E_WEB_PORT}"
  # DEMO_PASSWORD must be set so the proxy.ts fallback gate has a
  # known shared password. Playwright fixtures POST to /api/access
  # with this value to mint the abs_demo cookie before each test.
  ( cd "${REPO_ROOT}/web" && \
    ADVISOR_API_URL="http://127.0.0.1:${E2E_FASTAPI_PORT}" \
    ADVISOR_DEMO_USER_ID="$E2E_USER_ID" \
    CLERK_SECRET_KEY="" \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="" \
    DEMO_PASSWORD="${E2E_DEMO_PASSWORD:-e2e-demo-pw}" \
    ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-e2e-admin-pw}" \
    nohup npx next dev -p "$E2E_WEB_PORT" \
      >"${LOG_DIR}/web.log" 2>&1 &
    echo $! >"${PID_DIR}/web.pid"
  )
  # next dev takes longer to compile on first start; allow up to 90s.
  wait_for_port "$E2E_WEB_PORT" "Next.js" 90
}

main() {
  require_venv
  ensure_postgres
  ensure_test_db
  run_migrations
  seed_demo_user
  start_fastapi
  start_web
  log "E2E stack is up"
  cat <<EOF
  FastAPI:  http://127.0.0.1:${E2E_FASTAPI_PORT}/healthz
  Web:      http://localhost:${E2E_WEB_PORT}
  Logs:     ${LOG_DIR}/fastapi.log
            ${LOG_DIR}/web.log
  PIDs:     ${PID_DIR}/
EOF
}

main "$@"
