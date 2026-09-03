#!/usr/bin/env bash
# Back up the local dev Postgres (the `layer1` database in the
# `agentic-bylaw-system-postgres-1` container) on a three-tier retention
# schedule:
#
#   daily    layer1-<Dow>.dump           7 slots, keyed by day-of-week
#   weekly   layer1-weekly-<ISO week>.dump   first run of each ISO week
#   monthly  layer1-monthly-<YYYY-MM>.dump   first run of each calendar month
#
# The daily tier still rotates in place (day 8 overwrites day 1). The weekly
# and monthly tiers are promotions — a copy of the day's dump taken the first
# time the script runs in a given week / month — and are pruned explicitly by
# count, oldest first. Disk stays bounded on purpose: at most
# 7 + BYLAW_KEEP_WEEKLY + BYLAW_KEEP_MONTHLY dump files (17 by default).
#
# Files that don't match a tier's naming pattern (e.g. a hand-copied
# `layer1-pre-data-model-3.0-20260812.dump`) are never touched by the prune.
#
# Usage:
#   scripts/backup-dev-db.sh            # run once
#   scripts/backup-dev-db.sh --dry-run  # print the plan, change nothing
#   scripts/install-backup-cron.sh      # install nightly cron
#
# Override defaults via env:
#   BYLAW_PG_CONTAINER  container name (default: agentic-bylaw-system-postgres-1)
#   BYLAW_PG_DB         database       (default: layer1)
#   BYLAW_PG_USER       user           (default: layer1)
#   BYLAW_BACKUP_DIR    output dir     (default: $HOME/backups/agentic-bylaw-system)
#   BYLAW_KEEP_WEEKLY   weekly slots   (default: 4)
#   BYLAW_KEEP_MONTHLY  monthly slots  (default: 6)
#   BYLAW_BACKUP_DATE   YYYY-MM-DD; pretend "today" is this date. Test hook for
#                       simulating a multi-month run without waiting.

set -euo pipefail

CONTAINER="${BYLAW_PG_CONTAINER:-agentic-bylaw-system-postgres-1}"
DB="${BYLAW_PG_DB:-layer1}"
USER_="${BYLAW_PG_USER:-layer1}"
BACKUP_DIR="${BYLAW_BACKUP_DIR:-$HOME/backups/agentic-bylaw-system}"
KEEP_WEEKLY="${BYLAW_KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${BYLAW_KEEP_MONTHLY:-6}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    *) echo "usage: $(basename "$0") [--dry-run]" >&2; exit 2 ;;
  esac
done

# Format "today" — or BYLAW_BACKUP_DATE, if the caller injected a clock.
# BSD date (macOS) needs -j -f to parse; GNU date needs -d. Try both.
fmt_date() {
  local fmt="$1"
  if [ -z "${BYLAW_BACKUP_DATE:-}" ]; then
    date "+$fmt"
  elif date -j -f "%Y-%m-%d" "$BYLAW_BACKUP_DATE" "+$fmt" 2>/dev/null; then
    :
  else
    date -d "$BYLAW_BACKUP_DATE" "+$fmt"
  fi
}

DOW="$(fmt_date %a)"                    # Mon, Tue, ... Sun
WEEK="$(fmt_date %G-W%V)"               # ISO week, e.g. 2026-W33
MONTH="$(fmt_date %Y-%m)"               # 2026-08
TS="$(fmt_date '%Y-%m-%dT%H:%M:%S%z')"

OUT="$BACKUP_DIR/layer1-$DOW.dump"
TMP="$OUT.tmp"
WEEKLY="$BACKUP_DIR/layer1-weekly-$WEEK.dump"
MONTHLY="$BACKUP_DIR/layer1-monthly-$MONTH.dump"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[%s] DRY-RUN %s\n' "$TS" "$*" >&2
  else
    printf '[%s] %s\n' "$TS" "$*" | tee -a "$LOG" >&2
  fi
}

# Promote the day's dump into a longer-horizon tier the first time we run in
# that week / month. Copying (rather than hardlinking) keeps the disk ceiling
# honest: every slot in the table below is a real, independent file.
promote() {
  local target="$1" tier="$2"
  if [ -e "$target" ]; then
    log "SKIP: $tier slot $(basename "$target") already exists"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    log "would promote $(basename "$OUT") -> $(basename "$target") ($tier)"
    return 0
  fi
  # -p preserves mtime so `ls -t` (used by clone-dev-db.sh) still ranks dumps
  # by when the data was captured, not when the copy was made.
  cp -p "$OUT" "$target.tmp"
  mv -f "$target.tmp" "$target"
  log "OK: promoted $(basename "$OUT") -> $(basename "$target") ($tier)"
}

# Delete the oldest files in a tier until only $keep remain. Tier names sort
# lexicographically in chronological order (2026-W09 < 2026-W10, 2026-08 <
# 2026-09), so a plain sort is a chronological sort.
#
# $3 is an optional name that the caller is about to create; counting it here
# lets --dry-run report the prune that the real run would perform.
prune_tier() {
  local glob="$1" keep="$2" pending="${3:-}"
  local listing count excess
  listing="$(ls -1 "$BACKUP_DIR"/$glob 2>/dev/null | sort || true)"
  if [ -n "$pending" ] && [ ! -e "$pending" ]; then
    listing="$(printf '%s\n%s\n' "$listing" "$pending" | grep -v '^$' | sort || true)"
  fi
  count="$(printf '%s\n' "$listing" | grep -c . || true)"
  excess=$((count - keep))
  [ "$excess" -gt 0 ] || return 0
  printf '%s\n' "$listing" | head -n "$excess" | while IFS= read -r victim; do
    [ -n "$victim" ] || continue
    if [ "$DRY_RUN" -eq 1 ]; then
      log "would prune $(basename "$victim") (over $keep-slot limit)"
    else
      rm -f "$victim"
      log "PRUNED: $(basename "$victim") (over $keep-slot limit)"
    fi
  done
}

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

if [ "$DRY_RUN" -eq 1 ]; then
  log "plan for $TS (week $WEEK, month $MONTH)"
  log "would write $(basename "$OUT") (daily slot)"
  promote "$WEEKLY" weekly
  promote "$MONTHLY" monthly
  prune_tier 'layer1-weekly-*.dump' "$KEEP_WEEKLY" "$WEEKLY"
  prune_tier 'layer1-monthly-*.dump' "$KEEP_MONTHLY" "$MONTHLY"
  log "no files were changed"
  exit 0
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

# Promote before pruning, so a tier that is exactly at its limit drops its
# oldest slot in the same run that adds the newest one.
promote "$WEEKLY" weekly
promote "$MONTHLY" monthly
prune_tier 'layer1-weekly-*.dump' "$KEEP_WEEKLY"
prune_tier 'layer1-monthly-*.dump' "$KEEP_MONTHLY"
