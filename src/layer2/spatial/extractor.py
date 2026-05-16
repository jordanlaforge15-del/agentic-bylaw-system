"""Case-open spatial-facts extractor.

``extract_lot_facts`` is the orchestrator called from ``cases_router`` at
``POST /v1/cases``. It threads the existing geocoder, the parcels
dataset, and ``compute_lot_metrics`` together, and returns a dict
shaped for ``Case.metadata_json``.

The function never raises: any failure (geocode miss, no parcel match,
invalid geometry, slow ingest) returns ``{"status": "unresolved",
"reason": ...}`` so the caller can persist an explicit absence rather
than fail case creation.

The chat layer reads the persisted dict and renders it via
``format_lot_facts_block`` into the system prompt suffix.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import Point
from shapely.geometry import shape as shapely_shape
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer2.retrieval.geocode import resolve_location
from layer2.retrieval.location import extract_location_references
from layer2.retrieval.spatial import ResolvedLocation
from layer2.spatial.lot_metrics import LotMetrics, compute_lot_metrics

logger = logging.getLogger(__name__)


# Dataset role marker matching ``layer1.datasets.config.DatasetRole``.
PARCELS_ROLE = "property_parcels"
CIVIC_ADDRESS_ROLE = "civic_address"


def extract_lot_facts(
    db: Session,
    *,
    anchor_label: str,
    anchor_kind: str,
) -> dict[str, Any]:
    """Compute lot facts for an anchor and return a metadata_json fragment.

    The returned dict is always populated. On success it carries the
    full LotMetrics payload plus PID and provenance. On any failure
    it carries ``{"status": "unresolved", "reason": "..."}`` — the
    caller persists either shape under ``Case.metadata_json["spatial_facts"]``.
    """
    base: dict[str, Any] = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    if anchor_kind != "address":
        return _unresolved(base, "anchor_kind is not 'address'")

    try:
        return _extract_inner(db, anchor_label, base)
    except Exception as exc:  # noqa: BLE001 — never block case open on spatial errors
        logger.exception("lot_facts extraction failed: %s", exc)
        return _unresolved(base, f"extraction error: {type(exc).__name__}")


def format_lot_facts_block(spatial_facts: dict[str, Any] | None) -> str:
    """Render a ``<lot_facts>`` XML block for the system prompt suffix.

    Compact, deterministic format the model can parse without
    consuming many tokens. When ``spatial_facts`` is missing or empty,
    returns the empty string so callers can unconditionally concatenate.
    """
    if not spatial_facts:
        return ""
    status = spatial_facts.get("status")
    if status in (None, "unresolved"):
        reason = spatial_facts.get("reason") or "unknown"
        return f"<lot_facts>status=unresolved reason={reason}</lot_facts>"

    parts = [f"status={status}"]
    for key in (
        "pid",
        "area_m2",
        "frontage_m",
        "depth_m",
        "perimeter_m",
        "corner",
        "multi_unit",
        "confidence",
        "method",
    ):
        if key in spatial_facts and spatial_facts[key] is not None:
            value = spatial_facts[key]
            if isinstance(value, bool):
                value_str = "true" if value else "false"
            else:
                value_str = str(value)
            parts.append(f"{key}={value_str}")
    return "<lot_facts>" + " ".join(parts) + "</lot_facts>"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _extract_inner(
    db: Session, anchor_label: str, base: dict[str, Any]
) -> dict[str, Any]:
    refs = extract_location_references(anchor_label)
    if not refs:
        return _unresolved(
            base,
            "could not parse anchor as a civic address or PID",
        )
    ref = refs[0]
    resolved = resolve_location(db, ref)
    if resolved is None:
        return _unresolved(base, "geocoder could not resolve anchor")

    parcels_dataset_id = _find_parcels_dataset_id(db)
    if parcels_dataset_id is None:
        return _unresolved(
            base,
            "no property_parcels dataset is ingested; run the parcels ingest",
        )

    point = _representative_point(resolved)
    if point is None:
        return _unresolved(base, "resolved geometry has no usable centroid")

    parcel_feature = _find_containing_parcel(
        db, dataset_id=parcels_dataset_id, point=point
    )
    if parcel_feature is None:
        return _unresolved(
            base,
            "geocoded point is not inside any parcel polygon",
        )

    # Part 1 surfaces area + perimeter only. Frontage / depth / corner
    # need a road centerline dataset (Part 2). The shared-edge heuristic
    # fails on HRM's parcel layer because parcels tessellate edge-to-edge
    # to the road centerline — every street-facing edge of a residential
    # lot is ε-close to the parcel directly across the street, so the
    # heuristic classifies it as "shared with a neighbour" and frontage
    # collapses to zero. We skip the neighbour fetch entirely (one fewer
    # spatial query per case open) and only compute what's reliable.
    metrics = compute_lot_metrics(parcel_feature.geometry_geojson, [])
    if metrics.status == "unresolved":
        return _unresolved(base, metrics.reason or "lot metrics unresolved")

    multi_unit = _detect_multi_unit(
        db, parcel_geojson=parcel_feature.geometry_geojson
    )

    pid = (parcel_feature.canonical_attributes_json or {}).get("parcel_id")
    base.update(
        {
            "status": metrics.status,
            "method": "parcel_area",
            "pid": pid,
            "parcel_feature_id": parcel_feature.id,
            "anchor_source": resolved.source,
            "anchor_confidence": resolved.confidence,
        }
    )
    if metrics.area_m2 is not None:
        base["area_m2"] = round(metrics.area_m2, 1)
    if metrics.perimeter_m is not None:
        base["perimeter_m"] = round(metrics.perimeter_m, 2)
    # Confidence is 1.0 when the polygon was valid (area is a clean
    # PostGIS ST_Area); compute_lot_metrics drops to "uncertain" only
    # when shapely had to repair the geometry, which can leave the area
    # slightly off. The shared-edge confidence (used to be 0.6 with no
    # neighbours) is irrelevant for the area-only output.
    base["confidence"] = 1.0 if metrics.status == "ok" else 0.7
    if multi_unit is not None:
        base["multi_unit"] = multi_unit
    return base


def _unresolved(base: dict[str, Any], reason: str) -> dict[str, Any]:
    base.update({"status": "unresolved", "reason": reason})
    return base


def _find_parcels_dataset_id(db: Session) -> int | None:
    rows = db.execute(
        select(ExternalDataset.id, ExternalDataset.metadata_json)
    ).all()
    for row in rows:
        if (row.metadata_json or {}).get("role") == PARCELS_ROLE:
            return int(row.id)
    return None


def _representative_point(resolved: ResolvedLocation) -> Point | None:
    """Return a Point in EPSG:4326 representing the resolved location.

    Used to ST_Contains-test against the parcels dataset. A polygon
    resolution (parcel-id direct lookup) is collapsed to its
    centroid; a point resolution returns itself.
    """
    try:
        geom = shapely_shape(resolved.geometry)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    if geom.is_empty or not geom.is_valid:
        return None
    if geom.geom_type == "Point":
        return geom
    centroid = geom.centroid
    if centroid.is_empty:
        return None
    return centroid


def _find_containing_parcel(
    db: Session,
    *,
    dataset_id: int,
    point: Point,
) -> ExternalDatasetFeature | None:
    """Return the parcel feature whose polygon contains ``point``.

    Uses PostGIS ``ST_Contains`` when available and falls back to the
    shapely bbox-prefilter loop on SQLite (the test path). When the
    point lies exactly on a shared parcel boundary either neighbour
    is acceptable; PostGIS returns the highest-id row, shapely
    returns the first match in scan order. For lot-metrics purposes
    the choice doesn't matter — the containing parcel is the
    homeowner's, and the boundary case is vanishingly rare for
    real-world civic-address geocodes.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        geojson = json.dumps(point.__geo_interface__)
        sql = text(
            """
            SELECT edf.id AS feature_id
            FROM external_dataset_feature edf
            WHERE edf.external_dataset_id = :ds_id
              AND edf.geometry IS NOT NULL
              AND ST_Contains(
                  edf.geometry,
                  ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326)
              )
            LIMIT 1
            """
        )
        row = db.execute(sql, {"geojson": geojson, "ds_id": dataset_id}).first()
        if row is None:
            return None
        return db.get(ExternalDatasetFeature, int(row.feature_id))

    # SQLite fallback — bbox prefilter, then shapely contains.
    px, py = point.x, point.y
    features = (
        db.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == dataset_id
            )
        )
        .scalars()
        .all()
    )
    for feature in features:
        bbox = feature.geometry_bbox_json or {}
        if (
            bbox.get("minx", float("-inf")) > px
            or bbox.get("maxx", float("inf")) < px
            or bbox.get("miny", float("-inf")) > py
            or bbox.get("maxy", float("inf")) < py
        ):
            continue
        try:
            geom = shapely_shape(feature.geometry_geojson)
        except (TypeError, ValueError, KeyError):
            continue
        if geom.is_valid and geom.contains(point):
            return feature
    return None


def _find_neighbour_parcels(
    db: Session,
    *,
    dataset_id: int,
    parcel_feature: ExternalDatasetFeature,
) -> list[ExternalDatasetFeature]:
    """Return parcels in the same dataset whose boundary touches ``parcel_feature``.

    PostGIS uses ``ST_Touches`` (shared edge, disjoint interiors) plus
    ``ST_Intersects`` on bbox for performance. The SQLite fallback
    uses shapely's ``.touches()`` on the bbox-prefiltered candidate
    set — adequate at test scale.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        sql = text(
            """
            SELECT other.id AS feature_id
            FROM external_dataset_feature other
            JOIN external_dataset_feature anchor
              ON anchor.id = :anchor_id
            WHERE other.external_dataset_id = :ds_id
              AND other.id <> anchor.id
              AND other.geometry IS NOT NULL
              AND anchor.geometry IS NOT NULL
              AND ST_Touches(anchor.geometry, other.geometry)
            """
        )
        rows = db.execute(
            sql, {"anchor_id": parcel_feature.id, "ds_id": dataset_id}
        ).all()
        if not rows:
            return []
        ids = [int(r.feature_id) for r in rows]
        return list(
            db.execute(
                select(ExternalDatasetFeature).where(
                    ExternalDatasetFeature.id.in_(ids)
                )
            )
            .scalars()
            .all()
        )

    # SQLite fallback.
    try:
        anchor_geom = shapely_shape(parcel_feature.geometry_geojson)
    except (TypeError, ValueError, KeyError):
        return []
    if not anchor_geom.is_valid:
        return []
    a_bbox = parcel_feature.geometry_bbox_json or {}
    a_minx = a_bbox.get("minx", float("-inf"))
    a_maxx = a_bbox.get("maxx", float("inf"))
    a_miny = a_bbox.get("miny", float("-inf"))
    a_maxy = a_bbox.get("maxy", float("inf"))

    candidates = (
        db.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == dataset_id,
                ExternalDatasetFeature.id != parcel_feature.id,
            )
        )
        .scalars()
        .all()
    )
    out: list[ExternalDatasetFeature] = []
    for candidate in candidates:
        c_bbox = candidate.geometry_bbox_json or {}
        if (
            c_bbox.get("maxx", float("inf")) < a_minx
            or c_bbox.get("minx", float("-inf")) > a_maxx
            or c_bbox.get("maxy", float("inf")) < a_miny
            or c_bbox.get("miny", float("-inf")) > a_maxy
        ):
            continue
        try:
            c_geom = shapely_shape(candidate.geometry_geojson)
        except (TypeError, ValueError, KeyError):
            continue
        if c_geom.is_valid and anchor_geom.touches(c_geom):
            out.append(candidate)
    return out


def _detect_multi_unit(
    db: Session,
    *,
    parcel_geojson: dict[str, Any],
) -> bool | None:
    """Return True when the parcel contains 2+ civic-address points.

    Returns ``None`` when no civic-address dataset is loaded — the
    flag is then omitted from the persisted facts rather than
    asserted as False. The chat layer treats absence as "unknown".
    """
    civic_dataset_ids = [
        int(row.id)
        for row in db.execute(
            select(ExternalDataset.id, ExternalDataset.metadata_json)
        ).all()
        if (row.metadata_json or {}).get("role") == CIVIC_ADDRESS_ROLE
    ]
    if not civic_dataset_ids:
        return None

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        sql = text(
            """
            SELECT COUNT(*) AS n
            FROM external_dataset_feature edf
            WHERE edf.external_dataset_id = ANY(:ds_ids)
              AND edf.geometry IS NOT NULL
              AND ST_Contains(
                  ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326),
                  edf.geometry
              )
            """
        )
        row = db.execute(
            sql,
            {
                "ds_ids": civic_dataset_ids,
                "geojson": json.dumps(parcel_geojson),
            },
        ).first()
        return bool(row is not None and int(row.n) >= 2)

    # SQLite fallback.
    try:
        parcel_geom = shapely_shape(parcel_geojson)
    except (TypeError, ValueError, KeyError):
        return None
    if not parcel_geom.is_valid:
        return None
    count = 0
    features = (
        db.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id.in_(civic_dataset_ids)
            )
        )
        .scalars()
        .all()
    )
    for feature in features:
        try:
            point_geom = shapely_shape(feature.geometry_geojson)
        except (TypeError, ValueError, KeyError):
            continue
        if point_geom.is_valid and parcel_geom.contains(point_geom):
            count += 1
            if count >= 2:
                return True
    return False
