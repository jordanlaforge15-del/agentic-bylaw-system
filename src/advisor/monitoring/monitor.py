"""MonitorService — ties probe + tracker + alerters into a polling loop."""
from __future__ import annotations

import asyncio
import logging
import os

from advisor.monitoring.alerting import build_alerters_from_env
from advisor.monitoring.probe import HealthProbe, ProbeResult
from advisor.monitoring.state import FailureTracker

logger = logging.getLogger(__name__)

_DEFAULT_TARGET = "http://localhost:8000/healthz"
_DEFAULT_INTERVAL = 60
_DEFAULT_THRESHOLD = 2


class MonitorService:
    """Polls a health endpoint at *interval_seconds* and fires alerters.

    Designed to be run as a standalone process (see ``monitoring.main``).
    Can also be instantiated in tests with injected alerters.
    """

    def __init__(
        self,
        target_url: str = _DEFAULT_TARGET,
        interval_seconds: int = _DEFAULT_INTERVAL,
        failure_threshold: int = _DEFAULT_THRESHOLD,
        alerters: list | None = None,
        probe: HealthProbe | None = None,
    ) -> None:
        self.target_url = target_url
        self.interval_seconds = interval_seconds
        self._alerters = alerters if alerters is not None else []
        self._probe = probe or HealthProbe(url=target_url)
        self._tracker = FailureTracker(
            failure_threshold=failure_threshold,
            on_alert=self._dispatch_alerts,
        )

    def _dispatch_alerts(self, result: ProbeResult, consecutive_failures: int) -> None:
        for alerter in self._alerters:
            try:
                alerter.send(result, consecutive_failures)
            except Exception:  # noqa: BLE001
                logger.exception("alerter %r raised unexpectedly", alerter)

    def run_once(self) -> ProbeResult:
        """Execute a single probe cycle (probe → record → maybe alert)."""
        result = self._probe.check()
        self._tracker.record(result)
        level = logging.INFO if result.healthy else logging.WARNING
        logger.log(
            level,
            "health probe",
            extra={
                "url": result.url,
                "status": result.status_label,
                "http_status": result.http_status,
                "response_time_ms": result.response_time_ms,
                "consecutive_failures": self._tracker.consecutive_failures,
            },
        )
        return result

    async def run_forever(self) -> None:
        """Async polling loop — runs until cancelled."""
        logger.info(
            "monitor starting",
            extra={
                "target_url": self.target_url,
                "interval_seconds": self.interval_seconds,
                "failure_threshold": self._tracker.failure_threshold,
                "alerters": len(self._alerters),
            },
        )
        while True:
            # run_once() calls probe.check() (blocking urllib I/O) — offload to
            # thread pool so any future async co-tasks are not serialised behind it.
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(self.interval_seconds)

    @property
    def tracker(self) -> FailureTracker:
        return self._tracker

    @classmethod
    def from_env(cls) -> "MonitorService":
        target = os.environ.get("MONITOR_TARGET_URL", _DEFAULT_TARGET)
        interval = int(os.environ.get("MONITOR_PROBE_INTERVAL_SECONDS", str(_DEFAULT_INTERVAL)))
        threshold = int(os.environ.get("MONITOR_FAILURE_THRESHOLD", str(_DEFAULT_THRESHOLD)))
        alerters = build_alerters_from_env()
        return cls(
            target_url=target,
            interval_seconds=interval,
            failure_threshold=threshold,
            alerters=alerters,
        )
