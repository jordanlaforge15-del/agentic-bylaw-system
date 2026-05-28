"""Seed the e2e test database with synthetic parcel + centerline fixtures.

The lot-facts Playwright spec (`web/e2e/functional/lot-facts-centerline-buffer.spec.ts`)
needs fixture data in `layer1_test` before it can drive the case-open flow
through the centerline-buffer algorithm. Two fixtures are seeded:

  100 Test Street  — mid-block residential (1 street, parcel south edge
                     on the centerline). Pins the mid-block path.
  200 Corner Street — corner lot at the intersection of two
                     perpendicular streets, each with a distinct STR_NAME
                     so the extractor's name-grouping pass runs.
                     ABS-23 regression: before this fix the algorithm
                     reported corner=false and a bogus depth for real
                     HRM corner lots; this fixture catches a regression
                     on the grouping pass.

Each fixture rotates through:
  1. ``external_dataset`` rows with ``metadata_json.role`` of
     ``"property_parcels"`` / ``"road_centerlines"`` so the extractor's
     ``_find_dataset_id`` lookup succeeds.
  2. ``external_dataset_feature`` rows for the parcel polygon and the
     centerline LineStrings. Centerline rows carry ``STR_NAME`` in
     ``attributes_json`` — that's the key the extractor reads for
     street-name grouping (see ``_STREET_NAME_KEYS`` in extractor.py).
  3. A ``geocode_cache`` row keyed by ``civic:<normalized address>``
     pointing at the parcel centroid, short-circuiting Google.

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

MIDBLOCK_ADDRESS_RAW = "100 Test Street"
# Must match ``layer2.retrieval.geocode.normalize_reference`` for a civic
# address. "Test Street" → "test st" after suffix normalization.
MIDBLOCK_ADDRESS_NORMALIZED = "civic:100 test st"
MIDBLOCK_PARCEL_PID = "E2E00001"

CORNER_ADDRESS_RAW = "200 Corner Street"
CORNER_ADDRESS_NORMALIZED = "civic:200 corner st"
CORNER_PARCEL_PID = "E2E00002"

# Offset the corner-lot fixture geometry so its bbox doesn't overlap
# with the mid-block fixture's centerline (which would pull a stray
# horizontal "centerline" into the corner-lot calculation and break the
# corner test's expected frontage). 500 m east is well past the
# CENTERLINE_BBOX_PAD_DEG window (~111 m).
CORNER_X_OFFSET_M = 500.0


def _lonlat(x_m: float, y_m: float) -> tuple[float, float]:
    return (HALIFAX_LON + x_m / M_PER_DEG_LON, HALIFAX_LAT + y_m / M_PER_DEG_LAT)


def _polygon_bbox(coords: list[tuple[float, float]]) -> dict:
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}


# ---------------------------------------------------------------------------
# Mid-block fixture (100 Test Street): 15 × 30 m parcel, south edge on
# one east-west centerline.
# ---------------------------------------------------------------------------


def _midblock_parcel_polygon() -> dict:
    sw = _lonlat(0.0, 0.0)
    se = _lonlat(15.0, 0.0)
    ne = _lonlat(15.0, 30.0)
    nw = _lonlat(0.0, 30.0)
    return {"type": "Polygon", "coordinates": [[sw, se, ne, nw, sw]]}


def _midblock_parcel_bbox() -> dict:
    return _polygon_bbox(
        [_lonlat(0.0, 0.0), _lonlat(15.0, 0.0), _lonlat(15.0, 30.0), _lonlat(0.0, 30.0)]
    )


def _midblock_centerline_geometry() -> dict:
    """East-west centerline at y=0, extending well past the parcel ends.

    Long enough that the ``buffer(buffer_m)`` round-cap on the LineString
    endpoints can't bleed into the parcel and inflate frontage at the
    edges — the buffer in the parcel area is a clean rectangle.
    """
    return {
        "type": "LineString",
        "coordinates": [_lonlat(-50.0, 0.0), _lonlat(50.0, 0.0)],
    }


def _midblock_centerline_bbox() -> dict:
    return _polygon_bbox([_lonlat(-50.0, 0.0), _lonlat(50.0, 0.0)])


def _midblock_centroid_point() -> dict:
    centroid_lon, centroid_lat = _lonlat(7.5, 15.0)
    return {"type": "Point", "coordinates": [centroid_lon, centroid_lat]}


# ---------------------------------------------------------------------------
# Corner-lot fixture (200 Corner Street): 30 × 40 m parcel with SE corner
# at (CORNER_X_OFFSET_M, 0). Two perpendicular centerlines along the
# south and east edges, each with a distinct STR_NAME.
# ---------------------------------------------------------------------------


def _corner_parcel_polygon() -> dict:
    x0 = CORNER_X_OFFSET_M
    sw = _lonlat(x0, 0.0)
    se = _lonlat(x0 + 30.0, 0.0)
    ne = _lonlat(x0 + 30.0, 40.0)
    nw = _lonlat(x0, 40.0)
    return {"type": "Polygon", "coordinates": [[sw, se, ne, nw, sw]]}


def _corner_parcel_bbox() -> dict:
    x0 = CORNER_X_OFFSET_M
    return _polygon_bbox(
        [_lonlat(x0, 0.0), _lonlat(x0 + 30.0, 40.0)]
    )


def _corner_south_centerline_geometry() -> dict:
    """East-west centerline along the south edge of the corner parcel."""
    x0 = CORNER_X_OFFSET_M
    return {
        "type": "LineString",
        "coordinates": [_lonlat(x0 - 50.0, 0.0), _lonlat(x0 + 80.0, 0.0)],
    }


def _corner_south_centerline_bbox() -> dict:
    x0 = CORNER_X_OFFSET_M
    return _polygon_bbox([_lonlat(x0 - 50.0, 0.0), _lonlat(x0 + 80.0, 0.0)])


def _corner_east_centerline_geometry() -> dict:
    """North-south centerline along the east edge of the corner parcel."""
    x0 = CORNER_X_OFFSET_M
    return {
        "type": "LineString",
        "coordinates": [_lonlat(x0 + 30.0, -50.0), _lonlat(x0 + 30.0, 90.0)],
    }


def _corner_east_centerline_bbox() -> dict:
    x0 = CORNER_X_OFFSET_M
    return _polygon_bbox(
        [_lonlat(x0 + 30.0, -50.0), _lonlat(x0 + 30.0, 90.0)]
    )


def _corner_centroid_point() -> dict:
    centroid_lon, centroid_lat = _lonlat(CORNER_X_OFFSET_M + 15.0, 20.0)
    return {"type": "Point", "coordinates": [centroid_lon, centroid_lat]}


# ---------------------------------------------------------------------------
# Generic ensure helpers
# ---------------------------------------------------------------------------


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
        # Update feature_count to reflect any newly-seeded features.
        if existing.feature_count != feature_count:
            existing.feature_count = feature_count
            db.flush()
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
    attributes_json: dict | None = None,
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
        attributes_json=attributes_json or {"feature_key": feature_key},
        canonical_attributes_json=canonical_attributes_json or {},
        geometry_geojson=geometry_geojson,
        geometry_bbox_json=geometry_bbox_json,
        parse_status=ParseStatus.PARSED,
        metadata_json={"source": "e2e_seed"},
    )
    db.add(feature)
    db.flush()
    print(
        f"created external_dataset_feature id={feature.id} "
        f"dataset={dataset.name!r} key={feature_key!r}"
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


def _ensure_geocode_cache(
    db, *, normalized: str, raw: str, point: dict
) -> None:
    """Prime the geocoder so resolve_location returns the supplied point.

    Avoids hitting Google during e2e and pins the resolver name to
    ``e2e_seed`` so the test can assert ``anchor_source`` cleanly.
    """
    existing = db.scalar(
        select(GeocodeCache).where(GeocodeCache.normalized_text == normalized)
    )
    if existing is not None:
        return
    db.add(
        GeocodeCache(
            normalized_text=normalized,
            raw_text=raw,
            kind="civic_address",
            status="linked",
            resolver="e2e_seed",
            geometry_geojson=point,
            confidence=0.95,
            detail=None,
            metadata_json={"source": "e2e_seed"},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    print(f"created geocode_cache row for {normalized!r}")


def main() -> int:
    with session_scope() as db:
        # ABS-207: serialise concurrent beforeAll invocations so two
        # Playwright workers don't race the unique-key inserts below
        # (dataset name + feature_key + geocode_cache.normalized_text).
        # Same shape as seed_e2e_evaluator_bylaws.py's advisory lock.
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601200))

        # 2 parcels (mid-block + corner), 3 centerlines (1 mid-block + 2 corner).
        parcels = _ensure_dataset(
            db, name=PARCELS_DATASET_NAME, role="property_parcels", feature_count=2
        )
        centerlines = _ensure_dataset(
            db,
            name=CENTERLINES_DATASET_NAME,
            role="road_centerlines",
            feature_count=3,
        )

        # Mid-block fixture
        _ensure_feature(
            db,
            dataset=parcels,
            feature_key=MIDBLOCK_PARCEL_PID,
            geometry_geojson=_midblock_parcel_polygon(),
            geometry_bbox_json=_midblock_parcel_bbox(),
            canonical_attributes_json={"parcel_id": MIDBLOCK_PARCEL_PID},
        )
        _ensure_feature(
            db,
            dataset=centerlines,
            feature_key="E2E_CL_001",
            geometry_geojson=_midblock_centerline_geometry(),
            geometry_bbox_json=_midblock_centerline_bbox(),
            attributes_json={"feature_key": "E2E_CL_001", "STR_NAME": "TEST"},
        )

        # Corner-lot fixture (ABS-23 regression coverage)
        _ensure_feature(
            db,
            dataset=parcels,
            feature_key=CORNER_PARCEL_PID,
            geometry_geojson=_corner_parcel_polygon(),
            geometry_bbox_json=_corner_parcel_bbox(),
            canonical_attributes_json={"parcel_id": CORNER_PARCEL_PID},
        )
        _ensure_feature(
            db,
            dataset=centerlines,
            feature_key="E2E_CL_CORNER_S",
            geometry_geojson=_corner_south_centerline_geometry(),
            geometry_bbox_json=_corner_south_centerline_bbox(),
            attributes_json={
                "feature_key": "E2E_CL_CORNER_S",
                "STR_NAME": "CORNER",
            },
        )
        _ensure_feature(
            db,
            dataset=centerlines,
            feature_key="E2E_CL_CORNER_E",
            geometry_geojson=_corner_east_centerline_geometry(),
            geometry_bbox_json=_corner_east_centerline_bbox(),
            attributes_json={
                "feature_key": "E2E_CL_CORNER_E",
                "STR_NAME": "CROSS",
            },
        )

        _populate_postgis_geometry(db, parcels.id)
        _populate_postgis_geometry(db, centerlines.id)

        _ensure_geocode_cache(
            db,
            normalized=MIDBLOCK_ADDRESS_NORMALIZED,
            raw=MIDBLOCK_ADDRESS_RAW,
            point=_midblock_centroid_point(),
        )
        _ensure_geocode_cache(
            db,
            normalized=CORNER_ADDRESS_NORMALIZED,
            raw=CORNER_ADDRESS_RAW,
            point=_corner_centroid_point(),
        )

        summary = {
            "parcels_dataset_id": parcels.id,
            "centerlines_dataset_id": centerlines.id,
            "midblock_address": MIDBLOCK_ADDRESS_RAW,
            "midblock_pid": MIDBLOCK_PARCEL_PID,
            "corner_address": CORNER_ADDRESS_RAW,
            "corner_pid": CORNER_PARCEL_PID,
        }
        print(f"seed_e2e_parcels summary: {json.dumps(summary)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
