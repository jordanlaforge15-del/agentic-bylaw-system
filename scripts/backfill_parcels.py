"""One-time backfill of the ``parcel`` table from existing Halifax features.

Phase 1 introduced the ``parcel`` table (migration 0014). This script
populates it from the Halifax parcel data already ingested under
``external_dataset`` / ``external_dataset_feature``, and links each
parcel feature back to its new ``parcel`` row via ``parcel_id``.

Idempotent
----------
Safe to re-run. For each Halifax parcel feature:

* if a ``parcel`` row already exists with the same
  ``(jurisdiction, parcel_identifier)``, the row is reused and the
  feature's ``parcel_id`` FK is set / refreshed.
* otherwise a new row is inserted with centroid + area computed from
  the feature's geometry and the zone_code looked up from the
  Halifax zoning dataset.

Re-running after schema changes that add columns won't break — the
script touches only the columns it knows about.

Scope
-----
Halifax-only by design. Each new municipal ingest will get its own
backfill (the next jurisdiction's parcel attribute schema will differ
slightly, so making this generic now would be premature).

Usage
-----
    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \
        .venv/bin/python scripts/backfill_parcels.py

    # Dry-run (compute and report, don't write):
    .venv/bin/python scripts/backfill_parcels.py --dry-run

Document the run in the issue afterwards: row count, unmatched count,
runtime.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from dataclasses import dataclass
from typing import Any

from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature, Parcel
from layer1.db.session import session_scope


HALIFAX_JURISDICTION = "HRM"
HALIFAX_PARCELS_DATASET_NAME = "halifax_property_parcels"
HALIFAX_ZONING_DATASET_NAME = "halifax_zoning_boundaries"

# Canonical attribute key the Halifax parcels YAML maps ``PID`` to. See
# ``src/layer1/datasets/halifax_property_parcels.yaml`` — feature_key is
# also ``PID``, but we read from canonical_attributes so a future
# schema-mapping tweak doesn't silently break this script.
PARCEL_IDENTIFIER_CANONICAL_KEY = "parcel_id"
ZONE_CODE_CANONICAL_KEY = "zone_code"


logger = logging.getLogger("backfill_parcels")


@dataclass
class BackfillStats:
    """What we did, suitable for the post-run issue update."""

    features_total: int = 0
    parcels_inserted: int = 0
    parcels_reused: int = 0
    features_linked: int = 0
    unmatched_features: int = 0
    bad_geometry: int = 0
    missing_identifier: int = 0
    zone_lookups_successful: int = 0
    zone_lookups_failed: int = 0

    def summary_line(self) -> str:
        return (
            f"features={self.features_total} "
            f"inserted={self.parcels_inserted} "
            f"reused={self.parcels_reused} "
            f"linked={self.features_linked} "
            f"unmatched={self.unmatched_features} "
            f"bad_geometry={self.bad_geometry} "
            f"missing_identifier={self.missing_identifier} "
            f"zone_ok={self.zone_lookups_successful} "
            f"zone_missing={self.zone_lookups_failed}"
        )


def backfill(session: Session, *, dry_run: bool = False) -> BackfillStats:
    """Run the backfill on the supplied session.

    Caller owns the transaction — pass ``session_scope()``'s session in
    for real runs, or a test-fixture session for unit tests. In dry-run
    mode we still query and compute but never call ``session.add`` —
    useful for verifying the script can find the datasets before
    committing changes.
    """
    parcels_dataset = _find_parcels_dataset(session)
    if parcels_dataset is None:
        raise RuntimeError(
            f"no external_dataset named {HALIFAX_PARCELS_DATASET_NAME!r} found. "
            "Has the Halifax parcel ingest run on this database?"
        )
    zoning_dataset = _find_zoning_dataset(session)
    if zoning_dataset is None:
        logger.warning(
            "no %r dataset found; zone_code will be left NULL on inserted parcels",
            HALIFAX_ZONING_DATASET_NAME,
        )

    # Pre-load zoning features for the in-memory point-in-polygon lookup.
    # On postgres we could use ST_Contains via PostGIS; pulling rows
    # into shapely here keeps the script dialect-agnostic and runs in
    # acceptable time for Halifax (~11k zoning polygons).
    zoning_features = _load_zoning_geometries(session, zoning_dataset)

    stats = BackfillStats()
    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == parcels_dataset.id
            )
        )
        .scalars()
        .all()
    )
    stats.features_total = len(features)
    logger.info("processing %d parcel features", stats.features_total)

    # Cache parcels by (jurisdiction, identifier) so re-encountering the
    # same identifier within one run reuses the in-memory instance
    # instead of re-querying the DB. Halifax has unique PIDs, but the
    # cache hardens the script against future datasets with collisions.
    parcel_cache: dict[tuple[str, str], Parcel] = {}

    for index, feature in enumerate(features, start=1):
        parcel_identifier = _extract_parcel_identifier(feature)
        if parcel_identifier is None:
            stats.missing_identifier += 1
            stats.unmatched_features += 1
            logger.debug(
                "feature %d (%s) has no parcel identifier; skipping",
                feature.id,
                feature.feature_key,
            )
            continue

        geometry = feature.geometry_geojson or None
        polygon = _safe_shape(geometry)
        if polygon is None:
            stats.bad_geometry += 1
            stats.unmatched_features += 1
            logger.debug(
                "feature %d (%s) has unparseable geometry; skipping",
                feature.id,
                feature.feature_key,
            )
            continue

        cache_key = (HALIFAX_JURISDICTION, parcel_identifier)
        parcel = parcel_cache.get(cache_key)
        if parcel is None:
            parcel = _find_parcel(session, HALIFAX_JURISDICTION, parcel_identifier)
        if parcel is None:
            centroid_geojson = _centroid_geojson(polygon)
            area_m2 = _polygon_area_m2(polygon)
            zone_code = _zone_code_for_polygon(polygon, zoning_features)
            if zone_code is not None:
                stats.zone_lookups_successful += 1
            else:
                stats.zone_lookups_failed += 1
            parcel = Parcel(
                jurisdiction=HALIFAX_JURISDICTION,
                parcel_identifier=parcel_identifier,
                geometry_geojson=dict(geometry) if geometry else None,
                centroid_geojson=centroid_geojson,
                area_m2=area_m2,
                zone_code=zone_code,
                metadata_json={
                    "source_dataset": HALIFAX_PARCELS_DATASET_NAME,
                    "source_feature_id": feature.id,
                    "backfilled_via": "scripts/backfill_parcels.py",
                },
            )
            if not dry_run:
                session.add(parcel)
                session.flush()
            stats.parcels_inserted += 1
        else:
            stats.parcels_reused += 1

        parcel_cache[cache_key] = parcel
        if not dry_run:
            feature.parcel_id = parcel.id
        stats.features_linked += 1

        if index % 500 == 0:
            logger.info(
                "progress: %d / %d features (inserted=%d reused=%d)",
                index,
                stats.features_total,
                stats.parcels_inserted,
                stats.parcels_reused,
            )

    return stats


def _find_parcels_dataset(session: Session) -> ExternalDataset | None:
    return (
        session.execute(
            select(ExternalDataset).where(
                ExternalDataset.name == HALIFAX_PARCELS_DATASET_NAME
            )
        )
        .scalars()
        .first()
    )


def _find_zoning_dataset(session: Session) -> ExternalDataset | None:
    return (
        session.execute(
            select(ExternalDataset).where(
                ExternalDataset.name == HALIFAX_ZONING_DATASET_NAME
            )
        )
        .scalars()
        .first()
    )


def _find_parcel(session: Session, jurisdiction: str, identifier: str) -> Parcel | None:
    return (
        session.execute(
            select(Parcel).where(
                Parcel.jurisdiction == jurisdiction,
                Parcel.parcel_identifier == identifier,
            )
        )
        .scalars()
        .first()
    )


def _extract_parcel_identifier(feature: ExternalDatasetFeature) -> str | None:
    """Pull the canonical parcel id off the feature.

    Order of preference: canonical_attributes (the schema-mapped form;
    survives upstream attribute renames), then the raw attributes_json
    (in case canonical mapping hasn't run for some imports), then the
    feature_key as a last resort. Returned as a stripped string; None
    when nothing usable is present.
    """
    canonical = feature.canonical_attributes_json or {}
    raw = feature.attributes_json or {}
    for source in (canonical, raw):
        candidate = source.get(PARCEL_IDENTIFIER_CANONICAL_KEY) or source.get("PID")
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return text
    if feature.feature_key:
        return feature.feature_key.strip() or None
    return None


def _safe_shape(geometry: dict[str, Any] | None) -> BaseGeometry | None:
    if not geometry:
        return None
    try:
        geom = shapely_shape(geometry)
    except (ValueError, TypeError, KeyError):
        return None
    if geom.is_empty or not geom.is_valid:
        return None
    return geom


def _centroid_geojson(polygon: BaseGeometry) -> dict[str, Any]:
    """Return a GeoJSON Point for the polygon's centroid (EPSG:4326)."""
    centroid = polygon.centroid
    return {"type": "Point", "coordinates": [centroid.x, centroid.y]}


def _polygon_area_m2(polygon: BaseGeometry) -> float:
    """Estimate polygon area in m² via an equirectangular projection.

    pyproj isn't in the dependency set; the rest of the codebase uses
    the same equirectangular trick (see ``layer2.spatial.lot_metrics``).
    Error at parcel scale is well under 0.1% which is fine for a
    Phase-1 backfill — the displayed area drives only filter heuristics,
    not legal verdicts.
    """
    centroid = polygon.centroid
    lat = centroid.y
    project = _make_equirectangular_projector(lat, centroid.x)
    try:
        projected = shapely_transform(project, polygon)
    except Exception:  # noqa: BLE001 — shapely transform raises broadly
        return 0.0
    if projected.is_empty or not projected.is_valid:
        return 0.0
    area = float(projected.area)
    return round(area, 2)


def _make_equirectangular_projector(lat_origin_deg: float, lon_origin_deg: float):
    """Build a (lon, lat) -> (x_m, y_m) projector centred on the parcel."""
    cos_lat = math.cos(math.radians(lat_origin_deg))
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * cos_lat

    def project(lon: float, lat: float, z: float = 0.0) -> tuple[float, float]:
        return ((lon - lon_origin_deg) * m_per_deg_lon, (lat - lat_origin_deg) * m_per_deg_lat)

    return project


def _load_zoning_geometries(
    session: Session, zoning_dataset: ExternalDataset | None
) -> list[tuple[BaseGeometry, str]]:
    """Pre-load every zoning polygon as (shapely_geometry, zone_code).

    Returns ``[]`` when no zoning dataset is configured. Features
    without a zone_code (rare; usually a data-quality issue upstream)
    are dropped here so the point-in-polygon scan doesn't waste time
    on them.
    """
    if zoning_dataset is None:
        return []
    rows = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == zoning_dataset.id
            )
        )
        .scalars()
        .all()
    )
    out: list[tuple[BaseGeometry, str]] = []
    for row in rows:
        zone_code = (row.canonical_attributes_json or {}).get(ZONE_CODE_CANONICAL_KEY)
        if not zone_code:
            continue
        geom = _safe_shape(row.geometry_geojson)
        if geom is None:
            continue
        out.append((geom, str(zone_code)))
    logger.info("loaded %d zoning polygons", len(out))
    return out


def _zone_code_for_polygon(
    polygon: BaseGeometry, zoning_features: list[tuple[BaseGeometry, str]]
) -> str | None:
    """Return the zone_code whose polygon contains the parcel centroid.

    Centroid containment is the right semantics here: a parcel can
    nominally intersect multiple zones if it straddles a boundary, but
    the canonical "this parcel is in zone X" answer is the zone its
    centroid falls inside. Edge cases (centroid exactly on a boundary)
    are vanishingly rare with floating-point coordinates and resolved
    by picking the first match.
    """
    if not zoning_features:
        return None
    centroid = polygon.centroid
    for geom, zone_code in zoning_features:
        if geom.contains(centroid):
            return zone_code
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compute the backfill plan and report stats, but don't insert "
            "rows or set parcel_id FKs."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (otherwise read from the environment).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    started = time.monotonic()
    with session_scope(args.database_url) as session:
        stats = backfill(session, dry_run=args.dry_run)
    elapsed_s = time.monotonic() - started

    print(
        f"backfill_parcels: {stats.summary_line()} "
        f"elapsed_s={elapsed_s:.2f} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
