"""FastAPI router exposing ``/v1/monitoring/status``.

This endpoint is served by the advisor process itself (not the monitor
container) so operators can confirm that:

  1. The advisor can reach its own /healthz (on-demand single probe).
  2. The monitoring configuration (interval, threshold, alerters) is
     correctly derived from the current environment.

The route is unauthenticated — it returns no secrets and no user data.
Alerting credentials are never echoed; only a boolean ``configured``
flag is returned.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from advisor.monitoring.probe import HealthProbe, ProbeResult

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_TARGET = "http://localhost:8000/healthz"

# Cache the last probe result for up to 5 s to avoid amplifying load on /healthz
# from aggressive or misconfigured pollers.
_cache_ttl: float = 5.0
_cached_result: ProbeResult | None = None
_cached_at: float = 0.0

# Same amplification concern for the corpus-coherence audit (ABS-356): it's
# cheap (a handful of dataset-config reads + scoped queries), but a
# misconfigured poller hitting this every few seconds shouldn't turn into
# that many extra round trips against the DB.
_coherence_cache_ttl: float = 30.0
_cached_coherence_body: dict[str, Any] | None = None
_cached_coherence_status: int = 200
_cached_coherence_at: float = 0.0


@router.get("/v1/monitoring/status", include_in_schema=True)
async def monitoring_status() -> JSONResponse:
    """Run one health probe and return monitoring config + result."""
    global _cached_result, _cached_at  # noqa: PLW0603

    target_url = os.environ.get("MONITOR_TARGET_URL", _DEFAULT_TARGET)
    interval = int(os.environ.get("MONITOR_PROBE_INTERVAL_SECONDS", "60"))
    threshold = int(os.environ.get("MONITOR_FAILURE_THRESHOLD", "2"))
    slack_configured = bool(os.environ.get("MONITOR_SLACK_WEBHOOK_URL"))
    email_configured = bool(
        os.environ.get("MONITOR_SMTP_HOST") and os.environ.get("MONITOR_ALERT_EMAIL_TO")
    )

    now = time.monotonic()
    if _cached_result is None or (now - _cached_at) >= _cache_ttl:
        probe = HealthProbe(url=target_url, timeout_seconds=10.0)
        # probe.check() uses urllib (blocking I/O) — offload to thread pool
        # so we don't stall the event loop for the full probe timeout.
        _cached_result = await asyncio.to_thread(probe.check)
        _cached_at = now

    result = _cached_result
    body = {
        "probe": {
            "url": result.url,
            "status": result.status_label,
            "http_status": result.http_status,
            "response_time_ms": result.response_time_ms,
            "timestamp": result.timestamp.isoformat(),
            "error": result.error,
            "sli": result.sli,
        },
        "config": {
            "probe_interval_seconds": interval,
            "failure_threshold": threshold,
            "alerting": {
                "slack_configured": slack_configured,
                "email_configured": email_configured,
            },
        },
    }
    status_code = 200 if result.healthy else 503
    return JSONResponse(content=body, status_code=status_code)


def _e2e_markers_expected() -> bool:
    """True when this deployment legitimately hosts e2e fixture rows.

    Set (as ``ADVISOR_E2E_MARKERS_EXPECTED=1``) only by the e2e entrypoint
    (``advisor.api.e2e_server``), whose database IS the e2e suite's own
    instance — its seeded ``e2e-seed`` documents are fixtures, not
    contamination. Production and dev never set it, so any marker row there
    turns this endpoint red (ABS-432).
    """
    return os.environ.get("ADVISOR_E2E_MARKERS_EXPECTED", "") == "1"


def _run_corpus_coherence_audit() -> tuple[dict[str, Any], int]:
    """Blocking body of the corpus-coherence check, run off the event loop."""
    from bylaw_retrieval.retrieval import (  # noqa: PLC0415
        audit_corpus_coherence,
        audit_e2e_contamination,
        audit_enabled_name_collisions,
        audit_governing_bylaw_coverage,
        retrieval_enabled_resolver,
    )
    from layer1.db.session import session_scope  # noqa: PLC0415

    try:
        with session_scope() as session:
            report = audit_corpus_coherence(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
            contamination = audit_e2e_contamination(session)
            name_collisions = audit_enabled_name_collisions(session)
            coverage = audit_governing_bylaw_coverage(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
    except Exception as exc:
        logger.exception("corpus-coherence audit (ABS-356) failed to run")
        # Name the failure. An operator reading this at 23:00 during a rollout
        # needs to tell "the database is unreachable" from "this image cannot
        # read its dataset configs" (ABS-420) without shelling into the box.
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}, 503

    # An audit with nothing to check is not a passing audit. Production ran
    # for months on {"status":"ok","checked_roles":0} because the deployed
    # image had no dataset configs to declare roles from (ABS-420) — a green
    # that would have stayed green through every degradation it exists to
    # catch. Zero declarations is now an error, in every deployment.
    if report.checked_roles == 0:
        logger.error(
            "corpus-coherence audit (ABS-356) loaded zero overlay declarations — "
            "the deployment cannot read its layer1 dataset configs"
        )
        return {
            "status": "error",
            "detail": (
                "no overlay declarations loaded — this deployment cannot read its "
                "layer1 dataset configs, so the audit checked nothing"
            ),
            "checked_roles": 0,
            "bylaws_checked": report.bylaws_checked,
            "missing": [],
        }, 503

    if not report.coherent:
        logger.warning(
            "corpus-coherence audit (ABS-356) found %d missing overlay role(s): %s",
            len(report.missing),
            [(m.role, m.bylaw_name, m.reason) for m in report.missing],
        )

    # ABS-432: e2e-contamination tripwire. Green only when zero marker rows —
    # except in the e2e stack itself, where the suite's fixtures legitimately
    # carry the markers and are reported informationally instead.
    markers_expected = _e2e_markers_expected()
    contamination_red = contamination.contaminated and not markers_expected
    if contamination_red:
        logger.warning(
            "e2e-contamination sweep (ABS-432) found %d marker row(s) in a "
            "non-test database: %s",
            len(contamination.markers),
            [m.detail for m in contamination.markers],
        )
    if contamination.contaminated:
        contamination_status = "expected_test_fixtures" if markers_expected else "contaminated"
    else:
        contamination_status = "ok"

    # ABS-434: enabled-name-collision tripwire. Unlike the contamination
    # sweep there is no deployment where >1 enabled document per normalized
    # (municipality, bylaw_name) is legitimate — red is red everywhere,
    # including the e2e stack's own database.
    if not name_collisions.collision_free:
        logger.warning(
            "enabled-name-collision audit (ABS-434) found %d normalized bylaw "
            "identit(ies) with multiple retrieval-enabled documents: %s",
            len(name_collisions.collisions),
            [c.detail for c in name_collisions.collisions],
        )

    if not report.coherent:
        status = "incoherent"
    elif contamination_red:
        status = "contaminated"
    elif not name_collisions.collision_free:
        status = "name_collision"
    else:
        status = "ok"

    body = {
        "status": status,
        "checked_roles": report.checked_roles,
        "bylaws_checked": report.bylaws_checked,
        "missing": [m.model_dump() for m in report.missing],
        "e2e_contamination": {
            "status": contamination_status,
            **contamination.model_dump(mode="json"),
        },
        "enabled_name_collisions": {
            "status": "ok" if name_collisions.collision_free else "collision",
            **name_collisions.model_dump(mode="json"),
        },
        # ABS-472: informational only, and deliberately excluded from
        # ``status``. A municipality publishes far more by-law areas than any
        # corpus ingests, so incomplete coverage is the steady state, not a
        # regression — turning it red would leave this endpoint permanently
        # 503 and train operators to ignore it. What it reports is how much
        # mapped ground answers get refused on, and which by-law to ingest
        # next.
        "governing_bylaw_coverage": coverage.model_dump(mode="json"),
    }
    return body, (200 if status == "ok" else 503)


@router.get("/v1/monitoring/corpus-coherence", include_in_schema=True)
async def corpus_coherence_status() -> JSONResponse:
    """Audit that every declared overlay role is visible in retrieval scope (ABS-356).

    Loud-failure ops surface for the ABS-349/ABS-350 class of degradation: a
    linked geo dataset (zone, height_precinct, heritage, ...) falling out of
    ``get_address_profile``'s retrieval scope — unlinked, orphaned, or
    evicted by a newer ingest of the same bylaw — used to surface only as a
    null profile field or a customer-visible hedged answer. This returns 503
    with the specific missing role(s) and reason instead, and logs a warning
    on every incoherent result so it lands in ops logs even for a poller that
    only alerts on repeated failures.

    Also carries the ABS-432 ``e2e_contamination`` tripwire: the body's
    ``e2e_contamination`` object reports every row bearing an e2e fixture
    marker (``parser_version='e2e-seed'``, ``file_hash 'e2e-%'``,
    ``external_dataset.name 'e2e_%'``). Green only when zero — in a non-test
    deployment any marker row flips the endpoint to 503/``contaminated``.
    The e2e stack itself (``advisor.api.e2e_server``) declares its markers
    expected and reports them informationally.

    And the ABS-434 ``enabled_name_collisions`` tripwire: at most one
    retrieval-enabled document per case/hyphen/whitespace-normalized
    ``(municipality, bylaw_name)``. More than one (the doc-15/38 "By-law"
    vs "By-Law" double-enable) flips the endpoint to
    503/``name_collision`` in every deployment — no expected-fixtures
    exemption, because a fragmented enabled corpus is never legitimate.
    """
    global _cached_coherence_body, _cached_coherence_status, _cached_coherence_at  # noqa: PLW0603

    now = time.monotonic()
    if _cached_coherence_body is None or (now - _cached_coherence_at) >= _coherence_cache_ttl:
        _cached_coherence_body, _cached_coherence_status = await asyncio.to_thread(
            _run_corpus_coherence_audit
        )
        _cached_coherence_at = now

    return JSONResponse(content=_cached_coherence_body, status_code=_cached_coherence_status)
