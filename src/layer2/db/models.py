from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from layer1.db.base import Base, Document, json_type, utcnow
from layer2.db.types import EmbeddingVector
from layer2.models.enums import (
    AnswerStatus,
    ClaimStatus,
    ClaimType,
    QuerySessionStatus,
    RetrievalChannel,
    RetrievalRunStatus,
    SourceType,
    VerificationStatus,
)


class QuerySession(Base):
    __tablename__ = "query_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id", ondelete="SET NULL"))
    municipality: Mapped[str | None] = mapped_column(String(255))
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_question_text: Mapped[str | None] = mapped_column(Text)
    known_facts_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    status: Mapped[QuerySessionStatus] = mapped_column(
        SAEnum(QuerySessionStatus),
        nullable=False,
        default=QuerySessionStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    document: Mapped[Document | None] = relationship()


class FragmentEmbedding(Base):
    __tablename__ = "fragment_embedding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    source_fragment_id: Mapped[int] = mapped_column(ForeignKey("source_fragment.id", ondelete="CASCADE"), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class RetrievalRun(Base):
    __tablename__ = "retrieval_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_session_id: Mapped[int] = mapped_column(ForeignKey("query_session.id", ondelete="CASCADE"), nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_filters_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    query_terms_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    status: Mapped[RetrievalRunStatus] = mapped_column(SAEnum(RetrievalRunStatus), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class RetrievalResult(Base):
    __tablename__ = "retrieval_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    retrieval_run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_run.id", ondelete="CASCADE"), nullable=False)
    source_fragment_id: Mapped[int | None] = mapped_column(ForeignKey("source_fragment.id", ondelete="SET NULL"))
    source_table_id: Mapped[int | None] = mapped_column(ForeignKey("source_table.id", ondelete="SET NULL"))
    source_table_cell_id: Mapped[int | None] = mapped_column(ForeignKey("source_table_cell.id", ondelete="SET NULL"))
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType), nullable=False)
    retrieval_channel: Mapped[RetrievalChannel] = mapped_column(SAEnum(RetrievalChannel), nullable=False)
    base_score: Mapped[float | None] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)
    selected_for_prompt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rank_order: Mapped[int | None] = mapped_column(Integer)
    reason_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class PromptLog(Base):
    __tablename__ = "prompt_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_session_id: Mapped[int] = mapped_column(ForeignKey("query_session.id", ondelete="CASCADE"), nullable=False)
    retrieval_run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_run.id", ondelete="CASCADE"), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    assembled_context_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_parameters_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    fragment_ids_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    claim_ids_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnswerLog(Base):
    __tablename__ = "answer_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_session_id: Mapped[int] = mapped_column(ForeignKey("query_session.id", ondelete="CASCADE"), nullable=False)
    prompt_log_id: Mapped[int] = mapped_column(ForeignKey("prompt_log.id", ondelete="CASCADE"), nullable=False)
    raw_model_output: Mapped[str] = mapped_column(Text, nullable=False)
    final_answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_status: Mapped[AnswerStatus] = mapped_column(SAEnum(AnswerStatus), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class GeneratedClaim(Base):
    __tablename__ = "generated_claim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_session_id: Mapped[int | None] = mapped_column(ForeignKey("query_session.id", ondelete="SET NULL"))
    answer_log_id: Mapped[int | None] = mapped_column(ForeignKey("answer_log.id", ondelete="SET NULL"))
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    claim_type: Mapped[ClaimType] = mapped_column(SAEnum(ClaimType), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_subject: Mapped[str | None] = mapped_column(String(255))
    canonical_predicate: Mapped[str | None] = mapped_column(String(255))
    canonical_object_text: Mapped[str | None] = mapped_column(Text)
    numeric_value: Mapped[float | None] = mapped_column(Numeric(12, 4))
    normalized_value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    operator: Mapped[str | None] = mapped_column(String(32))
    zone_code: Mapped[str | None] = mapped_column(String(64))
    use_name: Mapped[str | None] = mapped_column(String(255))
    applicability_text: Mapped[str | None] = mapped_column(Text)
    condition_text: Mapped[str | None] = mapped_column(Text)
    exception_text: Mapped[str | None] = mapped_column(Text)
    source_fragment_ids_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    source_table_cell_ids_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    citation_text: Mapped[str | None] = mapped_column(Text)
    claim_status: Mapped[ClaimStatus] = mapped_column(SAEnum(ClaimStatus), nullable=False, default=ClaimStatus.ACTIVE)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(VerificationStatus),
        nullable=False,
        default=VerificationStatus.UNVERIFIED,
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    model_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class AnswerFeedback(Base):
    __tablename__ = "answer_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    answer_log_id: Mapped[int] = mapped_column(ForeignKey("answer_log.id", ondelete="CASCADE"), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    is_incomplete: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class ClaimFeedback(Base):
    __tablename__ = "claim_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generated_claim_id: Mapped[int] = mapped_column(ForeignKey("generated_claim.id", ondelete="CASCADE"), nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    corrected_value_text: Mapped[str | None] = mapped_column(Text)
    corrected_structured_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewer_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class RetrievalFeedback(Base):
    __tablename__ = "retrieval_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    retrieval_run_id: Mapped[int] = mapped_column(ForeignKey("retrieval_run.id", ondelete="CASCADE"), nullable=False)
    missing_source_fragment_id: Mapped[int | None] = mapped_column(ForeignKey("source_fragment.id", ondelete="SET NULL"))
    irrelevant_source_fragment_id: Mapped[int | None] = mapped_column(ForeignKey("source_fragment.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
