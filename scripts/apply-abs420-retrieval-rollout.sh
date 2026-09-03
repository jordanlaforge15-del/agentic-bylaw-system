#!/usr/bin/env bash
# The production rollout of retrieval_enabled, as a procedure (ABS-420).
#
# Migration 0024 replaces production's recency-derived retrieval scope with an
# explicit per-document flag. The backfill is designed to preserve behaviour,
# which makes this rollout's risk unusual: nothing looks different if it goes
# wrong. A backfill against a corpus that moved, or a curation slip, produces a
# corpus that still answers questions — just from the wrong documents. So every
# step here is a check with a verdict, not an instruction to eyeball something:
#
#   preflight   read-only, any time. Is production still the corpus the rollout
#               was reasoned about, and is the advisor image the one that can
#               actually run the coherence audit?
#   verify      after the deploy. Did the backfill produce the predicted enabled
#               set, is the scoped audit coherent, is the monitoring tripwire
#               green for a reason?
#   curate      the only writing mode. Applies an operator-named enable/disable
#               to reach the intended set; refuses to run without --apply.
#
# Usage:
#   scripts/apply-abs420-retrieval-rollout.sh preflight
#   scripts/apply-abs420-retrieval-rollout.sh verify
#   scripts/apply-abs420-retrieval-rollout.sh curate --enable 2 --disable 1 --apply
#
# Configuration:
#   ABS420_SSH_HOST        ssh target for the production host (default: bylaw-prod)
#   ABS420_PG_CONTAINER    Postgres container name (default: bylaw-postgres)
#   ABS420_ADVISOR         advisor container name (default: bylaw-advisor)
#   ABS420_PG_USER/DB      database role and name (default: layer1 / layer1)
#
# No container is created, recreated or restarted by any mode, so this does not
# itself need the 23:00 AST maintenance window — but `verify` is only meaningful
# after the deploy that carries 0024, which does.
#
# Everything runs inside the production containers over ssh. There is no tunnel
# and no forwarded port: the advisor image carries the layer1 CLI (`layer1
# list-documents`, `enable-retrieval`, `disable-retrieval`) and the ops scripts,
# so the database credential never leaves the host and no local port is left
# listening on a production DSN.

set -euo pipefail

SSH_HOST="${ABS420_SSH_HOST:-bylaw-prod}"
PG_CONTAINER="${ABS420_PG_CONTAINER:-bylaw-postgres}"
ADVISOR_CONTAINER="${ABS420_ADVISOR:-bylaw-advisor}"
PG_USER="${ABS420_PG_USER:-layer1}"
PG_DB="${ABS420_PG_DB:-layer1}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
GATE="$REPO_ROOT/scripts/abs420_rollout_gate.py"

# The migration this rollout ships. `verify` requires the database to be at or
# past it; `preflight` requires it not to have run yet.
MIGRATION="0024_document_retrieval_enabled"

# The overlay roles the coherence audit declares. Production must check all of
# them; anything less means the deployed image cannot read its dataset configs,
# which is the state that made this endpoint answer "ok" while checking nothing
# (ABS-420 — fixed by packaging layer1/datasets/*.yaml into the wheel).
EXPECTED_DECLARED_ROLES=7

say()  { printf '%s\n' "$*" >&2; }
head2() { printf '\n=== %s ===\n' "$*" >&2; }
fail() { printf 'STOP: %s\n' "$*" >&2; exit 1; }

# --- production reads ------------------------------------------------------
#
# psql runs inside the Postgres container, so the password comes from that
# container's own environment via peer auth on the local socket. It is never
# typed, passed on a command line, or forwarded to this machine.

psql_at() {
  ssh -o BatchMode=yes "$SSH_HOST" \
    "docker exec $PG_CONTAINER psql -U $PG_USER -d $PG_DB -At -F'|' -c \"$1\""
}

advisor_exec() {
  ssh -o BatchMode=yes "$SSH_HOST" "docker exec $ADVISOR_CONTAINER $1"
}

alembic_version() { psql_at "SELECT version_num FROM alembic_version"; }

inventory() {
  local sql
  sql="$("$PYTHON" "$GATE" --print-sql | grep "^$1|" | cut -d'|' -f2-)"
  psql_at "$sql"
}

# The monitoring endpoint, read from inside the advisor container: it binds
# 127.0.0.1:8000 behind Caddy and is not routed publicly.
coherence_body() {
  advisor_exec "curl -s http://localhost:8000/v1/monitoring/corpus-coherence"
}

# --- the three checks the issue's checklist asks for -----------------------

check_coherence_endpoint() {
  local body declared status
  body="$(coherence_body)"
  declared="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("checked_roles", 0))' <<<"$body")"
  status="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("status", "?"))' <<<"$body")"

  say "corpus-coherence: status=$status checked_roles=$declared"
  if [ "$declared" -lt "$EXPECTED_DECLARED_ROLES" ]; then
    say "$body"
    fail "the audit checked $declared overlay role(s), expected $EXPECTED_DECLARED_ROLES. \
A green from an audit that loaded no declarations is not evidence of anything — \
this image predates the ABS-420 packaging fix, or a dataset config was dropped."
  fi
  if [ "$status" != "ok" ]; then
    say "$body"
    fail "corpus-coherence is '$status'. The rollout is not complete until it is 'ok' \
for a reason — see the runbook's POCS step (docs/ABS-420-RETRIEVAL-ENABLED-ROLLOUT.md)."
  fi
  say "corpus-coherence is green over all $declared declared roles."
}

check_scoped_audit() {
  # The CLI form of the same audit, scoped to the retrieval-enabled set — the
  # issue's step 4. Exit code is the verdict; its JSON report names any role
  # that is not visible and why.
  if advisor_exec "python /app/scripts/corpus_coherence_audit.py" >&2; then
    say "scoped corpus-coherence audit: coherent."
  else
    fail "the scoped corpus-coherence audit reports missing overlay role(s) — see above."
  fi
}

check_address_profile() {
  # Step 4's spot-check, run through the real retrieval path (geocode +
  # scoped overlays) rather than re-implemented in SQL, because what is being
  # checked is what a paid answer would see. Fails on the two outcomes that
  # mean the switch broke something: a zone that is not CEN-2, or a missing
  # pedestrian-street facet (Schedule 7 — see the runbook's POCS step, which
  # production needs before this can pass at all).
  advisor_exec "python -c \"
import sys
from bylaw_retrieval.retrieval import RetrievalService, retrieval_enabled_resolver
from layer1.db.session import session_scope

with session_scope() as session:
    service = RetrievalService(session, default_document_id_resolver=retrieval_enabled_resolver)
    profile = service.get_address_profile('6321 Quinpool Road')
kinds = sorted({overlay.kind for overlay in profile.overlays})
print('zone=', profile.zone)
print('overlay kinds=', kinds)
print('abuts_pedestrian_street=', profile.abuts_pedestrian_street)
print('unresolvable=', profile.unresolvable, 'outside_mapped_area=', profile.outside_mapped_area)
problems = []
if profile.zone != 'CEN-2':
    problems.append('zone is %r, expected CEN-2' % (profile.zone,))
if 'pedestrian_street' not in kinds:
    problems.append('no pedestrian_street overlay — Schedule 7 is not in scope')
if problems:
    print('SPOT CHECK FAILED: ' + '; '.join(problems))
    sys.exit(1)
print('spot check ok')
\"" >&2
}

# --- modes -----------------------------------------------------------------

mode_preflight() {
  head2 "alembic"
  local version
  version="$(alembic_version)"
  say "production is at $version"
  if [ "$version" = "$MIGRATION" ] || [[ "$version" > "$MIGRATION" ]]; then
    say "NOTE: $MIGRATION has already run — preflight is for the state before it. \
Run 'verify' instead."
  fi

  head2 "document inventory (the drift gate)"
  inventory preflight | "$PYTHON" "$GATE" --preflight

  head2 "monitoring endpoint"
  # Deliberately not fatal here: before the deploy this is expected to be the
  # broken-but-green shape, and saying so is the point.
  local body declared
  body="$(coherence_body)"
  declared="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("checked_roles", 0))' <<<"$body")"
  say "checked_roles=$declared (expected $EXPECTED_DECLARED_ROLES after the deploy)"
  if [ "$declared" -lt "$EXPECTED_DECLARED_ROLES" ]; then
    say "As expected pre-deploy: this image cannot read its dataset configs, so the \
endpoint's 'ok' means 'checked nothing'. The deploy carrying ABS-420 fixes that, \
and the audit will then report the corpus as it really is — including any role \
production has never had ingested."
  fi

  head2 "verdict"
  say "Preflight complete. Proceed with the deploy, then run: $0 verify"
}

# Both writing-adjacent modes start here. Without it, a run against a
# pre-migration database gets a raw "column retrieval_enabled does not exist"
# from psql — which reads like the tool is broken rather than like the deploy
# has not happened.
require_migration() {
  head2 "alembic"
  local version
  version="$(alembic_version)"
  say "production is at $version"
  if [[ "$version" < "$MIGRATION" ]]; then
    fail "production is at $version, before $MIGRATION — the deploy did not run \
the migration. Do not curate; fix the deploy."
  fi
}

mode_verify() {
  require_migration

  head2 "backfill result (the enabled set)"
  inventory verify | "$PYTHON" "$GATE" --verify

  head2 "scoped coherence audit"
  check_scoped_audit

  head2 "address-profile spot check — 6321 Quinpool Road"
  check_address_profile

  head2 "monitoring endpoint"
  check_coherence_endpoint

  head2 "verdict"
  say "Rollout verified: migration applied, enabled set as predicted, audit coherent, \
tripwire green over all declared roles."
}

mode_curate() {
  local enable=() disable=() apply=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --enable)  shift; enable+=("$1") ;;
      --disable) shift; disable+=("$1") ;;
      --apply)   apply=1 ;;
      *) fail "unknown curate argument: $1" ;;
    esac
    shift
  done
  [ ${#enable[@]} -eq 0 ] && [ ${#disable[@]} -eq 0 ] && fail "curate needs --enable and/or --disable ids"

  require_migration

  head2 "current state"
  inventory verify >&2

  if [ "$apply" -eq 0 ]; then
    head2 "dry run"
    [ ${#enable[@]} -gt 0 ]  && say "would run: layer1 enable-retrieval ${enable[*]} --replace"
    [ ${#disable[@]} -gt 0 ] && say "would run: layer1 disable-retrieval ${disable[*]} --yes"
    say "Nothing was changed. Re-run with --apply to write."
    return 0
  fi

  # Disable first. Enabling with --replace already disables same-bylaw
  # siblings, but an explicit disable of a *different* by-law's document must
  # not be reordered behind an enable that could warn and continue.
  if [ ${#disable[@]} -gt 0 ]; then
    head2 "disabling ${disable[*]}"
    advisor_exec "layer1 disable-retrieval ${disable[*]} --yes" >&2
  fi
  if [ ${#enable[@]} -gt 0 ]; then
    head2 "enabling ${enable[*]}"
    advisor_exec "layer1 enable-retrieval ${enable[*]} --replace --yes" >&2
  fi

  head2 "re-verifying"
  inventory verify | "$PYTHON" "$GATE" --verify
  say "Curation applied and verified. Re-run '$0 verify' for the full check."
}

case "${1:-}" in
  preflight) shift; mode_preflight "$@" ;;
  verify)    shift; mode_verify "$@" ;;
  curate)    shift; mode_curate "$@" ;;
  *) fail "usage: $0 {preflight|verify|curate --enable ID --disable ID [--apply]}" ;;
esac
