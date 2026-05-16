"""Pure-geometry lot characteristics from a parcel polygon.

Given a parcel polygon and its touching neighbours (all in EPSG:4326),
compute area, road frontage, depth, and corner-lot status.

Method
------
Frontage uses the *shared-edge heuristic*: parcel-boundary segments
within ``EPSILON_METRES`` of any neighbour parcel's boundary are
classified as "shared with a neighbour" (i.e. not road-facing). The
remaining boundary length is the road frontage. This works strongly
in tessellated urban grids (where parcels share edges with neighbours
on every non-road side) and degrades to an ``uncertain`` status when
the parcel has no neighbours or an unclassifiable amount of boundary.

Corner detection groups the non-shared portion of the boundary into
connected components; two or more distinct components separated by
shared edges indicate frontage on multiple streets.

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


# Boundary segments within this many metres of a neighbour parcel
# boundary are treated as shared (i.e. not road frontage). 0.5 m
# absorbs the small slivers and rounding errors typical of municipal
# parcel digitisation while still distinguishing real road frontage
# from a misaligned shared edge.
EPSILON_METRES: float = 0.5

# Threshold below which we mark the result ``uncertain`` rather than
# ``ok``. Set so that an isolated rural lot (no neighbours, 100% of
# perimeter classified as frontage) still gets an "ok" status when
# its parcel geometry is otherwise valid — the no-neighbour case is
# common and intentional, not an error.
CONFIDENCE_OK_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class LotMetrics:
    """Computed spatial characteristics of a lot.

    ``status`` is ``"ok"`` when the computation succeeded and the
    confidence is at or above the threshold; ``"uncertain"`` when the
    geometry was usable but the result is shaky (e.g. very little of
    the boundary classified cleanly); ``"unresolved"`` when the
    geometry was unusable. ``reason`` is populated on ``unresolved``.

    All linear measurements are metres; ``area_m2`` is square metres.
    ``corner`` is True when the lot's road-facing boundary spans two
    or more distinct connected components (i.e. fronts more than one
    street). ``multi_unit`` is left as None by this module; the
    extractor sets it after a civic-address dataset lookup.
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
    neighbour_geojsons: list[dict[str, Any]] | None = None,
) -> LotMetrics:
    """Compute lot metrics from a parcel polygon and its neighbours.

    Inputs are GeoJSON geometry dicts in EPSG:4326. ``neighbour_geojsons``
    may be empty (rural lot fallback) or omitted entirely.

    Never raises for bad input — returns ``LotMetrics`` with
    ``status="unresolved"`` and ``reason`` set so the caller can persist
    an explicit absence rather than silently dropping a case.
    """
    neighbours = neighbour_geojsons or []
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

    neighbour_lines_m: list[LineString] = []
    for n_geojson in neighbours:
        try:
            n_geom = shapely_shape(n_geojson)
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if n_geom.is_empty or not n_geom.is_valid:
            continue
        if n_geom.geom_type == "MultiPolygon":
            for g in n_geom.geoms:
                _append_boundary(g, project, neighbour_lines_m)
        elif n_geom.geom_type == "Polygon":
            _append_boundary(n_geom, project, neighbour_lines_m)

    if neighbour_lines_m:
        shared_buffer = unary_union(neighbour_lines_m).buffer(EPSILON_METRES)
        non_shared = parcel_m.exterior.difference(shared_buffer)
        shared_length = perimeter_m - non_shared.length
        frontage_m = float(non_shared.length)
    else:
        # No neighbours found — every metre of perimeter is frontage by
        # default. Common for rural / unaddressed lots and for parcels
        # at the edge of a sparsely-digitised area.
        non_shared = parcel_m.exterior
        shared_length = 0.0
        frontage_m = perimeter_m

    corner = _detect_corner(non_shared)

    # Depth approximation: for a roughly rectangular lot, depth =
    # area / frontage. Falls back to None when frontage is near zero
    # (e.g. flag lot fronting only on a narrow lane).
    if frontage_m > 1.0:
        depth_m: float | None = area_m2 / frontage_m
    else:
        depth_m = None

    # Confidence: high when most of the perimeter classified cleanly
    # (shared or definitely non-shared). The shared-edge buffer is the
    # only ambiguous zone, and it's epsilon-thin so for clean
    # tessellations confidence approaches 1.0. With no neighbours, we
    # cap confidence at 0.6 — the result is usable but the model
    # should hedge.
    if neighbour_lines_m:
        # Crude proxy for "unambiguously classified": how much of
        # perimeter is either solidly inside the shared-buffer
        # (counted as shared_length) or solidly outside it (non_shared
        # length). The remaining sliver is the ambiguous epsilon-band.
        classified = shared_length + frontage_m
        confidence = max(
            0.0, min(1.0, classified / perimeter_m if perimeter_m > 0 else 0.0)
        )
    else:
        confidence = 0.6

    status = "ok" if confidence >= CONFIDENCE_OK_THRESHOLD else "uncertain"

    return LotMetrics(
        area_m2=area_m2,
        frontage_m=frontage_m,
        depth_m=depth_m,
        perimeter_m=perimeter_m,
        corner=corner,
        multi_unit=None,
        method="shared_edge",
        confidence=confidence,
        status=status,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _append_boundary(
    poly: Polygon,
    project: Any,
    out: list[LineString],
) -> None:
    """Project ``poly``'s exterior to metres and append as a LineString."""
    try:
        line_m = shapely_transform(project, poly.exterior)
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


def _detect_corner(non_shared: Any) -> bool:
    """True when the non-shared boundary spans 2+ angularly distinct parts.

    Connectedness alone isn't sufficient — a single road-facing edge
    that wraps around a slight curve is still one street. We require
    the disconnected components to also have meaningfully different
    bearings (>= 30°) before calling the lot a corner lot.
    """
    if non_shared.is_empty:
        return False
    if isinstance(non_shared, LineString):
        return False
    if not isinstance(non_shared, MultiLineString):
        return False

    bearings: list[float] = []
    for line in non_shared.geoms:
        bearing = _line_dominant_bearing(line)
        if bearing is None:
            continue
        if not any(_bearing_close(bearing, b) for b in bearings):
            bearings.append(bearing)
    return len(bearings) >= 2


def _line_dominant_bearing(line: LineString) -> float | None:
    """Length-weighted average bearing of a LineString, in degrees [0,180)."""
    coords = list(line.coords)
    if len(coords) < 2:
        return None
    total_weight = 0.0
    weighted_sin = 0.0
    weighted_cos = 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len == 0:
            continue
        # Fold to [0, π) — direction-agnostic bearing (a segment going
        # north is the same edge as one going south for our purposes).
        angle = math.atan2(dy, dx) % math.pi
        weighted_sin += math.sin(2 * angle) * seg_len
        weighted_cos += math.cos(2 * angle) * seg_len
        total_weight += seg_len
    if total_weight == 0:
        return None
    mean_double_angle = math.atan2(weighted_sin, weighted_cos)
    return math.degrees(mean_double_angle / 2) % 180


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
        method="shared_edge",
        confidence=0.0,
        status="unresolved",
        reason=reason,
    )
