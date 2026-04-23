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
