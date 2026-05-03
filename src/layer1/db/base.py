from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from layer1.models.enums import (
    BlockType,
    FragmentType,
    IngestionStatus,
    ParseStatus,
    ResolutionStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def json_type():
    return JSON().with_variant(JSONB, "postgresql")


class Document(Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    municipality: Mapped[str] = mapped_column(String(255), nullable=False)
    bylaw_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_label: Mapped[str | None] = mapped_column(String(255))
    consolidation_date: Mapped[object | None] = mapped_column(Date)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    parser_version: Mapped[str | None] = mapped_column(String(255))

    runs: Mapped[list["IngestionRun"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    page_blocks: Mapped[list["PageBlock"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    fragments: Mapped[list["SourceFragment"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    tables: Mapped[list["SourceTable"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    cross_references: Mapped[list["CrossReference"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class IngestionRun(Base):
    __tablename__ = "ingestion_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[IngestionStatus] = mapped_column(SAEnum(IngestionStatus), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warnings_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    errors_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)

    document: Mapped[Document] = relationship(back_populates="runs")


class PageBlock(Base):
    __tablename__ = "page_block"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[BlockType] = mapped_column(SAEnum(BlockType), nullable=False)
    bbox_json: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(json_type()))
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    is_boilerplate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parser_source: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    document: Mapped[Document] = relationship(back_populates="page_blocks")


class SourceFragment(Base):
    __tablename__ = "source_fragment"
    __table_args__ = (UniqueConstraint("document_id", "citation_path", name="uq_fragment_citation_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    fragment_type: Mapped[FragmentType] = mapped_column(SAEnum(FragmentType), nullable=False)
    citation_label: Mapped[str | None] = mapped_column(String(255))
    citation_path: Mapped[str | None] = mapped_column(String(1000))
    parent_fragment_id: Mapped[int | None] = mapped_column(ForeignKey("source_fragment.id", ondelete="SET NULL"))
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    reading_order_start: Mapped[int | None] = mapped_column(Integer)
    reading_order_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_status: Mapped[ParseStatus] = mapped_column(SAEnum(ParseStatus), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_block_ids_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    document: Mapped[Document] = relationship(back_populates="fragments")
    parent: Mapped["SourceFragment | None"] = relationship(remote_side=[id])


class SourceTable(Base):
    __tablename__ = "source_table"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    parent_fragment_id: Mapped[int | None] = mapped_column(ForeignKey("source_fragment.id", ondelete="SET NULL"))
    caption: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_status: Mapped[ParseStatus] = mapped_column(SAEnum(ParseStatus), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    document: Mapped[Document] = relationship(back_populates="tables")
    cells: Mapped[list["SourceTableCell"]] = relationship(back_populates="table", cascade="all, delete-orphan")


class SourceTableCell(Base):
    __tablename__ = "source_table_cell"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("source_table.id", ondelete="CASCADE"), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    col_index: Mapped[int] = mapped_column(Integer, nullable=False)
    row_header_path: Mapped[str | None] = mapped_column(Text)
    col_header_path: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_json: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(json_type()))
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    table: Mapped[SourceTable] = relationship(back_populates="cells")


class CrossReference(Base):
    __tablename__ = "cross_reference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    source_fragment_id: Mapped[int] = mapped_column(ForeignKey("source_fragment.id", ondelete="CASCADE"), nullable=False)
    raw_reference_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_citation_guess: Mapped[str | None] = mapped_column(String(255))
    target_fragment_id: Mapped[int | None] = mapped_column(ForeignKey("source_fragment.id", ondelete="SET NULL"))
    resolution_status: Mapped[ResolutionStatus] = mapped_column(SAEnum(ResolutionStatus), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    document: Mapped[Document] = relationship(back_populates="cross_references")


class SemanticEntity(Base):
    __tablename__ = "semantic_entity"
    __table_args__ = (
        UniqueConstraint("document_id", "entity_type", "canonical_name", name="uq_semantic_entity_document_type_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    aliases_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(64), default="unreviewed", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemanticFact(Base):
    __tablename__ = "semantic_fact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    primary_subject_entity_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_entity.id", ondelete="SET NULL"))
    primary_object_entity_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_entity.id", ondelete="SET NULL"))
    primary_scope_entity_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_entity.id", ondelete="SET NULL"))
    value_text: Mapped[str | None] = mapped_column(Text)
    normalized_value_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    assertion_type: Mapped[str] = mapped_column(String(64), default="explicit", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(64), default="unreviewed", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemanticFactParticipant(Base):
    __tablename__ = "semantic_fact_participant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("semantic_fact.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[int] = mapped_column(ForeignKey("semantic_entity.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class SemanticEdge(Base):
    __tablename__ = "semantic_edge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    source_entity_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_entity.id", ondelete="SET NULL"))
    source_fact_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_fact.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_entity_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_entity.id", ondelete="SET NULL"))
    target_fact_id: Mapped[int | None] = mapped_column(ForeignKey("semantic_fact.id", ondelete="CASCADE"))
    source_fragment_ids_json: Mapped[list] = mapped_column(MutableList.as_mutable(json_type()), default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class TableSemanticProfile(Base):
    __tablename__ = "table_semantic_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("source_table.id", ondelete="CASCADE"), nullable=False)
    profile_type: Mapped[str] = mapped_column(String(64), nullable=False)
    row_axis_type: Mapped[str | None] = mapped_column(String(64))
    column_axis_type: Mapped[str | None] = mapped_column(String(64))
    value_type: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(64), default="unreviewed", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class TableAxisBinding(Base):
    __tablename__ = "table_axis_binding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("source_table.id", ondelete="CASCADE"), nullable=False)
    axis: Mapped[str] = mapped_column(String(16), nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_id: Mapped[int] = mapped_column(ForeignKey("semantic_entity.id", ondelete="CASCADE"), nullable=False)
    raw_label: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class SemanticProvenance(Base):
    __tablename__ = "semantic_provenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str] = mapped_column(String(128), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)


class SemanticReviewEvent(Base):
    __tablename__ = "semantic_review_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(64))
    new_status: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalDataset(Base):
    __tablename__ = "external_dataset"
    __table_args__ = (UniqueConstraint("name", name="uq_external_dataset_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    crs: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_count: Mapped[int] = mapped_column(Integer, nullable=False)
    linked_document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id", ondelete="SET NULL"))
    linked_fragment_citation: Mapped[str | None] = mapped_column(String(255))
    linked_fragment_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_fragment.id", ondelete="SET NULL"), index=True
    )
    schema_mapping_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    parse_status: Mapped[ParseStatus] = mapped_column(SAEnum(ParseStatus), nullable=False)
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    features: Mapped[list["ExternalDatasetFeature"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class ExternalDatasetFeature(Base):
    __tablename__ = "external_dataset_feature"
    __table_args__ = (
        UniqueConstraint("external_dataset_id", "feature_key", name="uq_external_dataset_feature_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_dataset_id: Mapped[int] = mapped_column(
        ForeignKey("external_dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    canonical_attributes_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    geometry_geojson: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    geometry_bbox_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    parse_status: Mapped[ParseStatus] = mapped_column(SAEnum(ParseStatus), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)

    dataset: Mapped[ExternalDataset] = relationship(back_populates="features")
