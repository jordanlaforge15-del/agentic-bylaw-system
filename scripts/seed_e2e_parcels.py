"""Seed the e2e test database with a synthetic parcel + centerline pair.

The lot-facts Playwright spec (`web/e2e/functional/lot-facts-centerline-buffer.spec.ts`)
needs three pieces of state in `layer1_test` before it can drive the
case-open flow through the centerline-buffer algorithm:

1. An ``external_dataset`` row with ``metadata_json.role = "property_parcels"``
   plus one ``external_dataset_feature`` whose ``geometry_geojson`` is a
   15 × 30 m polygon at Halifax latitudes — small enough that the
   equirectangular projection in ``layer2.spatial.lot_metrics`` round-trips
   to round-number metres, big enough to produce a stable assertable area.
2. An ``external_dataset`` row with ``role = "road_centerlines"`` plus one
   LineString feature running along the parcel's south edge so the
   centerline-buffer captures it as frontage.
3. A ``geocode_cache`` row keyed at ``civic:100 test st`` pointing at the
   parcel's centroid, so ``layer2.retrieval.geocode.resolve_location``
   short-circuits to that point instead of calling Google.

Idempotent — re-running on the same database skips rows whose ``name`` /
``normalized_text`` already exist. Called from
``web/e2e/global-setup.ts`` (or directly via ``make e2e-seed-parcels``)
so the spec can assume the fixture data is present.

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \
        .venv/bin/python scripts/seed_e2e_parcels.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone

from sqlalchemy import select, text

from layer1.db.base import (
    ExternalDataset,
    ExternalDatasetFeature,
    GeocodeCache,
    utcnow,
)
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus


# Halifax-ish anchor — keeps the equirectangular projection's cosine
# correction in play, same as the unit-test fixtures.
HALIFAX_LON = -63.6
HALIFAX_LAT = 44.65
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(HALIFAX_LAT))


PARCELS_DATASET_NAME = "e2e_test_parcels"
CENTERLINES_DATASET_NAME = "e2e_test_centerlines"
TEST_ADDRESS_RAW = "100 Test Street"
# Must match ``layer2.retrieval.geocode.normalize_reference`` for a civic
# address. "Test Street" → "test st" after suffix normalization.
TEST_ADDRESS_NORMALIZED = "civic:100 test st"
TEST_PARCEL_PID = "E2E00001"


def _lonlat(x_m: float, y_m: float) -> tuple[float, float]:
    return (HALIFAX_LON + x_m / M_PER_DEG_LON, HALIFAX_LAT + y_m / M_PER_DEG_LAT)


def _parcel_polygon() -> dict:
    """15 m × 30 m parcel with the south edge sitting on the centerline."""
    sw = _lonlat(0.0, 0.0)
    se = _lonlat(15.0, 0.0)
    ne = _lonlat(15.0, 30.0)
    nw = _lonlat(0.0, 30.0)
    return {"type": "Polygon", "coordinates": [[sw, se, ne, nw, sw]]}


def _parcel_bbox() -> dict:
    sw_lon, sw_lat = _lonlat(0.0, 0.0)
    ne_lon, ne_lat = _lonlat(15.0, 30.0)
    return {"minx": sw_lon, "miny": sw_lat, "maxx": ne_lon, "maxy": ne_lat}


def _centerline_geometry() -> dict:
    """East-west centerline at y=0, extending well past the parcel ends.

    Long enough that the ``buffer(buffer_m)`` round-cap on the LineString
    endpoints can't bleed into the parcel and inflate frontage at the
    edges — the buffer in the parcel area is a clean rectangle.
    """
    west = _lonlat(-50.0, 0.0)
    east = _lonlat(50.0, 0.0)
    return {"type": "LineString", "coordinates": [west, east]}


def _centerline_bbox() -> dict:
    west_lon, _lat = _lonlat(-50.0, 0.0)
    east_lon, _lat2 = _lonlat(50.0, 0.0)
    return {"minx": west_lon, "miny": HALIFAX_LAT, "maxx": east_lon, "maxy": HALIFAX_LAT}


def _parcel_centroid_point() -> dict:
    centroid_lon, centroid_lat = _lonlat(7.5, 15.0)
    return {"type": "Point", "coordinates": [centroid_lon, centroid_lat]}


def _ensure_dataset(
    db,
    *,
    name: str,
    role: str,
    feature_count: int,
) -> ExternalDataset:
    """Return the dataset row, creating it (with metadata.role) if missing."""
    existing = db.scalar(
        select(ExternalDataset).where(ExternalDataset.name == name)
    )
    if existing is not None:
        return existing
    ds = ExternalDataset(
        name=name,
        publisher="e2e_seed",
        source_url=None,
        source_path=None,
        format="geojson",
        version=None,
        content_hash=f"e2e_seed_{name}",
        crs="EPSG:4326",
        feature_count=feature_count,
        linked_document_id=None,
        linked_fragment_citation=None,
        linked_fragment_id=None,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        ingestion_timestamp=utcnow(),
        metadata_json={"role": role, "publisher": "e2e_seed"},
    )
    db.add(ds)
    db.flush()
    print(f"created external_dataset id={ds.id} name={name!r} role={role!r}")
    return ds


def _ensure_feature(
    db,
    *,
    dataset: ExternalDataset,
    feature_key: str,
    geometry_geojson: dict,
    geometry_bbox_json: dict,
    canonical_attributes_json: dict | None = None,
) -> ExternalDatasetFeature:
    existing = db.scalar(
        select(ExternalDatasetFeature).where(
            ExternalDatasetFeature.external_dataset_id == dataset.id,
            ExternalDatasetFeature.feature_key == feature_key,
        )
    )
    if existing is not None:
        return existing
    feature = ExternalDatasetFeature(
        external_dataset_id=dataset.id,
        feature_key=feature_key,
        attributes_json={"feature_key": feature_key},
        canonical_attributes_json=canonical_attributes_json or {},
        geometry_geojson=geometry_geojson,
        geometry_bbox_json=geometry_bbox_json,
        parse_status=ParseStatus.PARSED,
        metadata_json={"source": "e2e_seed"},
    )
    db.add(feature)
    db.flush()
    print(
        f"created external_dataset_feature id={feature.id} dataset={dataset.name!r} key={feature_key!r}"
    )
    return feature


def _populate_postgis_geometry(db, dataset_id: int) -> None:
    """Mirror the geometry-on-ingest step in ``ingest_dataset.py``.

    Migration 0009 added the ``geometry`` PostGIS column and backfilled
    rows present at upgrade time. New rows inserted by this script need
    the column populated themselves or every ``ST_Contains`` /
    ``ST_Intersects`` against them will miss.
    """
    bind = getattr(db, "bind", None)
    if bind is None or bind.dialect.name != "postgresql":
        return
    db.execute(
        text(
            """
            UPDATE external_dataset_feature
               SET geometry = ST_GeomFromGeoJSON(geometry_geojson::text)
             WHERE external_dataset_id = :ds_id AND geometry IS NULL
            """
        ),
        {"ds_id": dataset_id},
    )
    db.flush()


def _ensure_geocode_cache(db) -> None:
    """Prime the geocoder so resolve_location returns our parcel centroid.

    Avoids hitting Google during e2e and pins the resolver name to
    ``e2e_seed`` so the test can assert ``anchor_source`` cleanly.
    """
    existing = db.scalar(
        select(GeocodeCache).where(
            GeocodeCache.normalized_text == TEST_ADDRESS_NORMALIZED
        )
    )
    if existing is not None:
        return
    db.add(
        GeocodeCache(
            normalized_text=TEST_ADDRESS_NORMALIZED,
            raw_text=TEST_ADDRESS_RAW,
            kind="civic_address",
            status="linked",
            resolver="e2e_seed",
            geometry_geojson=_parcel_centroid_point(),
            confidence=0.95,
            detail=None,
            metadata_json={"source": "e2e_seed"},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    print(f"created geocode_cache row for {TEST_ADDRESS_NORMALIZED!r}")


def main() -> int:
    with session_scope() as db:
        parcels = _ensure_dataset(
            db, name=PARCELS_DATASET_NAME, role="property_parcels", feature_count=1
        )
        _ensure_feature(
            db,
            dataset=parcels,
            feature_key=TEST_PARCEL_PID,
            geometry_geojson=_parcel_polygon(),
            geometry_bbox_json=_parcel_bbox(),
            canonical_attributes_json={"parcel_id": TEST_PARCEL_PID},
        )
        _populate_postgis_geometry(db, parcels.id)

        centerlines = _ensure_dataset(
            db,
            name=CENTERLINES_DATASET_NAME,
            role="road_centerlines",
            feature_count=1,
        )
        _ensure_feature(
            db,
            dataset=centerlines,
            feature_key="E2E_CL_001",
            geometry_geojson=_centerline_geometry(),
            geometry_bbox_json=_centerline_bbox(),
        )
        _populate_postgis_geometry(db, centerlines.id)

        _ensure_geocode_cache(db)

        # Summary so global-setup.ts log shows the seed actually landed.
        summary = {
            "parcels_dataset_id": parcels.id,
            "centerlines_dataset_id": centerlines.id,
            "address": TEST_ADDRESS_RAW,
            "normalized": TEST_ADDRESS_NORMALIZED,
            "pid": TEST_PARCEL_PID,
        }
        print(f"seed_e2e_parcels summary: {json.dumps(summary)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
