from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from layer2.models.enums import ClaimType


class QueryUnderstanding(BaseModel):
    normalized_question: str
    topics: list[str] = Field(default_factory=list)
    legal_concepts: list[str] = Field(default_factory=list)
    section_types: list[str] = Field(default_factory=list)
    zone_keywords: list[str] = Field(default_factory=list)
    use_keywords: list[str] = Field(default_factory=list)
    citation_guesses: list[str] = Field(default_factory=list)
    needs_definitions: bool = False


class CandidateFragment(BaseModel):
    source_fragment_id: int | None = None
    source_table_id: int | None = None
    source_table_cell_id: int | None = None
    source_type: str
    retrieval_channel: str
    base_score: float = 0.0
    rerank_score: float = 0.0
    text: str
    citation_label: str | None = None
    citation_path: str | None = None
    reason: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CachedClaimContext(BaseModel):
    claim_id: int
    claim_type: str
    topic: str | None = None
    text: str
    source_fragment_ids: list[int] = Field(default_factory=list)
    verification_status: str
    confidence: float | None = None


class PromptContext(BaseModel):
    question_text: str
    known_facts: dict[str, Any] = Field(default_factory=dict)
    fragments: list[CandidateFragment] = Field(default_factory=list)
    cached_claims: list[CachedClaimContext] = Field(default_factory=list)


class StructuredClaim(BaseModel):
    claim_type: ClaimType
    topic: str
    canonical_subject: str | None = None
    canonical_predicate: str | None = None
    canonical_object_text: str | None = None
    numeric_value: float | None = None
    normalized_value_text: str | None = None
    unit: str | None = None
    operator: str | None = None
    zone_code: str | None = None
    use_name: str | None = None
    applicability_text: str | None = None
    condition_text: str | None = None
    exception_text: str | None = None
    source_fragment_ids: list[int] = Field(default_factory=list)
    source_table_cell_ids: list[int] = Field(default_factory=list)
    citation_text: str | None = None
    confidence: float | None = None


class LLMAnswerPayload(BaseModel):
    answer_text: str
    assumptions: list[str] = Field(default_factory=list)
    insufficient_source: bool = False
    cited_fragment_ids: list[int] = Field(default_factory=list)
    cited_citation_labels: list[str] = Field(default_factory=list)
    claims: list[StructuredClaim] = Field(default_factory=list)


class EvalCase(BaseModel):
    question: str
    expected_topics: list[str] = Field(default_factory=list)
    expected_fragment_ids: list[int] = Field(default_factory=list)
    expected_citation_labels: list[str] = Field(default_factory=list)
    expected_answer_keywords: list[str] = Field(default_factory=list)
    expected_claim_shapes: list[dict[str, Any]] = Field(default_factory=list)
    known_facts_json: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class RetrievalBundle:
    understanding: QueryUnderstanding
    candidates: list[CandidateFragment] = field(default_factory=list)
    cached_claims: list[CachedClaimContext] = field(default_factory=list)
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    query_terms: dict[str, Any] = field(default_factory=dict)

