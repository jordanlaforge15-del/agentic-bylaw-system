#!/usr/bin/env bash
# Boot the end-to-end UI test stack on ports 3001 (Next.js) + 8001
# (FastAPI), wired against the DEDICATED ephemeral e2e Postgres
# instance (compose service ``postgres-e2e``, host port 5433 by
# default; ABS-428). The dev Postgres (service ``postgres``, :5432,
# database ``layer1``) is never touched by this script.
#
# Idempotent: re-running while the stack is already up is a no-op for
# already-healthy components. Use scripts/e2e-down.sh to tear down —
# teardown destroys the e2e container AND its data volume, so every
# fresh up gets a pristine instance (initdb + migrations + seeds).
#
# Env vars consumed:
#   E2E_TEST_DB    — DB name to create/migrate (default ``layer1_test``)
#   E2E_FASTAPI_PORT — port for the test FastAPI (default 8001)
#   E2E_WEB_PORT    — port for the Next.js dev server (default 3001)
#   PG_PORT         — host port that the postgres-e2e container
#                     publishes (default 5433). Override per worktree
#                     (PG_PORT=543X convention) to allow parallel
#                     `make e2e` runs; the compose file reads
#                     E2E_POSTGRES_HOST_PORT which this script exports
#                     below.
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
PG_PORT="${PG_PORT:-5433}"

DATABASE_URL_E2E="postgresql+psycopg://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${E2E_TEST_DB}"
# Export so child processes (Playwright globalSetup, seed scripts) inherit the
# correct URL without callers having to re-derive it from PG_PORT themselves.
export DATABASE_URL="$DATABASE_URL_E2E"
# The node ``pg`` client doesn't grok SQLAlchemy's ``+psycopg`` suffix
# (it parses the URL with whatwg URL semantics and bails on the
# unrecognised scheme). Build a separate pg-friendly URL for the
# Next.js dev server so route handlers that talk to Postgres directly
# (e.g. ``/api/invite`` → ``invite_request``) can reach the test DB.
DATABASE_URL_E2E_PG="postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${E2E_TEST_DB}"
PSQL_BASE_URL="postgresql://${PG_USER}:${PG_PASSWORD}@${PG_HOST}:${PG_PORT}/postgres"

# Compose reads this for the postgres-e2e `ports:` host-side binding.
# Keep it aligned with PG_PORT so a worktree overriding one always
# overrides the other consistently. Note this is deliberately NOT
# POSTGRES_HOST_PORT — that knob belongs to the dev `postgres` service
# and this script must never influence it (ABS-428).
export E2E_POSTGRES_HOST_PORT="$PG_PORT"

# The compose service name for the dedicated e2e instance. Every
# docker-compose invocation in this script is scoped to it explicitly;
# the dev `postgres` service is out of reach by construction.
E2E_PG_SERVICE="postgres-e2e"

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

require_docker() {
  # Fail fast with an actionable message when the Docker daemon isn't
  # reachable. Without this, the first `docker compose` call — the stale
  # container probe in ensure_postgres — is a bare `x="$(... )"` command
  # substitution. Under `set -euo pipefail` a daemon-unreachable failure
  # there exits the script rc=1 with the daemon's error swallowed by
  # `2>/dev/null`, so `make e2e-up` dies as an opaque "Error 1" right after
  # the "Creating web/.env.local" log and no clue as to why. On macOS the
  # daemon is often merely mid-start (Docker Desktop), so retry briefly
  # before giving up.
  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    echo "error: Docker Compose required but not installed." >&2
    exit 1
  fi
  local err=""
  for _ in $(seq 1 15); do
    err=$(docker info 2>&1 >/dev/null) && return 0
    sleep 1
  done
  echo "error: Docker daemon not reachable after 15s. Is Docker running?" >&2
  echo "last docker info error:" >&2
  echo "$err" >&2
  exit 1
}

ensure_compose_prereqs() {
  # docker-compose.yml's `web` service declares env_file: ./web/.env.local.
  # Compose validates the whole file on every command, so a missing
  # .env.local makes `docker compose exec postgres ...` fail rc=1 with
  # no postgres-related output — which previously surfaced as a silent
  # 60s readiness timeout. Materialize the file before any compose call.
  if [[ ! -f "${REPO_ROOT}/web/.env.local" ]]; then
    log "Creating web/.env.local from example (required by docker-compose.yml)"
    cp "${REPO_ROOT}/web/.env.local.example" "${REPO_ROOT}/web/.env.local"
  fi
}

ensure_postgres() {
  # ABS-170: Detect a stale container whose published port no longer matches
  # the requested PG_PORT. `docker compose up -d` is name-keyed: if a container
  # already exists, it attaches without re-applying the port mapping, so a
  # worktree previously launched with a different PG_PORT silently keeps the
  # old port. NM's reviewer then runs e2e against the wrong port and every
  # cycle fails at startup. Force-recreate when we detect the mismatch.
  # (ABS-428: the e2e instance is ephemeral — losing its content on
  # recreate is by design; every up migrates and seeds from scratch.)
  #
  # We use `docker inspect .HostConfig.PortBindings` rather than `docker port`
  # because the latter only reports for *running* containers — and our worst
  # case is a stale container that's been stopped and restarted with a new
  # port (it shows up here in `ps -aq` but exposes nothing via `docker port`).
  local stale_container stale_port
  # `|| true` so a transient probe failure can't silently `set -e` exit the
  # whole run before any diagnostic log prints. require_docker has already
  # confirmed the daemon is reachable, so an empty result here just means
  # "no existing container", which is the normal fresh-worktree case.
  stale_container="$(docker_compose ps -aq "$E2E_PG_SERVICE" 2>/dev/null | head -1 || true)"
  if [[ -n "$stale_container" ]]; then
    stale_port="$(docker inspect "$stale_container" \
      --format '{{range $p, $bs := .HostConfig.PortBindings}}{{if eq $p "5432/tcp"}}{{range $bs}}{{.HostPort}}{{end}}{{end}}{{end}}' \
      2>/dev/null)"
    if [[ -n "$stale_port" && "$stale_port" != "$PG_PORT" ]]; then
      log "Removing stale ${E2E_PG_SERVICE} container (port was ${stale_port}, now ${PG_PORT})"
      docker_compose rm -fsv "$E2E_PG_SERVICE" >/dev/null 2>&1 || true
    fi
  fi

  if docker_compose exec -T "$E2E_PG_SERVICE" pg_isready -U "$PG_USER" -d postgres >/dev/null 2>&1; then
    log "E2E Postgres already healthy on :${PG_PORT} — reusing"
    return 0
  fi
  log "Starting e2e Postgres container (${E2E_PG_SERVICE}) on :${PG_PORT}"
  # `docker compose up` creates a per-project bridge network. On a machine
  # running many parallel worktree stacks, Docker's default address pool can
  # become fully subnetted, and compose aborts with:
  #   "all predefined address pools have been fully subnetted"
  # Orphaned networks left behind by torn-down sibling worktrees are the usual
  # cause (each `nm-abs-NNN_default` eats a subnet until pruned). Detect that
  # signature, prune unused networks (safe: `docker network prune` only removes
  # networks that no container is attached to, so live sibling stacks survive),
  # and retry once before giving up with an actionable message. Without this the
  # reviewer sees a bare `make: *** [e2e-up] Error 1`, indistinguishable from a
  # code failure.
  local up_out
  if ! up_out="$(docker_compose up -d "$E2E_PG_SERVICE" 2>&1)"; then
    if printf '%s' "$up_out" | grep -qi 'address pools have been fully subnetted'; then
      log "Docker address pool exhausted — pruning orphaned networks and retrying"
      docker network prune -f >/dev/null 2>&1 || true
      if ! up_out="$(docker_compose up -d "$E2E_PG_SERVICE" 2>&1)"; then
        echo "error: could not start Postgres container after pruning networks" >&2
        echo "$up_out" >&2
        echo "       Docker predefined address pools are exhausted. Reclaim network" >&2
        echo "       space with: docker network prune -f (and docker system prune" >&2
        echo "       if needed), then retry." >&2
        exit 1
      fi
    else
      echo "error: could not start Postgres container" >&2
      echo "$up_out" >&2
      exit 1
    fi
  fi
  local last_err=""
  for _ in $(seq 1 60); do
    last_err=$(docker_compose exec -T "$E2E_PG_SERVICE" pg_isready -U "$PG_USER" -d postgres 2>&1) && return 0
    sleep 1
  done
  echo "error: Postgres did not become ready" >&2
  echo "last pg_isready output:" >&2
  echo "$last_err" >&2
  exit 1
}

ensure_test_db() {
  # POSTGRES_DB=layer1_test in the compose service means initdb already
  # created the default test DB on a fresh instance; this remains for
  # the E2E_TEST_DB-override case and as an idempotent safety net.
  log "Ensuring test database ${E2E_TEST_DB} exists"
  local exists
  exists=$(docker_compose exec -T "$E2E_PG_SERVICE" psql -U "$PG_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${E2E_TEST_DB}'" || true)
  if [[ "$exists" != "1" ]]; then
    docker_compose exec -T "$E2E_PG_SERVICE" psql -U "$PG_USER" -d postgres -c \
      "CREATE DATABASE \"${E2E_TEST_DB}\""
    echo "created database ${E2E_TEST_DB}"
  else
    echo "database ${E2E_TEST_DB} already exists"
  fi
}

run_migrations() {
  log "Running Alembic migrations against ${E2E_TEST_DB}"

  # ABS-318: Guard against dual-head chains before attempting the upgrade.
  # Two concurrent agents can both parent their migration to the same
  # down_revision; the resulting dual head makes `alembic upgrade head`
  # fail with "Multiple head revisions are present". Catching it here
  # produces an actionable error instead of an opaque migration crash.
  local alembic_heads head_count
  alembic_heads=$(
    DATABASE_URL="$DATABASE_URL_E2E" \
    PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
    "${REPO_ROOT}/.venv/bin/alembic" -c "${REPO_ROOT}/alembic.ini" heads 2>&1
  )
  head_count=$(printf '%s\n' "$alembic_heads" | grep -c '(head)' || true)
  if [[ "$head_count" -gt 1 ]]; then
    printf '\n' >&2
    printf '!! ============================================================ !!\n' >&2
    printf '!! ERROR: Multiple Alembic heads detected (%s heads)\n' "$head_count" >&2
    printf '!!\n' >&2
    printf '%s\n' "$alembic_heads" | grep '(head)' | sed 's/^/!!   /' >&2
    printf '!!\n' >&2
    printf '!! Two concurrent migration branches both parented to the same\n' >&2
    printf '!! down_revision. `alembic upgrade head` would fail.\n' >&2
    printf '!!\n' >&2
    printf '!! To fix on a feature branch, run from the worktree root:\n' >&2
    printf '!!   python scripts/rechain_migration.py\n' >&2
    printf '!!\n' >&2
    printf '!! Then commit and re-run `make e2e`.\n' >&2
    printf '!! ============================================================ !!\n\n' >&2
    exit 1
  fi

  # Pre-create alembic_version with a wider column. The default
  # VARCHAR(32) is one char too short for the revision id
  # ``0008_advisor_billing_subscription`` (33 chars), which makes a
  # fresh migration chain fail. Pre-creating with VARCHAR(255) is the
  # least invasive fix and only affects fresh databases; existing
  # databases keep their column as-is.
  docker_compose exec -T "$E2E_PG_SERVICE" psql -U "$PG_USER" -d "$E2E_TEST_DB" \
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

record_existing_listener() {
  # When idempotency bails ("already listening"), capture the listener
  # PID into the pidfile so scripts/e2e-down.sh has a breadcrumb. Without
  # this, an orphaned stack from a prior crashed/Ctrl+C'd run cannot be
  # cleaned up by any future e2e-down — it just prints "no pidfile" and
  # silently leaves the process running.
  #
  # The warning block here is deliberately loud. Reusing an existing
  # listener is the root cause of an ongoing class of e2e failures
  # where the process on the port was launched from older code (e.g.
  # a sibling worktree, or a crashed prior run that pre-dated the
  # routes the current test suite exercises). The endpoint returns
  # 404 / 503 against tests that pass against the on-disk code, which
  # looks like a flake but is really a stale-process problem. Forcing
  # the message into the operator's eye line is cheaper than another
  # round of trace-zip archaeology.
  local port="$1"
  local pidfile="$2"
  local label="$3"
  local existing_pid existing_started_at existing_age_secs
  existing_pid="$(lsof -iTCP:"$port" -sTCP:LISTEN -tnP 2>/dev/null | head -1)"
  if [[ -n "$existing_pid" ]]; then
    echo "$existing_pid" >"$pidfile"
    # Best-effort process start time (BSD ps on macOS; falls back
    # silently on other platforms). Used only for the warning.
    existing_started_at="$(ps -o lstart= -p "$existing_pid" 2>/dev/null | sed 's/^[[:space:]]*//' || true)"
    existing_age_secs="$(ps -o etime= -p "$existing_pid" 2>/dev/null | sed 's/^[[:space:]]*//' || true)"
    printf '\n'
    printf '!! ============================================================ !!\n' >&2
    printf '!! WARNING: REUSING EXISTING %s LISTENER ON :%s\n' "$label" "$port" >&2
    printf '!!   PID:         %s\n' "$existing_pid" >&2
    if [[ -n "$existing_started_at" ]]; then
      printf '!!   started:     %s (uptime %s)\n' "$existing_started_at" "${existing_age_secs:-?}" >&2
    fi
    printf '!!\n' >&2
    printf '!! This process was NOT launched by the current e2e-up run. If it\n' >&2
    printf '!! was started from older code (sibling worktree, prior crashed\n' >&2
    printf '!! run, stale dev shell), the e2e suite will test against that old\n' >&2
    printf '!! code and any new endpoints / fixes will appear to fail.\n' >&2
    printf '!!\n' >&2
    printf '!! To start a fresh stack:  ./scripts/e2e-down.sh && make e2e\n' >&2
    printf '!! ============================================================ !!\n\n' >&2
  else
    printf '\n!! WARNING: %s already listening on :%s but PID lookup failed.\n' "$label" "$port" >&2
    printf '!! Run ./scripts/e2e-down.sh and manually kill the holder before re-running.\n\n' >&2
  fi
}

wait_for_port() {
  # Args: port, label, [timeout=30], [pidfile=""]
  #
  # ABS-175: detecting "port is bound" is necessary but not sufficient
  # for "the process we just launched is ready and will stay up." A
  # `next dev` that's about to crash on a port collision shows up in
  # LISTEN state for a fraction of a second between bind and exit; the
  # naive check fired, callers moved on, and the resulting test cascade
  # looked like a 200-failure flake instead of a startup error.
  #
  # After the socket is open we now sleep ${SETTLE_SECS} and re-check
  # both the port AND (when a pidfile is supplied) that the recorded
  # process is still alive. If the post-settle check fails we surface
  # the tail of the matching log file so the operator sees the real
  # cause without grepping. Callers that don't pass a pidfile fall
  # back to a port-only re-verification, preserving backward compat.
  local port="$1"
  local label="$2"
  local timeout="${3:-30}"
  local pidfile="${4:-}"
  local settle_secs="${WAIT_FOR_PORT_SETTLE_SECS:-2}"
  for _ in $(seq 1 "$timeout"); do
    if is_listening "$port"; then
      sleep "$settle_secs"
      if [[ -n "$pidfile" && -f "$pidfile" ]]; then
        local pid; pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
          echo "error: ${label} on :${port} bound briefly then exited (pid ${pid})" >&2
          local logfile="${LOG_DIR}/$(basename "$pidfile" .pid).log"
          if [[ -f "$logfile" ]]; then
            echo "---- last 20 lines of ${logfile} ----" >&2
            tail -20 "$logfile" >&2 || true
            echo "---- end ${logfile} ----" >&2
          fi
          return 1
        fi
      fi
      if ! is_listening "$port"; then
        echo "error: ${label} on :${port} became unavailable after startup" >&2
        return 1
      fi
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
    record_existing_listener "$E2E_FASTAPI_PORT" "${PID_DIR}/fastapi.pid" "fastapi"
    return 0
  fi
  log "Starting FastAPI test server (advisor.api.e2e_server) on :${E2E_FASTAPI_PORT}"
  # PYTHONPATH ensures we run the worktree's src/, not whatever the
  # venv's editable install points at — important when the venv was
  # provisioned against a sibling worktree (e.g. main).
  # The subshell-level `</dev/null >>log 2>&1` is what prevents
  # callers piping this script (e.g. `bash e2e-up.sh | tail`) from
  # hanging: the subshell lingers as long as uvicorn runs, and would
  # otherwise hold the caller's pipe open via its inherited stdout.
  #
  # ABS-324: the prod launch posture is Answers-only (the Conversation
  # continue-case entry is gated OFF by default). But the AC also requires
  # that the Conversation product still works when its flag is enabled, and
  # the legacy /app continue-case suite (smoke 03-open-case,
  # case-existing-match, case-open-idempotency) drives exactly that path.
  # Enable ADVISOR_CONVERSATION_ENTRY_ENABLED here so those specs exercise
  # REAL behavior with no API stubbing; the launch-off posture is asserted
  # separately by abs320 (which stubs the flag false per-test).
  ( cd "$REPO_ROOT" && \
    DATABASE_URL="$DATABASE_URL_E2E" \
    PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
    ADVISOR_HOST=127.0.0.1 \
    ADVISOR_PORT="$E2E_FASTAPI_PORT" \
    ADVISOR_E2E_CORS_ORIGINS="http://localhost:${E2E_WEB_PORT}" \
    ADVISOR_ADMIN_API_ENABLED=true \
    ADVISOR_ADMIN_CLERK_USER_IDS="${E2E_USER_ID}" \
    ADVISOR_CONVERSATION_ENTRY_ENABLED=true \
    ADVISOR_ENABLED_QUESTIONS='*' \
    MONITOR_TARGET_URL="http://127.0.0.1:${E2E_FASTAPI_PORT}/healthz" \
    nohup "${REPO_ROOT}/.venv/bin/uvicorn" advisor.api.e2e_server:app \
      --host 127.0.0.1 --port "$E2E_FASTAPI_PORT" &
    echo $! >"${PID_DIR}/fastapi.pid"
    disown
  ) </dev/null >>"${LOG_DIR}/fastapi.log" 2>&1
  wait_for_port "$E2E_FASTAPI_PORT" "FastAPI" 30 "${PID_DIR}/fastapi.pid"
}

start_web() {
  if is_listening "$E2E_WEB_PORT"; then
    record_existing_listener "$E2E_WEB_PORT" "${PID_DIR}/web.pid" "next dev"
    return 0
  fi
  log "Starting Next.js dev server on :${E2E_WEB_PORT}"
  # DEMO_PASSWORD must be set so the proxy.ts fallback gate has a
  # known shared password. Playwright fixtures POST to /api/access
  # with this value to mint the abs_demo cookie before each test.
  # See start_fastapi for why the subshell redirection matters.
  #
  # ABS-19: Clerk mock mode. CLERK_SECRET_KEY is set to a test key that
  # passes isClerkConfigured() so proxy.ts takes the clerkMiddleware
  # branch. E2E_CLERK_MOCK=1 triggers the Turbopack resolveAlias in
  # next.config.ts that swaps @clerk/nextjs/server with our mock module
  # (web/lib/clerk-test-mock.ts). The mock reads test cookies for auth
  # state and forwards JWTs minted by /v1/_test/mint-jwt to FastAPI.
  # DEMO_PASSWORD is still set as a fallback for the /api/access endpoint
  # used by some legacy fixtures.
  ( cd "${REPO_ROOT}/web" && \
    NEXT_DIST_DIR=.next-e2e \
    ADVISOR_API_URL="http://127.0.0.1:${E2E_FASTAPI_PORT}" \
    ADVISOR_DEMO_USER_ID="$E2E_USER_ID" \
    CLERK_SECRET_KEY="sk_test_e2e_mock_key_for_testing_only_0000000000" \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="" \
    ADVISOR_ADMIN_CLERK_USER_IDS="${E2E_USER_ID}" \
    E2E_CLERK_MOCK=1 \
    DEMO_PASSWORD="${E2E_DEMO_PASSWORD:-e2e-demo-pw}" \
    ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-e2e-admin-pw}" \
    DATABASE_URL="$DATABASE_URL_E2E_PG" \
    NEXT_PUBLIC_GENERAL_FEEDBACK_ENABLED=true \
    nohup npx next dev -p "$E2E_WEB_PORT" &
    echo $! >"${PID_DIR}/web.pid"
    disown
  ) </dev/null >>"${LOG_DIR}/web.log" 2>&1
  # next dev takes longer to compile on first start; allow up to 90s.
  wait_for_port "$E2E_WEB_PORT" "Next.js" 90 "${PID_DIR}/web.pid"
}

main() {
  require_venv
  require_docker
  ensure_compose_prereqs
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
