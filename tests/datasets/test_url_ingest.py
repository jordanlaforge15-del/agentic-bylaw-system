"""Phase K — URL-based dataset ingest with ArcGIS pagination."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from layer1.datasets.config import load_dataset_config
from layer1.db.base import ExternalDatasetFeature
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.pipeline.ingest_dataset import (
    _fetch_arcgis_paginated,
    _fetch_dataset_to_cache,
    _resolve_source_path,
    ingest_geo_dataset,
)


class _Response:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def _make_feature(idx: int, zone: str = "ER-3") -> dict[str, Any]:
    """A minimal valid GeoJSON Polygon feature with HRM-style attributes."""
    base_lon = -63.6 + idx * 0.001
    base_lat = 44.65
    return {
        "type": "Feature",
        "id": idx,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [base_lon, base_lat],
                [base_lon + 0.0005, base_lat],
                [base_lon + 0.0005, base_lat + 0.0005],
                [base_lon, base_lat + 0.0005],
                [base_lon, base_lat],
            ]],
        },
        "properties": {
            "OBJECTID": idx,
            "GLOBALID": f"00000000-0000-0000-0000-{idx:012d}",
            "ZONE": zone,
            "BYLAW_ID": 23,
            "FCODE": "CDZN",
            "DESCRIPTION": f"Zone {zone} polygon {idx}",
            "SOURCE": "HAF",
            "SACC": "IN",
            "SDATE": "Mon, 13 Apr 2026 00:00:00 GMT",
        },
    }


def test_fetch_arcgis_paginated_stops_when_page_short():
    """A response with fewer features than the page size means we're done."""
    calls: list[str] = []

    def http_get(url: str, *, timeout: float):
        calls.append(url)
        return _Response({
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [_make_feature(i) for i in range(3)],
        })

    payload = _fetch_arcgis_paginated(
        "https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query",
        page_size=10,
        http_get=http_get,
    )
    assert len(calls) == 1
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 3


def test_fetch_arcgis_paginated_walks_pages_until_short():
    """Two full pages then a partial page closes the loop."""
    calls: list[str] = []

    def http_get(url: str, *, timeout: float):
        calls.append(url)
        # Return full page on the first two calls, partial on the third.
        offset = int(_query_param(url, "resultOffset"))
        if offset == 0:
            features = [_make_feature(i) for i in range(2)]
        elif offset == 2:
            features = [_make_feature(i + 2) for i in range(2)]
        else:
            features = [_make_feature(99)]
        return _Response({
            "type": "FeatureCollection",
            "features": features,
        })

    payload = _fetch_arcgis_paginated(
        "https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query",
        page_size=2,
        http_get=http_get,
    )
    assert len(calls) == 3
    assert len(payload["features"]) == 5  # 2 + 2 + 1


def test_fetch_arcgis_paginated_preserves_existing_query_params():
    """An incoming URL with where=, outFields= etc. must keep those values."""
    captured: list[str] = []

    def http_get(url: str, *, timeout: float):
        captured.append(url)
        return _Response({"type": "FeatureCollection", "features": []})

    _fetch_arcgis_paginated(
        "https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query"
        "?where=ZONE%3D%27ER-3%27&outFields=ZONE%2CBYLAW_ID&outSR=4326&f=geojson",
        page_size=1000,
        http_get=http_get,
    )
    assert captured  # at least one call
    page_url = captured[0]
    assert "where=ZONE%3D%27ER-3%27" in page_url
    assert "outFields=ZONE%2CBYLAW_ID" in page_url
    assert "outSR=4326" in page_url


def test_fetch_dataset_to_cache_writes_arcgis_payload(tmp_path: Path):
    def http_get(url: str, *, timeout: float):
        return _Response({
            "type": "FeatureCollection",
            "features": [_make_feature(i) for i in range(2)],
        })

    target = _fetch_dataset_to_cache(
        "https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query",
        "test_zoning",
        tmp_path,
        http_get=http_get,
    )
    assert target.exists()
    assert target.parent == tmp_path
    assert target.name.startswith("test_zoning_") and target.name.endswith(".geojson")
    payload = json.loads(target.read_text())
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 2


def test_fetch_dataset_to_cache_handles_non_arcgis_url(tmp_path: Path):
    """Plain GeoJSON URLs (not ArcGIS query endpoints) are fetched once."""
    calls: list[str] = []

    def http_get(url: str, *, timeout: float):
        calls.append(url)
        return _Response({
            "type": "FeatureCollection",
            "features": [_make_feature(0)],
        })

    target = _fetch_dataset_to_cache(
        "https://example.invalid/static/file.geojson",
        "test_static",
        tmp_path,
        http_get=http_get,
    )
    assert len(calls) == 1
    payload = json.loads(target.read_text())
    assert len(payload["features"]) == 1


def test_resolve_source_path_prefers_path_when_set(tmp_path: Path):
    """When both source_path and source_url could be set, source_path wins
    because it's explicit. (The schema permits one or the other; this
    guards against a future change where both can coexist.)"""
    fixture = tmp_path / "f.geojson"
    fixture.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    cfg = load_dataset_config(_tiny_config(tmp_path, source_path=str(fixture)))
    resolved = _resolve_source_path(cfg, tmp_path)
    assert resolved == fixture


def test_ingest_geo_dataset_via_url_end_to_end(tmp_path: Path):
    """End-to-end: a config with source_url drives a fetch -> parse ->
    persist sequence. The resulting dataset row records the right name,
    feature count, and CRS."""
    db_url = f"sqlite:///{tmp_path / 'url.db'}"
    create_all(db_url)

    payload = {
        "type": "FeatureCollection",
        "features": [_make_feature(i, zone="ER-3" if i % 2 else "DD") for i in range(5)],
    }

    def http_get(url: str, *, timeout: float):
        return _Response(payload)

    cfg_path = tmp_path / "url_dataset.yaml"
    cfg_path.write_text(_zoning_yaml())

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(
            session,
            cfg_path,
            cache_dir=tmp_path / "cache",
            http_get=http_get,
        )
        assert result.dataset.feature_count == 5
        assert result.dataset.crs == "EPSG:4326"
        # No bylaw exists -> linker reports no_document, not a crash.
        assert result.link_result.status == "no_document"
        dataset_id = result.dataset.id

    with session_scope(db_url) as session:
        features = (
            session.query(ExternalDatasetFeature)
            .filter_by(external_dataset_id=dataset_id)
            .all()
        )
        zones = sorted({f.canonical_attributes_json.get("zone_code") for f in features})
        assert zones == ["DD", "ER-3"]
        # Each feature carries the bylaw_area_id and an effective_date.
        sample = features[0]
        assert sample.canonical_attributes_json.get("bylaw_area_id") == 23
        assert sample.canonical_attributes_json.get("effective_date") == "2026-04-13"


def test_ingest_resolves_bylaw_area_lookup_into_canonical_attributes(tmp_path: Path):
    """ABS-66 regression: features whose raw BYLAW_ID matches a row in the
    YAML's ``lookups.bylaw_area_subtypes`` table get the resolved
    ``bylaw_area_code`` and ``bylaw_area_name`` baked into
    ``canonical_attributes_json``. The chat agent reads those instead of
    guessing a bylaw name from the bare integer.
    """
    db_url = f"sqlite:///{tmp_path / 'lookup.db'}"
    create_all(db_url)

    payload = {
        "type": "FeatureCollection",
        "features": [
            # idx 0 → BYLAW_ID=9 (Halifax Mainland — the ABS-66 anchor)
            _override_bylaw_id(_make_feature(0, zone="R-1"), 9),
            # idx 1 → BYLAW_ID=23 (Regional Centre — RCLUB)
            _override_bylaw_id(_make_feature(1, zone="ER-3"), 23),
            # idx 2 → BYLAW_ID=99 (unknown subtype — should be omitted, not crash)
            _override_bylaw_id(_make_feature(2, zone="ER-3"), 99),
        ],
    }

    def http_get(url: str, *, timeout: float):
        return _Response(payload)

    cfg_path = tmp_path / "lookup_dataset.yaml"
    cfg_path.write_text(_zoning_yaml_with_lookups())

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(
            session,
            cfg_path,
            cache_dir=tmp_path / "cache",
            http_get=http_get,
        )
        dataset_id = result.dataset.id

    with session_scope(db_url) as session:
        features = (
            session.query(ExternalDatasetFeature)
            .filter_by(external_dataset_id=dataset_id)
            .order_by(ExternalDatasetFeature.feature_key)
            .all()
        )
        by_bylaw = {
            f.canonical_attributes_json.get("bylaw_area_id"): f.canonical_attributes_json
            for f in features
        }
        assert by_bylaw[9]["bylaw_area_code"] == "hrm:HMAIN"
        assert by_bylaw[9]["bylaw_area_name"] == "Halifax Mainland Land Use By-law"
        assert by_bylaw[23]["bylaw_area_code"] == "hrm:RC"
        assert by_bylaw[23]["bylaw_area_name"] == "Regional Centre Land Use By-law"
        # Unknown subtype keeps bylaw_area_id but omits code/name (the
        # mapping is optional). Crucially the feature still ingests so a
        # one-off upstream subtype doesn't break the whole pipeline.
        assert "bylaw_area_code" not in by_bylaw[99]
        assert "bylaw_area_name" not in by_bylaw[99]


def test_fetch_retries_on_transient_remote_protocol_error(tmp_path: Path):
    """A RemoteProtocolError on the first attempt should retry and succeed
    on the second. ArcGIS endpoints drop heavy queries mid-payload often
    enough that this is worth verifying."""
    import httpx

    attempts: list[int] = []

    def http_get(url: str, *, timeout: float):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return _Response({
            "type": "FeatureCollection",
            "features": [_make_feature(0)],
        })

    target = _fetch_dataset_to_cache(
        "https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query",
        "test_retry",
        tmp_path,
        http_get=http_get,
    )
    assert len(attempts) == 2
    assert target.exists()


def test_fetch_gives_up_after_exhausting_retries(tmp_path: Path):
    import httpx

    def http_get(url: str, *, timeout: float):
        raise httpx.ConnectError("network down")

    with pytest.raises(httpx.ConnectError):
        _fetch_dataset_to_cache(
            "https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query",
            "test_giveup",
            tmp_path,
            http_get=http_get,
        )


def test_real_halifax_zoning_yaml_loads():
    cfg = load_dataset_config(Path("src/layer1/datasets/halifax_zoning.yaml"))
    assert cfg.name == "halifax_zoning_boundaries"
    assert cfg.source_url and "/arcgis/rest/services/" in cfg.source_url
    assert cfg.source_path is None
    assert cfg.attributes.feature_key == "GLOBALID"
    canonical = cfg.attributes.canonical
    assert "zone_code" in canonical
    assert canonical["zone_code"].from_field == "ZONE"
    assert "bylaw_area_id" in canonical
    # ABS-66: the YAML now resolves BYLAW_ID to a publisher-prefixed code
    # + human name so the agent doesn't hallucinate a bylaw name from
    # the bare integer. The lookup table is co-located with the dataset.
    assert "bylaw_area_code" in canonical
    assert canonical["bylaw_area_code"].lookup == "bylaw_area_subtypes"
    assert canonical["bylaw_area_code"].lookup_field == "code"
    assert "bylaw_area_name" in canonical
    assert canonical["bylaw_area_name"].lookup == "bylaw_area_subtypes"
    assert canonical["bylaw_area_name"].lookup_field == "name"
    table = cfg.lookups["bylaw_area_subtypes"]
    # The two ABS-66 anchors plus the RCLUB code we'll filter on downstream.
    assert table[9] == {
        "code": "hrm:HMAIN",
        "name": "Halifax Mainland Land Use By-law",
    }
    assert table[10] == {
        "code": "hrm:HPEN",
        "name": "Halifax Peninsula Land Use By-law",
    }
    assert table[23] == {
        "code": "hrm:RC",
        "name": "Regional Centre Land Use By-law",
    }


@pytest.mark.parametrize(
    "yaml_name,expected_dataset_name,expected_canonical",
    [
        (
            "halifax_far_precincts.yaml",
            "halifax_far_precincts",
            {"max_far"},
        ),
        (
            "halifax_heritage_districts.yaml",
            "halifax_heritage_districts",
            {"district_name", "district_status"},
        ),
        (
            "halifax_bonus_zoning_districts.yaml",
            "halifax_bonus_zoning_districts",
            {"district_code", "district_name"},
        ),
        (
            "halifax_shadow_impact_areas.yaml",
            "halifax_shadow_impact_areas",
            {"impact_area"},
        ),
    ],
)
def test_polygon_schedule_yamls_load(
    yaml_name: str, expected_dataset_name: str, expected_canonical: set[str]
):
    """Each polygon-based RCLUB schedule YAML must load cleanly and declare
    the canonical fields its retrieval consumers depend on."""
    cfg = load_dataset_config(Path("src/layer1/datasets") / yaml_name)
    assert cfg.name == expected_dataset_name
    assert cfg.source_url and "/arcgis/rest/services/" in cfg.source_url
    assert cfg.source_path is None
    assert cfg.attributes.feature_key == "GLOBALID"
    assert expected_canonical <= set(cfg.attributes.canonical)


def _query_param(url: str, name: str) -> str:
    """Pick a single query-string parameter out of a URL for assertions."""
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(url).query)[name][0]


def _zoning_yaml() -> str:
    return (
        "name: test_url_zoning\n"
        "publisher: Test\n"
        "format: geojson\n"
        "source_url: https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match: { municipality: HRM, bylaw_name: Test }\n"
        "  fragment_citation: Zoning Schedule\n"
        "attributes:\n"
        "  feature_key: GLOBALID\n"
        "  canonical:\n"
        "    zone_code: { from: ZONE, type: string }\n"
        "    bylaw_area_id: { from: BYLAW_ID, type: int, optional: true }\n"
        "    effective_date: { from: SDATE, type: rfc2822_date, optional: true }\n"
    )


def _zoning_yaml_with_lookups() -> str:
    """A YAML config that exercises the per-dataset lookup table mechanism.

    Two canonical fields (``bylaw_area_code`` and ``bylaw_area_name``)
    resolve through the same ``bylaw_area_subtypes`` lookup so a feature's
    BYLAW_ID integer drives a publisher-prefixed code and a human-readable
    name without the chat agent having to guess.
    """
    return (
        "name: test_url_zoning_lookup\n"
        "publisher: Test\n"
        "format: geojson\n"
        "source_url: https://example.invalid/arcgis/rest/services/X/FeatureServer/0/query\n"
        "crs: EPSG:4326\n"
        "links_to:\n"
        "  document_match: { municipality: HRM, bylaw_name: Test }\n"
        "  fragment_citation: Zoning Schedule\n"
        "attributes:\n"
        "  feature_key: GLOBALID\n"
        "  canonical:\n"
        "    zone_code: { from: ZONE, type: string }\n"
        "    bylaw_area_id: { from: BYLAW_ID, type: int, optional: true }\n"
        "    bylaw_area_code:\n"
        "      from: BYLAW_ID\n"
        "      type: string\n"
        "      optional: true\n"
        "      lookup: bylaw_area_subtypes\n"
        "      lookup_field: code\n"
        "    bylaw_area_name:\n"
        "      from: BYLAW_ID\n"
        "      type: string\n"
        "      optional: true\n"
        "      lookup: bylaw_area_subtypes\n"
        "      lookup_field: name\n"
        "lookups:\n"
        "  bylaw_area_subtypes:\n"
        "    9:  { code: \"hrm:HMAIN\", name: \"Halifax Mainland Land Use By-law\" }\n"
        "    23: { code: \"hrm:RC\",    name: \"Regional Centre Land Use By-law\" }\n"
    )


def _override_bylaw_id(feature: dict[str, Any], bylaw_id: int) -> dict[str, Any]:
    feature["properties"] = {**feature["properties"], "BYLAW_ID": bylaw_id}
    return feature


def _tiny_config(tmp_path: Path, *, source_path: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        f"name: t\n"
        f"publisher: Test\n"
        f"format: geojson\n"
        f"source_path: {source_path}\n"
        f"crs: EPSG:4326\n"
        f"links_to:\n"
        f"  document_match: {{ municipality: x, bylaw_name: y }}\n"
        f"  fragment_citation: z\n"
        f"attributes:\n"
        f"  feature_key: GLOBALID\n"
        f"  canonical:\n"
        f"    zone_code: {{ from: ZONE, type: string }}\n"
    )
    return p
