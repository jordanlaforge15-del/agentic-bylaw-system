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
# Match e2e-up.sh defaults so the lsof fallback in stop_pid targets the
# right port when the pidfile is missing/stale. Worktrees that overrode
# these on the e2e-up call must export the same values here.
E2E_FASTAPI_PORT="${E2E_FASTAPI_PORT:-8001}"
E2E_WEB_PORT="${E2E_WEB_PORT:-3001}"
PG_USER="${PG_USER:-layer1}"
STATE_DIR="${REPO_ROOT}/.e2e"
PID_DIR="${STATE_DIR}/pids"

log() { printf '\n==> %s\n' "$1"; }

stop_pid() {
  local pidfile="$1"
  local label="$2"
  local fallback_port="${3:-}"
  local pid=""
  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
  fi
  # Fall back to lsof when the pidfile is missing/empty/stale. e2e-up.sh
  # writes pidfiles on the "already listening" path now, but older runs
  # (or external processes squatting on the port) won't have one — the
  # lsof lookup ensures teardown still finds and kills the holder.
  if { [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; } && [[ -n "$fallback_port" ]]; then
    # ABS-170: trailing `|| true` keeps the substitution rc=0 when lsof
    # finds no listener on the port. Without it, `set -euo pipefail`
    # treats the failed pipeline as a script-level error and e2e-down
    # exits silently after "Stopping Next.js" — leaving the postgres
    # container alive and reproducing the symptom we set out to fix.
    local lsof_pid=""
    lsof_pid="$(lsof -iTCP:"$fallback_port" -sTCP:LISTEN -tnP 2>/dev/null | head -1 || true)"
    if [[ -n "$lsof_pid" ]]; then
      echo "${label}: pidfile missing/stale — using :${fallback_port} holder PID ${lsof_pid}"
      pid="$lsof_pid"
    fi
  fi
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
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
  else
    echo "${label}: nothing to kill (pidfile=${pidfile}${fallback_port:+, port=${fallback_port}})"
  fi
  rm -f "$pidfile"

  # ABS-176: post-kill verification by port. Killing the recorded PID
  # is not enough when the parent was a wrapper shell (nohup + disown
  # + subshell wrapping make macOS process-group semantics flaky); the
  # actual server child gets reparented to init and survives. Next
  # e2e-up then prints REUSING-EXISTING-LISTENER and silently adopts
  # the stale process — the failure mode this script is supposed to
  # prevent. Always verify the port is free at the end of stop_pid;
  # this also catches the "nothing to kill via pidfile but port is
  # still bound from a sibling process" case.
  if [[ -z "$fallback_port" ]]; then
    return 0
  fi
  local survivor=""
  survivor="$(lsof -iTCP:"$fallback_port" -sTCP:LISTEN -tnP 2>/dev/null | head -1 || true)"
  if [[ -z "$survivor" ]]; then
    return 0
  fi
  echo "${label}: :${fallback_port} still bound after SIGTERM — killing survivor PID ${survivor}"
  kill "$survivor" 2>/dev/null || true
  for _ in $(seq 1 5); do
    if ! lsof -iTCP:"$fallback_port" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  if lsof -iTCP:"$fallback_port" -sTCP:LISTEN >/dev/null 2>&1; then
    kill -9 "$survivor" 2>/dev/null || true
    echo "${label}: SIGKILLed survivor PID ${survivor}"
  fi
}

log "Stopping Next.js"
stop_pid "${PID_DIR}/web.pid" "web" "$E2E_WEB_PORT"

log "Stopping FastAPI"
stop_pid "${PID_DIR}/fastapi.pid" "fastapi" "$E2E_FASTAPI_PORT"

docker_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

if [[ "$DROP_DB" -eq 1 ]]; then
  log "Dropping test database ${E2E_TEST_DB}"
  docker_compose_cmd exec -T postgres psql -U "$PG_USER" -d postgres -c \
    "DROP DATABASE IF EXISTS \"${E2E_TEST_DB}\""
fi

# ABS-170: Remove the postgres container so the next `e2e-up` cannot attach
# to it with a stale port mapping. The compose project's named volume is
# preserved (no -v), so DB content survives container recreation. Without
# this, a worktree relaunched with a different PG_PORT silently keeps the
# old port and NM's reviewer e2e fails at startup with no useful signal.
log "Removing Postgres container (volume preserved)"
docker_compose_cmd rm -fs postgres >/dev/null 2>&1 || true

log "E2E stack is down"
