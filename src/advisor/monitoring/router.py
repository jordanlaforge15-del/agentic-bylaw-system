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


def _run_corpus_coherence_audit() -> tuple[dict[str, Any], int]:
    """Blocking body of the corpus-coherence check, run off the event loop."""
    from bylaw_retrieval.retrieval import (  # noqa: PLC0415
        audit_corpus_coherence,
        retrieval_enabled_resolver,
    )
    from layer1.db.session import session_scope  # noqa: PLC0415

    try:
        with session_scope() as session:
            report = audit_corpus_coherence(
                session, default_document_id_resolver=retrieval_enabled_resolver
            )
    except Exception:
        logger.exception("corpus-coherence audit (ABS-356) failed to run")
        return {"status": "error"}, 503

    if not report.coherent:
        logger.warning(
            "corpus-coherence audit (ABS-356) found %d missing overlay role(s): %s",
            len(report.missing),
            [(m.role, m.bylaw_name, m.reason) for m in report.missing],
        )

    body = {
        "status": "ok" if report.coherent else "incoherent",
        "checked_roles": report.checked_roles,
        "bylaws_checked": report.bylaws_checked,
        "missing": [m.model_dump() for m in report.missing],
    }
    return body, (200 if report.coherent else 503)


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
    """
    global _cached_coherence_body, _cached_coherence_status, _cached_coherence_at  # noqa: PLW0603

    now = time.monotonic()
    if _cached_coherence_body is None or (now - _cached_coherence_at) >= _coherence_cache_ttl:
        _cached_coherence_body, _cached_coherence_status = await asyncio.to_thread(
            _run_corpus_coherence_audit
        )
        _cached_coherence_at = now

    return JSONResponse(content=_cached_coherence_body, status_code=_cached_coherence_status)
