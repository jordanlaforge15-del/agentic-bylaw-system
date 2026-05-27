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

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from advisor.monitoring.probe import HealthProbe

router = APIRouter()

_DEFAULT_TARGET = "http://localhost:8000/healthz"


@router.get("/v1/monitoring/status", include_in_schema=True)
async def monitoring_status() -> JSONResponse:
    """Run one health probe and return monitoring config + result."""
    target_url = os.environ.get("MONITOR_TARGET_URL", _DEFAULT_TARGET)
    interval = int(os.environ.get("MONITOR_PROBE_INTERVAL_SECONDS", "60"))
    threshold = int(os.environ.get("MONITOR_FAILURE_THRESHOLD", "2"))
    slack_configured = bool(os.environ.get("MONITOR_SLACK_WEBHOOK_URL"))
    email_configured = bool(
        os.environ.get("MONITOR_SMTP_HOST") and os.environ.get("MONITOR_ALERT_EMAIL_TO")
    )

    probe = HealthProbe(url=target_url, timeout_seconds=10.0)
    result = probe.check()

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
