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
* ``evicted``   — the dataset is linked, but its document fell outside the
  active retrieval scope (superseded by a newer ingest of the same bylaw).

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
    latest_per_bylaw_resolver,
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

    * ``test_zoning_boundaries`` (role ``zone``) is linked only to the OLDER
      of two same-bylaw documents — invisible once ``latest_per_bylaw_resolver``
      pins scope to the newer one (evicted).
    * ``test_height_precincts`` (role ``height_precinct``) is linked to the
      NEWER document — visible, the fully-coherent control.
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
            default_document_id_resolver=latest_per_bylaw_resolver,
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
            default_document_id_resolver=latest_per_bylaw_resolver,
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
            default_document_id_resolver=latest_per_bylaw_resolver,
        )
    assert report.coherent is False
    entry = report.missing[0]
    assert entry.reason == "orphaned"
    assert entry.role == "heritage"


def test_detects_evicted_dataset(seeded_db: str) -> None:
    """Linked fine, but its document lost the latest-per-bylaw race."""
    with session_scope(seeded_db) as session:
        report = audit_corpus_coherence(
            session,
            overlay_declarations=[ZONE_DECLARATION],
            default_document_id_resolver=latest_per_bylaw_resolver,
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
            default_document_id_resolver=latest_per_bylaw_resolver,
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
