#!/usr/bin/env bash
# Pre-deploy smoke gate for the bylaw-advisor image.
#
# Runs the new image under prod-mirroring runtime constraints (read-only
# filesystem, tmpfs /tmp, all capabilities dropped, no-new-privileges)
# and imports advisor.api.main — the same module that uvicorn loads on
# startup. This catches:
#
#   1. Missing runtime imports: e.g. a package in [bim] but not in the
#      [advisor] extras (the v0.8.3/pyproj class of bug). The lazy
#      imports inside create_app are only triggered when db_session_factory
#      is wired, which build_app() does — so this catches deps that a bare
#      create_app() call would miss.
#
#   2. Startup-time filesystem assumptions: anything that writes to the
#      filesystem outside /tmp during import or app construction will fail
#      under --read-only (the v0.8.4/mkdir class of bug).
#
#   3. Missing package data: non-.py assets the installed wheel must carry
#      (taxonomy.json, prompt assets, compliance taxonomy). These are only
#      read when enrichment/eval code runs, so a bare import of
#      advisor.api.main passes even when they're absent — the ABS-412 /
#      ABS-409-heal class of bug. The smoke command loads each one
#      explicitly.
#
#   4. A corpus-coherence audit that cannot read its declaration set. This
#      one is worse than a crash: with the layer1 dataset configs missing,
#      /v1/monitoring/corpus-coherence answered {"status":"ok"} while
#      checking zero overlay roles, for the whole life of the endpoint
#      (ABS-420). The smoke command loads the declarations and requires a
#      non-empty set, so a packaging regression fails the deploy instead of
#      quietly disarming the tripwire.
#
# Usage (run on the production server via SSH before container swap):
#   ./scripts/preflight_advisor_image.sh <tag>
#   e.g.:  ./scripts/preflight_advisor_image.sh v1.2.3
#
#   Via SSH from the deploy workstation:
#   ssh bylaw-prod "cd /srv/bylaw && \
#     docker run --rm --read-only \
#       --tmpfs /tmp:size=64m,mode=1777 \
#       --env-file /srv/bylaw/.env \
#       --network bylaw_default \
#       --cap-drop ALL \
#       --security-opt no-new-privileges:true \
#       ghcr.io/jordanlaforge15-del/bylaw-advisor:<tag> \
#       python -c 'import advisor.api.main'"
#   (the manual one-liner above only covers check 1+2; prefer running the
#   script itself, whose smoke command also exercises package data)
#
# Exit codes:
#   0          — smoke passed; safe to proceed with docker compose up -d advisor
#   non-zero   — smoke failed; ABORT the deploy; old container keeps running
#
# This is a HARD GATE between `docker compose pull advisor` and
# `docker compose up -d advisor`. If it exits non-zero, do NOT swap the
# container — no rollback is needed because the running container is unchanged.
#
# Environment overrides (useful for CI or alternate installs):
#   BYLAW_ENV_FILE  — path to the .env file (default: /srv/bylaw/.env)
#   BYLAW_NETWORK   — Docker network name   (default: bylaw_default)

set -euo pipefail

REGISTRY="ghcr.io/jordanlaforge15-del"
IMAGE="${REGISTRY}/bylaw-advisor"
ENV_FILE="${BYLAW_ENV_FILE:-/srv/bylaw/.env}"
NETWORK="${BYLAW_NETWORK:-bylaw_default}"

usage() {
    echo "Usage: $0 <tag>" >&2
    echo "  e.g.: $0 v1.2.3" >&2
    exit 1
}

[[ $# -lt 1 ]] && usage

TAG="$1"
FULL_IMAGE="${IMAGE}:${TAG}"

# The smoke body: app construction + every module-relative data asset the
# wheel must ship (kept in sync with tests/test_package_data.py).
SMOKE_PY="
import advisor.api.main
from layer1.semantic.taxonomy import load_taxonomy as load_l1_taxonomy
assert load_l1_taxonomy()['entity_types']
from layer2.compliance.taxonomy import load_taxonomy as load_l2_taxonomy
assert load_l2_taxonomy().attributes
from layer2.prompts.builder import load_system_prompt
assert load_system_prompt()
from bylaw_retrieval.retrieval.coherence_audit import load_overlay_declarations
declarations = load_overlay_declarations()
assert declarations, 'corpus-coherence audit loaded zero overlay declarations'
print('[preflight] overlay declarations:', len(declarations))
print('[preflight] app import + package data OK')
"

echo "[preflight] Image:       ${FULL_IMAGE}"
echo "[preflight] Env file:    ${ENV_FILE}"
echo "[preflight] Network:     ${NETWORK}"
echo "[preflight] Constraints: --read-only  --tmpfs /tmp  --cap-drop ALL  --no-new-privileges"
echo "[preflight] Command:     python -c '<app import + package-data smoke>'"
echo

set +e
docker run --rm \
    --read-only \
    --tmpfs /tmp:size=64m,mode=1777 \
    --env-file "${ENV_FILE}" \
    --network "${NETWORK}" \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    "${FULL_IMAGE}" \
    python -c "${SMOKE_PY}"
SMOKE_EXIT=$?
set -e

echo
if [ "${SMOKE_EXIT}" -eq 0 ]; then
    echo "[preflight] PASS — ${FULL_IMAGE} constructed the FastAPI app successfully."
    echo "[preflight] Safe to proceed with: docker compose up -d advisor"
else
    echo "[preflight] FAIL (exit ${SMOKE_EXIT}) — ${FULL_IMAGE} could not initialize." >&2
    echo "[preflight] DEPLOY ABORTED. The old container is still running — no rollback needed." >&2
    echo "[preflight] Check the output above for the failing import or startup error." >&2
    exit "${SMOKE_EXIT}"
fi
