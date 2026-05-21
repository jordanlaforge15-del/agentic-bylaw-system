"""Synthetic APS Model Derivative properties payloads.

Matches the shape `GET {urn}/metadata/{guid}/properties` returns:
`{"data": {"collection": [{...element...}, ...]}}`. Each element has
an `objectid`, `name`, `externalId`, and a `properties` dict of
category → name → value mappings.

Values are in Revit's internal units (mm for length, mm² for area)
because that's what APS surfaces — the extractor's `_coerce_metres`
helper applies the 0.001 / 0.001² conversion.
"""
from __future__ import annotations

from typing import Any


def _building(*, height_mm: float | None = 9500.0) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {
        "Other": {"Category": "Buildings"},
        "Identity Data": {"Type Name": "Standard Building"},
    }
    if height_mm is not None:
        properties["Dimensions"] = {"BUILDING_HEIGHT": height_mm}
    return {
        "objectid": 1,
        "name": "Building 1",
        "externalId": "bld-1",
        "properties": properties,
    }


def _level(idx: int, elevation_mm: float) -> dict[str, Any]:
    return {
        "objectid": 10 + idx,
        "name": f"Level {idx}",
        "externalId": f"lvl-{idx}",
        "properties": {
            "Other": {"Category": "Levels"},
            "Constraints": {"Elevation": elevation_mm},
        },
    }


def _floor(idx: int, area_mm2: float) -> dict[str, Any]:
    return {
        "objectid": 100 + idx,
        "name": f"Floor {idx}",
        "externalId": f"flr-{idx}",
        "properties": {
            "Other": {"Category": "Floors"},
            "Dimensions": {"HOST_AREA_COMPUTED": area_mm2},
        },
    }


def _room(idx: int, *, room_name: str) -> dict[str, Any]:
    return {
        "objectid": 200 + idx,
        "name": room_name,
        "externalId": f"rm-{idx}",
        "properties": {
            "Other": {"Category": "Rooms"},
            "Identity Data": {"Room Name": room_name},
        },
    }


def _project_info(building_type: str | None = "Residential") -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {
        "Other": {"Category": "Project Information"},
        "Identity Data": {},
    }
    if building_type is not None:
        properties["Identity Data"]["Project Building Type"] = building_type
    return {
        "objectid": 999,
        "name": "Project Information",
        "externalId": "proj-info",
        "properties": properties,
    }


def happy_path_payload() -> dict[str, Any]:
    """Realistic small project: 2 floors, 2 levels, 3 rooms, height set."""
    return {
        "data": {
            "collection": [
                _building(height_mm=9500.0),
                _level(1, 0.0),
                _level(2, 3000.0),
                _floor(1, 180_000_000.0),  # 180 m²
                _floor(2, 180_000_000.0),  # 180 m²
                _room(1, room_name="Apartment 1"),
                _room(2, room_name="Apartment 2"),
                _room(3, room_name="Parking Bay 1"),
                _project_info("Residential"),
            ]
        }
    }


def no_height_payload() -> dict[str, Any]:
    """Building with no BUILDING_HEIGHT and no ROOF_LEVEL_HIGH_OFFSET."""
    return {
        "data": {
            "collection": [
                _building(height_mm=None),
                _level(1, 0.0),
                _floor(1, 100_000_000.0),
                _project_info("Residential"),
            ]
        }
    }


def no_use_class_payload() -> dict[str, Any]:
    """Project Information present but Project Building Type absent."""
    return {
        "data": {
            "collection": [
                _building(),
                _level(1, 0.0),
                _floor(1, 100_000_000.0),
                _project_info(building_type=None),
            ]
        }
    }


def parking_and_bikes_payload() -> dict[str, Any]:
    """Mixed-use project with rooms in three buckets."""
    return {
        "data": {
            "collection": [
                _building(),
                _level(1, 0.0),
                _level(2, 3000.0),
                _floor(1, 250_000_000.0),
                _room(1, room_name="Apartment 1"),
                _room(2, room_name="Dwelling 2"),
                _room(3, room_name="Parking Stall 1"),
                _room(4, room_name="Parking Stall 2"),
                _room(5, room_name="Parking Stall 3"),
                _room(6, room_name="Bicycle Storage"),
                _project_info("Residential"),
            ]
        }
    }


__all__ = [
    "happy_path_payload",
    "no_height_payload",
    "no_use_class_payload",
    "parking_and_bikes_payload",
]
