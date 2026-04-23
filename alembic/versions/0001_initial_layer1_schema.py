from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_layer1_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("municipality", sa.String(length=255), nullable=False),
        sa.Column("bylaw_name", sa.String(length=500), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("version_label", sa.String(length=255), nullable=True),
        sa.Column("consolidation_date", sa.Date(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_document_file_hash", "document", ["file_hash"])

    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", name="ingestionstatus"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warnings_json", json_type, nullable=False),
        sa.Column("errors_json", json_type, nullable=False),
    )

    op.create_table(
        "page_block",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.Enum("HEADING", "PARAGRAPH", "LIST_ITEM", "FOOTNOTE", "TABLE_REGION", "HEADER", "FOOTER", "UNKNOWN", name="blocktype"), nullable=False),
        sa.Column("bbox_json", json_type, nullable=True),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("is_boilerplate", sa.Boolean(), nullable=False),
        sa.Column("parser_source", sa.String(length=255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "source_fragment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fragment_type", sa.Enum("PART", "SECTION", "SUBSECTION", "CLAUSE", "SUBCLAUSE", "SCHEDULE", "APPENDIX", "HEADING", "PROSE", "LIST_ITEM", "FOOTNOTE", name="fragmenttype"), nullable=False),
        sa.Column("citation_label", sa.String(length=255), nullable=True),
        sa.Column("citation_path", sa.String(length=1000), nullable=True),
        sa.Column("parent_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("reading_order_start", sa.Integer(), nullable=True),
        sa.Column("reading_order_end", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_status", sa.Enum("PARSED", "UNCERTAIN", "FALLBACK", "ERROR", name="parsestatus"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_block_ids_json", json_type, nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.UniqueConstraint("document_id", "citation_path", name="uq_fragment_citation_path"),
    )

    op.create_table(
        "source_table",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("parse_status", sa.Enum("PARSED", "UNCERTAIN", "FALLBACK", "ERROR", name="parsestatus", create_type=False), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "source_table_cell",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("table_id", sa.Integer(), sa.ForeignKey("source_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("col_index", sa.Integer(), nullable=False),
        sa.Column("row_header_path", sa.Text(), nullable=True),
        sa.Column("col_header_path", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bbox_json", json_type, nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "cross_reference",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_reference_text", sa.Text(), nullable=False),
        sa.Column("target_citation_guess", sa.String(length=255), nullable=True),
        sa.Column("target_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolution_status", sa.Enum("UNRESOLVED", "RESOLVED", "AMBIGUOUS", "NO_TARGET", name="resolutionstatus"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cross_reference")
    op.drop_table("source_table_cell")
    op.drop_table("source_table")
    op.drop_table("source_fragment")
    op.drop_table("page_block")
    op.drop_table("ingestion_run")
    op.drop_index("ix_document_file_hash", table_name="document")
    op.drop_table("document")
    op.execute("DROP TYPE IF EXISTS resolutionstatus")
    op.execute("DROP TYPE IF EXISTS parsestatus")
    op.execute("DROP TYPE IF EXISTS fragmenttype")
    op.execute("DROP TYPE IF EXISTS blocktype")
    op.execute("DROP TYPE IF EXISTS ingestionstatus")
