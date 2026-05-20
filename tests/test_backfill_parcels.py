"""Coverage for the one-time parcel backfill script.

The full Halifax dataset is ~50k features and runs against postgres
post-merge; the test suite exercises the script's logic against a
hand-crafted three-feature fixture so we catch:

* idempotency — re-running picks up existing parcels and updates FKs
  instead of inserting duplicates.
* zone-code lookup — the parcel's centroid is intersected against
  the synthetic zoning polygon and the resulting zone_code lands on
  the parcel row.
* unmatched / bad-geometry features — the script logs them in stats
  without crashing the run.
* dry-run mode — computes stats but writes nothing.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from layer1.db.base import ExternalDataset, ExternalDatasetFeature, Parcel
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus

# scripts/ isn't a Python package (no __init__.py — keeps the existing
# one-off scripts runnable as plain files). Load the backfill module
# via importlib so the test can reach the functions without needing
# scripts/ on sys.path globally.
import importlib.util as _importlib_util
import sys as _sys

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill_parcels.py"
_spec = _importlib_util.spec_from_file_location("backfill_parcels", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_backfill_mod = _importlib_util.module_from_spec(_spec)
# Register the module before exec so @dataclass (which inspects
# sys.modules[cls.__module__]) doesn't blow up on AttributeError.
_sys.modules[_spec.name] = _backfill_mod
_spec.loader.exec_module(_backfill_mod)

HALIFAX_JURISDICTION = _backfill_mod.HALIFAX_JURISDICTION
HALIFAX_PARCELS_DATASET_NAME = _backfill_mod.HALIFAX_PARCELS_DATASET_NAME
HALIFAX_ZONING_DATASET_NAME = _backfill_mod.HALIFAX_ZONING_DATASET_NAME
backfill = _backfill_mod.backfill


def _make_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(db_url)
    return db_url


def _box(lon: float, lat: float, dx: float = 0.001, dy: float = 0.001) -> dict:
    """Tiny axis-aligned box around (lon, lat) in EPSG:4326."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - dx, lat - dy],
                [lon + dx, lat - dy],
                [lon + dx, lat + dy],
                [lon - dx, lat + dy],
                [lon - dx, lat - dy],
            ]
        ],
    }


def _seed(session) -> dict:
    """Two parcel features inside an ER-1 zone polygon + one with bad geometry.

    The ER-1 zoning polygon covers (-63.601, 44.649)..(-63.598, 44.652).
    Parcel A and B sit inside it; parcel C has a missing identifier.
    Feature D has malformed geometry so we exercise the bad-geometry
    branch.
    """
    parcels_dataset = ExternalDataset(
        name=HALIFAX_PARCELS_DATASET_NAME,
        publisher="HRM",
        format="geojson",
        content_hash="hash-parcels",
        crs="EPSG:4326",
        feature_count=4,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={"role": "property_parcels"},
    )
    zoning_dataset = ExternalDataset(
        name=HALIFAX_ZONING_DATASET_NAME,
        publisher="HRM",
        format="geojson",
        content_hash="hash-zoning",
        crs="EPSG:4326",
        feature_count=1,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add_all([parcels_dataset, zoning_dataset])
    session.flush()

    er1_polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [-63.601, 44.649],
                [-63.598, 44.649],
                [-63.598, 44.652],
                [-63.601, 44.652],
                [-63.601, 44.649],
            ]
        ],
    }
    zone_feature = ExternalDatasetFeature(
        external_dataset_id=zoning_dataset.id,
        feature_key="ZONE-ER1",
        attributes_json={"ZONE": "ER-1"},
        canonical_attributes_json={"zone_code": "ER-1"},
        geometry_geojson=er1_polygon,
        geometry_bbox_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )

    parcel_a = ExternalDatasetFeature(
        external_dataset_id=parcels_dataset.id,
        feature_key="PID-00099101",
        attributes_json={"PID": "00099101"},
        canonical_attributes_json={"parcel_id": "00099101"},
        geometry_geojson=_box(-63.6, 44.65),
        geometry_bbox_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    parcel_b = ExternalDatasetFeature(
        external_dataset_id=parcels_dataset.id,
        feature_key="PID-00099102",
        attributes_json={"PID": "00099102"},
        canonical_attributes_json={"parcel_id": "00099102"},
        geometry_geojson=_box(-63.5995, 44.6505),
        geometry_bbox_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    # Missing identifier: no PID anywhere, blank feature_key. Should
    # increment the missing_identifier counter.
    parcel_c = ExternalDatasetFeature(
        external_dataset_id=parcels_dataset.id,
        feature_key=" ",
        attributes_json={},
        canonical_attributes_json={},
        geometry_geojson=_box(-63.6005, 44.6495),
        geometry_bbox_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    # Bad geometry: empty dict — _safe_shape returns None.
    parcel_d = ExternalDatasetFeature(
        external_dataset_id=parcels_dataset.id,
        feature_key="PID-99999",
        attributes_json={"PID": "99999"},
        canonical_attributes_json={"parcel_id": "99999"},
        geometry_geojson={},
        geometry_bbox_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )

    session.add_all([zone_feature, parcel_a, parcel_b, parcel_c, parcel_d])
    session.flush()

    return {
        "parcels_dataset_id": parcels_dataset.id,
        "zoning_dataset_id": zoning_dataset.id,
        "feature_ids": {
            "A": parcel_a.id,
            "B": parcel_b.id,
            "C": parcel_c.id,
            "D": parcel_d.id,
        },
    }


def test_backfill_inserts_parcels_and_links_features(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        seed = _seed(session)

    with session_scope(db_url) as session:
        stats = backfill(session)

    assert stats.features_total == 4
    assert stats.parcels_inserted == 2
    assert stats.parcels_reused == 0
    assert stats.features_linked == 2
    assert stats.missing_identifier == 1
    assert stats.bad_geometry == 1
    assert stats.unmatched_features == 2
    assert stats.zone_lookups_successful == 2

    with session_scope(db_url) as session:
        parcels = session.execute(select(Parcel).order_by(Parcel.parcel_identifier)).scalars().all()
        assert [p.parcel_identifier for p in parcels] == ["00099101", "00099102"]
        assert {p.zone_code for p in parcels} == {"ER-1"}
        for parcel in parcels:
            assert parcel.jurisdiction == HALIFAX_JURISDICTION
            assert parcel.area_m2 is not None and float(parcel.area_m2) > 0
            assert parcel.centroid_geojson is not None
            assert parcel.centroid_geojson["type"] == "Point"
            assert parcel.metadata_json["source_dataset"] == HALIFAX_PARCELS_DATASET_NAME

        feature_a = session.get(ExternalDatasetFeature, seed["feature_ids"]["A"])
        feature_b = session.get(ExternalDatasetFeature, seed["feature_ids"]["B"])
        feature_c = session.get(ExternalDatasetFeature, seed["feature_ids"]["C"])
        feature_d = session.get(ExternalDatasetFeature, seed["feature_ids"]["D"])
        assert feature_a.parcel_id is not None
        assert feature_b.parcel_id is not None
        assert feature_a.parcel_id != feature_b.parcel_id
        # Unmatched features stay unlinked (NULL parcel_id).
        assert feature_c.parcel_id is None
        assert feature_d.parcel_id is None


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed(session)
    with session_scope(db_url) as session:
        backfill(session)

    # Second run reuses existing parcel rows, doesn't double-insert.
    with session_scope(db_url) as session:
        stats = backfill(session)
    assert stats.parcels_inserted == 0
    assert stats.parcels_reused == 2
    assert stats.features_linked == 2

    with session_scope(db_url) as session:
        count = len(session.execute(select(Parcel)).scalars().all())
        assert count == 2


def test_backfill_dry_run_writes_nothing(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        seed = _seed(session)

    with session_scope(db_url) as session:
        stats = backfill(session, dry_run=True)
    # Stats are computed
    assert stats.parcels_inserted == 2
    assert stats.features_linked == 2

    # But no rows persisted, no FKs set.
    with session_scope(db_url) as session:
        assert session.execute(select(Parcel)).scalars().first() is None
        for fid in seed["feature_ids"].values():
            feature = session.get(ExternalDatasetFeature, fid)
            assert feature.parcel_id is None


def test_backfill_raises_when_parcels_dataset_missing(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        # Only seed a zoning dataset — no parcels dataset present.
        zoning = ExternalDataset(
            name=HALIFAX_ZONING_DATASET_NAME,
            publisher="HRM",
            format="geojson",
            content_hash="hash-zoning",
            crs="EPSG:4326",
            feature_count=0,
            schema_mapping_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(zoning)

    import pytest

    with pytest.raises(RuntimeError, match="halifax_property_parcels"):
        with session_scope(db_url) as session:
            backfill(session)
