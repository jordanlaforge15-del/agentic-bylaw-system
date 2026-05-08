"""PostGIS spatial column + GiST index on external_dataset_feature.

Spatial containment / intersection queries against the
``halifax_zoning_boundaries`` dataset (11k polygons) used to be a
sequential scan: every search loaded all features, parsed each
``geometry_geojson`` JSONB column through shapely, and ran the bbox
prefilter + intersect / contains in Python. That was ~2.6 s per query
in production traffic.

This migration:

* Enables the ``postgis`` extension on the cluster (no-op when
  already loaded).
* Adds a real ``geometry`` column on ``external_dataset_feature``
  typed as ``geometry(Geometry, 4326)`` so PostGIS enforces the
  WGS-84 SRID at insert time.
* Backfills the column from the existing JSONB via
  ``ST_GeomFromGeoJSON`` — single UPDATE for the whole table; on
  this dataset (~12k rows total across all external datasets) it
  runs in well under a second.
* Adds a GiST index on the new column. The retrieval service's
  hot path (``layer2.retrieval.spatial.query_features``) is rewritten
  in the same change to use ``ST_Intersects`` against the index.

SQLite-safety: the test suite uses sqlite, which has no PostGIS. We
detect the dialect at upgrade time and skip every PostGIS statement
on sqlite. The ORM doesn't declare the geometry column (the column
is server-managed) so model load works on sqlite regardless.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0009_postgis_spatial_index"
down_revision = "0008_advisor_billing_subscription"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # sqlite test path — there's no PostGIS to enable, no
        # geometry type to add, and the spatial.py rewrite has its
        # own dialect-aware fallback. Leave the schema untouched.
        return

    op.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    op.execute(
        text(
            "ALTER TABLE external_dataset_feature "
            "ADD COLUMN IF NOT EXISTS geometry geometry(Geometry, 4326)"
        )
    )
    # Backfill. ST_GeomFromGeoJSON returns SRID 4326 by default per
    # RFC 7946 (GeoJSON), which matches the column's typmod. We only
    # touch rows that don't already have a geometry — re-running
    # this migration on a partially-populated DB is safe.
    op.execute(
        text(
            """
            UPDATE external_dataset_feature
               SET geometry = ST_GeomFromGeoJSON(geometry_geojson::text)
             WHERE geometry IS NULL
               AND geometry_geojson IS NOT NULL
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS "
            "ix_external_dataset_feature_geometry_gist "
            "ON external_dataset_feature USING GIST (geometry)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        text(
            "DROP INDEX IF EXISTS ix_external_dataset_feature_geometry_gist"
        )
    )
    op.execute(
        text(
            "ALTER TABLE external_dataset_feature DROP COLUMN IF EXISTS geometry"
        )
    )
    # Leave the postgis extension installed — other migrations or
    # production data may depend on it. Dropping it on downgrade
    # would also drop any dependent geometry columns elsewhere.
