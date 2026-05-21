"""ABS-51 derived-attribute tests.

Each test passes `nearby_centerlines=` explicitly so the function
skips its DB query and we don't need to seed `external_dataset_feature`
rows for every case. The end-to-end test exercises the DB path via the
ABS-48 scaffold pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
)
from layer1.pipeline.ingest_submission import ingest_submission
from layer1.models.submission_schemas import SubmissionIngestConfig
from layer2.compliance.db.models import (
    Parcel,
    Submission,
    SubmissionAttributeSource,
    SubmissionSourceType,
)
from layer2.compliance.derived_attributes import (
    DEFAULT_JURISDICTION_EPSG_MAP,
    compute_derived_attributes,
)
from layer2.compliance.taxonomy import load_taxonomy


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


@dataclass
class _StubParcel:
    """In-memory Parcel stand-in for the geometric tests.

    Only the fields the function reads (geometry_geojson, centroid_geojson,
    area_m2, jurisdiction) need to be present — the real ORM has more.
    """

    geometry_geojson: dict[str, Any] | None
    area_m2: float | None
    jurisdiction: str | None
    centroid_geojson: dict[str, Any] | None = None


@dataclass
class _StubSubmission:
    """Minimal Submission stand-in carrying just the parcel reference."""

    parcel: _StubParcel | None


# Halifax-area base point in NAD83 / UTM 20N (EPSG:2961).
_HALIFAX_BASE_EASTING = 454500.0
_HALIFAX_BASE_NORTHING = 4946000.0


def _halifax_geometric_context() -> dict[str, Any]:
    """Geometric-context dict the IFC extractor (ABS-49) would have stashed."""
    return {
        "context_type": "Model",
        "context_identifier": "Body",
        "world_origin": [_HALIFAX_BASE_EASTING, _HALIFAX_BASE_NORTHING, 0.0],
        "true_north_direction": [0.0, 1.0],  # grid-aligned
    }


def _halifax_parcel_polygon_4326() -> dict[str, Any]:
    """A roughly 30 × 40 m HRM parcel in EPSG:4326 anchored on the base point.

    We build it in 2961, then reproject. Keeps the math debuggable —
    the parcel area in 2961 should land near 1200 m².
    """
    from pyproj import Transformer

    transformer = Transformer.from_crs(2961, 4326, always_xy=True)
    corners_2961 = [
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 15, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 15, _HALIFAX_BASE_NORTHING + 20),
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING + 20),
    ]
    corners_4326 = [
        list(transformer.transform(e, n)) for (e, n) in corners_2961
    ]
    corners_4326.append(corners_4326[0])
    return {"type": "Polygon", "coordinates": [corners_4326]}


def _halifax_road_centerline_4326() -> dict[str, Any]:
    """A road centerline running along the parcel's south edge in 4326."""
    from pyproj import Transformer

    transformer = Transformer.from_crs(2961, 4326, always_xy=True)
    line_2961 = [
        (_HALIFAX_BASE_EASTING - 50, _HALIFAX_BASE_NORTHING - 20),
        (_HALIFAX_BASE_EASTING + 50, _HALIFAX_BASE_NORTHING - 20),
    ]
    line_4326 = [list(transformer.transform(e, n)) for (e, n) in line_2961]
    return {"type": "LineString", "coordinates": line_4326}


def _crossroad_centerline_4326() -> dict[str, Any]:
    """A cross-street running along the parcel's west edge — for corner tests."""
    from pyproj import Transformer

    transformer = Transformer.from_crs(2961, 4326, always_xy=True)
    line_2961 = [
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING - 50),
        (_HALIFAX_BASE_EASTING - 15, _HALIFAX_BASE_NORTHING + 50),
    ]
    line_4326 = [list(transformer.transform(e, n)) for (e, n) in line_2961]
    return {"type": "LineString", "coordinates": line_4326}


def _centered_building_footprint() -> dict[str, Any]:
    """10 × 8 m building centered at project-local (0, 0)."""
    return {
        "type": "Polygon",
        "coordinates": [
            [[-5.0, -4.0], [5.0, -4.0], [5.0, 4.0], [-5.0, 4.0], [-5.0, -4.0]]
        ],
    }


def _make_extraction(
    *,
    footprint: dict[str, Any] | None,
    extra_attrs: list[ExtractedAttribute] | None = None,
) -> SubmissionExtractionResult:
    return SubmissionExtractionResult(
        source_type=SubmissionSourceType.IFC,
        source_artifact_path="/fake/path.ifc",
        attributes=extra_attrs or [],
        footprint_geojson=footprint,
        raw_metadata={
            "extractor": {
                "name": "ifc-submission",
                "ifc_schema": "IFC4",
                "geometric_context": _halifax_geometric_context(),
                "unit_scale_to_metres": 1.0,
            }
        },
        warnings=[],
    )


def _attrs_by_key(attrs):
    return {a.attribute_key: a for a in attrs}


# ----------------------------------------------------------------------
# Area-only attributes
# ----------------------------------------------------------------------


def test_lot_coverage_and_far_from_area_only_inputs():
    # No footprint geometry, but the extractor already reported area
    # attributes — lot_coverage_percent / floor_area_ratio still compute.
    parcel = _StubParcel(
        geometry_geojson=None,  # we'll skip geometric attrs anyway
        area_m2=600.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(
        footprint=None,
        extra_attrs=[
            ExtractedAttribute(
                attribute_key="building_footprint_area_m2", value=180.0, unit="m2"
            ),
            ExtractedAttribute(
                attribute_key="gross_floor_area_m2", value=360.0, unit="m2"
            ),
        ],
    )
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[],
    )
    by_key = _attrs_by_key(out)
    assert by_key["lot_coverage_percent"].value == 30.0
    assert by_key["lot_coverage_percent"].source == SubmissionAttributeSource.DERIVED
    assert by_key["floor_area_ratio"].value == 0.6
    assert by_key["floor_area_ratio"].source == SubmissionAttributeSource.DERIVED


def test_lot_coverage_skipped_when_parcel_area_missing():
    parcel = _StubParcel(geometry_geojson=None, area_m2=None, jurisdiction="HRM")
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=_make_extraction(
            footprint=None,
            extra_attrs=[
                ExtractedAttribute(
                    attribute_key="building_footprint_area_m2", value=180.0, unit="m2"
                ),
            ],
        ),
        nearby_centerlines=[],
    )
    assert "lot_coverage_percent" not in _attrs_by_key(out)


def test_lot_coverage_over_100_percent_warns_and_lowers_confidence():
    parcel = _StubParcel(
        geometry_geojson=None, area_m2=100.0, jurisdiction="HRM"
    )
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=_make_extraction(
            footprint=None,
            extra_attrs=[
                ExtractedAttribute(
                    attribute_key="building_footprint_area_m2", value=200.0, unit="m2"
                ),
            ],
        ),
        nearby_centerlines=[],
    )
    attr = _attrs_by_key(out)["lot_coverage_percent"]
    assert attr.value == 200.0
    assert attr.confidence == 0.4
    warns = attr.evidence.get("derived_run_warnings", [])
    assert any("larger than parcel" in w for w in warns)


def test_returns_empty_when_submission_has_no_parcel():
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=None),
        extraction=_make_extraction(footprint=None),
        nearby_centerlines=[],
    )
    assert out == []


# ----------------------------------------------------------------------
# Setbacks
# ----------------------------------------------------------------------


def test_setbacks_centered_building_symmetric():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=_centered_building_footprint())
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[_halifax_road_centerline_4326()],
    )
    by_key = _attrs_by_key(out)
    # Parcel is 30 × 40 m, building is 10 × 8 m centered → front setback
    # is (40-8)/2 = 16 m, side setbacks (30-10)/2 = 10 m each, rear = 16 m.
    assert by_key["front_setback_m"].value == pytest.approx(16.0, abs=0.1)
    assert by_key["rear_setback_m"].value == pytest.approx(16.0, abs=0.1)
    assert by_key["side_setback_left_m"].value == pytest.approx(10.0, abs=0.1)
    assert by_key["side_setback_right_m"].value == pytest.approx(10.0, abs=0.1)
    for key in ("front_setback_m", "rear_setback_m", "side_setback_left_m", "side_setback_right_m"):
        assert by_key[key].source == SubmissionAttributeSource.DERIVED


def test_setbacks_off_centre_building_asymmetric():
    # Push the building 5m east from centre. Front/rear setbacks
    # unchanged; left = 10+5 = 15, right = 10-5 = 5 (or vice versa
    # depending on left/right assignment).
    off_footprint = {
        "type": "Polygon",
        "coordinates": [
            [[0.0, -4.0], [10.0, -4.0], [10.0, 4.0], [0.0, 4.0], [0.0, -4.0]]
        ],
    }
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=off_footprint)
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[_halifax_road_centerline_4326()],
    )
    by_key = _attrs_by_key(out)
    # Front/rear still ~16m; sides should sum to ~20 (parcel width 30 - building 10).
    sides = by_key["side_setback_left_m"].value + by_key["side_setback_right_m"].value
    assert sides == pytest.approx(20.0, abs=0.5)
    assert by_key["side_setback_left_m"].value != by_key["side_setback_right_m"].value


def test_setbacks_skipped_when_no_centerlines():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=_centered_building_footprint())
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[],
    )
    by_key = _attrs_by_key(out)
    assert "front_setback_m" not in by_key
    assert "rear_setback_m" not in by_key


def test_geometric_attrs_skipped_when_jurisdiction_unmapped():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="Calgary",  # not in DEFAULT_JURISDICTION_EPSG_MAP
    )
    extraction = _make_extraction(
        footprint=_centered_building_footprint(),
        extra_attrs=[
            ExtractedAttribute(
                attribute_key="building_footprint_area_m2", value=80.0, unit="m2"
            ),
        ],
    )
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[_halifax_road_centerline_4326()],
    )
    by_key = _attrs_by_key(out)
    # Area-only attrs still compute.
    assert "lot_coverage_percent" in by_key
    # Setback attrs do not (no EPSG mapping for Calgary).
    assert "front_setback_m" not in by_key
    warns = by_key["lot_coverage_percent"].evidence["derived_run_warnings"]
    assert any("no source EPSG mapping" in w for w in warns)


# ----------------------------------------------------------------------
# Corner lot
# ----------------------------------------------------------------------


def test_corner_lot_detected_with_two_centerlines():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=_centered_building_footprint())
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[
            _halifax_road_centerline_4326(),
            _crossroad_centerline_4326(),
        ],
    )
    by_key = _attrs_by_key(out)
    corner = by_key["corner_lot_boolean"]
    assert corner.value is True
    assert corner.confidence == 1.0


def test_corner_lot_low_confidence_without_centerlines():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=_centered_building_footprint())
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[],
    )
    by_key = _attrs_by_key(out)
    corner = by_key["corner_lot_boolean"]
    assert corner.confidence == 0.4
    assert "no road centerlines" in corner.evidence["reason_for_low_confidence"]


# ----------------------------------------------------------------------
# Arterial frontage
# ----------------------------------------------------------------------


def test_arterial_frontage_detected_via_centerline_properties():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=_centered_building_footprint())
    centerline = _halifax_road_centerline_4326()
    centerline["properties"] = {"road_class": "Arterial"}
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[centerline],
    )
    by_key = _attrs_by_key(out)
    assert by_key["arterial_frontage_boolean"].value is True


def test_arterial_frontage_skipped_when_no_classification_metadata():
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=_centered_building_footprint())
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[_halifax_road_centerline_4326()],  # no properties
    )
    by_key = _attrs_by_key(out)
    assert "arterial_frontage_boolean" not in by_key


# ----------------------------------------------------------------------
# Footprint sanity
# ----------------------------------------------------------------------


def test_footprint_outside_parcel_warns():
    # Building 100m × 100m — way bigger than the 30×40m parcel.
    big_footprint = {
        "type": "Polygon",
        "coordinates": [
            [[-50.0, -50.0], [50.0, -50.0], [50.0, 50.0], [-50.0, 50.0], [-50.0, -50.0]]
        ],
    }
    parcel = _StubParcel(
        geometry_geojson=_halifax_parcel_polygon_4326(),
        area_m2=1200.0,
        jurisdiction="HRM",
    )
    extraction = _make_extraction(footprint=big_footprint)
    out = compute_derived_attributes(
        session=None,
        submission=_StubSubmission(parcel=parcel),
        extraction=extraction,
        nearby_centerlines=[_halifax_road_centerline_4326()],
    )
    # Setbacks still computed; some attribute carries a footprint-outside
    # warning in its run-level warnings.
    warns: list[str] = []
    for attr in out:
        warns.extend(attr.evidence.get("derived_run_warnings", []))
    assert any("inside the parcel" in w for w in warns)


# ----------------------------------------------------------------------
# Pipeline integration
# ----------------------------------------------------------------------


def test_end_to_end_via_ingest_submission_hook(tmp_path: Path):
    """ABS-48 + ABS-49 + ABS-52 + ABS-51 — the full chain.

    Seed a Parcel row, a synthetic IFC, register the IFC extractor,
    pass `compute_derived_attributes` as the pipeline's derived hook,
    and verify the persisted `submission_attribute` rows include both
    extracted *and* derived rows.
    """
    # Force the IFC extractor to be registered.
    import layer1.parsers.ifc_submission  # noqa: F401
    from fixtures.submissions.synthetic_ifc import (
        SyntheticBuildingSpec, SyntheticSpace, write_synthetic_ifc,
    )

    db_url = f"sqlite:///{tmp_path / 'derived.db'}"
    create_all(db_url)

    # Seed parcel.
    parcel_polygon = _halifax_parcel_polygon_4326()
    with session_scope(db_url) as session:
        parcel = Parcel(
            jurisdiction="HRM",
            parcel_identifier="TEST-001",
            geometry_geojson=parcel_polygon,
            area_m2=1200.0,
        )
        session.add(parcel)
        session.flush()
        parcel_id = parcel.id

    # Build a synthetic IFC with a 10×8 footprint centered on project-local origin.
    # The world_origin sits on the Halifax base point so the reprojected
    # footprint lands near the parcel centroid and ABS-52's sanity check
    # doesn't fire.
    spec = SyntheticBuildingSpec(
        overall_height_m=9.0,
        storey_gross_planned_area_m2=[200.0, 200.0],
        spaces=[
            SyntheticSpace(name="Apt 1", occupancy_type="Residential Unit"),
        ],
        footprint_coords=[(-5.0, -4.0), (5.0, -4.0), (5.0, 4.0), (-5.0, 4.0)],
        world_origin=(_HALIFAX_BASE_EASTING, _HALIFAX_BASE_NORTHING, 0.0),
    )
    ifc_path = write_synthetic_ifc(spec, tmp_path / "demo.ifc")

    centerlines = [_halifax_road_centerline_4326()]

    # Hand-rolled hook that injects centerlines so the test doesn't
    # need to seed an external_dataset_feature row.
    def _hook(session, submission, extraction):
        return compute_derived_attributes(
            session=session,
            submission=submission,
            extraction=extraction,
            nearby_centerlines=centerlines,
        )

    taxonomy = load_taxonomy()
    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            ifc_path,
            SubmissionSourceType.IFC,
            parcel_id=parcel_id,
            config=SubmissionIngestConfig(run_evaluator=False),
            derived_attribute_fn=_hook,
            taxonomy=taxonomy,
        )

    assert result.errors == []

    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        rows = {a.attribute_key: a for a in sub.attributes}
        # Derived attributes present.
        assert "lot_coverage_percent" in rows
        assert rows["lot_coverage_percent"].source == SubmissionAttributeSource.DERIVED
        assert "floor_area_ratio" in rows
        assert "front_setback_m" in rows
        assert "rear_setback_m" in rows
        # Extracted attributes still there too.
        assert rows["building_height_m"].source == SubmissionAttributeSource.EXTRACTED
