#!/usr/bin/env bash
# Boot the local dev stack: Postgres + FastAPI advisor backend on :8000
# + Next.js frontend on :3000. Foreground; Ctrl+C tears both servers
# down. Expects ANTHROPIC_API_KEY in the environment.
#
# Env vars consumed:
#   ANTHROPIC_API_KEY  — required; passed through to the advisor backend
#   DEV_FASTAPI_PORT   — default 8000
#   DEV_WEB_PORT       — default 3000
#   DEV_USER_ID        — default demo-user-1 (forwarded as ADVISOR_DEMO_USER_ID)
#   DATABASE_URL       — default from .env or
#                        postgresql+psycopg://layer1:layer1@localhost:5432/layer1
#   NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
#   CLERK_SECRET_KEY   — optional; when present (in shell env or
#                        web/.env.local), enables real Clerk sign-in.
#                        When absent, the legacy /access shared-password
#                        gate is used.
#
# This is the MANUAL-TESTING companion to scripts/e2e-up.sh. Key
# differences: dev DB (layer1), canonical ports (3000/8000), no
# demo-user seed, no Clerk blanking. For automated Playwright runs,
# use e2e-up.sh — it targets layer1_test and disables Clerk so the
# test fixtures' /access gate path is deterministic.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

DEV_FASTAPI_PORT="${DEV_FASTAPI_PORT:-8000}"
DEV_WEB_PORT="${DEV_WEB_PORT:-3000}"
DEV_USER_ID="${DEV_USER_ID:-demo-user-1}"

log() { printf '\n==> %s\n' "$1"; }

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "error: ANTHROPIC_API_KEY is not set. Export it before running this script." >&2
  exit 1
fi

if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  echo "error: ${REPO_ROOT}/.venv missing. Run ./scripts/dev-setup.sh first." >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://layer1:layer1@localhost:5432/layer1}"
export DATABASE_URL

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

is_listening() {
  lsof -iTCP:"$1" -sTCP:LISTEN -P -n >/dev/null 2>&1
}

for port in "$DEV_FASTAPI_PORT" "$DEV_WEB_PORT"; do
  if is_listening "$port"; then
    echo "error: port :${port} is already in use" >&2
    exit 1
  fi
done

log "Ensuring Postgres container is up"
docker_compose up -d postgres
for _ in $(seq 1 60); do
  if docker_compose exec -T postgres pg_isready -U layer1 -d postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker_compose exec -T postgres pg_isready -U layer1 -d postgres >/dev/null

log "Running Alembic migrations against ${DATABASE_URL}"
"${REPO_ROOT}/.venv/bin/alembic" upgrade head

if [[ ! -f web/.env.local ]]; then
  log "Creating web/.env.local from example"
  cp web/.env.local.example web/.env.local
fi

if [[ ! -d web/node_modules ]]; then
  log "Installing web dependencies (npm install)"
  ( cd web && npm install )
fi

API_PID=""
WEB_PID=""
cleanup() {
  trap - EXIT INT TERM
  log "Shutting down dev stack"
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "Starting FastAPI advisor on :${DEV_FASTAPI_PORT}"
(
  PYTHONUNBUFFERED=1 \
  PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  DATABASE_URL="$DATABASE_URL" \
  ADVISOR_DEMO_USER_ID="$DEV_USER_ID" \
  "${REPO_ROOT}/.venv/bin/uvicorn" advisor.api.dev:app \
    --host 127.0.0.1 --port "$DEV_FASTAPI_PORT" 2>&1 \
  | awk -v tag="[api]" '{ print tag, $0; fflush() }'
) &
API_PID=$!

log "Starting Next.js dev server on :${DEV_WEB_PORT}"
# Clerk keys are NOT forced to empty here (unlike e2e-up.sh). They
# come from your shell env if exported, or from web/.env.local
# (which next dev loads natively). When unset, Clerk runs in
# fallback mode and the legacy /access password gate takes over.
(
  cd "${REPO_ROOT}/web" && \
  ADVISOR_API_URL="http://127.0.0.1:${DEV_FASTAPI_PORT}" \
  ADVISOR_DEMO_USER_ID="$DEV_USER_ID" \
  npx next dev -p "$DEV_WEB_PORT" 2>&1 \
  | awk -v tag="[web]" '{ print tag, $0; fflush() }'
) &
WEB_PID=$!

log "Dev stack running — Ctrl+C to stop"
echo "  FastAPI:  http://127.0.0.1:${DEV_FASTAPI_PORT}/healthz"
echo "  Web:      http://localhost:${DEV_WEB_PORT}"

wait
