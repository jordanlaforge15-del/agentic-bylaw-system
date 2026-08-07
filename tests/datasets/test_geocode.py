from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import Document, GeocodeCache, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2
from sqlalchemy import select

from layer2.retrieval.geocode import (
    _cache_put,
    normalize_reference,
    normalize_street,
    resolve_location,
)
from layer2.retrieval.location import LocationReference
from layer2.retrieval.spatial import expand_spatial, query_features
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment
from layer2.retrieval.datasets import expand_datasets


HEIGHT_CONFIG = """
name: mini_height_precincts_e
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
  feature_key: GlobalID
  canonical:
    max_height_m: { from: MAXBLDHGT, type: float, optional: true }
    max_height_storeys: { from: MAXBLDSTRY, type: int, optional: true }
  ignore: [OBJECTID, SACC]
"""

CIVIC_CONFIG = """
name: mini_civic_addresses
publisher: Test
format: geojson
source_path: tests/fixtures/geo/mini_civic_addresses.geojson
crs: EPSG:4326
role: civic_address
attributes:
  feature_key: ADDR_ID
  canonical:
    civic_number: { from: CIVIC_NUMBER, type: string }
    street_name: { from: STREET_NAME, type: string }
    parcel_id: { from: PID, type: string }
"""


@pytest.fixture()
def both_datasets(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax Regional Municipality",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="/synthetic.pdf",
            file_hash="cafefade" * 8,
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

    height_yaml = tmp_path / "height.yaml"
    height_yaml.write_text(HEIGHT_CONFIG)
    civic_yaml = tmp_path / "civic.yaml"
    civic_yaml.write_text(CIVIC_CONFIG)

    with session_scope(db_url) as session:
        height_result = ingest_geo_dataset(session, height_yaml)
        civic_result = ingest_geo_dataset(session, civic_yaml)
        height_id = height_result.dataset.id
        civic_id = civic_result.dataset.id
        # Civic is role-bearing — must not be linked or counted as orphan.
        assert civic_result.link_result.status == "not_applicable"

    return {
        "db_url": db_url,
        "document_id": document_id,
        "fragment_id": fragment_id,
        "height_dataset_id": height_id,
        "civic_dataset_id": civic_id,
    }


def test_civic_address_dataset_is_not_an_orphan(both_datasets):
    from layer1.datasets.linker import find_orphan_datasets
    with session_scope(both_datasets["db_url"]) as session:
        orphans = find_orphan_datasets(session)
        assert orphans == []


def test_normalize_street_unifies_suffix_variants():
    assert normalize_street("Barrington Street") == "barrington st"
    assert normalize_street("Barrington St.") == "barrington st"
    assert normalize_street("Barrington St") == "barrington st"
    assert normalize_street("  Barrington   street  ") == "barrington st"
    assert normalize_street("Spring Garden Road") == "spring garden rd"
    assert normalize_street(None) == ""


def test_normalize_reference_keys_are_stable():
    a = LocationReference(raw_text="1234 Barrington Street", kind="civic_address",
                           civic_number="1234", street="Barrington Street")
    b = LocationReference(raw_text="  1234   Barrington st.  ", kind="civic_address",
                           civic_number="1234", street="Barrington st.")
    assert normalize_reference(a) == normalize_reference(b)


def test_resolve_civic_address_hits_dataset(both_datasets):
    ref = LocationReference(
        raw_text="1234 Barrington Street",
        kind="civic_address",
        civic_number="1234",
        street="Barrington Street",
    )
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
    assert resolved is not None
    assert resolved.kind == "point"
    assert resolved.geometry["type"] == "Point"
    assert resolved.source == "mini_civic_addresses"
    assert resolved.geometry["coordinates"] == [-63.59, 44.65]


def test_resolve_civic_address_handles_suffix_variants(both_datasets):
    ref = LocationReference(
        raw_text="999 Main Street",
        kind="civic_address",
        civic_number="999",
        # Dataset stores "Main St"; query says "Main Street". Normalization should match.
        street="Main Street",
    )
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
    assert resolved is not None
    assert resolved.geometry["coordinates"] == [-63.55, 44.65]


def test_resolve_civic_address_miss_returns_none(both_datasets):
    ref = LocationReference(
        raw_text="9999 Nowhere Lane",
        kind="civic_address",
        civic_number="9999",
        street="Nowhere Lane",
    )
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
    assert resolved is None
    # Cache records the miss:
    with session_scope(both_datasets["db_url"]) as session:
        cached = session.query(GeocodeCache).filter_by(
            normalized_text=normalize_reference(ref)
        ).one()
        assert cached.status == "no_match"


def test_resolve_parcel_id_hits_dataset(both_datasets):
    ref = LocationReference(
        raw_text="PID 00000002", kind="parcel_id", parcel_id="00000002"
    )
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
    assert resolved is not None
    assert resolved.geometry["coordinates"] == [-63.57, 44.65]


def test_named_place_returns_none_in_v1(both_datasets):
    ref = LocationReference(raw_text="Halifax Citadel", kind="named_place", name="Halifax Citadel")
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
    assert resolved is None


def test_cache_hit_short_circuits_dataset_lookup(both_datasets):
    ref = LocationReference(
        raw_text="1234 Barrington Street",
        kind="civic_address",
        civic_number="1234",
        street="Barrington Street",
    )
    with session_scope(both_datasets["db_url"]) as session:
        first = resolve_location(session, ref)
        assert first is not None

    # Now corrupt the underlying dataset so a fresh lookup would fail —
    # if the cache is honoured, the second call still returns the same point.
    with session_scope(both_datasets["db_url"]) as session:
        from layer1.db.base import ExternalDatasetFeature
        for feature in session.query(ExternalDatasetFeature).all():
            feature.canonical_attributes_json = {"civic_number": "0", "street_name": "wrong"}
        session.flush()

    with session_scope(both_datasets["db_url"]) as session:
        second = resolve_location(session, ref)
    assert second is not None
    assert second.geometry["coordinates"] == first.geometry["coordinates"]


def _winning_cache_row(normalized_text: str, ref: LocationReference) -> GeocodeCache:
    return GeocodeCache(
        normalized_text=normalized_text,
        raw_text=ref.raw_text,
        kind=ref.kind,
        status="no_match",
        resolver="miss",
        detail="committed by the race winner",
        metadata_json={"reference": ref.model_dump()},
        created_at=datetime.now(timezone.utc),
    )


def test_cache_put_recovers_from_lost_write_race(both_datasets, monkeypatch):
    """ABS-422: the loser of a geocode-cache write race must not 500.

    Two concurrent requests geocode the same not-yet-cached address. The
    winner inserts and commits ``normalized_text`` first; the loser's own
    check-then-insert already read a miss, so its INSERT trips
    ``uq_geocode_cache_normalized_text``. Before the fix this
    ``UniqueViolation`` poisoned the session and cascaded into a
    ``PendingRollbackError`` 500. Now ``_cache_put`` inserts inside a
    SAVEPOINT, catches the ``IntegrityError``, rolls back only the nested
    unit, re-reads the winning row and refreshes it — no raise, one row,
    session still usable.

    The race window is simulated deterministically: the FIRST cache read
    (the check) misses as it would before the winner committed, while the
    recovery re-read sees the committed winner.
    """
    import layer2.retrieval.geocode as geocode_module

    db_url = both_datasets["db_url"]
    ref = LocationReference(
        raw_text="4321 Contested Street",
        kind="civic_address",
        civic_number="4321",
        street="Contested Street",
    )
    key = normalize_reference(ref)

    # Winner commits the cache row in its own transaction first.
    with session_scope(db_url) as winner:
        winner.add(_winning_cache_row(key, ref))

    real_get_row = geocode_module._cache_get_row
    calls = {"n": 0}

    def flaky_get_row(session, normalized_text):
        calls["n"] += 1
        # First read = the check-then-insert probe: the winner's commit is
        # not yet visible, so it misses. Later reads see the real DB.
        if calls["n"] == 1:
            return None
        return real_get_row(session, normalized_text)

    monkeypatch.setattr(geocode_module, "_cache_get_row", flaky_get_row)

    with session_scope(db_url) as loser:
        # Must not raise even though the INSERT trips the unique constraint.
        _cache_put(
            loser,
            normalized_text=key,
            ref=ref,
            resolved=None,
            resolver="miss",
            status="no_match",
            detail="loser refreshed the winning row",
            source_dataset_id=None,
            source_feature_id=None,
        )
        assert calls["n"] >= 2, "recovery re-read should have run"
        # The session survives the absorbed conflict — unrelated work in the
        # same request keeps working rather than hitting PendingRollbackError.
        loser.execute(select(GeocodeCache)).scalars().all()

    # Exactly one row survives and the loser's payload was reconciled onto it.
    with session_scope(db_url) as s:
        rows = s.execute(
            select(GeocodeCache).where(GeocodeCache.normalized_text == key)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].detail == "loser refreshed the winning row"


def test_cache_put_updates_existing_row_without_savepoint(both_datasets):
    """The common no-conflict path still updates a pre-existing row in place
    (regression guard for the refactor that routes both reads through
    ``_cache_get_row``)."""
    db_url = both_datasets["db_url"]
    ref = LocationReference(
        raw_text="10 Update Street",
        kind="civic_address",
        civic_number="10",
        street="Update Street",
    )
    key = normalize_reference(ref)
    with session_scope(db_url) as s:
        _cache_put(
            s, normalized_text=key, ref=ref, resolved=None, resolver="miss",
            status="no_match", detail="first", source_dataset_id=None,
            source_feature_id=None,
        )
    with session_scope(db_url) as s:
        _cache_put(
            s, normalized_text=key, ref=ref, resolved=None, resolver="miss",
            status="no_match", detail="second", source_dataset_id=None,
            source_feature_id=None,
        )
    with session_scope(db_url) as s:
        rows = s.execute(
            select(GeocodeCache).where(GeocodeCache.normalized_text == key)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].detail == "second"


def test_resolved_location_intersects_height_precinct(both_datasets):
    """End-to-end: resolve an address to a point, then intersect against
    the height precinct dataset and confirm the matched feature."""
    ref = LocationReference(
        raw_text="1234 Barrington Street",
        kind="civic_address",
        civic_number="1234",
        street="Barrington Street",
    )
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
        assert resolved is not None
        matches = query_features(
            session, dataset_id=both_datasets["height_dataset_id"], location=resolved
        )
    assert len(matches) == 1
    assert matches[0].feature.canonical_attributes_json["max_height_m"] == 25.0


def test_full_pipeline_address_to_dataset_feature_candidate(both_datasets):
    """Compose the full Phase D + E flow: location ref → resolved → expand."""
    ref = LocationReference(
        raw_text="1234 Barrington Street",
        kind="civic_address",
        civic_number="1234",
        street="Barrington Street",
    )
    with session_scope(both_datasets["db_url"]) as session:
        resolved = resolve_location(session, ref)
        candidates = [
            CandidateFragment(
                source_fragment_id=both_datasets["fragment_id"],
                source_type=SourceType.FRAGMENT.value,
                retrieval_channel=RetrievalChannel.FULL_TEXT.value,
                base_score=0.8,
                text="Schedule 15 prose.",
            )
        ]
        with_dataset = expand_datasets(session, candidates)
        with_features = expand_spatial(session, with_dataset, location=resolved)

    feature_candidates = [
        c for c in with_features if c.source_type == SourceType.DATASET_FEATURE.value
    ]
    assert len(feature_candidates) == 1
    assert "25" in feature_candidates[0].text  # 25m precinct
    assert feature_candidates[0].reason["location_source"] == "mini_civic_addresses"
