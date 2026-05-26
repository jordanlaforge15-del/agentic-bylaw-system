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

echo "[preflight] Image:       ${FULL_IMAGE}"
echo "[preflight] Env file:    ${ENV_FILE}"
echo "[preflight] Network:     ${NETWORK}"
echo "[preflight] Constraints: --read-only  --tmpfs /tmp  --cap-drop ALL  --no-new-privileges"
echo "[preflight] Command:     python -c 'import advisor.api.main'"
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
    python -c "import advisor.api.main"
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
