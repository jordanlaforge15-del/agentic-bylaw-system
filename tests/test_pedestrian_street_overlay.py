"""ABS-350 — the ``pedestrian_street`` overlay role (Schedule 7 POCS).

Schedule 7 of the Regional Centre Land Use By-Law designates
pedestrian-oriented commercial streets. Whether a lot abuts one flips the
ground-floor-use answer: s.38(2) prohibits ground-floor office on a POCS
street, s.69(d) permits it otherwise — the exact fact the dev-DB answer for
6184 Quinpool Rd (advisor_question_purchase id 3) could not establish.

Unlike the precinct overlays (zoning, height, FAR, heritage — area polygons
tested point-in-polygon) the POCS layer is LINE geometry: a geocoded civic
point never lands on a street centreline, so the retrieval service must use an
*abuts* predicate (nearest designated segment within a buffer). These tests
build a synthetic Halifax-shaped corpus on sqlite — the Regional Centre doc, a
Schedule 7 fragment, a POCS line dataset with a Quinpool corridor + a control
Gottingen corridor, and geocode-cache rows resolving the ticket's address
(~10 m off the Quinpool centreline) and a far-away control.
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


# The Quinpool corridor runs at ~constant latitude here. The ticket address
# sits ~10 m north of the centreline so a zero-radius point misses and only the
# buffered (abuts) query intersects — exercising the buffer, not a trivial hit.
_QUINPOOL_LAT = 44.64610
_QUINPOOL_LINE = {
    "type": "LineString",
    "coordinates": [
        [-63.6100, _QUINPOOL_LAT],
        [-63.6070, _QUINPOOL_LAT],
        [-63.6040, _QUINPOOL_LAT],
    ],
}
_QUINPOOL_POINT = {"type": "Point", "coordinates": [-63.6070, _QUINPOOL_LAT + 0.00009]}

_GOTTINGEN_LINE = {
    "type": "LineString",
    "coordinates": [[-63.5864, 44.6524], [-63.5879, 44.6579]],
}

# Far from every designated corridor — the definitive-negative control.
_CONTROL_POINT = {"type": "Point", "coordinates": [-63.5500, 44.6800]}


def _bbox(geom: dict) -> dict:
    xs = [c[0] for c in geom["coordinates"]]
    ys = [c[1] for c in geom["coordinates"]]
    return {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}


def _add_fragment(session, *, document_id: int, label: str, path: str) -> int:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label=label,
        citation_path=path,
        page_start=27,
        page_end=27,
        text=f"{label}: Pedestrian-Oriented Commercial Streets.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment.id


def _add_pocs_dataset(session, *, fragment_id: int) -> int:
    dataset = ExternalDataset(
        name="halifax_pedestrian_oriented_commercial_streets",
        publisher="HRM",
        format="geojson",
        content_hash="hash-pocs",
        crs="EPSG:4326",
        feature_count=2,
        linked_fragment_id=fragment_id,
        linked_fragment_citation="Schedule 7",
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(dataset)
    session.flush()
    for key, street, geom in (
        ("S7-QUINPOOL", "Quinpool Road", _QUINPOOL_LINE),
        ("S7-GOTTINGEN", "Gottingen Street", _GOTTINGEN_LINE),
    ):
        session.add(
            ExternalDatasetFeature(
                external_dataset_id=dataset.id,
                feature_key=key,
                attributes_json={},
                canonical_attributes_json={"street_name": street},
                geometry_geojson=geom,
                geometry_bbox_json=_bbox(geom),
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )
    session.flush()
    return dataset.id


def _add_geocode(session, *, normalized: str, raw: str, point: dict) -> None:
    session.add(
        GeocodeCache(
            normalized_text=normalized,
            raw_text=raw,
            kind="civic_address",
            status="linked",
            resolver="test_seed",
            geometry_geojson=point,
            confidence=0.95,
            detail=None,
            metadata_json={},
        )
    )


def _base_corpus(session) -> int:
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
    _add_geocode(
        session,
        normalized="civic:6184 quinpool rd",
        raw="6184 Quinpool Road",
        point=_QUINPOOL_POINT,
    )
    _add_geocode(
        session,
        normalized="civic:500 nowhere rd",
        raw="500 Nowhere Road",
        point=_CONTROL_POINT,
    )
    return document.id


@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    """DB with the Schedule 7 POCS dataset in scope."""
    db_url = f"sqlite:///{tmp_path / 'pocs.db'}"
    create_layer1(db_url)
    create_layer2(db_url)
    with session_scope(db_url) as session:
        document_id = _base_corpus(session)
        frag = _add_fragment(session, document_id=document_id, label="Schedule 7", path="schedule_7")
        _add_pocs_dataset(session, fragment_id=frag)
    return db_url


@pytest.fixture()
def seeded_db_no_pocs(tmp_path: Path) -> str:
    """DB with the address resolvable but NO POCS dataset in scope."""
    db_url = f"sqlite:///{tmp_path / 'no_pocs.db'}"
    create_layer1(db_url)
    create_layer2(db_url)
    with session_scope(db_url) as session:
        _base_corpus(session)
    return db_url


def test_abuts_pedestrian_street_true_for_designated_address(seeded_db: str) -> None:
    """AC — the ticket's address abuts a Schedule 7 POCS street: definitive True
    with a Schedule 7 citation and the corridor name."""
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("6184 Quinpool Road")

    assert isinstance(profile, AddressProfile)
    assert profile.unresolvable is False
    assert profile.abuts_pedestrian_street is True

    pocs = [o for o in profile.overlays if o.kind == "pedestrian_street"]
    assert pocs, "expected a pedestrian_street overlay"
    assert pocs[0].label == "Quinpool Road"
    assert pocs[0].citation == "Schedule 7"
    # A citation backs the pedestrian_street facet, tracing to the schedule.
    pocs_citations = [c for c in profile.citations if "pedestrian_street" in c.backs]
    assert pocs_citations
    assert pocs_citations[0].citation_path == "schedule_7"


def test_non_designated_address_is_definitive_false(seeded_db: str) -> None:
    """AC — an address off every corridor returns a definitive False (not
    missing/None), so the agent applies s.69(d) instead of hedging."""
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("500 Nowhere Road")

    assert profile.unresolvable is False
    assert profile.abuts_pedestrian_street is False
    assert not [o for o in profile.overlays if o.kind == "pedestrian_street"]


def test_no_pocs_dataset_in_scope_yields_none(seeded_db_no_pocs: str) -> None:
    """Without a Schedule 7 dataset in scope the field is None (unknown), never
    a spurious False."""
    with session_scope(seeded_db_no_pocs) as session:
        profile = RetrievalService(session).get_address_profile("6184 Quinpool Road")

    assert profile.unresolvable is False
    assert profile.abuts_pedestrian_street is None


def test_search_linked_datasets_surfaces_pocs_abut(seeded_db: str) -> None:
    """search_bylaw_evidence surfaces the abutting POCS segment under
    linked_datasets when a location slot is set — the same abuts predicate the
    profile uses, so the evaluator path and the thick tool never diverge."""
    slot = LocationSlot(civic_number="6184", street="Quinpool Road")
    with session_scope(seeded_db) as session:
        response = RetrievalService(session).search(
            RetrievalRequest(
                query="ground floor use pedestrian street",
                location=slot,
                include_datasets=True,
                limit=10,
            )
        )

    streets = [
        feature.canonical_attributes.get("street_name")
        for match in response.matches
        for dataset in match.linked_datasets
        for feature in dataset.feature_matches
    ]
    assert "Quinpool Road" in streets


def test_control_address_surfaces_no_pocs_in_search(seeded_db: str) -> None:
    """Negative control: the far-away address abuts nothing, so no POCS feature
    match surfaces in the linked datasets."""
    slot = LocationSlot(civic_number="500", street="Nowhere Road")
    with session_scope(seeded_db) as session:
        response = RetrievalService(session).search(
            RetrievalRequest(
                query="ground floor use pedestrian street",
                location=slot,
                include_datasets=True,
                limit=10,
            )
        )

    streets = [
        feature.canonical_attributes.get("street_name")
        for match in response.matches
        for dataset in match.linked_datasets
        for feature in dataset.feature_matches
    ]
    assert "Quinpool Road" not in streets
    assert "Gottingen Street" not in streets


# --- Direct spatial-predicate coverage (abuts buffer boundary) -------------
from sqlalchemy import select as _select  # noqa: E402

from layer2.retrieval.spatial import (  # noqa: E402
    DEFAULT_ABUT_DISTANCE_M,
    ResolvedLocation,
    query_features,
)


def _pocs_dataset_id(session) -> int:
    return session.execute(
        _select(ExternalDataset.id).where(
            ExternalDataset.name == "halifax_pedestrian_oriented_commercial_streets"
        )
    ).scalars().one()


def _point_north_of_quinpool(metres: float) -> dict:
    # ~111_320 m per degree of latitude; offset the point straight north of the
    # constant-latitude Quinpool centreline so the abut distance ≈ ``metres``.
    return {
        "type": "Point",
        "coordinates": [-63.6070, _QUINPOOL_LAT + metres / 111_320.0],
    }


def test_abuts_predicate_matches_within_buffer(seeded_db: str) -> None:
    """A point ~25 m off the centreline (inside the 30 m default buffer) abuts
    the segment; the nearest-first ordering names the Quinpool corridor."""
    location = ResolvedLocation(kind="point", geometry=_point_north_of_quinpool(25.0))
    with session_scope(seeded_db) as session:
        matches = query_features(
            session,
            dataset_id=_pocs_dataset_id(session),
            location=location,
            predicate="abuts",
        )
        assert matches, "expected an abut match within the buffer"
        assert matches[0].feature.canonical_attributes_json["street_name"] == "Quinpool Road"
        assert matches[0].contains_input is True


def test_abuts_predicate_excludes_beyond_buffer(seeded_db: str) -> None:
    """A point ~40 m off the centreline (outside the 30 m buffer) does not
    abut — the predicate is a real-metre distance test, not a bbox hit."""
    assert DEFAULT_ABUT_DISTANCE_M == 30.0
    location = ResolvedLocation(kind="point", geometry=_point_north_of_quinpool(40.0))
    with session_scope(seeded_db) as session:
        matches = query_features(
            session,
            dataset_id=_pocs_dataset_id(session),
            location=location,
            predicate="abuts",
        )
    assert matches == []


def test_intersects_predicate_misses_line_point(seeded_db: str) -> None:
    """The default intersects predicate reports a spurious negative for a point
    ~10 m off a line — proving why the POCS overlay needs the abuts test."""
    location = ResolvedLocation(kind="point", geometry=_QUINPOOL_POINT)
    with session_scope(seeded_db) as session:
        matches = query_features(
            session,
            dataset_id=_pocs_dataset_id(session),
            location=location,
            predicate="intersects",
        )
    assert matches == []
