"""Unit tests for advisor.monitoring.state (FailureTracker)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from advisor.monitoring.probe import ProbeResult
from advisor.monitoring.state import FailureTracker


def _result(healthy: bool) -> ProbeResult:
    return ProbeResult(
        timestamp=datetime.now(tz=timezone.utc),
        url="http://localhost/healthz",
        healthy=healthy,
        http_status=200 if healthy else 503,
        response_time_ms=5.0,
    )


class TestFailureTracker:
    def test_no_alert_on_single_failure(self):
        on_alert = MagicMock()
        tracker = FailureTracker(failure_threshold=2, on_alert=on_alert)
        tracker.record(_result(False))
        on_alert.assert_not_called()

    def test_alert_fires_at_threshold(self):
        on_alert = MagicMock()
        tracker = FailureTracker(failure_threshold=2, on_alert=on_alert)
        tracker.record(_result(False))
        tracker.record(_result(False))
        on_alert.assert_called_once()
        call_args = on_alert.call_args
        assert call_args[0][1] == 2  # consecutive_failures passed to callback

    def test_alert_fires_only_once_per_outage(self):
        on_alert = MagicMock()
        tracker = FailureTracker(failure_threshold=2, on_alert=on_alert)
        for _ in range(5):
            tracker.record(_result(False))
        on_alert.assert_called_once()

    def test_counter_resets_on_healthy(self):
        tracker = FailureTracker(failure_threshold=2)
        tracker.record(_result(False))
        tracker.record(_result(True))
        assert tracker.consecutive_failures == 0

    def test_alert_active_resets_on_healthy(self):
        on_alert = MagicMock()
        tracker = FailureTracker(failure_threshold=2, on_alert=on_alert)
        tracker.record(_result(False))
        tracker.record(_result(False))
        assert tracker.alert_active is True
        tracker.record(_result(True))
        assert tracker.alert_active is False

    def test_alert_refires_after_recovery(self):
        on_alert = MagicMock()
        tracker = FailureTracker(failure_threshold=2, on_alert=on_alert)
        # First outage
        tracker.record(_result(False))
        tracker.record(_result(False))
        # Recovery
        tracker.record(_result(True))
        # Second outage
        tracker.record(_result(False))
        tracker.record(_result(False))
        assert on_alert.call_count == 2

    def test_threshold_of_1(self):
        on_alert = MagicMock()
        tracker = FailureTracker(failure_threshold=1, on_alert=on_alert)
        tracker.record(_result(False))
        on_alert.assert_called_once()

    def test_last_result_stored(self):
        tracker = FailureTracker()
        r = _result(True)
        tracker.record(r)
        assert tracker.last_result is r
