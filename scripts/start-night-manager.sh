#!/usr/bin/env bash
set -euo pipefail

# Night Manager launch script
# Wraps the NM in caffeinate + tmux for overnight unattended execution.
#
# Usage:
#   ./scripts/start-night-manager.sh [--max-agents 3] [--label Triaged] [--deploy] [--dry-run]
#
# Prerequisites:
#   - LINEAR_API_KEY in .env or environment
#   - tmux installed
#   - Python venv at .venv/ with project deps
#
# Monitor (session name is derived from REPO_ROOT — see SESSION_NAME below):
#   tmux attach -t <session-name-printed-by-this-script>
#   tail -f .night-manager/nm-*.log
#   tail -f .night-manager/logs/ABS-*.jsonl | jq .

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ABS-152: worktree-scoped tmux session name.
# Hashing REPO_ROOT means each worktree (main checkout, .claude/worktrees/*) gets a
# unique session, so a worktree's e2e suite that exercises the launcher can never
# kill an unrelated NM session in another worktree.
SESSION_NAME="nm-$(printf '%s' "$REPO_ROOT" | shasum -a 1 | cut -c1-8)"

cd "$REPO_ROOT"

# Load .env if it exists (for LINEAR_API_KEY).
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi
unset ANTHROPIC_API_KEY 2>/dev/null || true

if [ -z "${LINEAR_API_KEY:-}" ]; then
    echo "ERROR: LINEAR_API_KEY not set. Add it to .env or export it." >&2
    exit 1
fi

# Ensure venv exists
if [ ! -d .venv ]; then
    echo "ERROR: .venv not found. Run ./scripts/dev-setup.sh first." >&2
    exit 1
fi

# Ensure required packages
.venv/bin/python -c "import httpx, anthropic, dotenv" 2>/dev/null || {
    echo "Installing missing dependencies..."
    .venv/bin/pip install httpx anthropic python-dotenv
}

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE=".night-manager/nm-${TIMESTAMP}.log"
mkdir -p .night-manager

# Kill any existing NM session FOR THIS WORKTREE (other worktrees use different hashes — see SESSION_NAME).
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

# Launch in tmux with caffeinate to prevent sleep
tmux new-session -d -s "$SESSION_NAME" \
    "caffeinate -s ${REPO_ROOT}/.venv/bin/python -m scripts.night_manager $* 2>&1 | tee ${LOG_FILE}; echo '--- Night Manager exited. Press any key to close. ---'; read"

echo "Night Manager started in tmux session '${SESSION_NAME}' (repo: ${REPO_ROOT})"
echo ""
echo "  Attach:    tmux attach -t ${SESSION_NAME}"
echo "  Logs:      tail -f ${LOG_FILE}"
echo "  Agent logs: tail -f .night-manager/logs/ABS-*.jsonl | jq ."
echo "  State:     cat .night-manager/state.json | jq ."
echo "  Kill:      tmux kill-session -t ${SESSION_NAME}"
