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


# --- ABS-435: abutment measured from the parcel, not the rooftop point ------
#
# A civic geocode returns a rooftop/centroid point, so the point-to-centreline
# distance is dominated by lot depth rather than by whether the lot fronts the
# street. Over the HRM parcels along Quinpool Road the distance runs 0.1–283 m
# for parcels that genuinely front it and starts at 26.5 m for parcels that do
# not — the populations overlap, so no point threshold separates them. That is
# how 6321 Quinpool Rd (36.7 m out, squarely on the corridor) reported
# abuts_pedestrian_street=false. Measured from the parcel boundary the question
# IS separable: the front lot line sits on the right-of-way edge.
#
# These two fixtures pin both directions of that change on one corpus:
#   deep lot  — rooftop 37 m out, front lot line 9 m out  -> True  (was False)
#   back lot  — rooftop 28 m out, front lot line 25 m out -> False (was True)
# The back lot is the guard that makes this a measurement fix and not a
# threshold bump: simply widening the point buffer to catch the deep lot would
# have started reporting the back lot as abutting.

_DEEP_LOT_ADDRESS = "6321 Quinpool Road"
_DEEP_LOT_NORMALIZED = "civic:6321 quinpool rd"
_DEEP_LOT_LON = -63.6055

_BACK_LOT_ADDRESS = "12 Backlot Lane"
_BACK_LOT_NORMALIZED = "civic:12 backlot ln"
_BACK_LOT_LON = -63.6045

_M_PER_DEG_LAT = 111_320.0
# Half-width of the synthetic lots, in degrees of longitude at Quinpool's
# latitude. Narrow enough that the two lots never overlap each other or the
# 6184 Quinpool fixture point.
_LOT_HALF_WIDTH_DEG = 15.0 / (_M_PER_DEG_LAT * 0.712)


def _lat_north_of_quinpool(metres: float) -> float:
    return _QUINPOOL_LAT + metres / _M_PER_DEG_LAT


def _lot_polygon(lon: float, *, front_m: float, rear_m: float) -> dict:
    """A rectangular lot fronting the Quinpool corridor.

    ``front_m`` / ``rear_m`` are the front and rear lot lines' distances north
    of the (constant-latitude) centreline.
    """
    west, east = lon - _LOT_HALF_WIDTH_DEG, lon + _LOT_HALF_WIDTH_DEG
    south, north = _lat_north_of_quinpool(front_m), _lat_north_of_quinpool(rear_m)
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def _polygon_bbox(geom: dict) -> dict:
    ring = geom["coordinates"][0]
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}


def _add_parcels_dataset(session) -> int:
    """A parcel fabric tagged role=property_parcels, the way HRM's is."""
    dataset = ExternalDataset(
        name="halifax_property_parcels",
        publisher="HRM",
        format="geojson",
        content_hash="hash-parcels",
        crs="EPSG:4326",
        feature_count=2,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={"role": "property_parcels"},
    )
    session.add(dataset)
    session.flush()
    for key, geom in (
        ("PID-DEEP", _lot_polygon(_DEEP_LOT_LON, front_m=9.0, rear_m=49.0)),
        ("PID-BACK", _lot_polygon(_BACK_LOT_LON, front_m=25.0, rear_m=65.0)),
    ):
        session.add(
            ExternalDatasetFeature(
                external_dataset_id=dataset.id,
                feature_key=key,
                attributes_json={},
                canonical_attributes_json={"parcel_id": key},
                geometry_geojson=geom,
                geometry_bbox_json=_polygon_bbox(geom),
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )
    session.flush()
    return dataset.id


@pytest.fixture()
def seeded_db_with_parcels(tmp_path: Path) -> str:
    """POCS corpus plus a parcel fabric and the two lot-depth fixtures."""
    db_url = f"sqlite:///{tmp_path / 'pocs_parcels.db'}"
    create_layer1(db_url)
    create_layer2(db_url)
    with session_scope(db_url) as session:
        document_id = _base_corpus(session)
        frag = _add_fragment(
            session, document_id=document_id, label="Schedule 7", path="schedule_7"
        )
        _add_pocs_dataset(session, fragment_id=frag)
        _add_parcels_dataset(session)
        _add_geocode(
            session,
            normalized=_DEEP_LOT_NORMALIZED,
            raw=_DEEP_LOT_ADDRESS,
            point={
                "type": "Point",
                "coordinates": [_DEEP_LOT_LON, _lat_north_of_quinpool(37.0)],
            },
        )
        _add_geocode(
            session,
            normalized=_BACK_LOT_NORMALIZED,
            raw=_BACK_LOT_ADDRESS,
            point={
                "type": "Point",
                "coordinates": [_BACK_LOT_LON, _lat_north_of_quinpool(28.0)],
            },
        )
    return db_url


def test_deep_lot_on_corridor_abuts_via_parcel(seeded_db_with_parcels: str) -> None:
    """AC — 6321 Quinpool Rd reports abuts_pedestrian_street=true.

    The rooftop point is 37 m from the centreline, beyond DEFAULT_ABUT_DISTANCE_M,
    but the lot fronts the corridor 9 m out. This is the ABS-435 false negative.
    """
    with session_scope(seeded_db_with_parcels) as session:
        profile = RetrievalService(session).get_address_profile(_DEEP_LOT_ADDRESS)

    assert profile.unresolvable is False
    assert profile.abuts_pedestrian_street is True
    pocs = [o for o in profile.overlays if o.kind == "pedestrian_street"]
    assert pocs and pocs[0].label == "Quinpool Road"


def test_deep_lot_would_miss_on_the_rooftop_point_alone(
    seeded_db_with_parcels: str,
) -> None:
    """Pins WHY the fix is the parcel and not the geometry alone: even against
    the corrected corridor the rooftop point is outside the point buffer."""
    location = ResolvedLocation(
        kind="point",
        geometry={
            "type": "Point",
            "coordinates": [_DEEP_LOT_LON, _lat_north_of_quinpool(37.0)],
        },
    )
    with session_scope(seeded_db_with_parcels) as session:
        matches = query_features(
            session,
            dataset_id=_pocs_dataset_id(session),
            location=location,
            predicate="abuts",
            abut_distance_m=DEFAULT_ABUT_DISTANCE_M,
        )
    assert matches == []


def test_back_lot_within_the_point_buffer_does_not_abut(
    seeded_db_with_parcels: str,
) -> None:
    """The guard against a naive threshold bump.

    This lot's rooftop point is 28 m from the corridor — inside the 30 m point
    buffer — but its front lot line is 25 m out, so it does not front the
    designated street. Measuring from the parcel keeps it a definitive False.
    """
    with session_scope(seeded_db_with_parcels) as session:
        profile = RetrievalService(session).get_address_profile(_BACK_LOT_ADDRESS)

    assert profile.unresolvable is False
    assert profile.abuts_pedestrian_street is False
    assert not [o for o in profile.overlays if o.kind == "pedestrian_street"]


def test_parcel_upgrade_is_skipped_without_a_parcel_fabric(seeded_db: str) -> None:
    """No parcels dataset in scope — the point path still answers, and the
    shallow-lot fixture (6184 Quinpool, 10 m out) stays True."""
    with session_scope(seeded_db) as session:
        profile = RetrievalService(session).get_address_profile("6184 Quinpool Road")

    assert profile.abuts_pedestrian_street is True
