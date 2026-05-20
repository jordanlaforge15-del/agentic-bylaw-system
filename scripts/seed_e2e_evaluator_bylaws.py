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
  polygon at Halifax latitudes and zone_code ``ER-1``.
* A ``geocode_cache`` row keyed at ``civic:100 evaluator way`` so
  ``LocationSlot{civic_number: 100, street: 'Evaluator Way'}``
  resolves without calling Google.
"""
from __future__ import annotations

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
    """Insert (or re-insert) the evaluator e2e fixture rows.

    Returns the new ids for document, parcel, and each fragment so the
    Playwright spec can later assert on citation_path -> fragment_id
    mappings if needed.
    """
    document = (
        session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if document is not None:
        # Drop fragments + parcel + dataset to make the re-seed clean.
        session.query(SourceFragment).filter(
            SourceFragment.document_id == document.id
        ).delete(synchronize_session=False)
        session.delete(document)
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
        session.delete(parcel)
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
        session.query(ExternalDatasetFeature).filter(
            ExternalDatasetFeature.external_dataset_id == dataset.id
        ).delete(synchronize_session=False)
        session.delete(dataset)
    session.query(GeocodeCache).filter(
        GeocodeCache.normalized_text == TEST_ADDRESS_NORMALIZED
    ).delete(synchronize_session=False)
    session.flush()

    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/evaluator_bylaw.pdf",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="application/pdf",
        page_count=10,
        parser_version="e2e-seed",
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()

    front_clause = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.CLAUSE,
        citation_label="4.2.1",
        citation_path="4.2.1",
        parent_fragment_id=None,
        page_start=1,
        page_end=1,
        reading_order_start=1,
        text="The minimum front yard shall not be less than 4.5 metres.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        attribute_tags=["front_setback_m"],
    )
    height_clause = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.CLAUSE,
        citation_label="4.3.1",
        citation_path="4.3.1",
        parent_fragment_id=None,
        page_start=2,
        page_end=2,
        reading_order_start=2,
        text="The maximum building height shall not exceed 11 metres.",
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        attribute_tags=["building_height_m"],
    )
    corner_clause = SourceFragment(
        document_id=document.id,
        fragment_type=FragmentType.CLAUSE,
        citation_label="4.4.1",
        citation_path="4.4.1",
        parent_fragment_id=None,
        page_start=3,
        page_end=3,
        reading_order_start=3,
        text=(
            "On a corner lot the minimum front yard shall be 6.0 metres "
            "instead of the value otherwise required by §4.2.1."
        ),
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        attribute_tags=["corner_lot_boolean", "front_setback_m"],
    )
    session.add_all([front_clause, height_clause, corner_clause])

    parcel = Parcel(
        jurisdiction="HRM",
        parcel_identifier=TEST_PID,
        geometry_geojson=_polygon(),
        centroid_geojson=_centroid_geojson(),
        area_m2=600.0,
        zone_code="ER-1",
        metadata_json={"source_dataset": PARCELS_DATASET_NAME, "seed": "evaluator-e2e"},
    )
    session.add(parcel)
    session.flush()

    dataset = ExternalDataset(
        name=PARCELS_DATASET_NAME,
        publisher="HRM",
        format="geojson",
        content_hash="e2e-evaluator-parcels",
        crs="EPSG:4326",
        feature_count=1,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={"role": "property_parcels"},
    )
    session.add(dataset)
    session.flush()
    feature = ExternalDatasetFeature(
        external_dataset_id=dataset.id,
        feature_key=TEST_PID,
        attributes_json={"PID": TEST_PID},
        canonical_attributes_json={"parcel_id": TEST_PID},
        geometry_geojson=_polygon(),
        geometry_bbox_json=_bbox(),
        parse_status=ParseStatus.PARSED,
        metadata_json={},
        parcel_id=parcel.id,
    )
    session.add(feature)

    # Geocode cache row so the address resolves without Google.
    cache = GeocodeCache(
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
    session.add(cache)
    session.flush()

    return {
        "document_id": document.id,
        "parcel_id": parcel.id,
        "front_clause_id": front_clause.id,
        "height_clause_id": height_clause.id,
        "corner_clause_id": corner_clause.id,
    }


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
