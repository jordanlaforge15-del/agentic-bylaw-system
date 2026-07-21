"""Seed the Schedule 7 Pedestrian-Oriented Commercial Streets layer for e2e.

Used by ``web/e2e/functional/schedule7-pocs-profile.spec.ts`` (ABS-350) to
prove the new ``pedestrian_street`` overlay role is spatially queryable on the
real Postgres/PostGIS stack and that ``get_address_profile`` reports the POCS
designation — the fact the 6184 Quinpool Rd answer (advisor_question_purchase
id 3) flipped on (s.38(2) prohibits ground-floor office on a POCS street;
s.69(d) permits it otherwise).

Seeds:

* A ``HRM / Regional Centre Land Use By-Law`` document with a ``Schedule 7``
  fragment (p.27 list-of-schedules reference) so the dataset linker binds the
  overlay to its citing fragment by ``citation_label``.
* One linked ``external_dataset`` whose name contains "pedestrian" (so the
  retrieval service resolves it to the ``pedestrian_street`` overlay role and
  uses the *abuts* predicate) holding two designated-corridor LineStrings — a
  Quinpool Road segment and a control Gottingen Street segment — ingested
  through ``ingest_geo_dataset`` so the PostGIS ``geometry`` column is
  populated. ST_DWithin reads the real geometry column, not geometry_geojson.
* Two ``geocode_cache`` rows (status ``linked``): ``6184 Quinpool Road`` sits
  ~10 m off the Quinpool centreline (so a zero-radius point misses and only the
  buffered abuts query intersects), and ``500 Nowhere Road`` sits far from
  every corridor as a definitive-negative control.

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

# ABS-350 fix: Schedule 7 belongs to the SAME Regional Centre LUB document that
# carries the zoning/height/FAR/heritage schedules (seed_e2e_address_profile).
# Bind this seed to that shared document instead of minting a *second* "Regional
# Centre Land Use By-Law" document. The advisor scopes retrieval with
# latest_per_bylaw_resolver, which keeps only the newest document per
# (municipality, bylaw_name); a separate, newer POCS document would evict the
# zone overlays' document from scope and regress address_lookup zone resolution
# (100 Robie Street -> HR-2) in abs274-bylaw-query / address-profile-mcp-tool.
# Reusing the same document keeps both the zone overlays and the POCS overlay in
# one bylaw partition so every resolver mode sees them together.
from seed_e2e_address_profile import (
    CORPUS_ADVISORY_LOCK_KEY,
    DOCUMENT_BYLAW_NAME as _AP_BYLAW_NAME,
    DOCUMENT_FILE_HASH as _AP_DOCUMENT_FILE_HASH,
    DOCUMENT_MUNICIPALITY as _AP_MUNICIPALITY,
)

DOCUMENT_FILE_HASH = _AP_DOCUMENT_FILE_HASH
DOCUMENT_MUNICIPALITY = _AP_MUNICIPALITY
DOCUMENT_BYLAW_NAME = _AP_BYLAW_NAME
SCHEDULE_CITATION = "Schedule 7"

# Pre-fix standalone POCS document. The original ABS-350 seed minted its own
# "Regional Centre Land Use By-Law" document under this file_hash with a newer
# ingestion_timestamp than the address-profile document. Because
# latest_per_bylaw_resolver keeps only the newest document per
# (municipality, bylaw_name), that standalone doc *evicts* the address-profile
# document (which carries the zone/height/FAR/heritage overlays) from scope,
# so get_address_profile returns zone=None (abs274-bylaw-query /
# address-profile-mcp-tool). The seed now binds to the shared document, but
# layer1_test persists across `make e2e` runs, so a stale doc left behind by a
# pre-fix run keeps evicting the zone overlays until it is actively removed.
_STALE_POCS_FILE_HASH = "e2e-pocs-schedule7-doc-1"

# The ABS-349 POCS seed that landed on dev minted its standalone document under a
# DISTINCT (municipality, bylaw_name) identity — "Halifax Regional Municipality"
# / "Regional Centre Land Use By-Law (POCS Schedule 7 E2E)" — deliberately unlike
# the real name so it wouldn't collide with the address-profile document by
# (municipality, bylaw_name). On a persistent layer1_test volume a pre-fix run
# may have left that doc behind under its own file_hash; if that file_hash ever
# diverges from `_STALE_POCS_FILE_HASH`, the hash purge alone would miss it and it
# would keep occupying its own bylaw partition. Purge by this identity too so the
# shared volume self-heals regardless of which seed version ran last. This name is
# intentionally NOT the shared document's ("HRM" / "Regional Centre Land Use
# By-Law"), so the identity purge can never touch the shared address-profile doc.
_STALE_POCS_MUNICIPALITY = "Halifax Regional Municipality"
_STALE_POCS_BYLAW_NAME = "Regional Centre Land Use By-Law (POCS Schedule 7 E2E)"

# The name MUST contain "pedestrian" so RetrievalService._overlay_role maps it
# to the pedestrian_street role (ABS-350) and applies the abuts predicate.
DATASET_NAME = "e2e_pedestrian_oriented_commercial_streets"

# The ABS-349 seed ingested the POCS features under this name (no "pedestrian",
# so it never resolved to the overlay role). On a persistent layer1_test volume a
# pre-fix run may have left it behind; drop it too so the volume self-heals and no
# stale, role-less POCS dataset lingers alongside the renamed one.
_STALE_DATASET_NAME = "e2e_pocs_schedule7"

# 6184 Quinpool Rd — the dev-DB address that motivated the ticket. The Quinpool
# corridor runs ~constant-latitude here; the seeded point sits ~10 m north of
# the centreline so a zero-radius point misses and only the ~30 m abuts buffer
# intersects (the whole reason the overlay needs a distance predicate).
QUINPOOL_ADDRESS_RAW = "6184 Quinpool Road"
QUINPOOL_ADDRESS_NORMALIZED = "civic:6184 quinpool rd"
_QUINPOOL_LINE_LAT = 44.64610
_QUINPOOL_POINT: dict[str, Any] = {
    "type": "Point",
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


def _purge_stale_pocs_document(session) -> None:
    """Remove any pre-fix standalone POCS document if it lingers in the DB.

    Two identities are purged, matched with OR so both a hash-keyed and a
    name-keyed leftover are caught in one pass:

    * ``_STALE_POCS_FILE_HASH`` — the file_hash the pre-fix ABS-350 seed used.
    * ``(_STALE_POCS_MUNICIPALITY, _STALE_POCS_BYLAW_NAME)`` — the distinct
      (municipality, bylaw_name) identity the ABS-349 seed minted; caught even
      if its file_hash ever diverges from the hash above.

    Idempotent and safe on a clean DB (no-op when absent). Deleting the
    document and its fragments is enough: both ``external_dataset`` FKs
    (``linked_document_id`` / ``linked_fragment_id``) are ``ON DELETE SET
    NULL``, and the same-named pedestrian dataset is re-dropped and re-ingested
    against the shared document below, so no dataset is left dangling. See
    ``_STALE_POCS_FILE_HASH`` for why this eviction breaks zone resolution.

    The name identity is intentionally the pre-fix "(POCS Schedule 7 E2E)" name,
    never the shared address-profile document's, so this can never delete the
    document that carries the zone/height/FAR/heritage overlays.
    """
    from sqlalchemy import and_, or_

    stale_docs = (
        session.scalars(
            select(Document).where(
                or_(
                    Document.file_hash == _STALE_POCS_FILE_HASH,
                    and_(
                        Document.municipality == _STALE_POCS_MUNICIPALITY,
                        Document.bylaw_name == _STALE_POCS_BYLAW_NAME,
                    ),
                )
            )
        )
        .all()
    )
    if not stale_docs:
        return
    stale_ids = [doc.id for doc in stale_docs]
    session.query(SourceFragment).filter(
        SourceFragment.document_id.in_(stale_ids)
    ).delete(synchronize_session=False)
    session.query(Document).filter(Document.id.in_(stale_ids)).delete(
        synchronize_session=False
    )
    session.flush()


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
        existing.confidence = 0.95
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
            confidence=0.95,
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
            # Serialise against concurrent Playwright workers. Deliberately the
            # SAME key as seed_e2e_address_profile (not a per-script one): both
            # seeds mutate the shared Regional Centre document, so distinct keys
            # let them interleave — double-minting the shared document on a
            # fresh DB and evicting each other's overlays from resolver scope.
            if session.bind.dialect.name == "postgresql":
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                        k=CORPUS_ADVISORY_LOCK_KEY
                    )
                )

            _purge_stale_pocs_document(session)
            document = _get_or_create_document(session)
            _ensure_fragment(session, document_id=document.id)
            session.flush()

            _drop_existing_dataset(session, _STALE_DATASET_NAME)
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
