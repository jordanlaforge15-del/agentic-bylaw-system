"""ORM models for the Phase-1 compliance schema.

The four tables here are the structural primitives every later phase
reads or writes. Their lifecycle:

* ``parcel`` — owned by the spatial side. Created by the one-time
  Halifax backfill (``scripts/backfill_parcels.py``) and, going
  forward, by any new municipal ingest. The denormalised
  ``zone_code`` is a convenience for the evaluator; the source of
  truth remains the linked ``external_dataset_feature``.
* ``submission`` — one row per development proposal. ``submitter_id``
  is intentionally nullable because Phase 1 has no tenancy concept —
  it lights up when the advisor wires submissions to advisor users.
* ``submission_attribute`` — the bag of facts a submission carries.
  Keyed on the Phase-1 attribute taxonomy (see
  ``src/layer2/compliance/attributes/taxonomy.yaml``). The
  ``(submission_id, attribute_key)`` uniqueness constraint enforces a
  single value per attribute; updates overwrite in-place.
* ``approval_decision`` — append-only ledger. Each evaluator run
  produces a new row; old rows are kept so the audit trail survives
  taxonomy bumps or evaluator-logic changes. ``evaluator_version``
  pins the calling code's deploy SHA so re-evaluation is reproducible.

The ``source_fragment.attribute_tags`` JSONB column and the
``external_dataset_feature.parcel_id`` FK live on the layer-1 models
directly, since they're additive structural extensions to existing
tables.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from layer1.db.base import Base, Parcel, json_type, utcnow

# Re-export so callers in this package have a single import surface.
__all__ = [
    "Parcel",
    "Submission",
    "SubmissionAttribute",
    "ApprovalDecision",
    "SubmissionStatus",
    "SubmissionSourceType",
    "SubmissionAttributeSource",
]


class SubmissionStatus(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    ARCHIVED = "archived"


class SubmissionSourceType(StrEnum):
    MANUAL = "manual"
    IFC = "ifc"
    RVT_APS = "rvt_aps"
    PDF = "pdf"
    SPECKLE = "speckle"


class SubmissionAttributeSource(StrEnum):
    MANUAL = "manual"
    EXTRACTED = "extracted"
    DERIVED = "derived"
    OVERRIDE = "override"


class Submission(Base):
    """A development proposal being evaluated against the bylaw graph.

    The submission *is* the unit of evaluation: one ``approval_decision``
    row is written per evaluator run against this submission. The
    submission carries its attribute bag in ``submission_attribute``
    rows and points at the ``parcel`` whose zone / overlays drive
    which clauses apply.
    """

    __tablename__ = "submission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parcel_id: Mapped[int | None] = mapped_column(
        ForeignKey("parcel.id", ondelete="SET NULL"), index=True
    )
    submitter_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[SubmissionStatus] = mapped_column(
        SAEnum(SubmissionStatus, name="submission_status"),
        nullable=False,
        default=SubmissionStatus.DRAFT,
        index=True,
    )
    source_type: Mapped[SubmissionSourceType] = mapped_column(
        SAEnum(SubmissionSourceType, name="submission_source_type"),
        nullable=False,
        default=SubmissionSourceType.MANUAL,
    )
    source_artifact_path: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    parcel: Mapped[Parcel | None] = relationship()
    attributes: Mapped[list["SubmissionAttribute"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["ApprovalDecision"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class SubmissionAttribute(Base):
    """A single attribute value carried by a submission.

    Uniqueness on ``(submission_id, attribute_key)`` makes the bag a
    map: trying to insert a second value for the same key fails at the
    DB layer rather than silently shadowing.
    """

    __tablename__ = "submission_attribute"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "attribute_key", name="uq_submission_attribute_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attribute_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type()), default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[SubmissionAttributeSource] = mapped_column(
        SAEnum(SubmissionAttributeSource, name="submission_attribute_source"),
        nullable=False,
        default=SubmissionAttributeSource.MANUAL,
    )
    evidence_json: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(json_type()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    submission: Mapped[Submission] = relationship(back_populates="attributes")


class ApprovalDecision(Base):
    """Immutable evaluator output for one (submission, evaluator_version).

    Re-evaluation never updates an existing row — it appends a new one,
    so the history of "what did the evaluator say last week vs. today"
    is recoverable. ``decided_by`` is nullable in Phase 1 because every
    decision here is automated; manual reviewer overrides land in
    Phase 4.
    """

    __tablename__ = "approval_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_summary_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(json_type()), default=dict
    )
    notes: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    submission: Mapped[Submission] = relationship(back_populates="decisions")
