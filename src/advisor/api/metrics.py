"""Prometheus metrics and SLI/SLO definitions for the Bylaw Advisor API.

SLI definitions:
  - Availability: fraction of non-5xx responses across all endpoints
  - Latency: request duration histogram, with chat-specific tracking
  - Error rate: fraction of 5xx responses across all endpoints

SLO targets:
  - Availability >= 99.5%
  - Chat response latency p95 < 10s
  - Error rate < 1%
"""
from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram, Info, generate_latest
from starlette.responses import Response

SLO_AVAILABILITY_TARGET = 0.995
SLO_CHAT_LATENCY_P95_SECONDS = 10.0
SLO_ERROR_RATE_CEILING = 0.01

slo_info = Info(
    "advisor_slo",
    "Service Level Objective targets",
)
slo_info.info(
    {
        "availability_target": str(SLO_AVAILABILITY_TARGET),
        "chat_latency_p95_seconds": str(SLO_CHAT_LATENCY_P95_SECONDS),
        "error_rate_ceiling": str(SLO_ERROR_RATE_CEILING),
    }
)

http_requests_total = Counter(
    "advisor_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "advisor_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

chat_request_duration_seconds = Histogram(
    "advisor_chat_request_duration_seconds",
    "Chat endpoint request duration in seconds (time to first byte)",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0),
)

http_requests_in_progress = Gauge(
    "advisor_http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "endpoint"],
)

http_5xx_total = Counter(
    "advisor_http_5xx_total",
    "Total 5xx responses (error-rate SLI numerator)",
)

_SKIP_PATHS = frozenset({"/metrics"})


def _normalize_path(path: str) -> str:
    """Collapse path parameters to reduce cardinality."""
    parts = path.rstrip("/").split("/")
    normalized = []
    for i, part in enumerate(parts):
        if i > 0 and normalized and normalized[-1] == "sessions" and part:
            normalized.append("{id}")
        elif part.isdigit():
            normalized.append("{id}")
        else:
            normalized.append(part)
    return "/".join(normalized) or "/"


class PrometheusMiddleware:
    """Pure ASGI middleware for Prometheus request metrics.

    Uses raw ASGI protocol instead of BaseHTTPMiddleware to avoid
    breaking SSE/streaming responses (EventSourceResponse on /v1/chat).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        endpoint = _normalize_path(path)
        is_chat = path == "/v1/chat"

        http_requests_in_progress.labels(method=method, endpoint=endpoint).inc()
        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                if is_chat:
                    chat_request_duration_seconds.observe(
                        time.monotonic() - start
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if is_chat and status_code == 500:
                chat_request_duration_seconds.observe(time.monotonic() - start)
            raise
        finally:
            duration = time.monotonic() - start
            http_requests_in_progress.labels(
                method=method, endpoint=endpoint
            ).dec()
            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).inc()
            http_request_duration_seconds.labels(
                method=method, endpoint=endpoint
            ).observe(duration)
            if status_code >= 500:
                http_5xx_total.inc()


def metrics_response() -> Response:
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
