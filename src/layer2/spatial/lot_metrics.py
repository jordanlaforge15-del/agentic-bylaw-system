"""Pure-geometry lot characteristics from a parcel polygon.

Given a parcel polygon and the set of road centerlines near it (all in
EPSG:4326), compute area, road frontage, depth, and corner-lot status.

Method
------
Frontage uses the *centerline-buffer* heuristic. HRM's parcel layer
tessellates edge-to-edge to road centerlines (no right-of-way polygon
between adjacent parcels and the street), which means the parcel's
road-facing edges sit right on the centerline. We:

1. Project both the parcel and the candidate centerlines to local
   metres via an equirectangular projection centred on the parcel.
2. Union the centerlines and buffer the union by ``buffer_m``.
3. Intersect the parcel's exterior boundary with the buffer — the
   length of that intersection is the road frontage.

This replaces an earlier shared-edge heuristic that classified each
boundary segment by whether it was ε-close to a neighbour parcel; that
approach collapsed to 0 m on tessellated urban parcels because every
front edge of a residential lot is also ε-close to the parcel directly
across the street.

Corner detection inspects the bearings of the frontage-intersection
segments: a mid-block lot's frontage runs along one bearing, while a
corner lot's frontage wraps around the parcel's road-facing corner and
spans two perpendicular bearings.

Projection
----------
pyproj is not a dependency, so we project to local-tangent-plane
metres via an equirectangular projection centred on the parcel
centroid. For a single parcel (a few hundred to a few thousand m²)
the error is well under 0.1% — fine for the precision a homeowner-
facing answer needs.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

logger = logging.getLogger(__name__)


# Half-width of the centerline buffer in metres. Halifax road allowances
# are typically 12–30 m wide, and HRM's parcels tessellate edge-to-edge
# to the centerline — so the parcel's front edge sits right on the
# centerline. An 8 m buffer comfortably catches a front edge that's
# exactly on the centerline while staying narrow enough to avoid pulling
# in non-frontage edges of nearby parcels.
DEFAULT_BUFFER_M: float = 8.0


# Threshold below which we mark the result ``uncertain`` rather than
# ``ok``. The geometry confidence (computed in compute_lot_metrics) is
# 1.0 for a clean parcel polygon and drops when shapely had to repair
# the input. Frontage / buffer-tuning risk is layered on by the
# extractor, which knows about the perimeter ratio.
CONFIDENCE_OK_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class LotMetrics:
    """Computed spatial characteristics of a lot.

    ``status`` is ``"ok"`` when the computation succeeded and the
    confidence is at or above the threshold; ``"uncertain"`` when the
    geometry was usable but the result is shaky (e.g. shapely had to
    repair the polygon); ``"unresolved"`` when the geometry was
    unusable. ``reason`` is populated on ``unresolved``.

    All linear measurements are metres; ``area_m2`` is square metres.
    ``corner`` is True when the lot's road-facing boundary spans two
    or more distinct bearings (i.e. fronts more than one street).
    ``multi_unit`` is left as None by this module; the extractor sets
    it after a civic-address dataset lookup.
    """

    area_m2: float | None
    frontage_m: float | None
    depth_m: float | None
    perimeter_m: float | None
    corner: bool | None
    multi_unit: bool | None
    method: str
    confidence: float
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the metrics as a JSON-friendly dict for persistence."""
        d: dict[str, Any] = {
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "status": self.status,
        }
        if self.area_m2 is not None:
            d["area_m2"] = round(self.area_m2, 1)
        if self.frontage_m is not None:
            d["frontage_m"] = round(self.frontage_m, 2)
        if self.depth_m is not None:
            d["depth_m"] = round(self.depth_m, 2)
        if self.perimeter_m is not None:
            d["perimeter_m"] = round(self.perimeter_m, 2)
        if self.corner is not None:
            d["corner"] = self.corner
        if self.multi_unit is not None:
            d["multi_unit"] = self.multi_unit
        if self.reason:
            d["reason"] = self.reason
        return d


def compute_lot_metrics(
    parcel_geojson: dict[str, Any],
    centerline_geojsons: list[dict[str, Any]] | None = None,
    *,
    buffer_m: float = DEFAULT_BUFFER_M,
) -> LotMetrics:
    """Compute lot metrics from a parcel polygon and nearby centerlines.

    Inputs are GeoJSON geometry dicts in EPSG:4326. ``centerline_geojsons``
    may be empty (no centerline data, or no nearby segments) — in that
    case frontage, depth, and corner are reported as 0 / None / False and
    the caller can adjust confidence based on coverage.

    Never raises for bad input — returns ``LotMetrics`` with
    ``status="unresolved"`` and ``reason`` set so the caller can persist
    an explicit absence rather than silently dropping a case.
    """
    centerlines = centerline_geojsons or []
    try:
        parcel_raw = shapely_shape(parcel_geojson)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        return _unresolved(f"parcel geometry not parseable: {exc}")

    if parcel_raw.is_empty:
        return _unresolved("parcel geometry is empty")
    if not parcel_raw.is_valid:
        return _unresolved("parcel geometry is invalid")
    # MultiPolygon: take the largest piece. Halifax parcels are
    # occasionally published as MultiPolygons with slivers from clip
    # operations; the dominant piece is the real lot.
    if parcel_raw.geom_type == "MultiPolygon":
        parcel = max(parcel_raw.geoms, key=lambda g: g.area)
    elif parcel_raw.geom_type == "Polygon":
        parcel = parcel_raw
    else:
        return _unresolved(
            f"parcel geometry is {parcel_raw.geom_type}, expected Polygon"
        )

    centroid = parcel.centroid
    project = _make_equirectangular_projector(centroid.y, centroid.x)
    try:
        parcel_m = shapely_transform(project, parcel)
    except Exception as exc:  # noqa: BLE001 — shapely transform can raise broadly
        return _unresolved(f"projection failed: {exc}")
    if parcel_m.is_empty or not parcel_m.is_valid:
        return _unresolved("parcel collapsed under projection")

    area_m2 = float(parcel_m.area)
    perimeter_m = float(parcel_m.exterior.length)

    centerline_lines_m: list[LineString] = []
    for n_geojson in centerlines:
        try:
            n_geom = shapely_shape(n_geojson)
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if n_geom.is_empty or not n_geom.is_valid:
            continue
        if n_geom.geom_type == "LineString":
            _append_line(n_geom, project, centerline_lines_m)
        elif n_geom.geom_type == "MultiLineString":
            for g in n_geom.geoms:
                _append_line(g, project, centerline_lines_m)

    if centerline_lines_m:
        buffer = unary_union(centerline_lines_m).buffer(buffer_m)
        frontage_intersection = parcel_m.exterior.intersection(buffer)
        frontage_m = float(frontage_intersection.length)
        corner = _detect_corner(frontage_intersection, buffer_m=buffer_m)
    else:
        # No centerline segments reached this parcel. We can still report
        # area and perimeter, but frontage is unknown. The caller decides
        # whether to surface a frontage=0 result or flag it as uncertain.
        frontage_m = 0.0
        corner = False

    if frontage_m > 1.0:
        depth_m: float | None = area_m2 / frontage_m
    else:
        depth_m = None

    # Geometry confidence: 1.0 when the parcel polygon was clean enough
    # to project without repair. The extractor layers on frontage-quality
    # adjustments (e.g. drop to 0.7 when frontage is < 5% of perimeter)
    # because that requires knowing the perimeter — kept here, not in the
    # confidence math, since extractor.py decides what's "ok" for the
    # case-open payload.
    confidence = 1.0
    status = "ok" if confidence >= CONFIDENCE_OK_THRESHOLD else "uncertain"

    return LotMetrics(
        area_m2=area_m2,
        frontage_m=frontage_m,
        depth_m=depth_m,
        perimeter_m=perimeter_m,
        corner=corner,
        multi_unit=None,
        method="centerline_buffer",
        confidence=confidence,
        status=status,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _append_line(
    line: LineString,
    project: Any,
    out: list[LineString],
) -> None:
    """Project ``line`` to metres and append if non-empty."""
    try:
        line_m = shapely_transform(project, line)
    except Exception:  # noqa: BLE001 — silent skip on transform failure
        return
    if not line_m.is_empty:
        out.append(line_m)


def _make_equirectangular_projector(lat0: float, lon0: float):
    """Return a 2-arg fn for shapely.ops.transform projecting 4326 → metres.

    Equirectangular projection centred on (lat0, lon0). Suitable for
    parcel-scale geometry — at Halifax latitudes the area error is well
    under 0.1% for a 30 m × 30 m lot.
    """
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    def _project(x: float, y: float, z: float | None = None) -> tuple[float, float]:
        return ((x - lon0) * m_per_deg_lon, (y - lat0) * m_per_deg_lat)

    return _project


def _detect_corner(frontage: Any, *, buffer_m: float) -> bool:
    """True when the frontage intersection spans 2+ distinct bearings.

    A mid-block lot's frontage is one straight segment along one bearing
    (plus a small artifact at each end where the side edges cross the
    buffer — see below). A corner lot's frontage wraps around the
    parcel's road-facing corner and includes long segments along two
    perpendicular bearings.

    Artifact filter: ``ST_Intersection(parcel_boundary, buffer)`` also
    captures the portion of each PERPENDICULAR parcel edge that happens
    to fall inside the buffer near the corners of the parcel — those
    "artifact" segments are bounded in length by ``buffer_m`` and would
    otherwise contribute a spurious second bearing for every lot. We
    require segments to be longer than ``1.1 * buffer_m`` (slightly
    above the artifact ceiling) before counting their bearing.

    Bearings >= 30° apart are treated as distinct — a single slightly-
    curving road shouldn't trigger a corner classification.
    """
    if frontage.is_empty:
        return False
    min_segment_m = max(1.0, buffer_m * 1.1)
    if isinstance(frontage, LineString):
        return len(_line_distinct_bearings(frontage, min_segment_m)) >= 2
    if isinstance(frontage, MultiLineString):
        bearings: list[float] = []
        for line in frontage.geoms:
            for bearing in _line_distinct_bearings(line, min_segment_m):
                if not any(_bearing_close(bearing, b) for b in bearings):
                    bearings.append(bearing)
        return len(bearings) >= 2
    # GeometryCollection or unexpected — be conservative.
    return False


def _line_distinct_bearings(
    line: LineString, min_segment_m: float
) -> list[float]:
    """Return distinct dominant bearings within ``line`` (degrees [0,180)).

    Segments shorter than ``min_segment_m`` are skipped — see
    ``_detect_corner`` for why this filter matters (perpendicular-edge
    artifacts from the buffer intersection).
    """
    coords = list(line.coords)
    if len(coords) < 2:
        return []
    bearings: list[float] = []
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len < min_segment_m:
            continue
        angle_deg = math.degrees(math.atan2(dy, dx)) % 180
        if not any(_bearing_close(angle_deg, b) for b in bearings):
            bearings.append(angle_deg)
    return bearings


def _bearing_close(a: float, b: float, tolerance_deg: float = 30.0) -> bool:
    """Two bearings (both in [0,180)) are 'the same direction' if within tol."""
    diff = abs(a - b) % 180
    return diff <= tolerance_deg or diff >= 180 - tolerance_deg


def _unresolved(reason: str) -> LotMetrics:
    return LotMetrics(
        area_m2=None,
        frontage_m=None,
        depth_m=None,
        perimeter_m=None,
        corner=None,
        multi_unit=None,
        method="centerline_buffer",
        confidence=0.0,
        status="unresolved",
        reason=reason,
    )
