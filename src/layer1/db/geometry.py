"""The PostGIS ``external_dataset_feature.geometry`` column (ABS-491).

``geometry`` is the authoritative spatial column: every ``ST_Intersects`` /
``ST_Contains`` on the retrieval hot path reads it through the GiST index
added by migration ``0009_postgis_spatial_index``. It is a denormalization
of ``geometry_geojson`` — the same shape, stored twice — so the two can
drift, and every drifted row is a feature that silently stops matching
spatial queries while still looking correct in the JSONB.

This module is the one place that knows how the two relate:

* :func:`postgis_geometry_type` — the column type, so the ORM declares the
  column instead of pretending it doesn't exist. Real ``geometry(Geometry,
  4326)`` on Postgres; inert ``TEXT`` on sqlite, which has no PostGIS (same
  variant trick ``json_type()`` uses for JSONB).
* :func:`sync_feature_geometry` — the **single writer**. Every insert path
  that lands ``geometry_geojson`` rows calls this to derive ``geometry``;
  nothing else writes the column. No-op on sqlite.
* :func:`audit_feature_geometry` — the consistency check: does ``geometry``
  still equal what ``geometry_geojson`` says it should be? Postgres-only;
  reports ``checked=False`` elsewhere rather than lying about a pass.

Everything here speaks raw SQL rather than the ORM on purpose: the
derivation is a PostGIS expression with no Python equivalent, and staying
off ``layer1.db.base`` keeps the import one-way (base imports the type from
here, not the other way round).
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field
from sqlalchemy import Text, bindparam, text
from sqlalchemy.orm import Session
from sqlalchemy.types import UserDefinedType

#: WGS-84. GeoJSON is defined in it (RFC 7946), the column's typmod pins
#: it, and every read path in ``layer2.retrieval.spatial`` tags query
#: geometry with it. Nothing in this system stores anything else.
GEOMETRY_SRID = 4326

FEATURE_TABLE = "external_dataset_feature"

#: The derivation, in one place. ``ST_GeomFromGeoJSON`` already returns
#: 4326, but the explicit ``ST_SetSRID`` matches the read side verbatim and
#: makes the SRID a property of this expression rather than of a PostGIS
#: default we'd have to re-check on every upgrade.
_DERIVED_GEOMETRY = (
    f"ST_SetSRID(ST_GeomFromGeoJSON(geometry_geojson::text), {GEOMETRY_SRID})"
)

#: The GeoJSON geometry types ``ST_GeomFromGeoJSON`` accepts. Anything else
#: — an empty ``{}`` (the column default), or a whole ``Feature`` wrapper —
#: makes it raise, which would turn a forgiving audit into a 500.
_GEOJSON_TYPES = (
    "'Point', 'LineString', 'Polygon', "
    "'MultiPoint', 'MultiLineString', 'MultiPolygon', 'GeometryCollection'"
)

#: Rows we can derive a geometry for.
_HAS_GEOJSON = f"geometry_geojson ->> 'type' IN ({_GEOJSON_TYPES})"


class PostGISGeometry(UserDefinedType):
    """``geometry(Geometry, 4326)`` — the PostGIS column type, declared.

    Deliberately thin: no bind/result processing, because nothing assigns
    this attribute through the ORM. Values come back from Postgres as WKB
    hex, which is exactly what a debugging read wants and what the (never
    taken) ORM write path would refuse to fabricate.
    """

    cache_ok = True

    def __init__(
        self, geometry_type: str = "Geometry", srid: int = GEOMETRY_SRID
    ) -> None:
        self.geometry_type = geometry_type
        self.srid = srid

    def get_col_spec(self, **kw: object) -> str:
        return f"geometry({self.geometry_type},{self.srid})"


def postgis_geometry_type():
    """Column type for ``external_dataset_feature.geometry``.

    Postgres gets the real PostGIS type — which means ``CREATE EXTENSION
    postgis`` must already have run, as migration 0009 guarantees. sqlite
    (the unit-test dialect) gets a plain ``TEXT`` column that is created,
    never populated, and never read: the shapely fallback in
    ``layer2.retrieval.spatial`` works off ``geometry_geojson`` there.
    """
    return Text().with_variant(PostGISGeometry(), "postgresql")


def sync_feature_geometry(
    session: Session,
    *,
    dataset_id: int | None = None,
    feature_ids: Sequence[int] | None = None,
    resync: bool = False,
) -> int:
    """Derive ``geometry`` from ``geometry_geojson``. The only writer.

    Flushes first, so callers can ``session.add()`` features and sync in the
    same breath without an ordering footgun.

    ``dataset_id`` / ``feature_ids`` narrow the update; passing neither
    sweeps the whole table (what migration 0009's backfill did). By default
    only rows with a NULL ``geometry`` are touched — re-running is cheap and
    safe. ``resync=True`` recomputes every in-scope row, which is what you
    want after ``geometry_geojson`` itself changed.

    Returns the number of rows written; always 0 on non-Postgres dialects,
    where there is no column to maintain.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return 0
    if feature_ids is not None and len(feature_ids) == 0:
        return 0

    session.flush()

    conditions = [_HAS_GEOJSON]
    if not resync:
        conditions.append("geometry IS NULL")
    if dataset_id is not None:
        conditions.append("external_dataset_id = :dataset_id")
    if feature_ids is not None:
        conditions.append("id IN :feature_ids")

    stmt = text(
        f"UPDATE {FEATURE_TABLE} SET geometry = {_DERIVED_GEOMETRY} "
        f"WHERE {' AND '.join(conditions)}"
    )
    params: dict[str, object] = {}
    if dataset_id is not None:
        params["dataset_id"] = dataset_id
    if feature_ids is not None:
        stmt = stmt.bindparams(bindparam("feature_ids", expanding=True))
        params["feature_ids"] = list(feature_ids)

    result = session.execute(stmt, params)
    session.flush()
    return result.rowcount or 0


class GeometryMismatch(BaseModel):
    """One row whose ``geometry`` disagrees with its ``geometry_geojson``."""

    feature_id: int
    external_dataset_id: int
    feature_key: str
    status: str


class FeatureGeometryReport(BaseModel):
    """Result of :func:`audit_feature_geometry`."""

    dialect: str
    #: False on sqlite — the column is inert there, so "no mismatches" would
    #: be a vacuous pass. Callers must treat this as "unknown", not "ok".
    checked: bool
    features_total: int = 0
    features_with_geojson: int = 0
    features_with_geometry: int = 0
    #: Derivable from GeoJSON but ``geometry`` is NULL — the classic missed
    #: write site: every spatial query against these rows misses.
    missing_geometry: int = 0
    #: ``geometry`` is populated but no GeoJSON backs it any more.
    orphan_geometry: int = 0
    #: Populated, but not the shape ``geometry_geojson`` describes.
    geometry_mismatch: int = 0
    #: Populated with the right shape in the wrong spatial reference.
    srid_mismatch: int = 0
    ok: bool = True
    sample: list[GeometryMismatch] = Field(default_factory=list)


#: Per-row classification. Shared by the count roll-up and the sample query
#: so a status can never mean two different things in the same report.
_CLASSIFY_SQL = f"""
WITH scoped AS (
    SELECT id,
           external_dataset_id,
           feature_key,
           geometry,
           geometry IS NOT NULL AS has_geometry,
           {_HAS_GEOJSON} AS has_geojson,
           CASE WHEN {_HAS_GEOJSON} THEN {_DERIVED_GEOMETRY} END AS expected
      FROM {FEATURE_TABLE}
     -- CAST, not ``::int``: SQLAlchemy's text() bind-parameter regex
     -- refuses to substitute a name immediately followed by ``:``, and an
     -- untyped NULL parameter is one Postgres can't resolve on its own.
     WHERE (CAST(:dataset_id AS integer) IS NULL
            OR external_dataset_id = CAST(:dataset_id AS integer))
)
SELECT id,
       external_dataset_id,
       feature_key,
       has_geometry,
       has_geojson,
       CASE
           WHEN has_geojson AND NOT has_geometry THEN 'missing_geometry'
           WHEN has_geometry AND NOT has_geojson THEN 'orphan_geometry'
           WHEN NOT has_geometry AND NOT has_geojson THEN 'ok'
           WHEN ST_SRID(geometry) <> {GEOMETRY_SRID} THEN 'srid_mismatch'
           WHEN NOT ST_OrderingEquals(geometry, expected) THEN 'geometry_mismatch'
           ELSE 'ok'
       END AS status
  FROM scoped
"""


def audit_feature_geometry(
    session: Session,
    *,
    dataset_id: int | None = None,
    sample_limit: int = 20,
) -> FeatureGeometryReport:
    """Compare every ``geometry`` against the shape its GeoJSON describes.

    Exact-vertex comparison (``ST_OrderingEquals``) rather than topological
    ``ST_Equals``: both sides are derived from the same coordinates by the
    same expression, so anything short of byte-for-byte agreement means a
    write path drifted — which is the whole point of the check.

    On sqlite there is nothing to compare; the report comes back with
    ``checked=False`` and zeroed counts.
    """
    dialect = session.bind.dialect.name if session.bind is not None else "unknown"
    if dialect != "postgresql":
        return FeatureGeometryReport(dialect=dialect, checked=False)

    params = {"dataset_id": dataset_id}
    rollup = session.execute(
        text(
            f"WITH classified AS ({_CLASSIFY_SQL}) "
            "SELECT status, "
            "       count(*) AS n, "
            "       count(*) FILTER (WHERE has_geojson) AS n_geojson, "
            "       count(*) FILTER (WHERE has_geometry) AS n_geometry "
            "  FROM classified GROUP BY status"
        ),
        params,
    ).all()

    report = FeatureGeometryReport(dialect=dialect, checked=True)
    counts: dict[str, int] = {}
    for row in rollup:
        counts[row.status] = row.n
        report.features_total += row.n
        report.features_with_geojson += row.n_geojson
        report.features_with_geometry += row.n_geometry
    report.missing_geometry = counts.get("missing_geometry", 0)
    report.orphan_geometry = counts.get("orphan_geometry", 0)
    report.geometry_mismatch = counts.get("geometry_mismatch", 0)
    report.srid_mismatch = counts.get("srid_mismatch", 0)
    report.ok = not (
        report.missing_geometry
        or report.orphan_geometry
        or report.geometry_mismatch
        or report.srid_mismatch
    )

    if not report.ok and sample_limit > 0:
        sample = session.execute(
            text(
                f"WITH classified AS ({_CLASSIFY_SQL}) "
                "SELECT id, external_dataset_id, feature_key, status "
                "  FROM classified WHERE status <> 'ok' "
                " ORDER BY id LIMIT :sample_limit"
            ),
            {**params, "sample_limit": sample_limit},
        ).all()
        report.sample = [
            GeometryMismatch(
                feature_id=row.id,
                external_dataset_id=row.external_dataset_id,
                feature_key=row.feature_key,
                status=row.status,
            )
            for row in sample
        ]
    return report
