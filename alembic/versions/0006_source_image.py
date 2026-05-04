from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_source_image"
down_revision = "0005_geocode_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "source_image",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("bbox_json", json_type, nullable=True),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("image_format", sa.String(length=16), nullable=True),
        sa.Column("caption_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("figure_kind", sa.String(length=64), nullable=False),
        sa.Column("docling_ref", sa.String(length=255), nullable=True),
        sa.Column("parse_status", postgresql.ENUM("PARSED", "UNCERTAIN", "FALLBACK", "ERROR", name="parsestatus", create_type=False), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_index("ix_source_image_document_page", "source_image", ["document_id", "page_number"])


def downgrade() -> None:
    op.drop_index("ix_source_image_document_page", table_name="source_image")
    op.drop_table("source_image")
