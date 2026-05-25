"""External health-check probe with consecutive-failure alerting.

Runs as a standalone daemon (see ``scripts/availability-monitor.py``)
and polls the ``/healthz`` endpoint at a configurable interval. Fires
alerts after N consecutive failures and sends a recovery notification
when the service comes back.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from advisor.monitoring.alerts import AlertSender
    from advisor.monitoring.config import MonitoringSettings

logger = logging.getLogger(__name__)


class HealthProbe:
    def __init__(self, settings: MonitoringSettings, alert_sender: AlertSender) -> None:
        self._settings = settings
        self._alert_sender = alert_sender
        self._consecutive_failures: int = 0
        self._alerted: bool = False
        self._total_checks: int = 0
        self._total_failures: int = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def is_alerted(self) -> bool:
        return self._alerted

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_checks": self._total_checks,
            "total_failures": self._total_failures,
            "consecutive_failures": self._consecutive_failures,
            "alerted": self._alerted,
        }

    def check(self) -> dict[str, Any]:
        """Execute a single health check and handle alert transitions."""
        self._total_checks += 1
        healthy = False
        data: dict[str, Any] = {}

        try:
            resp = httpx.get(self._settings.healthz_url, timeout=10)
            data = resp.json()
            healthy = resp.status_code == 200 and data.get("status") == "ok"
        except Exception as exc:
            data = {"error": str(exc)}

        if healthy:
            if self._alerted:
                self._alert_sender.send(
                    "Service Recovered",
                    f"{self._settings.healthz_url} is healthy again "
                    f"after {self._consecutive_failures} consecutive failures.",
                )
            self._consecutive_failures = 0
            self._alerted = False
        else:
            self._total_failures += 1
            self._consecutive_failures += 1
            threshold = self._settings.consecutive_failures_threshold
            if self._consecutive_failures >= threshold and not self._alerted:
                self._alert_sender.send(
                    "Service Down",
                    f"{self._settings.healthz_url} has failed "
                    f"{self._consecutive_failures} consecutive checks.\n"
                    f"Last response: {data}",
                )
                self._alerted = True

        return {
            "healthy": healthy,
            "consecutive_failures": self._consecutive_failures,
            "alerted": self._alerted,
            "response": data,
            "timestamp": time.time(),
        }

    def run_loop(self) -> None:
        """Block forever, probing at the configured interval."""
        logger.info(
            "Starting health probe: url=%s interval=%ds threshold=%d",
            self._settings.healthz_url,
            self._settings.probe_interval_seconds,
            self._settings.consecutive_failures_threshold,
        )
        while True:
            result = self.check()
            status = "healthy" if result["healthy"] else "FAILING"
            logger.info(
                "Probe %s — consecutive_failures=%d",
                status,
                result["consecutive_failures"],
            )
            time.sleep(self._settings.probe_interval_seconds)
