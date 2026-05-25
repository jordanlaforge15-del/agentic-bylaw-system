"""Unit tests for the health-check probe and alerting logic."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from advisor.monitoring.config import MonitoringSettings
from advisor.monitoring.probe import HealthProbe


@pytest.fixture
def settings():
    return MonitoringSettings(
        MONITORING_ENABLED=True,
        MONITORING_HEALTHZ_URL="http://localhost:1/healthz",
        MONITORING_PROBE_INTERVAL=1,
        MONITORING_FAILURE_THRESHOLD=2,
    )


@pytest.fixture
def sender():
    return MagicMock()


def _ok_response():
    return httpx.Response(200, json={"status": "ok"})


def _fail_response():
    return httpx.Response(503, json={"status": "degraded"})


def test_healthy_check_resets_failures(settings, sender):
    with patch("httpx.get", return_value=_ok_response()):
        probe = HealthProbe(settings, sender)
        result = probe.check()

    assert result["healthy"] is True
    assert result["consecutive_failures"] == 0
    sender.send.assert_not_called()


def test_consecutive_failures_trigger_alert(settings, sender):
    with patch("httpx.get", return_value=_fail_response()):
        probe = HealthProbe(settings, sender)
        probe.check()
        assert probe.consecutive_failures == 1
        sender.send.assert_not_called()

        probe.check()
        assert probe.consecutive_failures == 2
        assert probe.is_alerted is True
        sender.send.assert_called_once()
        assert "Service Down" in sender.send.call_args[0][0]


def test_recovery_sends_notification(settings, sender):
    probe = HealthProbe(settings, sender)
    with patch("httpx.get", return_value=_fail_response()):
        probe.check()
        probe.check()
    sender.send.reset_mock()

    with patch("httpx.get", return_value=_ok_response()):
        probe.check()

    assert probe.consecutive_failures == 0
    assert probe.is_alerted is False
    sender.send.assert_called_once()
    assert "Recovered" in sender.send.call_args[0][0]


def test_no_duplicate_alerts(settings, sender):
    with patch("httpx.get", return_value=_fail_response()):
        probe = HealthProbe(settings, sender)
        for _ in range(5):
            probe.check()

    assert probe.consecutive_failures == 5
    sender.send.assert_called_once()


def test_connection_error_counts_as_failure(settings, sender):
    with patch("httpx.get", side_effect=ConnectionError("refused")):
        probe = HealthProbe(settings, sender)
        probe.check()
        probe.check()

    assert probe.consecutive_failures == 2
    assert probe.is_alerted is True


def test_stats_property(settings, sender):
    with patch("httpx.get", return_value=_ok_response()):
        probe = HealthProbe(settings, sender)
        probe.check()

    stats = probe.stats
    assert stats["total_checks"] == 1
    assert stats["total_failures"] == 0
    assert stats["consecutive_failures"] == 0
