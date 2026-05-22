# Revit → Phase-1 taxonomy parameter map (ABS-50)

This is the contract `src/layer1/parsers/aps_submission.py::_PropertyMapper`
expects when reading APS Model Derivative property JSON. Each row lists
the Revit `BuiltInParameter` name(s) that satisfy a Phase-1 attribute,
the fallback order, and the confidence the mapper attaches to the
extracted value.

The mapping uses `BuiltInParameter` names directly (as APS surfaces
them in the `properties` payload), not the LookupParameter user-facing
labels — those vary with Revit's UI language and templates.

## Direct mappings (read straight from a property)

| Phase-1 attribute            | Revit parameter (in order)                                       | Confidence | Notes |
|------------------------------|------------------------------------------------------------------|------------|-------|
| `building_height_m`          | `BUILDING_HEIGHT`, `ROOF_LEVEL_HIGH_OFFSET`, `Overall Height`    | 1.0        | First non-null wins. Internal unit is mm; mapper divides by 1000. |
| `primary_use_class`          | `Project Building Type`, `Building Type`, `Occupancy Type`       | 0.4        | Free-text — heuristic confidence per the ABS-49 policy. |
| `gross_floor_area_m2`        | sum of `HOST_AREA_COMPUTED` (else `Area`) on Floor category      | 1.0        | Internal unit is mm²; mapper divides by 1,000,000. |

## Counts / derived from element collections

| Phase-1 attribute               | How                                                            | Confidence | Notes |
|---------------------------------|----------------------------------------------------------------|------------|-------|
| `building_height_storeys`       | Count of elements where `Other.Category == "Levels"`           | 1.0        | Includes basement / mezzanine levels — match the IFC extractor's behaviour. |
| `residential_unit_count`        | Count of Room / Area elements whose name or `Room Name` contains "dwelling", "apartment", "residential", or "unit" | 1.0 | Order matters: residential is matched before parking so "Residential Storage Unit" doesn't double-count. |
| `parking_stalls_count`          | Same, keywords: "parking", "stall", "car park"                 | 1.0        | |
| `bicycle_stalls_count`          | Same, keywords: "bicycle", "bike", "cycle"                     | 1.0        | |

## Out of scope for this issue

- **`building_footprint_area_m2`** — set by the existing ABS-51
  derived-attribute pipeline when the model ships a ground-storey
  slab; the APS path doesn't currently emit it directly. A
  follow-up issue can pull `HOST_AREA_COMPUTED` from the lowest-
  elevation floor element to populate this.
- **`PropertyLine` polygon extraction** — APS metadata describes
  property-line points but not as a closed polygon. The full polygon
  needs an SVF view export, deferred to a follow-up. Until then
  setbacks rely on the parcel polygon from `parcel.geometry_geojson`
  rather than the BIM-side property line.
- **Coordinate-system metadata** — the IFC path stashes
  `IfcGeometricRepresentationContext` for ABS-52. The APS metadata
  carries project location (lat/lon, base point) under
  `Project Information.Project Address`; wiring that into
  `project_location_from_aps(...)` is straightforward but deferred
  here so ABS-50 can land without an end-to-end APS run to validate.

## Discovery tips

When auditing a customer Revit file before ingest, use:

1. `RevitLookup` (Autodesk free add-in) to confirm the exact
   BuiltInParameter names per element.
2. Run `APSClient.fetch_properties(urn, guid)` against a translation
   of the customer's file; the returned JSON has every property under
   `data.collection[].properties` keyed by category.

Document any new BuiltInParameter the mapper needs to recognise here
when extending `_HEIGHT_KEYS` / `_USE_CLASS_KEYS` / `_AREA_KEYS` in
`aps_submission.py`.

## Cost note

Every `extract_aps` call against the real APS endpoint triggers a paid
Model Derivative translation. The IFC path (ABS-49) is always cheaper
and equally accurate when the architect will export IFC. APS exists
for shops that won't.
