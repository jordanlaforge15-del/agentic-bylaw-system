from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_external_dataset_schema"
down_revision = "0003_semantic_index_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "external_dataset",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("crs", sa.String(length=64), nullable=False),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.Column("linked_document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="SET NULL"), nullable=True),
        sa.Column("linked_fragment_citation", sa.String(length=255), nullable=True),
        sa.Column("linked_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("schema_mapping_json", json_type, nullable=False),
        sa.Column("parse_status", sa.Enum("PARSED", "UNCERTAIN", "FALLBACK", "ERROR", name="parsestatus", create_type=False), nullable=False),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.UniqueConstraint("name", name="uq_external_dataset_name"),
    )
    op.create_index("ix_external_dataset_content_hash", "external_dataset", ["content_hash"])
    op.create_index("ix_external_dataset_linked_fragment_id", "external_dataset", ["linked_fragment_id"])

    op.create_table(
        "external_dataset_feature",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_dataset_id", sa.Integer(), sa.ForeignKey("external_dataset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_key", sa.String(length=255), nullable=False),
        sa.Column("attributes_json", json_type, nullable=False),
        sa.Column("canonical_attributes_json", json_type, nullable=False),
        sa.Column("geometry_geojson", json_type, nullable=False),
        sa.Column("geometry_bbox_json", json_type, nullable=False),
        sa.Column("parse_status", sa.Enum("PARSED", "UNCERTAIN", "FALLBACK", "ERROR", name="parsestatus", create_type=False), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.UniqueConstraint("external_dataset_id", "feature_key", name="uq_external_dataset_feature_key"),
    )
    op.create_index(
        "ix_external_dataset_feature_dataset_id",
        "external_dataset_feature",
        ["external_dataset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_dataset_feature_dataset_id", table_name="external_dataset_feature")
    op.drop_table("external_dataset_feature")
    op.drop_index("ix_external_dataset_linked_fragment_id", table_name="external_dataset")
    op.drop_index("ix_external_dataset_content_hash", table_name="external_dataset")
    op.drop_table("external_dataset")
