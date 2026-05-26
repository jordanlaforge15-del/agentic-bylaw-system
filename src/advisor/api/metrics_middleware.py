"""Starlette middleware that records per-request Prometheus metrics.

Captures request count, latency histogram, error count, and in-progress
gauge for every HTTP request. Endpoint labels are normalised so path
parameters (``/v1/chat/sessions/42``) collapse into their route template
(``/v1/chat/sessions/{session_id}``), keeping cardinality bounded.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from advisor.api.metrics import (
    REQUEST_COUNT,
    REQUEST_ERRORS,
    REQUEST_IN_PROGRESS,
    REQUEST_LATENCY,
)

_SKIP_PATHS = frozenset({"/metrics", "/healthz", "/readyz"})


def _resolve_endpoint(request: Request) -> str:
    """Return the route template for the request, or the raw path."""
    app = request.app
    if hasattr(app, "routes"):
        for route in app.routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                return getattr(route, "path", request.url.path)
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP metrics for every request (except health/metrics paths)."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        endpoint = _resolve_endpoint(request)
        method = request.method

        REQUEST_IN_PROGRESS.labels(method=method, endpoint=endpoint).inc()
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            REQUEST_ERRORS.labels(
                method=method, endpoint=endpoint, status="500"
            ).inc()
            REQUEST_COUNT.labels(
                method=method, endpoint=endpoint, status="500"
            ).inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(elapsed)
            REQUEST_IN_PROGRESS.labels(method=method, endpoint=endpoint).dec()

        status = str(response.status_code)
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
        if response.status_code >= 500:
            REQUEST_ERRORS.labels(
                method=method, endpoint=endpoint, status=status
            ).inc()

        return response
