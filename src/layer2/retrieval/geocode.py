from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import ExternalDataset, ExternalDatasetFeature, GeocodeCache
from layer2.retrieval.google_geocoder import GoogleGeocoder, GoogleGeocoderConfig
from layer2.retrieval.location import LocationReference
from layer2.retrieval.spatial import ResolvedLocation

logger = logging.getLogger(__name__)


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

    See ``resolve_location_with_detail`` when the caller also needs the
    failure reason (to surface as a note back to an LLM, for example).
    """
    resolved, _ = resolve_location_with_detail(
        session, ref, use_cache=use_cache, google_geocoder=google_geocoder
    )
    return resolved


def resolve_location_with_detail(
    session: Session,
    ref: LocationReference,
    *,
    use_cache: bool = True,
    google_geocoder: GoogleGeocoder | None = None,
) -> tuple[ResolvedLocation | None, str | None]:
    """Same as ``resolve_location`` but also returns the failure detail.

    When the call ends in a miss, ``detail`` is a short human-readable
    reason (REQUEST_DENIED, ZERO_RESULTS, "no civic-address dataset
    matches…", "no external geocoder available", …) so the caller can
    surface it back to the LLM as a response note instead of leaving a
    silent miss the model has to guess at.

    On a successful resolve, ``detail`` is None — callers don't need a
    success message; the populated geometry is the answer.
    """
    cache_key = normalize_reference(ref)
    if use_cache:
        cached_row = _cache_get_row(session, cache_key)
        if cached_row is not None and cached_row.status == "linked":
            return _resolved_from_cache_row(cached_row), None
        if cached_row is not None:
            # Cached miss — replay the detail without re-running the lookup.
            return None, cached_row.detail

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
            else:
                # Surface the geocoder's own failure reason so audits and
                # the geocode_cache row record WHY (REQUEST_DENIED,
                # ZERO_RESULTS, NETWORK_ERROR, ...) rather than leaving a
                # silent miss that's hard to diagnose later.
                reason = getattr(external, "last_failure_reason", None)
                reason_detail = getattr(external, "last_failure_detail", None)
                if reason:
                    bits = [f"{external.name} failed: {reason}"]
                    if reason_detail:
                        bits.append(f"({reason_detail})")
                    detail = " ".join(bits)
                    resolver = f"{external.name}:{reason.lower()}"
        else:
            # No external geocoder available (key unset / lazy-build returned
            # None). Mark the miss explicitly so the caller can distinguish a
            # config issue from a genuine "address doesn't exist" miss —
            # otherwise both look identical to anything reading the cache row.
            resolver = "no_external_geocoder"
            geocoder_hint = (
                "no external geocoder available "
                "(GOOGLE_MAPS_API_KEY unset or geocoder disabled)"
            )
            detail = f"{detail}; {geocoder_hint}" if detail else geocoder_hint

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
    return resolved, (None if resolved is not None else (detail or None))


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


_MISSING_KEY_LOGGED = False


def _maybe_build_google_geocoder() -> GoogleGeocoder | None:
    """Lazy-build a GoogleGeocoder from settings if ``GOOGLE_MAPS_API_KEY``
    is set in the environment (or in an auto-loaded .env file).

    Returns None when no key is configured. We emit a one-shot WARNING the
    first time this happens per process so an operator can SEE that the
    geocoder is silently disabled — the previous file-path implementation
    failed silently when launched with the wrong cwd, which masked exactly
    this kind of misconfiguration for hours at a time.

    The function is intentionally module-level (not cached) so a key set
    mid-run via ``os.environ[...]`` picks up on the next call once the
    settings cache is cleared. ``get_settings()`` itself IS cached, so a
    long-running process started without the key needs the cache cleared
    or a restart for a fresh key to take effect.
    """
    global _MISSING_KEY_LOGGED
    from layer2.config import get_settings

    settings = get_settings()
    api_key = settings.google_maps_api_key
    if not api_key:
        if not _MISSING_KEY_LOGGED:
            logger.warning(
                "Google Maps geocoder disabled: GOOGLE_MAPS_API_KEY is not "
                "set. Civic-address resolution will fall through to the "
                "in-database resolver only. Set the env var (or add it to "
                ".env) and restart to enable the fallback."
            )
            _MISSING_KEY_LOGGED = True
        return None
    return GoogleGeocoder(
        GoogleGeocoderConfig(
            api_key=api_key,
            region_bias=settings.google_maps_region_bias,
            components=settings.google_maps_components,
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
    row = _cache_get_row(session, normalized_text)
    if row is None or row.geometry_geojson is None or row.status != "linked":
        return None
    return _resolved_from_cache_row(row)


def _cache_get_row(session: Session, normalized_text: str) -> GeocodeCache | None:
    return (
        session.execute(
            select(GeocodeCache).where(GeocodeCache.normalized_text == normalized_text)
        )
        .scalars()
        .first()
    )


def _resolved_from_cache_row(row: GeocodeCache) -> ResolvedLocation | None:
    if row.geometry_geojson is None or row.status != "linked":
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
