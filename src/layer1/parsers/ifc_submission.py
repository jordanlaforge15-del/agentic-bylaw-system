"""IFC → Phase-1 attribute extraction (ABS-49).

Direct attribute extraction from IFC files via IfcOpenShell. No AI,
no inference — every attribute either reads a documented IFC property
(Pset_BuildingCommon, IfcBuildingStorey.Elevation, …) or derives one
from a hierarchy walk.

Plugs into the submission factory: importing this module registers the
IFC extractor for `SubmissionSourceType.IFC`. Callers do not import
this module directly; they call `extract_submission(..., IFC, ...)`
from `submission_factory`. The "import-as-registration" pattern matches
how `layer1.parsers.factory` is wired and avoids an explicit registry
of registries.

Scope (deliberately bounded):

* In: building-level attributes the Phase-1 taxonomy recognises
  (height, GFA, footprint area, primary use, unit / parking / bicycle
  stall counts) plus the unconverted footprint polygon + project-local
  → world transform context for ABS-51 / ABS-52.
* Out: derived attributes (setbacks, coverage, FAR — ABS-51) and any
  coordinate transform (ABS-52). This module stays in project-local
  units; ABS-51 reads the footprint + transform and reprojects.

Confidence policy: every attribute is one of three confidence levels:

| level | meaning                                                  |
| ----- | -------------------------------------------------------- |
| 1.0   | Property read directly off the documented IFC property.  |
| 0.6   | Derived from a fallback (storey elevation, space sum).   |
| 0.4   | Heuristic on a free-text field (ObjectType keyword).     |

Missing values are *not* persisted — the caller (ABS-53 UI) prompts the
submitter for manual override. We never default a missing height to 0.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from layer1.models.submission_schemas import (
    ExtractedAttribute,
    SubmissionExtractionResult,
    SubmissionIngestConfig,
)
from layer1.parsers.submission_factory import register_extractor
from layer2.compliance.db.models import (
    SubmissionAttributeSource,
    SubmissionSourceType,
)


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Public entry point — registered with the submission factory
# ----------------------------------------------------------------------


def extract_ifc(
    source_path: Path, config: SubmissionIngestConfig
) -> SubmissionExtractionResult:
    """Extract Phase-1 attributes from an IFC file.

    Returns a `SubmissionExtractionResult` shaped for the scaffold
    pipeline. Soft-fails (extractor returns a result with warnings)
    on attribute-level gaps; hard-fails (raises) only when the file
    can't be opened or has no `IfcBuilding`, because then the rest of
    the extraction has nothing to anchor to.

    Importing `ifcopenshell` is deferred so the layer-1 import graph
    doesn't pull the C++ extension unless an IFC file is actually
    being processed.
    """
    try:
        import ifcopenshell
        import ifcopenshell.util.element  # noqa: F401 — registers helpers
        import ifcopenshell.util.unit  # noqa: F401
    except ImportError as exc:  # pragma: no cover — install-time error
        raise RuntimeError(
            "ifcopenshell is required to ingest IFC submissions. "
            "Install with `.venv/bin/pip install -e '.[bim]'`."
        ) from exc

    try:
        ifc_file = ifcopenshell.open(str(source_path))
    except Exception as exc:
        raise RuntimeError(f"failed to open IFC file {source_path}: {exc}") from exc

    buildings = list(ifc_file.by_type("IfcBuilding"))
    if not buildings:
        raise RuntimeError(
            f"IFC file {source_path} has no IfcBuilding — "
            "cannot extract building-level attributes."
        )

    # The Phase-1 taxonomy assumes a single proposed building per
    # submission. Multi-building sites are rare in zoning submissions
    # (each gets its own permit); we extract the first and surface a
    # warning so the user knows the others were ignored.
    state = _ExtractionState(
        unit_scale_to_metres=ifcopenshell.util.unit.calculate_unit_scale(ifc_file),
    )
    if len(buildings) > 1:
        state.warnings.append(
            f"IFC contains {len(buildings)} IfcBuilding entities — "
            f"extracted from the first ({buildings[0].Name or buildings[0].GlobalId!r}); "
            "others ignored."
        )

    primary = buildings[0]
    state.attributes.extend(_extract_building_attributes(ifc_file, primary, state))

    storeys = _storeys_for_building(ifc_file, primary)
    state.attributes.extend(_extract_storey_attributes(ifc_file, primary, storeys, state))

    spaces = _spaces_for_building(ifc_file, primary)
    state.attributes.extend(_extract_space_count_attributes(ifc_file, spaces, state))

    footprint_geojson = _extract_footprint_polygon(ifc_file, primary, storeys, state)
    geo_context = _extract_geometric_context(ifc_file, state)
    raw_metadata = {
        "extractor": {
            "name": "ifc-submission",
            "ifcopenshell_version": ifcopenshell.version,
            "ifc_schema": ifc_file.schema,
            "unit_scale_to_metres": state.unit_scale_to_metres,
            "building_global_id": primary.GlobalId,
            "building_name": primary.Name,
            "n_storeys": len(storeys),
            "n_spaces": len(spaces),
            "geometric_context": geo_context,
        }
    }

    return SubmissionExtractionResult(
        source_type=SubmissionSourceType.IFC,
        source_artifact_path=str(source_path),
        attributes=state.attributes,
        footprint_geojson=footprint_geojson,
        raw_metadata=raw_metadata,
        warnings=state.warnings,
    )


# ----------------------------------------------------------------------
# Internal state
# ----------------------------------------------------------------------


@dataclass
class _ExtractionState:
    """Mutable bag passed down the extraction stack.

    Keeps the per-attribute helpers pure-ish: they read the IFC file,
    add to `attributes` / `warnings`. State holds the unit scale so
    every helper that returns a metre value can multiply through it
    without re-deriving from the file.
    """

    unit_scale_to_metres: float
    attributes: list[ExtractedAttribute] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Building-level attributes
# ----------------------------------------------------------------------


def _extract_building_attributes(
    ifc_file: Any, building: Any, state: _ExtractionState
) -> list[ExtractedAttribute]:
    """Read `IfcBuilding` direct properties + key Psets.

    Three direct attributes come from the building entity itself:

    * `primary_use_class` — `ObjectType` first, `Pset_BuildingUse.MarketCategory`
      fallback. ObjectType is free-text in the IFC spec, so confidence 0.4.
    * `building_footprint_area_m2` — `Pset_BuildingCommon.BuildingFootprint`
      (rare). Storey-slab fallback lives in `_extract_storey_attributes` so
      it can read the ground storey's `Pset_QuantityArea.NetArea`.
    * Height handled in `_extract_storey_attributes` because the fallback
      needs storey elevations.
    """
    import ifcopenshell.util.element

    out: list[ExtractedAttribute] = []
    psets = ifcopenshell.util.element.get_psets(building) or {}

    # primary_use_class
    use_class = building.ObjectType
    confidence = 1.0 if use_class else 0.0
    if not use_class:
        market_category = (psets.get("Pset_BuildingUse") or {}).get("MarketCategory")
        if market_category:
            use_class = market_category
            confidence = 0.6
    if use_class:
        out.append(
            ExtractedAttribute(
                attribute_key="primary_use_class",
                value=str(use_class).strip().lower(),
                confidence=0.4 if confidence == 1.0 else confidence,  # ObjectType is free-text
                evidence={
                    "ifc_entity": "IfcBuilding",
                    "ifc_global_id": building.GlobalId,
                    "source_field": "ObjectType" if confidence != 0.6 else "Pset_BuildingUse.MarketCategory",
                },
            )
        )
    else:
        state.warnings.append(
            "IfcBuilding.ObjectType and Pset_BuildingUse.MarketCategory are both empty — "
            "primary_use_class not extracted; UI should prompt for manual override."
        )

    # building_footprint_area_m2 (Pset path; ground-slab fallback in storey code)
    footprint = (psets.get("Pset_BuildingCommon") or {}).get("BuildingFootprint")
    if isinstance(footprint, (int, float)):
        out.append(
            ExtractedAttribute(
                attribute_key="building_footprint_area_m2",
                value=float(footprint),
                unit="m2",
                confidence=1.0,
                evidence={
                    "ifc_entity": "IfcBuilding",
                    "ifc_global_id": building.GlobalId,
                    "source_field": "Pset_BuildingCommon.BuildingFootprint",
                },
            )
        )

    return out


# ----------------------------------------------------------------------
# Storey-driven attributes (height + storey count + GFA fallback)
# ----------------------------------------------------------------------


def _extract_storey_attributes(
    ifc_file: Any,
    building: Any,
    storeys: list[Any],
    state: _ExtractionState,
) -> list[ExtractedAttribute]:
    """Storey count + building height (direct or elevation-derived) + GFA fallback."""
    import ifcopenshell.util.element

    out: list[ExtractedAttribute] = []

    # building_height_storeys
    out.append(
        ExtractedAttribute(
            attribute_key="building_height_storeys",
            value=len(storeys),
            unit="storeys",
            confidence=1.0 if storeys else 0.0,
            evidence={
                "ifc_entity": "IfcBuildingStorey",
                "ifc_global_ids": [s.GlobalId for s in storeys],
            },
        )
    )

    # building_height_m — try Pset_BuildingCommon.OverallHeight first
    building_psets = ifcopenshell.util.element.get_psets(building) or {}
    overall_height = (building_psets.get("Pset_BuildingCommon") or {}).get(
        "OverallHeight"
    )
    if isinstance(overall_height, (int, float)):
        out.append(
            ExtractedAttribute(
                attribute_key="building_height_m",
                value=float(overall_height) * state.unit_scale_to_metres,
                unit="m",
                confidence=1.0,
                evidence={
                    "ifc_entity": "IfcBuilding",
                    "ifc_global_id": building.GlobalId,
                    "source_field": "Pset_BuildingCommon.OverallHeight",
                },
            )
        )
    else:
        # Fallback: derive from min/max storey elevation.
        elevations = [
            float(s.Elevation) * state.unit_scale_to_metres
            for s in storeys
            if isinstance(s.Elevation, (int, float))
        ]
        if elevations:
            derived = max(elevations) - min(elevations)
            if derived > 0:
                out.append(
                    ExtractedAttribute(
                        attribute_key="building_height_m",
                        value=derived,
                        unit="m",
                        confidence=0.6,
                        evidence={
                            "ifc_entity": "IfcBuildingStorey",
                            "source_field": "max(Elevation) - min(Elevation)",
                            "elevations_m": elevations,
                        },
                    )
                )
                state.warnings.append(
                    "Pset_BuildingCommon.OverallHeight not set; building_height_m "
                    "derived from storey elevation span (confidence 0.6) — "
                    "doesn't account for roof above topmost storey."
                )
            else:
                state.warnings.append(
                    "storey elevations all identical or invalid — "
                    "building_height_m not extracted; UI should prompt for manual override."
                )
        else:
            state.warnings.append(
                "no IfcBuildingStorey.Elevation values and no Pset_BuildingCommon.OverallHeight — "
                "building_height_m not extracted; UI should prompt for manual override."
            )

    # gross_floor_area_m2 — sum across storeys (Pset_BuildingCommon.GrossPlannedArea)
    gfa_per_storey: list[float] = []
    storey_evidence: list[dict] = []
    for storey in storeys:
        storey_psets = ifcopenshell.util.element.get_psets(storey) or {}
        gross = (storey_psets.get("Pset_BuildingCommon") or {}).get("GrossPlannedArea")
        if isinstance(gross, (int, float)):
            value_m2 = float(gross) * (state.unit_scale_to_metres ** 2)
            gfa_per_storey.append(value_m2)
            storey_evidence.append(
                {
                    "storey_global_id": storey.GlobalId,
                    "storey_name": storey.Name,
                    "gross_planned_area_m2": value_m2,
                }
            )
    if gfa_per_storey:
        out.append(
            ExtractedAttribute(
                attribute_key="gross_floor_area_m2",
                value=sum(gfa_per_storey),
                unit="m2",
                confidence=1.0,
                evidence={
                    "ifc_entity": "IfcBuildingStorey",
                    "source_field": "sum(Pset_BuildingCommon.GrossPlannedArea)",
                    "per_storey": storey_evidence,
                },
            )
        )
    # Space-sum fallback is handled inside _extract_space_count_attributes
    # because it shares the IfcSpace traversal.

    return out


# ----------------------------------------------------------------------
# Space-driven attributes (residential/parking/bicycle counts + GFA fallback)
# ----------------------------------------------------------------------


# Occupancy keyword → taxonomy attribute key. Keys lowercased; we match
# against `OccupancyType.lower()` and `ObjectType.lower()`. Lists are
# intentionally short — IFC has no canonical occupancy vocabulary so we
# stick to the spec's suggested values plus the obvious synonyms.
_OCCUPANCY_KEYWORDS = {
    "residential_unit_count": ("residential unit", "dwelling", "apartment", "unit"),
    "parking_stalls_count": ("parking", "parking stall", "parking space", "car park"),
    "bicycle_stalls_count": ("bicycle", "bike", "bicycle storage", "cycle"),
}


def _extract_space_count_attributes(
    ifc_file: Any, spaces: list[Any], state: _ExtractionState
) -> list[ExtractedAttribute]:
    """Count IfcSpaces by occupancy keyword and sum-fallback GFA."""
    import ifcopenshell.util.element

    counts = {k: 0 for k in _OCCUPANCY_KEYWORDS}
    matched_global_ids: dict[str, list[str]] = {k: [] for k in _OCCUPANCY_KEYWORDS}
    unrecognised: list[str] = []

    # GFA sum-fallback book-keeping (only emit if Pset path missed).
    space_area_sum_m2 = 0.0
    space_area_evidence: list[dict] = []
    any_space_area_found = False

    for space in spaces:
        psets = ifcopenshell.util.element.get_psets(space) or {}
        occupancy = (psets.get("Pset_SpaceCommon") or {}).get("OccupancyType")
        object_type = space.ObjectType
        text = " ".join(t for t in (occupancy, object_type) if t).lower()

        matched_key: str | None = None
        for key, keywords in _OCCUPANCY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                matched_key = key
                break
        if matched_key:
            counts[matched_key] += 1
            matched_global_ids[matched_key].append(space.GlobalId)
        elif text:
            unrecognised.append(text)

        # GFA sum-fallback — Qto_SpaceBaseQuantities.GrossFloorArea, then NetArea.
        qto = ifcopenshell.util.element.get_psets(space, qtos_only=True) or {}
        space_quants = qto.get("Qto_SpaceBaseQuantities") or {}
        area = space_quants.get("GrossFloorArea") or space_quants.get("NetFloorArea")
        if isinstance(area, (int, float)):
            value_m2 = float(area) * (state.unit_scale_to_metres ** 2)
            space_area_sum_m2 += value_m2
            space_area_evidence.append(
                {
                    "space_global_id": space.GlobalId,
                    "space_name": space.Name,
                    "area_m2": value_m2,
                }
            )
            any_space_area_found = True

    out: list[ExtractedAttribute] = []
    for key in ("residential_unit_count", "parking_stalls_count", "bicycle_stalls_count"):
        # Always emit count — zero is a real, meaningful value here ("no
        # parking provided" is a regulator-visible fact). Confidence
        # tracks how comprehensive the source vocabulary was: 1.0 when
        # everything matched, 0.6 when some spaces had unrecognised
        # occupancy text (we might have missed some).
        count = counts[key]
        confidence = 1.0
        if unrecognised:
            confidence = 0.6
        out.append(
            ExtractedAttribute(
                attribute_key=key,
                value=count,
                unit="stalls" if "stalls" in key else "units",
                confidence=confidence,
                evidence={
                    "ifc_entity": "IfcSpace",
                    "matched_global_ids": matched_global_ids[key],
                    "match_keywords": list(_OCCUPANCY_KEYWORDS[key]),
                    "unrecognised_occupancy_texts": unrecognised if confidence < 1.0 else [],
                },
            )
        )

    # GFA fallback — only emit if `_extract_storey_attributes` didn't
    # already add one. We check `state.attributes` to know.
    has_gfa = any(a.attribute_key == "gross_floor_area_m2" for a in state.attributes)
    if not has_gfa and any_space_area_found:
        out.append(
            ExtractedAttribute(
                attribute_key="gross_floor_area_m2",
                value=space_area_sum_m2,
                unit="m2",
                confidence=0.6,
                evidence={
                    "ifc_entity": "IfcSpace",
                    "source_field": "sum(Qto_SpaceBaseQuantities.GrossFloorArea or NetFloorArea)",
                    "per_space": space_area_evidence,
                },
            )
        )
        state.warnings.append(
            "Pset_BuildingCommon.GrossPlannedArea not set on storeys; "
            "gross_floor_area_m2 derived from IfcSpace area quantities (confidence 0.6)."
        )

    return out


# ----------------------------------------------------------------------
# Footprint geometry + geometric context (for ABS-51 / ABS-52)
# ----------------------------------------------------------------------


def _extract_footprint_polygon(
    ifc_file: Any,
    building: Any,
    storeys: list[Any],
    state: _ExtractionState,
) -> dict[str, Any] | None:
    """Return the ground-storey footprint polygon (project-local CRS) as GeoJSON.

    Strategy: find the lowest-elevation storey, look for `IfcSlab` of
    PredefinedType `FLOOR` or `BASESLAB` in its decomposition, extract
    the 2D polygon from the slab's `IfcExtrudedAreaSolid.SweptArea`.
    No CRS conversion happens here — ABS-52 reads the polygon + the
    geometric context from `raw_metadata` to reproject.

    Returns None when no usable slab is found; the issue spec puts
    footprint extraction in the "best-effort, surface a warning" bucket
    rather than failing the whole ingest. ABS-51 then can't compute
    setbacks for this submission and the UI prompts the architect for
    manual measurements.
    """
    import ifcopenshell.util.element

    if not storeys:
        state.warnings.append(
            "no IfcBuildingStorey under IfcBuilding — footprint polygon not extracted."
        )
        return None

    # Lowest-elevation storey heuristic. Ties are stable (first wins).
    ground = min(
        storeys,
        key=lambda s: float(s.Elevation) if isinstance(s.Elevation, (int, float)) else 0.0,
    )
    slabs = [
        elem
        for elem in ifcopenshell.util.element.get_decomposition(ground)
        if elem.is_a("IfcSlab")
    ]
    if not slabs:
        state.warnings.append(
            "ground storey has no IfcSlab children — footprint polygon not extracted; "
            "ABS-51 setback computation will need a manual polygon."
        )
        return None

    polygon_coords = _slab_to_polygon_coords(slabs[0], state)
    if polygon_coords is None:
        state.warnings.append(
            "ground-storey IfcSlab geometry not extrudable to a polygon — "
            "footprint polygon not extracted."
        )
        return None

    # GeoJSON Polygon: outer ring must close (first == last) per RFC 7946.
    ring = list(polygon_coords)
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])

    return {
        "type": "Polygon",
        "coordinates": [ring],
        "properties": {
            "crs": "project-local",
            "unit": "metres",
            "source": "IfcSlab on ground IfcBuildingStorey",
            "slab_global_id": slabs[0].GlobalId,
        },
    }


def _slab_to_polygon_coords(
    slab: Any, state: _ExtractionState
) -> list[tuple[float, float]] | None:
    """Pull the 2D outline of an IfcSlab in project-local coordinates.

    IFC stores slabs as `IfcExtrudedAreaSolid` over an
    `IfcArbitraryClosedProfileDef` (most common case). Walk to the
    profile's polyline and return its points scaled to metres. Other
    slab geometries (mapped, BIM360 SweptDisk, NURBS) return None;
    handling them fully would require running the IfcOpenShell
    geometry kernel, which is too heavy for unit tests. ABS-51 / the
    UI prompt for a manual polygon in those cases.
    """
    for rep in getattr(slab, "Representation", None).Representations or []:
        for item in rep.Items or []:
            if not item.is_a("IfcExtrudedAreaSolid"):
                continue
            profile = getattr(item, "SweptArea", None)
            if profile is None or not profile.is_a(
                "IfcArbitraryClosedProfileDef"
            ):
                continue
            curve = getattr(profile, "OuterCurve", None)
            if curve is None or not curve.is_a("IfcPolyline"):
                continue
            points = curve.Points or []
            if len(points) < 3:
                continue
            scale = state.unit_scale_to_metres
            return [
                (float(p.Coordinates[0]) * scale, float(p.Coordinates[1]) * scale)
                for p in points
            ]
    return None


def _extract_geometric_context(
    ifc_file: Any, state: _ExtractionState
) -> dict[str, Any]:
    """Capture the project-to-world transform context for ABS-52.

    `IfcGeometricRepresentationContext` carries the project's CRS
    (`TrueNorth`, `WorldCoordinateSystem` origin) — exactly what ABS-52
    needs to translate project-local coordinates into a real CRS.

    Returned as a small JSON-safe dict on `raw_metadata.geometric_context`
    so we don't promise the downstream module a specific pyproj
    transform object — ABS-52 builds the transform from this dict.
    """
    contexts = list(ifc_file.by_type("IfcGeometricRepresentationContext"))
    if not contexts:
        state.warnings.append(
            "no IfcGeometricRepresentationContext found — ABS-52 will have no "
            "project-to-world transform to apply; footprint coords stay project-local."
        )
        return {}

    ctx = contexts[0]
    true_north = getattr(ctx, "TrueNorth", None)
    world_cs = getattr(ctx, "WorldCoordinateSystem", None)
    origin = None
    if world_cs is not None:
        location = getattr(world_cs, "Location", None)
        if location is not None and getattr(location, "Coordinates", None):
            origin = [float(c) for c in location.Coordinates]
    return {
        "context_type": ctx.ContextType,
        "context_identifier": ctx.ContextIdentifier,
        "coordinate_space_dimension": ctx.CoordinateSpaceDimension,
        "precision": ctx.Precision,
        "world_origin": origin,
        "true_north_direction": list(true_north.DirectionRatios)
        if true_north is not None
        else None,
    }


# ----------------------------------------------------------------------
# Hierarchy helpers
# ----------------------------------------------------------------------


def _storeys_for_building(ifc_file: Any, building: Any) -> list[Any]:
    """Return `IfcBuildingStorey` children of `building`, sorted by Elevation."""
    import ifcopenshell.util.element

    storeys = [
        elem
        for elem in ifcopenshell.util.element.get_decomposition(building)
        if elem.is_a("IfcBuildingStorey")
    ]
    storeys.sort(
        key=lambda s: float(s.Elevation)
        if isinstance(s.Elevation, (int, float))
        else 0.0
    )
    return storeys


def _spaces_for_building(ifc_file: Any, building: Any) -> list[Any]:
    """Return every `IfcSpace` in `building`'s decomposition tree."""
    import ifcopenshell.util.element

    return [
        elem
        for elem in ifcopenshell.util.element.get_decomposition(building)
        if elem.is_a("IfcSpace")
    ]


# ----------------------------------------------------------------------
# Register with the submission factory
# ----------------------------------------------------------------------

register_extractor(SubmissionSourceType.IFC, extract_ifc)


__all__ = ["extract_ifc"]
