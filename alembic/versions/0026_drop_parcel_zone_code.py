"""Drop the write-only ``parcel.zone_code`` column (ABS-481).

``parcel.zone_code`` was added by ``0014_compliance_schema`` as a
denormalised copy of the intersecting zoning polygon's zone, documented
in two places as "a convenience for the evaluator's zone-applicability
filter". The evaluator never referenced it: the only writers were
``scripts/backfill_parcels.py`` and the evaluator e2e seed, and no
reader existed anywhere in ``src/`` or ``web/``. That makes it a
stale-able duplicate with no refresh contract — nothing recomputed it
when the zoning dataset was re-ingested — plus an index maintained for
no query.

The zone of record is, and always was, the intersecting zoning
``external_dataset_feature``'s
``canonical_attributes_json['zone_code']`` (unrelated to this column and
untouched here).

Downgrade re-creates the column and its index but leaves every value
NULL — the source data lives in the zoning dataset, and re-deriving it
is the backfill's job, not a migration's. Since nothing read the column,
NULL is behaviourally identical to the values that were there.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_drop_parcel_zone_code"
down_revision = "0025_signup_grant_unique"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_parcel_zone_code"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("parcel")}
    if "zone_code" not in columns:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("parcel")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="parcel")
    op.drop_column("parcel", "zone_code")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("parcel")}
    if "zone_code" in columns:
        return
    op.add_column("parcel", sa.Column("zone_code", sa.String(length=64), nullable=True))
    op.create_index(INDEX_NAME, "parcel", ["zone_code"])
