"""Pydantic v2 models for the City Intake Manifest.

These models define the schema produced by the City Intake (Bootstrap) phase
of the City Learning Pipeline. They are intentionally permissive on optional
metadata so jurisdictions with sparse source information can still produce a
valid manifest, while enforcing the non-negotiable invariants:

- ``ParserConfig.confidence`` must lie in ``[0.0, 1.0]``.
- ``TaxonomyMap.confidence`` must lie in ``[0.0, 1.0]``.
- ``CityIntakeManifest`` must reference at least one in-scope source.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


DocumentType = Literal["bylaw", "schedule", "appendix", "map", "amendment", "policy"]
DocumentFormat = Literal["pdf", "html", "xml", "docx"]
AccessMethod = Literal["direct_download", "paginated_html", "portal", "api"]
ManifestStatus = Literal["draft", "approved", "active", "deprecated"]

CanonicalZoneType = Literal[
    "residential",
    "commercial",
    "mixed_use",
    "industrial",
    "institutional",
    "open_space",
    "special_area",
    "unknown",
]


class SourceDocument(BaseModel):
    document_name: str
    document_type: DocumentType
    document_role: str
    parent_document: Optional[str]
    source_url: str
    format: DocumentFormat
    access_method: AccessMethod
    page_count_estimate: Optional[int]
    last_amended: Optional[date]
    amendment_series: Optional[str]
    in_scope: bool


class CitationLevel(BaseModel):
    level: str
    pattern: str
    label_format: str


class CitationScheme(BaseModel):
    full_citation_example: str
    separator: str
    hierarchy: list[CitationLevel]


class ParserConfig(BaseModel):
    parser_version: str
    citation_scheme: CitationScheme
    schedule_patterns: list[str]
    table_caption_pattern: Optional[str]
    confidence: float
    flags: list[str]

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v):
        assert 0.0 <= v <= 1.0, "confidence must be between 0 and 1"
        return v


class ZoneDesignation(BaseModel):
    code: str
    full_name: Optional[str] = None
    canonical_type: CanonicalZoneType
    description: Optional[str] = None


class VocabularyExtensionCandidate(BaseModel):
    local_term: str
    proposed_canonical_key: str
    was_in_vocabulary: bool = False
    source_window: Optional[int] = None


class TaxonomyMap(BaseModel):
    zone_designations: list[ZoneDesignation]
    use_class_map: dict[str, str]
    standards_categories: list[str]
    companion_bylaws_required: list[str]
    confidence: float
    flags: list[str] = []
    vocabulary_version: Optional[str] = None
    vocabulary_extension_candidates: list[VocabularyExtensionCandidate] = []
    proposed_categories: list[str] = []

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v):
        assert 0.0 <= v <= 1.0, "confidence must be between 0 and 1"
        return v


class Municipality(BaseModel):
    name: str
    jurisdiction_code: str
    province: str
    governing_body: Optional[str]


class QAReport(BaseModel):
    status: Literal["PASS", "PASS_WITH_FLAGS", "FAIL"]
    citation_resolution_rate: float
    zone_completeness: float
    pattern_coverage: float
    flags: list[dict]
    recommended_action: str


class CityIntakeManifest(BaseModel):
    municipality: Municipality
    sources: list[SourceDocument]
    parser_config: Optional[ParserConfig]
    taxonomy: Optional[TaxonomyMap] = None
    qa_report: Optional[QAReport]
    manifest_version: str
    status: ManifestStatus
    pipeline_ready: bool
    flags: list[str] = []

    @field_validator("sources")
    @classmethod
    def at_least_one_in_scope(cls, v):
        assert any(s.in_scope for s in v), "manifest must have at least one in-scope source"
        return v
