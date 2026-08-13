"""Seed a tiny Halifax-shaped bylaw corpus for the evaluator e2e spec.

Used by ``web/e2e/functional/evaluator-mcp-tool.spec.ts`` to drop a
known-shape bylaw document + tagged clauses + a parcel + a geocode
cache row into ``layer1_test`` (or whatever DATABASE_URL the caller
points it at).

Idempotent — re-running drops the previously seeded rows by their
unique keys and re-inserts. Skips rows that already match the seed
exactly, so a re-run on an already-seeded DB is a no-op rather than
an error.

Seeded content:

* Document ``HRM / Evaluator E2E Bylaw`` with three tagged clauses:
    * §4.2.1  attribute_tags=[front_setback_m]    "minimum 4.5 m"
    * §4.3.1  attribute_tags=[building_height_m]  "maximum 11 m"
    * §4.4.1  attribute_tags=[corner_lot_boolean, front_setback_m]
              conditional corner-lot clause (tested as 'uncertain' in
              the e2e spec when corner_lot_boolean is missing).
* A ``parcel`` row at ``HRM / E2E00100`` with a small synthetic
  polygon at Halifax latitudes.
* A ``geocode_cache`` row keyed at ``civic:100 evaluator way`` so
  ``LocationSlot{civic_number: 100, street: 'Evaluator Way'}``
  resolves without calling Google.
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import math
import sys
from typing import Any

from sqlalchemy import select

from layer1.db.base import (
    Document,
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    Parcel,
    SourceFragment,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


HALIFAX_LON = -63.6
HALIFAX_LAT = 44.65
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(HALIFAX_LAT))


DOCUMENT_FILE_HASH = "e2e-evaluator-bylaw-1"
DOCUMENT_MUNICIPALITY = "HRM"
DOCUMENT_BYLAW_NAME = "Evaluator E2E Bylaw"

PARCELS_DATASET_NAME = "e2e_evaluator_parcels"
TEST_PID = "E2E00100"
TEST_ADDRESS_NORMALIZED = "civic:100 evaluator way"
TEST_ADDRESS_RAW = "100 Evaluator Way"


def _lonlat(x_m: float, y_m: float) -> tuple[float, float]:
    return (
        HALIFAX_LON + x_m / M_PER_DEG_LON,
        HALIFAX_LAT + y_m / M_PER_DEG_LAT,
    )


def _polygon() -> dict[str, Any]:
    sw = _lonlat(0.0, 0.0)
    se = _lonlat(20.0, 0.0)
    ne = _lonlat(20.0, 30.0)
    nw = _lonlat(0.0, 30.0)
    return {"type": "Polygon", "coordinates": [[sw, se, ne, nw, sw]]}


def _bbox() -> dict[str, Any]:
    sw_lon, sw_lat = _lonlat(0.0, 0.0)
    ne_lon, ne_lat = _lonlat(20.0, 30.0)
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [sw_lon, sw_lat],
                [ne_lon, sw_lat],
                [ne_lon, ne_lat],
                [sw_lon, ne_lat],
                [sw_lon, sw_lat],
            ]
        ],
    }


def _centroid_geojson() -> dict[str, Any]:
    lon, lat = _lonlat(10.0, 15.0)
    return {"type": "Point", "coordinates": [lon, lat]}


def seed(session) -> dict[str, int]:
    """Insert (or reuse) the evaluator e2e fixture rows.

    Concurrency-safe: Playwright runs the smoke spec across four
    viewport projects in parallel, so the seed gets invoked four
    times concurrently in ``beforeAll``. On Postgres we acquire a
    transaction-scoped advisory lock keyed by a stable arbitrary
    integer so only one worker mutates state at a time; the others
    wait, then find the rows already present and no-op.

    The data is static — there's nothing to update between runs — so
    "exists → skip" is the right semantics. Each component is
    upserted by its stable unique key.
    """
    if session.bind.dialect.name == "postgresql":
        # Arbitrary 64-bit constant — must be stable across processes,
        # unique among seed scripts on this DB. The number is just the
        # ASCII codepoint sum of "abs46-evaluator" scaled to a 32-bit
        # range; any fixed integer works.
        from sqlalchemy import text as sa_text

        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601146))

    document = _get_or_create_document(session)
    _ensure_fragments(session, document_id=document.id)
    parcel = _get_or_create_parcel(session)
    dataset = _get_or_create_dataset(session)
    _ensure_dataset_feature(session, dataset_id=dataset.id, parcel_id=parcel.id)
    _ensure_geocode_cache(session)
    session.flush()

    front_id, height_id, corner_id = _fragment_ids(session, document_id=document.id)
    return {
        "document_id": document.id,
        "parcel_id": parcel.id,
        "front_clause_id": front_id,
        "height_clause_id": height_id,
        "corner_clause_id": corner_id,
    }


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if document is not None:
        # Converge the publish flag on re-seed: rows created before
        # ABS-413 (or left disabled by the migration backfill) must
        # still end up retrieval-enabled in the persistent e2e DB.
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/evaluator_bylaw.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=10,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragments(session, *, document_id: int) -> None:
    """Ensure the three tagged clauses exist on the document.

    Each clause is keyed by ``(document_id, citation_path)`` — the
    same unique constraint the layer-1 schema enforces — so an INSERT
    that races with a peer worker hits the unique constraint and
    is treated as "already present".
    """
    fragments = [
        dict(
            citation_label="4.2.1",
            citation_path="4.2.1",
            page_start=1,
            reading_order_start=1,
            text="The minimum front yard shall not be less than 4.5 metres.",
            attribute_tags=["front_setback_m"],
        ),
        dict(
            citation_label="4.3.1",
            citation_path="4.3.1",
            page_start=2,
            reading_order_start=2,
            text="The maximum building height shall not exceed 11 metres.",
            attribute_tags=["building_height_m"],
        ),
        dict(
            citation_label="4.4.1",
            citation_path="4.4.1",
            page_start=3,
            reading_order_start=3,
            text=(
                "On a corner lot the minimum front yard shall be 6.0 metres "
                "instead of the value otherwise required by §4.2.1."
            ),
            attribute_tags=["corner_lot_boolean", "front_setback_m"],
        ),
    ]
    for fragment in fragments:
        existing = session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path == fragment["citation_path"],
            )
        ).scalars().first()
        if existing is not None:
            continue
        session.add(
            SourceFragment(
                document_id=document_id,
                fragment_type=FragmentType.CLAUSE,
                citation_label=fragment["citation_label"],
                citation_path=fragment["citation_path"],
                parent_fragment_id=None,
                page_start=fragment["page_start"],
                page_end=fragment["page_start"],
                reading_order_start=fragment["reading_order_start"],
                text=fragment["text"],
                parse_status=ParseStatus.PARSED,
                confidence=1.0,
                attribute_tags=list(fragment["attribute_tags"]),
            )
        )


def _get_or_create_parcel(session) -> Parcel:
    parcel = (
        session.execute(
            select(Parcel).where(
                Parcel.jurisdiction == "HRM",
                Parcel.parcel_identifier == TEST_PID,
            )
        )
        .scalars()
        .first()
    )
    if parcel is not None:
        return parcel
    parcel = Parcel(
        jurisdiction="HRM",
        parcel_identifier=TEST_PID,
        geometry_geojson=_polygon(),
        centroid_geojson=_centroid_geojson(),
        area_m2=600.0,
        metadata_json={"source_dataset": PARCELS_DATASET_NAME, "seed": "evaluator-e2e"},
    )
    session.add(parcel)
    session.flush()
    return parcel


def _get_or_create_dataset(session) -> ExternalDataset:
    dataset = (
        session.execute(
            select(ExternalDataset).where(
                ExternalDataset.name == PARCELS_DATASET_NAME
            )
        )
        .scalars()
        .first()
    )
    if dataset is not None:
        # Repair stale rows seeded before ABS-77: the evaluator fixture must
        # not shadow the lot-facts dataset (both used "property_parcels").
        if (dataset.metadata_json or {}).get("role") == "property_parcels":
            dataset.metadata_json = {**dataset.metadata_json, "role": "evaluator_parcels"}
            session.flush()
        return dataset
    dataset = ExternalDataset(
        name=PARCELS_DATASET_NAME,
        publisher="HRM",
        format="geojson",
        content_hash="e2e-evaluator-parcels",
        crs="EPSG:4326",
        feature_count=1,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={"role": "evaluator_parcels"},
    )
    session.add(dataset)
    session.flush()
    return dataset


def _ensure_dataset_feature(session, *, dataset_id: int, parcel_id: int) -> None:
    existing = session.execute(
        select(ExternalDatasetFeature).where(
            ExternalDatasetFeature.external_dataset_id == dataset_id,
            ExternalDatasetFeature.feature_key == TEST_PID,
        )
    ).scalars().first()
    if existing is not None:
        return
    session.add(
        ExternalDatasetFeature(
            external_dataset_id=dataset_id,
            feature_key=TEST_PID,
            attributes_json={"PID": TEST_PID},
            canonical_attributes_json={"parcel_id": TEST_PID},
            geometry_geojson=_polygon(),
            geometry_bbox_json=_bbox(),
            parse_status=ParseStatus.PARSED,
            metadata_json={},
            parcel_id=parcel_id,
        )
    )


def _ensure_geocode_cache(session) -> None:
    existing = session.execute(
        select(GeocodeCache).where(
            GeocodeCache.normalized_text == TEST_ADDRESS_NORMALIZED
        )
    ).scalars().first()
    if existing is not None:
        return
    session.add(
        GeocodeCache(
            normalized_text=TEST_ADDRESS_NORMALIZED,
            raw_text=TEST_ADDRESS_RAW,
            kind="civic_address",
            status="resolved",
            resolver="e2e_seed",
            geometry_geojson=_centroid_geojson(),
            confidence=1.0,
            detail="seeded for evaluator e2e",
            metadata_json={},
        )
    )


def _fragment_ids(session, *, document_id: int) -> tuple[int, int, int]:
    rows = session.execute(
        select(SourceFragment.citation_path, SourceFragment.id).where(
            SourceFragment.document_id == document_id,
            SourceFragment.citation_path.in_(["4.2.1", "4.3.1", "4.4.1"]),
        )
    ).all()
    by_path = {row.citation_path: row.id for row in rows}
    return by_path["4.2.1"], by_path["4.3.1"], by_path["4.4.1"]


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(
        "seed_e2e_evaluator_bylaws: "
        f"document={ids['document_id']} parcel={ids['parcel_id']} "
        f"front={ids['front_clause_id']} height={ids['height_clause_id']} "
        f"corner={ids['corner_clause_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
