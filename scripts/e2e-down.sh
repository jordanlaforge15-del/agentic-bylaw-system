#!/usr/bin/env bash
# Stop the FastAPI + Next.js processes spawned by ./scripts/e2e-up.sh,
# then DESTROY the dedicated e2e Postgres container and its data volume
# (ABS-428). The e2e instance is ephemeral by contract: every
# `e2e-up` after this teardown boots a pristine instance and re-runs
# migrations + seeds, so no e2e state can leak between runs. The dev
# Postgres (compose service ``postgres``, :5432) is never touched.
#
# --drop-db is accepted for backward compatibility but is a no-op now:
# the whole instance is destroyed on every teardown anyway.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ "${1:-}" == "--drop-db" ]]; then
  echo "note: --drop-db is obsolete — the e2e Postgres instance (container" >&2
  echo "      + volume) is destroyed on every teardown since ABS-428." >&2
fi

# Match e2e-up.sh defaults so the lsof fallback in stop_pid targets the
# right port when the pidfile is missing/stale. Worktrees that overrode
# these on the e2e-up call must export the same values here.
E2E_FASTAPI_PORT="${E2E_FASTAPI_PORT:-8001}"
E2E_WEB_PORT="${E2E_WEB_PORT:-3001}"
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

# ABS-428: destroy the dedicated e2e Postgres instance — container AND
# data volume. This is what makes the instance ephemeral: the next
# `e2e-up` boots from a fresh initdb and re-runs migrations + seeds, so
# no document/case/user state can carry over between runs. It also
# subsumes the ABS-170 stale-port concern (no container left to attach
# to with an outdated port mapping).
#
# Scoped strictly to the `postgres-e2e` service; the dev `postgres`
# service and its `layer1-postgres` volume are never touched here.
log "Destroying e2e Postgres container + volume (postgres-e2e)"
# `rm -fsv` stops+removes the container and any anonymous volumes.
docker_compose_cmd rm -fsv postgres-e2e >/dev/null 2>&1 || true

# The named data volume (`layer1-postgres-e2e` in docker-compose.yml) is
# not removed by `rm -v` (named volumes are preserved by design there),
# so remove it explicitly. Match on the compose-assigned labels rather
# than a computed "<project>_layer1-postgres-e2e" name so a custom
# COMPOSE_PROJECT_NAME or compose's name normalisation can't cause a
# miss — but filter by THIS project's name so sibling worktrees'
# in-flight e2e volumes survive.
compose_project_name() {
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    printf '%s' "$COMPOSE_PROJECT_NAME" | tr '[:upper:]' '[:lower:]'
  else
    basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_-]//g; s/^[_-]+//'
  fi
}
e2e_volumes="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$(compose_project_name)" \
  --filter "label=com.docker.compose.volume=layer1-postgres-e2e" 2>/dev/null || true)"
if [[ -n "$e2e_volumes" ]]; then
  # shellcheck disable=SC2086
  docker volume rm -f $e2e_volumes >/dev/null 2>&1 || true
  echo "removed e2e Postgres volume(s): $e2e_volumes"
else
  echo "no e2e Postgres volume to remove"
fi

log "E2E stack is down"
