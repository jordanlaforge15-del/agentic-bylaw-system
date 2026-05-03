from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature, GeocodeCache
from layer2.retrieval.google_geocoder import (
    GoogleGeocoder,
    GoogleGeocoderConfig,
    load_google_maps_api_key,
)
from layer2.retrieval.location import LocationReference
from layer2.retrieval.spatial import ResolvedLocation


_STREET_SUFFIX_NORMALIZATION = {
    "street": "st",
    "st.": "st",
    "avenue": "ave",
    "ave.": "ave",
    "road": "rd",
    "rd.": "rd",
    "boulevard": "blvd",
    "blvd.": "blvd",
    "drive": "dr",
    "dr.": "dr",
    "lane": "ln",
    "ln.": "ln",
    "crescent": "cres",
    "cres.": "cres",
    "court": "ct",
    "ct.": "ct",
    "place": "pl",
    "pl.": "pl",
    "highway": "hwy",
    "hwy.": "hwy",
    "parkway": "pkwy",
    "pkwy.": "pkwy",
    "terrace": "terr",
    "terr.": "terr",
}


def normalize_street(street: str | None) -> str:
    """Lowercase, collapse whitespace, normalize common suffix variants.

    Used both as the cache key normalizer and as the matcher against the
    civic-address dataset. Street-name normalization is a notorious
    rabbit hole; this is the minimum viable version that handles the
    common cases without pulling in libpostal.
    """
    if not street:
        return ""
    text = re.sub(r"\s+", " ", street.strip().lower())
    parts = text.split()
    if parts and parts[-1] in _STREET_SUFFIX_NORMALIZATION:
        parts[-1] = _STREET_SUFFIX_NORMALIZATION[parts[-1]]
    return " ".join(parts)


def normalize_reference(ref: LocationReference) -> str:
    if ref.kind == "parcel_id":
        return f"pid:{(ref.parcel_id or '').strip()}"
    if ref.kind == "civic_address":
        street = normalize_street(ref.street)
        unit = (ref.unit or "").strip().lower()
        unit_part = f"#{unit}" if unit else ""
        return f"civic:{(ref.civic_number or '').strip().lower()} {street}{unit_part}".strip()
    if ref.kind == "named_place":
        return f"named:{(ref.name or '').strip().lower()}"
    if ref.kind == "intersection":
        streets = sorted(normalize_street(s) for s in (ref.streets or []))
        return f"intersection:{'|'.join(streets)}"
    return f"raw:{ref.raw_text.strip().lower()}"  # pragma: no cover


def resolve_location(
    session: Session,
    ref: LocationReference,
    *,
    use_cache: bool = True,
    google_geocoder: GoogleGeocoder | None = None,
) -> ResolvedLocation | None:
    """Resolve a LocationReference to a ResolvedLocation, layered resolvers.

    Order:
      1. Cache hit (if ``use_cache``).
      2. Parcel-id direct lookup against any civic-address dataset.
      3. Civic-address lookup against any role=civic_address dataset.
      4. External geocoder (Google Maps), if a key is available — closes
         the loop for civic-address and named-place questions when no
         in-database civic-address dataset exists. Provenance is weaker
         (``ResolvedLocation.source = "google_maps"``) but the spatial
         channel still works end-to-end.
      5. Refusal — return None.

    Returns None on miss; never raises for a missing match. A None return is
    the correct behaviour and the caller should refuse the question rather
    than guess. ``ResolvedLocation.source`` names the resolver so the
    citation chain can attribute the lookup.

    ``google_geocoder`` is injected for testability — production callers can
    leave it None and let the helper assemble one from settings.
    """
    cache_key = normalize_reference(ref)
    if use_cache:
        cached = _cache_get(session, cache_key)
        if cached is not None:
            return cached

    resolved: ResolvedLocation | None = None
    detail = ""
    source_dataset_id: int | None = None
    source_feature_id: int | None = None
    resolver = "miss"
    status = "no_match"

    if ref.kind == "parcel_id":
        match = _find_by_parcel_id(session, ref.parcel_id or "")
        if match is not None:
            feature, dataset = match
            resolved = _feature_to_resolved(feature, dataset, ref, kind_hint="parcel")
            source_dataset_id, source_feature_id = dataset.id, feature.id
            resolver = f"parcel_id:{dataset.name}"
            status = "linked"
        else:
            detail = f"no civic-address dataset has parcel_id {ref.parcel_id!r}"

    elif ref.kind == "civic_address":
        match = _find_by_civic_address(session, ref.civic_number or "", ref.street or "")
        if match is not None:
            feature, dataset = match
            resolved = _feature_to_resolved(feature, dataset, ref)
            source_dataset_id, source_feature_id = dataset.id, feature.id
            resolver = f"civic_address:{dataset.name}"
            status = "linked"
        else:
            detail = (
                f"no civic-address dataset matches civic_number={ref.civic_number!r} "
                f"street={ref.street!r}"
            )

    else:
        # named_place / intersection: in-database resolvers can't handle
        # these, but the Google fallback below may pick them up.
        detail = f"in-database resolvers do not handle kind={ref.kind!r}"
        resolver = "unsupported_kind"

    # External fallback — only runs when no in-database resolver succeeded.
    if resolved is None:
        external = google_geocoder or _maybe_build_google_geocoder()
        if external is not None:
            external_match = external.resolve(ref)
            if external_match is not None:
                resolved = external_match
                resolver = external.name
                status = "linked"
                detail = (
                    f"resolved via {external.name} with confidence "
                    f"{external_match.confidence:.2f}"
                )

    if use_cache:
        _cache_put(
            session,
            normalized_text=cache_key,
            ref=ref,
            resolved=resolved,
            resolver=resolver,
            status=status,
            detail=detail,
            source_dataset_id=source_dataset_id,
            source_feature_id=source_feature_id,
        )
    return resolved


def _find_by_parcel_id(
    session: Session, parcel_id: str
) -> tuple[ExternalDatasetFeature, ExternalDataset] | None:
    if not parcel_id:
        return None
    civic_dataset_ids = _civic_address_dataset_ids(session)
    if not civic_dataset_ids:
        return None
    rows = (
        session.execute(
            select(ExternalDatasetFeature, ExternalDataset)
            .join(ExternalDataset, ExternalDataset.id == ExternalDatasetFeature.external_dataset_id)
            .where(ExternalDataset.id.in_(civic_dataset_ids))
        )
        .all()
    )
    for feature, dataset in rows:
        canonical = feature.canonical_attributes_json or {}
        if str(canonical.get("parcel_id") or "") == parcel_id:
            return feature, dataset
    return None


def _find_by_civic_address(
    session: Session, civic_number: str, street: str
) -> tuple[ExternalDatasetFeature, ExternalDataset] | None:
    if not civic_number or not street:
        return None
    target_civic = civic_number.strip().lower()
    target_street = normalize_street(street)
    civic_dataset_ids = _civic_address_dataset_ids(session)
    if not civic_dataset_ids:
        return None
    rows = (
        session.execute(
            select(ExternalDatasetFeature, ExternalDataset)
            .join(ExternalDataset, ExternalDataset.id == ExternalDatasetFeature.external_dataset_id)
            .where(ExternalDataset.id.in_(civic_dataset_ids))
        )
        .all()
    )
    for feature, dataset in rows:
        canonical = feature.canonical_attributes_json or {}
        feature_civic = str(canonical.get("civic_number") or "").strip().lower()
        feature_street = normalize_street(canonical.get("street_name"))
        if feature_civic == target_civic and feature_street == target_street:
            return feature, dataset
    return None


def _maybe_build_google_geocoder() -> GoogleGeocoder | None:
    """Lazy-build a GoogleGeocoder from settings if a key is on disk.

    Returning None when there's no key file is the silent-skip path that
    keeps existing test environments working without injecting credentials.
    The function is intentionally module-level (not cached) so a key dropped
    in mid-run picks up on the next call.
    """
    from layer2.config import get_settings

    settings = get_settings()
    api_key = load_google_maps_api_key(settings.google_maps_api_key_path)
    if not api_key:
        return None
    return GoogleGeocoder(
        GoogleGeocoderConfig(
            api_key=api_key,
            region_bias=settings.google_maps_region_bias,
            timeout_s=settings.google_maps_request_timeout_s,
        )
    )


def _civic_address_dataset_ids(session: Session) -> list[int]:
    rows = (
        session.execute(select(ExternalDataset.id, ExternalDataset.metadata_json))
        .all()
    )
    return [row.id for row in rows if (row.metadata_json or {}).get("role") == "civic_address"]


def _feature_to_resolved(
    feature: ExternalDatasetFeature,
    dataset: ExternalDataset,
    ref: LocationReference,
    *,
    kind_hint: str | None = None,
) -> ResolvedLocation:
    geom = feature.geometry_geojson
    geom_type = (geom or {}).get("type", "")
    if kind_hint == "parcel" or geom_type in {"Polygon", "MultiPolygon"}:
        kind = "parcel"
    elif geom_type in {"Point", "MultiPoint"}:
        kind = "point"
    else:
        kind = "shape"
    canonical = feature.canonical_attributes_json or {}
    return ResolvedLocation(
        kind=kind,
        geometry=geom,
        confidence=float(canonical.get("confidence") or ref.confidence or 0.95),
        source=dataset.name,
        reference_text=ref.raw_text,
    )


def _cache_get(session: Session, normalized_text: str) -> ResolvedLocation | None:
    row = (
        session.execute(
            select(GeocodeCache).where(GeocodeCache.normalized_text == normalized_text)
        )
        .scalars()
        .first()
    )
    if row is None or row.geometry_geojson is None or row.status != "linked":
        return None
    geom = row.geometry_geojson
    geom_type = geom.get("type", "")
    if geom_type in {"Polygon", "MultiPolygon"}:
        kind = "parcel"
    elif geom_type in {"Point", "MultiPoint"}:
        kind = "point"
    else:
        kind = "shape"
    return ResolvedLocation(
        kind=kind,
        geometry=geom,
        confidence=row.confidence or 0.0,
        source=row.resolver,
        reference_text=row.raw_text,
    )


def _cache_put(
    session: Session,
    *,
    normalized_text: str,
    ref: LocationReference,
    resolved: ResolvedLocation | None,
    resolver: str,
    status: str,
    detail: str,
    source_dataset_id: int | None,
    source_feature_id: int | None,
) -> None:
    existing = (
        session.execute(
            select(GeocodeCache).where(GeocodeCache.normalized_text == normalized_text)
        )
        .scalars()
        .first()
    )
    payload: dict[str, Any] = {
        "raw_text": ref.raw_text,
        "kind": ref.kind,
        "status": status,
        "resolver": resolver,
        "source_dataset_id": source_dataset_id,
        "source_feature_id": source_feature_id,
        "geometry_geojson": resolved.geometry if resolved else None,
        "confidence": resolved.confidence if resolved else None,
        "detail": detail or None,
        "metadata_json": {"reference": ref.model_dump()},
    }
    if existing is None:
        existing = GeocodeCache(
            normalized_text=normalized_text,
            created_at=datetime.now(timezone.utc),
            **payload,
        )
        session.add(existing)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)
    session.flush()
