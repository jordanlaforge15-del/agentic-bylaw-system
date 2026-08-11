from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from layer2.retrieval.location import LocationReference
from layer2.retrieval.resolution_quality import CONFIDENCE_BY_LOCATION_TYPE
from layer2.retrieval.spatial import ResolvedLocation


# Confidence is mapped from Google's location_type enum. ROOFTOP means the
# response is a precise rooftop match; RANGE_INTERPOLATED is interpolated
# along a street; GEOMETRIC_CENTER and APPROXIMATE are progressively coarser.
_CONFIDENCE_BY_TYPE = CONFIDENCE_BY_LOCATION_TYPE
#
# ABS-466 — which location_types we accept, decided by NAME rather than by a
# float comparison. The previous rule was ``confidence < 0.6`` against a
# GEOMETRIC_CENTER worth exactly 0.6, so the "somewhere in this neighbourhood"
# case the comment claimed to exclude passed by equality. Two live cache rows
# (567 Windsor Street, 89 Jubilee Road) were linked at 0.6 because of it.
#
# The decision, deliberately, is kind-aware:
#
#   * A civic address names a *building*. Google returning GEOMETRIC_CENTER
#     for one means it never found the civic number and fell back to the
#     centre of a block or route — the point can sit on any parcel on the
#     block, so it cannot be trusted to select a zoning polygon. Rejected.
#   * A named place or an intersection has no rooftop to match. GEOMETRIC_CENTER
#     is the *best available* answer for "Barrington and Spring Garden", and
#     refusing it would remove those question kinds entirely. Accepted, and the
#     resolution is reported as centroid-quality so callers still qualify it.
#
# APPROXIMATE is never accepted (locality-level, useless for parcel work), and
# neither is an unrecognised/missing type. Membership in a named set can't
# drift by a rounding change the way the old float boundary could; the four
# location_types are pinned by test in tests/datasets/test_google_geocoder.py.
_ACCEPTED_LOCATION_TYPES_BY_KIND: dict[str, frozenset[str]] = {
    "civic_address": frozenset({"ROOFTOP", "RANGE_INTERPOLATED"}),
    "named_place": frozenset({"ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}),
    "intersection": frozenset({"ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}),
}


def accepted_location_types(kind: str) -> frozenset[str]:
    """The location_types a reference of ``kind`` may resolve through."""
    return _ACCEPTED_LOCATION_TYPES_BY_KIND.get(kind, frozenset({"ROOFTOP"}))


_DEFAULT_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"


@dataclass(frozen=True)
class GoogleGeocoderConfig:
    api_key: str
    region_bias: str = "ca"
    # ``components`` is a hard filter (unlike region_bias which is a soft
    # hint). Without this, a query like "6299 South Street" can resolve to
    # Perth, Australia even with region_bias="ca", because Google treats
    # the bias as a fallback rather than a constraint. country:CA forces
    # results to Canadian addresses; deployments with a known city can
    # narrow further (e.g. "country:CA|administrative_area:NS|locality:Halifax").
    components: str = "country:CA"
    timeout_s: float = 5.0
    endpoint: str = _DEFAULT_ENDPOINT


class HttpClient(Protocol):
    """Minimal synchronous HTTP interface — lets tests inject a fake without
    monkeypatching httpx globally."""

    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> Any: ...


class GoogleGeocoder:
    """Resolves civic_address LocationReferences to points via Google Geocoding API.

    Failure modes are handled by returning ``None``; we never raise from
    here so a network blip can't crash retrieval. Each failure also stamps
    ``last_failure_reason`` and ``last_failure_detail`` on the instance so
    callers can record the reason in audit/cache rows. Reasons follow Google's
    own status taxonomy (REQUEST_DENIED, OVER_QUERY_LIMIT, ZERO_RESULTS, ...)
    plus our own short codes for cases Google doesn't have status values for
    (NETWORK_ERROR, INVALID_JSON, LOW_CONFIDENCE, MISSING_GEOMETRY).
    """

    name = "google_maps"

    def __init__(
        self,
        config: GoogleGeocoderConfig,
        *,
        http_client: HttpClient | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx
        self.last_failure_reason: str | None = None
        self.last_failure_detail: str | None = None

    def _record_failure(self, reason: str, detail: str | None = None) -> None:
        self.last_failure_reason = reason
        self.last_failure_detail = detail

    def resolve(self, ref: LocationReference) -> ResolvedLocation | None:
        self.last_failure_reason = None
        self.last_failure_detail = None

        if ref.kind not in {"civic_address", "named_place", "intersection"}:
            self._record_failure("UNSUPPORTED_KIND", f"kind={ref.kind!r}")
            return None
        query = _query_string(ref)
        if not query:
            self._record_failure("EMPTY_QUERY")
            return None
        params: dict[str, Any] = {
            "address": query,
            "key": self._config.api_key,
            "region": self._config.region_bias,
        }
        if self._config.components:
            params["components"] = self._config.components
        try:
            response = self._http.get(
                self._config.endpoint,
                params=params,
                timeout=self._config.timeout_s,
            )
        except (httpx.HTTPError, OSError) as exc:
            self._record_failure("NETWORK_ERROR", str(exc))
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            self._record_failure("INVALID_JSON", str(exc))
            return None
        status = payload.get("status")
        if status != "OK":
            self._record_failure(
                status or "UNKNOWN_STATUS",
                payload.get("error_message"),
            )
            return None
        if not payload.get("results"):
            self._record_failure("ZERO_RESULTS")
            return None

        best = payload["results"][0]
        location_type = ((best.get("geometry") or {}).get("location_type") or "").upper()
        confidence = _CONFIDENCE_BY_TYPE.get(location_type, 0.5)
        accepted = accepted_location_types(ref.kind)
        if location_type not in accepted:
            self._record_failure(
                "LOW_CONFIDENCE",
                f"location_type={location_type!r} is not accepted for "
                f"kind={ref.kind!r} (accepted: {', '.join(sorted(accepted))})",
            )
            return None
        loc = (best.get("geometry") or {}).get("location") or {}
        if "lat" not in loc or "lng" not in loc:
            self._record_failure("MISSING_GEOMETRY")
            return None
        return ResolvedLocation(
            kind="point",
            geometry={"type": "Point", "coordinates": [float(loc["lng"]), float(loc["lat"])]},
            confidence=confidence,
            source=self.name,
            reference_text=ref.raw_text,
            # ABS-466: carry Google's own verdict forward. A bare float can't
            # distinguish "estimated along the street" from "matched the
            # building", and everything downstream — the cache row, the
            # AddressProfile, the answer-time qualifier — needs that word.
            location_type=location_type,
        )


def _query_string(ref: LocationReference) -> str:
    if ref.kind == "civic_address":
        # Real-world address format: "1234 Barrington Street, Unit 5".
        # Civic number and street are joined with a space (not a comma) —
        # using a comma here was causing Google to misroute to other-region
        # matches even with region_bias=ca. Unit is comma-separated since
        # it's a logically distinct address component.
        primary_parts = [p for p in [ref.civic_number, ref.street] if p]
        primary = " ".join(primary_parts)
        if ref.unit:
            primary = f"{primary}, Unit {ref.unit}" if primary else f"Unit {ref.unit}"
        return primary or ref.raw_text
    if ref.kind == "named_place":
        return ref.name or ref.raw_text
    if ref.kind == "intersection":
        if ref.streets:
            return " and ".join(ref.streets)
        return ref.raw_text
    return ref.raw_text
