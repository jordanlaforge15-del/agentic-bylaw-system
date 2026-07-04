"""Seed the Schedule 7 Pedestrian-Oriented Commercial Streets layer for e2e.

Used by ``web/e2e/functional/schedule7-pocs-intersection.spec.ts`` (ABS-349) to
prove the ingested POCS layer is spatially queryable on the real Postgres/PostGIS
stack:

* A ``HRM / Regional Centre Land Use By-Law`` document with a ``Schedule 7``
  fragment (p.27 list-of-schedules reference) so the dataset linker binds the
  overlay to its citing fragment by ``citation_label``.
* One linked ``external_dataset`` (role-less, links_to Schedule 7) holding two
  designated-corridor LineStrings — a Quinpool Road segment and a control
  Gottingen Street segment — ingested through ``ingest_geo_dataset`` so the
  PostGIS ``geometry`` column is populated. ``ST_Intersects`` reads the real
  geometry column, not ``geometry_geojson`` (the ABS-332-era e2e PostGIS gotcha
  the sqlite unit tests mask).
* Two ``geocode_cache`` rows (status ``linked``): ``6184 Quinpool Road`` sits
  ~10 m off the Quinpool centreline (so only a *buffered* point intersects —
  exercising the buffer, not a trivial on-line hit), and ``500 Nowhere Road``
  sits far from every corridor as a negative control.

Idempotent — the dataset is dropped by name and re-ingested; the document,
fragment, and geocode-cache rows are upserted by their unique keys.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \\
        .venv/bin/python scripts/seed_e2e_pocs.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    SourceFragment,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset


DOCUMENT_FILE_HASH = "e2e-pocs-schedule7-doc-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Regional Centre Land Use By-Law"
SCHEDULE_CITATION = "Schedule 7"

DATASET_NAME = "e2e_pocs_schedule7"

# 6184 Quinpool Rd — the dev-DB address that motivated the ticket. The Quinpool
# corridor runs ~constant-latitude here; the seeded point sits ~10 m north of
# the centreline so a zero-radius point misses and only the ~15 m buffered
# point intersects (the whole reason the query buffers).
QUINPOOL_ADDRESS_RAW = "6184 Quinpool Road"
QUINPOOL_ADDRESS_NORMALIZED = "civic:6184 quinpool rd"
_QUINPOOL_LINE_LAT = 44.64610
_QUINPOOL_POINT: dict[str, Any] = {
    "type": "Point",
    # ~10 m north of the centreline (0.00009 deg lat ≈ 10 m at this latitude).
    "coordinates": [-63.6070, _QUINPOOL_LINE_LAT + 0.00009],
}

# Negative control — far from every designated corridor.
CONTROL_ADDRESS_RAW = "500 Nowhere Road"
CONTROL_ADDRESS_NORMALIZED = "civic:500 nowhere rd"
_CONTROL_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.5500, 44.6800]}


def _feature_collection() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "SEGMENT_ID": "E2E-S7-QUINPOOL",
                    "STREET": "Quinpool Road",
                    "SCHEDULE": "Schedule 7",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-63.6100, _QUINPOOL_LINE_LAT],
                        [-63.6070, _QUINPOOL_LINE_LAT],
                        [-63.6040, _QUINPOOL_LINE_LAT],
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "SEGMENT_ID": "E2E-S7-GOTTINGEN",
                    "STREET": "Gottingen Street",
                    "SCHEDULE": "Schedule 7",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-63.5864, 44.6524],
                        [-63.5879, 44.6579],
                    ],
                },
            },
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
        f"    municipality: {DOCUMENT_MUNICIPALITY}\n"
        f"    bylaw_name: {DOCUMENT_BYLAW_NAME}\n"
        f"  fragment_citation: {SCHEDULE_CITATION}\n"
        "attributes:\n"
        "  feature_key: SEGMENT_ID\n"
        "  canonical:\n"
        "    street_name: { from: STREET, type: string }\n"
        "  ignore: [SCHEDULE]\n"
    )


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH))
        .scalars()
        .first()
    )
    if document is not None:
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/pocs_schedule7_bylaw.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=500,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragment(session, *, document_id: int) -> None:
    existing = session.execute(
        select(SourceFragment).where(
            SourceFragment.document_id == document_id,
            SourceFragment.citation_label == SCHEDULE_CITATION,
        )
    ).scalars().first()
    if existing is not None:
        return
    session.add(
        SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label=SCHEDULE_CITATION,
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


def _drop_existing_dataset(session, name: str) -> None:
    existing = session.scalar(select(ExternalDataset).where(ExternalDataset.name == name))
    if existing is None:
        return
    session.query(ExternalDatasetFeature).filter(
        ExternalDatasetFeature.external_dataset_id == existing.id
    ).delete(synchronize_session=False)
    session.delete(existing)
    session.flush()


def _ensure_geocode_cache(session, *, normalized: str, raw: str, point: dict) -> None:
    existing = session.execute(
        select(GeocodeCache).where(GeocodeCache.normalized_text == normalized)
    ).scalars().first()
    if existing is not None:
        existing.status = "linked"
        existing.geometry_geojson = point
        existing.confidence = 1.0
        session.flush()
        return
    session.add(
        GeocodeCache(
            normalized_text=normalized,
            raw_text=raw,
            kind="civic_address",
            status="linked",
            resolver="e2e_seed",
            geometry_geojson=point,
            confidence=1.0,
            detail="seeded for POCS Schedule 7 e2e",
            metadata_json={"source": "e2e_seed"},
            created_at=utcnow(),
        )
    )
    session.flush()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        with session_scope() as session:
            # Serialise against concurrent Playwright workers so two seed runs
            # don't race the drop-then-reingest path (same shape as the sibling
            # seed scripts' advisory locks).
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601349)
                )

            document = _get_or_create_document(session)
            _ensure_fragment(session, document_id=document.id)
            session.flush()

            _drop_existing_dataset(session, DATASET_NAME)
            geojson_path = work_dir / f"{DATASET_NAME}.geojson"
            geojson_path.write_text(json.dumps(_feature_collection()), encoding="utf-8")
            cfg_path = work_dir / f"{DATASET_NAME}.yaml"
            cfg_path.write_text(_config_yaml(geojson_path), encoding="utf-8")
            result = ingest_geo_dataset(session, cfg_path)

            _ensure_geocode_cache(
                session,
                normalized=QUINPOOL_ADDRESS_NORMALIZED,
                raw=QUINPOOL_ADDRESS_RAW,
                point=_QUINPOOL_POINT,
            )
            _ensure_geocode_cache(
                session,
                normalized=CONTROL_ADDRESS_NORMALIZED,
                raw=CONTROL_ADDRESS_RAW,
                point=_CONTROL_POINT,
            )

            summary = {
                "dataset_id": result.dataset.id,
                "dataset_name": DATASET_NAME,
                "parse_status": result.dataset.parse_status.value,
                "feature_count": result.dataset.feature_count,
                "link_status": result.link_result.status,
                "quinpool_address": QUINPOOL_ADDRESS_RAW,
                "control_address": CONTROL_ADDRESS_RAW,
            }
    print(f"seed_e2e_pocs summary: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
