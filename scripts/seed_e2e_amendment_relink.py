"""Seed the ABS-355 amendment re-link e2e scenario against Postgres/PostGIS.

Used by ``web/e2e/functional/abs355-amendment-relink.spec.ts`` to prove the
production twin of the ABS-349/350 regression: geo layers pin to a specific
document version at ingest time (``ExternalDataset.linked_fragment_id`` ->
``SourceFragment`` -> ``Document``), and retrieval scoping
(``retrieval_enabled_resolver`` + ``_scoped_linked_datasets``) only surfaces
layers whose pinned document is retrieval-enabled (ABS-413). So publishing an
amended version of a bylaw without moving its layers would silently evict
every existing layer and ``get_address_profile`` would start returning
``zone=null``.

This seed reproduces a real amendment and exercises the fix end-to-end through
PostGIS:

1. A v1 ``HRM / ABS-355 Amendment Bylaw`` document, retrieval-enabled, with a
   ``Zoning Schedule`` fragment.
2. A linked zone geo dataset (one polygon covering the test point), ingested
   through ``ingest_geo_dataset`` so the PostGIS ``geometry`` column is
   populated — not just ``geometry_geojson`` (the ABS-332/349 gotcha: the
   ST_Intersects path needs the real geometry column). At this point the layer
   is pinned to v1.
3. A v2 re-ingest of the same document (new file_hash, newer timestamp, its own
   ``Zoning Schedule`` fragment), seeded disabled — mirroring a real ingest —
   and then *published* via ``set_retrieval_enabled(..., replace=True)``, the
   exact production operation behind the CLI's ``enable-retrieval --replace``,
   which disables v1 and runs the relink pass that re-points the layer onto v2.

The spec then asserts ``get_address_profile`` still resolves the zone and that
its citations now reference the v2 document, not the evicted v1.

Prints a JSON summary line the spec parses for the v1/v2 document ids::

    seed_e2e_amendment_relink summary: {"v1_document_id": .., "v2_document_id": ..}

Idempotent — v1/v2 documents, their fragments, the dataset, and the geocode
row are dropped by their unique keys and rebuilt each run.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \\
        .venv/bin/python scripts/seed_e2e_amendment_relink.py
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from layer1.pipeline.publish import set_retrieval_enabled
from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    SourceFragment,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset


MUNICIPALITY = "HRM"
BYLAW_NAME = "ABS-355 Amendment Bylaw"
CITATION = "Zoning Schedule"

DOCUMENT_V1_FILE_HASH = "e2e-abs355-amendment-doc-v1"
DOCUMENT_V2_FILE_HASH = "e2e-abs355-amendment-doc-v2"

DATASET_NAME = "e2e_abs355_zoning"
ZONE_CODE = "AR-9"

TEST_ADDRESS_RAW = "355 Amendment Avenue"
TEST_ADDRESS_NORMALIZED = "civic:355 amendment ave"

# A point + covering box placed clear of the other e2e seed polygons so this
# address resolves only against this seed's zone layer.
TEST_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.70, 44.70]}
_BOX = [
    [-63.71, 44.69],
    [-63.69, 44.69],
    [-63.69, 44.71],
    [-63.71, 44.71],
    [-63.71, 44.69],
]


def _feature_collection() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [_BOX]},
                "properties": {
                    "GLOBALID": "abs355-zone-1",
                    "ZONE": ZONE_CODE,
                    "DESCRIPTION": "Amendment Residential",
                },
            }
        ],
    }


def _config_yaml(geojson_path: Path) -> str:
    return (
        f"name: {DATASET_NAME}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match:\n"
        f"    municipality: {MUNICIPALITY}\n"
        f"    bylaw_name: {BYLAW_NAME}\n"
        f"  fragment_citation: {CITATION}\n"
        "attributes:\n"
        "  feature_key: GLOBALID\n"
        "  canonical:\n"
        "    zone_code: { from: ZONE, type: string }\n"
        "    zone_description: { from: DESCRIPTION, type: string, optional: true }\n"
    )


def _drop_document(session, file_hash: str) -> None:
    document = session.scalar(select(Document).where(Document.file_hash == file_hash))
    if document is None:
        return
    session.query(SourceFragment).filter(
        SourceFragment.document_id == document.id
    ).delete(synchronize_session=False)
    session.delete(document)
    session.flush()


def _drop_dataset(session) -> None:
    existing = session.scalar(select(ExternalDataset).where(ExternalDataset.name == DATASET_NAME))
    if existing is None:
        return
    session.query(ExternalDatasetFeature).filter(
        ExternalDatasetFeature.external_dataset_id == existing.id
    ).delete(synchronize_session=False)
    session.delete(existing)
    session.flush()


def _create_document(
    session, *, file_hash: str, ingested_at: datetime, enabled: bool = False
) -> Document:
    document = Document(
        municipality=MUNICIPALITY,
        bylaw_name=BYLAW_NAME,
        source_path=f"e2e/abs355_amendment_{file_hash}.pdf",
        file_hash=file_hash,
        mime_type="application/pdf",
        page_count=120,
        parser_version="e2e-seed",
        ingestion_timestamp=ingested_at,
        retrieval_enabled=enabled,
    )
    session.add(document)
    session.flush()
    session.add(
        SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label=CITATION,
            citation_path=CITATION.lower().replace(" ", "_"),
            page_start=42,
            page_end=42,
            reading_order_start=42,
            text=f"{CITATION}: zoning designations.",
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            source_block_ids_json=[],
            metadata_json={},
        )
    )
    session.flush()
    return document


def _ensure_geocode_cache(session) -> None:
    existing = session.scalar(
        select(GeocodeCache).where(GeocodeCache.normalized_text == TEST_ADDRESS_NORMALIZED)
    )
    if existing is not None:
        existing.status = "linked"
        existing.geometry_geojson = TEST_POINT
        existing.confidence = 1.0
        session.flush()
        return
    session.add(
        GeocodeCache(
            normalized_text=TEST_ADDRESS_NORMALIZED,
            raw_text=TEST_ADDRESS_RAW,
            kind="civic_address",
            status="linked",
            resolver="e2e_seed",
            geometry_geojson=TEST_POINT,
            confidence=1.0,
            detail="seeded for ABS-355 amendment-relink e2e",
            metadata_json={},
        )
    )
    session.flush()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        with session_scope() as session:
            # Serialise against concurrent Playwright workers so two seed runs
            # don't race the drop-then-rebuild path.
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601355)
                )

            # Clean slate so re-runs are deterministic.
            _drop_dataset(session)
            _drop_document(session, DOCUMENT_V1_FILE_HASH)
            _drop_document(session, DOCUMENT_V2_FILE_HASH)

            now = datetime.now(timezone.utc)
            v1_doc = _create_document(
                session,
                file_hash=DOCUMENT_V1_FILE_HASH,
                ingested_at=now - timedelta(days=1),
                enabled=True,
            )

            # Ingest the zone layer while only v1 exists — it pins to v1.
            geojson_path = work_dir / f"{DATASET_NAME}.geojson"
            geojson_path.write_text(json.dumps(_feature_collection()), encoding="utf-8")
            cfg_path = work_dir / f"{DATASET_NAME}.yaml"
            cfg_path.write_text(_config_yaml(geojson_path), encoding="utf-8")
            ingest_result = ingest_geo_dataset(session, cfg_path)
            assert ingest_result.link_result.status == "linked", ingest_result.link_result
            assert ingest_result.dataset.linked_document_id == v1_doc.id

            # Amend: re-ingest v2 under the same name (disabled, like any real
            # ingest), then PUBLISH it — the production operation behind
            # `enable-retrieval --replace`, which disables v1 and runs the
            # relink pass that moves the layer onto v2.
            v2_doc = _create_document(
                session,
                file_hash=DOCUMENT_V2_FILE_HASH,
                ingested_at=now,
            )
            publish_result = set_retrieval_enabled(
                session, [v2_doc.id], True, replace=True
            )
            relink_results = publish_result.relink_results
            assert [r.status for r in relink_results] == ["linked"], relink_results
            assert not v1_doc.retrieval_enabled and v2_doc.retrieval_enabled
            session.refresh(ingest_result.dataset)
            assert ingest_result.dataset.linked_document_id == v2_doc.id

            _ensure_geocode_cache(session)
            summary = {
                "v1_document_id": v1_doc.id,
                "v2_document_id": v2_doc.id,
                "relinked": len(relink_results),
            }
    print(f"seed_e2e_amendment_relink summary: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
