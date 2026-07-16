"""HTTP health probe — one-shot check against a target URL."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ProbeResult:
    timestamp: datetime
    url: str
    healthy: bool
    http_status: int | None
    response_time_ms: float
    error: str | None = None
    # SLI fields forwarded from the /healthz response body (when present)
    sli: dict | None = None

    @property
    def status_label(self) -> str:
        return "healthy" if self.healthy else "unhealthy"


class HealthProbe:
    """Fetches *url* once and returns a :class:`ProbeResult`.

    A probe is "healthy" when the HTTP response status is < 500.  A 503
    (degraded but reachable) is therefore *unhealthy*; the system must
    fully recover (200) to reset the failure counter.
    """

    def __init__(self, url: str, timeout_seconds: float = 10.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def check(self) -> ProbeResult:
        import urllib.request  # stdlib — no extra deps for the monitor
        import urllib.error
        import json

        t0 = time.monotonic()
        now = datetime.now(tz=timezone.utc)
        try:
            req = urllib.request.Request(self.url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                status = resp.status
                try:
                    body = json.loads(resp.read())
                    sli = body.get("sli")
                except Exception:  # noqa: BLE001
                    sli = None
                healthy = status < 500
                return ProbeResult(
                    timestamp=now,
                    url=self.url,
                    healthy=healthy,
                    http_status=status,
                    response_time_ms=round(elapsed_ms, 1),
                    sli=sli,
                )
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProbeResult(
                timestamp=now,
                url=self.url,
                healthy=False,
                http_status=exc.code,
                response_time_ms=round(elapsed_ms, 1),
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.monotonic() - t0) * 1000
            return ProbeResult(
                timestamp=now,
                url=self.url,
                healthy=False,
                http_status=None,
                response_time_ms=round(elapsed_ms, 1),
                error=str(exc),
            )
