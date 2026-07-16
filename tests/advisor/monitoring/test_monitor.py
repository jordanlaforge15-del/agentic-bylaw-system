"""Unit tests for advisor.monitoring.monitor (MonitorService)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from advisor.monitoring.monitor import MonitorService
from advisor.monitoring.probe import HealthProbe, ProbeResult


def _healthy_result() -> ProbeResult:
    return ProbeResult(
        timestamp=datetime.now(tz=timezone.utc),
        url="http://localhost/healthz",
        healthy=True,
        http_status=200,
        response_time_ms=10.0,
    )


def _unhealthy_result() -> ProbeResult:
    return ProbeResult(
        timestamp=datetime.now(tz=timezone.utc),
        url="http://localhost/healthz",
        healthy=False,
        http_status=None,
        response_time_ms=0.5,
        error="Connection refused",
    )


class TestMonitorService:
    def test_run_once_returns_probe_result(self):
        probe = MagicMock(spec=HealthProbe)
        probe.check.return_value = _healthy_result()
        service = MonitorService(probe=probe)
        result = service.run_once()
        assert result.healthy is True

    def test_alerts_dispatched_on_consecutive_failures(self):
        probe = MagicMock(spec=HealthProbe)
        probe.check.return_value = _unhealthy_result()
        alerter = MagicMock()
        service = MonitorService(
            probe=probe,
            failure_threshold=2,
            alerters=[alerter],
        )
        service.run_once()
        alerter.send.assert_not_called()
        service.run_once()
        alerter.send.assert_called_once()

    def test_no_alert_for_single_failure(self):
        probe = MagicMock(spec=HealthProbe)
        probe.check.return_value = _unhealthy_result()
        alerter = MagicMock()
        service = MonitorService(
            probe=probe,
            failure_threshold=2,
            alerters=[alerter],
        )
        service.run_once()
        alerter.send.assert_not_called()

    def test_tracker_resets_after_recovery(self):
        probe = MagicMock(spec=HealthProbe)
        alerter = MagicMock()
        service = MonitorService(probe=probe, failure_threshold=2, alerters=[alerter])
        probe.check.return_value = _unhealthy_result()
        service.run_once()
        service.run_once()  # triggers alert
        probe.check.return_value = _healthy_result()
        service.run_once()  # recovery
        assert service.tracker.consecutive_failures == 0

    def test_from_env_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("MONITOR_TARGET_URL", "http://example.com/healthz")
        monkeypatch.setenv("MONITOR_PROBE_INTERVAL_SECONDS", "30")
        monkeypatch.setenv("MONITOR_FAILURE_THRESHOLD", "3")
        monkeypatch.delenv("MONITOR_SLACK_WEBHOOK_URL", raising=False)
        service = MonitorService.from_env()
        assert service.target_url == "http://example.com/healthz"
        assert service.interval_seconds == 30
        assert service.tracker.failure_threshold == 3
