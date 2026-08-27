#!/usr/bin/env bash
# Install (or remove) the production backup cron entries on the deploy host.
#
# Two jobs, because they answer two different questions:
#
#   02:30 daily   scripts/backup-prod-db.sh        do we have a backup?
#   04:00 Sunday  scripts/verify-prod-backup.sh --restore   does it restore?
#
# The restore test runs 90 minutes after the Sunday dump, against whatever the
# newest artifact is — so a week where every dump was silently corrupt is
# caught within seven days rather than during an outage.
#
# Both jobs source BYLAW_BACKUP_ENV_FILE first. cron does not read the login
# shell's profile, so the Storage Box target, the passphrase file path and the
# retention overrides have to come from a file the job reads itself.
#
# Usage (run as the `deploy` user on the production host):
#   scripts/install-prod-backup-cron.sh
#   scripts/install-prod-backup-cron.sh --show
#   scripts/install-prod-backup-cron.sh --uninstall
#
# Re-running install replaces any prior entries of ours, so it is idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_SCRIPT="${BYLAW_BACKUP_SCRIPT:-$REPO_ROOT/scripts/backup-prod-db.sh}"
VERIFY_SCRIPT="${BYLAW_VERIFY_SCRIPT:-$REPO_ROOT/scripts/verify-prod-backup.sh}"
ENV_FILE="${BYLAW_BACKUP_ENV_FILE:-/srv/bylaw/backup.env}"
CRON_LOG="${BYLAW_BACKUP_CRON_LOG:-/srv/bylaw/backups/cron.log}"

TAG_BACKUP="# agentic-bylaw-system:backup-prod-db"
TAG_VERIFY="# agentic-bylaw-system:verify-prod-backup"

CURRENT="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "$CURRENT" | grep -vF "$TAG_BACKUP" | grep -vF "$TAG_VERIFY" || true)"

if [ "${1:-}" = "--show" ]; then
  printf '%s\n' "$CURRENT" | grep -F "agentic-bylaw-system:" || echo "(no backup cron entries installed)"
  exit 0
fi

if [ "${1:-}" = "--uninstall" ]; then
  if [ -z "$FILTERED" ]; then
    crontab -r 2>/dev/null || true
  else
    printf '%s\n' "$FILTERED" | crontab -
  fi
  echo "Uninstalled production backup cron entries."
  exit 0
fi

for script in "$BACKUP_SCRIPT" "$VERIFY_SCRIPT"; do
  if [ ! -x "$script" ]; then
    echo "ERROR: $script is not executable. Run: chmod +x $script" >&2
    exit 1
  fi
done

if [ ! -r "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE is missing or unreadable." >&2
  echo "       Create it from docs/PROD_DB_BACKUP.md before installing cron —" >&2
  echo "       without it the jobs run with no Storage Box target and no" >&2
  echo "       passphrase, and the backup script will refuse to ship offsite." >&2
  exit 1
fi

# `set -a` exports everything the env file assigns, so the scripts see it.
# PATH is set explicitly: cron's default is /usr/bin:/bin and would not find
# docker on every install.
PREAMBLE="PATH=/usr/local/bin:/usr/bin:/bin; set -a; . $ENV_FILE; set +a;"

BACKUP_LINE="30 2 * * * $PREAMBLE $BACKUP_SCRIPT >> $CRON_LOG 2>&1 $TAG_BACKUP"
VERIFY_LINE="0 4 * * 0 $PREAMBLE $VERIFY_SCRIPT --restore >> $CRON_LOG 2>&1 $TAG_VERIFY"

{
  if [ -n "$FILTERED" ]; then
    printf '%s\n' "$FILTERED"
  fi
  printf '%s\n' "$BACKUP_LINE"
  printf '%s\n' "$VERIFY_LINE"
} | crontab -

mkdir -p "$(dirname "$CRON_LOG")"

echo "Installed crontab entries:"
echo "  $BACKUP_LINE"
echo "  $VERIFY_LINE"
echo
echo "Verify with: crontab -l"
echo "Logs: $CRON_LOG and $(dirname "$CRON_LOG")/backup.log"
