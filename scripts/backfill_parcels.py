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
  the feature's geometry.

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
from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope


HALIFAX_JURISDICTION = "HRM"
HALIFAX_PARCELS_DATASET_NAME = "halifax_property_parcels"

# Canonical attribute key the Halifax parcels YAML maps ``PID`` to. See
# ``src/layer1/datasets/halifax_property_parcels.yaml`` — feature_key is
# also ``PID``, but we read from canonical_attributes so a future
# schema-mapping tweak doesn't silently break this script.
PARCEL_IDENTIFIER_CANONICAL_KEY = "parcel_id"


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

    def summary_line(self) -> str:
        return (
            f"features={self.features_total} "
            f"inserted={self.parcels_inserted} "
            f"reused={self.parcels_reused} "
            f"linked={self.features_linked} "
            f"unmatched={self.unmatched_features} "
            f"bad_geometry={self.bad_geometry} "
            f"missing_identifier={self.missing_identifier}"
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

    stats = BackfillStats()
    # Pre-count for progress logging, but stream the actual feature rows
    # via yield_per so we don't materialize all ~150k Halifax parcel
    # features (with full geometry_geojson payloads) in memory at once.
    # The earlier .all() flavour OOM'd inside a 3 GB container against
    # prod-sized data. Streaming + per-batch expunge keeps RSS flat.
    from sqlalchemy import func

    stats.features_total = (
        session.execute(
            select(func.count(ExternalDatasetFeature.id)).where(
                ExternalDatasetFeature.external_dataset_id == parcels_dataset.id
            )
        )
        .scalar_one()
    )
    logger.info("processing %d parcel features", stats.features_total)

    # Cache parcels by (jurisdiction, identifier) — store only the int
    # `parcel.id` (post-flush), not the full Parcel ORM instance. Halifax
    # has unique PIDs, so we accumulate ~180k cache entries; keeping the
    # whole ORM instance (with its geometry_geojson dict) blew past 2 GB
    # in the streamed run that processed 148k/182k features before OOM.
    # An int per entry is ~28 bytes; an instance is multiple kB.
    parcel_id_cache: dict[tuple[str, str], int] = {}

    feature_stream = (
        session.execute(
            select(ExternalDatasetFeature)
            .where(ExternalDatasetFeature.external_dataset_id == parcels_dataset.id)
            .execution_options(yield_per=500)
        )
        .scalars()
    )

    for index, feature in enumerate(feature_stream, start=1):
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
        parcel_id = parcel_id_cache.get(cache_key)
        if parcel_id is None:
            existing = _find_parcel(session, HALIFAX_JURISDICTION, parcel_identifier)
            if existing is not None:
                parcel_id = existing.id
                stats.parcels_reused += 1
                if not dry_run:
                    session.expunge(existing)
            else:
                centroid_geojson = _centroid_geojson(polygon)
                area_m2 = _polygon_area_m2(polygon)
                parcel = Parcel(
                    jurisdiction=HALIFAX_JURISDICTION,
                    parcel_identifier=parcel_identifier,
                    geometry_geojson=dict(geometry) if geometry else None,
                    centroid_geojson=centroid_geojson,
                    area_m2=area_m2,
                    metadata_json={
                        "source_dataset": HALIFAX_PARCELS_DATASET_NAME,
                        "source_feature_id": feature.id,
                        "backfilled_via": "scripts/backfill_parcels.py",
                    },
                )
                if not dry_run:
                    session.add(parcel)
                    session.flush()
                    parcel_id = parcel.id
                    # Done with this Parcel — drop it from the identity map
                    # so the next 150k inserts don't accumulate.
                    session.expunge(parcel)
                else:
                    parcel_id = -1  # sentinel — never read back in dry-run
                stats.parcels_inserted += 1
            parcel_id_cache[cache_key] = parcel_id

        if not dry_run:
            feature.parcel_id = parcel_id
        stats.features_linked += 1

        if index % 500 == 0:
            logger.info(
                "progress: %d / %d features (inserted=%d reused=%d)",
                index,
                stats.features_total,
                stats.parcels_inserted,
                stats.parcels_reused,
            )
            # Flush pending parcel inserts + feature.parcel_id UPDATEs so
            # the per-batch session work gets pushed to the DB, then expunge
            # the *features* (we no longer need them) to drop their
            # geometry_geojson payloads from the identity map. Parcels stay
            # tracked by design.
            if not dry_run:
                session.flush()
            for cached_feature in [
                obj for obj in list(session.identity_map.values())
                if isinstance(obj, ExternalDatasetFeature)
            ]:
                session.expunge(cached_feature)

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

    if not args.dry_run:
        # ABS-499: no unfenced write to the dev corpus.
        fence_or_abort("backfill-parcels", database_url=args.database_url)

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
