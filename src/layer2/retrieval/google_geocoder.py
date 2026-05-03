from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from layer2.retrieval.location import LocationReference
from layer2.retrieval.spatial import ResolvedLocation


# Confidence is mapped from Google's location_type enum. ROOFTOP means the
# response is a precise rooftop match; RANGE_INTERPOLATED is interpolated
# along a street; GEOMETRIC_CENTER and APPROXIMATE are progressively coarser.
# We don't promote results below RANGE_INTERPOLATED because address-typed
# questions deserve precise matches, not "somewhere in this neighbourhood".
_CONFIDENCE_BY_TYPE = {
    "ROOFTOP": 0.95,
    "RANGE_INTERPOLATED": 0.85,
    "GEOMETRIC_CENTER": 0.6,
    "APPROXIMATE": 0.4,
}
_MIN_ACCEPTED_TYPE_CONFIDENCE = 0.6
_DEFAULT_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"


@dataclass(frozen=True)
class GoogleGeocoderConfig:
    api_key: str
    region_bias: str = "ca"
    timeout_s: float = 5.0
    endpoint: str = _DEFAULT_ENDPOINT


class HttpClient(Protocol):
    """Minimal synchronous HTTP interface — lets tests inject a fake without
    monkeypatching httpx globally."""

    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> Any: ...


def load_google_maps_api_key(path: str | Path) -> str | None:
    """Read the API key from disk. Returns None if the file is missing or
    empty so callers can detect the "no geocoder configured" case without
    raising. The contents are stripped — trailing newlines from text editors
    have caused real headaches with these tokens before.
    """
    p = Path(path)
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    return raw or None


class GoogleGeocoder:
    """Resolves civic_address LocationReferences to points via Google Geocoding API.

    Failure modes are all handled the same way: return ``None`` and let the
    layered ``resolve_location`` machinery log a miss in the cache. We never
    raise from here — a network blip shouldn't crash retrieval.
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

    def resolve(self, ref: LocationReference) -> ResolvedLocation | None:
        if ref.kind not in {"civic_address", "named_place", "intersection"}:
            return None
        query = _query_string(ref)
        if not query:
            return None
        try:
            response = self._http.get(
                self._config.endpoint,
                params={
                    "address": query,
                    "key": self._config.api_key,
                    "region": self._config.region_bias,
                },
                timeout=self._config.timeout_s,
            )
        except (httpx.HTTPError, OSError):
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if payload.get("status") != "OK" or not payload.get("results"):
            return None

        best = payload["results"][0]
        location_type = (best.get("geometry") or {}).get("location_type") or ""
        confidence = _CONFIDENCE_BY_TYPE.get(location_type, 0.5)
        if confidence < _MIN_ACCEPTED_TYPE_CONFIDENCE:
            return None
        loc = (best.get("geometry") or {}).get("location") or {}
        if "lat" not in loc or "lng" not in loc:
            return None
        return ResolvedLocation(
            kind="point",
            geometry={"type": "Point", "coordinates": [float(loc["lng"]), float(loc["lat"])]},
            confidence=confidence,
            source=self.name,
            reference_text=ref.raw_text,
        )


def _query_string(ref: LocationReference) -> str:
    if ref.kind == "civic_address":
        parts = [p for p in [ref.civic_number, ref.street, ref.unit] if p]
        return ", ".join(parts) if parts else ref.raw_text
    if ref.kind == "named_place":
        return ref.name or ref.raw_text
    if ref.kind == "intersection":
        if ref.streets:
            return " and ".join(ref.streets)
        return ref.raw_text
    return ref.raw_text
