"""Builder for synthetic IFC files used by the ABS-49 extractor tests.

Generates a one-building, two-storey, N-space IFC4 model on demand so
tests can vary the inputs (missing OverallHeight, unusual occupancy
text, etc.) without shipping a binary fixture. Uses `ifcopenshell.api`
high-level helpers so the file ends up with the OwnerHistory,
RepresentationContext, and unit assignment that a real IFC export
would have — which is what the extractor exercises against.

The builder is intentionally tiny: it covers exactly the surface the
extractor reads and nothing more. New tests that need a different
shape add a `kwargs` switch here rather than hand-rolling another IFC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element


@dataclass
class SyntheticSpace:
    """One IfcSpace the builder will materialise.

    `occupancy_type` lands in `Pset_SpaceCommon.OccupancyType`;
    `object_type` lands in `IfcSpace.ObjectType`. Tests use these to
    exercise the extractor's residential / parking / bicycle matching.
    `gross_floor_area_m2` populates `Qto_SpaceBaseQuantities.GrossFloorArea`
    so the GFA sum-fallback path has data when the storey-level
    `Pset_BuildingCommon.GrossPlannedArea` is omitted.
    """

    name: str
    object_type: str | None = None
    occupancy_type: str | None = None
    gross_floor_area_m2: float | None = None
    storey_index: int = 0


@dataclass
class SyntheticBuildingSpec:
    """All the knobs that vary across the test matrix.

    Defaults give a "happy path" model: 2 storeys, no spaces, no
    properties — tests opt in to each attribute by setting the
    corresponding field. Storey elevations are in metres; the file is
    written with SI-metre units so no unit conversion happens on read.
    """

    object_type: str | None = "residential"
    overall_height_m: float | None = None
    storey_elevations_m: list[float] = field(default_factory=lambda: [0.0, 3.0])
    storey_gross_planned_area_m2: list[float] | None = None
    spaces: list[SyntheticSpace] = field(default_factory=list)
    footprint_coords: list[tuple[float, float]] | None = field(
        default_factory=lambda: [(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)]
    )
    include_building_pset: bool = True
    n_buildings: int = 1


def write_synthetic_ifc(spec: SyntheticBuildingSpec, out_path: Path) -> Path:
    """Materialise `spec` into a real IFC4 file at `out_path`.

    Returns the path written. Re-opening the file via `ifcopenshell.open`
    should produce the same object graph the extractor walks at runtime,
    so the test exercises the real read path end-to-end.
    """
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run(
        "root.create_entity", ifc, ifc_class="IfcProject", name="Test Project"
    )
    ifcopenshell.api.run(
        "unit.assign_unit", ifc, length={"is_metric": True, "raw": "METERS"}
    )
    # ifcopenshell auto-creates an IfcGeometricRepresentationContext on
    # the project once units are assigned; verify here so tests that
    # read raw_metadata.geometric_context find a real value.
    if not ifc.by_type("IfcGeometricRepresentationContext"):
        ifcopenshell.api.run(
            "context.add_context",
            ifc,
            context_type="Model",
            context_identifier="Body",
            target_view="MODEL_VIEW",
        )

    site = ifcopenshell.api.run(
        "root.create_entity", ifc, ifc_class="IfcSite", name="Site"
    )
    ifcopenshell.api.run(
        "aggregate.assign_object", ifc, products=[site], relating_object=project
    )

    for b_idx in range(spec.n_buildings):
        building = ifcopenshell.api.run(
            "root.create_entity",
            ifc,
            ifc_class="IfcBuilding",
            name=f"Building {b_idx + 1}",
        )
        building.ObjectType = spec.object_type
        ifcopenshell.api.run(
            "aggregate.assign_object", ifc, products=[building], relating_object=site
        )

        if b_idx == 0:
            _attach_storeys_and_spaces(ifc, building, spec)

    ifc.write(str(out_path))
    return out_path


def _attach_storeys_and_spaces(
    ifc, building, spec: SyntheticBuildingSpec
) -> None:
    storeys = []
    for idx, elev in enumerate(spec.storey_elevations_m):
        storey = ifcopenshell.api.run(
            "root.create_entity",
            ifc,
            ifc_class="IfcBuildingStorey",
            name=f"Storey {idx + 1}",
        )
        storey.Elevation = float(elev)
        storeys.append(storey)
    if storeys:
        ifcopenshell.api.run(
            "aggregate.assign_object",
            ifc,
            products=storeys,
            relating_object=building,
        )

    if spec.include_building_pset and spec.overall_height_m is not None:
        _set_pset(
            ifc,
            building,
            "Pset_BuildingCommon",
            {"OverallHeight": spec.overall_height_m},
        )

    if spec.storey_gross_planned_area_m2 is not None:
        for storey, gfa in zip(storeys, spec.storey_gross_planned_area_m2):
            _set_pset(
                ifc, storey, "Pset_BuildingCommon", {"GrossPlannedArea": gfa}
            )

    for sp in spec.spaces:
        space = ifcopenshell.api.run(
            "root.create_entity", ifc, ifc_class="IfcSpace", name=sp.name
        )
        if sp.object_type is not None:
            space.ObjectType = sp.object_type
        ifcopenshell.api.run(
            "aggregate.assign_object",
            ifc,
            products=[space],
            relating_object=storeys[sp.storey_index],
        )
        if sp.occupancy_type is not None:
            _set_pset(
                ifc, space, "Pset_SpaceCommon", {"OccupancyType": sp.occupancy_type}
            )
        if sp.gross_floor_area_m2 is not None:
            _set_qto(
                ifc,
                space,
                "Qto_SpaceBaseQuantities",
                {"GrossFloorArea": sp.gross_floor_area_m2},
            )

    if spec.footprint_coords and storeys:
        _attach_ground_slab(ifc, storeys[0], spec.footprint_coords)


def _set_pset(ifc, element, pset_name: str, props: dict) -> None:
    pset = ifcopenshell.api.run(
        "pset.add_pset", ifc, product=element, name=pset_name
    )
    ifcopenshell.api.run(
        "pset.edit_pset", ifc, pset=pset, properties=props
    )


def _set_qto(ifc, element, qto_name: str, props: dict) -> None:
    """Add a quantity-take-off set.

    ifcopenshell.api treats Qto and Pset symmetrically — the difference
    is just the entity name on disk (`IfcElementQuantity` vs.
    `IfcPropertySet`). `pset.add_qto` is the helper. Property values
    here become `IfcQuantityArea` etc. according to the qto template.
    """
    qto = ifcopenshell.api.run(
        "pset.add_qto", ifc, product=element, name=qto_name
    )
    ifcopenshell.api.run(
        "pset.edit_qto", ifc, qto=qto, properties=props
    )


def _attach_ground_slab(
    ifc, storey, coords: list[tuple[float, float]]
) -> None:
    """Create a flat IfcSlab with the given footprint polygon under `storey`.

    Minimal `IfcExtrudedAreaSolid` over an `IfcArbitraryClosedProfileDef`
    — the exact shape the extractor's `_slab_to_polygon_coords` reads.
    Polygon is closed automatically if the caller didn't repeat the
    first point.
    """
    if coords[0] != coords[-1]:
        coords = list(coords) + [coords[0]]

    points = [
        ifc.create_entity("IfcCartesianPoint", Coordinates=p) for p in coords
    ]
    polyline = ifc.create_entity("IfcPolyline", Points=points)
    profile = ifc.create_entity(
        "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=polyline
    )

    origin = ifc.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    z_dir = ifc.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
    x_dir = ifc.create_entity("IfcDirection", DirectionRatios=(1.0, 0.0, 0.0))
    placement = ifc.create_entity(
        "IfcAxis2Placement3D", Location=origin, Axis=z_dir, RefDirection=x_dir
    )
    extrude = ifc.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=placement,
        ExtrudedDirection=z_dir,
        Depth=0.2,
    )

    context = ifc.by_type("IfcGeometricRepresentationContext")[0]
    shape_rep = ifc.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[extrude],
    )
    product_rep = ifc.create_entity(
        "IfcProductDefinitionShape", Representations=[shape_rep]
    )

    slab = ifcopenshell.api.run(
        "root.create_entity", ifc, ifc_class="IfcSlab", name="Ground Slab"
    )
    slab.PredefinedType = "FLOOR"
    slab.Representation = product_rep

    ifcopenshell.api.run(
        "spatial.assign_container",
        ifc,
        products=[slab],
        relating_structure=storey,
    )


__all__ = [
    "SyntheticBuildingSpec",
    "SyntheticSpace",
    "write_synthetic_ifc",
]
