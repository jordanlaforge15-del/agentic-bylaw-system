"""ABS-273 / Phase 3 — the thick ``get_address_profile`` tool.

``get_address_profile`` collapses the address → zone spatial resolution plus
every linked-overlay lookup (height precinct, FAR precinct, heritage, bonus
zoning) into a single call, so a case-bound conversation gets its grounding
context up front instead of spending 4+ tool calls assembling it.

These tests build a synthetic Halifax-shaped corpus on sqlite — a document
with the schedule fragments each overlay dataset links to, the datasets and
their (point-containing) features, and a ``geocode_cache`` row so the civic
address resolves without an external geocoder. That mirrors the production
spatial path closely enough that a divergence in the shared spatial-join
code (FR-3.3) trips here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import (
    AddressProfile,
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
)
from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    SourceFragment,
)
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.db.init_db import create_all as create_layer2


# A point inside every seeded overlay polygon, and the box that contains it.
TEST_POINT = {"type": "Point", "coordinates": [-63.59, 44.65]}
_BOX_COORDS = [
    [-63.60, 44.64],
    [-63.58, 44.64],
    [-63.58, 44.66],
    [-63.60, 44.66],
    [-63.60, 44.64],
]


def _box_geojson() -> dict:
    return {"type": "Polygon", "coordinates": [_BOX_COORDS]}


def _box_bbox() -> dict:
    return {"minx": -63.60, "miny": 44.64, "maxx": -63.58, "maxy": 44.66}


def _add_fragment(session, *, document_id: int, label: str, path: str) -> int:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label=label,
        citation_path=path,
        page_start=500,
        page_end=500,
        text=f"{label}.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment.id


def _add_overlay_dataset(
    session,
    *,
    name: str,
    fragment_id: int,
    citation: str,
    canonical: dict,
) -> None:
    """Insert a linked dataset + a single point-containing feature.

    We build the rows directly (rather than running ``ingest_geo_dataset``)
    so each feature's ``canonical_attributes_json`` is pinned exactly — the
    profile reads those verbatim.
    """
    dataset = ExternalDataset(
        name=name,
        publisher="Test",
        format="geojson",
        content_hash=f"hash-{name}",
        crs="EPSG:4326",
        feature_count=1,
        linked_fragment_id=fragment_id,
        linked_fragment_citation=citation,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(dataset)
    session.flush()
    session.add(
        ExternalDatasetFeature(
            external_dataset_id=dataset.id,
            feature_key=f"{name}-f1",
            attributes_json={},
            canonical_attributes_json=canonical,
            geometry_geojson=_box_geojson(),
            geometry_bbox_json=_box_bbox(),
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
    )
    session.flush()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    """A sqlite DB with the Regional Centre doc, four overlays, and a
    geocode-cache row resolving ``100 Robie Street`` to ``TEST_POINT``."""
    db_url = f"sqlite:///{tmp_path / 'address_profile.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="/synthetic.pdf",
            file_hash="r" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            page_count=500,
        )
        session.add(document)
        session.flush()
        document_id = document.id

        zoning_frag = _add_fragment(
            session, document_id=document_id, label="Zoning Schedule", path="zoning_schedule"
        )
        height_frag = _add_fragment(
            session, document_id=document_id, label="Schedule 15", path="schedules.schedule_15"
        )
        far_frag = _add_fragment(
            session, document_id=document_id, label="Schedule 17", path="schedules.schedule_17"
        )
        heritage_frag = _add_fragment(
            session, document_id=document_id, label="Schedule 22", path="schedules.schedule_22"
        )

        _add_overlay_dataset(
            session,
            name="halifax_zoning_boundaries",
            fragment_id=zoning_frag,
            citation="Zoning Schedule",
            canonical={"zone_code": "HR-2", "zone_description": "High-Rise Residential"},
        )
        _add_overlay_dataset(
            session,
            name="halifax_height_precincts",
            fragment_id=height_frag,
            citation="Schedule 15",
            canonical={"max_height_m": 25.0},
        )
        _add_overlay_dataset(
            session,
            name="halifax_far_precincts",
            fragment_id=far_frag,
            citation="Schedule 17",
            canonical={"max_far": 3.5},
        )
        _add_overlay_dataset(
            session,
            name="halifax_heritage_districts",
            fragment_id=heritage_frag,
            citation="Schedule 22",
            canonical={"district_name": "Schmidtville", "district_status": "Active"},
        )

        # Geocode cache: civic:100 robie st -> the test point. status must be
        # "linked" for the cache short-circuit to return a ResolvedLocation.
        session.add(
            GeocodeCache(
                normalized_text="civic:100 robie st",
                raw_text="100 Robie Street",
                kind="civic_address",
                status="linked",
                resolver="test_seed",
                geometry_geojson=TEST_POINT,
                confidence=1.0,
                detail=None,
                metadata_json={},
            )
        )

    return db_url


def test_get_address_profile_returns_dto_for_known_address(seeded_db: str) -> None:
    """AC-3.2 — a known address yields an AddressProfile with the seeded zone
    and a non-empty citation list."""
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_address_profile("100 Robie Street")

    assert isinstance(profile, AddressProfile)
    assert profile.unresolvable is False
    assert profile.zone == "HR-2"
    assert profile.civic_number == "100"
    assert profile.street == "Robie Street"
    assert profile.citations, "expected at least one citation"
    # The zone citation must point at the zoning schedule fragment.
    zone_citations = [c for c in profile.citations if "zone" in c.backs]
    assert zone_citations
    assert zone_citations[0].citation_path == "zoning_schedule"


def test_get_address_profile_zone_matches_existing_evaluator(seeded_db: str) -> None:
    """AC-3.3 — regression guard against spatial-logic divergence.

    The evaluator resolves a zone by calling ``RetrievalService.search`` with
    the location slot and reading the zoning dataset's spatial feature match.
    ``get_address_profile`` must resolve the SAME zone for the same address;
    if the two spatial paths ever diverge this fails.
    """
    slot = LocationSlot(civic_number="100", street="Robie Street")
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_address_profile("100 Robie Street")

        response = service.search(
            RetrievalRequest(
                query="zone", location=slot, include_datasets=True, limit=10
            )
        )

    # Pull the zone the shared spatial channel resolved from the zoning
    # dataset's feature match.
    evaluator_zone = None
    for match in response.matches:
        for dataset in match.linked_datasets:
            for feature in dataset.feature_matches:
                if "zone_code" in feature.canonical_attributes:
                    evaluator_zone = feature.canonical_attributes["zone_code"]
    assert evaluator_zone == "HR-2"
    assert profile.zone == evaluator_zone


def test_get_address_profile_unresolvable_returns_null_dto(seeded_db: str) -> None:
    """AC-3.4 — a clearly-bogus address returns unresolvable=True, never raises."""
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_address_profile("asdf qwerty")

    assert isinstance(profile, AddressProfile)
    assert profile.unresolvable is True
    assert profile.zone is None
    assert profile.citations == []
    assert profile.overlays == []


def test_get_address_profile_overlays_populated(seeded_db: str) -> None:
    """AC-3.5 — heritage and FAR precinct fields populate when the spatial
    data carries them for the address."""
    with session_scope(seeded_db) as session:
        service = RetrievalService(session)
        profile = service.get_address_profile("100 Robie Street")

    assert profile.far_precinct == "FA-3.5"
    assert profile.height_precinct == "HP-25"
    assert profile.heritage is True

    kinds = {o.kind for o in profile.overlays}
    assert {"zone", "height_precinct", "far_precinct", "heritage"} <= kinds
    # Every overlay that contributed a value carries a citation.
    assert len(profile.citations) == len(profile.overlays)
    heritage_overlay = next(o for o in profile.overlays if o.kind == "heritage")
    assert heritage_overlay.label == "Schmidtville"
    assert heritage_overlay.citation == "Schedule 22"
