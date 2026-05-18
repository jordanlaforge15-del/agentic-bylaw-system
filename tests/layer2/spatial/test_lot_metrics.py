"""Unit tests for ``layer2.spatial.lot_metrics.compute_lot_metrics``.

Fixtures are constructed at Halifax latitudes (44.65°N, -63.6°E) so the
equirectangular projection inside ``compute_lot_metrics`` exercises the
non-equator cosine correction the production code applies. A helper
``rect`` builds a metres-sized rectangle and converts to EPSG:4326 with
the inverse of that projection — i.e. dimensions in metres round-trip
within the projection's own precision. ``line_between`` builds a
LineString in the same metres-to-lon/lat space so synthetic centerlines
can be positioned exactly relative to a synthetic parcel.

The three named real-world fixtures the ABS-7 ticket calls out
(6321 Quinpool, 1505 Barrington, 5251 Duke) are represented here by
three synthetic scenarios that exercise their characteristic shapes:
mid-block residential (Quinpool), mid-block commercial (Barrington),
and large corner lot (Duke). Real-address verification against HRM's
mapping tool happens manually post-deploy — see the issue's verification
checklist.
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


def test_mid_block_residential_lot_quinpool_style() -> None:
    """A typical Quinpool residential lot — one street, ~15 m frontage.

    Parcel: 15 m wide × 30 m deep, with the front (south) edge sitting
    on the street centerline (HRM tessellation pattern, worst case for
    the perpendicular-edge artifact). One centerline runs east-west
    along y=0; buffer is the default 8 m.

    The ``ST_Length(ST_Intersection(parcel_boundary, buffer))`` formula
    counts the south edge (15 m) PLUS the first ~buffer_m of each of
    the two perpendicular side edges (where they cross the buffer near
    the parcel's road-facing corners). Total: 15 + 8 + 8 = 31 m. For
    realistic Halifax parcels set back ~5 m from the centerline, the
    artifact shrinks to ~2 × (buffer_m − setback) and frontage tracks
    closer to the true edge length.
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=15.0, height_m=30.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(450.0, rel=1e-3)
    assert metrics.perimeter_m == pytest.approx(90.0, rel=1e-3)
    # 15 m south edge + 2 × 8 m perpendicular-edge artifact ≈ 31 m.
    assert metrics.frontage_m == pytest.approx(31.0, abs=0.5)
    # depth = area / frontage = 450 / 31 ≈ 14.5 m.
    assert metrics.depth_m == pytest.approx(14.5, abs=0.5)
    # Corner detection filters segments < 1.1 × buffer_m (= 8.8 m), so
    # the two 8 m perpendicular artifacts don't count as a second bearing.
    assert metrics.corner is False
    assert metrics.method == "centerline_buffer"
    assert metrics.confidence == pytest.approx(1.0)


def test_mid_block_commercial_lot_barrington_style() -> None:
    """A larger Barrington commercial lot — wider frontage, deeper lot."""
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=40.0, height_m=60.0)
    centerline = line_between((-100.0, 0.0), (100.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(2400.0, rel=1e-3)
    # 40 m south edge + 2 × 8 m artifact = 56 m.
    assert metrics.frontage_m == pytest.approx(56.0, abs=0.5)
    # 40 m east-west edge passes the 8.8 m artifact filter; the 8 m
    # perpendicular bits don't. Only one bearing → not a corner.
    assert metrics.corner is False


def test_corner_lot_duke_style_detects_two_streets() -> None:
    """A large Duke-style corner lot — fronts on two perpendicular streets.

    Parcel: 30 m × 40 m at the corner of two streets meeting at the
    lot's SE corner (origin). South centerline runs along y=0; east
    centerline runs along x=30.
    """
    parcel = rect_at(x_m=0.0, y_m=0.0, width_m=30.0, height_m=40.0)
    south_st = line_between((-50.0, 0.0), (80.0, 0.0))
    east_st = line_between((30.0, -50.0), (30.0, 90.0))

    metrics = compute_lot_metrics(parcel, [south_st, east_st])

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(1200.0, rel=1e-3)
    # South edge (30 m) + east edge (40 m) + 2 × 8 m artifacts on the
    # north / west edges = 86 m.
    assert metrics.frontage_m == pytest.approx(86.0, abs=1.0)
    # Two long perpendicular bearings present (30 m horizontal and 40 m
    # vertical, both above the 8.8 m artifact filter) → corner lot.
    assert metrics.corner is True


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


def test_buffer_too_small_for_setback_parcel_misses_frontage() -> None:
    """Documents the buffer-tuning risk.

    Parcel set back 12 m from the centerline (e.g. a wide rural ROW).
    With buffer_m=8 the buffer doesn't reach the parcel and frontage is 0.
    """
    parcel = rect_at(x_m=0.0, y_m=12.0, width_m=20.0, height_m=20.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline], buffer_m=8.0)

    assert metrics.status == "ok"
    assert metrics.area_m2 == pytest.approx(400.0, rel=1e-3)
    assert metrics.frontage_m == 0.0
    assert metrics.depth_m is None
    assert metrics.corner is False


def test_larger_buffer_catches_setback_parcel() -> None:
    """Same setback parcel, but buffer_m=15 reaches the front edge.

    Buffer extends from y=-15 to y=15. Parcel south edge at y=12 is
    fully inside (full 20 m of edge). Each perpendicular edge enters
    the buffer for the 3 m between y=12 and y=15, so the artifact is
    only 2 × 3 = 6 m. Total frontage ≈ 26 m.
    """
    parcel = rect_at(x_m=0.0, y_m=12.0, width_m=20.0, height_m=20.0)
    centerline = line_between((-50.0, 0.0), (50.0, 0.0))

    metrics = compute_lot_metrics(parcel, [centerline], buffer_m=15.0)

    assert metrics.status == "ok"
    assert metrics.frontage_m == pytest.approx(26.0, abs=0.5)


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

    # 20 m south edge + 2 × 8 m artifact = 36 m. (Same algorithm as the
    # mid-block LineString case; this test just confirms MultiLineString
    # decomposition.)
    assert metrics.frontage_m == pytest.approx(36.0, abs=0.5)
    assert metrics.corner is False


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
    # No multi_unit detected at this layer; field omitted, not asserted as None.
    assert "multi_unit" not in payload
    # Area is rounded to one decimal.
    assert isinstance(payload["area_m2"], float)
    assert payload["area_m2"] == round(payload["area_m2"], 1)
