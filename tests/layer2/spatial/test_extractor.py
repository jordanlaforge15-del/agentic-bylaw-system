"""Integration tests for ``layer2.spatial.extractor``.

These exercise the orchestrator end-to-end against a SQLite DB seeded
with a small parcels dataset. The geocoder is short-circuited via a
pre-populated ``GeocodeCache`` row so the test doesn't depend on a
Google Maps key or an in-DB civic-address dataset.
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


def rect(*, width_m: float, height_m: float, centre_lon: float = _HALIFAX_LON, centre_lat: float = _HALIFAX_LAT) -> dict:
    dlon = (width_m / _m_per_deg_lon(centre_lat)) / 2
    dlat = (height_m / _M_PER_DEG_LAT) / 2
    coords = [
        [centre_lon - dlon, centre_lat - dlat],
        [centre_lon + dlon, centre_lat - dlat],
        [centre_lon + dlon, centre_lat + dlat],
        [centre_lon - dlon, centre_lat + dlat],
        [centre_lon - dlon, centre_lat - dlat],
    ]
    return {"type": "Polygon", "coordinates": [coords]}


def offset_rect(*, width_m: float, height_m: float, offset_east_m: float = 0.0, offset_north_m: float = 0.0) -> dict:
    new_lon = _HALIFAX_LON + offset_east_m / _m_per_deg_lon(_HALIFAX_LAT)
    new_lat = _HALIFAX_LAT + offset_north_m / _M_PER_DEG_LAT
    return rect(width_m=width_m, height_m=height_m, centre_lon=new_lon, centre_lat=new_lat)


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


@pytest.fixture()
def parcels_db(tmp_path: Path):
    """A SQLite DB with three adjacent test parcels ingested.

    Parcel layout (centred at Halifax):
        anchor (PID=A001): 20×20 m square at the origin
        north  (PID=N001): 20×20 m square immediately north, sharing top edge
        east   (PID=E001): 20×20 m square immediately east, sharing right edge
    Anchor's south and west edges are road-facing.
    """
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_layer1(db_url)
    create_layer2(db_url)

    features = []
    for pid, geom in [
        ("A001", rect(width_m=20.0, height_m=20.0)),
        (
            "N001",
            offset_rect(width_m=20.0, height_m=20.0, offset_north_m=20.0),
        ),
        (
            "E001",
            offset_rect(width_m=20.0, height_m=20.0, offset_east_m=20.0),
        ),
    ]:
        features.append(
            {
                "type": "Feature",
                "properties": {"PID": pid},
                "geometry": geom,
            }
        )
    fc = {"type": "FeatureCollection", "features": features}
    fixture_path = tmp_path / "parcels.geojson"
    fixture_path.write_text(json.dumps(fc))

    cfg_path = tmp_path / "parcels.yaml"
    cfg_path.write_text(_PARCELS_YAML.format(fixture=str(fixture_path)))

    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, cfg_path)
        assert result.dataset.feature_count == 3

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


def test_happy_path_returns_full_facts(parcels_db) -> None:
    # Point the geocode cache at the anchor parcel's centroid so
    # ``_find_containing_parcel`` hits A001.
    _prime_geocode_cache_for_address(
        parcels_db["db_url"],
        normalized_text="civic:100 main st",
        raw_text="100 Main Street",
        kind="civic_address",
        lon=_HALIFAX_LON,
        lat=_HALIFAX_LAT,
    )
    with session_scope(parcels_db["db_url"]) as session:
        facts = extract_lot_facts(
            session,
            anchor_label="100 Main Street",
            anchor_kind="address",
        )

    assert facts["status"] == "ok"
    assert facts["pid"] == "A001"
    assert facts["method"] == "shared_edge"
    assert facts["area_m2"] == pytest.approx(400.0, rel=1e-3)
    # Two sides shared (north+east), two sides frontage (south+west).
    # Corner-touch buffer eats ~1 m near the (north+east) corner.
    assert facts["frontage_m"] == pytest.approx(39.0, abs=1.5)
    assert facts["corner"] is True
    # No civic-address dataset loaded → multi_unit omitted, not False.
    assert "multi_unit" not in facts
    assert facts["anchor_source"] == "test_fixture"
    assert facts["confidence"] > 0.9
    assert "computed_at" in facts


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
        "method": "shared_edge",
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
    facts = {"status": "ok", "area_m2": 100.0, "method": "shared_edge"}
    block = format_lot_facts_block(facts)
    assert "status=ok" in block
    assert "area_m2=100.0" in block
    assert "pid" not in block
    assert "corner" not in block
