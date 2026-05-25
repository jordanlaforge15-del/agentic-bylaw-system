"""Unit tests for SLO latency tracking."""
from __future__ import annotations

from advisor.monitoring.slo import LatencyTracker, SLOTargets


def test_empty_tracker_reports_full_compliance():
    tracker = LatencyTracker(window_seconds=60)
    targets = SLOTargets()
    snap = tracker.snapshot(targets)

    assert snap["total_requests"] == 0
    assert snap["availability_pct"] == 100.0
    assert snap["slo_met"]["availability"] is True
    assert snap["slo_met"]["chat_latency_p95"] is True


def test_records_requests_and_computes_availability():
    tracker = LatencyTracker(window_seconds=3600)
    targets = SLOTargets(availability_pct=99.5)

    for _ in range(99):
        tracker.record("/healthz", 0.01, 200)
    tracker.record("/healthz", 0.05, 500)

    snap = tracker.snapshot(targets)
    assert snap["total_requests"] == 100
    assert snap["successful_requests"] == 99
    assert snap["availability_pct"] == 99.0
    assert snap["slo_met"]["availability"] is False


def test_chat_latency_p95():
    tracker = LatencyTracker(window_seconds=3600)
    targets = SLOTargets(chat_latency_p95_seconds=10.0)

    for i in range(20):
        tracker.record("/v1/chat", float(i), 200)

    snap = tracker.snapshot(targets)
    assert snap["chat_requests"] == 20
    assert snap["chat_latency_p95_seconds"] >= 18.0
    assert snap["slo_met"]["chat_latency_p95"] is False


def test_chat_latency_p95_within_target():
    tracker = LatencyTracker(window_seconds=3600)
    targets = SLOTargets(chat_latency_p95_seconds=10.0)

    for _ in range(100):
        tracker.record("/v1/chat", 2.0, 200)

    snap = tracker.snapshot(targets)
    assert snap["chat_latency_p95_seconds"] == 2.0
    assert snap["slo_met"]["chat_latency_p95"] is True


def test_non_chat_requests_excluded_from_chat_latency():
    tracker = LatencyTracker(window_seconds=3600)
    targets = SLOTargets()

    tracker.record("/healthz", 0.001, 200)
    tracker.record("/readyz", 0.002, 200)

    snap = tracker.snapshot(targets)
    assert snap["total_requests"] == 2
    assert snap["chat_requests"] == 0
    assert snap["chat_latency_p95_seconds"] == 0.0
