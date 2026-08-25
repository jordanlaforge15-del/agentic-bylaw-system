#!/usr/bin/env bash
# Bring up a FastAPI advisor for eval runs, with its billing mode stated
# out loud (ABS-515).
#
# WHY THIS EXISTS
# ---------------
# `scripts/run_test_prompts.py` drives whatever advisor is listening on a
# port. It used to have no way to know how that process was configured,
# which is how an eight-case sweep billed ~$1.70 nobody had agreed to
# spend. The fix has two halves: `/healthz` now reports the gateway the
# advisor actually built and whether it bills per token, and the runner
# refuses to start against a metered one without `--allow-metered`.
#
# This script is the other end of that handshake: one documented command
# that starts an eval advisor, pins the provider and model explicitly
# rather than inheriting whatever `.env` happens to hold, and prints the
# exact runner invocation — including the consent flag — to paste next.
#
# ON `claude_code`
# ----------------
# The original plan here was to pin ADVISOR_LLM_PROVIDER=claude_code so
# eval turns billed an operator's subscription instead of API credits.
# ABS-522 removed that backend: on an otherwise identical eight-case run
# it scored 0/8 golden passes against the API backend's 3/8, stopping its
# research roughly four times sooner. There is no cheap provider to
# default to any more. What is left to fix is the *silence* — a metered
# eval run is now a deliberate, acknowledged act.
#
# THE `.env` TRAP THIS AVOIDS
# ---------------------------
# `set -a; . ./.env; set +a` exports every name in `.env` into the
# process environment. That is how the expensive run started: it left
# ADVISOR_LLM_PROVIDER at its default and promoted ANTHROPIC_API_KEY to
# an inheritable env var, so the advisor booted metered and nothing said
# so. This script reads `.env` in a subshell and re-exports only the
# handful of names the advisor needs, then echoes what it resolved.
#
# Env vars consumed:
#   ANTHROPIC_API_KEY   — required (shell env wins over .env)
#   ADVISOR_EVAL_PORT   — default 8000
#   ADVISOR_EVAL_MODEL  — default: whatever ADVISOR_LLM_MAIN_MODEL /
#                         .env resolves to; overriding it here is how you
#                         run a cheaper baseline sweep
#   DATABASE_URL        — default from .env, else the local dev DB
#   ADVISOR_DEMO_USER_ID — default demo-user-1
#
# Foreground; Ctrl+C stops it. For the full manual-testing stack
# (advisor + Next.js) use scripts/dev-up.sh instead — this one is
# deliberately backend-only, because eval runs talk to /v1/chat directly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PORT="${ADVISOR_EVAL_PORT:-8000}"
DEMO_USER_ID="${ADVISOR_DEMO_USER_ID:-demo-user-1}"

if [[ ! -x "${REPO_ROOT}/.venv/bin/uvicorn" ]]; then
  echo "error: ${REPO_ROOT}/.venv missing uvicorn. Run ./scripts/dev-setup.sh first." >&2
  exit 1
fi

# Read .env WITHOUT `set -a`. `env -i` runs the loader with an empty
# environment so the subshell cannot see (and therefore cannot echo back)
# anything already exported here; only names that genuinely came from the
# file are printed, and only the ones we ask for.
read_dotenv() {
  local key="$1"
  [[ -f .env ]] || return 0
  env -i "PATH=${PATH}" "BASH_ENV=" bash -c '
    set -euo pipefail
    # shellcheck disable=SC1091
    source .env 2>/dev/null || true
    printf "%s" "${'"$key"':-}"
  ' 2>/dev/null || true
}

: "${ANTHROPIC_API_KEY:=$(read_dotenv ANTHROPIC_API_KEY)}"
: "${DATABASE_URL:=$(read_dotenv DATABASE_URL)}"
: "${DATABASE_URL:=postgresql+psycopg://layer1:layer1@localhost:5432/layer1}"

MODEL="${ADVISOR_EVAL_MODEL:-${ADVISOR_LLM_MAIN_MODEL:-$(read_dotenv ADVISOR_LLM_MAIN_MODEL)}}"
: "${MODEL:=claude-opus-4-5}"

if [[ -z "${ANTHROPIC_API_KEY}" ]]; then
  cat >&2 <<'MSG'
error: ANTHROPIC_API_KEY is not set and .env does not define it.

Since ABS-522 the advisor has exactly one provider and it needs this key
— there is no subscription-billed fallback to run evals on any more.
Export the key, or add it to .env, and re-run.
MSG
  exit 1
fi

cat <<MSG

  ┌───────────────────────────────────────────────────────────────────┐
  │  EVAL ADVISOR — TURNS ON THIS PROCESS BILL METERED API CREDITS    │
  └───────────────────────────────────────────────────────────────────┘

  provider : anthropic  (metered; the claude_code subscription backend
             was removed in ABS-522 — it answered worse, 0/8 vs 3/8)
  model    : ${MODEL}
  port     : ${PORT}
  database : ${DATABASE_URL%%\?*}

  A full 8-case sweep on Opus costs roughly \$1.50-\$2.00. Run a single
  case first (--ids TC-001) and multiply before committing to the sweep.

  Once this is up, in another shell:

    .venv/bin/python scripts/run_test_prompts.py \\
      --base-url http://127.0.0.1:${PORT} \\
      --model ${MODEL} \\
      --allow-metered --ids TC-001

  Then grade it:

    .venv/bin/python scripts/verify_run.py evals/runs/<timestamp>

MSG

# Pinned explicitly rather than inherited: the provider is the value
# whose default caused the incident, so it is stated at the call site
# where `make advisor-eval` shows it, not left to .env resolution order.
exec env \
  PYTHONUNBUFFERED=1 \
  PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
  ADVISOR_LLM_PROVIDER=anthropic \
  ADVISOR_LLM_MAIN_MODEL="${MODEL}" \
  ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  DATABASE_URL="${DATABASE_URL}" \
  ADVISOR_DEMO_USER_ID="${DEMO_USER_ID}" \
  "${REPO_ROOT}/.venv/bin/uvicorn" advisor.api.dev:app \
  --host 127.0.0.1 --port "${PORT}"
