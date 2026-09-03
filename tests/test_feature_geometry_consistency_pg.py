"""ABS-491 — ``geometry`` really does equal ``geometry_geojson``, on PostGIS.

Postgres-only: sqlite has no geometry column to compare against, so on that
dialect the audit reports ``checked=False`` and these assertions would be
vacuous (the sqlite half of the contract is pinned in
``tests/test_feature_geometry_writer.py``). Gated on a Postgres
``DATABASE_URL``; skipped otherwise. Run with the dev/e2e Postgres stack up.

Every row this creates is scoped to a throwaway dataset and deleted in a
``finally``, so it can run against the shared e2e database without leaving
fixtures behind for the contamination sweep to find.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from layer1.config import get_settings
from layer1.db.base import ExternalDataset, ExternalDatasetFeature
from layer1.db.geometry import audit_feature_geometry, sync_feature_geometry
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus

_DB_URL = get_settings().database_url
pg_only = pytest.mark.skipif(
    not _DB_URL.startswith("postgresql"),
    reason=(
        "the geometry column is PostGIS-only; run with the dev/e2e Postgres "
        "stack up (sqlite has nothing to compare)."
    ),
)

_BOX = [
    [-63.60, 44.64],
    [-63.58, 44.64],
    [-63.58, 44.66],
    [-63.60, 44.66],
    [-63.60, 44.64],
]
# A different shape, used to fake the drift a second writer would cause.
_OTHER_BOX = [
    [-63.50, 44.54],
    [-63.48, 44.54],
    [-63.48, 44.56],
    [-63.50, 44.56],
    [-63.50, 44.54],
]


def _seed_dataset(session, suffix: str) -> int:
    dataset = ExternalDataset(
        name=f"abs491-geometry-{suffix}",
        publisher="ABS-491 test",
        format="geojson",
        content_hash=f"abs491-{suffix}",
        crs="EPSG:4326",
        feature_count=2,
        schema_mapping_json={},
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(dataset)
    session.flush()
    session.add(
        ExternalDatasetFeature(
            external_dataset_id=dataset.id,
            feature_key=f"{suffix}-polygon",
            attributes_json={},
            canonical_attributes_json={},
            geometry_geojson={"type": "Polygon", "coordinates": [_BOX]},
            geometry_bbox_json={
                "minx": -63.60,
                "miny": 44.64,
                "maxx": -63.58,
                "maxy": 44.66,
            },
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
    )
    # A geometry-less feature: ``geometry_geojson`` defaults to ``{}``, which
    # has no GeoJSON ``type``. The writer must skip it rather than hand
    # ST_GeomFromGeoJSON an empty object (which raises), and the audit must
    # call it ok rather than "missing".
    session.add(
        ExternalDatasetFeature(
            external_dataset_id=dataset.id,
            feature_key=f"{suffix}-no-geometry",
            attributes_json={},
            canonical_attributes_json={},
            geometry_bbox_json={},
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
    )
    session.flush()
    return dataset.id


def _drop_dataset(session, dataset_id: int) -> None:
    session.execute(
        text("DELETE FROM external_dataset WHERE id = :id"), {"id": dataset_id}
    )


@pg_only
def test_writer_populates_geometry_and_audit_agrees() -> None:
    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as session:
        dataset_id = _seed_dataset(session, suffix)
        try:
            # Freshly inserted rows have no geometry yet — the audit sees
            # exactly the drift a forgotten write site would leave behind.
            before = audit_feature_geometry(session, dataset_id=dataset_id)
            assert before.checked is True
            assert before.features_total == 2
            assert before.missing_geometry == 1
            assert before.ok is False
            assert [s.status for s in before.sample] == ["missing_geometry"]

            assert sync_feature_geometry(session, dataset_id=dataset_id) == 1

            after = audit_feature_geometry(session, dataset_id=dataset_id)
            assert after.ok is True
            assert after.missing_geometry == 0
            assert after.geometry_mismatch == 0
            assert after.srid_mismatch == 0
            assert after.features_with_geometry == 1
            assert after.features_with_geojson == 1

            # The derived column is what the spatial hot path queries: a
            # point inside the polygon must match through ST_Intersects.
            hit = session.execute(
                text(
                    "SELECT count(*) FROM external_dataset_feature "
                    " WHERE external_dataset_id = :id "
                    "   AND ST_Intersects(geometry, "
                    "       ST_SetSRID(ST_MakePoint(-63.59, 44.65), 4326))"
                ),
                {"id": dataset_id},
            ).scalar_one()
            assert hit == 1
        finally:
            _drop_dataset(session, dataset_id)


@pg_only
def test_audit_catches_geometry_that_drifted_from_its_geojson() -> None:
    """A second writer landing a different shape is exactly what this catches."""
    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as session:
        dataset_id = _seed_dataset(session, suffix)
        try:
            sync_feature_geometry(session, dataset_id=dataset_id)

            session.execute(
                text(
                    "UPDATE external_dataset_feature "
                    "   SET geometry = ST_SetSRID("
                    "       ST_GeomFromGeoJSON(:other), 4326) "
                    " WHERE external_dataset_id = :id AND geometry IS NOT NULL"
                ),
                {
                    "id": dataset_id,
                    "other": json.dumps(
                        {"type": "Polygon", "coordinates": [_OTHER_BOX]}
                    ),
                },
            )
            drifted = audit_feature_geometry(session, dataset_id=dataset_id)
            assert drifted.ok is False
            assert drifted.geometry_mismatch == 1
            assert [s.status for s in drifted.sample] == ["geometry_mismatch"]

            # ``resync`` is the repair: recompute in-scope rows, not just the
            # NULL ones the default pass would touch.
            assert sync_feature_geometry(session, dataset_id=dataset_id) == 0
            assert (
                sync_feature_geometry(session, dataset_id=dataset_id, resync=True) == 1
            )
            assert audit_feature_geometry(session, dataset_id=dataset_id).ok is True
        finally:
            _drop_dataset(session, dataset_id)


@pg_only
def test_writer_scopes_to_explicit_feature_ids() -> None:
    suffix = uuid.uuid4().hex[:12]
    with session_scope(_DB_URL) as session:
        dataset_id = _seed_dataset(session, suffix)
        try:
            feature_id = session.execute(
                text(
                    "SELECT id FROM external_dataset_feature "
                    " WHERE external_dataset_id = :id "
                    "   AND geometry_geojson ->> 'type' IS NOT NULL"
                ),
                {"id": dataset_id},
            ).scalar_one()
            # A disjoint id set must leave the row alone...
            assert (
                sync_feature_geometry(session, feature_ids=[feature_id - 10**9]) == 0
            )
            assert audit_feature_geometry(session, dataset_id=dataset_id).ok is False
            # ...and the right one must write it.
            assert sync_feature_geometry(session, feature_ids=[feature_id]) == 1
            assert audit_feature_geometry(session, dataset_id=dataset_id).ok is True
        finally:
            _drop_dataset(session, dataset_id)
