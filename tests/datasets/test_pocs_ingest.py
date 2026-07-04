"""Ingest coverage for the Schedule 7 Pedestrian-Oriented Commercial Streets
dataset (ABS-349).

Schedule 7 of the Regional Centre Land Use By-Law is a map-only schedule with
no textual street list — the by-law text references it in 23+ fragments
(ground-floor uses s.38(2)/s.69(d), setbacks, streetwall storeys) but never
enumerates the streets, so no retrieval call could answer "does this lot abut
a pedestrian-oriented commercial street". This dataset supplies the missing
spatial layer, digitized from the schedule map and committed as a repo GeoJSON.

These tests pin:
  * the real committed config + GeoJSON parse cleanly to PARSED with the
    expected corridor count and the street_name canonical surfaced, and
  * the dataset binds to the Regional Centre LUB "Schedule 7" fragment when
    that document/fragment exist (the links_to path that seed + prod rely on).
"""
from __future__ import annotations

from pathlib import Path

from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    SourceFragment,
    utcnow,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset


CONFIG_PATH = Path("src/layer1/datasets/halifax_pedestrian_oriented_commercial_streets.yaml")
EXPECTED_STREETS = {
    "Quinpool Road",
    "Spring Garden Road",
    "Gottingen Street",
    "Agricola Street",
    "Barrington Street",
    "Dutch Village Road",
}


def _setup_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'layer1.db'}"
    create_all(db_url)
    return db_url


def test_pocs_config_parses_to_designated_corridors(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, CONFIG_PATH)

        assert result.dataset.parse_status == ParseStatus.PARSED
        assert result.dataset.feature_count == len(EXPECTED_STREETS)
        assert result.dataset.linked_fragment_citation == "Schedule 7"
        assert result.warnings == []
        dataset_id = result.dataset.id

    with session_scope(db_url) as session:
        dataset = session.get(ExternalDataset, dataset_id)
        assert dataset is not None
        assert dataset.name == "halifax_pedestrian_oriented_commercial_streets"

        features = (
            session.query(ExternalDatasetFeature)
            .filter_by(external_dataset_id=dataset_id)
            .all()
        )
        assert len(features) == len(EXPECTED_STREETS)
        # street_name is surfaced so a spatial hit can cite the corridor by name.
        assert {
            f.canonical_attributes_json["street_name"] for f in features
        } == EXPECTED_STREETS
        # Every feature is a designated segment geometry (LineString) — the
        # whole point is that ST_Intersects has real geometry to match against.
        assert all(
            f.geometry_geojson["type"] == "LineString" for f in features
        )
        # The raw schedule provenance is retained even though it's not canonical.
        quinpool = next(
            f for f in features
            if f.canonical_attributes_json["street_name"] == "Quinpool Road"
        )
        assert quinpool.attributes_json["SCHEDULE"] == "Schedule 7"


def test_pocs_links_to_regional_centre_schedule_7_fragment(tmp_path: Path):
    db_url = _setup_db(tmp_path)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="test/rclub.pdf",
            file_hash="test-rclub-doc",
            mime_type="application/pdf",
            page_count=500,
            parser_version="test",
            ingestion_timestamp=utcnow(),
        )
        session.add(document)
        session.flush()
        session.add(
            SourceFragment(
                document_id=document.id,
                fragment_type=FragmentType.SCHEDULE,
                citation_label="Schedule 7",
                citation_path="schedule_7",
                page_start=27,
                page_end=27,
                reading_order_start=27,
                text="Schedule 7: Pedestrian-Oriented Commercial Streets.",
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        session.flush()

        result = ingest_geo_dataset(session, CONFIG_PATH)

        assert result.link_result.status == "linked"
        assert result.dataset.linked_document_id == document.id
        assert result.dataset.linked_fragment_id is not None
