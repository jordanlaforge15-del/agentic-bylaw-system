from __future__ import annotations

import json
from dataclasses import dataclass
from math import cos, radians
from typing import Any, Literal

from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer2.models.enums import RetrievalChannel, SourceType
from layer2.models.schemas import CandidateFragment


# A resolved location is whatever the geocoder gave us back. Stage D supports
# the three primitive shapes; Phase E layers civic-address resolution on top
# without changing the spatial query interface.
ResolvedLocationKind = Literal["point", "shape", "parcel"]

# Spatial predicate a query_features call evaluates:
#   "intersects" — point-in-polygon / geometric intersection (the precinct
#      overlays: zoning, height, FAR, heritage). A non-match is a definitive
#      negative because the point is either inside a polygon or it isn't.
#   "abuts" — nearest designated feature lies within ``abut_distance_m`` of the
#      location. This is the right test for LINE datasets (Schedule 7
#      pedestrian-oriented commercial streets): a geocoded point never falls
#      exactly on a street centreline, so ST_Intersects would report a
#      spurious negative for every address. Buffering the point against the
#      nearest segment answers "does this lot abut a designated street".
SpatialPredicate = Literal["intersects", "abuts"]

# Default abut buffer. A geocoded civic point sits at the building/parcel
# centroid, typically 10–25 m off the street centreline; 30 m comfortably
# spans a parcel + right-of-way half-width without bleeding onto the parallel
# street one block over. Matches the 30 m distance the ABS-350 dataset
# verification used against 6184 Quinpool Rd.
DEFAULT_ABUT_DISTANCE_M = 30.0


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
    predicate: SpatialPredicate = "intersects",
    abut_distance_m: float = DEFAULT_ABUT_DISTANCE_M,
) -> list[FeatureMatch]:
    """Intersect (or abut) a resolved location against a dataset's features.

    PostgreSQL/PostGIS path: spatial filter at the SQL layer using
    ``ST_Intersects`` against the GiST-indexed ``geometry`` column.
    A single round-trip returns the matching feature ids plus the
    overlap metric and contains_input flag, then we hydrate the
    matched ORM rows in one bulk query. At Halifax scale (~11k zoning
    polygons + smaller schedules) this drops query_features from the
    sequential-scan cost of ~2.6 s to a few ms.

    ``predicate="abuts"`` switches to a distance test (``ST_DWithin`` on
    geography, so ``abut_distance_m`` is real metres) for LINE datasets whose
    features a geocoded point never lands exactly on — Schedule 7
    pedestrian-oriented commercial streets. Matches sort nearest-first and
    every abut match reports ``contains_input=True`` (a within-buffer hit is
    the definitive positive for the abuts semantics; there is no weaker
    partial-overlap tier).

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
            session,
            dataset_id=dataset_id,
            location_geom=location_geom,
            predicate=predicate,
            abut_distance_m=abut_distance_m,
        )
    return _query_features_shapely(
        session,
        dataset_id=dataset_id,
        location_geom=location_geom,
        predicate=predicate,
        abut_distance_m=abut_distance_m,
    )


def _query_features_postgis(
    session: Session,
    *,
    dataset_id: int,
    location_geom: Any,
    predicate: SpatialPredicate = "intersects",
    abut_distance_m: float = DEFAULT_ABUT_DISTANCE_M,
) -> list[FeatureMatch]:
    if predicate == "abuts":
        return _query_features_postgis_abuts(
            session,
            dataset_id=dataset_id,
            location_geom=location_geom,
            abut_distance_m=abut_distance_m,
        )
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


def _query_features_postgis_abuts(
    session: Session,
    *,
    dataset_id: int,
    location_geom: Any,
    abut_distance_m: float,
) -> list[FeatureMatch]:
    # Distance-based ``abuts`` predicate for LINE datasets (Schedule 7). Cast
    # both geometries to ``geography`` so ST_DWithin / ST_Distance measure in
    # metres regardless of the 4326 degree units, and let the GiST index on
    # the geography-cast still prefilter via ST_DWithin. ``proximity`` is a
    # monotone-decreasing-in-distance score (nearest ~ 1.0) so the caller's
    # "strongest match wins" ordering keeps working — matches[0] is the
    # closest designated segment, whose street name we cite.
    geojson = json.dumps(location_geom.__geo_interface__)
    sql = text(
        """
        WITH input_geom AS (
          SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326) AS g
        )
        SELECT
          edf.id AS feature_id,
          ST_Distance(edf.geometry::geography, ig.g::geography) AS distance_m,
          1.0 / (1.0 + ST_Distance(edf.geometry::geography, ig.g::geography))
            AS proximity
        FROM external_dataset_feature edf
        CROSS JOIN input_geom ig
        WHERE edf.external_dataset_id = :ds_id
          AND edf.geometry IS NOT NULL
          AND ST_DWithin(edf.geometry::geography, ig.g::geography, :dist_m)
        ORDER BY distance_m ASC
        """
    )
    rows = session.execute(
        sql, {"geojson": geojson, "ds_id": dataset_id, "dist_m": abut_distance_m}
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
                overlap_area=float(r.proximity or 0.0),
                # A within-buffer hit is the definitive abut positive — there
                # is no weaker overlap tier for a line, so flag every match as
                # a strong (contains) hit for scoring parity with polygons.
                contains_input=True,
            )
        )
    return matches


def _query_features_shapely(
    session: Session,
    *,
    dataset_id: int,
    location_geom: Any,
    predicate: SpatialPredicate = "intersects",
    abut_distance_m: float = DEFAULT_ABUT_DISTANCE_M,
) -> list[FeatureMatch]:
    # Legacy fallback for the sqlite test path. Keep the original
    # bbox-prefilter + shapely-intersect loop; the prod call site
    # routes to ``_query_features_postgis`` above.
    if predicate == "abuts":
        return _query_features_shapely_abuts(
            session,
            dataset_id=dataset_id,
            location_geom=location_geom,
            abut_distance_m=abut_distance_m,
        )
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


def _query_features_shapely_abuts(
    session: Session,
    *,
    dataset_id: int,
    location_geom: Any,
    abut_distance_m: float,
) -> list[FeatureMatch]:
    # SQLite/shapely abuts fallback. Shapely measures distance in the raw
    # 4326 degree plane, which is anisotropic (a degree of longitude is
    # shorter than a degree of latitude away from the equator), so project
    # both geometries into a local equirectangular metre frame centred on the
    # location before measuring. That keeps ``abut_distance_m`` a real-metre
    # threshold and matches the PostGIS geography path closely enough that the
    # unit tests exercise the same predicate the prod stack runs.
    origin = location_geom.representative_point()
    lat0, lon0 = origin.y, origin.x
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * cos(radians(lat0))

    def to_metres(x: Any, y: Any, z: Any = None) -> tuple[Any, Any]:
        return ((x - lon0) * m_per_deg_lon, (y - lat0) * m_per_deg_lat)

    location_m = shapely_transform(to_metres, location_geom)
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
        try:
            feature_geom = shapely_shape(feature.geometry_geojson)
        except (ValueError, TypeError, KeyError):
            continue
        if not feature_geom.is_valid or feature_geom.is_empty:
            continue
        distance_m = shapely_transform(to_metres, feature_geom).distance(location_m)
        if distance_m > abut_distance_m:
            continue
        matches.append(
            FeatureMatch(
                feature=feature,
                overlap_area=1.0 / (1.0 + distance_m),
                contains_input=True,
            )
        )
    matches.sort(key=lambda m: -m.overlap_area)
    return matches


# Default abut buffer for parcel-to-parcel adjacency (ABS-375). HRM's parcel
# fabric is topologically clean along shared lot lines, but reprojection to
# 4326 and the source's coordinate precision leave sub-metre slivers between
# nominally-touching parcels, so a strict ST_Touches misses real neighbours.
# 1 m closes those slivers without reaching across a right-of-way to the lot on
# the far side of a lane (Halifax rear lanes are ~6 m). The subject parcel
# itself is always excluded from the result regardless of buffer.
DEFAULT_PARCEL_ABUT_BUFFER_M = 1.0


def find_containing_feature(
    session: Session,
    *,
    dataset_ids: list[int],
    point: Any,
) -> ExternalDatasetFeature | None:
    """Return the feature (across ``dataset_ids``) whose polygon contains ``point``.

    ``point`` is a shapely geometry in EPSG:4326. Used to resolve the
    subject parcel for the adjacent-zoning lookup (ABS-375). PostGIS uses
    ``ST_Contains``; SQLite falls back to a bbox-prefilter + shapely loop so
    the unit tests exercise the same contract without PostGIS.
    """
    if not dataset_ids:
        return None
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        geojson = json.dumps(point.__geo_interface__)
        sql = text(
            """
            SELECT edf.id AS feature_id
            FROM external_dataset_feature edf
            WHERE edf.external_dataset_id = ANY(:ds_ids)
              AND edf.geometry IS NOT NULL
              AND ST_Contains(
                  edf.geometry,
                  ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326)
              )
            LIMIT 1
            """
        )
        row = session.execute(
            sql, {"geojson": geojson, "ds_ids": list(dataset_ids)}
        ).first()
        if row is None:
            return None
        return session.get(ExternalDatasetFeature, int(row.feature_id))

    px, py = point.x, point.y
    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id.in_(dataset_ids)
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


def find_abutting_features(
    session: Session,
    *,
    dataset_ids: list[int],
    subject: ExternalDatasetFeature,
    buffer_m: float = DEFAULT_PARCEL_ABUT_BUFFER_M,
) -> list[ExternalDatasetFeature]:
    """Return every feature that touches (or lies within ``buffer_m`` of) ``subject``.

    Excludes ``subject`` itself. Used to enumerate the parcels abutting the
    subject parcel for the adjacent-zoning lookup (ABS-375). PostGIS uses a
    geography ``ST_DWithin`` so ``buffer_m`` is real metres; SQLite projects
    into a local metre frame and measures shapely distance, matching the
    PostGIS predicate closely enough for the unit tests.
    """
    if not dataset_ids:
        return []
    try:
        subject_geom = shapely_shape(subject.geometry_geojson)
    except (TypeError, ValueError, KeyError):
        return []
    if not subject_geom.is_valid or subject_geom.is_empty:
        return []

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        geojson = json.dumps(subject_geom.__geo_interface__)
        sql = text(
            """
            WITH input_geom AS (
              SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326) AS g
            )
            SELECT edf.id AS feature_id
            FROM external_dataset_feature edf
            CROSS JOIN input_geom ig
            WHERE edf.external_dataset_id = ANY(:ds_ids)
              AND edf.id <> :subject_id
              AND edf.geometry IS NOT NULL
              AND ST_DWithin(edf.geometry::geography, ig.g::geography, :buf_m)
            """
        )
        rows = session.execute(
            sql,
            {
                "geojson": geojson,
                "ds_ids": list(dataset_ids),
                "subject_id": subject.id,
                "buf_m": buffer_m,
            },
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
        return list(features)

    # SQLite/shapely fallback — project into a local metre frame so the
    # buffer threshold is real metres, mirroring the PostGIS geography path.
    origin = subject_geom.representative_point()
    lat0, lon0 = origin.y, origin.x
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * cos(radians(lat0))

    def to_metres(x: Any, y: Any, z: Any = None) -> tuple[Any, Any]:
        return ((x - lon0) * m_per_deg_lon, (y - lat0) * m_per_deg_lat)

    subject_m = shapely_transform(to_metres, subject_geom)
    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id.in_(dataset_ids)
            )
        )
        .scalars()
        .all()
    )
    out: list[ExternalDatasetFeature] = []
    for feature in features:
        if feature.id == subject.id:
            continue
        try:
            geom = shapely_shape(feature.geometry_geojson)
        except (TypeError, ValueError, KeyError):
            continue
        if not geom.is_valid or geom.is_empty:
            continue
        distance_m = shapely_transform(to_metres, geom).distance(subject_m)
        if distance_m <= buffer_m:
            out.append(feature)
    return out


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


_DISPLAY_KEYS: frozenset[str] = frozenset({"display_label", "source_case"})


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
    for key in sorted(canonical):
        if key in _DISPLAY_KEYS:
            continue
        value = canonical[key]
        parts.append(f"{key}={value:g}" if isinstance(value, float) else f"{key}={value}")
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
