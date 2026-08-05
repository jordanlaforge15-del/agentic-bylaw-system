"""ABS-356 — corpus-coherence audit.

When a linked geo dataset falls out of retrieval scope, ``get_address_profile``
degrades silently: the affected overlay comes back ``None`` and a paid answer
hedges instead of citing a schedule. ``audit_corpus_coherence`` asserts every
overlay role a dataset config declares is actually visible through
``scoped_linked_datasets`` for its bylaw, and classifies a miss into exactly
one of three modes established by the ABS-349/ABS-350 postmortem and
``layer1.datasets.linker``'s existing vocabulary:

* ``unlinked``  — no dataset with the declared name was ever ingested.
* ``orphaned``  — the dataset exists but was never resolved to a fragment.
* ``evicted``   — the dataset is linked, but its document is outside the
  active retrieval scope (not retrieval-enabled — ABS-413).

These tests build a synthetic sqlite corpus (mirroring
``tests/test_get_address_profile.py``'s fixture style) with one dataset per
degradation mode, plus one fully-coherent role, so each mode is exercised in
isolation and in combination.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import (
    OverlayDeclaration,
    audit_corpus_coherence,
    audit_e2e_contamination,
    retrieval_enabled_resolver,
)
from bylaw_retrieval.retrieval.coherence_audit import (
    DEFAULT_DATASET_CONFIG_DIR,
    load_overlay_declarations,
)
from layer1.db.base import Document, ExternalDataset, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

MUNICIPALITY = "HRM"
BYLAW_NAME = "Regional Centre Land Use By-Law"


def _add_fragment(session, *, document_id: int, label: str, path: str) -> int:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.SCHEDULE,
        citation_label=label,
        citation_path=path,
        page_start=1,
        page_end=1,
        text=f"{label}.",
        parse_status=ParseStatus.PARSED,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment.id


@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    """A sqlite corpus with one dataset per degradation mode:

    * ``test_zoning_boundaries`` (role ``zone``) is linked only to the
      DISABLED of two same-bylaw documents — invisible once
      ``retrieval_enabled_resolver`` pins scope to the enabled one (evicted).
    * ``test_height_precincts`` (role ``height_precinct``) is linked to the
      ENABLED document — visible, the fully-coherent control.
    * ``test_heritage_districts`` (role ``heritage``) exists but was never
      linked to any fragment (orphaned).
    * No dataset is ever created for ``shadow_impact`` (unlinked).
    """
    db_url = f"sqlite:///{tmp_path / 'coherence.db'}"
    create_layer1(db_url)

    now = datetime.now(timezone.utc)
    with session_scope(db_url) as session:
        old_document = Document(
            municipality=MUNICIPALITY,
            bylaw_name=BYLAW_NAME,
            source_path="/old.pdf",
            file_hash="o" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=now - timedelta(days=1),
            page_count=10,
        )
        session.add(old_document)
        session.flush()

        new_document = Document(
            municipality=MUNICIPALITY,
            bylaw_name=BYLAW_NAME,
            source_path="/new.pdf",
            file_hash="n" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=now,
            page_count=10,
            retrieval_enabled=True,
        )
        session.add(new_document)
        session.flush()

        zone_fragment_id = _add_fragment(
            session, document_id=old_document.id, label="Zoning Schedule", path="zoning_schedule"
        )
        session.add(
            ExternalDataset(
                name="test_zoning_boundaries",
                format="geojson",
                content_hash="h-zone",
                crs="EPSG:4326",
                feature_count=1,
                linked_fragment_id=zone_fragment_id,
                linked_fragment_citation="Zoning Schedule",
                schema_mapping_json={},
                parse_status=ParseStatus.PARSED,
                metadata_json={"link_status": "linked"},
            )
        )

        height_fragment_id = _add_fragment(
            session, document_id=new_document.id, label="Schedule 15", path="schedule_15"
        )
        session.add(
            ExternalDataset(
                name="test_height_precincts",
                format="geojson",
                content_hash="h-height",
                crs="EPSG:4326",
                feature_count=1,
                linked_fragment_id=height_fragment_id,
                linked_fragment_citation="Schedule 15",
                schema_mapping_json={},
                parse_status=ParseStatus.PARSED,
                metadata_json={"link_status": "linked"},
            )
        )

        session.add(
            ExternalDataset(
                name="test_heritage_districts",
                format="geojson",
                content_hash="h-heritage",
                crs="EPSG:4326",
                feature_count=1,
                linked_fragment_id=None,
                linked_fragment_citation="Schedule 22",
                schema_mapping_json={},
                parse_status=ParseStatus.PARSED,
                metadata_json={"link_status": "no_fragment"},
            )
        )

    return db_url


ZONE_DECLARATION = OverlayDeclaration(
    dataset_name="test_zoning_boundaries",
    municipality=MUNICIPALITY,
    bylaw_name=BYLAW_NAME,
    fragment_citation="Zoning Schedule",
)
HEIGHT_DECLARATION = OverlayDeclaration(
    dataset_name="test_height_precincts",
    municipality=MUNICIPALITY,
    bylaw_name=BYLAW_NAME,
    fragment_citation="Schedule 15",
)
HERITAGE_DECLARATION = OverlayDeclaration(
    dataset_name="test_heritage_districts",
    municipality=MUNICIPALITY,
    bylaw_name=BYLAW_NAME,
    fragment_citation="Schedule 22",
)
SHADOW_DECLARATION = OverlayDeclaration(
    dataset_name="test_shadow_impact_areas",
    municipality=MUNICIPALITY,
    bylaw_name=BYLAW_NAME,
    fragment_citation="Schedule 51",
)


def test_coherent_when_the_declared_role_is_visible_in_scope(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[HEIGHT_DECLARATION],
            default_document_id_resolver=retrieval_enabled_resolver,
        )
    assert report.coherent is True
    assert report.missing == []
    assert report.checked_roles == 1
    assert report.bylaws_checked == 1


def test_detects_unlinked_dataset(seeded_db: str) -> None:
    """No ``ExternalDataset`` row exists at all for the declared name."""
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[SHADOW_DECLARATION],
            default_document_id_resolver=retrieval_enabled_resolver,
        )
    assert report.coherent is False
    assert len(report.missing) == 1
    entry = report.missing[0]
    assert entry.reason == "unlinked"
    assert entry.role == "shadow_impact"
    assert entry.dataset_name == "test_shadow_impact_areas"


def test_detects_orphaned_dataset(seeded_db: str) -> None:
    """The dataset exists but the linker never resolved it to a fragment."""
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[HERITAGE_DECLARATION],
            default_document_id_resolver=retrieval_enabled_resolver,
        )
    assert report.coherent is False
    entry = report.missing[0]
    assert entry.reason == "orphaned"
    assert entry.role == "heritage"


def test_detects_evicted_dataset(seeded_db: str) -> None:
    """Linked fine, but its document is not retrieval-enabled."""
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[ZONE_DECLARATION],
            default_document_id_resolver=retrieval_enabled_resolver,
        )
    assert report.coherent is False
    entry = report.missing[0]
    assert entry.reason == "evicted"
    assert entry.role == "zone"


def test_unscoped_audit_sees_the_evicted_dataset_again(seeded_db: str) -> None:
    """Proves the 'evicted' failure is a function of the scoping resolver,
    not some other misconfiguration: drop the resolver and the same dataset
    becomes visible."""
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[ZONE_DECLARATION],
            default_document_id_resolver=None,
        )
    assert report.coherent is True


def test_reports_every_missing_role_together(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[
                ZONE_DECLARATION,
                HEIGHT_DECLARATION,
                HERITAGE_DECLARATION,
                SHADOW_DECLARATION,
            ],
            default_document_id_resolver=retrieval_enabled_resolver,
        )
    assert report.coherent is False
    assert report.checked_roles == 4
    assert report.bylaws_checked == 1
    reasons = {entry.role: entry.reason for entry in report.missing}
    assert reasons == {"zone": "evicted", "heritage": "orphaned", "shadow_impact": "unlinked"}


def test_load_overlay_declarations_reads_the_real_dataset_configs() -> None:
    """The production source of truth: every overlay-role YAML under
    src/layer1/datasets/ maps to exactly one of the known roles, and
    role-bearing (non-overlay) datasets are excluded."""
    declarations = load_overlay_declarations(DEFAULT_DATASET_CONFIG_DIR)
    names = {d.dataset_name for d in declarations}

    assert "halifax_property_parcels" not in names
    assert "halifax_street_centerlines" not in names
    assert "halifax_zoning_boundaries" in names
    assert "halifax_pedestrian_oriented_commercial_streets" in names
    assert len(declarations) == 7
    for declaration in declarations:
        assert declaration.municipality
        assert declaration.bylaw_name
        assert declaration.fragment_citation


# ---------------------------------------------------------------------------
# ABS-432 — audit_e2e_contamination
# ---------------------------------------------------------------------------
#
# The tripwire behind the dev/e2e Postgres split (ABS-428) and the dev purge
# (ABS-429): any row bearing an e2e-suite fingerprint in a non-test database
# must be reported loudly. ``seeded_db`` above is deliberately marker-free
# (documents hashed 'o'*64/'n'*64, datasets named test_*) so it doubles as
# the clean baseline here.


def _add_marker_document(
    db_url: str, *, file_hash: str, parser_version: str | None
) -> int:
    with session_scope(db_url) as session:
        document = Document(
            municipality="Marker Town",
            bylaw_name="Marker Bylaw",
            source_path="/marker.pdf",
            file_hash=file_hash,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version=parser_version,
        )
        session.add(document)
        session.flush()
        return document.id


def test_e2e_contamination_clean_on_a_marker_free_corpus(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert report.contaminated is False
    assert report.markers == []
    assert report.marker_counts == {
        "document_parser_version": 0,
        "document_file_hash": 0,
        "external_dataset_name": 0,
    }


def test_e2e_contamination_flags_the_parser_version_marker(seeded_db: str) -> None:
    doc_id = _add_marker_document(seeded_db, file_hash="a" * 64, parser_version="e2e-seed")
    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert report.contaminated is True
    assert len(report.markers) == 1
    marker = report.markers[0]
    assert marker.table == "document"
    assert marker.row_id == doc_id
    assert marker.marker_kinds == ["document_parser_version"]
    assert "Marker Bylaw" in marker.detail
    assert report.marker_counts["document_parser_version"] == 1
    assert report.marker_counts["document_file_hash"] == 0


def test_e2e_contamination_flags_the_file_hash_marker(seeded_db: str) -> None:
    _add_marker_document(seeded_db, file_hash="e2e-sneaky-hash", parser_version="docling")
    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert report.contaminated is True
    assert report.markers[0].marker_kinds == ["document_file_hash"]
    assert report.marker_counts["document_file_hash"] == 1


def test_a_document_matching_both_markers_is_reported_once_with_both_kinds(
    seeded_db: str,
) -> None:
    doc_id = _add_marker_document(seeded_db, file_hash="e2e-double", parser_version="e2e-seed")
    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert len(report.markers) == 1
    assert report.markers[0].row_id == doc_id
    assert report.markers[0].marker_kinds == [
        "document_parser_version",
        "document_file_hash",
    ]
    assert report.marker_counts["document_parser_version"] == 1
    assert report.marker_counts["document_file_hash"] == 1


def test_e2e_contamination_flags_the_dataset_name_marker(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        session.add(
            ExternalDataset(
                name="e2e_zoning_boundaries",
                format="geojson",
                content_hash="h-e2e",
                crs="EPSG:4326",
                feature_count=1,
                schema_mapping_json={},
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )
    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert report.contaminated is True
    assert report.markers[0].table == "external_dataset"
    assert report.markers[0].marker_kinds == ["external_dataset_name"]
    assert "e2e_zoning_boundaries" in report.markers[0].detail


def test_dataset_marker_underscore_is_literal_not_a_wildcard(seeded_db: str) -> None:
    """`e2e_%` must not match `e2eX...` — the SQL underscore is escaped."""
    with session_scope(seeded_db) as session:
        session.add(
            ExternalDataset(
                name="e2eXnot_a_marker",
                format="geojson",
                content_hash="h-not-marker",
                crs="EPSG:4326",
                feature_count=1,
                schema_mapping_json={},
                parse_status=ParseStatus.PARSED,
                metadata_json={},
            )
        )
    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert report.contaminated is False


def test_e2e_contamination_goes_green_again_after_cleanup(seeded_db: str) -> None:
    """Both ways in one flow: red on the synthetic marker, green after delete."""
    doc_id = _add_marker_document(seeded_db, file_hash="e2e-cleanup-me", parser_version="e2e-seed")
    with session_scope(seeded_db) as session:
        assert audit_e2e_contamination(session).contaminated is True

    with session_scope(seeded_db) as session:
        session.delete(session.get(Document, doc_id))

    with session_scope(seeded_db) as session:
        report = audit_e2e_contamination(session)
    assert report.contaminated is False
    assert report.markers == []
