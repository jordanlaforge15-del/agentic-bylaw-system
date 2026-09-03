#!/usr/bin/env bash
# Back up the PRODUCTION Postgres (`layer1` in the `bylaw-postgres` container
# on the Hetzner CX22) and mirror the result to a Hetzner Storage Box.
#
# This is the offsite sibling of scripts/backup-dev-db.sh. That script protects
# a laptop's working copy; this one protects the only copy of user, billing and
# audit data that exists (docs/DEPLOYMENT.md, "Data safety"). The differences
# that follow from that are deliberate:
#
#   * Every dump is verified before it is allowed to become "the backup". An
#     unverified dump is worse than no dump, because it reads as protection.
#   * Artifacts are encrypted at rest by default. They leave the server, and
#     they carry the contents of advisor_user / advisor_case_purchase.
#   * The offsite copy is a *mirror* of the pruned local set, so retention is
#     enforced in exactly one place and the two sides cannot drift.
#
# Retention (ABS-131): 7 daily slots + 4 weekly slots.
#
#   daily   layer1-prod-<Dow>.dump[.gpg]          7 slots, keyed by day-of-week
#   weekly  layer1-prod-weekly-<ISO week>.dump[.gpg]  first run of each ISO week
#
# The daily tier rotates in place (day 8 overwrites day 1). The weekly tier is
# a promotion — a copy of the day's dump taken the first time the script runs
# in a given ISO week — pruned explicitly by count, oldest first. Disk is
# bounded at 7 + BYLAW_KEEP_WEEKLY artifacts (11 by default), on both sides.
#
# Files that don't match a tier's naming pattern (e.g. a hand-taken
# `layer1-prod-pre-migration-20260812.dump`) are never touched by the prune.
#
# Usage:
#   scripts/backup-prod-db.sh                # dump, verify, encrypt, mirror
#   scripts/backup-prod-db.sh --dry-run      # print the plan, change nothing
#   scripts/backup-prod-db.sh --no-offsite   # local only (e.g. Storage Box down)
#   scripts/install-prod-backup-cron.sh      # install the cron entries
#
# Deep restore-into-a-real-Postgres verification is a separate, slower job:
#   scripts/verify-prod-backup.sh --restore
#
# Configuration (normally sourced from /srv/bylaw/backup.env — see
# docs/PROD_DB_BACKUP.md):
#   BYLAW_PROD_PG_CONTAINER   container name  (default: bylaw-postgres)
#   BYLAW_PROD_PG_DB          database        (default: layer1)
#   BYLAW_PROD_PG_USER        user            (default: layer1)
#   BYLAW_PROD_BACKUP_DIR     staging dir     (default: /srv/bylaw/backups)
#   BYLAW_PG_IMAGE            image used for verification (default: bylaw-postgres:latest)
#   BYLAW_KEEP_WEEKLY         weekly slots    (default: 4)
#   BYLAW_BACKUP_PASSPHRASE_FILE  gpg symmetric passphrase; enables encryption
#   BYLAW_BACKUP_ALLOW_PLAINTEXT  set to 1 to ship unencrypted (refused otherwise)
#   BYLAW_STORAGE_BOX_TARGET  rsync target, e.g. u1234@u1234.your-storagebox.de:backups/prod
#   BYLAW_STORAGE_BOX_PORT    ssh port        (default: 23 — Hetzner Storage Box)
#   BYLAW_STORAGE_BOX_SSH_KEY identity file   (default: $HOME/.ssh/storagebox)
#   BYLAW_BACKUP_DATE         YYYY-MM-DD; pretend "today" is this date. Test hook.

set -euo pipefail

CONTAINER="${BYLAW_PROD_PG_CONTAINER:-bylaw-postgres}"
DB="${BYLAW_PROD_PG_DB:-layer1}"
PGUSER_="${BYLAW_PROD_PG_USER:-layer1}"
BACKUP_DIR="${BYLAW_PROD_BACKUP_DIR:-/srv/bylaw/backups}"
PG_IMAGE="${BYLAW_PG_IMAGE:-bylaw-postgres:latest}"
KEEP_WEEKLY="${BYLAW_KEEP_WEEKLY:-4}"
PASSPHRASE_FILE="${BYLAW_BACKUP_PASSPHRASE_FILE:-}"
ALLOW_PLAINTEXT="${BYLAW_BACKUP_ALLOW_PLAINTEXT:-0}"
STORAGE_TARGET="${BYLAW_STORAGE_BOX_TARGET:-}"
STORAGE_PORT="${BYLAW_STORAGE_BOX_PORT:-23}"
STORAGE_KEY="${BYLAW_STORAGE_BOX_SSH_KEY:-$HOME/.ssh/storagebox}"

DRY_RUN=0
OFFSITE=1
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    --no-offsite) OFFSITE=0 ;;
    *) echo "usage: $(basename "$0") [--dry-run] [--no-offsite]" >&2; exit 2 ;;
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

DOW="$(fmt_date %a)"        # Mon, Tue, ... Sun
WEEK="$(fmt_date %G-W%V)"   # ISO week, e.g. 2026-W33
TS="$(fmt_date '%Y-%m-%dT%H:%M:%S%z')"

# Encryption is on whenever a passphrase file is configured. The suffix is part
# of the artifact name so a half-migrated directory is never ambiguous about
# what a given file actually contains.
if [ -n "$PASSPHRASE_FILE" ]; then
  SUFFIX=".dump.gpg"
  OTHER_SUFFIX=".dump"
else
  SUFFIX=".dump"
  OTHER_SUFFIX=".dump.gpg"
fi

OUT="$BACKUP_DIR/layer1-prod-$DOW$SUFFIX"
STAGE="$BACKUP_DIR/layer1-prod-$DOW.staging"
WEEKLY="$BACKUP_DIR/layer1-prod-weekly-$WEEK$SUFFIX"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"

log() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[%s] DRY-RUN %s\n' "$TS" "$*" >&2
  else
    printf '[%s] %s\n' "$TS" "$*" | tee -a "$LOG" >&2
  fi
}

cleanup_stage() { rm -f "$STAGE" "$STAGE.gpg"; }
trap cleanup_stage EXIT

# --- retention helpers ---------------------------------------------------

# Promote the day's artifact into the weekly tier the first time we run in that
# ISO week. Copying (rather than hardlinking) keeps the disk ceiling honest:
# every slot is a real, independent file, and rsync mirrors it as one.
promote() {
  local target="$1"
  if [ -e "$target" ]; then
    log "SKIP: weekly slot $(basename "$target") already exists"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    log "would promote $(basename "$OUT") -> $(basename "$target") (weekly)"
    return 0
  fi
  cp -p "$OUT" "$target.tmp"
  mv -f "$target.tmp" "$target"
  log "OK: promoted $(basename "$OUT") -> $(basename "$target") (weekly)"
}

# Delete the oldest weekly slots until only $keep remain. ISO week names sort
# lexicographically in chronological order (2026-W09 < 2026-W10), so a plain
# sort is a chronological sort.
#
# $3 is an optional name the caller is about to create; counting it here lets
# --dry-run report the prune that a real run would perform.
prune_weekly() {
  local keep="$1" pending="${2:-}"
  local listing count excess
  listing="$(ls -1 "$BACKUP_DIR"/layer1-prod-weekly-* 2>/dev/null | sort || true)"
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

# --- preflight -----------------------------------------------------------

# Locate docker — cron's PATH is minimal and won't include every install's shim.
if ! command -v docker >/dev/null 2>&1; then
  for candidate in /usr/bin/docker /usr/local/bin/docker /opt/homebrew/bin/docker; do
    if [ -x "$candidate" ]; then
      PATH="$(dirname "$candidate"):$PATH"
      export PATH
      break
    fi
  done
fi

# Refusing to ship plaintext offsite is a hard gate, not a warning. Check it
# BEFORE spending minutes on a dump that we would then decline to upload.
offsite_allowed() {
  [ "$OFFSITE" -eq 1 ] || return 1
  [ -n "$STORAGE_TARGET" ] || return 1
  return 0
}

if offsite_allowed && [ -z "$PASSPHRASE_FILE" ] && [ "$ALLOW_PLAINTEXT" != "1" ]; then
  log "ERROR: refusing to send an unencrypted dump offsite. Set"
  log "       BYLAW_BACKUP_PASSPHRASE_FILE, or BYLAW_BACKUP_ALLOW_PLAINTEXT=1"
  log "       to override. See docs/PROD_DB_BACKUP.md."
  exit 1
fi

if [ -n "$PASSPHRASE_FILE" ] && [ ! -r "$PASSPHRASE_FILE" ]; then
  log "ERROR: passphrase file '$PASSPHRASE_FILE' is missing or unreadable"
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "plan for $TS (week $WEEK)"
  log "would write $(basename "$OUT") (daily slot)"
  if [ -n "$PASSPHRASE_FILE" ]; then
    log "would encrypt with gpg --symmetric (passphrase file $PASSPHRASE_FILE)"
  else
    log "would write UNENCRYPTED (no BYLAW_BACKUP_PASSPHRASE_FILE configured)"
  fi
  promote "$WEEKLY"
  prune_weekly "$KEEP_WEEKLY" "$WEEKLY"
  if offsite_allowed; then
    log "would mirror $BACKUP_DIR -> $STORAGE_TARGET (rsync --delete, port $STORAGE_PORT)"
  else
    log "would skip offsite mirror (no target configured or --no-offsite)"
  fi
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

# --- dump ----------------------------------------------------------------

log "Starting pg_dump of $DB from $CONTAINER"

# -Fc = custom format: compressed, and restorable table-by-table with
# pg_restore. The dump lands in a staging file; nothing is promoted to "the
# backup" until it has passed verification below, so a failed run always
# leaves yesterday's known-good artifact in place.
# `rc=0; cmd || rc=$?` rather than `if ! cmd; then rc=$?`: inside `if !` the
# `!` has already rewritten $? to 0, so the second form always reports success.
rc=0
docker exec -i "$CONTAINER" pg_dump -U "$PGUSER_" -d "$DB" -Fc > "$STAGE" || rc=$?
if [ "$rc" -ne 0 ]; then
  log "ERROR: pg_dump failed (exit $rc); kept previous $(basename "$OUT") untouched"
  exit "$rc"
fi

RAW_SIZE="$(wc -c < "$STAGE" | tr -d ' ')"
log "OK: dumped $RAW_SIZE bytes to staging"

# --- verify --------------------------------------------------------------

# Read the archive's table of contents back. This is the cheap half of
# verification and it catches the failure that matters most in practice: a
# truncated or zero-length dump that a naive `if pg_dump; then` would have
# happily accepted (pg_dump's exit status says nothing about the bytes that
# actually reached the far end of a pipe).
#
# Runs in a throwaway container off the production Postgres image, with the
# backup directory mounted read-only — the running database is never touched.
log "Verifying archive integrity (pg_restore --list)"
if ! TOC="$(docker run --rm -v "$BACKUP_DIR:/backups:ro" "$PG_IMAGE" \
            pg_restore --list "/backups/$(basename "$STAGE")" 2>&1)"; then
  log "ERROR: archive verification failed; rejecting this dump"
  log "       pg_restore said: $(printf '%s' "$TOC" | head -n 3 | tr '\n' ' ')"
  log "       kept previous $(basename "$OUT") untouched"
  exit 1
fi

# A syntactically valid but *empty* archive also fails verification. The
# tables below are the system of record (docs/DEPLOYMENT.md, "Data safety") —
# if the dump doesn't carry them, it is not a backup of this system.
REQUIRED_TABLES="advisor_user advisor_case advisor_usage_event alembic_version"
missing=""
for table in $REQUIRED_TABLES; do
  printf '%s\n' "$TOC" | grep -qE "TABLE (DATA )?public $table " || missing="$missing $table"
done
if [ -n "$missing" ]; then
  log "ERROR: archive is missing required table(s):$missing; rejecting this dump"
  log "       kept previous $(basename "$OUT") untouched"
  exit 1
fi
log "OK: archive lists all required tables"

# --- encrypt -------------------------------------------------------------

if [ -n "$PASSPHRASE_FILE" ]; then
  log "Encrypting with gpg --symmetric --cipher-algo AES256"
  rc=0
  gpg --batch --yes --quiet --symmetric --cipher-algo AES256 \
      --passphrase-file "$PASSPHRASE_FILE" \
      --output "$STAGE.gpg" "$STAGE" || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "ERROR: gpg encryption failed (exit $rc); rejecting this dump"
    exit "$rc"
  fi
  mv -f "$STAGE.gpg" "$OUT"
else
  mv -f "$STAGE" "$OUT"
fi

# Backups are readable by their owner only — the artifact contains every user
# row, every purchase and the full audit trail.
chmod 600 "$OUT"
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
log "OK: wrote $(basename "$OUT") ($SIZE bytes)"

# Toggling encryption on or off changes the suffix, which would otherwise let
# a slot hold two artifacts — doubling the rotation, and (worse, on the way in)
# leaving a plaintext copy of the user table on disk indefinitely.
retire_counterpart() {
  local stale="$1$OTHER_SUFFIX"
  [ -e "$stale" ] || return 0
  rm -f "$stale"
  log "REMOVED: stale $(basename "$stale") (encryption mode changed)"
}

retire_counterpart "$BACKUP_DIR/layer1-prod-$DOW"

# Promote before pruning, so a tier that is exactly at its limit drops its
# oldest slot in the same run that adds the newest one.
promote "$WEEKLY"
retire_counterpart "$BACKUP_DIR/layer1-prod-weekly-$WEEK"
prune_weekly "$KEEP_WEEKLY"

# --- offsite mirror ------------------------------------------------------

if ! offsite_allowed; then
  if [ "$OFFSITE" -eq 0 ]; then
    log "SKIP: offsite mirror disabled by --no-offsite"
  else
    log "WARN: no BYLAW_STORAGE_BOX_TARGET configured; backup is LOCAL ONLY"
  fi
  exit 0
fi

# `rsync --delete` makes the Storage Box an exact mirror of the pruned local
# set, which is the whole reason retention is computed in one place. The flip
# side is that a broken local directory would delete the offsite copy too, so
# refuse to run the mirror unless today's artifact is actually there.
if [ ! -s "$OUT" ]; then
  log "ERROR: $(basename "$OUT") is missing or empty; refusing to mirror with --delete"
  exit 1
fi

log "Mirroring $BACKUP_DIR -> $STORAGE_TARGET"
if rsync --archive --delete --exclude='*.staging' --exclude='*.tmp' \
        -e "ssh -p $STORAGE_PORT -i $STORAGE_KEY -o StrictHostKeyChecking=accept-new" \
        "$BACKUP_DIR/" "$STORAGE_TARGET/"; then
  log "OK: offsite mirror complete"
else
  rc=$?
  log "ERROR: offsite mirror failed (exit $rc); local backup is intact but NOT offsite"
  exit "$rc"
fi
