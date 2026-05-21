"""Unit tests for ``layer2.spatial.lot_metrics.compute_lot_metrics``.

Fixtures are constructed at Halifax latitudes (44.65°N, -63.6°E) so the
equirectangular projection inside ``compute_lot_metrics`` exercises the
non-equator cosine correction the production code applies. A helper
``rect`` builds a metres-sized rectangle and converts to EPSG:4326 with
the inverse of that projection — i.e. dimensions in metres round-trip
within the projection's own precision. ``line_between`` builds a
LineString in the same metres-to-lon/lat space so synthetic centerlines
can be positioned exactly relative to a synthetic parcel.

The named real-world fixtures the ABS-23 ticket calls out are exercised
separately in ``test_lot_metrics_real_geometry.py`` against GeoJSON
dumped from prod; the cases here cover the algorithm's edges in
controlled synthetic geometry.
"""
from __future__ import annotations

import math

import pytest

from layer2.spatial.lot_metrics import (
    DEFAULT_BUFFER_M,
    compute_lot_metrics,
)


# Halifax-ish anchor for all test fixtures.
_HALIFAX_LON = -63.6
_HALIFAX_LAT = 44.65

# Match the projection in ``lot_metrics._make_equirectangular_projector``.
_M_PER_DEG_LAT = 111_320.0


def _m_per_deg_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def _to_lonlat(
    x_m: float,
    y_m: float,
    *,
    centre_lon: float = _HALIFAX_LON,
    centre_lat: float = _HALIFAX_LAT,
) -> tuple[float, float]:
    """Inverse equirectangular at Halifax: metres → (lon, lat)."""
    return (
        centre_lon + x_m / _m_per_deg_lon(centre_lat),
        centre_lat + y_m / _M_PER_DEG_LAT,
    )


def rect_at(
    *,
    x_m: float,
    y_m: float,
    width_m: float,
    height_m: float,
) -> dict:
    """Build a GeoJSON Polygon at (x_m, y_m) of given metre dimensions."""
    p1 = _to_lonlat(x_m, y_m)
    p2 = _to_lonlat(x_m + width_m, y_m)
    p3 = _to_lonlat(x_m + width_m, y_m + height_m)
    p4 = _to_lonlat(x_m, y_m + height_m)
    return {"type": "Polygon", "coordinates": [[p1, p2, p3, p4, p1]]}


def line_between(
    a: tuple[float, float],
    b: tuple[float, float],
) -> dict:
    """Build a GeoJSON LineString from two (x_m, y_m) endpoints."""
    return {
        "type": "LineString",
        "coordinates": [_to_lonlat(*a), _to_lonlat(*b)],
    }


# ---------------------------------------------------------------------------
# Pure-geometry tests
# ---------------------------------------------------------------------------


def test_default_buffer_is_twelve_metres() -> None:
    """ABS-23: the default buffer was widened from 8 m to 12 m so it
    reaches the typical 7–15 m HRM road allowance. If this changes
    without intent, downstream tests and the documented behavior in
    ``compute_lot_metrics`` will drift."""
    assert DEFAULT_BUFFER_M == 12.0


def test_mid_block_residential_lot_one_street_one_frontage() -> None:
    """A typical residential lot fronting a single street.

    Parcel: 15 m wide × 30 m deep with the front (south) edge on the
    street centerline (HRM-tessellation worst case for the
    perpendicular-edge artifact). One centerline runs east-west along
    y=0; default buffer is 12 m.

    The ``ST_Length(ST_Intersection(parcel_boundary, buffer))`` formula
    counts the 15 m south edge PLUS the first ~buffer_m of each of the
    two perpendicular side edges where they cross the buffer near the
    parcel's road-facing corners. Total: 15 + 12 + 12 = 39 m. For
    realistic Halifax parcels set back ~5 m from the centerline the
    artifact shrinks to 2 × (buffer_m − setback) and frontage tracks
    closer to the true edge length.
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=15.0, height_m=30.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(450.0, rel=1e-3)
    assert metrics.perimeter_m == pytest.approx(90.0, rel=1e-3)
    # 15 m south edge + 2 × 12 m perpendicular-edge artifact ≈ 39 m.
    assert metrics.frontage_m == pytest.approx(39.0, abs=0.5)
    # Single-street lot → depth is meaningful: area / frontage = 450/39 ≈ 11.5 m.
    assert metrics.depth_m == pytest.approx(11.5, abs=0.5)
    assert metrics.corner is False
    assert metrics.street_count == 1
    assert metrics.method == "centerline_buffer"
    assert metrics.confidence == pytest.approx(1.0)


def test_corner_lot_two_streets_depth_omitted() -> None:
    """A corner lot fronting two perpendicular streets.

    Parcel: 30 m × 40 m at the corner of two streets meeting at the
    lot's SE corner (origin). South centerline runs along y=0; east
    centerline runs along x=30. Two centerlines (no street names →
    each is treated as a distinct "street") → corner=True, depth
    omitted (geometrically undefined for multi-frontage).
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=30.0, height_m=40.0)
    south_st = line_between((-50.0, 0.0), (80.0, 0.0))
    east_st = line_between((30.0, -50.0), (30.0, 90.0))

    metrics = compute_lot_metrics(parcel, [south_st, east_st])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(1200.0, rel=1e-3)
    # South (30 m) + east (40 m) main edges, plus ~12 m of each perpendicular
    # edge entering the buffer at the two non-corner corners → 94 m total.
    assert metrics.frontage_m == pytest.approx(94.0, abs=2.0)
    assert metrics.corner is True
    assert metrics.street_count == 2
    # ABS-23 fix: depth was area/frontage on corner lots (e.g. 65 m of
    # "depth" reported for 6321 Quinpool) — that's not a meaningful
    # number. The two-frontage case omits depth entirely.
    assert metrics.depth_m is None


def test_corner_lot_with_street_name_grouping_merges_segments() -> None:
    """Two centerline segments of the same street count as one street.

    HRM splits each road at every intersection — Quinpool alone may
    appear as 5+ segments adjacent to a single parcel. Without name
    grouping the algorithm would see ``street_count = 5`` for a
    mid-block lot and falsely flag it as city-block-like.

    Setup: 30 × 30 m parcel mid-block on "MAIN" (two segments of MAIN
    on either side of an intersection). One centerline along the
    south edge, second centerline continuing east of it.
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=30.0, height_m=30.0)
    main_west = line_between((-60.0, 0.0), (15.0, 0.0))
    main_east = line_between((15.0, 0.0), (90.0, 0.0))

    metrics = compute_lot_metrics(
        parcel,
        [main_west, main_east],
        centerline_names=["MAIN", "MAIN"],
    )

    assert metrics.status == "ok"
    assert metrics.street_count == 1
    assert metrics.corner is False
    # Depth is still computed because this is a single-street lot.
    assert metrics.depth_m is not None


def test_city_block_three_or_more_streets_suppresses_frontage() -> None:
    """4+ streets touching the parcel → city-block suppression.

    A 30 × 30 m parcel surrounded by 4 centerlines (one per side, all
    distinct streets). Buffer wraps all 4 sides → algorithm reports
    ``status="uncertain"`` with frontage / depth / corner suppressed.
    Mirrors the prod behavior for 5251 Duke (full city block).
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=30.0, height_m=30.0)
    south = line_between((-50.0, 0.0), (80.0, 0.0))
    east = line_between((30.0, -50.0), (30.0, 80.0))
    north = line_between((-50.0, 30.0), (80.0, 30.0))
    west = line_between((0.0, -50.0), (0.0, 80.0))

    metrics = compute_lot_metrics(
        parcel,
        [south, east, north, west],
        centerline_names=["SOUTH ST", "EAST ST", "NORTH ST", "WEST ST"],
    )

    assert metrics.status == "uncertain"
    assert metrics.street_count == 4
    assert metrics.frontage_m is None
    assert metrics.depth_m is None
    assert metrics.corner is None
    assert metrics.area_m2 == pytest.approx(900.0, rel=1e-3)
    assert metrics.perimeter_m == pytest.approx(120.0, rel=1e-3)
    assert metrics.reason is not None and "city block" in metrics.reason


def test_no_centerlines_reports_zero_frontage_with_area_intact() -> None:
    """When no centerlines are provided, area / perimeter are still computed.

    The extractor surfaces a 0.7-confidence area-only payload in this
    case (centerline dataset not ingested, or sparse rural region).
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=20.0, height_m=20.0)
    metrics = compute_lot_metrics(parcel, [])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)
    assert metrics.perimeter_m == pytest.approx(80.0, rel=1e-3)
    assert metrics.frontage_m == 0.0
    assert metrics.depth_m is None
    assert metrics.corner is False
    assert metrics.street_count == 0


def test_buffer_too_small_for_setback_parcel_misses_frontage() -> None:
    """Documents the buffer-tuning floor: parcel further from centerline
    than ``buffer_m`` returns zero frontage.

    Parcel set back 15 m from the centerline (e.g. a wide rural ROW).
    With buffer_m=8 the buffer doesn't reach the parcel and frontage is 0.
    """
    parcel = rect_at(x_m=0.0, y_m=15.0, width_m=20.0, height_m=20.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline], buffer_m=8.0)

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)
    assert metrics.frontage_m == 0.0
    assert metrics.depth_m is None
    assert metrics.corner is False
    assert metrics.street_count == 0


def test_larger_buffer_catches_setback_parcel() -> None:
    """Same setback parcel, but a wider buffer reaches the front edge.

    Buffer extends from y=-18 to y=18. Parcel south edge at y=15 is
    fully inside (full 20 m of edge). Each perpendicular edge enters
    the buffer for the 3 m between y=15 and y=18, so the artifact is
    only 2 × 3 = 6 m. Total frontage ≈ 26 m.
    """
    parcel = rect_at(x_m=0.0, y_m=15.0, width_m=20.0, height_m=20.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline], buffer_m=18.0)

    assert metrics.status == "ok"
    assert metrics.frontage_m == pytest.approx(26.0, abs=0.5)
    assert metrics.street_count == 1


def test_multilinestring_centerline_treated_as_segments() -> None:
    """A MultiLineString centerline is decomposed and unioned correctly."""
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=20.0, height_m=20.0)
    mls = {
        "type": "MultiLineString",
        "coordinates": [
            [
                _to_lonlat(-50.0, 0.0),
                _to_lonlat(50.0, 0.0),
            ]
        ],
    }
    metrics = compute_lot_metrics(parcel, [mls])

    # 20 m south edge + 2 × 12 m artifact = 44 m. (Same algorithm as the
    # single-LineString case; this test just confirms MultiLineString
    # decomposition.)
    assert metrics.frontage_m == pytest.approx(44.0, abs=0.5)
    assert metrics.corner is False
    assert metrics.street_count == 1


def test_centerline_names_length_mismatch_returns_unresolved() -> None:
    """A length-mismatched ``centerline_names`` is a programming error
    — surface it loudly rather than silently mis-grouping."""
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=20.0, height_m=20.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(
        parcel, [centerline], centerline_names=["MAIN", "EXTRA"]
    )
    assert metrics.status == "unresolved"
    assert "length" in (metrics.reason or "")


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
    big = rect_at(x_m=0.0, y_m=0.0, width_m=20.0, height_m=20.0)
    sliver = rect_at(x_m=500.0, y_m=0.0, width_m=2.0, height_m=2.0)
    multipoly = {
        "type": "MultiPolygon",
        "coordinates": [big["coordinates"], sliver["coordinates"]],
    }
    metrics = compute_lot_metrics(multipoly, [])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)


def test_to_dict_omits_none_fields_and_rounds() -> None:
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=20.0, height_m=20.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))
    metrics = compute_lot_metrics(parcel, [centerline])
    payload = metrics.to_dict()

    assert payload["status"] == "ok"
    assert payload["method"] == "centerline_buffer"
    assert "area_m2" in payload
    assert "frontage_m" in payload
    assert "depth_m" in payload
    assert payload["street_count"] == 1
    # No multi_unit detected at this layer; field omitted, not asserted as None.
    assert "multi_unit" not in payload
    # Area is rounded to one decimal.
    assert isinstance(payload["area_m2"], float)
    assert payload["area_m2"] == round(payload["area_m2"], 1)
