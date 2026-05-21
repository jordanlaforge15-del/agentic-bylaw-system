"""ABS-51: derived-attribute computation from BIM footprint + parcel.

The five derived attributes this module emits aren't in any IFC / RVT
property set; they fall out of a Shapely intersection between the
extracted building footprint and the parcel polygon (plus, for
front/rear classification, the nearby road centerlines).

Wired into the ABS-48 pipeline via the `derived_attribute_fn=` hook.
The pipeline supplies a SQLAlchemy session, the Submission row, and
the extractor's `SubmissionExtractionResult`; we return an
`ExtractedAttribute` list that the pipeline persists as
`submission_attribute` rows with `source=DERIVED`.

Two CRS transforms happen here:

* Building footprint: project-local → EPSG:2961 via ABS-52
  (`to_projected`). The IFC extractor (ABS-49) stashed the
  IfcGeometricRepresentationContext on `extraction.raw_metadata.extractor.geometric_context`
  which feeds straight into `project_location_from_ifc_context`.
* Parcel polygon: stored as 4326 GeoJSON in `Parcel.geometry_geojson`;
  reprojected to EPSG:2961 via pyproj directly. Both ends in the same
  projected CRS so Shapely distance / area / contains work as metres.

Source EPSG for the IFC has to be supplied externally because IFC
files rarely carry IfcMapConversion. We resolve it from
`parcel.jurisdiction` via a small map (HRM → 2961). Other
jurisdictions surface a warning and skip the geometric attrs; the
non-geometric ones (lot_coverage_percent, floor_area_ratio) still
compute from the area attributes alone.

Confidence policy mirrors ABS-49: 1.0 when every input validates, 0.4
when a sanity check trips (footprint outside parcel, missing road
data, no source EPSG mapping), and we skip the attribute entirely
when the input is straight-up absent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyproj import Transformer
from shapely.geometry import LineString, Polygon
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union
from sqlalchemy.orm import Session

from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
)
from layer2.compliance.db.models import (
    Submission,
    SubmissionAttributeSource,
)
from layer2.spatial.coord_transform import (
    CoordTransformError,
    DEFAULT_PROJECTED_EPSG,
    ProjectLocation,
    TransformResult,
    project_location_from_ifc_context,
    to_projected,
)


# Jurisdictions → source EPSG for project-local CRS. Halifax projects
# default to NAD83 / UTM 20N. Adding a city = adding a row.
DEFAULT_JURISDICTION_EPSG_MAP: dict[str, int] = {
    "HRM": 2961,
    "Halifax": 2961,
    "Halifax Regional Municipality": 2961,
}


@dataclass
class _ComputationState:
    """Mutable bag passed down the per-attribute helpers."""

    attributes: list[ExtractedAttribute] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def compute_derived_attributes(
    session: Session,
    submission: Submission,
    extraction: SubmissionExtractionResult,
    *,
    jurisdiction_epsg_map: dict[str, int] | None = None,
    nearby_centerlines: list[dict[str, Any]] | None = None,
) -> list[ExtractedAttribute]:
    """Compute the Phase-2 derived attributes for a submission.

    Returns a list of `ExtractedAttribute` rows the ABS-48 pipeline
    will tag with `source=DERIVED` and persist. The function never
    raises — every failure mode produces a warning + a missing or
    low-confidence attribute. The pipeline's `result.warnings` is
    augmented elsewhere; the warnings stashed here go into each
    attribute's `evidence` dict.

    `nearby_centerlines` is normally `None`, in which case the
    function queries `external_dataset_feature` for the road dataset
    near this parcel. Tests can pass the list directly to avoid DB
    fixturing.
    """
    state = _ComputationState()
    epsg_map = jurisdiction_epsg_map or DEFAULT_JURISDICTION_EPSG_MAP

    parcel = submission.parcel
    if parcel is None:
        state.warnings.append(
            "submission has no parcel_id — derived attributes skipped (need parcel "
            "polygon for setbacks and parcel.area_m2 for lot_coverage / FAR)."
        )
        return state.attributes

    extracted_by_key = {a.attribute_key: a for a in extraction.attributes}

    # ---- area-only attributes (lot_coverage_percent, floor_area_ratio) ----
    _compute_lot_coverage(parcel, extracted_by_key, extraction, state)
    _compute_floor_area_ratio(parcel, extracted_by_key, state)

    # ---- geometric attributes (setbacks + corner + arterial) ----
    parcel_polygon_4326 = _parcel_polygon(parcel, state)
    if parcel_polygon_4326 is None:
        # Parcel had no polygon; lot_coverage / FAR may still have landed
        # above from area-only inputs. Just stop here.
        return _stamp_warnings(state)

    source_epsg = _resolve_source_epsg(parcel.jurisdiction, epsg_map, state)
    footprint_in_target = _reproject_footprint_to_target(
        extraction, source_epsg, parcel, state
    )
    parcel_polygon_target = _reproject_parcel(parcel_polygon_4326, state)

    if footprint_in_target is not None and parcel_polygon_target is not None:
        _check_footprint_inside_parcel(
            footprint_in_target, parcel_polygon_target, state
        )

    if nearby_centerlines is None:
        nearby_centerlines = _query_nearby_centerlines(session, parcel)

    if (
        footprint_in_target is not None
        and parcel_polygon_target is not None
    ):
        _compute_setbacks(
            footprint_in_target,
            parcel_polygon_target,
            nearby_centerlines,
            state,
        )

    _compute_corner_lot(parcel_polygon_4326, nearby_centerlines, state)
    _compute_arterial_frontage(nearby_centerlines, state)

    return _stamp_warnings(state)


# ----------------------------------------------------------------------
# Area-only attributes (no geometry needed)
# ----------------------------------------------------------------------


def _compute_lot_coverage(
    parcel,
    extracted_by_key: dict[str, ExtractedAttribute],
    extraction: SubmissionExtractionResult,
    state: _ComputationState,
) -> None:
    """lot_coverage_percent = building_footprint_area_m2 / parcel.area_m2 * 100.

    Prefers `building_footprint_area_m2` from the extractor. Falls
    back to the footprint polygon's own area when the extractor
    didn't surface a Pset value (most IFC exports ship the slab
    geometry but not `Pset_BuildingCommon.BuildingFootprint`). The
    fallback runs at confidence 0.6 since the area comes from a
    derived measurement, not the property set.

    `parcel.area_m2` is denormalised on the parcel row and populated
    by the Phase-1 backfill from the parcel polygon — when it's null
    we skip rather than recompute (the value is the SoT).
    """
    if parcel.area_m2 is None or float(parcel.area_m2) <= 0:
        state.warnings.append(
            "parcel.area_m2 is null or non-positive — lot_coverage_percent skipped."
        )
        return

    value: float | None = None
    footprint_confidence: float = 1.0
    source_field: str = ""
    footprint_attr = extracted_by_key.get("building_footprint_area_m2")
    if footprint_attr is not None:
        value = _attr_numeric_value(footprint_attr)
        footprint_confidence = footprint_attr.confidence
        source_field = "extracted:building_footprint_area_m2"
    if (value is None or value <= 0) and extraction.footprint_geojson is not None:
        # Project-local coords are already in metres (ABS-49 applied
        # unit_scale before stashing the polygon), so polygon area in
        # m² is meaningful directly.
        derived_area = _polygon_area_m2(extraction.footprint_geojson)
        if derived_area > 0:
            value = derived_area
            footprint_confidence = 0.6
            source_field = "derived:polygon_area"
    if value is None or value <= 0:
        state.warnings.append(
            "no building_footprint_area_m2 and no footprint polygon — "
            "lot_coverage_percent skipped."
        )
        return

    parcel_area = float(parcel.area_m2)
    percent = (value / parcel_area) * 100.0
    confidence = footprint_confidence
    if percent > 100.0:
        state.warnings.append(
            f"building footprint ({value:.1f} m²) larger than parcel "
            f"({parcel_area:.1f} m²) — lot coverage {percent:.1f}% > 100%; "
            "footprint may be mis-georeferenced."
        )
        confidence = min(confidence, 0.4)
    state.attributes.append(
        ExtractedAttribute(
            attribute_key="lot_coverage_percent",
            value=round(percent, 2),
            unit="%",
            confidence=confidence,
            source=SubmissionAttributeSource.DERIVED,
            evidence={
                "formula": "building_footprint_area_m2 / parcel.area_m2 * 100",
                "footprint_area_m2": value,
                "parcel_area_m2": parcel_area,
                "footprint_source": source_field,
                "footprint_confidence": footprint_confidence,
            },
        )
    )


def _compute_floor_area_ratio(
    parcel,
    extracted_by_key: dict[str, ExtractedAttribute],
    state: _ComputationState,
) -> None:
    """floor_area_ratio = gross_floor_area_m2 / parcel.area_m2 (dimensionless)."""
    if parcel.area_m2 is None or float(parcel.area_m2) <= 0:
        state.warnings.append(
            "parcel.area_m2 is null or non-positive — floor_area_ratio skipped."
        )
        return
    gfa_attr = extracted_by_key.get("gross_floor_area_m2")
    if gfa_attr is None:
        state.warnings.append(
            "gross_floor_area_m2 not extracted — floor_area_ratio skipped."
        )
        return
    value = _attr_numeric_value(gfa_attr)
    if value is None or value <= 0:
        state.warnings.append(
            "gross_floor_area_m2 is null / non-positive — floor_area_ratio skipped."
        )
        return

    parcel_area = float(parcel.area_m2)
    far = value / parcel_area
    state.attributes.append(
        ExtractedAttribute(
            attribute_key="floor_area_ratio",
            value=round(far, 3),
            unit=None,
            confidence=gfa_attr.confidence,
            source=SubmissionAttributeSource.DERIVED,
            evidence={
                "formula": "gross_floor_area_m2 / parcel.area_m2",
                "gross_floor_area_m2": value,
                "parcel_area_m2": parcel_area,
                "gfa_confidence": gfa_attr.confidence,
            },
        )
    )


# ----------------------------------------------------------------------
# Footprint + parcel reprojection
# ----------------------------------------------------------------------


def _parcel_polygon(parcel, state: _ComputationState) -> Polygon | None:
    """Convert `parcel.geometry_geojson` to a Shapely Polygon in 4326."""
    if not parcel.geometry_geojson:
        state.warnings.append(
            "parcel.geometry_geojson is null — geometric derived attributes skipped."
        )
        return None
    try:
        geom = shapely_shape(parcel.geometry_geojson)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        state.warnings.append(f"parcel polygon not parseable: {exc}")
        return None
    if geom.geom_type == "MultiPolygon":
        # Per lot_metrics convention: take the largest piece.
        if not geom.geoms:
            state.warnings.append("parcel MultiPolygon is empty.")
            return None
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon":
        state.warnings.append(
            f"parcel geometry is {geom.geom_type}; expected Polygon — skipping geometric attrs."
        )
        return None
    if not geom.is_valid:
        state.warnings.append("parcel polygon is invalid (self-intersecting?).")
        return None
    return geom


def _resolve_source_epsg(
    jurisdiction: str | None,
    epsg_map: dict[str, int],
    state: _ComputationState,
) -> int | None:
    if not jurisdiction:
        state.warnings.append(
            "parcel.jurisdiction is empty — cannot resolve source EPSG; "
            "geometric derived attributes skipped."
        )
        return None
    epsg = epsg_map.get(jurisdiction)
    if epsg is None:
        state.warnings.append(
            f"no source EPSG mapping for jurisdiction {jurisdiction!r} — "
            "geometric derived attributes skipped. Add a mapping in "
            "DEFAULT_JURISDICTION_EPSG_MAP."
        )
    return epsg


def _reproject_footprint_to_target(
    extraction: SubmissionExtractionResult,
    source_epsg: int | None,
    parcel,
    state: _ComputationState,
) -> Polygon | None:
    """Reproject the IFC footprint into the target CRS (EPSG:2961 by default)."""
    if extraction.footprint_geojson is None:
        state.warnings.append(
            "extractor returned no footprint polygon — setbacks skipped."
        )
        return None
    if source_epsg is None:
        return None

    geom_context = (
        (extraction.raw_metadata or {}).get("extractor", {}).get("geometric_context")
    )
    unit_scale = (
        (extraction.raw_metadata or {}).get("extractor", {}).get(
            "unit_scale_to_metres", 1.0
        )
    )
    location = project_location_from_ifc_context(
        geom_context, source_epsg=source_epsg, unit_scale_to_metres=float(unit_scale)
    )
    parcel_centroid = _parcel_centroid_4326(parcel)
    try:
        result: TransformResult = to_projected(
            extraction.footprint_geojson,
            location,
            target_epsg=DEFAULT_PROJECTED_EPSG,
            parcel_centroid_4326=parcel_centroid,
        )
    except CoordTransformError as exc:
        state.warnings.append(
            f"footprint coord transform failed — setbacks skipped: {exc}"
        )
        return None

    for warning in result.warnings:
        state.warnings.append(f"footprint coord transform: {warning}")
    return _polygon_from_geojson(result.geometry)


def _reproject_parcel(
    parcel_polygon_4326: Polygon, state: _ComputationState
) -> Polygon | None:
    """Reproject a 4326 Shapely Polygon to EPSG:2961 via pyproj."""
    transformer = Transformer.from_crs(
        4326, DEFAULT_PROJECTED_EPSG, always_xy=True
    )
    try:
        projected = shapely_transform(
            lambda x, y, z=None: transformer.transform(x, y), parcel_polygon_4326
        )
    except Exception as exc:  # noqa: BLE001 — pyproj raises broadly
        state.warnings.append(f"parcel reprojection failed: {exc}")
        return None
    if projected.is_empty or not projected.is_valid:
        state.warnings.append("parcel polygon collapsed under reprojection.")
        return None
    return projected


def _parcel_centroid_4326(parcel) -> tuple[float, float] | None:
    """Pull a (lon, lat) centroid from a Parcel's stashed centroid_geojson or polygon."""
    centroid = parcel.centroid_geojson
    if isinstance(centroid, dict) and centroid.get("type") == "Point":
        coords = centroid.get("coordinates")
        if coords and len(coords) >= 2:
            return (float(coords[0]), float(coords[1]))
    if parcel.geometry_geojson:
        try:
            geom = shapely_shape(parcel.geometry_geojson)
            if geom.geom_type == "MultiPolygon":
                geom = max(geom.geoms, key=lambda g: g.area)
            c = geom.centroid
            return (float(c.x), float(c.y))
        except Exception:  # noqa: BLE001
            return None
    return None


# ----------------------------------------------------------------------
# Setbacks
# ----------------------------------------------------------------------


def _check_footprint_inside_parcel(
    footprint: Polygon, parcel: Polygon, state: _ComputationState
) -> None:
    """Surface a warning if the footprint isn't substantially inside the parcel."""
    if footprint.is_empty or parcel.is_empty:
        return
    inside = footprint.intersection(parcel)
    inside_area = float(inside.area) if not inside.is_empty else 0.0
    footprint_area = float(footprint.area)
    if footprint_area <= 0:
        return
    ratio = inside_area / footprint_area
    if ratio < 0.95:
        state.warnings.append(
            f"only {ratio:.1%} of the building footprint sits inside the parcel "
            "— setback values likely meaningless; check coord-transform metadata."
        )


def _compute_setbacks(
    footprint: Polygon,
    parcel: Polygon,
    centerlines: list[dict[str, Any]],
    state: _ComputationState,
) -> None:
    """Compute front / rear / side_left / side_right setbacks.

    Requires `centerlines` (in 4326) to classify the front segment.
    Without them we can compute "shortest distance to each boundary
    segment" but not which one is the front — the taxonomy keys
    require front/rear/side labels, so we skip + warn instead of
    emitting unlabelled distances.
    """
    if not centerlines:
        state.warnings.append(
            "no road centerlines available — setbacks skipped (cannot classify "
            "front / rear without knowing which boundary fronts the street). "
            "UI should prompt the architect to mark the front yard."
        )
        return

    parcel_segments = _polygon_edges(parcel)
    if len(parcel_segments) < 3:
        state.warnings.append(
            f"parcel has only {len(parcel_segments)} boundary segments — "
            "setbacks skipped (need at least 3-sided polygon)."
        )
        return

    # Centerlines are 4326; reproject to target CRS for distance ops.
    transformer = Transformer.from_crs(
        4326, DEFAULT_PROJECTED_EPSG, always_xy=True
    )
    centerline_geoms: list[LineString] = []
    for cl in centerlines:
        try:
            geom = shapely_shape(cl)
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if geom.is_empty or not geom.is_valid:
            continue
        if geom.geom_type == "LineString":
            centerline_geoms.append(
                shapely_transform(
                    lambda x, y, z=None: transformer.transform(x, y), geom
                )
            )
        elif geom.geom_type == "MultiLineString":
            for g in geom.geoms:
                centerline_geoms.append(
                    shapely_transform(
                        lambda x, y, z=None: transformer.transform(x, y), g
                    )
                )

    if not centerline_geoms:
        state.warnings.append(
            "centerline geometries were empty or invalid — setbacks skipped."
        )
        return

    centerline_union = unary_union(centerline_geoms)

    # Classify each parcel segment by distance to the centerline union.
    # The closest segment is the front. The most-parallel-and-distant
    # segment is the rear; the remaining two (or N-2) are sides.
    segment_distances = [
        (seg, seg.distance(centerline_union)) for seg in parcel_segments
    ]
    segment_distances.sort(key=lambda t: t[1])
    front_segment = segment_distances[0][0]

    # Rear: among the remaining segments, the one most parallel to
    # the front and furthest from it.
    rest = [seg for seg, _ in segment_distances[1:]]
    rear_segment = _pick_rear(front_segment, rest)
    sides = [s for s in rest if s is not rear_segment]
    left_segment, right_segment = _classify_sides(front_segment, sides)

    # For each labelled segment, distance from the building footprint
    # to the segment is the setback.
    out: list[tuple[str, LineString]] = [
        ("front_setback_m", front_segment),
        ("rear_setback_m", rear_segment) if rear_segment is not None else None,
    ]
    out = [item for item in out if item is not None]
    if left_segment is not None:
        out.append(("side_setback_left_m", left_segment))
    if right_segment is not None:
        out.append(("side_setback_right_m", right_segment))

    for key, segment in out:
        if segment is None:
            continue
        distance_m = float(footprint.distance(segment))
        state.attributes.append(
            ExtractedAttribute(
                attribute_key=key,
                value=round(distance_m, 2),
                unit="m",
                confidence=1.0,
                source=SubmissionAttributeSource.DERIVED,
                evidence={
                    "computation": "shapely_polygon_distance",
                    "segment_endpoints_target_crs": [
                        list(segment.coords[0]),
                        list(segment.coords[-1]),
                    ],
                },
            )
        )


def _polygon_edges(poly: Polygon) -> list[LineString]:
    coords = list(poly.exterior.coords)
    edges: list[LineString] = []
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        if a != b:
            edges.append(LineString([a, b]))
    return edges


def _pick_rear(front: LineString, rest: list[LineString]) -> LineString | None:
    """Pick the segment in `rest` most likely to be the rear yard edge.

    Heuristic: parallel-to-front + far-from-front. Score each candidate
    as `parallelism * distance` where parallelism = |cos(angle_diff)|.
    """
    if not rest:
        return None
    front_angle = _segment_angle(front)
    best, best_score = None, -1.0
    for seg in rest:
        seg_angle = _segment_angle(seg)
        # Angles compared mod π since a segment's "direction" is
        # orientation-insensitive for parallelism.
        delta = abs((seg_angle - front_angle) % 3.141592653589793)
        parallelism = abs(
            __import__("math").cos(
                min(delta, 3.141592653589793 - delta)
            )
        )
        # Reward parallel-and-far via product.
        score = parallelism * front.distance(seg)
        if score > best_score:
            best_score = score
            best = seg
    return best


def _classify_sides(
    front: LineString, sides: list[LineString]
) -> tuple[LineString | None, LineString | None]:
    """Pick left vs right side from the remaining segments.

    Convention: stand on the front edge facing the building. Your
    right hand points in the +front-direction; your left points in
    the -front-direction. So we project each side-segment's midpoint
    onto the front direction (dot product). Positive projection ⇒
    right; negative ⇒ left.

    Two side candidates is the typical rectangular-lot case (one each
    side). Irregular >4-sided lots may produce >2 sides; we pick the
    nearest candidate per bucket (the taxonomy only has left + right
    slots, no left_1 / left_2). Returns (left, right). Either is None
    when only one side candidate exists overall.
    """
    if not sides:
        return (None, None)
    if len(sides) == 1:
        return (sides[0], None)
    front_mid_x = (front.coords[0][0] + front.coords[-1][0]) / 2.0
    front_mid_y = (front.coords[0][1] + front.coords[-1][1]) / 2.0
    dx = front.coords[-1][0] - front.coords[0][0]
    dy = front.coords[-1][1] - front.coords[0][1]
    left_candidates: list[LineString] = []
    right_candidates: list[LineString] = []
    for seg in sides:
        mid_x = (seg.coords[0][0] + seg.coords[-1][0]) / 2.0
        mid_y = (seg.coords[0][1] + seg.coords[-1][1]) / 2.0
        rx = mid_x - front_mid_x
        ry = mid_y - front_mid_y
        # Dot product along front direction.
        projection = dx * rx + dy * ry
        if projection >= 0:
            right_candidates.append(seg)
        else:
            left_candidates.append(seg)
    left = min(left_candidates, key=lambda s: front.distance(s)) if left_candidates else None
    right = min(right_candidates, key=lambda s: front.distance(s)) if right_candidates else None
    return (left, right)


def _segment_angle(seg: LineString) -> float:
    import math

    (x1, y1), (x2, y2) = seg.coords[0], seg.coords[-1]
    return math.atan2(y2 - y1, x2 - x1)


# ----------------------------------------------------------------------
# Corner lot + arterial frontage
# ----------------------------------------------------------------------


def _compute_corner_lot(
    parcel_polygon_4326: Polygon,
    centerlines: list[dict[str, Any]],
    state: _ComputationState,
) -> None:
    """Delegate corner detection to layer2.spatial.lot_metrics.

    `compute_lot_metrics` already runs the centerline-buffer algorithm
    and reports `corner: bool`. No reason to reimplement it. When no
    centerlines are available the lot_metrics path reports corner=False
    — we attach confidence 0.4 + a warning so the UI knows it wasn't
    actually verified.
    """
    from layer2.spatial.lot_metrics import compute_lot_metrics

    parcel_geojson = parcel_polygon_4326.__geo_interface__
    metrics = compute_lot_metrics(
        parcel_geojson, centerline_geojsons=centerlines or []
    )
    if metrics.corner is None:
        state.warnings.append(
            "corner_lot_boolean not computed by lot_metrics — skipped."
        )
        return
    confidence = 1.0
    evidence: dict[str, Any] = {
        "method": metrics.method,
        "lot_metrics_status": metrics.status,
        "frontage_m": metrics.frontage_m,
    }
    if not centerlines:
        confidence = 0.4
        evidence["reason_for_low_confidence"] = (
            "no road centerlines near parcel — corner heuristic returned False "
            "by default rather than from actual classification."
        )
    state.attributes.append(
        ExtractedAttribute(
            attribute_key="corner_lot_boolean",
            value=bool(metrics.corner),
            unit=None,
            confidence=confidence,
            source=SubmissionAttributeSource.DERIVED,
            evidence=evidence,
        )
    )


def _compute_arterial_frontage(
    centerlines: list[dict[str, Any]], state: _ComputationState
) -> None:
    """Check whether any nearby centerline carries an `arterial` tag.

    The road dataset's per-feature `attributes_json` may carry road
    classification (HRM publishes one, but the local backfill doesn't
    always populate it). We look for any of a few keys with values
    suggesting arterial. When the dataset has no classification info
    at all we skip + warn rather than guess.
    """
    if not centerlines:
        state.warnings.append(
            "no road centerlines near parcel — arterial_frontage_boolean skipped."
        )
        return

    any_classification_present = False
    has_arterial = False
    for cl in centerlines:
        # `cl` here is the geometry dict — but `_query_nearby_centerlines`
        # returns geometry dicts only. We'd need a different fetcher to
        # grab classification. Stub honestly: only compute when caller
        # threaded classification through.
        # The geometry dict may carry "properties" if the caller built
        # a GeoJSON Feature; check for arterial signals there.
        props = (cl.get("properties") or {}) if isinstance(cl, dict) else {}
        for key in ("road_class", "functional_class", "classification", "class"):
            val = props.get(key)
            if val is None:
                continue
            any_classification_present = True
            if isinstance(val, str) and "arterial" in val.lower():
                has_arterial = True
    if not any_classification_present:
        state.warnings.append(
            "road dataset features lack classification metadata — "
            "arterial_frontage_boolean skipped."
        )
        return

    state.attributes.append(
        ExtractedAttribute(
            attribute_key="arterial_frontage_boolean",
            value=bool(has_arterial),
            unit=None,
            confidence=1.0,
            source=SubmissionAttributeSource.DERIVED,
            evidence={
                "method": "centerline_properties_keyword",
                "matched_keys": ["road_class", "functional_class", "classification", "class"],
            },
        )
    )


# ----------------------------------------------------------------------
# DB helpers
# ----------------------------------------------------------------------


def _query_nearby_centerlines(session: Session, parcel) -> list[dict[str, Any]]:
    """Pull centerline GeoJSON geometries near this parcel.

    Mirrors the existing `layer2.spatial.extractor._find_nearby_centerlines`
    pattern but works from a `Parcel` row (rather than the ExternalDataset-
    Feature variant) so the submission pipeline doesn't need to round-trip
    through the parcel-feature lookup. Returns empty list when no road
    dataset is ingested — caller's per-attribute handler decides what to
    do with that.
    """
    from sqlalchemy import select
    from layer1.db.base import ExternalDataset, ExternalDatasetFeature

    # Find the road centerlines dataset id by role tag.
    rows = session.execute(
        select(ExternalDataset.id, ExternalDataset.metadata_json)
    ).all()
    centerlines_dataset_id: int | None = None
    for row in rows:
        if (row.metadata_json or {}).get("role") == "road_centerlines":
            centerlines_dataset_id = int(row.id)
            break
    if centerlines_dataset_id is None:
        return []

    # Parcel bbox in 4326. Pad slightly so we catch centerlines that
    # the parcel doesn't strictly enclose.
    if not parcel.geometry_geojson:
        return []
    try:
        parcel_geom = shapely_shape(parcel.geometry_geojson)
    except (TypeError, ValueError, KeyError, AttributeError):
        return []
    minx, miny, maxx, maxy = parcel_geom.bounds
    pad = 0.001  # ~110m at HRM latitudes; same convention as extractor.py

    features = (
        session.execute(
            select(ExternalDatasetFeature).where(
                ExternalDatasetFeature.external_dataset_id == centerlines_dataset_id
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for feature in features:
        bbox = feature.geometry_bbox_json or {}
        if (
            bbox.get("minx", float("-inf")) > maxx + pad
            or bbox.get("maxx", float("inf")) < minx - pad
            or bbox.get("miny", float("-inf")) > maxy + pad
            or bbox.get("maxy", float("inf")) < miny - pad
        ):
            continue
        if feature.geometry_geojson:
            # Surface attributes alongside geometry so
            # _compute_arterial_frontage can read classification tags.
            decorated = dict(feature.geometry_geojson)
            if feature.canonical_attributes_json or feature.attributes_json:
                decorated["properties"] = {
                    **(feature.attributes_json or {}),
                    **(feature.canonical_attributes_json or {}),
                }
            out.append(decorated)
    return out


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def _attr_numeric_value(attr: ExtractedAttribute) -> float | None:
    """Pull a numeric value from an ExtractedAttribute, handling dict-wrapped values."""
    value = attr.value
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _polygon_area_m2(geometry_geojson: dict[str, Any]) -> float:
    """Shoelace area of a GeoJSON Polygon outer ring (assumes metric coords)."""
    poly = _polygon_from_geojson(geometry_geojson)
    if poly is None or poly.is_empty:
        return 0.0
    return float(poly.area)


def _polygon_from_geojson(geometry: dict[str, Any]) -> Polygon | None:
    try:
        geom = shapely_shape(geometry)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    if geom.geom_type != "Polygon":
        return None
    return geom


def _stamp_warnings(state: _ComputationState) -> list[ExtractedAttribute]:
    """Attach the run's overall warnings to each attribute's evidence."""
    if not state.warnings:
        return state.attributes
    for attr in state.attributes:
        # Don't clobber attribute-specific evidence; append a shared
        # warnings list under a stable key.
        attr.evidence.setdefault("derived_run_warnings", list(state.warnings))
    return state.attributes


__all__ = [
    "DEFAULT_JURISDICTION_EPSG_MAP",
    "compute_derived_attributes",
]
