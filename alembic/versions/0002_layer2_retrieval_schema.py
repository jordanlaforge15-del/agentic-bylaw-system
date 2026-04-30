from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

try:
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover
    Vector = None

revision = "0002_layer2_retrieval_schema"
down_revision = "0001_initial_layer1_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    enum_type = postgresql.ENUM if is_postgres else sa.Enum
    enum_options = {"create_type": False} if is_postgres else {}
    querysessionstatus = enum_type(
        "PENDING",
        "RETRIEVING",
        "ANSWERING",
        "COMPLETED",
        "FAILED",
        name="querysessionstatus",
        **enum_options,
    )
    retrievalrunstatus = enum_type("RUNNING", "COMPLETED", "FAILED", name="retrievalrunstatus", **enum_options)
    sourcetype = enum_type("FRAGMENT", "TABLE", "TABLE_CELL", "CLAIM", name="sourcetype", **enum_options)
    retrievalchannel = enum_type(
        "FULL_TEXT",
        "VECTOR",
        "HIERARCHY",
        "CROSS_REFERENCE",
        "CLAIM_REUSE",
        "TABLE",
        name="retrievalchannel",
        **enum_options,
    )
    answerstatus = enum_type("COMPLETED", "INSUFFICIENT_SOURCE", "FAILED", name="answerstatus", **enum_options)
    claimtype = enum_type(
        "DEFINITION",
        "USE_PERMISSION",
        "DIMENSIONAL_STANDARD",
        "PARKING_REQUIREMENT",
        "APPLICABILITY_CONDITION",
        "EXCEPTION",
        "CROSS_REFERENCE_DEPENDENCY",
        "GENERAL_REGULATION",
        "PROCEDURE_REQUIREMENT",
        name="claimtype",
        **enum_options,
    )
    claimstatus = enum_type("ACTIVE", "SUPERSEDED", "REJECTED", name="claimstatus", **enum_options)
    verificationstatus = enum_type("UNVERIFIED", "VERIFIED", "DISPUTED", name="verificationstatus", **enum_options)

    enums = [
        querysessionstatus,
        retrievalrunstatus,
        sourcetype,
        retrievalchannel,
        answerstatus,
        claimtype,
        claimstatus,
        verificationstatus,
    ]
    if is_postgres:
        for enum in enums:
            enum.create(bind, checkfirst=True)

    op.create_table(
        "query_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="SET NULL"), nullable=True),
        sa.Column("municipality", sa.String(length=255), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("normalized_question_text", sa.Text(), nullable=True),
        sa.Column("known_facts_json", json_type, nullable=False),
        sa.Column("status", querysessionstatus, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    vector_type = Vector(384) if is_postgres and Vector is not None else sa.JSON()
    op.create_table(
        "fragment_embedding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("embedding", vector_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )
    if is_postgres:
        op.create_index(
            "ix_fragment_embedding_vector_cosine",
            "fragment_embedding",
            ["embedding"],
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )

    op.create_table(
        "retrieval_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_session_id", sa.Integer(), sa.ForeignKey("query_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retrieval_version", sa.String(length=64), nullable=False),
        sa.Column("metadata_filters_json", json_type, nullable=False),
        sa.Column("query_terms_json", json_type, nullable=False),
        sa.Column("status", retrievalrunstatus, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "retrieval_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("retrieval_run_id", sa.Integer(), sa.ForeignKey("retrieval_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_table_id", sa.Integer(), sa.ForeignKey("source_table.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_table_cell_id", sa.Integer(), sa.ForeignKey("source_table_cell.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_type", sourcetype, nullable=False),
        sa.Column("retrieval_channel", retrievalchannel, nullable=False),
        sa.Column("base_score", sa.Float(), nullable=True),
        sa.Column("rerank_score", sa.Float(), nullable=True),
        sa.Column("selected_for_prompt", sa.Boolean(), nullable=False),
        sa.Column("rank_order", sa.Integer(), nullable=True),
        sa.Column("reason_json", json_type, nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "prompt_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_session_id", sa.Integer(), sa.ForeignKey("query_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retrieval_run_id", sa.Integer(), sa.ForeignKey("retrieval_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("assembled_context_text", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("model_parameters_json", json_type, nullable=False),
        sa.Column("fragment_ids_json", json_type, nullable=False),
        sa.Column("claim_ids_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "answer_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_session_id", sa.Integer(), sa.ForeignKey("query_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_log_id", sa.Integer(), sa.ForeignKey("prompt_log.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_model_output", sa.Text(), nullable=False),
        sa.Column("final_answer_text", sa.Text(), nullable=False),
        sa.Column("answer_status", answerstatus, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "generated_claim",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_session_id", sa.Integer(), sa.ForeignKey("query_session.id", ondelete="SET NULL"), nullable=True),
        sa.Column("answer_log_id", sa.Integer(), sa.ForeignKey("answer_log.id", ondelete="SET NULL"), nullable=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_type", claimtype, nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("canonical_subject", sa.String(length=255), nullable=True),
        sa.Column("canonical_predicate", sa.String(length=255), nullable=True),
        sa.Column("canonical_object_text", sa.Text(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("normalized_value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("operator", sa.String(length=32), nullable=True),
        sa.Column("zone_code", sa.String(length=64), nullable=True),
        sa.Column("use_name", sa.String(length=255), nullable=True),
        sa.Column("applicability_text", sa.Text(), nullable=True),
        sa.Column("condition_text", sa.Text(), nullable=True),
        sa.Column("exception_text", sa.Text(), nullable=True),
        sa.Column("source_fragment_ids_json", json_type, nullable=False),
        sa.Column("source_table_cell_ids_json", json_type, nullable=False),
        sa.Column("citation_text", sa.Text(), nullable=True),
        sa.Column("claim_status", claimstatus, nullable=False),
        sa.Column("verification_status", verificationstatus, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("answer_log_id", sa.Integer(), sa.ForeignKey("answer_log.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("is_incomplete", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "claim_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generated_claim_id", sa.Integer(), sa.ForeignKey("generated_claim.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("corrected_value_text", sa.Text(), nullable=True),
        sa.Column("corrected_structured_json", json_type, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewer_type", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    op.create_table(
        "retrieval_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("retrieval_run_id", sa.Integer(), sa.ForeignKey("retrieval_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("missing_source_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("irrelevant_source_fragment_id", sa.Integer(), sa.ForeignKey("source_fragment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", json_type, nullable=False),
    )

    if is_postgres:
        op.create_index(
            "ix_source_fragment_text_tsv",
            "source_fragment",
            [sa.text("to_tsvector('english', coalesce(citation_label, '') || ' ' || coalesce(text, ''))")],
            postgresql_using="gin",
        )
        op.create_index(
            "ix_source_table_caption_tsv",
            "source_table",
            [sa.text("to_tsvector('english', coalesce(caption, ''))")],
            postgresql_using="gin",
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.drop_index("ix_source_table_caption_tsv", table_name="source_table")
        op.drop_index("ix_source_fragment_text_tsv", table_name="source_fragment")
        op.drop_index("ix_fragment_embedding_vector_cosine", table_name="fragment_embedding")
    op.drop_table("retrieval_feedback")
    op.drop_table("claim_feedback")
    op.drop_table("answer_feedback")
    op.drop_table("generated_claim")
    op.drop_table("answer_log")
    op.drop_table("prompt_log")
    op.drop_table("retrieval_result")
    op.drop_table("retrieval_run")
    op.drop_table("fragment_embedding")
    op.drop_table("query_session")
    for type_name in [
        "verificationstatus",
        "claimstatus",
        "claimtype",
        "answerstatus",
        "retrievalchannel",
        "sourcetype",
        "retrievalrunstatus",
        "querysessionstatus",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
