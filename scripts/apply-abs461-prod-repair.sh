#!/usr/bin/env bash
# Apply ABS-461's page-break repair to the production corpus (ABS-465).
#
# The assessment in docs/data-gaps/abs461-production-impact.md establishes that
# production carries the identical defect dev did, and that a data repair — not
# a re-ingest — is the right instrument. This script is that procedure, with the
# parts an operator can get wrong taken out of their hands:
#
#   * the SSH tunnel is opened and torn down by a trap, including on Ctrl-C, so
#     a forwarded port to the production database cannot be left behind;
#   * the database password is read from the container's own environment, never
#     typed or stored (the runbook used to name a credential that is wrong);
#   * the dry run is mandatory and its output is *gated*, not eyeballed. The
#     issue says "if it prints anything else, stop"; a human comparing four
#     fragment-id pairs against a document at 1am is how that gets skipped;
#   * writing requires --apply. The default run changes nothing.
#
# Usage:
#   scripts/apply-abs461-prod-repair.sh                  # dry run + drift gate
#   scripts/apply-abs461-prod-repair.sh --apply          # repair, then verify
#   scripts/apply-abs461-prod-repair.sh --gate FILE      # gate a saved transcript
#   PG_PASSWORD=... ... --redact < FILE                  # the redaction filter alone
#
# Configuration:
#   ABS461_SSH_HOST       ssh target for the production host (default: bylaw-prod)
#   ABS461_PG_CONTAINER   Postgres container name (default: bylaw-postgres)
#   ABS461_PG_ADDRESS     container bridge address (default: 172.18.0.2:5432)
#   ABS461_LOCAL_PORT     local end of the tunnel (default: 15442)
#   ABS461_SIDECAR_DIR    durable home for the revert sidecar
#                         (default: $HOME/abs461-prod-sidecars)
#
# No container is touched, so this does not need the 23:00 AST maintenance
# window. It should still land alongside the advisor image that carries
# ABS-461's lookup_citation change — see the issue's "ship together" note.

set -euo pipefail

SSH_HOST="${ABS461_SSH_HOST:-bylaw-prod}"
PG_CONTAINER="${ABS461_PG_CONTAINER:-bylaw-postgres}"
PG_ADDRESS="${ABS461_PG_ADDRESS:-172.18.0.2:5432}"
LOCAL_PORT="${ABS461_LOCAL_PORT:-15442}"
SIDECAR_DIR="${ABS461_SIDECAR_DIR:-$HOME/abs461-prod-sidecars}"
DOCUMENT_ID=4

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"

# --- the drift gate ------------------------------------------------------
#
# What the assessment measured on 2026-08-11 and this script re-measured on
# 2026-08-27. Prod must still look exactly like this, because that is the
# state the repair was reasoned about against. Anything else means the corpus
# moved under us and the change needs re-assessing, not forcing.
EXPECTED_SPLITS="5791+5792 6070+6071 6393+6394 7121+7122"
EXPECTED_SUMMARY="4 joined, 2 phantom section(s) removed, 10 citation_path(s) rewritten, 0 unresolved"

say() { printf '%s\n' "$*" >&2; }
fail() { printf 'STOP: %s\n' "$*" >&2; exit 1; }

# --- credential handling -------------------------------------------------
#
# Two rules, both learned the hard way, both exercised by
# tests/test_abs461_prod_repair_gate.py:
#
#   1. The password never reaches a command line. /proc/<pid>/cmdline is
#      world-readable, so `--database-url postgresql://user:secret@...` shows
#      the production credential to every other account on this machine for
#      the length of the run. It goes through the environment instead, which
#      is readable only by its owner.
#
#   2. The password is never fed to a *pattern*. Redaction used to be
#      `sed "s/${PG_PASSWORD}/***/g"`, which a generated Postgres password
#      breaks in several ways: a '/' terminates sed's own s/// delimiter, a
#      '.' matches any character, an '&' expands to the whole match. The
#      first of those is a hard error, and under `set -euo pipefail` a hard
#      error in the redaction pipe took the run down with it — discarding
#      the repair's entire output, including the revert sidecar path, after
#      the write to production had already committed. str.replace() has no
#      metacharacters and nothing to escape.

encode_password() {
  PW="$PG_PASSWORD" python3 -c \
    'import os, urllib.parse; print(urllib.parse.quote(os.environ["PW"], safe=""))'
}

# Filter stdin to stdout, blanking the password in both its raw and its
# URL-encoded form — a failed connection prints the DSN, which carries the
# encoded one.
redact() {
  PW="${PG_PASSWORD:-}" PW_ENC="${PG_PASSWORD_ENC:-}" python3 -c '
import os, sys

secrets = sorted(
    {s for s in (os.environ.get("PW", ""), os.environ.get("PW_ENC", "")) if s},
    key=len,
    reverse=True,
)
for line in sys.stdin:
    for secret in secrets:
        line = line.replace(secret, "***")
    sys.stdout.write(line)
'
}

# Run the repair with the DSN in the environment rather than in argv.
run_repair() {
  (
    export DATABASE_URL="$REPAIR_DSN"
    # layer1.config lets PG_PORT override DATABASE_URL's port (ABS-501, for
    # stale e2e exports). An operator whose shell still carries a worktree's
    # port triplet would otherwise have this run silently redirected off the
    # tunnel and onto a local test database.
    unset PG_PORT
    exec "$PYTHON" "$REPO_ROOT/scripts/repair_page_break_splits.py" "$@"
  )
}

# Read a dry-run transcript and decide whether production still matches the
# assessment. Split out as its own mode so it is testable without a production
# database in the loop — see tests/test_abs461_prod_repair_gate.py.
gate_transcript() {
  local file="$1"
  [ -s "$file" ] || fail "dry-run transcript '$file' is missing or empty"

  local observed
  observed="$(sed -n 's/.*would join fragment \([0-9]*\) + \([0-9]*\).*/\1+\2/p' "$file" \
              | sort | tr '\n' ' ' | sed 's/ $//')"
  local expected
  expected="$(printf '%s\n' $EXPECTED_SPLITS | sort | tr '\n' ' ' | sed 's/ $//')"

  if [ "$observed" != "$expected" ]; then
    say "expected splits: $expected"
    say "observed splits: ${observed:-<none>}"
    fail "production no longer matches the assessed state — re-assess, do not apply"
  fi

  if ! grep -qF "$EXPECTED_SUMMARY" "$file"; then
    say "expected summary: $EXPECTED_SUMMARY"
    say "observed summary: $(grep -F 'page-break splits:' "$file" || echo '<none>')"
    fail "the change is not the size the assessment measured — re-assess, do not apply"
  fi

  # An unresolved phantom means the script joined the text but could not find a
  # real section to reparent the orphaned clauses onto. It leaves those paths
  # alone and says so. That is a correct refusal, and a reason to stop.
  if grep -qE "LEFT AS-IS|SKIPPED" "$file"; then
    say "$(grep -E "LEFT AS-IS|SKIPPED" "$file")"
    fail "the repair could not place every clause — resolve by hand before applying"
  fi

  say "OK: production matches the assessed state (4 splits, 2 phantoms, 10 rewrites)"
}

MODE="dry-run"
GATE_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) MODE="apply" ;;
    --dry-run) MODE="dry-run" ;;
    --gate) shift; MODE="gate"; GATE_FILE="${1:-}" ;;
    --redact) MODE="redact" ;;
    --sidecar-dir) shift; SIDECAR_DIR="${1:-}" ;;
    *) fail "usage: $(basename "$0") [--apply] [--gate FILE] [--redact] [--sidecar-dir DIR]" ;;
  esac
  shift
done

if [ "$MODE" = "gate" ]; then
  [ -n "$GATE_FILE" ] || fail "--gate needs a transcript path"
  gate_transcript "$GATE_FILE"
  exit 0
fi

# The redaction filter, on its own, over stdin. Exposed so it can be tested
# against the passwords that break a sed-based one without a production
# database anywhere in the loop.
if [ "$MODE" = "redact" ]; then
  PG_PASSWORD="${PG_PASSWORD:-}"
  PG_PASSWORD_ENC=""
  [ -z "$PG_PASSWORD" ] || PG_PASSWORD_ENC="$(encode_password)"
  redact
  exit 0
fi

[ -x "$PYTHON" ] || fail "$PYTHON not found — provision the checkout first (scripts/dev-setup.sh)"

WORK="$(mktemp -d)"
TUNNEL_PID=""
cleanup() {
  # A forwarded port to the production database outliving this script is the
  # one failure mode here that is worse than not running it at all.
  [ -z "$TUNNEL_PID" ] || kill "$TUNNEL_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM
chmod 700 "$WORK"

# --- credentials ---------------------------------------------------------

say "Reading database credentials from $PG_CONTAINER on $SSH_HOST"
PG_USER="$(ssh -o BatchMode=yes "$SSH_HOST" \
           "docker exec $PG_CONTAINER printenv POSTGRES_USER" 2>/dev/null | tr -d '\r\n')"
PG_DB="$(ssh -o BatchMode=yes "$SSH_HOST" \
         "docker exec $PG_CONTAINER printenv POSTGRES_DB" 2>/dev/null | tr -d '\r\n')"
PG_PASSWORD="$(ssh -o BatchMode=yes "$SSH_HOST" \
               "docker exec $PG_CONTAINER printenv POSTGRES_PASSWORD" 2>/dev/null | tr -d '\r\n')"
[ -n "$PG_USER" ] && [ -n "$PG_DB" ] && [ -n "$PG_PASSWORD" ] \
  || fail "could not read POSTGRES_USER/DB/PASSWORD from $PG_CONTAINER"

# The password is generated, so it may carry characters a URL would eat.
PG_PASSWORD_ENC="$(encode_password)"
REPAIR_DSN="postgresql+psycopg://${PG_USER}:${PG_PASSWORD_ENC}@127.0.0.1:${LOCAL_PORT}/${PG_DB}"

# --- tunnel --------------------------------------------------------------

say "Opening tunnel 127.0.0.1:$LOCAL_PORT -> $PG_ADDRESS"
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N \
    -L "${LOCAL_PORT}:${PG_ADDRESS}" "$SSH_HOST" &
TUNNEL_PID=$!
# ExitOnForwardFailure makes a bound-port collision a startup failure rather
# than a run against whatever else is listening on that port.
sleep 2
kill -0 "$TUNNEL_PID" 2>/dev/null || fail "tunnel did not come up (is $LOCAL_PORT already bound?)"

# --- dry run, then the gate ----------------------------------------------

say "Dry run (writes nothing)"
run_repair --document-id "$DOCUMENT_ID" --dry-run > "$WORK/dry-run.txt" 2>&1 \
  || { redact < "$WORK/dry-run.txt" >&2; fail "dry run failed"; }

redact < "$WORK/dry-run.txt" >&2
gate_transcript "$WORK/dry-run.txt"

if [ "$MODE" = "dry-run" ]; then
  say ""
  say "Dry run only. Re-run with --apply to write the repair."
  exit 0
fi

# --- apply ---------------------------------------------------------------

mkdir -p "$SIDECAR_DIR"
case "$SIDECAR_DIR" in
  /tmp/*|/var/folders/*) fail "refusing to write the revert sidecar into a temp directory" ;;
esac

say "Applying repair; revert sidecar -> $SIDECAR_DIR"
# Captured to a file rather than piped, so nothing between here and the
# terminal can lose the output of a run that has already written to
# production — the sidecar path is in there.
apply_status=0
run_repair --document-id "$DOCUMENT_ID" --sidecar-dir "$SIDECAR_DIR" \
  > "$WORK/apply.txt" 2>&1 || apply_status=$?
redact < "$WORK/apply.txt" >&2 \
  || say "WARNING: could not redact the repair's output; withheld to avoid printing the password"

SIDECAR="$(ls -1t "$SIDECAR_DIR"/page_break_repair_sidecar_*.json 2>/dev/null | head -n 1 || true)"

if [ "$apply_status" -ne 0 ]; then
  # A non-zero exit does not mean nothing was written: unresolved clauses are
  # reported as a failure after the commit. Name the sidecar before stopping.
  [ -z "$SIDECAR" ] || say "a revert sidecar was written: $SIDECAR"
  fail "repair exited $apply_status — see the output above"
fi
[ -n "$SIDECAR" ] || fail "repair reported success but wrote no revert sidecar"

# --- verify --------------------------------------------------------------
#
# Both phantoms, not just the one the eval case surfaced. Asked over psql in
# the container rather than through the tunnel, so a green result is not
# reported by the same connection that did the writing.
say "Verifying"
verify_ok=1
for prefix in "Part V > 2 >" "Part V > 3 >"; do
  count="$(ssh -o BatchMode=yes "$SSH_HOST" \
    "docker exec -i $PG_CONTAINER psql -U $PG_USER -d $PG_DB -tAc \
     \"select count(*) from source_fragment where document_id=$DOCUMENT_ID \
       and citation_path like '${prefix}%'\"" 2>/dev/null | tr -d '[:space:]')"
  say "  fragments under '${prefix}%': ${count:-?}"
  [ "${count:-1}" = "0" ] || verify_ok=0
done

say ""
say "Revert command (record this on the issue alongside the sidecar path):"
say "  unset PG_PORT   # it would override the port below (ABS-501)"
say "  DATABASE_URL=\"postgresql+psycopg://${PG_USER}:<password>@127.0.0.1:${LOCAL_PORT}/${PG_DB}\" \\"
say "    .venv/bin/python scripts/repair_page_break_splits.py --revert $SIDECAR"
say ""

[ "$verify_ok" -eq 1 ] || fail "a phantom prefix still has rows — revert with the command above"
printf 'PASS: repair applied, both phantom prefixes empty, sidecar at %s\n' "$SIDECAR"
