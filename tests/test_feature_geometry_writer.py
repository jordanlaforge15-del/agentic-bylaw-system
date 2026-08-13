"""ABS-491 — the PostGIS geometry column is ORM-declared and has one writer.

Dialect-independent half of the story. The interesting assertions (does
``geometry`` actually equal ``geometry_geojson``?) need PostGIS and live in
``tests/test_feature_geometry_consistency_pg.py``; what can be checked on
sqlite is the shape of the contract:

* the column exists on the model, and renders as ``geometry(Geometry,4326)``
  on Postgres while degrading to an inert ``TEXT`` column on sqlite;
* the writer and the audit are honest no-ops off Postgres — the audit says
  ``checked=False`` rather than reporting a vacuous pass;
* nothing outside the writer (and the historic migration that introduced
  the column) assigns it.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.dialects import postgresql, sqlite

from layer1.db.base import ExternalDatasetFeature
from layer1.db.geometry import (
    GEOMETRY_SRID,
    audit_feature_geometry,
    sync_feature_geometry,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope

REPO_ROOT = Path(__file__).resolve().parents[1]

# The one production writer, plus the migration that added and backfilled
# the column. 0009 is history: rewriting it to call the helper would change
# an already-applied migration's behaviour on every deployed database.
ALLOWED_WRITE_SITES = {
    "src/layer1/db/geometry.py",
    "alembic/versions/0009_postgis_spatial_index.py",
}

# Assignment to the column, in either the UPDATE (`SET geometry =`) or the
# helper's f-string form. Read-side uses of ST_GeomFromGeoJSON (query
# geometry passed in from the caller) are not writes and are not matched.
WRITE_PATTERN = re.compile(r"SET geometry\s*=|geometry = \{_DERIVED_GEOMETRY\}")


def test_orm_declares_the_geometry_column() -> None:
    column = ExternalDatasetFeature.__table__.c.geometry
    assert column.nullable is True
    assert (
        column.type.compile(dialect=postgresql.dialect())
        == f"geometry(Geometry,{GEOMETRY_SRID})"
    )
    # sqlite has no PostGIS; the variant keeps ``create_all`` legal there.
    assert column.type.compile(dialect=sqlite.dialect()) == "TEXT"


def test_geometry_column_is_deferred() -> None:
    """The WKB payload never rides along on an ordinary feature load."""
    prop = ExternalDatasetFeature.__mapper__.get_property("geometry")
    assert prop.deferred is True


def test_sqlite_create_all_and_writer_are_inert(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'geometry.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        assert sync_feature_geometry(session) == 0
        assert sync_feature_geometry(session, dataset_id=1) == 0
        report = audit_feature_geometry(session)
    assert report.dialect == "sqlite"
    assert report.checked is False
    # A skipped audit must never read as a pass by accident.
    assert report.features_total == 0
    assert report.sample == []


def test_empty_feature_id_scope_writes_nothing(tmp_path: Path) -> None:
    """An empty id list means "no rows", not "every row"."""
    db_url = f"sqlite:///{tmp_path / 'geometry_scope.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        assert sync_feature_geometry(session, feature_ids=[]) == 0


def test_exactly_one_write_path_for_the_geometry_column() -> None:
    offenders = []
    for root in ("src", "scripts", "alembic"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWED_WRITE_SITES:
                continue
            if WRITE_PATTERN.search(path.read_text(encoding="utf-8")):
                offenders.append(rel)
    assert offenders == [], (
        "external_dataset_feature.geometry is written outside "
        "layer1.db.geometry.sync_feature_geometry: " + ", ".join(offenders)
    )
