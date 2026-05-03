from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_geocode_cache"
down_revision = "0004_external_dataset_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "geocode_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_text", sa.String(length=500), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("resolver", sa.String(length=128), nullable=False),
        sa.Column("source_dataset_id", sa.Integer(), sa.ForeignKey("external_dataset.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_feature_id", sa.Integer(), sa.ForeignKey("external_dataset_feature.id", ondelete="SET NULL"), nullable=True),
        sa.Column("geometry_geojson", json_type, nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.UniqueConstraint("normalized_text", name="uq_geocode_cache_normalized_text"),
    )
    op.create_index("ix_geocode_cache_kind", "geocode_cache", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_geocode_cache_kind", table_name="geocode_cache")
    op.drop_table("geocode_cache")
