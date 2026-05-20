#!/usr/bin/env bash
# Install (or remove) a crontab entry that runs scripts/backup-dev-db.sh
# nightly at 03:00 local time.
#
# Usage:
#   scripts/install-backup-cron.sh            # install / update
#   scripts/install-backup-cron.sh --uninstall
#
# Re-running install replaces any prior entry pointing at this script,
# so it is safe to run repeatedly.
#
# macOS caveat: cron does NOT run while the Mac is asleep. If the laptop
# is closed at 03:00 the backup that night is skipped — there is no
# catch-up. Run the script manually after long sleeps, or switch to a
# launchd agent with `RunAtLoad` if you need wake-on-fire semantics.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/backup-dev-db.sh"
TAG="# agentic-bylaw-system:backup-dev-db"

if [ ! -x "$SCRIPT" ]; then
  echo "ERROR: $SCRIPT is not executable. Run: chmod +x $SCRIPT" >&2
  exit 1
fi

# Snapshot current crontab (may be empty), strip any prior entry of ours.
CURRENT="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "$CURRENT" | grep -vF "$TAG" || true)"

if [ "${1:-}" = "--uninstall" ]; then
  if [ -z "$FILTERED" ]; then
    crontab -r 2>/dev/null || true
  else
    printf '%s\n' "$FILTERED" | crontab -
  fi
  echo "Uninstalled backup cron entry."
  exit 0
fi

# 03:00 every day. PATH override so docker is reachable from cron's minimal env.
NEW_LINE="0 3 * * * PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin $SCRIPT $TAG"

{
  if [ -n "$FILTERED" ]; then
    printf '%s\n' "$FILTERED"
  fi
  printf '%s\n' "$NEW_LINE"
} | crontab -

echo "Installed crontab entry:"
echo "  $NEW_LINE"
echo
echo "Verify with: crontab -l"
echo "Logs: \$HOME/backups/agentic-bylaw-system/backup.log"
