from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from layer1.db.base import Document, GeocodeCache, SourceFragment
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2
from layer2.retrieval.geocode import normalize_reference, resolve_location
from layer2.retrieval.google_geocoder import GoogleGeocoder, GoogleGeocoderConfig
from layer2.retrieval.location import LocationReference


HEIGHT_CONFIG = """
name: mini_height_precincts_g
publisher: Test
format: geojson
source_path: tests/fixtures/geo/mini_height_precincts.geojson
crs: EPSG:4326
links_to:
  document_match:
    municipality: Halifax Regional Municipality
    bylaw_name: Regional Centre Land Use By-law
  fragment_citation: Schedule 15
attributes:
  feature_key: GlobalID
  canonical:
    max_height_m: { from: MAXBLDHGT, type: float, optional: true }
    max_height_storeys: { from: MAXBLDSTRY, type: int, optional: true }
  ignore: [OBJECTID, SACC]
"""


class _MockResponse:
    def __init__(self, payload: dict[str, Any] | None, *, raises: Exception | None = None):
        self._payload = payload
        self._raises = raises

    def json(self) -> dict[str, Any]:
        if self._raises is not None:
            raise self._raises
        return self._payload or {}


class _MockHttp:
    def __init__(self, response: _MockResponse | Exception):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, Any], timeout: float):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _civic(num: str = "1234", street: str = "Barrington Street") -> LocationReference:
    return LocationReference(
        raw_text=f"{num} {street}",
        kind="civic_address",
        civic_number=num,
        street=street,
    )


def _config(api_key: str = "test-key") -> GoogleGeocoderConfig:
    return GoogleGeocoderConfig(api_key=api_key, region_bias="ca", timeout_s=1.0)


def test_geocoder_returns_point_for_rooftop_match():
    payload = {
        "status": "OK",
        "results": [
            {
                "geometry": {
                    "location": {"lat": 44.6488, "lng": -63.5752},
                    "location_type": "ROOFTOP",
                }
            }
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)

    resolved = geocoder.resolve(_civic())
    assert resolved is not None
    assert resolved.kind == "point"
    assert resolved.geometry == {"type": "Point", "coordinates": [-63.5752, 44.6488]}
    assert resolved.confidence == 0.95
    assert resolved.source == "google_maps"
    # Civic number + street joined with a SPACE (not a comma) — using a
    # comma here was producing region-mismatched Google results even with
    # region_bias=ca (e.g. "6299, South Street" misrouting to Australia).
    assert http.calls[0]["params"]["address"] == "1234 Barrington Street"
    assert http.calls[0]["params"]["region"] == "ca"
    assert http.calls[0]["params"]["key"] == "test-key"


def test_geocoder_rejects_low_confidence_approximate_match():
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.0, "lng": -63.0}, "location_type": "APPROXIMATE"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None
    assert geocoder.last_failure_reason == "LOW_CONFIDENCE"


def test_geocoder_returns_none_on_zero_results():
    http = _MockHttp(_MockResponse({"status": "ZERO_RESULTS", "results": []}))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None
    assert geocoder.last_failure_reason == "ZERO_RESULTS"


def test_geocoder_surfaces_request_denied_reason():
    """REQUEST_DENIED is the symptom of an unconfigured Geocoding API on the
    user's Google Cloud project. The reason and the error_message must be
    visible to operators so they can act on it instead of silently
    debugging a 'no spatial match' that's actually an auth failure."""
    payload = {
        "status": "REQUEST_DENIED",
        "results": [],
        "error_message": "This API is not activated on your API project.",
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None
    assert geocoder.last_failure_reason == "REQUEST_DENIED"
    assert "not activated" in (geocoder.last_failure_detail or "")


def test_geocoder_surfaces_network_error():
    http = _MockHttp(httpx.ConnectError("network down"))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None
    assert geocoder.last_failure_reason == "NETWORK_ERROR"


def test_geocoder_surfaces_low_confidence():
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.0, "lng": -63.0}, "location_type": "APPROXIMATE"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None
    assert geocoder.last_failure_reason == "LOW_CONFIDENCE"


def test_geocoder_returns_none_on_network_error():
    http = _MockHttp(httpx.ConnectError("network down"))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None


def test_geocoder_returns_none_on_invalid_json():
    http = _MockHttp(_MockResponse(None, raises=ValueError("not json")))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    assert geocoder.resolve(_civic()) is None


def test_geocoder_sends_components_hard_filter():
    """Regression: region_bias is a soft hint and Google cheerfully falls
    back to non-Canadian matches without a hard filter (e.g. '6299 South
    Street' returning Perth, Australia). The components parameter is a
    hard constraint — country:CA forces a Canadian-only result set."""
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.6, "lng": -63.5}, "location_type": "ROOFTOP"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    geocoder.resolve(_civic())
    assert http.calls[0]["params"]["components"] == "country:CA"


def test_geocoder_omits_components_when_blank():
    """An empty components string is treated as 'no hard filter'. Lets
    deployments that need global coverage opt out."""
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.6, "lng": -63.5}, "location_type": "ROOFTOP"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(
        GoogleGeocoderConfig(api_key="x", components=""), http_client=http
    )
    geocoder.resolve(_civic())
    assert "components" not in http.calls[0]["params"]


def test_civic_address_query_format_uses_space_not_comma():
    """Regression: with a comma between civic_number and street, Google's
    Geocoding API ignored region_bias for some addresses (e.g. '6299, South
    Street' returned a Perth, Australia match instead of Halifax). The
    address query string must read like a real address."""
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.6, "lng": -63.5}, "location_type": "ROOFTOP"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    geocoder.resolve(_civic("6299", "South Street"))
    assert http.calls[0]["params"]["address"] == "6299 South Street"


def test_civic_address_with_unit_keeps_unit_comma_separated():
    """A unit component IS comma-separated since it's a logically distinct
    address component, just like 'Halifax' or 'NS' would be."""
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.6, "lng": -63.5}, "location_type": "ROOFTOP"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    ref = LocationReference(
        raw_text="1234 Barrington Street Unit 5",
        kind="civic_address",
        civic_number="1234",
        street="Barrington Street",
        unit="5",
    )
    geocoder.resolve(ref)
    assert http.calls[0]["params"]["address"] == "1234 Barrington Street, Unit 5"


def test_geocoder_handles_named_place():
    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.6471, "lng": -63.5800}, "location_type": "GEOMETRIC_CENTER"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    ref = LocationReference(
        raw_text="Halifax Citadel", kind="named_place", name="Halifax Citadel"
    )
    resolved = geocoder.resolve(ref)
    assert resolved is not None
    assert resolved.confidence == 0.6
    assert http.calls[0]["params"]["address"] == "Halifax Citadel"


def test_geocoder_skips_parcel_id_kind():
    """Parcel ids belong to the in-database resolver, not Google Maps."""
    http = _MockHttp(_MockResponse({"status": "OK", "results": []}))
    geocoder = GoogleGeocoder(_config(), http_client=http)
    ref = LocationReference(raw_text="PID 123", kind="parcel_id", parcel_id="123")
    assert geocoder.resolve(ref) is None
    assert http.calls == []  # no network call attempted


def test_settings_reads_google_maps_api_key_from_env(monkeypatch):
    """``GOOGLE_MAPS_API_KEY`` must be read from the env, not a file path —
    the previous file-path implementation silently disabled the geocoder
    when the calling process's cwd wasn't the repo root. This test locks
    in the env-only contract."""
    from layer2 import config as cfg

    cfg.get_settings.cache_clear()
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-env-key")
    settings = cfg.Layer2Settings()
    assert settings.google_maps_api_key == "test-env-key"
    cfg.get_settings.cache_clear()


def test_settings_google_maps_api_key_defaults_to_none(monkeypatch):
    """When ``GOOGLE_MAPS_API_KEY`` is unset, the field is None — not an
    empty string, not a path. ``_maybe_build_google_geocoder`` keys off
    truthiness, so None is the correct disabled-state sentinel."""
    from layer2 import config as cfg

    cfg.get_settings.cache_clear()
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    # Avoid pydantic-settings auto-loading a .env that has the key set
    # locally — we want to assert the unset behaviour deterministically.
    monkeypatch.setattr(
        cfg.Layer2Settings, "model_config", {**cfg.Layer2Settings.model_config, "env_file": None}
    )
    settings = cfg.Layer2Settings()
    assert settings.google_maps_api_key is None
    cfg.get_settings.cache_clear()


def test_resolve_location_uses_google_fallback_when_dataset_misses(tmp_path: Path):
    """End-to-end: the in-database civic-address resolver misses (no
    role=civic_address dataset), and the Google fallback resolves the
    address. The cache row records the resolver name."""
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    payload = {
        "status": "OK",
        "results": [
            {"geometry": {"location": {"lat": 44.65, "lng": -63.59}, "location_type": "ROOFTOP"}}
        ],
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)

    ref = _civic()
    with session_scope(db_url) as session:
        resolved = resolve_location(session, ref, google_geocoder=geocoder)

    assert resolved is not None
    assert resolved.source == "google_maps"
    assert resolved.geometry["coordinates"] == [-63.59, 44.65]

    with session_scope(db_url) as session:
        cached = session.query(GeocodeCache).filter_by(
            normalized_text=normalize_reference(ref)
        ).one()
        assert cached.status == "linked"
        assert cached.resolver == "google_maps"


def test_resolve_location_prefers_in_database_resolver_over_google(tmp_path: Path):
    """If a role=civic_address dataset has the address, that's the
    authoritative resolver — Google must not be called."""
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax Regional Municipality",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="/synthetic.pdf",
            file_hash="g" * 64,
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()
        SourceFragment(  # noqa: F841 - just need a target for the linker
            document_id=document.id,
            fragment_type=FragmentType.SCHEDULE,
            citation_label="Schedule 15",
            citation_path="schedules.schedule_15",
            page_start=500,
            page_end=502,
            text="Schedule 15.",
            parse_status=ParseStatus.PARSED,
            source_block_ids_json=[],
            metadata_json={},
        )
        # The civic-address dataset already has 1234 Barrington in the fixture.
        civic_yaml = tmp_path / "civic.yaml"
        civic_yaml.write_text(
            "name: mini_civic_addresses_g\n"
            "publisher: Test\n"
            "format: geojson\n"
            "source_path: tests/fixtures/geo/mini_civic_addresses.geojson\n"
            "crs: EPSG:4326\n"
            "role: civic_address\n"
            "attributes:\n"
            "  feature_key: ADDR_ID\n"
            "  canonical:\n"
            "    civic_number: { from: CIVIC_NUMBER, type: string }\n"
            "    street_name: { from: STREET_NAME, type: string }\n"
            "    parcel_id: { from: PID, type: string }\n"
        )

    with session_scope(db_url) as session:
        ingest_geo_dataset(session, civic_yaml)

    http = _MockHttp(_MockResponse({"status": "OK", "results": []}))
    geocoder = GoogleGeocoder(_config(), http_client=http)

    with session_scope(db_url) as session:
        resolved = resolve_location(session, _civic(), google_geocoder=geocoder)
    assert resolved is not None
    assert resolved.source == "mini_civic_addresses_g"
    assert http.calls == []  # in-database resolver took the lookup


def test_geocode_cache_records_request_denied_reason(tmp_path: Path):
    """End-to-end: a Google REQUEST_DENIED leaves a cache row whose
    ``detail`` and ``resolver`` columns make the failure mode visible to
    operators without requiring server logs."""
    db_url = f"sqlite:///{tmp_path / 'cache.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    payload = {
        "status": "REQUEST_DENIED",
        "results": [],
        "error_message": "This API is not activated on your API project.",
    }
    http = _MockHttp(_MockResponse(payload))
    geocoder = GoogleGeocoder(_config(), http_client=http)

    ref = _civic()
    with session_scope(db_url) as session:
        resolved = resolve_location(session, ref, google_geocoder=geocoder)

    assert resolved is None

    with session_scope(db_url) as session:
        cached = session.query(GeocodeCache).filter_by(
            normalized_text=normalize_reference(ref)
        ).one()
        assert cached.status == "no_match"
        assert "REQUEST_DENIED" in (cached.detail or "")
        assert "not activated" in (cached.detail or "")
        assert cached.resolver == "google_maps:request_denied"


def test_resolve_location_without_geocoder_still_refuses_gracefully(tmp_path: Path):
    """The fallback is genuinely optional — a deployment without a Google
    API key keeps the previous Phase E behaviour (return None).

    The autouse conftest fixture stubs ``_maybe_build_google_geocoder`` to
    return None, so this test exercises the no-fallback path even on a
    machine where ``google_maps_api_key`` is on disk.
    """
    db_url = f"sqlite:///{tmp_path / 'layer.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    with session_scope(db_url) as session:
        resolved = resolve_location(session, _civic())
    assert resolved is None
