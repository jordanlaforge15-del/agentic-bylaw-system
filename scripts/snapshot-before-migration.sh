#!/usr/bin/env bash
# Take a *labelled* snapshot of the local dev Postgres before something
# mutates it (an `alembic upgrade`, a repath, a backfill).
#
# This is the fence described in ABS-499. The nightly job
# (scripts/backup-dev-db.sh) writes into a 7-slot day-of-week rotation, so a
# data migration that runs between two nightly dumps can have its pre-change
# state overwritten within a single rotation cycle. Labelled snapshots live in
# a `labelled/` subdirectory under their own timestamped filenames, so the
# rotation never touches them and the caller's tag says what they were taken
# before.
#
# Usage:
#   scripts/snapshot-before-migration.sh <tag>
#
# On success the absolute path of the snapshot is the only thing written to
# stdout (progress and errors go to stderr and the shared backup.log), so
# callers can capture it:
#
#   snap="$(scripts/snapshot-before-migration.sh abs-488-repath)"
#
# Exits nonzero — leaving no partial file behind — if docker is missing, the
# container is not running, or pg_dump fails. Callers are expected to treat a
# nonzero exit as "do not proceed with the migration".
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
LABELLED_DIR="$BACKUP_DIR/labelled"

TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"
STAMP="$(date '+%Y%m%dT%H%M%S')"
LOG="$BACKUP_DIR/backup.log"

log() { printf '[%s] snapshot: %s\n' "$TS" "$*" | tee -a "$LOG" >&2; }

RAW_TAG="${1:-}"
if [ -z "$RAW_TAG" ]; then
  echo "usage: $(basename "$0") <tag>" >&2
  exit 64
fi

# Keep the tag filesystem-safe: it comes from a caller-supplied string
# (a script name, a migration revision) and lands in a filename.
# `tr -s` squeezes the runs a substitution leaves behind, and consecutive dots
# collapse to one so no tag can produce a `..` component.
TAG="$(printf '%s' "$RAW_TAG" \
  | tr -c 'A-Za-z0-9._-' '-' \
  | tr -s '-' \
  | sed 's/\.\{2,\}/./g; s/^[-.]*//; s/[-.]*$//')"
if [ -z "$TAG" ]; then
  echo "error: tag '$RAW_TAG' contains no usable characters" >&2
  exit 64
fi

mkdir -p "$LABELLED_DIR"

OUT="$LABELLED_DIR/layer1-$TAG-$STAMP.dump"
TMP="$OUT.tmp"

# Locate docker — callers may be cron or a minimal-PATH shell that won't
# include Docker Desktop's shim. Same fallback as backup-dev-db.sh.
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
  log "ERROR: docker not found on PATH ($PATH); refusing to snapshot '$TAG'"
  exit 1
fi

if ! docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q '^true$'; then
  log "ERROR: container '$CONTAINER' is not running; refusing to snapshot '$TAG'"
  exit 1
fi

log "Starting labelled pg_dump of $DB from $CONTAINER -> $OUT"

if docker exec -i "$CONTAINER" pg_dump -U "$USER_" -d "$DB" -Fc > "$TMP"; then
  mv -f "$TMP" "$OUT"
  SIZE="$(wc -c < "$OUT" | tr -d ' ')"
  log "OK: wrote $OUT ($SIZE bytes)"
  printf '%s\n' "$OUT"
else
  rc=$?
  rm -f "$TMP"
  log "ERROR: pg_dump failed (exit $rc); no snapshot written for '$TAG'"
  exit "$rc"
fi
