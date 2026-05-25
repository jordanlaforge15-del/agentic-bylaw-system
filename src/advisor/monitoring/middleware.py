"""ASGI middleware that records per-request latency into a ``LatencyTracker``.

Uses a raw ASGI wrapper (not ``BaseHTTPMiddleware``) so SSE / streaming
responses pass through without buffering. Latency is measured as
time-to-first-byte: the clock stops when the ``http.response.start``
message is sent, which is the meaningful metric for both regular JSON
endpoints and the ``/v1/chat`` SSE stream.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from advisor.monitoring.slo import LatencyTracker


class LatencyMiddleware:
    def __init__(self, app: ASGIApp, *, tracker: LatencyTracker) -> None:
        self.app = app
        self._tracker = tracker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status_code = 200
        recorded = False

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, recorded
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                if not recorded:
                    latency = time.monotonic() - start
                    self._tracker.record(scope.get("path", ""), latency, status_code)
                    recorded = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not recorded:
                latency = time.monotonic() - start
                self._tracker.record(scope.get("path", ""), latency, 500)
            raise
