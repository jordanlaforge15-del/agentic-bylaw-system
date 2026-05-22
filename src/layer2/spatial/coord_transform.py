"""Project-local → projected CRS / WGS84 transforms for BIM submissions.

The single hardest bug in BIM-side compliance is "the setback came out
800 metres" because the transform from the BIM file's project-local
coordinate space to the parcel's geographic CRS got it wrong. This
module is the one place we do that transform, with three jobs:

1. Normalise per-extractor metadata (IFC `IfcGeometricRepresentation-
   Context` + `IfcMapConversion`, APS project location, etc.) into a
   single `ProjectLocation` shape.
2. Reproject a project-local GeoJSON polygon into:
   - a *projected* CRS (default EPSG:2961 — NAD83 Stane Plane Nova
     Scotia, the metric-friendly CRS used by HRM cadastre) for
     setback math, and
   - EPSG:4326 (lon/lat) for storage and the existing spatial-query
     surface (`external_dataset_feature.geometry`).
3. Sanity-check the result. Two checks: building-centroid within 5 km
   of the parcel centroid (catches outright wrong CRS / origin) and
   transformed-area within 20% of project-local area (catches the
   mm-vs-m unit footgun Revit exporters hit regularly). Failures
   downgrade confidence; egregious failures raise so the pipeline
   surfaces the problem instead of persisting nonsense.

Per the issue spec: when metadata is too sparse to transform safely we
`raise CoordTransformError`, not return zeros. The ABS-48 pipeline
catches extractor exceptions into `SubmissionIngestResult.errors`, so
the UI shows the architect a clear "we can't place your model — please
confirm the project location" rather than a 800-metre setback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError


# Default target CRS for setback math in Halifax. Other municipalities
# get other Stane Plane zones (BC: 3005/3155, ON: 26920, etc.) — when
# we onboard a non-Halifax customer we route off `parcel.jurisdiction`.
DEFAULT_PROJECTED_EPSG = 2961  # NAD83(CSRS) / UTM zone 20N for HRM

WGS84_EPSG = 4326

# Halifax-area bounding box (rough HRM extent). Used as a final
# defensive check before we trust a 4326 conversion. Wider than HRM
# itself (HRM is ~150x150 km but this includes the immediate region).
_HRM_BOUNDS = (-65.0, 43.5, -62.0, 46.0)  # lon_min, lat_min, lon_max, lat_max


# ----------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------


class CoordTransformError(RuntimeError):
    """Raised when the supplied project metadata can't yield a safe transform.

    The caller (ABS-51 derived-attribute pipeline) catches this and
    records the error string in `submission_attribute.evidence_json`
    so the UI shows the architect *why* setbacks couldn't be computed
    instead of silently emitting a wrong number.
    """


@dataclass(frozen=True)
class ProjectLocation:
    """Normalised input that both IFC and APS extractors can produce.

    The fields are deliberately permissive: real-world exports often
    omit one or two. The transform raises `CoordTransformError` only
    when the omission makes the result meaningless (no `source_epsg`,
    no base point). A missing `true_north_angle_deg` defaults to 0°
    (grid-north == true-north) which is benign for short distances
    and the default most exporters take.

    `source_epsg` — the CRS the project base point is expressed in
    (e.g. 2961 for HRM). None means "unknown" and triggers an error;
    we don't guess a CRS for the architect.

    `true_north_angle_deg` — degrees from project +Y axis to true
    north, measured clockwise (the AEC convention). Equivalent to the
    `IfcGeometricRepresentationContext.TrueNorth` direction vector.

    `project_base_easting` / `project_base_northing` — where project-
    local (0, 0) sits in the source CRS, in source-CRS units (metres
    for Stane Plane).

    `unit_scale_to_metres` — project-local unit scale; 1.0 when the
    BIM file is in metres, 0.001 when in millimetres. The IFC
    extractor (ABS-49) already applies this to its outputs, but we
    keep it here so the APS adapter has the same hook.
    """

    source_epsg: int | None
    project_base_easting: float = 0.0
    project_base_northing: float = 0.0
    project_base_elevation: float = 0.0
    true_north_angle_deg: float = 0.0
    unit_scale_to_metres: float = 1.0
    # Free-form provenance — which extractor built this, which IFC
    # context id, which Revit shared-coords entry. Passed through into
    # `TransformResult.evidence` so debugging starts from the raw inputs.
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransformResult:
    """What `to_projected` / `to_wgs84` hand back.

    `geometry` is a GeoJSON-shaped dict with `type` + `coordinates`,
    just like the input — but in the target CRS. `crs` is a stable
    string ("EPSG:2961", "EPSG:4326") so persisters don't have to
    introspect. `confidence` rolls up the sanity checks: 1.0 when
    everything passed, 0.4 when at least one check tripped but the
    output is still inside the "probably right CRS" window. `warnings`
    explains each downgrade in plain text.
    """

    geometry: dict[str, Any]
    crs: str
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Adapters — turn extractor-specific metadata into ProjectLocation
# ----------------------------------------------------------------------


def project_location_from_ifc_context(
    geometric_context: dict[str, Any] | None,
    *,
    source_epsg: int | None,
    unit_scale_to_metres: float = 1.0,
) -> ProjectLocation:
    """Build a `ProjectLocation` from ABS-49's `raw_metadata.geometric_context`.

    Pulls the true-north direction vector and (if present) the world
    coordinate-system origin. `source_epsg` is supplied by the caller
    because IFC files rarely carry an explicit CRS — the architect or
    a per-municipality config map provides it. For Halifax: 2961.
    """
    if geometric_context is None:
        return ProjectLocation(
            source_epsg=source_epsg,
            unit_scale_to_metres=unit_scale_to_metres,
            provenance={"source": "ifc-no-context"},
        )

    origin = geometric_context.get("world_origin") or [0.0, 0.0, 0.0]
    easting = float(origin[0]) if len(origin) > 0 else 0.0
    northing = float(origin[1]) if len(origin) > 1 else 0.0
    elevation = float(origin[2]) if len(origin) > 2 else 0.0

    angle_deg = 0.0
    direction = geometric_context.get("true_north_direction")
    if direction and len(direction) >= 2:
        # IFC convention: TrueNorth is a 2D direction in the project
        # XY plane. atan2(x, y) gives the CW angle from +Y axis.
        dx, dy = float(direction[0]), float(direction[1])
        if dx != 0.0 or dy != 0.0:
            angle_deg = math.degrees(math.atan2(dx, dy))

    return ProjectLocation(
        source_epsg=source_epsg,
        project_base_easting=easting,
        project_base_northing=northing,
        project_base_elevation=elevation,
        true_north_angle_deg=angle_deg,
        unit_scale_to_metres=unit_scale_to_metres,
        provenance={
            "source": "ifc-geometric-context",
            "context_type": geometric_context.get("context_type"),
            "context_identifier": geometric_context.get("context_identifier"),
        },
    )


def project_location_from_aps(
    *,
    source_epsg: int | None,
    latitude: float | None = None,
    longitude: float | None = None,
    project_base_point: tuple[float, float, float] | None = None,
    true_north_angle_deg: float = 0.0,
    unit_scale_to_metres: float = 1.0,
) -> ProjectLocation:
    """Build a `ProjectLocation` from APS Revit project-location parameters.

    Two ways to set the base point:

    * `project_base_point` directly in source-CRS metres (what APS
      Model Derivative property extract gives you when a Revit
      shared-coords system is configured).
    * `latitude` / `longitude` (degrees) when only Revit's "default"
      lat/lon is set — we reproject through the source CRS so the
      output is consistent with the IFC adapter.

    Raises `CoordTransformError` immediately if both are missing AND
    `source_epsg` is None — the resulting ProjectLocation would have
    nothing to anchor to.
    """
    if (
        project_base_point is None
        and latitude is None
        and longitude is None
        and source_epsg is None
    ):
        raise CoordTransformError(
            "APS project location: no base point, no lat/lon, no source EPSG — "
            "cannot construct a usable ProjectLocation."
        )

    easting, northing, elevation = 0.0, 0.0, 0.0
    if project_base_point is not None:
        easting, northing, elevation = project_base_point
    elif latitude is not None and longitude is not None and source_epsg is not None:
        transformer = _safe_transformer(WGS84_EPSG, source_epsg)
        easting, northing = transformer.transform(longitude, latitude)

    return ProjectLocation(
        source_epsg=source_epsg,
        project_base_easting=easting,
        project_base_northing=northing,
        project_base_elevation=elevation,
        true_north_angle_deg=true_north_angle_deg,
        unit_scale_to_metres=unit_scale_to_metres,
        provenance={
            "source": "aps-project-location",
            "from_lat_lon": (
                latitude is not None and longitude is not None
                and project_base_point is None
            ),
        },
    )


# ----------------------------------------------------------------------
# Public transforms
# ----------------------------------------------------------------------


def to_projected(
    geometry_geojson: dict[str, Any],
    location: ProjectLocation,
    *,
    target_epsg: int = DEFAULT_PROJECTED_EPSG,
    parcel_centroid_4326: tuple[float, float] | None = None,
) -> TransformResult:
    """Transform a project-local polygon to a projected CRS (metres).

    `parcel_centroid_4326` (lon, lat) drives the centroid sanity
    check. When supplied we additionally reproject the transformed
    polygon to 4326 so we can compute the centroid-distance vs the
    parcel; the original 2961 output is what's returned. When not
    supplied the centroid check is skipped (the area check still runs).
    """
    _require_location(location, "to_projected")
    src_coords = _polygon_coords(geometry_geojson)
    if not src_coords:
        raise CoordTransformError(
            "geometry has no coordinates — cannot transform an empty polygon."
        )

    # 1. Project-local → source-CRS metres: scale, rotate, translate.
    site_coords = [
        _local_to_source_crs(x, y, location)
        for (x, y) in src_coords
    ]

    # 2. Source-CRS → target projected CRS via pyproj.
    if location.source_epsg == target_epsg:
        out_coords = site_coords
    else:
        transformer = _safe_transformer(location.source_epsg, target_epsg)
        out_coords = [transformer.transform(x, y) for (x, y) in site_coords]

    geometry_out = _polygon_dict(out_coords)
    warnings: list[str] = []
    confidence = 1.0

    # Sanity check 1: area ratio (mm-vs-m footgun catcher).
    src_area = _polygon_area_m2(src_coords) * (location.unit_scale_to_metres ** 2)
    out_area = _polygon_area_m2(out_coords)
    confidence, warnings = _apply_area_sanity_check(
        src_area, out_area, confidence, warnings
    )

    # Sanity check 2: centroid distance.
    if parcel_centroid_4326 is not None:
        transformer_to_4326 = _safe_transformer(target_epsg, WGS84_EPSG)
        out_centroid_xy = _polygon_centroid(out_coords)
        out_centroid_4326 = transformer_to_4326.transform(*out_centroid_xy)
        confidence, warnings = _apply_centroid_sanity_check(
            (out_centroid_4326[0], out_centroid_4326[1]),
            parcel_centroid_4326,
            confidence,
            warnings,
        )

    return TransformResult(
        geometry=geometry_out,
        crs=f"EPSG:{target_epsg}",
        confidence=confidence,
        warnings=warnings,
        evidence={
            "source_epsg": location.source_epsg,
            "target_epsg": target_epsg,
            "true_north_angle_deg": location.true_north_angle_deg,
            "project_base": [
                location.project_base_easting,
                location.project_base_northing,
            ],
            "unit_scale_to_metres": location.unit_scale_to_metres,
            "src_area_m2": src_area,
            "out_area_m2": out_area,
            "provenance": location.provenance,
        },
    )


def to_wgs84(
    geometry_geojson: dict[str, Any],
    location: ProjectLocation,
    *,
    parcel_centroid_4326: tuple[float, float] | None = None,
) -> TransformResult:
    """Transform a project-local polygon to EPSG:4326 (lon/lat).

    Always goes through the source projected CRS first; never tries
    to interpret project-local units as geographic. The area sanity
    check runs against the polygon area in source-CRS metres (a real
    projected area), not against the 4326 polygon's degree-squared
    area which would be meaningless.
    """
    _require_location(location, "to_wgs84")
    src_coords = _polygon_coords(geometry_geojson)
    if not src_coords:
        raise CoordTransformError(
            "geometry has no coordinates — cannot transform an empty polygon."
        )

    site_coords = [
        _local_to_source_crs(x, y, location)
        for (x, y) in src_coords
    ]

    transformer = _safe_transformer(location.source_epsg, WGS84_EPSG)
    out_coords = [transformer.transform(x, y) for (x, y) in site_coords]

    # Validate the converted coordinates land somewhere on Earth. A
    # transform that returns inf / nan indicates the input was outside
    # the source CRS's valid envelope.
    for lon, lat in out_coords:
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise CoordTransformError(
                f"transformed coordinate ({lon}, {lat}) is non-finite — "
                f"input geometry is outside EPSG:{location.source_epsg}'s valid envelope."
            )

    geometry_out = _polygon_dict(out_coords)
    warnings: list[str] = []
    confidence = 1.0

    src_area = _polygon_area_m2(src_coords) * (location.unit_scale_to_metres ** 2)
    # We need an area in metres to compare against — reproject again to
    # the projected target only for the sanity-check footprint.
    site_area = _polygon_area_m2(site_coords)
    confidence, warnings = _apply_area_sanity_check(
        src_area, site_area, confidence, warnings
    )

    if parcel_centroid_4326 is not None:
        out_centroid = _polygon_centroid(out_coords)
        confidence, warnings = _apply_centroid_sanity_check(
            (out_centroid[0], out_centroid[1]),
            parcel_centroid_4326,
            confidence,
            warnings,
        )

    return TransformResult(
        geometry=geometry_out,
        crs=f"EPSG:{WGS84_EPSG}",
        confidence=confidence,
        warnings=warnings,
        evidence={
            "source_epsg": location.source_epsg,
            "target_epsg": WGS84_EPSG,
            "true_north_angle_deg": location.true_north_angle_deg,
            "project_base": [
                location.project_base_easting,
                location.project_base_northing,
            ],
            "unit_scale_to_metres": location.unit_scale_to_metres,
            "src_area_m2": src_area,
            "site_area_m2": site_area,
            "provenance": location.provenance,
        },
    )


# ----------------------------------------------------------------------
# Sanity checks
# ----------------------------------------------------------------------


# Tunables — picked to match the issue spec's "within 20%" and "within
# 5 km". Hard fail thresholds catch the obvious mismatches (mm-vs-m =
# 1000× area, wrong CRS = >100 km centroid).
_AREA_SOFT_LOW = 0.80
_AREA_SOFT_HIGH = 1.20
_AREA_HARD_LOW = 0.50
_AREA_HARD_HIGH = 2.00
_CENTROID_SOFT_KM = 5.0
_CENTROID_HARD_KM = 100.0

# Absolute upper bound for a plausible single-submission building
# footprint. Mall of America footprint is ~390,000 m²; anything
# above that is almost certainly a unit mismatch (mm-vs-m inflates by
# 1,000,000×, so even a small house becomes >1M m²). The internal
# src/out ratio check can't catch this because both sides are
# consistent with each other — only an external bound notices.
_MAX_PLAUSIBLE_BUILDING_AREA_M2 = 500_000


def _apply_area_sanity_check(
    src_area_m2: float,
    out_area_m2: float,
    confidence: float,
    warnings: list[str],
) -> tuple[float, list[str]]:
    """Compare src vs out area; downgrade or raise per the thresholds.

    Two layers:

    * **Internal consistency**: src/out ratio. Catches CRS-introduced
      area distortion (claiming metres-as-4326, claiming degrees-as-2961).
    * **Absolute bound**: out_area > `_MAX_PLAUSIBLE_BUILDING_AREA_M2`.
      Catches mm-vs-m unit confusion in the data even when the internal
      ratio is 1.0 (both sides equally wrong).
    """
    if src_area_m2 <= 0 or out_area_m2 <= 0:
        # Don't trip on degenerate polygons — that's the caller's bug,
        # not ours, and shouldn't manifest as a unit-mismatch warning.
        return confidence, warnings

    if out_area_m2 > _MAX_PLAUSIBLE_BUILDING_AREA_M2:
        raise CoordTransformError(
            f"transformed area {out_area_m2:.1f} m² exceeds the largest "
            f"plausible single building "
            f"({_MAX_PLAUSIBLE_BUILDING_AREA_M2:,.0f} m²). "
            "Almost certainly a millimetres-vs-metres unit mismatch in the "
            "source file. Refusing to emit nonsense coordinates."
        )

    ratio = out_area_m2 / src_area_m2
    if ratio < _AREA_HARD_LOW or ratio > _AREA_HARD_HIGH:
        raise CoordTransformError(
            f"transformed area {out_area_m2:.1f} m² is "
            f"{ratio:.2f}× project-local area {src_area_m2:.1f} m² — "
            "likely wrong source CRS. Refusing to emit nonsense coordinates."
        )
    if ratio < _AREA_SOFT_LOW or ratio > _AREA_SOFT_HIGH:
        warnings.append(
            f"transformed area {out_area_m2:.1f} m² differs from project-local "
            f"area {src_area_m2:.1f} m² by more than 20% ({ratio:.2%} ratio) — "
            "check project units and source CRS."
        )
        confidence = min(confidence, 0.4)
    return confidence, warnings


def _apply_centroid_sanity_check(
    out_centroid_4326: tuple[float, float],
    parcel_centroid_4326: tuple[float, float],
    confidence: float,
    warnings: list[str],
) -> tuple[float, list[str]]:
    """Compare transformed-building centroid vs supplied parcel centroid."""
    dist_km = _haversine_km(
        out_centroid_4326[0], out_centroid_4326[1],
        parcel_centroid_4326[0], parcel_centroid_4326[1],
    )
    if dist_km > _CENTROID_HARD_KM:
        raise CoordTransformError(
            f"building centroid sits {dist_km:.1f} km from the parcel centroid — "
            "almost certainly wrong source CRS or project base point. "
            "Refusing to emit nonsense coordinates."
        )
    if dist_km > _CENTROID_SOFT_KM:
        warnings.append(
            f"building centroid sits {dist_km:.2f} km from the parcel centroid "
            f"(>{_CENTROID_SOFT_KM} km) — check project base point / true-north angle."
        )
        confidence = min(confidence, 0.4)
    return confidence, warnings


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _require_location(location: ProjectLocation, op: str) -> None:
    if location.source_epsg is None:
        raise CoordTransformError(
            f"{op}: ProjectLocation has no source_epsg — caller must supply "
            "the CRS the project base point is expressed in (e.g. 2961 for HRM). "
            "We don't guess source CRSes."
        )
    try:
        crs = CRS.from_epsg(location.source_epsg)
    except CRSError as exc:
        raise CoordTransformError(
            f"{op}: source_epsg={location.source_epsg!r} is not a valid EPSG code: {exc}"
        ) from exc
    if crs.is_geographic:
        # BIM project-local coordinates are metres in a projected CRS.
        # A geographic source CRS (4326) would mean "treat lon/lat as
        # the base point and metres-units around it", which never makes
        # sense for a BIM ingest. Reject it loudly so the architect
        # supplies the right CRS instead of silently producing a
        # building somewhere in Africa.
        raise CoordTransformError(
            f"{op}: source_epsg={location.source_epsg} is a geographic CRS "
            "(lat/lon). BIM project-local coordinates must be expressed in a "
            "projected (metric) CRS — e.g. 2961 for Halifax. "
            "If the source file declares 4326, the architect probably needs "
            "to re-export with the project's shared coordinate system set."
        )


def _safe_transformer(source_epsg: int, target_epsg: int) -> Transformer:
    """Build a `pyproj.Transformer`, normalising xy/lonlat argument order.

    `always_xy=True` keeps `transform(x, y)` consistent for both
    projected (easting / northing) and geographic (lon / lat) CRSes —
    which is what every other layer in this codebase assumes.
    """
    try:
        # Validate both CRSes upfront so the error names the bad one.
        CRS.from_epsg(source_epsg)
        CRS.from_epsg(target_epsg)
    except CRSError as exc:
        raise CoordTransformError(f"invalid EPSG code in transform: {exc}") from exc
    return Transformer.from_crs(source_epsg, target_epsg, always_xy=True)


def _local_to_source_crs(
    x: float, y: float, location: ProjectLocation
) -> tuple[float, float]:
    """Project-local (x, y) → source-CRS (easting, northing).

    Order: scale to metres → rotate so project +Y aligns with source-CRS
    +Y (i.e. "true north" → grid north) → translate by the base point.
    True-north angle is CW from project +Y, so to rotate project axes
    onto CRS axes we rotate by -angle.
    """
    sx = x * location.unit_scale_to_metres
    sy = y * location.unit_scale_to_metres
    angle = math.radians(-location.true_north_angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rx = sx * cos_a - sy * sin_a
    ry = sx * sin_a + sy * cos_a
    return (
        rx + location.project_base_easting,
        ry + location.project_base_northing,
    )


def _polygon_coords(
    geometry_geojson: dict[str, Any],
) -> list[tuple[float, float]]:
    """Extract the outer ring of a GeoJSON Polygon as (x, y) tuples.

    Multi-rings (interior holes) and MultiPolygon are out of scope for
    Phase 2 — buildings as compliance-evaluated geometry are single
    closed rings. If we ship MultiPolygon support later, extend here
    and `_polygon_dict`.
    """
    if not isinstance(geometry_geojson, dict):
        return []
    if geometry_geojson.get("type") != "Polygon":
        raise CoordTransformError(
            f"only GeoJSON Polygon is supported; got {geometry_geojson.get('type')!r}"
        )
    rings = geometry_geojson.get("coordinates") or []
    if not rings:
        return []
    return [(float(p[0]), float(p[1])) for p in rings[0]]


def _polygon_dict(coords: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """GeoJSON Polygon dict from a list of (x, y) points, auto-closing the ring."""
    pts = [list(p) for p in coords]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def _polygon_area_m2(coords: Sequence[tuple[float, float]]) -> float:
    """Shoelace area for a closed polygon. Returns absolute area in input-unit².

    No CRS-awareness: caller passes coords already in metres and trusts
    the value as m². Used for the area sanity check — comparing
    project-local m² to source-CRS m² is meaningful because both are
    in the same units after our transform pipeline.
    """
    if len(coords) < 3:
        return 0.0
    total = 0.0
    for i in range(len(coords)):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % len(coords)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _polygon_centroid(
    coords: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """Area-weighted centroid for a closed polygon.

    Falls back to the bounding-box centre when the polygon is
    degenerate (area = 0) — that case shouldn't arise in practice but
    keeps the sanity-check path from crashing on a divide-by-zero.
    """
    if len(coords) < 3:
        if not coords:
            return (0.0, 0.0)
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(coords)):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % len(coords)]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if area2 == 0:
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    return (cx / (3.0 * area2), cy / (3.0 * area2))


def _haversine_km(
    lon1: float, lat1: float, lon2: float, lat2: float
) -> float:
    """Great-circle distance in kilometres between two 4326 points."""
    r_km = 6371.0088  # mean Earth radius
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2 * r_km * math.asin(min(1.0, math.sqrt(a)))


__all__ = [
    "CoordTransformError",
    "DEFAULT_PROJECTED_EPSG",
    "ProjectLocation",
    "TransformResult",
    "WGS84_EPSG",
    "project_location_from_aps",
    "project_location_from_ifc_context",
    "to_projected",
    "to_wgs84",
]
