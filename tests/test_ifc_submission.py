"""ABS-49: IFC submission extractor tests.

Exercises `extract_ifc` against synthetic IFC files built in-process so
every test owns its fixture. Importing `layer1.parsers.ifc_submission`
also exercises its side-effect: registering itself with the submission
factory (asserted in `test_factory_dispatches_ifc_via_registration`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Import for the registration side-effect; the symbol is also used directly.
from layer1.parsers.ifc_submission import extract_ifc
from layer1.parsers.submission_factory import extract_submission, get_extractor
from layer1.models.submission_schemas import SubmissionIngestConfig
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.pipeline.ingest_submission import ingest_submission
from layer2.compliance.db.models import (
    Submission,
    SubmissionAttribute,
    SubmissionAttributeSource,
    SubmissionSourceType,
    SubmissionStatus,
)
from layer2.compliance.taxonomy import load_taxonomy

from fixtures.submissions.synthetic_ifc import (
    SyntheticBuildingSpec,
    SyntheticSpace,
    write_synthetic_ifc,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _attr_by_key(result, key: str):
    matches = [a for a in result.attributes if a.attribute_key == key]
    assert matches, f"{key!r} not in extraction result; got {[a.attribute_key for a in result.attributes]}"
    assert len(matches) == 1, f"{key!r} appeared {len(matches)} times — extractor is double-emitting"
    return matches[0]


def _has_attr(result, key: str) -> bool:
    return any(a.attribute_key == key for a in result.attributes)


@pytest.fixture()
def cfg() -> SubmissionIngestConfig:
    return SubmissionIngestConfig(run_evaluator=False)


# ----------------------------------------------------------------------
# Factory registration
# ----------------------------------------------------------------------


def test_factory_dispatches_ifc_via_registration(tmp_path: Path, cfg):
    # Confirms importing ifc_submission registers the extractor; this is
    # what makes the scaffold pipeline pick it up by source_type.
    extractor = get_extractor(SubmissionSourceType.IFC)
    assert extractor is extract_ifc

    spec = SyntheticBuildingSpec(overall_height_m=8.0, spaces=[])
    ifc_path = write_synthetic_ifc(spec, tmp_path / "ok.ifc")
    result = extract_submission(ifc_path, SubmissionSourceType.IFC, config=cfg)
    assert result.source_type == SubmissionSourceType.IFC
    assert _has_attr(result, "building_height_m")


# ----------------------------------------------------------------------
# Building-level attributes
# ----------------------------------------------------------------------


def test_overall_height_extracted_with_full_confidence(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(overall_height_m=12.5)
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "h.ifc"), cfg)

    height = _attr_by_key(result, "building_height_m")
    assert height.value == pytest.approx(12.5)
    assert height.unit == "m"
    assert height.confidence == 1.0
    assert (
        height.evidence["source_field"] == "Pset_BuildingCommon.OverallHeight"
    )


def test_missing_overall_height_falls_back_to_storey_elevation_span(
    tmp_path: Path, cfg
):
    spec = SyntheticBuildingSpec(
        overall_height_m=None,
        storey_elevations_m=[0.0, 3.5, 7.0],
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "fb.ifc"), cfg)

    height = _attr_by_key(result, "building_height_m")
    assert height.value == pytest.approx(7.0)
    assert height.confidence == 0.6
    assert any(
        "derived from storey elevation span" in w for w in result.warnings
    )


def test_missing_height_and_no_elevations_skips_attribute_with_warning(
    tmp_path: Path, cfg
):
    spec = SyntheticBuildingSpec(
        overall_height_m=None,
        storey_elevations_m=[],  # no storeys
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "none.ifc"), cfg)

    assert not _has_attr(result, "building_height_m")
    assert any("building_height_m not extracted" in w for w in result.warnings)


def test_primary_use_class_from_object_type_has_heuristic_confidence(
    tmp_path: Path, cfg
):
    spec = SyntheticBuildingSpec(object_type="Mixed Use Residential")
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "ut.ifc"), cfg)

    use = _attr_by_key(result, "primary_use_class")
    assert use.value == "mixed use residential"  # lowercased
    # ObjectType is free-text — heuristic confidence per the policy.
    assert use.confidence == 0.4


def test_storey_count_is_exact(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(storey_elevations_m=[0.0, 3.0, 6.0, 9.0])
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "sc.ifc"), cfg)

    sc = _attr_by_key(result, "building_height_storeys")
    assert sc.value == 4
    assert sc.confidence == 1.0


# ----------------------------------------------------------------------
# GFA paths
# ----------------------------------------------------------------------


def test_gfa_summed_from_storeys_when_pset_present(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(
        overall_height_m=6.0,
        storey_gross_planned_area_m2=[180.0, 180.0],
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "gfa.ifc"), cfg)

    gfa = _attr_by_key(result, "gross_floor_area_m2")
    assert gfa.value == pytest.approx(360.0)
    assert gfa.confidence == 1.0
    assert gfa.evidence["source_field"].startswith("sum(Pset_BuildingCommon")


def test_gfa_falls_back_to_space_quantities(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(
        overall_height_m=6.0,
        storey_gross_planned_area_m2=None,  # no storey pset
        spaces=[
            SyntheticSpace(name="Apt 1", occupancy_type="Residential Unit",
                           gross_floor_area_m2=90.0, storey_index=0),
            SyntheticSpace(name="Apt 2", occupancy_type="Residential Unit",
                           gross_floor_area_m2=90.0, storey_index=1),
        ],
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "gfa-fb.ifc"), cfg)

    gfa = _attr_by_key(result, "gross_floor_area_m2")
    assert gfa.value == pytest.approx(180.0)
    assert gfa.confidence == 0.6
    assert any("derived from IfcSpace area quantities" in w for w in result.warnings)


# ----------------------------------------------------------------------
# Space-count attributes
# ----------------------------------------------------------------------


def test_residential_unit_count_from_occupancy_type(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(
        overall_height_m=6.0,
        spaces=[
            SyntheticSpace(name="Apt 1", occupancy_type="Residential Unit"),
            SyntheticSpace(name="Apt 2", occupancy_type="Residential Unit"),
            SyntheticSpace(name="Apt 3", object_type="Dwelling"),  # object_type path
        ],
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "ru.ifc"), cfg)

    ru = _attr_by_key(result, "residential_unit_count")
    assert ru.value == 3
    assert ru.confidence == 1.0


def test_parking_and_bicycle_counts_with_unrecognised_occupancy_drops_confidence(
    tmp_path: Path, cfg
):
    spec = SyntheticBuildingSpec(
        overall_height_m=6.0,
        spaces=[
            SyntheticSpace(name="Parking 1", object_type="Parking"),
            SyntheticSpace(name="Parking 2", object_type="Parking"),
            SyntheticSpace(name="Bike rack", object_type="Bicycle Storage"),
            SyntheticSpace(name="Mystery", object_type="Storage Closet"),  # unrecognised
        ],
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "ct.ifc"), cfg)

    parking = _attr_by_key(result, "parking_stalls_count")
    assert parking.value == 2
    # An unrecognised occupancy text in the source drops confidence
    # because we might have missed parking/residential/etc. spaces.
    assert parking.confidence == 0.6
    assert "storage closet" in parking.evidence["unrecognised_occupancy_texts"]

    bike = _attr_by_key(result, "bicycle_stalls_count")
    assert bike.value == 1

    residential = _attr_by_key(result, "residential_unit_count")
    assert residential.value == 0  # zero is a real value — emit it.


# ----------------------------------------------------------------------
# Footprint geometry + geometric context
# ----------------------------------------------------------------------


def test_footprint_polygon_extracted_and_closed(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(
        overall_height_m=6.0,
        footprint_coords=[(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)],
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "fp.ifc"), cfg)

    assert result.footprint_geojson is not None
    assert result.footprint_geojson["type"] == "Polygon"
    ring = result.footprint_geojson["coordinates"][0]
    # RFC 7946 requires the ring to close; the extractor enforces this.
    assert ring[0] == ring[-1]
    # The 4 distinct corners should be present (project-local metres).
    distinct = {tuple(p) for p in ring}
    assert distinct == {(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)}
    # Provenance kept so ABS-51 / UI can audit.
    assert result.footprint_geojson["properties"]["crs"] == "project-local"


def test_footprint_missing_when_no_slab(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(
        overall_height_m=6.0,
        footprint_coords=None,
    )
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "nofp.ifc"), cfg)

    assert result.footprint_geojson is None
    assert any("footprint polygon not extracted" in w for w in result.warnings)


def test_geometric_context_captured_on_raw_metadata(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(overall_height_m=6.0)
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "ctx.ifc"), cfg)

    ctx = result.raw_metadata["extractor"]["geometric_context"]
    assert ctx["context_type"] == "Model"
    # ifcopenshell sets a default world origin even if zero — present is what matters.
    assert ctx["coordinate_space_dimension"] == 3


# ----------------------------------------------------------------------
# Multi-building / edge cases
# ----------------------------------------------------------------------


def test_multiple_buildings_uses_first_and_warns(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(overall_height_m=6.0, n_buildings=2)
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "multi.ifc"), cfg)

    assert any("IfcBuilding entities" in w for w in result.warnings)


def test_raw_metadata_records_library_and_schema(tmp_path: Path, cfg):
    spec = SyntheticBuildingSpec(overall_height_m=6.0)
    result = extract_ifc(write_synthetic_ifc(spec, tmp_path / "meta.ifc"), cfg)

    meta = result.raw_metadata["extractor"]
    assert meta["name"] == "ifc-submission"
    assert meta["ifc_schema"] == "IFC4"
    assert meta["ifcopenshell_version"]  # whatever version got installed
    assert meta["n_storeys"] == 2
    assert meta["building_global_id"]


# ----------------------------------------------------------------------
# End-to-end via the scaffold pipeline
# ----------------------------------------------------------------------


def test_end_to_end_ingest_submission_with_ifc(tmp_path: Path):
    """Verify the scaffold pipeline → IFC extractor → persistence path.

    This is the integration the scaffold (ABS-48) and IFC extractor
    (this issue) jointly own. Confirms that a real IFC file dropped on
    `ingest_submission(..., IFC, ...)` lands `submission` +
    `submission_attribute` rows with the extractor's confidence values.
    """
    db_url = f"sqlite:///{tmp_path / 'e2e.db'}"
    create_all(db_url)

    spec = SyntheticBuildingSpec(
        overall_height_m=11.0,
        storey_gross_planned_area_m2=[240.0, 240.0],
        spaces=[
            SyntheticSpace(name="Apt 1", occupancy_type="Residential Unit", storey_index=1),
            SyntheticSpace(name="Apt 2", occupancy_type="Residential Unit", storey_index=1),
            SyntheticSpace(name="Stall 1", object_type="Parking", storey_index=0),
        ],
    )
    ifc_path = write_synthetic_ifc(spec, tmp_path / "demo.ifc")

    # Real taxonomy — we want to confirm the extractor's keys all match
    # what's in `attributes/taxonomy.yaml`, not a stub.
    taxonomy = load_taxonomy()

    with session_scope(db_url) as session:
        result = ingest_submission(
            session,
            ifc_path,
            SubmissionSourceType.IFC,
            config=SubmissionIngestConfig(run_evaluator=False),
            taxonomy=taxonomy,
        )

    assert result.errors == []
    assert result.n_attributes_persisted >= 5  # height, storeys, gfa, ru count, parking count, bike count, use
    assert result.n_attributes_skipped == 0  # every extracted key should be in the real taxonomy

    with session_scope(db_url) as session:
        sub = session.get(Submission, result.submission_id)
        assert sub.source_type == SubmissionSourceType.IFC
        assert sub.status == SubmissionStatus.DRAFT  # evaluator skipped
        rows = {a.attribute_key: a for a in sub.attributes}
        assert "building_height_m" in rows
        assert rows["building_height_m"].value_json["value"] == pytest.approx(11.0)
        assert rows["building_height_m"].source == SubmissionAttributeSource.EXTRACTED
        assert rows["residential_unit_count"].value_json["value"] == 2
        assert rows["parking_stalls_count"].value_json["value"] == 1
        assert sub.metadata_json["footprint_geojson"]["type"] == "Polygon"
