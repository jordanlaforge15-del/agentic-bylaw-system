from __future__ import annotations

from enum import StrEnum


class QuerySessionStatus(StrEnum):
    PENDING = "pending"
    RETRIEVING = "retrieving"
    ANSWERING = "answering"
    COMPLETED = "completed"
    FAILED = "failed"


class RetrievalRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceType(StrEnum):
    FRAGMENT = "fragment"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    CLAIM = "claim"
    DATASET = "dataset"
    DATASET_FEATURE = "dataset_feature"


class RetrievalChannel(StrEnum):
    FULL_TEXT = "full_text"
    VECTOR = "vector"
    HIERARCHY = "hierarchy"
    CROSS_REFERENCE = "cross_reference"
    CLAIM_REUSE = "claim_reuse"
    TABLE = "table"
    DATASET = "dataset"
    SPATIAL = "spatial"


class AnswerStatus(StrEnum):
    COMPLETED = "completed"
    INSUFFICIENT_SOURCE = "insufficient_source"
    FAILED = "failed"


class ClaimType(StrEnum):
    DEFINITION = "definition"
    USE_PERMISSION = "use_permission"
    DIMENSIONAL_STANDARD = "dimensional_standard"
    PARKING_REQUIREMENT = "parking_requirement"
    APPLICABILITY_CONDITION = "applicability_condition"
    EXCEPTION = "exception"
    CROSS_REFERENCE_DEPENDENCY = "cross_reference_dependency"
    GENERAL_REGULATION = "general_regulation"
    PROCEDURE_REQUIREMENT = "procedure_requirement"


class ClaimStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
