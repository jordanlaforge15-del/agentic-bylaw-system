"""In-process SLO tracking with a sliding-window latency buffer.

The ``LatencyTracker`` records per-request latency and status codes,
then computes real-time SLO compliance on demand. It is wired into the
FastAPI app via ``LatencyMiddleware`` and exposed at ``GET /metrics``.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SLOTargets:
    availability_pct: float = 99.5
    chat_latency_p95_seconds: float = 10.0


class LatencyTracker:
    """Thread-safe sliding-window tracker for request latency and status."""

    def __init__(self, window_seconds: int = 3600) -> None:
        self._lock = threading.Lock()
        self._window = window_seconds
        self._requests: deque[tuple[float, float, str, int]] = deque()
        self._chat_latencies: deque[tuple[float, float]] = deque()

    def record(self, endpoint: str, latency_s: float, status_code: int) -> None:
        now = time.time()
        with self._lock:
            self._requests.append((now, latency_s, endpoint, status_code))
            if "/v1/chat" in endpoint:
                self._chat_latencies.append((now, latency_s))
            self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._requests and self._requests[0][0] < cutoff:
            self._requests.popleft()
        while self._chat_latencies and self._chat_latencies[0][0] < cutoff:
            self._chat_latencies.popleft()

    def snapshot(self, targets: SLOTargets) -> dict[str, Any]:
        """Return current SLO metrics and compliance status."""
        now = time.time()
        with self._lock:
            self._prune(now)

            total = len(self._requests)
            if total == 0:
                return {
                    "window_seconds": self._window,
                    "total_requests": 0,
                    "availability_pct": 100.0,
                    "chat_latency_p95_seconds": 0.0,
                    "slo_targets": {
                        "availability_pct": targets.availability_pct,
                        "chat_latency_p95_seconds": targets.chat_latency_p95_seconds,
                    },
                    "slo_met": {"availability": True, "chat_latency_p95": True},
                }

            successful = sum(1 for _, _, _, s in self._requests if s < 500)
            availability = (successful / total) * 100

            chat_lats = sorted(lat for _, lat in self._chat_latencies)
            if chat_lats:
                idx = min(int(len(chat_lats) * 0.95), len(chat_lats) - 1)
                p95 = chat_lats[idx]
            else:
                p95 = 0.0

            return {
                "window_seconds": self._window,
                "total_requests": total,
                "successful_requests": successful,
                "availability_pct": round(availability, 3),
                "chat_latency_p95_seconds": round(p95, 3),
                "chat_requests": len(self._chat_latencies),
                "slo_targets": {
                    "availability_pct": targets.availability_pct,
                    "chat_latency_p95_seconds": targets.chat_latency_p95_seconds,
                },
                "slo_met": {
                    "availability": availability >= targets.availability_pct,
                    "chat_latency_p95": p95 <= targets.chat_latency_p95_seconds,
                },
            }
