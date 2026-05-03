from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from shapely.geometry import mapping as shapely_mapping
from shapely.geometry import shape as shapely_shape
from shapely.validation import explain_validity, make_valid

from layer1.datasets.canonical import CANONICAL_FIELDS, CoercionError, coerce_value
from layer1.datasets.config import DatasetConfig
from layer1.models.enums import ParseStatus


class FeatureData(BaseModel):
    feature_key: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    canonical_attributes: dict[str, Any] = Field(default_factory=dict)
    geometry: dict[str, Any]
    bbox: dict[str, float]
    parse_status: ParseStatus = ParseStatus.PARSED
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeoDatasetParseResult(BaseModel):
    config: DatasetConfig
    content_hash: str
    declared_crs: str
    feature_count: int
    features: list[FeatureData]
    warnings: list[str] = Field(default_factory=list)


_TEMPLATE_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def parse_geojson(path: str | Path, config: DatasetConfig) -> GeoDatasetParseResult:
    """Parse a GeoJSON FeatureCollection into the dataset ingest shape.

    The dataset's declared CRS (config.crs) must match the file's CRS. We do
    not silently reproject — that would mask provenance issues. Add pyproj
    and an explicit reproject step the day a non-EPSG:4326 dataset arrives.
    """
    fs_path = Path(path)
    raw_bytes = fs_path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    payload = json.loads(raw_bytes.decode("utf-8"))

    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"expected GeoJSON FeatureCollection, got {payload.get('type')!r}")

    declared_crs = _extract_crs(payload, default="EPSG:4326")
    if declared_crs != config.crs:
        raise ValueError(
            f"dataset CRS mismatch: file declares {declared_crs!r}, config declares "
            f"{config.crs!r}. Reprojection is not supported in this version."
        )

    raw_features = payload.get("features", []) or []
    warnings: list[str] = []
    seen_keys: set[str] = set()
    parsed: list[FeatureData] = []

    for index, feature in enumerate(raw_features):
        try:
            parsed_feature = _parse_feature(feature, index, config, warnings)
        except ValueError as exc:
            warnings.append(f"feature[{index}] dropped: {exc}")
            continue
        if parsed_feature.feature_key in seen_keys:
            warnings.append(
                f"feature[{index}] duplicate feature_key {parsed_feature.feature_key!r}; keeping first"
            )
            continue
        seen_keys.add(parsed_feature.feature_key)
        parsed.append(parsed_feature)

    return GeoDatasetParseResult(
        config=config,
        content_hash=content_hash,
        declared_crs=declared_crs,
        feature_count=len(parsed),
        features=parsed,
        warnings=warnings,
    )


def _extract_crs(payload: dict[str, Any], default: str) -> str:
    """Best-effort CRS extraction from a GeoJSON FeatureCollection.

    GeoJSON 2016 (RFC 7946) mandates EPSG:4326, but earlier files commonly
    include a top-level ``crs`` member. We honour either; absence of any
    declaration falls back to the caller-supplied default.
    """
    crs_obj = payload.get("crs")
    if not crs_obj:
        return default
    name = crs_obj.get("properties", {}).get("name")
    if not name:
        return default
    if name.startswith("EPSG:"):
        return name
    if "EPSG" in name and "::" in name:
        return "EPSG:" + name.rsplit("::", 1)[-1]
    if name in {"urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:OGC::CRS84"}:
        return "EPSG:4326"
    return name


def _parse_feature(
    feature: dict[str, Any],
    index: int,
    config: DatasetConfig,
    warnings: list[str],
) -> FeatureData:
    if not isinstance(feature, dict):
        raise ValueError(f"feature is not a JSON object: {type(feature).__name__}")
    geometry = feature.get("geometry")
    if not geometry:
        raise ValueError("feature has no geometry")
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError(f"feature properties not a JSON object: {type(properties).__name__}")

    geom = shapely_shape(geometry)
    geometry_status = ParseStatus.PARSED
    repaired = False
    if not geom.is_valid:
        reason = explain_validity(geom)
        repaired_geom = make_valid(geom)
        if repaired_geom.is_empty or not repaired_geom.is_valid:
            raise ValueError(f"invalid geometry, repair failed: {reason}")
        geom = repaired_geom
        geometry_status = ParseStatus.UNCERTAIN
        repaired = True
    minx, miny, maxx, maxy = geom.bounds
    bbox = {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy}

    feature_key_field = config.attributes.feature_key
    raw_key = properties.get(feature_key_field)
    if raw_key in (None, ""):
        raise ValueError(f"missing feature_key field {feature_key_field!r}")
    feature_key = str(raw_key)

    canonical, attr_status, feature_warnings = _apply_canonical_mapping(properties, config)
    status = attr_status if geometry_status == ParseStatus.PARSED else geometry_status
    if repaired:
        feature_warnings.append(
            f"geometry was invalid ({reason}); repaired via shapely.make_valid"
        )
    for w in feature_warnings:
        warnings.append(f"feature[{index}] ({feature_key}): {w}")

    return FeatureData(
        feature_key=feature_key,
        attributes=properties,
        canonical_attributes=canonical,
        geometry=shapely_mapping(geom) if repaired else geometry,
        bbox=bbox,
        parse_status=status,
        metadata={"geometry_repaired": True} if repaired else {},
    )


def _apply_canonical_mapping(
    properties: dict[str, Any],
    config: DatasetConfig,
) -> tuple[dict[str, Any], ParseStatus, list[str]]:
    """Translate raw properties to the canonical attribute vocabulary.

    Optional fields whose source is missing or null are simply omitted from the
    canonical dict (callers should treat absence as null). Required fields
    that fail to coerce mark the feature ``UNCERTAIN`` and record a warning,
    rather than dropping the feature outright — geometry is still useful even
    when one attribute is malformed.
    """
    canonical: dict[str, Any] = {}
    warnings: list[str] = []
    status = ParseStatus.PARSED

    for canonical_name, mapping in config.attributes.canonical.items():
        if mapping.synthesize is not None:
            try:
                canonical[canonical_name] = _render_template(mapping.synthesize, properties)
            except KeyError as exc:
                if mapping.optional:
                    continue
                warnings.append(f"missing source field {exc.args[0]!r} for synthesized {canonical_name!r}")
                status = ParseStatus.UNCERTAIN
            continue

        raw = properties.get(mapping.from_field)
        if raw is None or (isinstance(raw, str) and raw.strip() == "") or raw in mapping.null_when:
            if mapping.optional:
                continue
            warnings.append(f"missing required source field {mapping.from_field!r} for {canonical_name!r}")
            status = ParseStatus.UNCERTAIN
            continue

        try:
            canonical[canonical_name] = coerce_value(raw, mapping.type)
        except CoercionError as exc:
            if mapping.optional:
                warnings.append(str(exc))
                continue
            warnings.append(f"coercion failed for {canonical_name!r}: {exc}")
            status = ParseStatus.UNCERTAIN

    # Sanity: every canonical key actually persisted is one we declared.
    extra = set(canonical) - set(CANONICAL_FIELDS)
    if extra:  # pragma: no cover - defensive; config validator forbids this
        raise RuntimeError(f"canonical keys not declared in CANONICAL_FIELDS: {sorted(extra)}")

    return canonical, status, warnings


def _render_template(template: str, properties: dict[str, Any]) -> str:
    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in properties:
            raise KeyError(key)
        return str(properties[key])

    return _TEMPLATE_PATTERN.sub(_sub, template)
