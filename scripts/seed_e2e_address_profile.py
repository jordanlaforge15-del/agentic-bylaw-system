"""Seed a tiny Halifax-shaped spatial corpus for the get_address_profile spec.

Used by ``web/e2e/functional/address-profile-mcp-tool.spec.ts`` (ABS-273) to
drop everything ``RetrievalService.get_address_profile`` needs to resolve one
address end-to-end through the real Postgres/PostGIS stack:

* A ``HRM / Regional Centre Land Use By-Law`` document with four schedule
  fragments — ``Zoning Schedule``, ``Schedule 15`` (height), ``Schedule 17``
  (FAR), and ``Schedule 22`` (heritage) — so the dataset linker can bind
  each overlay to its citing fragment by ``citation_label``.
* Four linked geo datasets, each a single polygon that contains the test
  point, ingested through ``ingest_geo_dataset`` so the PostGIS ``geometry``
  column is populated (the ``ST_Intersects`` path needs the real geometry
  column, not just ``geometry_geojson`` — see the e2e PostGIS gotcha).
* A ``geocode_cache`` row keyed ``civic:100 robie st`` (status ``linked``)
  pointing at the test point, so ``100 Robie Street`` resolves without an
  external geocoder.

Idempotent — datasets are dropped by name and re-ingested; the document,
fragments, and geocode-cache row are upserted by their unique keys.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \\
        .venv/bin/python scripts/seed_e2e_address_profile.py
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


DOCUMENT_FILE_HASH = "e2e-address-profile-doc-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Regional Centre Land Use By-Law"

TEST_ADDRESS_RAW = "100 Robie Street"
TEST_ADDRESS_NORMALIZED = "civic:100 robie st"

# A point inside every seeded overlay polygon, and the box that contains it.
TEST_POINT: dict[str, Any] = {"type": "Point", "coordinates": [-63.59, 44.65]}
_BOX = [
    [-63.60, 44.64],
    [-63.58, 44.64],
    [-63.58, 44.66],
    [-63.60, 44.66],
    [-63.60, 44.64],
]


# (dataset_name, fragment citation_label, raw properties, canonical YAML block)
# dataset_name carries the keyword get_address_profile classifies on.
OVERLAYS: list[dict[str, Any]] = [
    {
        "name": "e2e_ap_zoning",
        "citation": "Zoning Schedule",
        "feature_key_field": "GLOBALID",
        "properties": {"GLOBALID": "ap-zone-1", "ZONE": "HR-2", "DESCRIPTION": "High-Rise Residential"},
        "canonical": (
            "    zone_code: { from: ZONE, type: string }\n"
            "    zone_description: { from: DESCRIPTION, type: string, optional: true }\n"
        ),
    },
    {
        "name": "e2e_ap_height_precincts",
        "citation": "Schedule 15",
        "feature_key_field": "GlobalID",
        "properties": {"GlobalID": "ap-height-1", "MAXBLDHGT": 25.0},
        "canonical": ("    max_height_m: { from: MAXBLDHGT, type: float, optional: true }\n"),
    },
    {
        "name": "e2e_ap_far_precincts",
        "citation": "Schedule 17",
        "feature_key_field": "GLOBALID",
        "properties": {"GLOBALID": "ap-far-1", "FAR": 3.5},
        "canonical": ("    max_far: { from: FAR, type: float }\n"),
    },
    {
        "name": "e2e_ap_heritage_districts",
        "citation": "Schedule 22",
        "feature_key_field": "GLOBALID",
        "properties": {
            "GLOBALID": "ap-heritage-1",
            "HCDNAME": "Schmidtville",
            "STATUS": "Active",
        },
        "canonical": (
            "    district_name: { from: HCDNAME, type: string }\n"
            "    district_status: { from: STATUS, type: string, optional: true }\n"
        ),
    },
]


def _polygon() -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [_BOX]}


def _feature_collection(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [{"type": "Feature", "geometry": _polygon(), "properties": props}],
    }


def _config_yaml(overlay: dict[str, Any], geojson_path: Path) -> str:
    return (
        f"name: {overlay['name']}\n"
        "publisher: e2e_seed\n"
        "format: geojson\n"
        f"source_path: {geojson_path}\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match:\n"
        f"    municipality: {DOCUMENT_MUNICIPALITY}\n"
        f"    bylaw_name: {DOCUMENT_BYLAW_NAME}\n"
        f"  fragment_citation: {overlay['citation']}\n"
        "attributes:\n"
        f"  feature_key: {overlay['feature_key_field']}\n"
        "  canonical:\n"
        f"{overlay['canonical']}"
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
        source_path="e2e/address_profile_bylaw.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=500,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragments(session, *, document_id: int) -> None:
    page = 100
    for overlay in OVERLAYS:
        citation = overlay["citation"]
        existing = session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_label == citation,
            )
        ).scalars().first()
        if existing is not None:
            continue
        session.add(
            SourceFragment(
                document_id=document_id,
                fragment_type=FragmentType.SCHEDULE,
                citation_label=citation,
                citation_path=citation.lower().replace(" ", "_"),
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


def _ensure_geocode_cache(session) -> None:
    existing = session.execute(
        select(GeocodeCache).where(GeocodeCache.normalized_text == TEST_ADDRESS_NORMALIZED)
    ).scalars().first()
    if existing is not None:
        # Force status=linked so the cache short-circuit returns a resolved
        # location (a stale "resolved" row from a prior shape would miss).
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
            detail="seeded for address-profile e2e",
            metadata_json={},
        )
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        with session_scope() as session:
            # Serialise against concurrent Playwright workers so two seed
            # runs don't race the drop-then-reingest path.
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601273)
                )

            document = _get_or_create_document(session)
            _ensure_fragments(session, document_id=document.id)
            session.flush()

            linked = 0
            for overlay in OVERLAYS:
                _drop_existing_dataset(session, overlay["name"])
                geojson_path = work_dir / f"{overlay['name']}.geojson"
                geojson_path.write_text(
                    json.dumps(_feature_collection(overlay["properties"])),
                    encoding="utf-8",
                )
                cfg_path = work_dir / f"{overlay['name']}.yaml"
                cfg_path.write_text(_config_yaml(overlay, geojson_path), encoding="utf-8")
                result = ingest_geo_dataset(session, cfg_path)
                if result.link_result.status == "linked":
                    linked += 1

            _ensure_geocode_cache(session)
            summary = {"document_id": document.id, "overlays_linked": linked}
    print(f"seed_e2e_address_profile summary: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
