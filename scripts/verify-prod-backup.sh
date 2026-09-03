#!/usr/bin/env bash
# Prove that a production backup artifact can actually be restored.
#
# scripts/backup-prod-db.sh already reads each fresh dump's table of contents
# before accepting it. That catches truncation, but it does not prove the data
# comes back. This script closes that gap by replaying the recovery procedure
# an operator would run during an outage — decrypt, restore, query — against a
# throwaway Postgres, so the answer is known before it's needed.
#
# Two depths:
#
#   scripts/verify-prod-backup.sh             # fast: archive TOC + required tables
#   scripts/verify-prod-backup.sh --restore   # full: restore into a scratch
#                                             #       Postgres and count rows
#
# Nothing here touches the production database or the production container.
# The scratch instance is a fresh container off the same image, on no network,
# destroyed on exit (including on Ctrl-C).
#
# Usage:
#   scripts/verify-prod-backup.sh [--restore] [--file PATH] [--quiet]
#
# --file defaults to the most recently modified layer1-prod-* artifact in
# BYLAW_PROD_BACKUP_DIR, which is what "is last night's backup good?" means.
#
# Configuration:
#   BYLAW_PROD_BACKUP_DIR         where artifacts live (default: /srv/bylaw/backups)
#   BYLAW_PG_IMAGE                image to restore into (default: bylaw-postgres:latest)
#   BYLAW_BACKUP_PASSPHRASE_FILE  required to verify a .gpg artifact
#   BYLAW_VERIFY_TIMEOUT          seconds to wait for scratch Postgres (default: 60)

set -euo pipefail

BACKUP_DIR="${BYLAW_PROD_BACKUP_DIR:-/srv/bylaw/backups}"
PG_IMAGE="${BYLAW_PG_IMAGE:-bylaw-postgres:latest}"
PASSPHRASE_FILE="${BYLAW_BACKUP_PASSPHRASE_FILE:-}"
TIMEOUT="${BYLAW_VERIFY_TIMEOUT:-60}"

# Same set the backup script gates on. Kept in one place per script rather
# than a shared file so each can be copied to the server standalone.
REQUIRED_TABLES="advisor_user advisor_case advisor_usage_event alembic_version"

DO_RESTORE=0
FILE=""
QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --restore) DO_RESTORE=1 ;;
    --quiet|-q) QUIET=1 ;;
    --file) shift; FILE="${1:-}" ;;
    *) echo "usage: $(basename "$0") [--restore] [--file PATH] [--quiet]" >&2; exit 2 ;;
  esac
  shift
done

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*" >&2; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

WORK=""
SCRATCH=""
cleanup() {
  [ -z "$WORK" ] || rm -rf "$WORK"
  # Tear the scratch instance down even on Ctrl-C. A leaked container would
  # sit on the production host holding a copy of the user table in memory.
  if [ -n "$SCRATCH" ]; then
    docker rm -f "$SCRATCH" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [ -z "$FILE" ]; then
  FILE="$(ls -1t "$BACKUP_DIR"/layer1-prod-*.dump "$BACKUP_DIR"/layer1-prod-*.dump.gpg \
          2>/dev/null | head -n 1 || true)"
  [ -n "$FILE" ] || fail "no layer1-prod-* artifact found in $BACKUP_DIR"
fi
[ -s "$FILE" ] || fail "$FILE is missing or empty"

command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"

say "Verifying $(basename "$FILE") ($(wc -c < "$FILE" | tr -d ' ') bytes)"

# --- decrypt -------------------------------------------------------------

WORK="$(mktemp -d)"
chmod 700 "$WORK"
PLAIN="$WORK/layer1-prod.dump"

case "$FILE" in
  *.gpg)
    [ -n "$PASSPHRASE_FILE" ] || fail "$(basename "$FILE") is encrypted but BYLAW_BACKUP_PASSPHRASE_FILE is unset"
    [ -r "$PASSPHRASE_FILE" ] || fail "passphrase file '$PASSPHRASE_FILE' is missing or unreadable"
    say "Decrypting with gpg"
    gpg --batch --yes --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" \
        --output "$PLAIN" "$FILE" \
      || fail "gpg could not decrypt $(basename "$FILE") — the artifact or the passphrase is wrong"
    ;;
  *)
    cp "$FILE" "$PLAIN"
    ;;
esac

# --- level 1: archive table of contents ----------------------------------

say "Reading archive table of contents"
TOC="$(docker run --rm -v "$WORK:/verify:ro" "$PG_IMAGE" \
       pg_restore --list /verify/layer1-prod.dump 2>&1)" \
  || fail "pg_restore could not read the archive: $(printf '%s' "$TOC" | head -n 3 | tr '\n' ' ')"

missing=""
for table in $REQUIRED_TABLES; do
  printf '%s\n' "$TOC" | grep -qE "TABLE (DATA )?public $table " || missing="$missing $table"
done
[ -z "$missing" ] || fail "archive is missing required table(s):$missing"
say "OK: archive is readable and lists all required tables"

if [ "$DO_RESTORE" -eq 0 ]; then
  printf 'PASS: %s (archive check)\n' "$(basename "$FILE")"
  exit 0
fi

# --- level 2: restore into a scratch Postgres ----------------------------

SCRATCH="bylaw-backup-verify-$$"
say "Starting scratch Postgres ($SCRATCH) from $PG_IMAGE"

# --network none: the scratch instance must not be reachable, and must not be
# able to reach the production database. It exists to answer one question.
docker run --rm -d --name "$SCRATCH" --network none \
  -e POSTGRES_PASSWORD=verify -e POSTGRES_USER=verify -e POSTGRES_DB=verify \
  -v "$WORK:/verify:ro" "$PG_IMAGE" >/dev/null \
  || fail "could not start scratch Postgres"

waited=0
until docker exec "$SCRATCH" pg_isready -U verify -d verify >/dev/null 2>&1; do
  waited=$((waited + 1))
  [ "$waited" -lt "$TIMEOUT" ] || fail "scratch Postgres did not become ready in ${TIMEOUT}s"
  sleep 1
done
say "Scratch Postgres ready after ${waited}s"

# --no-owner / --no-acl: the dump's roles don't exist here, and role ownership
# is not what we're testing. --exit-on-error turns a partial restore into a
# failure instead of a pile of warnings nobody reads.
say "Restoring into scratch Postgres"
if ! restore_out="$(docker exec "$SCRATCH" pg_restore --no-owner --no-acl \
                    --exit-on-error -U verify -d verify \
                    /verify/layer1-prod.dump 2>&1)"; then
  fail "pg_restore failed: $(printf '%s' "$restore_out" | tail -n 5 | tr '\n' ' ')"
fi
say "OK: restore completed without errors"

# --- level 2b: does the restored data answer questions? ------------------

summary=""
for table in $REQUIRED_TABLES; do
  if ! count="$(docker exec "$SCRATCH" psql -U verify -d verify -tAc \
                "SELECT count(*) FROM $table" 2>&1)"; then
    fail "restored database has no queryable '$table': $(printf '%s' "$count" | tr '\n' ' ')"
  fi
  count="$(printf '%s' "$count" | tr -d '[:space:]')"
  summary="$summary $table=$count"
  say "  $table: $count rows"
done

# alembic_version is the schema-state pointer. A restore that comes back with
# zero rows there restored a shell, not a database — every subsequent
# `alembic upgrade head` would replay from scratch over live data.
alembic_rows="$(docker exec "$SCRATCH" psql -U verify -d verify -tAc \
                'SELECT count(*) FROM alembic_version' 2>/dev/null | tr -d '[:space:]')"
[ "${alembic_rows:-0}" -ge 1 ] || fail "restored alembic_version is empty — schema pointer did not survive"

printf 'PASS: %s (full restore)%s\n' "$(basename "$FILE")" "$summary"
