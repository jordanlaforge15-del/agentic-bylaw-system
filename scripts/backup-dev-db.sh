#!/usr/bin/env bash
# Back up the local dev Postgres (the `layer1` database in the
# `agentic-bylaw-system-postgres-1` container) to a rotating
# 7-file set keyed by day-of-week. Day 8 overwrites day 1 in place,
# so disk usage stays bounded with no separate prune step.
#
# Usage:
#   scripts/backup-dev-db.sh            # run once
#   scripts/install-backup-cron.sh      # install nightly cron
#
# Override defaults via env:
#   BYLAW_PG_CONTAINER  container name (default: agentic-bylaw-system-postgres-1)
#   BYLAW_PG_DB         database       (default: layer1)
#   BYLAW_PG_USER       user           (default: layer1)
#   BYLAW_BACKUP_DIR    output dir     (default: $HOME/backups/agentic-bylaw-system)

set -euo pipefail

CONTAINER="${BYLAW_PG_CONTAINER:-agentic-bylaw-system-postgres-1}"
DB="${BYLAW_PG_DB:-layer1}"
USER_="${BYLAW_PG_USER:-layer1}"
BACKUP_DIR="${BYLAW_BACKUP_DIR:-$HOME/backups/agentic-bylaw-system}"

DOW="$(date +%a)"                       # Mon, Tue, ... Sun
TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"
OUT="$BACKUP_DIR/layer1-$DOW.dump"
TMP="$OUT.tmp"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() { printf '[%s] %s\n' "$TS" "$*" | tee -a "$LOG" >&2; }

# Locate docker — cron's PATH is minimal and won't include Docker Desktop's
# /usr/local/bin shim on every macOS setup. Fall back to common locations.
if ! command -v docker >/dev/null 2>&1; then
  for candidate in /usr/local/bin/docker /opt/homebrew/bin/docker; do
    if [ -x "$candidate" ]; then
      PATH="$(dirname "$candidate"):$PATH"
      export PATH
      break
    fi
  done
fi

if ! command -v docker >/dev/null 2>&1; then
  log "ERROR: docker not found on PATH ($PATH); cannot back up"
  exit 1
fi

if ! docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q '^true$'; then
  log "ERROR: container '$CONTAINER' is not running; skipping backup"
  exit 1
fi

log "Starting pg_dump of $DB from $CONTAINER -> $OUT"

# -Fc = custom format: compressed, supports partial/parallel restore via
# pg_restore. Dump into a .tmp file first and atomically rename on success
# so an interrupted run never leaves a half-written file as the day's backup.
if docker exec -i "$CONTAINER" pg_dump -U "$USER_" -d "$DB" -Fc > "$TMP"; then
  mv -f "$TMP" "$OUT"
  SIZE="$(wc -c < "$OUT" | tr -d ' ')"
  log "OK: wrote $OUT ($SIZE bytes)"
else
  rc=$?
  rm -f "$TMP"
  log "ERROR: pg_dump failed (exit $rc); kept previous $OUT untouched"
  exit "$rc"
fi
