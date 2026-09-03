"""ABS-466 — the address-resolution quality vocabulary.

One classifier serves the geocoder, the cache round-trip, the
``AddressProfile`` DTO and the advisor's answer-time qualifier, so a
resolution can't be "interpolated" in one layer and "fine" in the next.
"""
from __future__ import annotations

import pytest

from layer2.retrieval.resolution_quality import (
    classify_resolution,
    is_precise,
    resolution_caveat,
)


@pytest.mark.parametrize(
    "location_type,expected",
    [
        ("ROOFTOP", "rooftop"),
        ("RANGE_INTERPOLATED", "interpolated"),
        ("GEOMETRIC_CENTER", "centroid"),
        ("APPROXIMATE", "approximate"),
        ("rooftop", "rooftop"),  # case-insensitive
        ("SOMETHING_NEW", "unknown"),
    ],
)
def test_location_type_is_authoritative(location_type, expected):
    """Google's own word wins, and it wins over a contradicting float — a
    0.95 tagged RANGE_INTERPOLATED is still an estimate."""
    assert classify_resolution(location_type, 0.95) == expected
    assert classify_resolution(location_type, None) == expected


@pytest.mark.parametrize(
    "confidence,expected",
    [
        (1.0, "rooftop"),
        (0.95, "rooftop"),
        (0.9, "interpolated"),
        (0.85, "interpolated"),
        (0.7, "centroid"),
        (0.6, "centroid"),
        (0.4, "approximate"),
        (0.0, "unknown"),
    ],
)
def test_confidence_fallback_for_typeless_rows(confidence, expected):
    """In-database resolutions and pre-ABS-466 cache rows have no type."""
    assert classify_resolution(None, confidence) == expected


def test_missing_signal_is_unknown_not_rooftop():
    """An absent signal must never read as the strongest one."""
    assert classify_resolution(None, None) == "unknown"
    assert not is_precise(classify_resolution(None, None))
    assert resolution_caveat("unknown") is not None


def test_only_rooftop_needs_no_caveat():
    assert resolution_caveat("rooftop") is None
    for quality in ("interpolated", "centroid", "approximate", "unknown"):
        caveat = resolution_caveat(quality)
        assert caveat and "HRM" in caveat
