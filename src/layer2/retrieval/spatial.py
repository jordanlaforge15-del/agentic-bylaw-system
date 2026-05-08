from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from shapely.geometry import shape as shapely_shape
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment


# A resolved location is whatever the geocoder gave us back. Stage D supports
# the three primitive shapes; Phase E layers civic-address resolution on top
# without changing the spatial query interface.
ResolvedLocationKind = Literal["point", "shape", "parcel"]


@dataclass(frozen=True)
class ResolvedLocation:
    """A location ready to intersect against precinct features.

    ``geometry`` is a GeoJSON geometry dict in EPSG:4326 (the only CRS our
    datasets currently store, asserted at ingest). ``confidence`` is the
    geocoder's reported confidence; ``source`` names the resolver so
    citations can attribute the lookup.
    """

    kind: ResolvedLocationKind
    geometry: dict[str, Any]
    confidence: float = 1.0
    source: str = "direct"
    reference_text: str | None = None


@dataclass(frozen=True)
class FeatureMatch:
    feature: ExternalDatasetFeature
    overlap_area: float  # square degrees in 4326 — coarse, fine for ordering
    contains_input: bool


def query_features(
    session: Session,
    *,
    dataset_id: int,
    location: ResolvedLocation,
) -> list[FeatureMatch]:
    """Intersect a resolved location against an external dataset's features.

    PostgreSQL/PostGIS path: spatial filter at the SQL layer using
    ``ST_Intersects`` against the GiST-indexed ``geometry`` column.
    A single round-trip returns the matching feature ids plus the
    overlap metric and contains_input flag, then we hydrate the
    matched ORM rows in one bulk query. At Halifax scale (~11k zoning
    polygons + smaller schedules) this drops query_features from the
    sequential-scan cost of ~2.6 s to a few ms.

    SQLite path (test suite): falls back to the legacy shapely loop
    so behaviour tests keep working without PostGIS.
    """
    try:
        location_geom = shapely_shape(location.geometry)
    except (ValueError, TypeError, KeyError):
        return []
    if not location_geom.is_valid or location_geom.is_empty:
        return []

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return _query_features_postgis(
            session, dataset_id=dataset_id, location_geom=location_geom
        )
    return _query_features_shapely(
        session, dataset_id=dataset_id, location_geom=location_geom
    )


def _query_features_postgis(
    session: Session,
    *,
    dataset_id: int,
    location_geom: Any,
) -> list[FeatureMatch]:
    # Pass the geometry as GeoJSON text — ST_GeomFromGeoJSON is the
    # cleanest round-trip for any shapely geometry type and avoids the
    # WKT-vs-EWKT SRID dance. We supply the geometry once via CTE so
    # the planner sees a single constant geometry across the SELECT.
    geojson = json.dumps(location_geom.__geo_interface__)
    sql = text(
        """
        WITH input_geom AS (
          SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326) AS g
        )
        SELECT
          edf.id AS feature_id,
          ST_Contains(edf.geometry, ig.g) AS contains_input,
          CASE
            WHEN GeometryType(ig.g) IN ('POINT', 'MULTIPOINT')
              THEN 1.0
            WHEN GeometryType(ig.g) IN ('LINESTRING', 'MULTILINESTRING')
              THEN ST_Length(ST_Intersection(edf.geometry, ig.g))
            ELSE
              ST_Area(ST_Intersection(edf.geometry, ig.g))
          END AS overlap_metric
        FROM external_dataset_feature edf
        CROSS JOIN input_geom ig
        WHERE edf.external_dataset_id = :ds_id
          AND edf.geometry IS NOT NULL
          AND ST_Intersects(edf.geometry, ig.g)
        ORDER BY overlap_metric DESC, contains_input DESC
        """
    )
    rows = session.execute(
        sql, {"geojson": geojson, "ds_id": dataset_id}
    ).all()
    if not rows:
        return []
    ids = [r.feature_id for r in rows]
    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.id.in_(ids)
            )
        )
        .scalars()
        .all()
    )
    by_id = {f.id: f for f in features}
    matches: list[FeatureMatch] = []
    for r in rows:
        feature = by_id.get(r.feature_id)
        if feature is None:
            continue
        matches.append(
            FeatureMatch(
                feature=feature,
                overlap_area=float(r.overlap_metric or 0.0),
                contains_input=bool(r.contains_input),
            )
        )
    return matches


def _query_features_shapely(
    session: Session,
    *,
    dataset_id: int,
    location_geom: Any,
) -> list[FeatureMatch]:
    # Legacy fallback for the sqlite test path. Keep the original
    # bbox-prefilter + shapely-intersect loop; the prod call site
    # routes to ``_query_features_postgis`` above.
    minx, miny, maxx, maxy = location_geom.bounds
    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == dataset_id
            )
        )
        .scalars()
        .all()
    )

    matches: list[FeatureMatch] = []
    for feature in features:
        bbox = feature.geometry_bbox_json or {}
        if (
            bbox.get("maxx", float("inf")) < minx
            or bbox.get("minx", float("-inf")) > maxx
            or bbox.get("maxy", float("inf")) < miny
            or bbox.get("miny", float("-inf")) > maxy
        ):
            continue
        feature_geom = shapely_shape(feature.geometry_geojson)
        if not feature_geom.is_valid or not feature_geom.intersects(location_geom):
            continue
        overlap = feature_geom.intersection(location_geom)
        contains = feature_geom.contains(location_geom)
        if location_geom.geom_type in {"Point", "MultiPoint"}:
            overlap_metric = 1.0 if not overlap.is_empty else 0.0
        elif location_geom.geom_type in {"LineString", "MultiLineString"}:
            overlap_metric = overlap.length
        else:
            overlap_metric = overlap.area
        matches.append(
            FeatureMatch(
                feature=feature,
                overlap_area=float(overlap_metric),
                contains_input=contains,
            )
        )
    matches.sort(key=lambda m: (-m.overlap_area, not m.contains_input))
    return matches


def expand_spatial(
    session: Session,
    candidates: list[CandidateFragment],
    *,
    location: ResolvedLocation | None,
) -> list[CandidateFragment]:
    """Emit DATASET_FEATURE candidates whenever a DATASET candidate is in the
    stream and a location is active. The location parameter survives across
    multiple datasets in the same query (height + FAR + zone overlay), so we
    don't consume it after one match.
    """
    if location is None:
        return list(candidates)

    seen_feature_ids = {
        c.external_dataset_feature_id
        for c in candidates
        if c.external_dataset_feature_id is not None
    }
    expanded = list(candidates)
    for candidate in list(candidates):
        if candidate.source_type != SourceType.DATASET.value:
            continue
        if candidate.external_dataset_id is None:
            continue
        matches = query_features(session, dataset_id=candidate.external_dataset_id, location=location)
        if not matches:
            continue
        dataset = session.get(ExternalDataset, candidate.external_dataset_id)
        for match in matches:
            if match.feature.id in seen_feature_ids:
                continue
            expanded.append(_feature_to_candidate(match, dataset, candidate, location))
            seen_feature_ids.add(match.feature.id)
    return expanded


def _feature_to_candidate(
    match: FeatureMatch,
    dataset: ExternalDataset | None,
    parent_candidate: CandidateFragment,
    location: ResolvedLocation,
) -> CandidateFragment:
    canonical = match.feature.canonical_attributes_json or {}
    parts = []
    label = canonical.get("display_label")
    if label:
        parts.append(label)
    height_m = canonical.get("max_height_m")
    height_storeys = canonical.get("max_height_storeys")
    if height_m is not None:
        parts.append(f"max_height_m={height_m:g}")
    if height_storeys is not None:
        parts.append(f"max_height_storeys={height_storeys}")
    if height_m is None and height_storeys is None:
        parts.append("no maximum height specified")
    case = canonical.get("source_case")
    if case:
        parts.append(f"source_case={case}")
    citation = (
        parent_candidate.citation_label
        or (dataset.linked_fragment_citation if dataset else None)
        or "(unlinked)"
    )
    text = f"{citation} feature: " + ", ".join(parts) if parts else f"{citation} feature {match.feature.feature_key}"
    return CandidateFragment(
        source_fragment_id=parent_candidate.source_fragment_id,
        external_dataset_id=match.feature.external_dataset_id,
        external_dataset_feature_id=match.feature.id,
        source_type=SourceType.DATASET_FEATURE.value,
        retrieval_channel=RetrievalChannel.SPATIAL.value,
        base_score=0.7 + (0.1 if match.contains_input else 0.0),
        text=text,
        citation_label=parent_candidate.citation_label,
        citation_path=parent_candidate.citation_path,
        reason={
            "expansion": "spatial",
            "feature_key": match.feature.feature_key,
            "overlap_area": match.overlap_area,
            "contains_input": match.contains_input,
            "location_source": location.source,
            "location_kind": location.kind,
            "location_reference_text": location.reference_text,
        },
        metadata={
            "canonical_attributes": canonical,
            "geometry_bbox": match.feature.geometry_bbox_json,
        },
    )
