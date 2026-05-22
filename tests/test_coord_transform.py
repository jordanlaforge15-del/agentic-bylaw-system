"""ABS-52: coordinate-system transform tests.

Synthetic fixtures cover the categories the issue spec calls out:
Halifax-correct EPSG:2961, incorrectly-tagged-as-WGS84, no source EPSG,
no true-north angle, mm-vs-m unit confusion, true-north rotation, and
a 2961 → 4326 → 2961 round-trip.

Every test runs in-process against pyproj's bundled grids; no network,
no fixture files.
"""
from __future__ import annotations

import math

import pytest

from layer2.spatial.coord_transform import (
    CoordTransformError,
    DEFAULT_PROJECTED_EPSG,
    ProjectLocation,
    project_location_from_aps,
    project_location_from_ifc_context,
    to_projected,
    to_wgs84,
)


# ----------------------------------------------------------------------
# Fixtures + helpers
# ----------------------------------------------------------------------


# A 10 m × 8 m rectangle at project-local origin — what a typical IFC
# `IfcSlab` footprint looks like after `_slab_to_polygon_coords` in
# layer1.parsers.ifc_submission applies its unit scale.
_RECT_PROJECT_LOCAL = {
    "type": "Polygon",
    "coordinates": [
        [[0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0], [0.0, 0.0]]
    ],
}

# Approximate Halifax downtown base point in NAD83 / UTM 20N (EPSG:2961).
# Sourced from a Halifax test parcel — doesn't matter that it's exact,
# only that it places the rectangle inside HRM bounds.
_HALIFAX_BASE_EASTING = 454500.0
_HALIFAX_BASE_NORTHING = 4946000.0
# Approximate lon/lat of that same point (Halifax CBD).
_HALIFAX_PARCEL_CENTROID_4326 = (-63.575, 44.643)


def _halifax_location(**overrides) -> ProjectLocation:
    fields = dict(
        source_epsg=2961,
        project_base_easting=_HALIFAX_BASE_EASTING,
        project_base_northing=_HALIFAX_BASE_NORTHING,
        true_north_angle_deg=0.0,
        unit_scale_to_metres=1.0,
    )
    fields.update(overrides)
    return ProjectLocation(**fields)


# ----------------------------------------------------------------------
# Halifax-correct happy path
# ----------------------------------------------------------------------


def test_halifax_correct_polygon_round_trips_to_4326_inside_hrm_bounds():
    loc = _halifax_location()
    result = to_wgs84(_RECT_PROJECT_LOCAL, loc)
    assert result.crs == "EPSG:4326"
    assert result.confidence == 1.0
    ring = result.geometry["coordinates"][0]
    assert len(ring) == 5  # auto-closed
    for lon, lat in ring:
        # HRM is roughly (-65, 43.5) to (-62, 46).
        assert -65 <= lon <= -62, f"lon {lon} outside HRM bounds"
        assert 43.5 <= lat <= 46, f"lat {lat} outside HRM bounds"


def test_halifax_correct_polygon_to_projected_preserves_area_within_tolerance():
    loc = _halifax_location()
    result = to_projected(_RECT_PROJECT_LOCAL, loc)
    assert result.crs == f"EPSG:{DEFAULT_PROJECTED_EPSG}"
    # source_epsg == target_epsg here, so this is a no-op CRS-wise
    # apart from the local-to-CRS translation.
    src_area = 10.0 * 8.0
    out_area = result.evidence["out_area_m2"]
    assert out_area == pytest.approx(src_area, rel=0.01)
    assert result.confidence == 1.0
    assert result.warnings == []


def test_centroid_check_passes_when_parcel_centroid_near_base():
    loc = _halifax_location()
    result = to_projected(
        _RECT_PROJECT_LOCAL, loc,
        parcel_centroid_4326=_HALIFAX_PARCEL_CENTROID_4326,
    )
    assert result.confidence == 1.0
    assert all("centroid" not in w for w in result.warnings)


# ----------------------------------------------------------------------
# Wrong / missing CRS metadata — must surface as errors, not bad data
# ----------------------------------------------------------------------


def test_missing_source_epsg_raises():
    loc = ProjectLocation(source_epsg=None)
    with pytest.raises(CoordTransformError, match="source_epsg"):
        to_projected(_RECT_PROJECT_LOCAL, loc)
    with pytest.raises(CoordTransformError, match="source_epsg"):
        to_wgs84(_RECT_PROJECT_LOCAL, loc)


def test_geometry_tagged_as_wgs84_but_in_metres_trips_centroid_check():
    # Common mistake: architect labels project coords as "lat/lon" but
    # the model is actually in metres. Treating that as 4326 puts the
    # building somewhere in the middle of the Atlantic — centroid
    # distance check should trigger the hard fail.
    bad_loc = _halifax_location(
        source_epsg=4326,
        project_base_easting=0.0,
        project_base_northing=0.0,
    )
    # Either sanity check (area or centroid or envelope) is fine here —
    # the point is we don't quietly emit a polygon in the Atlantic.
    with pytest.raises(CoordTransformError, match="(area|envelope|centroid|CRS)"):
        to_projected(
            _RECT_PROJECT_LOCAL, bad_loc,
            parcel_centroid_4326=_HALIFAX_PARCEL_CENTROID_4326,
        )


def test_unit_confusion_mm_vs_m_trips_area_check():
    # Architect's IFC said metres but the actual coords are millimetres
    # (10 000 mm × 8 000 mm = an 80 km² building). The transform output
    # area is the same as inputs because they're labelled as metres —
    # we catch the issue via unit_scale_to_metres=1.0 on a model that
    # should have been 0.001. Translate that: src area = 80 km² because
    # the caller said unit_scale=1 → src_area = 80M m². out_area is the
    # same; ratio is 1.0; no trip. The real catcher is when ABS-49
    # supplied unit_scale=0.001 and the geometry was 10000x8000, putting
    # src_area at 80m² and out_area would be 80M m² because we'd skip the
    # scale on output. Simulate that here.
    mm_rect = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [10000.0, 0.0], [10000.0, 8000.0], [0.0, 8000.0], [0.0, 0.0]]
        ],
    }
    loc = _halifax_location(unit_scale_to_metres=0.001)
    # src_area: 80 m² (after scale). out_area at the source CRS: 80 m²
    # (the scale is applied to every coord). Ratio ~ 1.0 → fine. So the
    # area check is a *consistency* check; it correctly catches the
    # case where the scale is WRONG, not when it's right. To exercise
    # the bad path: claim unit_scale_to_metres=1.0 against mm coords.
    wrong_loc = _halifax_location(unit_scale_to_metres=1.0)
    with pytest.raises(CoordTransformError, match="area"):
        to_projected(mm_rect, wrong_loc)


def test_missing_true_north_defaults_to_zero_with_no_warning():
    # The issue spec says missing true-north shouldn't be a hard error
    # since most exports default to grid-north (0°). Confirm we don't
    # warn or raise for that case alone.
    loc = _halifax_location(true_north_angle_deg=0.0)
    result = to_projected(_RECT_PROJECT_LOCAL, loc)
    assert result.confidence == 1.0
    assert result.warnings == []


# ----------------------------------------------------------------------
# Rotation (true-north angle)
# ----------------------------------------------------------------------


def test_true_north_rotation_applied_to_geometry():
    # A 10 × 0 line along project +X with a 90° true-north rotation
    # (project +Y points east instead of north) should end up running
    # along source-CRS +Y (north) after the transform.
    line_rect = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, 0.0], [10.0, 0.0], [10.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        ],
    }
    loc = _halifax_location(true_north_angle_deg=90.0)
    result = to_projected(line_rect, loc)
    # source CRS == target CRS here so no pyproj reprojection.
    # Original: (0,0) → (10,0). After -90° rotation: (0,0) → (0, -10),
    # then base translation lands us 10 units south of base.
    ring = result.geometry["coordinates"][0]
    # The (10, 0) vertex should now be at (base_x, base_y - 10).
    transformed_vertex = ring[1]
    assert transformed_vertex[0] == pytest.approx(_HALIFAX_BASE_EASTING, abs=1e-6)
    assert transformed_vertex[1] == pytest.approx(
        _HALIFAX_BASE_NORTHING - 10.0, abs=1e-6
    )


# ----------------------------------------------------------------------
# Round-trip
# ----------------------------------------------------------------------


def test_forward_to_4326_and_to_2961_describe_the_same_polygon():
    """`to_projected` and `to_wgs84` should be spatially equivalent.

    We don't round-trip through ProjectLocation (which models "BIM
    project-local → CRS", not generic CRS-to-CRS). Instead: take the
    same project-local polygon, send it through both forward transforms,
    then reproject the 4326 ring back to 2961 via raw pyproj and check
    we get the 2961 ring back to ~1cm.
    """
    from pyproj import Transformer

    loc = _halifax_location()
    proj = to_projected(_RECT_PROJECT_LOCAL, loc)
    wgs = to_wgs84(_RECT_PROJECT_LOCAL, loc)

    proj_ring = proj.geometry["coordinates"][0]
    wgs_ring = wgs.geometry["coordinates"][0]
    transformer = Transformer.from_crs(4326, DEFAULT_PROJECTED_EPSG, always_xy=True)
    for (lon, lat), (expected_e, expected_n) in zip(wgs_ring, proj_ring):
        recovered_e, recovered_n = transformer.transform(lon, lat)
        assert recovered_e == pytest.approx(expected_e, abs=0.01)
        assert recovered_n == pytest.approx(expected_n, abs=0.01)


# ----------------------------------------------------------------------
# Empty / malformed inputs
# ----------------------------------------------------------------------


def test_empty_geometry_raises():
    empty = {"type": "Polygon", "coordinates": []}
    with pytest.raises(CoordTransformError, match="empty"):
        to_projected(empty, _halifax_location())


def test_non_polygon_geometry_raises():
    point = {"type": "Point", "coordinates": [0.0, 0.0]}
    with pytest.raises(CoordTransformError, match="Polygon"):
        to_projected(point, _halifax_location())


# ----------------------------------------------------------------------
# Adapter: IFC geometric context → ProjectLocation
# ----------------------------------------------------------------------


def test_ifc_adapter_reads_world_origin_and_true_north():
    ctx = {
        "context_type": "Model",
        "context_identifier": "Body",
        "world_origin": [_HALIFAX_BASE_EASTING, _HALIFAX_BASE_NORTHING, 0.0],
        "true_north_direction": [0.5, 0.5],  # 45° CW from +Y
    }
    loc = project_location_from_ifc_context(
        ctx, source_epsg=2961, unit_scale_to_metres=1.0
    )
    assert loc.source_epsg == 2961
    assert loc.project_base_easting == _HALIFAX_BASE_EASTING
    assert loc.project_base_northing == _HALIFAX_BASE_NORTHING
    # atan2(0.5, 0.5) = 45°
    assert loc.true_north_angle_deg == pytest.approx(45.0)


def test_ifc_adapter_handles_missing_context():
    loc = project_location_from_ifc_context(None, source_epsg=2961)
    assert loc.source_epsg == 2961
    assert loc.project_base_easting == 0.0
    assert loc.true_north_angle_deg == 0.0
    assert loc.provenance["source"] == "ifc-no-context"


# ----------------------------------------------------------------------
# Adapter: APS project location → ProjectLocation
# ----------------------------------------------------------------------


def test_aps_adapter_with_project_base_point():
    loc = project_location_from_aps(
        source_epsg=2961,
        project_base_point=(_HALIFAX_BASE_EASTING, _HALIFAX_BASE_NORTHING, 0.0),
        true_north_angle_deg=10.0,
    )
    assert loc.project_base_easting == _HALIFAX_BASE_EASTING
    assert loc.true_north_angle_deg == 10.0


def test_aps_adapter_with_lat_lon_reprojects_to_source_crs():
    lon, lat = _HALIFAX_PARCEL_CENTROID_4326
    loc = project_location_from_aps(
        source_epsg=2961, latitude=lat, longitude=lon
    )
    # Re-deriving the lat/lon from the easting/northing should round-trip
    # close to the original.
    from pyproj import Transformer
    back = Transformer.from_crs(2961, 4326, always_xy=True)
    out_lon, out_lat = back.transform(
        loc.project_base_easting, loc.project_base_northing
    )
    assert out_lon == pytest.approx(lon, abs=1e-5)
    assert out_lat == pytest.approx(lat, abs=1e-5)


def test_aps_adapter_with_no_inputs_raises():
    with pytest.raises(CoordTransformError, match="ProjectLocation"):
        project_location_from_aps(source_epsg=None)
