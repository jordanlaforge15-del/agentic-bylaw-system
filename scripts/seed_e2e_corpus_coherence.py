"""Seed a small, self-contained corpus for the corpus-coherence audit e2e spec.

Used by ``web/e2e/functional/corpus-coherence-audit.spec.ts`` (ABS-356) to
exercise ``audit_corpus_coherence`` against the real Postgres stack. Seeds
TWO throwaway bylaws — deliberately NOT "HRM / Regional Centre Land Use
By-Law", so this spec never collides with the address-profile / POCS seeds
sharing that partition:

* ``Corpus Coherence Test Bylaw`` — a zone dataset and a height_precinct
  dataset, both linked. Fully coherent: every role a config declares is
  visible in scope.
* ``Corpus Coherence Broken-Link Test Bylaw`` — the same two roles, but the
  height_precinct dataset's ``linked_fragment_id`` is nulled out after
  ingest, the exact "orphan" condition ``layer1.datasets.linker`` already has
  a name for.

Both bylaws are seeded deterministically on every run (Playwright's
``fullyParallel`` config runs this spec's tests concurrently across four
viewport projects sharing one Postgres DB, so the "broken" fixture is seeded
up front rather than mutated mid-test — no test races another test's view of
shared state).

Idempotent — datasets are dropped by name and re-ingested on every run, so
the seed's shape never drifts across repeated ``make e2e`` invocations
against the persistent ``layer1_test`` volume.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \\
        .venv/bin/python scripts/seed_e2e_corpus_coherence.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from layer1.db.base import Document, ExternalDataset, ExternalDatasetFeature, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset

_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.55, 44.60]}
_BOX = [
    [-63.56, 44.59],
    [-63.54, 44.59],
    [-63.54, 44.61],
    [-63.56, 44.61],
    [-63.56, 44.59],
]


def _overlay(name_suffix: str) -> list[dict[str, Any]]:
    # name carries the keyword overlay_role_for_name classifies on ("zoning"
    # -> zone, "height" -> height_precinct) — see bylaw_retrieval.retrieval.service.
    return [
        {
            "name": f"e2e_coherence_zoning_boundaries{name_suffix}",
            "citation": "Zoning Schedule",
            "feature_key_field": "GLOBALID",
            "properties": {"GLOBALID": f"coh-zone{name_suffix}-1", "ZONE": "CT-1"},
            "canonical": "    zone_code: { from: ZONE, type: string }\n",
        },
        {
            "name": f"e2e_coherence_height_precincts{name_suffix}",
            "citation": "Schedule 15",
            "feature_key_field": "GlobalID",
            "properties": {"GlobalID": f"coh-height{name_suffix}-1", "MAXBLDHGT": 20.0},
            "canonical": "    max_height_m: { from: MAXBLDHGT, type: float, optional: true }\n",
        },
    ]


# Bylaw A: fully coherent — both roles linked.
DOCUMENT_A_FILE_HASH = "e2e-corpus-coherence-doc-1"
DOCUMENT_A_MUNICIPALITY = "E2E Coherence Municipality"
DOCUMENT_A_BYLAW_NAME = "Corpus Coherence Test Bylaw"
OVERLAYS_A = _overlay("")

# Bylaw B: height_precinct is orphaned after ingest (linked_fragment_id nulled).
DOCUMENT_B_FILE_HASH = "e2e-corpus-coherence-doc-2"
DOCUMENT_B_MUNICIPALITY = "E2E Coherence Broken-Link Municipality"
DOCUMENT_B_BYLAW_NAME = "Corpus Coherence Broken-Link Test Bylaw"
OVERLAYS_B = _overlay("_broken")
ORPHAN_DATASET_NAME_B = OVERLAYS_B[1]["name"]  # height_precinct


def _polygon() -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [_BOX]}


def _feature_collection(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [{"type": "Feature", "geometry": _polygon(), "properties": props}],
    }


def _config_yaml(overlay: dict[str, Any], municipality: str, bylaw_name: str, geojson_path: Path) -> str:
    return (
        f"name: {overlay['name']}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match:\n"
        f"    municipality: {municipality}\n"
        f"    bylaw_name: {bylaw_name}\n"
        f"  fragment_citation: {overlay['citation']}\n"
        "attributes:\n"
        f"  feature_key: {overlay['feature_key_field']}\n"
        "  canonical:\n"
        f"{overlay['canonical']}"
    )


def _get_or_create_document(session, *, file_hash: str, municipality: str, bylaw_name: str) -> Document:
    document = session.execute(
        select(Document).where(Document.file_hash == file_hash)
    ).scalars().first()
    if document is not None:
        # Converge the publish flag on re-seed: rows created before
        # ABS-413 (or left disabled by the migration backfill) must
        # still end up retrieval-enabled in the persistent e2e DB.
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=municipality,
        bylaw_name=bylaw_name,
        source_path="e2e/corpus_coherence_bylaw.pdf",
        file_hash=file_hash,
        mime_type="application/pdf",
        page_count=100,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragments(session, *, document_id: int, overlays: list[dict[str, Any]]) -> None:
    page = 1
    for overlay in overlays:
        citation = overlay["citation"]
        existing = session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_label == citation,
            )
        ).scalars().first()
        if existing is not None:
            page += 1
            continue
        session.add(
            SourceFragment(
                document_id=document_id,
                fragment_type=FragmentType.SCHEDULE,
                citation_label=citation,
                citation_path=f"{citation.lower().replace(' ', '_')}_{document_id}",
                page_start=page,
                page_end=page,
                reading_order_start=page,
                text=f"{citation}.",
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        page += 1


def _drop_existing_dataset(session, name: str) -> None:
    existing = session.scalar(select(ExternalDataset).where(ExternalDataset.name == name))
    if existing is None:
        return
    session.query(ExternalDatasetFeature).filter(
        ExternalDatasetFeature.external_dataset_id == existing.id
    ).delete(synchronize_session=False)
    session.delete(existing)
    session.flush()


def _seed_bylaw(
    session,
    work_dir: Path,
    *,
    file_hash: str,
    municipality: str,
    bylaw_name: str,
    overlays: list[dict[str, Any]],
    orphan_dataset_name: str | None = None,
) -> dict[str, Any]:
    document = _get_or_create_document(
        session, file_hash=file_hash, municipality=municipality, bylaw_name=bylaw_name
    )
    _ensure_fragments(session, document_id=document.id, overlays=overlays)
    session.flush()

    linked = 0
    for overlay in overlays:
        _drop_existing_dataset(session, overlay["name"])
        geojson_path = work_dir / f"{overlay['name']}.geojson"
        geojson_path.write_text(json.dumps(_feature_collection(overlay["properties"])), encoding="utf-8")
        cfg_path = work_dir / f"{overlay['name']}.yaml"
        cfg_path.write_text(_config_yaml(overlay, municipality, bylaw_name, geojson_path), encoding="utf-8")
        result = ingest_geo_dataset(session, cfg_path)
        if result.link_result.status == "linked":
            linked += 1

    if orphan_dataset_name is not None:
        dataset = session.scalar(select(ExternalDataset).where(ExternalDataset.name == orphan_dataset_name))
        if dataset is None:
            raise SystemExit(f"cannot orphan {orphan_dataset_name!r}: dataset was not ingested")
        dataset.linked_fragment_id = None
        dataset.metadata_json = {**(dataset.metadata_json or {}), "link_status": "no_fragment"}
        session.flush()

    return {"document_id": document.id, "overlays_linked": linked}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        with session_scope() as session:
            # Serialise against concurrent Playwright workers, mirroring the
            # sibling seed scripts' advisory-lock pattern (ABS-207).
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=3560003560)
                )

            summary_a = _seed_bylaw(
                session,
                work_dir,
                file_hash=DOCUMENT_A_FILE_HASH,
                municipality=DOCUMENT_A_MUNICIPALITY,
                bylaw_name=DOCUMENT_A_BYLAW_NAME,
                overlays=OVERLAYS_A,
            )
            summary_b = _seed_bylaw(
                session,
                work_dir,
                file_hash=DOCUMENT_B_FILE_HASH,
                municipality=DOCUMENT_B_MUNICIPALITY,
                bylaw_name=DOCUMENT_B_BYLAW_NAME,
                overlays=OVERLAYS_B,
                orphan_dataset_name=ORPHAN_DATASET_NAME_B,
            )

    print(f"seed_e2e_corpus_coherence summary: {json.dumps({'coherent_bylaw': summary_a, 'broken_link_bylaw': summary_b})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
