from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_semantic_index_schema"
down_revision = "0002_layer2_retrieval_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    op.create_table(
        "semantic_entity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_name", sa.String(length=500), nullable=False),
        sa.Column("aliases_json", json_type, nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "entity_type", "canonical_name", name="uq_semantic_entity_document_type_name"),
    )
    op.create_table(
        "semantic_fact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(length=128), nullable=False),
        sa.Column("primary_subject_entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="SET NULL"), nullable=True),
        sa.Column("primary_object_entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="SET NULL"), nullable=True),
        sa.Column("primary_scope_entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="SET NULL"), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("normalized_value_json", json_type, nullable=False),
        sa.Column("assertion_type", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "semantic_fact_participant",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fact_id", sa.Integer(), sa.ForeignKey("semantic_fact.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_table(
        "semantic_edge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_fact_id", sa.Integer(), sa.ForeignKey("semantic_fact.id", ondelete="CASCADE"), nullable=True),
        sa.Column("edge_type", sa.String(length=64), nullable=False),
        sa.Column("target_entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_fact_id", sa.Integer(), sa.ForeignKey("semantic_fact.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_fragment_ids_json", json_type, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_table(
        "table_semantic_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("table_id", sa.Integer(), sa.ForeignKey("source_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_type", sa.String(length=64), nullable=False),
        sa.Column("row_axis_type", sa.String(length=64), nullable=True),
        sa.Column("column_axis_type", sa.String(length=64), nullable=True),
        sa.Column("value_type", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_table(
        "table_axis_binding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("table_id", sa.Integer(), sa.ForeignKey("source_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("axis", sa.String(length=16), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("semantic_entity.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_label", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_table(
        "semantic_provenance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("extraction_method", sa.String(length=128), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    op.create_table(
        "semantic_review_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.String(length=64), nullable=True),
        sa.Column("new_status", sa.String(length=64), nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table_name in [
        "semantic_review_event",
        "semantic_provenance",
        "table_axis_binding",
        "table_semantic_profile",
        "semantic_edge",
        "semantic_fact_participant",
        "semantic_fact",
        "semantic_entity",
    ]:
        op.drop_table(table_name)
