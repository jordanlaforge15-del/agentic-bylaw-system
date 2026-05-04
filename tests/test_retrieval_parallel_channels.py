"""Phase J — spatial as a parallel retrieval channel.

The MCP retrieval service runs two retrievers per request:
- text: keyword scoring against fragments (existing).
- spatial: when a location is supplied, intersection against every dataset
  whose linked_fragment_id is in scope. A spatial hit surfaces its linked
  fragment as a top-level match even when keyword scoring didn't pick it up.

These tests exercise the merge logic on a synthetic doc so spatial behavior
is testable without depending on the real Halifax dataset.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import LocationSlot, RetrievalRequest, RetrievalService
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2


HEIGHT_CONFIG = """
name: parallel_channels_height
publisher: Test
format: geojson
source_path: tests/fixtures/geo/mini_height_precincts.geojson
crs: EPSG:4326
links_to:
  document_match:
    municipality: HRM
    bylaw_name: Regional Centre Land Use By-Law
  fragment_citation: Schedule 15
attributes:
  feature_key: GlobalID
  canonical:
    max_height_m: { from: MAXBLDHGT, type: float, optional: true }
    max_height_storeys: { from: MAXBLDSTRY, type: int, optional: true }
"""


@pytest.fixture()
def linked_dataset(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'parallel.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/synthetic.pdf",
            file_hash="p" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=500,
        )
        session.add(document)
        session.flush()
        # Schedule 15 fragment with intentionally THIN text — the keyword
        # scorer won't pick it up for "max height at <address>" queries.
        # This is exactly the synthetic-fragment situation in production,
        # which Phase J is meant to handle.
        schedule = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Schedule 15",
            citation_path="schedules.schedule_15",
            page_start=500,
            page_end=500,
            text="Schedule 15.",  # deliberately minimal
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={"manually_seeded": True},
        )
        session.add(schedule)
        # An UNRELATED fragment that text-matches "max height" — would be
        # the top text hit if spatial channel didn't exist.
        prose = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.SECTION,
            citation_label="109",
            citation_path="109",
            page_start=115,
            page_end=115,
            text="109 Maximum building height shall not exceed the maximum required building height.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        session.add(prose)
        session.flush()
        document_id = document.id
        schedule_id = schedule.id
        prose_id = prose.id

    cfg_path = tmp_path / "height.yaml"
    cfg_path.write_text(HEIGHT_CONFIG)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.link_result.status == "linked"
        assert result.link_result.fragment_id == schedule_id

    return {
        "db_url": db_url,
        "document_id": document_id,
        "schedule_id": schedule_id,
        "prose_id": prose_id,
    }


def _point_inside_first_precinct() -> dict:
    """Centroid of feature 1 in the mini fixture (max_height_m=25)."""
    return {"type": "Point", "coordinates": [-63.59, 44.65]}


def test_spatial_channel_surfaces_schedule_when_text_misses(linked_dataset):
    """Spatial-only path: a query that does NOT keyword-match the synthetic
    Schedule 15 fragment still surfaces it as a top-level match because the
    location intersected its linked dataset. The dataset feature comes
    along as evidence."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="zzz_does_not_match_anything",
                location=LocationSlot(geometry=_point_inside_first_precinct()),
                limit=5,
            )
        )

    schedule_matches = [
        m for m in response.matches if m.fragment_id == linked_dataset["schedule_id"]
    ]
    assert len(schedule_matches) == 1
    sm = schedule_matches[0]
    assert sm.retrieval_channels == ["spatial"]
    assert len(sm.linked_datasets) == 1
    assert len(sm.linked_datasets[0].feature_matches) == 1
    fm = sm.linked_datasets[0].feature_matches[0]
    assert fm.canonical_attributes["max_height_m"] == 25.0
    assert fm.contains_input is True


def test_text_channel_alone_when_no_location(linked_dataset):
    """No location supplied: spatial channel doesn't run, behaviour is the
    pre-Phase-J text-only path."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(query="maximum building height", limit=5)
        )

    assert response.matches
    # Section 109 is the strong text match.
    top = response.matches[0]
    assert top.fragment_id == linked_dataset["prose_id"]
    assert top.retrieval_channels == ["text"]
    # Schedule fragment doesn't appear because its text is just "Schedule 15."
    schedule_present = any(
        m.fragment_id == linked_dataset["schedule_id"] for m in response.matches
    )
    assert not schedule_present


def test_both_channels_boost_a_match_when_text_and_spatial_agree(linked_dataset):
    """When the same fragment is hit by BOTH channels, score gets a bonus
    so it ranks above single-channel hits at the same raw score."""
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        # Make the text channel hit Schedule 15 too, by querying for its
        # exact citation_label so the keyword scorer picks it up.
        response = service.search(
            RetrievalRequest(
                query="Schedule 15",
                location=LocationSlot(geometry=_point_inside_first_precinct()),
                limit=5,
            )
        )

    schedule_match = next(
        m for m in response.matches if m.fragment_id == linked_dataset["schedule_id"]
    )
    assert sorted(schedule_match.retrieval_channels) == ["spatial", "text"]
    # Score is at least the spatial-contains score plus the both-bonus.
    assert schedule_match.score >= 100.0 + 10.0


def test_spatial_channel_finds_nothing_outside_polygons(linked_dataset):
    """A point that doesn't intersect any feature produces no spatial
    matches. Text matches still flow normally."""
    far_away = {"type": "Point", "coordinates": [-100.0, 50.0]}
    with session_scope(linked_dataset["db_url"]) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="maximum building height",
                location=LocationSlot(geometry=far_away),
                limit=5,
            )
        )

    # No spatial-only matches; text matches still appear (Section 109).
    assert response.matches
    assert all(m.retrieval_channels == ["text"] for m in response.matches)


def test_spatial_channel_respects_default_scope(tmp_path: Path):
    """Under --latest-only, the spatial channel must only consider datasets
    whose linked fragment is on the active document. No leak into datasets
    linked to older documents.
    """
    db_url = f"sqlite:///{tmp_path / 'scoped.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        # Two documents, both with a Schedule 15 fragment.
        old = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/old.pdf",
            file_hash="o" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            page_count=500,
        )
        new = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/new.pdf",
            file_hash="n" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime(2026, 5, 4, tzinfo=timezone.utc),
            page_count=500,
        )
        session.add_all([old, new])
        session.flush()
        for doc in (old, new):
            session.add(
                SourceFragment(
                    document_id=doc.id,
                    fragment_type=FragmentType.SCHEDULE,
                    citation_label="Schedule 15",
                    citation_path="schedules.schedule_15",
                    page_start=500,
                    page_end=500,
                    text="Schedule 15.",
                    parse_status=ParseStatus.PARSED,
                    source_block_ids_json=[],
                    metadata_json={},
                )
            )
        session.flush()
        new_id = new.id

    # Ingest the dataset — Phase B's linker picks the most-recent doc by
    # default (this is established behavior; see test_linker.py).
    cfg_path = tmp_path / "height.yaml"
    cfg_path.write_text(HEIGHT_CONFIG)
    with session_scope(db_url) as session:
        ingest_geo_dataset(session, cfg_path)

    from bylaw_retrieval.retrieval import latest_document_id_resolver

    with session_scope(db_url) as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        response = service.search(
            RetrievalRequest(
                query="anything",
                location=LocationSlot(geometry=_point_inside_first_precinct()),
                limit=5,
            )
        )

    # Every match's document_id must be the latest doc — spatial channel
    # didn't reach back into the old doc.
    assert all(m.document_id == new_id for m in response.matches)


def test_no_linked_datasets_yields_no_spatial_matches(tmp_path: Path):
    """Sanity check: if no dataset is linked, spatial channel produces
    nothing — and the response is just the text channel."""
    db_url = f"sqlite:///{tmp_path / 'nolinks.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/synthetic.pdf",
            file_hash="x" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=10,
        )
        session.add(document)
        session.flush()
        session.add(
            SourceFragment(
                document_id=document.id,
                fragment_type=FragmentType.SECTION,
                citation_label="1",
                citation_path="1",
                page_start=1,
                page_end=1,
                text="Maximum height shall not exceed limits set by Schedule 15.",
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(
                query="maximum height",
                location=LocationSlot(geometry=_point_inside_first_precinct()),
                limit=5,
            )
        )
    # Only text matches; no spatial-only entries (no linked datasets exist).
    assert all(m.retrieval_channels == ["text"] for m in response.matches)
