"""Unit tests for ``layer2.spatial.lot_metrics.compute_lot_metrics``.

Fixtures are constructed at Halifax latitudes (44.65°N, -63.6°E) so the
equirectangular projection inside ``compute_lot_metrics`` exercises the
non-equator cosine correction the production code applies. A helper
``rect`` builds a metres-sized rectangle and converts to EPSG:4326 with
the inverse of that projection — i.e. dimensions in metres round-trip
within the projection's own precision.
"""
from __future__ import annotations

import math

import pytest

from layer2.spatial.lot_metrics import compute_lot_metrics


# Halifax-ish anchor for all test fixtures.
_HALIFAX_LON = -63.6
_HALIFAX_LAT = 44.65

# Match the projection in ``lot_metrics._make_equirectangular_projector``.
_M_PER_DEG_LAT = 111_320.0


def _m_per_deg_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def rect(
    *,
    width_m: float,
    height_m: float,
    centre_lon: float = _HALIFAX_LON,
    centre_lat: float = _HALIFAX_LAT,
) -> dict:
    """Build a GeoJSON Polygon of given metre dimensions, axis-aligned."""
    dlon = (width_m / _m_per_deg_lon(centre_lat)) / 2
    dlat = (height_m / _M_PER_DEG_LAT) / 2
    coords = [
        [centre_lon - dlon, centre_lat - dlat],
        [centre_lon + dlon, centre_lat - dlat],
        [centre_lon + dlon, centre_lat + dlat],
        [centre_lon - dlon, centre_lat + dlat],
        [centre_lon - dlon, centre_lat - dlat],
    ]
    return {"type": "Polygon", "coordinates": [coords]}


def offset_rect(
    *,
    width_m: float,
    height_m: float,
    offset_east_m: float = 0.0,
    offset_north_m: float = 0.0,
    centre_lon: float = _HALIFAX_LON,
    centre_lat: float = _HALIFAX_LAT,
) -> dict:
    """Build a rect translated by (offset_east_m, offset_north_m)."""
    new_lon = centre_lon + offset_east_m / _m_per_deg_lon(centre_lat)
    new_lat = centre_lat + offset_north_m / _M_PER_DEG_LAT
    return rect(
        width_m=width_m,
        height_m=height_m,
        centre_lon=new_lon,
        centre_lat=new_lat,
    )


# ---------------------------------------------------------------------------
# Pure-geometry tests
# ---------------------------------------------------------------------------


def test_square_no_neighbours_classifies_full_perimeter_as_frontage() -> None:
    parcel = rect(width_m=20.0, height_m=20.0)
    metrics = compute_lot_metrics(parcel, [])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)
    assert metrics.perimeter_m == pytest.approx(80.0, rel=1e-3)
    # No neighbours → entire perimeter is frontage.
    assert metrics.frontage_m == pytest.approx(80.0, rel=1e-3)
    # Depth = area / frontage on a perfect square fronts-all-sides.
    assert metrics.depth_m == pytest.approx(5.0, rel=1e-3)
    # Single connected non-shared boundary → not a corner lot.
    assert metrics.corner is False
    # No-neighbour fallback caps confidence at 0.6.
    assert metrics.confidence == pytest.approx(0.6, abs=1e-6)
    assert metrics.multi_unit is None


def test_square_three_neighbours_leaves_one_side_as_frontage() -> None:
    # Anchor: 20×20 m square at Halifax.
    # Neighbours: 20×20 m squares to the north, east, and south
    # (sharing the top, right, and bottom edges). The west edge is
    # the road frontage.
    parcel = rect(width_m=20.0, height_m=20.0)
    north = offset_rect(width_m=20.0, height_m=20.0, offset_north_m=20.0)
    east = offset_rect(width_m=20.0, height_m=20.0, offset_east_m=20.0)
    south = offset_rect(width_m=20.0, height_m=20.0, offset_north_m=-20.0)

    metrics = compute_lot_metrics(parcel, [north, east, south])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)
    # Three sides shared → one 20 m side remains as frontage.
    assert metrics.frontage_m == pytest.approx(20.0, abs=1.0)
    # Depth = area / frontage ≈ 20 m for a square fronting one side.
    assert metrics.depth_m == pytest.approx(20.0, rel=0.1)
    # Frontage is one connected segment → not a corner lot.
    assert metrics.corner is False
    # Clean tessellation with three neighbours → high confidence.
    assert metrics.confidence > 0.9


def test_corner_lot_detects_two_distinct_frontage_components() -> None:
    # Anchor: 20×20 m square. Neighbours only on the north and east
    # — south and west edges are road-facing on two different streets.
    parcel = rect(width_m=20.0, height_m=20.0)
    north = offset_rect(width_m=20.0, height_m=20.0, offset_north_m=20.0)
    east = offset_rect(width_m=20.0, height_m=20.0, offset_east_m=20.0)

    metrics = compute_lot_metrics(parcel, [north, east])

    assert metrics.status == "ok"
    # Two adjacent edges are frontage → nominal 40 m total. The
    # 0.5 m shared-edge buffer eats ~1 m at the touching-corner
    # where the two neighbours meet, so accept ~38–40 m.
    assert metrics.frontage_m == pytest.approx(39.0, abs=1.5)
    # Two connected components with different bearings → corner.
    assert metrics.corner is True


def test_invalid_geometry_returns_unresolved_without_raising() -> None:
    # Self-intersecting "bowtie" polygon. shapely flags it invalid;
    # ``compute_lot_metrics`` must return ``unresolved`` rather than
    # crash the case-open path.
    bowtie = {
        "type": "Polygon",
        "coordinates": [
            [
                [-63.60, 44.65],
                [-63.59, 44.66],
                [-63.60, 44.66],
                [-63.59, 44.65],
                [-63.60, 44.65],
            ]
        ],
    }
    metrics = compute_lot_metrics(bowtie, [])

    assert metrics.status == "unresolved"
    assert metrics.area_m2 is None
    assert metrics.frontage_m is None
    assert metrics.reason is not None


def test_empty_input_returns_unresolved() -> None:
    metrics = compute_lot_metrics({}, [])
    assert metrics.status == "unresolved"
    assert metrics.reason is not None


def test_non_polygon_input_returns_unresolved() -> None:
    point = {"type": "Point", "coordinates": [-63.6, 44.65]}
    metrics = compute_lot_metrics(point, [])
    assert metrics.status == "unresolved"
    assert "Polygon" in (metrics.reason or "")


def test_multipolygon_uses_largest_piece() -> None:
    # MultiPolygon with one big 20×20 m piece and one 2×2 m sliver.
    big = rect(width_m=20.0, height_m=20.0)
    sliver = offset_rect(
        width_m=2.0, height_m=2.0, offset_east_m=500.0
    )
    multipoly = {
        "type": "MultiPolygon",
        "coordinates": [big["coordinates"], sliver["coordinates"]],
    }
    metrics = compute_lot_metrics(multipoly, [])

    assert metrics.status == "ok"
    # Should report the big polygon's area, not the sliver's.
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)


def test_to_dict_omits_none_fields_and_rounds() -> None:
    parcel = rect(width_m=20.0, height_m=20.0)
    metrics = compute_lot_metrics(parcel, [])
    payload = metrics.to_dict()

    assert payload["status"] == "ok"
    assert payload["method"] == "shared_edge"
    assert "area_m2" in payload
    assert "frontage_m" in payload
    # No multi_unit detected at this layer; field omitted, not asserted as None.
    assert "multi_unit" not in payload
    # Area is rounded to one decimal.
    assert isinstance(payload["area_m2"], float)
    assert payload["area_m2"] == round(payload["area_m2"], 1)
