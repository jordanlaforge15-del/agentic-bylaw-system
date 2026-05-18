"""Integration tests for ``layer2.spatial.extractor``.

These exercise the orchestrator end-to-end against a SQLite DB seeded
with a small parcels dataset plus a tiny road-centerlines dataset.
The geocoder is short-circuited via a pre-populated ``GeocodeCache``
row so the test doesn't depend on a Google Maps key or an in-DB
civic-address dataset.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import GeocodeCache
from layer1.db.init_db import create_all as create_layer1
from layer1.db.session import session_scope
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer2.db.init_db import create_all as create_layer2
from layer2.spatial.extractor import extract_lot_facts, format_lot_facts_block


_HALIFAX_LON = -63.6
_HALIFAX_LAT = 44.65
_M_PER_DEG_LAT = 111_320.0


def _m_per_deg_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


def _to_lonlat(x_m: float, y_m: float) -> tuple[float, float]:
    return (
        _HALIFAX_LON + x_m / _m_per_deg_lon(_HALIFAX_LAT),
        _HALIFAX_LAT + y_m / _M_PER_DEG_LAT,
    )


def rect_at(*, x_m: float, y_m: float, width_m: float, height_m: float) -> dict:
    p1 = _to_lonlat(x_m, y_m)
    p2 = _to_lonlat(x_m + width_m, y_m)
    p3 = _to_lonlat(x_m + width_m, y_m + height_m)
    p4 = _to_lonlat(x_m, y_m + height_m)
    return {"type": "Polygon", "coordinates": [[p1, p2, p3, p4, p1]]}


def line_between(a: tuple[float, float], b: tuple[float, float]) -> dict:
    return {
        "type": "LineString",
        "coordinates": [_to_lonlat(*a), _to_lonlat(*b)],
    }


_PARCELS_YAML = """
name: test_property_parcels
publisher: Test
format: geojson
source_path: {fixture}
crs: EPSG:4326
role: property_parcels
attributes:
  feature_key: PID
  canonical:
    parcel_id:
      from: PID
      type: string
  ignore:
    - OBJECTID
"""


_CENTERLINES_YAML = """
name: test_street_centerlines
publisher: Test
format: geojson
source_path: {fixture}
crs: EPSG:4326
role: road_centerlines
attributes:
  feature_key: ASSETID
  ignore:
    - OBJECTID
"""


@pytest.fixture()
def parcels_db(tmp_path: Path):
    """A SQLite DB with three test parcels and one road centerline ingested.

    Parcel layout (HRM tessellation pattern — front edge on centerline):
        anchor (PID=A001): 15 × 30 m parcel at the origin, south edge
            on the centerline (mid-block residential).
        west   (PID=W001): 15 × 30 m parcel immediately west of A001.
        east   (PID=E001): 15 × 30 m parcel immediately east of A001.
    Centerline runs east-west along y=0 — the south edge of every parcel.
    """
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    parcel_features = []
    for pid, geom in [
        ("A001", rect_at(x_m=0.0, y_m=0.0, width_m=15.0, height_m=30.0)),
        ("W001", rect_at(x_m=-15.0, y_m=0.0, width_m=15.0, height_m=30.0)),
        ("E001", rect_at(x_m=15.0, y_m=0.0, width_m=15.0, height_m=30.0)),
    ]:
        parcel_features.append(
            {
                "type": "Feature",
                "properties": {"PID": pid},
                "geometry": geom,
            }
        )
    parcel_fc = {"type": "FeatureCollection", "features": parcel_features}
    parcel_fixture = tmp_path / "parcels.geojson"
    parcel_fixture.write_text(json.dumps(parcel_fc))

    centerline_features = [
        {
            "type": "Feature",
            "properties": {"ASSETID": "MAIN-001"},
            "geometry": line_between((-50.0, 0.0), (50.0, 0.0)),
        }
    ]
    centerline_fc = {"type": "FeatureCollection", "features": centerline_features}
    centerline_fixture = tmp_path / "centerlines.geojson"
    centerline_fixture.write_text(json.dumps(centerline_fc))

    parcel_cfg = tmp_path / "parcels.yaml"
    parcel_cfg.write_text(_PARCELS_YAML.format(fixture=str(parcel_fixture)))
    centerline_cfg = tmp_path / "centerlines.yaml"
    centerline_cfg.write_text(
        _CENTERLINES_YAML.format(fixture=str(centerline_fixture))
    )

    with session_scope(db_url) as session:
        parcels_result = ingest_geo_dataset(session, parcel_cfg)
        assert parcels_result.dataset.feature_count == 3
        centerlines_result = ingest_geo_dataset(session, centerline_cfg)
        assert centerlines_result.dataset.feature_count == 1

    return {"db_url": db_url}


@pytest.fixture()
def parcels_db_no_centerlines(tmp_path: Path):
    """Same parcels as ``parcels_db``, but no centerlines dataset ingested.

    Exercises the area-only fallback path: extractor still returns area
    + perimeter, but frontage / depth / corner are absent and confidence
    drops to 0.7.
    """
    db_url = f"sqlite:///{tmp_path / 'test_no_cl.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    parcel_features = [
        {
            "type": "Feature",
            "properties": {"PID": "A001"},
            "geometry": rect_at(x_m=0.0, y_m=0.0, width_m=15.0, height_m=30.0),
        }
    ]
    parcel_fc = {"type": "FeatureCollection", "features": parcel_features}
    parcel_fixture = tmp_path / "parcels.geojson"
    parcel_fixture.write_text(json.dumps(parcel_fc))

    parcel_cfg = tmp_path / "parcels.yaml"
    parcel_cfg.write_text(_PARCELS_YAML.format(fixture=str(parcel_fixture)))

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, parcel_cfg)
        assert result.dataset.feature_count == 1

    return {"db_url": db_url}


def _prime_geocode_cache_for_address(
    db_url: str,
    *,
    normalized_text: str,
    raw_text: str,
    kind: str,
    lon: float,
    lat: float,
) -> None:
    """Insert a 'linked' GeocodeCache row so ``resolve_location`` hits cache."""
    with session_scope(db_url) as session:
        session.add(
            GeocodeCache(
                normalized_text=normalized_text,
                raw_text=raw_text,
                kind=kind,
                status="linked",
                resolver="test_fixture",
                geometry_geojson={"type": "Point", "coordinates": [lon, lat]},
                confidence=0.95,
                detail=None,
                metadata_json={},
                created_at=datetime.now(timezone.utc),
            )
        )


def test_non_address_anchor_returns_unresolved(parcels_db) -> None:
    with session_scope(parcels_db["db_url"]) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="DA-2024-12345",
            anchor_kind="development_application",
        )
    assert facts["status"] == "unresolved"
    assert "anchor_kind" in (facts.get("reason") or "")


def test_unparseable_address_returns_unresolved(parcels_db) -> None:
    with session_scope(parcels_db["db_url"]) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="not a real address",
            anchor_kind="address",
        )
    assert facts["status"] == "unresolved"


def test_no_parcels_dataset_returns_unresolved(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    create_layer1(db_url)
    create_layer2(db_url)
    # Pre-cache the geocode so we get past the geocoder and fail on
    # "no property_parcels dataset" specifically.
    _prime_geocode_cache_for_address(
        db_url,
        normalized_text="civic:100 main st",
        raw_text="100 Main Street",
        kind="civic_address",
        lon=_HALIFAX_LON,
        lat=_HALIFAX_LAT,
    )
    with session_scope(db_url) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="100 Main Street",
            anchor_kind="address",
        )
    assert facts["status"] == "unresolved"
    assert "parcels" in (facts.get("reason") or "")


def test_happy_path_returns_full_lot_facts(parcels_db) -> None:
    # Point the geocode cache at a spot inside the anchor parcel A001
    # (15 × 30 m, x in [0,15], y in [0,30] in local metres). Centroid
    # of A001 in metres is (7.5, 15), which converts to (lon_offset, lat_offset).
    anchor_lon, anchor_lat = _to_lonlat(7.5, 15.0)
    _prime_geocode_cache_for_address(
        parcels_db["db_url"],
        normalized_text="civic:100 main st",
        raw_text="100 Main Street",
        kind="civic_address",
        lon=anchor_lon,
        lat=anchor_lat,
    )
    with session_scope(parcels_db["db_url"]) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="100 Main Street",
            anchor_kind="address",
        )

    assert facts["status"] == "ok"
    assert facts["pid"] == "A001"
    assert facts["method"] == "centerline_buffer"
    assert facts["area_m2"] == pytest.approx(450.0, rel=1e-3)
    assert facts["perimeter_m"] == pytest.approx(90.0, abs=0.5)
    # South edge sits on the centerline (worst case for the perpendicular-
    # edge artifact): 15 m south edge + 2 × 8 m artifact bits from the
    # side edges entering the buffer ≈ 31 m. See lot_metrics.compute_lot_metrics
    # docstring for the algorithm details.
    assert facts["frontage_m"] == pytest.approx(31.0, abs=0.5)
    assert facts["depth_m"] == pytest.approx(14.5, abs=0.5)
    assert facts["corner"] is False
    # Frontage is well above 5% of perimeter → confidence stays at 1.0.
    assert facts["confidence"] == pytest.approx(1.0)
    # No civic-address dataset loaded → multi_unit omitted, not False.
    assert "multi_unit" not in facts
    assert facts["anchor_source"] == "test_fixture"
    assert "computed_at" in facts


def test_happy_path_without_centerlines_drops_confidence(
    parcels_db_no_centerlines,
) -> None:
    """Area-only fallback when centerlines aren't ingested.

    The extractor still returns area + perimeter (so the area-only
    questions Part 1 unlocked still work), but frontage / depth / corner
    are absent and confidence is 0.7 to flag the missing data.
    """
    anchor_lon, anchor_lat = _to_lonlat(7.5, 15.0)
    _prime_geocode_cache_for_address(
        parcels_db_no_centerlines["db_url"],
        normalized_text="civic:100 main st",
        raw_text="100 Main Street",
        kind="civic_address",
        lon=anchor_lon,
        lat=anchor_lat,
    )
    with session_scope(parcels_db_no_centerlines["db_url"]) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="100 Main Street",
            anchor_kind="address",
        )

    assert facts["status"] == "ok"
    assert facts["method"] == "centerline_buffer"
    assert facts["area_m2"] == pytest.approx(450.0, rel=1e-3)
    assert "frontage_m" not in facts
    assert "depth_m" not in facts
    assert "corner" not in facts
    # Frontage absent / 0 → confidence drops to 0.7.
    assert facts["confidence"] == pytest.approx(0.7)


def test_geocoded_point_outside_any_parcel_returns_unresolved(parcels_db) -> None:
    # Point ~1 km east of the parcels cluster.
    far_lon = _HALIFAX_LON + 1000.0 / _m_per_deg_lon(_HALIFAX_LAT)
    _prime_geocode_cache_for_address(
        parcels_db["db_url"],
        normalized_text="civic:999 nowhere st",
        raw_text="999 Nowhere Street",
        kind="civic_address",
        lon=far_lon,
        lat=_HALIFAX_LAT,
    )
    with session_scope(parcels_db["db_url"]) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="999 Nowhere Street",
            anchor_kind="address",
        )
    assert facts["status"] == "unresolved"
    assert "parcel" in (facts.get("reason") or "")


# ---------------------------------------------------------------------------
# format_lot_facts_block
# ---------------------------------------------------------------------------


def test_format_block_ok_includes_all_present_fields() -> None:
    facts = {
        "status": "ok",
        "pid": "00012345",
        "area_m2": 612.4,
        "frontage_m": 18.3,
        "depth_m": 33.4,
        "corner": False,
        "multi_unit": False,
        "confidence": 0.92,
        "method": "centerline_buffer",
    }
    block = format_lot_facts_block(facts)
    assert block.startswith("<lot_facts>")
    assert block.endswith("</lot_facts>")
    assert "status=ok" in block
    assert "pid=00012345" in block
    assert "area_m2=612.4" in block
    assert "corner=false" in block
    assert "multi_unit=false" in block


def test_format_block_unresolved_carries_reason() -> None:
    facts = {"status": "unresolved", "reason": "geocoder miss"}
    block = format_lot_facts_block(facts)
    assert "status=unresolved" in block
    assert "reason=geocoder miss" in block


def test_format_block_none_or_empty_returns_empty_string() -> None:
    assert format_lot_facts_block(None) == ""
    assert format_lot_facts_block({}) == ""


def test_format_block_omits_missing_optional_fields() -> None:
    # Minimal "ok" payload — no pid, no corner.
    facts = {"status": "ok", "area_m2": 100.0, "method": "centerline_buffer"}
    block = format_lot_facts_block(facts)
    assert "status=ok" in block
    assert "area_m2=100.0" in block
    assert "pid" not in block
    assert "corner" not in block
