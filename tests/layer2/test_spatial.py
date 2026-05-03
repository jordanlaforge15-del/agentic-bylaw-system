from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, ExternalDataset, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment
from layer2.retrieval.datasets import expand_datasets
from layer2.retrieval.spatial import (
    ResolvedLocation,
    expand_spatial,
    query_features,
)


# Mini fixture covers three adjacent precincts:
#   precinct A (25m): -63.60 .. -63.58 lon, 44.64 .. 44.66 lat
#   precinct B (35m): -63.58 .. -63.56 lon, 44.64 .. 44.66 lat
#   precinct C (50m): -63.56 .. -63.54 lon, 44.64 .. 44.66 lat
MINI_FIXTURE_CONFIG = """
name: mini_height_precincts_spatial
publisher: Test
format: geojson
source_path: tests/fixtures/geo/mini_height_precincts.geojson
crs: EPSG:4326
links_to:
  document_match:
    municipality: Halifax Regional Municipality
    bylaw_name: Regional Centre Land Use By-law
  fragment_citation: Schedule 15
attributes:
  feature_key: GLOBALID
  canonical:
    max_height_m: { from: HEIGHT, type: float }
    display_label: { synthesize: "{HEIGHT}m precinct" }
  ignore: [OBJECTID, SACC]
"""


@pytest.fixture()
def linked_dataset(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax Regional Municipality",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="/synthetic.pdf",
            file_hash="cafefeed" * 8,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()
        fragment = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Schedule 15",
            citation_path="schedules.schedule_15",
            page_start=500,
            page_end=502,
            text="Schedule 15: Maximum Building Height Precincts.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(fragment)
        session.flush()
        document_id, fragment_id = document.id, fragment.id

    cfg_path = tmp_path / "mini.yaml"
    cfg_path.write_text(MINI_FIXTURE_CONFIG)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        dataset_id = result.dataset.id
        assert result.link_result.status == "linked"

    return {
        "db_url": db_url,
        "document_id": document_id,
        "fragment_id": fragment_id,
        "dataset_id": dataset_id,
    }


def _point(lon: float, lat: float) -> ResolvedLocation:
    return ResolvedLocation(
        kind="point",
        geometry={"type": "Point", "coordinates": [lon, lat]},
        source="test",
        reference_text=f"({lon},{lat})",
    )


def _line(coords: list[list[float]]) -> ResolvedLocation:
    return ResolvedLocation(
        kind="shape",
        geometry={"type": "LineString", "coordinates": coords},
        source="test",
        reference_text="line",
    )


def test_point_in_single_polygon_returns_one_match(linked_dataset):
    with session_scope(linked_dataset["db_url"]) as session:
        # Center of precinct A (25m):
        matches = query_features(
            session,
            dataset_id=linked_dataset["dataset_id"],
            location=_point(-63.59, 44.65),
        )
    assert len(matches) == 1
    assert matches[0].feature.canonical_attributes_json["max_height_m"] == 25.0
    assert matches[0].contains_input is True


def test_point_outside_all_polygons_returns_empty(linked_dataset):
    with session_scope(linked_dataset["db_url"]) as session:
        matches = query_features(
            session,
            dataset_id=linked_dataset["dataset_id"],
            location=_point(-63.40, 44.70),
        )
    assert matches == []


def test_line_crossing_two_precincts_returns_both_in_overlap_order(linked_dataset):
    # Line from inside A through inside B, sliced by their shared boundary at -63.58:
    line = _line([[-63.595, 44.65], [-63.575, 44.65]])
    with session_scope(linked_dataset["db_url"]) as session:
        matches = query_features(
            session,
            dataset_id=linked_dataset["dataset_id"],
            location=line,
        )
    heights = [m.feature.canonical_attributes_json["max_height_m"] for m in matches]
    assert sorted(heights) == [25.0, 35.0]
    # Each match is a partial overlap so contains_input must be False:
    assert all(m.contains_input is False for m in matches)
    # Overlap_area is non-zero and ordered descending:
    assert matches[0].overlap_area >= matches[-1].overlap_area > 0


def test_expand_spatial_emits_dataset_feature_candidates(linked_dataset):
    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        with_dataset = expand_datasets(session, candidates)
        with_features = expand_spatial(
            session, with_dataset, location=_point(-63.59, 44.65)
        )

    feature_candidates = [
        c for c in with_features if c.source_type == SourceType.DATASET_FEATURE.value
    ]
    assert len(feature_candidates) == 1
    fc = feature_candidates[0]
    assert fc.retrieval_channel == RetrievalChannel.SPATIAL.value
    assert fc.external_dataset_id == linked_dataset["dataset_id"]
    assert fc.external_dataset_feature_id is not None
    assert "25m precinct" in fc.text or "max_height_m=25" in fc.text
    assert fc.reason["expansion"] == "spatial"
    assert fc.reason["contains_input"] is True
    assert fc.reason["location_source"] == "test"
    assert fc.metadata["canonical_attributes"]["max_height_m"] == 25.0


def test_expand_spatial_passthrough_when_no_location(linked_dataset):
    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        with_dataset = expand_datasets(session, candidates)
        with_features = expand_spatial(session, with_dataset, location=None)
    assert with_features == with_dataset


def test_expand_spatial_does_nothing_without_dataset_candidate(linked_dataset):
    """If traversal never reached the dataset, location alone shouldn't
    fabricate evidence — it must come *through* the graph."""
    with session_scope(linked_dataset["db_url"]) as session:
        candidates = [
            CandidateFragment(
                source_fragment_id=999,  # not the linked fragment
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Unrelated prose.",
            )
        ]
        result = expand_spatial(session, candidates, location=_point(-63.59, 44.65))
    assert result == candidates


def test_location_survives_to_other_datasets_in_same_query(linked_dataset):
    """When two DATASET candidates appear (height + FAR + zone overlay), the
    location parameter must apply to both — it's not consumed by the first
    match."""
    with session_scope(linked_dataset["db_url"]) as session:
        # Pretend a second linked dataset exists for the same fragment:
        second = ExternalDataset(
            name="mini_height_precincts_clone",
            publisher="Test",
            source_url=None,
            source_path="/synthetic2.geojson",
            format="geojson",
            content_hash="abc" * 22 + "ab",
            crs="EPSG:4326",
            feature_count=0,
            linked_document_id=linked_dataset["document_id"],
            linked_fragment_citation="Schedule 15",
            linked_fragment_id=linked_dataset["fragment_id"],
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(second)
        session.flush()

        candidates = [
            CandidateFragment(
                source_fragment_id=linked_dataset["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        with_datasets = expand_datasets(session, candidates)
        # Confirm we now have two DATASET candidates:
        assert sum(1 for c in with_datasets if c.source_type == SourceType.DATASET.value) == 2

        with_features = expand_spatial(
            session, with_datasets, location=_point(-63.59, 44.65)
        )
    # The first dataset has features; the second is empty. Both DATASET
    # candidates remain; one DATASET_FEATURE arrives.
    feature_candidates = [
        c for c in with_features if c.source_type == SourceType.DATASET_FEATURE.value
    ]
    assert len(feature_candidates) == 1
    assert feature_candidates[0].external_dataset_id == linked_dataset["dataset_id"]


def test_invalid_location_geometry_returns_empty_match_list(linked_dataset):
    bogus = ResolvedLocation(
        kind="shape",
        geometry={"type": "Polygon", "coordinates": [[[0, 0]]]},  # too few points
        source="test",
    )
    with session_scope(linked_dataset["db_url"]) as session:
        matches = query_features(
            session, dataset_id=linked_dataset["dataset_id"], location=bogus
        )
    assert matches == []
