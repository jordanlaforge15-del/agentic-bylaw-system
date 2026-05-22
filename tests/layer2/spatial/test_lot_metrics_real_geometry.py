"""Real-geometry regression tests for ``compute_lot_metrics``.

Pins the algorithm's behavior on the three reference addresses ABS-23
called out, using parcel + nearby-centerline GeoJSON dumped from prod
on 2026-05-19 (see ``fixtures/abs23_prod_geometry.json`` for provenance).

These complement the synthetic-fixture tests in ``test_lot_metrics.py``:
synthetic tests cover algorithm edges (corner detection, depth omission,
city-block suppression) on clean geometry; this file pins the real-data
behavior so a regression on the underlying buffer width, street grouping,
or city-block-detection threshold trips a unit test and not a prod sanity
check after deploy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer2.spatial.lot_metrics import compute_lot_metrics

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "abs23_prod_geometry.json"
)


def _load_address(label: str) -> dict:
    data = json.loads(_FIXTURE.read_text())
    for entry in data:
        if entry["address"] == label:
            return entry
    raise KeyError(label)


def _run(entry: dict):
    centerlines = [c["geometry"] for c in entry["centerlines"]]
    names = [c.get("street_name") for c in entry["centerlines"]]
    return compute_lot_metrics(
        entry["parcel"]["geometry"],
        centerlines,
        centerline_names=names,
    )


def test_6321_quinpool_corner_lot_two_streets() -> None:
    """6321 Quinpool, Halifax — corner of Quinpool × Harvard.

    HRM ground truth: 32.7 m Quinpool + 35 m Harvard frontage,
    1,102 m² area, corner=true. Before ABS-23 prod returned 16.75 m
    frontage and corner=false because the 8 m buffer didn't reach
    Quinpool's centerline (9.5 m away). After: street-name grouping
    of Harvard (7.8 m) + Quinpool (9.5 / 12.8 m) with the 12 m buffer
    gives corner=True, frontage ≈ 74 m, depth omitted.
    """
    entry = _load_address("6321 Quinpool Road")
    metrics = _run(entry)

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(1093.1, abs=1.0)
    assert metrics.perimeter_m == pytest.approx(132.8, abs=0.5)
    # ~67.7 m of true frontage + small artifact from perpendicular edges
    # entering the buffer = ~74 m. Tolerance accommodates projection /
    # ingest precision.
    assert metrics.frontage_m == pytest.approx(74.0, abs=2.0)
    assert metrics.street_count == 2
    assert metrics.corner is True
    # Multi-frontage parcel — depth must NOT be reported.
    assert metrics.depth_m is None


def test_1505_barrington_city_block_suppressed() -> None:
    """1505 Barrington (PID 00076232, 5,347 m² polygon).

    The geocoder lands on a parcel that's bounded by 5 streets within
    the buffer (Hollis / Salter / Granville / Barrington / Spring
    Garden) — a city block, not the 3-sided triangle the user-visible
    address would suggest. ABS-23 algorithm correctly suppresses
    frontage / depth / corner and flags the result uncertain so the
    chat layer hedges. The PID-disambiguation question (HRM's
    address-search returns a different 7,785 m² PID) is tracked
    separately.
    """
    entry = _load_address("1505 Barrington Street")
    metrics = _run(entry)

    assert metrics.status == "uncertain"
    assert metrics.area_m2 == pytest.approx(5347.7, abs=1.0)
    assert metrics.perimeter_m == pytest.approx(362.9, abs=0.5)
    assert metrics.street_count is not None and metrics.street_count >= 4
    assert metrics.frontage_m is None
    assert metrics.depth_m is None
    assert metrics.corner is None
    assert metrics.reason is not None and "city block" in metrics.reason


def test_5251_duke_city_block_suppressed() -> None:
    """5251 Duke, Halifax — full city block (36,643 m² institutional).

    Multiple streets surround the block (Barrington / Argyle / Duke /
    Albemarle / Cogswell / Reconciliation). The street-count rule and
    the frontage/perimeter > 0.75 rule both flag city-block
    overreach. Frontage / depth / corner are suppressed; the chat
    layer surfaces area only.
    """
    entry = _load_address("5251 Duke Street")
    metrics = _run(entry)

    assert metrics.status == "uncertain"
    assert metrics.area_m2 == pytest.approx(36646.8, abs=2.0)
    assert metrics.perimeter_m == pytest.approx(787.7, abs=0.5)
    assert metrics.street_count is not None and metrics.street_count >= 4
    assert metrics.frontage_m is None
    assert metrics.depth_m is None
    assert metrics.corner is None
