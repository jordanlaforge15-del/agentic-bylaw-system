"""Pure-geometry lot characteristics from a parcel polygon.

Given a parcel polygon and the set of road centerlines near it (all in
EPSG:4326), compute area, road frontage, depth, and corner-lot status.

Method
------
Frontage uses a centerline-buffer heuristic. Each centerline is projected
to local metres, buffered by ``buffer_m``, and intersected with the parcel
exterior; the intersection length is the frontage contributed by that
centerline. Centerlines that belong to the same street (matched by name
when present, else treated as independent) are unioned before buffering so
multiple segments of one street collapse to a single contribution.

The buffer width is sized to reach typical Halifax urban parcel boundaries.
HRM parcels are *not* tessellated to the road centerline as an earlier
revision of this module assumed — they're inset 7–15 m for the road
allowance (sidewalk + half-ROW). ABS-23 measured the actual gap on
6321 Quinpool (7.8 / 9.5 m), 1505 Barrington (7.7–9.0 m), and
5251 Duke (8.3 m+). The 12 m default catches the typical urban gap
without overreaching into adjacent parcels.

Corner / multi-frontage detection
---------------------------------
The count of *distinct streets* contributing > ``MIN_STREET_CONTRIB_M``
of intersection length is the signal:
  - 1 street → mid-block; depth = area / frontage is meaningful.
  - 2 streets → corner; depth is geometrically undefined and is omitted.
  - 3 streets → triangular / cul-de-sac-throat; report frontage, omit depth.
  - 4+ streets, or frontage / perimeter > ``CITY_BLOCK_FRONTAGE_RATIO`` →
    the buffer has wrapped around the parcel (institutional / city-block
    parcel — e.g. 5251 Duke). Frontage / depth / corner are suppressed
    and the caller drops confidence so the model hedges.

Replacing bearing-distinct detection: the prior approach filtered out
segments shorter than ``1.1 × buffer_m`` to avoid the perpendicular-edge
artifact, which meant any buffer increase silently disabled corner
detection for the typical-size frontage. The street-grouping approach
is robust to buffer changes.

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


# Half-width of the centerline buffer in metres. Sized to reach typical
# Halifax urban parcel boundaries — HRM parcels are inset 7–15 m from
# the road centerline (road allowance), so anything narrower silently
# drops corner-lot frontage. See ABS-23 for the per-address measurements.
DEFAULT_BUFFER_M: float = 12.0


# Threshold below which we mark the result ``uncertain`` rather than
# ``ok``. The geometry confidence (computed in compute_lot_metrics) is
# 1.0 for a clean parcel polygon and drops when shapely had to repair
# the input. Frontage / buffer-tuning risk is layered on by the
# extractor, which knows about the perimeter ratio.
CONFIDENCE_OK_THRESHOLD: float = 0.5


# A street counts toward the multi-frontage tally only when it
# contributes at least this much intersection length. Filters out the
# tiny slivers a far-away centerline can produce when the buffer
# just barely reaches a corner of the parcel.
MIN_STREET_CONTRIB_M: float = 4.0


# When the total buffer-intersection length exceeds this fraction of the
# parcel perimeter, the buffer has wrapped around the parcel — typical
# of an institutional / city-block lot (5251 Duke). Frontage / depth /
# corner are suppressed and confidence drops.
CITY_BLOCK_FRONTAGE_RATIO: float = 0.75


# Above this street count the parcel is surrounded by streets, not
# fronting them — same suppression as the frontage-ratio rule.
CITY_BLOCK_STREET_COUNT: int = 4


@dataclass(frozen=True)
class LotMetrics:
    """Computed spatial characteristics of a lot.

    ``status`` is ``"ok"`` when the computation succeeded and the
    confidence is at or above the threshold; ``"uncertain"`` when the
    geometry was usable but the result is shaky (e.g. shapely had to
    repair the polygon, or the buffer wrapped around the parcel);
    ``"unresolved"`` when the geometry was unusable. ``reason`` is
    populated on ``unresolved`` / ``uncertain``.

    All linear measurements are metres; ``area_m2`` is square metres.
    ``corner`` is True when the lot's road-facing boundary spans two
    or more distinct streets. ``street_count`` is the number of
    distinct streets contributing non-trivial frontage; the extractor
    uses it to decide confidence and which fields to surface.
    ``multi_unit`` is left as None by this module; the extractor sets
    it after a civic-address dataset lookup.
    """

    area_m2: float | None
    frontage_m: float | None
    depth_m: float | None
    perimeter_m: float | None
    corner: bool | None
    street_count: int | None
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
        if self.street_count is not None:
            d["street_count"] = self.street_count
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
    centerline_names: list[str | None] | None = None,
) -> LotMetrics:
    """Compute lot metrics from a parcel polygon and nearby centerlines.

    Inputs are GeoJSON geometry dicts in EPSG:4326. ``centerline_geojsons``
    may be empty (no centerline data, or no nearby segments) — in that
    case frontage, depth, and corner are reported as 0 / None / False and
    the caller can adjust confidence based on coverage.

    ``centerline_names`` is an optional parallel list of street names for
    grouping segments that belong to the same street. When absent or
    ``None`` for a given index, the segment is treated as its own
    "street" — fine for synthetic tests with one centerline per street,
    but suboptimal for real data where HRM splits each road at every
    intersection. The extractor passes ``STR_NAME`` from the centerlines
    dataset so multiple segments of e.g. Quinpool count once.

    Never raises for bad input — returns ``LotMetrics`` with
    ``status="unresolved"`` and ``reason`` set so the caller can persist
    an explicit absence rather than silently dropping a case.
    """
    centerlines = centerline_geojsons or []
    names = centerline_names or [None] * len(centerlines)
    if len(names) != len(centerlines):
        return _unresolved(
            f"centerline_names length {len(names)} != "
            f"centerline_geojsons length {len(centerlines)}"
        )

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
    parcel_boundary = parcel_m.exterior

    # Project every centerline to metres and group by street name. A
    # missing name yields a synthetic per-index key ("__unnamed_<i>"),
    # which preserves the "each centerline is its own street" behaviour
    # the synthetic test fixtures rely on.
    groups: dict[str, list[LineString]] = {}
    for i, geojson in enumerate(centerlines):
        try:
            geom = shapely_shape(geojson)
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if geom.is_empty or not geom.is_valid:
            continue
        key = names[i] if names[i] else f"__unnamed_{i}"
        if geom.geom_type == "LineString":
            _append_projected_line(geom, project, groups, key)
        elif geom.geom_type == "MultiLineString":
            for g in geom.geoms:
                _append_projected_line(g, project, groups, key)

    if groups:
        # Total frontage: union every projected line (across all streets)
        # and intersect parcel boundary once. Single-pass intersection
        # avoids double-counting the small overlap at street intersections.
        all_lines: list[LineString] = [ln for lns in groups.values() for ln in lns]
        union_buf = unary_union(all_lines).buffer(buffer_m)
        frontage_m = float(parcel_boundary.intersection(union_buf).length)

        # Per-street contribution: how much of the parcel boundary falls
        # inside the buffer around just that street's centerlines.
        street_count = 0
        for lines in groups.values():
            group_buf = unary_union(lines).buffer(buffer_m)
            contrib = float(parcel_boundary.intersection(group_buf).length)
            if contrib >= MIN_STREET_CONTRIB_M:
                street_count += 1
    else:
        # No centerline segments reached this parcel. We can still report
        # area and perimeter, but frontage is unknown. The caller decides
        # whether to surface a frontage=0 result or flag it as uncertain.
        frontage_m = 0.0
        street_count = 0

    perim_ratio = frontage_m / perimeter_m if perimeter_m > 0 else 0.0
    city_block_like = (
        street_count >= CITY_BLOCK_STREET_COUNT
        or perim_ratio >= CITY_BLOCK_FRONTAGE_RATIO
    )

    if city_block_like:
        # Parcel is surrounded by streets, not fronting them. Suppress
        # frontage / depth / corner and let the extractor drop confidence.
        return LotMetrics(
            area_m2=area_m2,
            frontage_m=None,
            depth_m=None,
            perimeter_m=perimeter_m,
            corner=None,
            street_count=street_count,
            multi_unit=None,
            method="centerline_buffer",
            confidence=1.0,
            status="uncertain",
            reason=(
                f"buffer wrapped around parcel "
                f"(street_count={street_count}, frontage/perim={perim_ratio:.2f}) "
                f"— likely city block; frontage / depth / corner suppressed"
            ),
        )

    corner = street_count >= 2 if frontage_m > 1.0 else False

    # Depth is only meaningful for a single-frontage (mid-block) lot.
    # For corner / triangular parcels area-divided-by-frontage produces
    # nonsense — e.g. 6321 Quinpool reported 65 m of "depth" when the
    # lot is actually 35 m deep — so we omit it.
    if street_count == 1 and frontage_m > 1.0:
        depth_m: float | None = area_m2 / frontage_m
    else:
        depth_m = None

    # Geometry confidence: 1.0 when the parcel polygon was clean enough
    # to project without repair. The extractor layers on frontage-quality
    # adjustments (e.g. drop to 0.7 for triangular / cul-de-sac lots)
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
        street_count=street_count,
        multi_unit=None,
        method="centerline_buffer",
        confidence=confidence,
        status=status,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _append_projected_line(
    line: LineString,
    project: Any,
    groups: dict[str, list[LineString]],
    key: str,
) -> None:
    """Project ``line`` to metres and add to the named group."""
    try:
        line_m = shapely_transform(project, line)
    except Exception:  # noqa: BLE001 — silent skip on transform failure
        return
    if not line_m.is_empty:
        groups.setdefault(key, []).append(line_m)


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


def _unresolved(reason: str) -> LotMetrics:
    return LotMetrics(
        area_m2=None,
        frontage_m=None,
        depth_m=None,
        perimeter_m=None,
        corner=None,
        street_count=None,
        multi_unit=None,
        method="centerline_buffer",
        confidence=0.0,
        status="unresolved",
        reason=reason,
    )
