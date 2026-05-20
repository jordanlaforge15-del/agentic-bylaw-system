"""Compliance evaluator service.

Given a set of submission attributes and a location, produce a
structured compliance matrix: for each attribute, which bylaw
clauses apply, what each requires, and a pass / fail / uncertain
verdict with the computed delta.

Architecture
------------
The evaluator orchestrates three collaborators:

* :class:`RetrievalService` — fetches candidate clauses per attribute,
  scoped by the new ``attribute_tag_filter`` (added by ABS-45). The
  evaluator never duplicates retrieval logic; it just feeds the
  request and reads the matches.
* :class:`ConstraintExtractor` — pulls a structured
  ``{operator, value, unit}`` out of each candidate clause's text.
  Pluggable so tests can run hermetic and production uses Claude.
* the layer-1 ``source_fragment.metadata_json`` field — used as a
  cache for prior extractions (keyed on the fragment text hash +
  taxonomy version + attribute) so a re-evaluation against the same
  corpus doesn't pay the LLM cost twice.

The aggregator that turns clause-level verdicts into an
``overall_status`` is the third surface; its policy is intentionally
explicit and documented inline (see :func:`_aggregate_overall_status`).

What this module is NOT
-----------------------
* It does not run the LLM directly — that goes through the injected
  :class:`ConstraintExtractor` so tests have a swap point.
* It does not implement zone-applicability ranking beyond what the
  retrieval service already does — that improvement is Phase 4
  scope.
* It does not handle subjective attributes — those don't carry an
  ``attribute_tags`` entry and never reach this code path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from bylaw_retrieval.retrieval import (
    LocationSlot,
    RetrievalMatch,
    RetrievalRequest,
    RetrievalService,
)
from layer1.db.base import SourceFragment
from layer2.compliance.db.models import (
    ApprovalDecision,
    Submission,
    SubmissionAttribute as SubmissionAttributeRow,
    SubmissionAttributeSource,
    SubmissionStatus,
)
from layer2.compliance.taxonomy import (
    AttributeDefinition,
    AttributeValueType,
    Taxonomy,
    load_taxonomy,
)


logger = logging.getLogger("layer2.compliance.evaluator")


EVALUATOR_VERSION = "phase1-2026-05"
CONSTRAINT_CACHE_KEY = "clause_constraints_v1"
DEFAULT_PER_ATTRIBUTE_LIMIT = 8


Verdict = Literal["pass", "fail", "uncertain"]
OverallStatus = Literal["compliant", "non_compliant", "uncertain", "incomplete"]


@dataclass
class SubmissionAttributeInput:
    """An attribute value supplied to the evaluator.

    Use either inline (when ``EvaluationRequest.submission_id`` is
    None) or pulled from ``submission_attribute`` when persisting.
    ``value`` is whatever the taxonomy's ``value_type`` expects —
    number, integer, string, bool. Unit is normalised by the taxonomy
    (the evaluator doesn't unit-convert in v1).
    """

    attribute_key: str
    value: Any
    unit: str | None = None
    source: SubmissionAttributeSource = SubmissionAttributeSource.MANUAL
    confidence: float = 1.0


@dataclass
class DocumentFilters:
    """Per-call filters narrowing the bylaw corpus.

    Mirror the same fields as ``RetrievalRequest`` so callers can
    re-use shapes they already build for retrieval. Each is optional;
    when None, the evaluator queries the union of all ingested
    bylaws (subject to whatever default the retrieval service is
    configured with — e.g. ``--latest-only``).
    """

    municipality: str | None = None
    bylaw_name: str | None = None
    citation_path_prefix: str | None = None
    document_id: int | None = None


@dataclass
class EvaluationRequest:
    """Inputs for one evaluator run."""

    attributes: list[SubmissionAttributeInput]
    location: LocationSlot | None = None
    submission_id: int | None = None
    document_filters: DocumentFilters | None = None
    taxonomy_version: str | None = None
    per_attribute_limit: int = DEFAULT_PER_ATTRIBUTE_LIMIT
    # When True and submission_id is set, the evaluator writes an
    # approval_decision row pinned to EVALUATOR_VERSION. False is the
    # right call from agent flows that want a "what-if" probe.
    persist_decision: bool = True


@dataclass
class ConstraintLiteral:
    """A structured constraint extracted from a clause's prose.

    The five-tuple of (operator, value, unit, condition,
    confidence) is the contract every later phase reads. ``None``
    operator means "the clause is informational / subjective /
    conditional-on-something-we-don't-have" — verdict is then
    ``uncertain`` regardless of submitted value.
    """

    operator: Literal["<", "<=", "==", ">=", ">", "between", "in", "unknown"] | None = None
    value: Any = None
    unit: str | None = None
    condition: str | None = None
    confidence: float = 1.0
    # Free-form list of attribute IDs the clause is conditional on.
    # Used to surface ``unevaluated_attributes`` when a clause's
    # condition references something the submission didn't carry
    # (e.g. corner_lot_boolean).
    conditional_on: list[str] = field(default_factory=list)


@dataclass
class ClauseEvaluation:
    """One bylaw clause's contribution to an attribute's verdict."""

    fragment_id: int
    citation_path: str | None
    document_id: int
    municipality: str
    bylaw_name: str
    text: str
    score: float
    retrieval_channels: list[str]
    constraint: ConstraintLiteral | None
    verdict: Verdict
    delta: dict[str, Any] | None
    notes: list[str] = field(default_factory=list)


@dataclass
class AttributeResult:
    """The per-attribute row of the compliance matrix."""

    attribute_key: str
    submitted_value: Any
    applicable_clauses: list[ClauseEvaluation]
    verdict: Verdict
    delta: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class EvaluationResponse:
    overall_status: OverallStatus
    attribute_results: list[AttributeResult]
    unevaluated_attributes: list[str]
    notes: list[str] = field(default_factory=list)
    evaluator_version: str = EVALUATOR_VERSION
    taxonomy_version: str | None = None
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approval_decision_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable form for persistence + MCP responses."""
        return {
            "overall_status": self.overall_status,
            "evaluator_version": self.evaluator_version,
            "taxonomy_version": self.taxonomy_version,
            "decided_at": self.decided_at.isoformat(),
            "notes": list(self.notes),
            "unevaluated_attributes": list(self.unevaluated_attributes),
            "attribute_results": [
                {
                    "attribute_key": r.attribute_key,
                    "submitted_value": r.submitted_value,
                    "verdict": r.verdict,
                    "delta": r.delta,
                    "notes": list(r.notes),
                    "applicable_clauses": [
                        {
                            "fragment_id": c.fragment_id,
                            "citation_path": c.citation_path,
                            "document_id": c.document_id,
                            "municipality": c.municipality,
                            "bylaw_name": c.bylaw_name,
                            "text": c.text,
                            "score": c.score,
                            "retrieval_channels": list(c.retrieval_channels),
                            "constraint": _constraint_to_json(c.constraint),
                            "verdict": c.verdict,
                            "delta": c.delta,
                            "notes": list(c.notes),
                        }
                        for c in r.applicable_clauses
                    ],
                }
                for r in self.attribute_results
            ],
        }


# ----------------------------------------------------------------------
# Pluggable constraint extractor
# ----------------------------------------------------------------------


class ConstraintExtractor(Protocol):
    """Extracts a structured constraint from a clause's text.

    Production: Claude-backed. Tests: deterministic fake. The
    extractor must be side-effect free and stable for the same
    (text, attribute) pair so the cache key is sound.
    """

    name: str

    def extract(
        self,
        *,
        clause_text: str,
        citation_context: str,
        attribute: AttributeDefinition,
    ) -> ConstraintLiteral | None: ...


class RegexConstraintExtractor:
    """Fallback extractor that pulls simple numeric constraints from clause text.

    Heuristics are deliberately narrow:

    * "shall not be less than X m" → ``>= X m``
    * "minimum X m" → ``>= X m``
    * "shall not exceed X m" / "maximum X m" → ``<= X m``
    * "shall be X m" → ``== X m``

    Anything outside these patterns returns None ("uncertain" verdict
    at the clause level). The regex extractor is used as a final
    fallback when the LLM-backed extractor returns None or fails; it
    also serves as the default in tests so the suite has a real,
    deterministic extractor without mocking machinery.
    """

    name = "regex-extractor"

    _PATTERNS = (
        # ">= X UNIT"
        (re.compile(r"(?:not\s+(?:be\s+)?less\s+than|at\s+least|minimum)\s+(\d+(?:\.\d+)?)\s*(m|metres?|meters?|feet|ft|%)?", re.IGNORECASE), ">="),
        # "<= X UNIT"
        (re.compile(r"(?:shall\s+not\s+exceed|not\s+(?:be\s+)?more\s+than|at\s+most|maximum|not\s+exceed)\s+(\d+(?:\.\d+)?)\s*(m|metres?|meters?|feet|ft|%)?", re.IGNORECASE), "<="),
        # "== X UNIT"
        (re.compile(r"(?:shall\s+be)\s+(\d+(?:\.\d+)?)\s*(m|metres?|meters?|feet|ft|%)?", re.IGNORECASE), "=="),
    )

    def extract(
        self,
        *,
        clause_text: str,
        citation_context: str,
        attribute: AttributeDefinition,
    ) -> ConstraintLiteral | None:
        # Don't try to extract numbers for non-numeric attributes —
        # the value_type contract rules them out.
        if attribute.value_type not in {
            AttributeValueType.NUMBER,
            AttributeValueType.INTEGER,
        }:
            return None
        for pattern, operator in self._PATTERNS:
            match = pattern.search(clause_text)
            if not match:
                continue
            value = float(match.group(1))
            unit_token = (match.group(2) or "").lower()
            unit = _normalise_unit(unit_token) if unit_token else attribute.unit
            if attribute.value_type == AttributeValueType.INTEGER:
                value = int(round(value))
            return ConstraintLiteral(
                operator=operator,  # type: ignore[arg-type]
                value=value,
                unit=unit,
                confidence=0.6,
            )
        return None


def _normalise_unit(token: str) -> str:
    token = token.lower()
    if token in {"m", "metre", "metres", "meter", "meters"}:
        return "m"
    if token in {"ft", "feet"}:
        return "ft"
    return token


# ----------------------------------------------------------------------
# Evaluator service
# ----------------------------------------------------------------------


class EvaluatorService:
    """Top-level orchestrator. Holds the session + collaborators.

    The session is reused across the call to keep transactional
    semantics with any ``approval_decision`` write. Construct one per
    request; the retrieval service it builds is cheap.
    """

    def __init__(
        self,
        session: Session,
        *,
        retrieval_service: RetrievalService | None = None,
        constraint_extractor: ConstraintExtractor | None = None,
        taxonomy: Taxonomy | None = None,
    ) -> None:
        self.session = session
        self.retrieval = retrieval_service or RetrievalService(session)
        self.extractor = constraint_extractor or RegexConstraintExtractor()
        self.taxonomy = taxonomy or load_taxonomy()

    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        notes: list[str] = []
        taxonomy_version = request.taxonomy_version or self.taxonomy.version
        if taxonomy_version != self.taxonomy.version:
            notes.append(
                f"taxonomy_version mismatch: caller requested {taxonomy_version!r} "
                f"but evaluator is running {self.taxonomy.version!r}; "
                "results pinned to the running version."
            )

        attributes_by_key = {a.attribute_key: a for a in request.attributes}
        attribute_results: list[AttributeResult] = []
        for submitted in request.attributes:
            attribute_def = _try_get_attribute(self.taxonomy, submitted.attribute_key)
            if attribute_def is None:
                attribute_results.append(
                    AttributeResult(
                        attribute_key=submitted.attribute_key,
                        submitted_value=submitted.value,
                        applicable_clauses=[],
                        verdict="uncertain",
                        notes=[
                            f"attribute_key {submitted.attribute_key!r} is not in "
                            f"taxonomy version {self.taxonomy.version!r}; cannot evaluate."
                        ],
                    )
                )
                continue
            result = self._evaluate_attribute(
                submitted=submitted,
                attribute_def=attribute_def,
                request=request,
                attributes_by_key=attributes_by_key,
            )
            attribute_results.append(result)

        unevaluated = self._compute_unevaluated_attributes(
            request=request, attribute_results=attribute_results
        )
        overall = _aggregate_overall_status(attribute_results, unevaluated)
        response = EvaluationResponse(
            overall_status=overall,
            attribute_results=attribute_results,
            unevaluated_attributes=unevaluated,
            notes=notes,
            taxonomy_version=self.taxonomy.version,
        )

        if request.submission_id is not None and request.persist_decision:
            response.approval_decision_id = self._persist_decision(
                request=request, response=response
            )
        return response

    # -- per-attribute evaluation ---------------------------------------

    def _evaluate_attribute(
        self,
        *,
        submitted: SubmissionAttributeInput,
        attribute_def: AttributeDefinition,
        request: EvaluationRequest,
        attributes_by_key: dict[str, SubmissionAttributeInput],
    ) -> AttributeResult:
        matches = self._retrieve_candidates(
            attribute_def=attribute_def,
            request=request,
        )
        clauses: list[ClauseEvaluation] = []
        attribute_notes: list[str] = []
        for match in matches:
            clause = self._evaluate_clause(
                match=match,
                submitted=submitted,
                attribute_def=attribute_def,
                attributes_by_key=attributes_by_key,
            )
            clauses.append(clause)
            if clause.verdict == "uncertain" and clause.constraint and clause.constraint.conditional_on:
                missing = [
                    c
                    for c in clause.constraint.conditional_on
                    if c not in attributes_by_key
                ]
                if missing:
                    attribute_notes.append(
                        f"clause {clause.citation_path or clause.fragment_id} is conditional "
                        f"on missing inputs: {sorted(set(missing))}"
                    )

        verdict = _aggregate_attribute_verdict(clauses, has_clauses=bool(matches))
        delta = _aggregate_attribute_delta(submitted=submitted, clauses=clauses)

        if not matches:
            attribute_notes.append(
                f"no bylaw clauses tagged with {attribute_def.id!r} in the configured "
                "scope; treating attribute as uncertain."
            )

        return AttributeResult(
            attribute_key=submitted.attribute_key,
            submitted_value=submitted.value,
            applicable_clauses=clauses,
            verdict=verdict,
            delta=delta,
            notes=attribute_notes,
        )

    def _retrieve_candidates(
        self,
        *,
        attribute_def: AttributeDefinition,
        request: EvaluationRequest,
    ) -> list[RetrievalMatch]:
        # The text channel query is the attribute's description plus
        # the first few bylaw keywords — a stable, low-cardinality
        # phrase that the existing scorer handles well. The
        # attribute_tag_filter is the hard pre-filter; the text query
        # only ranks within it.
        query = " ".join(
            [attribute_def.description, *list(attribute_def.bylaw_tag_keywords)[:4]]
        ).strip()
        retrieval_request = RetrievalRequest(
            query=query or attribute_def.id,
            location=request.location,
            attribute_tag_filter=[attribute_def.id],
            limit=request.per_attribute_limit,
            municipality=(
                request.document_filters.municipality if request.document_filters else None
            ),
            bylaw_name=(
                request.document_filters.bylaw_name if request.document_filters else None
            ),
            citation_path_prefix=(
                request.document_filters.citation_path_prefix
                if request.document_filters
                else None
            ),
            document_id=(
                request.document_filters.document_id if request.document_filters else None
            ),
            # Keep response shape small; the evaluator doesn't need
            # ancestor chains / tables / cross-refs to compute verdicts.
            include_context=False,
            include_cross_references=False,
            include_tables=False,
            include_datasets=False,
        )
        response = self.retrieval.search(retrieval_request)
        return response.matches

    def _evaluate_clause(
        self,
        *,
        match: RetrievalMatch,
        submitted: SubmissionAttributeInput,
        attribute_def: AttributeDefinition,
        attributes_by_key: dict[str, SubmissionAttributeInput],
    ) -> ClauseEvaluation:
        constraint = self._get_or_extract_constraint(
            match=match, attribute_def=attribute_def
        )
        notes: list[str] = []
        verdict, delta = _verdict_from_constraint(
            constraint=constraint,
            submitted=submitted,
            attributes_by_key=attributes_by_key,
            notes=notes,
        )
        return ClauseEvaluation(
            fragment_id=match.fragment_id,
            citation_path=match.citation_path,
            document_id=match.document_id,
            municipality=match.municipality,
            bylaw_name=match.bylaw_name,
            text=match.text,
            score=match.score,
            retrieval_channels=list(match.retrieval_channels),
            constraint=constraint,
            verdict=verdict,
            delta=delta,
            notes=notes,
        )

    # -- caching --------------------------------------------------------

    def _get_or_extract_constraint(
        self, *, match: RetrievalMatch, attribute_def: AttributeDefinition
    ) -> ConstraintLiteral | None:
        fragment = self.session.get(SourceFragment, match.fragment_id)
        if fragment is None:
            return None
        cache_key = _constraint_cache_key(
            text=fragment.text,
            taxonomy_version=self.taxonomy.version,
            attribute_id=attribute_def.id,
            extractor_name=self.extractor.name,
        )
        cache = (fragment.metadata_json or {}).get(CONSTRAINT_CACHE_KEY) or {}
        if cache_key in cache:
            return _constraint_from_json(cache[cache_key])

        citation_context = _format_citation_context(match)
        constraint = self.extractor.extract(
            clause_text=fragment.text,
            citation_context=citation_context,
            attribute=attribute_def,
        )
        # Persist (None included) so subsequent calls don't re-attempt.
        new_metadata = dict(fragment.metadata_json or {})
        new_cache = dict(cache)
        new_cache[cache_key] = _constraint_to_json(constraint)
        new_metadata[CONSTRAINT_CACHE_KEY] = new_cache
        fragment.metadata_json = new_metadata
        return constraint

    # -- persistence ----------------------------------------------------

    def _persist_decision(
        self, *, request: EvaluationRequest, response: EvaluationResponse
    ) -> int | None:
        submission = self.session.get(Submission, request.submission_id)
        if submission is None:
            response.notes.append(
                f"submission_id={request.submission_id} not found; "
                "decision not persisted."
            )
            return None
        # Flip status to EVALUATED so the UI can show a recent run.
        submission.status = SubmissionStatus.EVALUATED
        decision = ApprovalDecision(
            submission_id=submission.id,
            evaluator_version=EVALUATOR_VERSION,
            decision_summary_json=response.to_json(),
            notes=" ".join(response.notes) or None,
        )
        self.session.add(decision)
        self.session.flush()
        return decision.id

    def _compute_unevaluated_attributes(
        self,
        *,
        request: EvaluationRequest,
        attribute_results: list[AttributeResult],
    ) -> list[str]:
        """Identify regulated attributes the submission didn't supply.

        v1 heuristic: every attribute in the taxonomy. The full
        zone-applicability lookup (which set of attributes is
        regulated for *this* zone) is Phase 4 — until then, we report
        "missing inputs" for any taxonomy attribute the submission
        didn't supply that has tagged clauses in the corpus.

        Querying for tagged clauses per missing attribute would
        balloon LLM cost on each evaluation; instead we report
        attributes that are referenced by a clause we surfaced
        (i.e. ``conditional_on`` on any extracted constraint).
        """
        supplied = {r.attribute_key for r in attribute_results}
        referenced: set[str] = set()
        for result in attribute_results:
            for clause in result.applicable_clauses:
                if clause.constraint and clause.constraint.conditional_on:
                    referenced.update(clause.constraint.conditional_on)
        missing = sorted(referenced - supplied)
        return missing


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _try_get_attribute(taxonomy: Taxonomy, key: str) -> AttributeDefinition | None:
    try:
        return taxonomy.by_id(key)
    except KeyError:
        return None


def _format_citation_context(match: RetrievalMatch) -> str:
    parts: list[str] = []
    for ancestor in match.ancestor_chain or []:
        parts.append(
            f"{ancestor.citation_path or '[uncited]'}: {ancestor.text[:200].rstrip()}"
        )
    return "\n".join(parts)


def _constraint_cache_key(
    *,
    text: str,
    taxonomy_version: str,
    attribute_id: str,
    extractor_name: str,
) -> str:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{taxonomy_version}|{extractor_name}|{attribute_id}|{text_hash}"


def _constraint_to_json(constraint: ConstraintLiteral | None) -> dict[str, Any] | None:
    if constraint is None:
        return None
    return {
        "operator": constraint.operator,
        "value": constraint.value,
        "unit": constraint.unit,
        "condition": constraint.condition,
        "confidence": constraint.confidence,
        "conditional_on": list(constraint.conditional_on),
    }


def _constraint_from_json(raw: dict[str, Any] | None) -> ConstraintLiteral | None:
    if raw is None:
        return None
    return ConstraintLiteral(
        operator=raw.get("operator"),
        value=raw.get("value"),
        unit=raw.get("unit"),
        condition=raw.get("condition"),
        confidence=float(raw.get("confidence", 1.0)),
        conditional_on=list(raw.get("conditional_on") or []),
    )


def _verdict_from_constraint(
    *,
    constraint: ConstraintLiteral | None,
    submitted: SubmissionAttributeInput,
    attributes_by_key: dict[str, SubmissionAttributeInput],
    notes: list[str],
) -> tuple[Verdict, dict[str, Any] | None]:
    if constraint is None or constraint.operator is None or constraint.operator == "unknown":
        notes.append("constraint not extractable from clause; verdict is uncertain.")
        return "uncertain", None

    # Conditional-on: clause references attributes the submission
    # didn't carry. Verdict is uncertain; ``unevaluated_attributes``
    # carries the missing keys upward.
    if constraint.conditional_on:
        missing = [c for c in constraint.conditional_on if c not in attributes_by_key]
        if missing:
            notes.append(
                f"clause is conditional on missing inputs: {sorted(set(missing))}"
            )
            return "uncertain", None

    try:
        submitted_value = float(submitted.value) if _is_numericish(submitted.value) else submitted.value
    except (TypeError, ValueError):
        notes.append(f"submitted value {submitted.value!r} not coercible to a number.")
        return "uncertain", None

    operator = constraint.operator
    required = constraint.value
    if operator in {">", ">=", "<", "<=", "=="}:
        try:
            required_num = float(required)
        except (TypeError, ValueError):
            notes.append("clause constraint value is not numeric; cannot compute delta.")
            return "uncertain", None
        return _numeric_verdict(
            operator=operator,
            submitted=submitted_value,
            required=required_num,
            unit=constraint.unit,
            submitted_unit=submitted.unit,
        )
    if operator == "between":
        if not isinstance(required, (list, tuple)) or len(required) != 2:
            notes.append("between operator requires [low, high].")
            return "uncertain", None
        low, high = float(required[0]), float(required[1])
        if low <= submitted_value <= high:
            return "pass", {
                "required_min": low,
                "required_max": high,
                "submitted": submitted_value,
                "unit": constraint.unit,
            }
        shortfall = low - submitted_value if submitted_value < low else submitted_value - high
        return "fail", {
            "required_min": low,
            "required_max": high,
            "submitted": submitted_value,
            "shortfall": abs(shortfall),
            "unit": constraint.unit,
        }
    if operator == "in":
        if not isinstance(required, (list, tuple)):
            notes.append("'in' operator requires a list of allowed values.")
            return "uncertain", None
        if submitted_value in required or submitted.value in required:
            return "pass", {
                "allowed": list(required),
                "submitted": submitted.value,
            }
        return "fail", {
            "allowed": list(required),
            "submitted": submitted.value,
        }
    notes.append(f"unknown operator {operator!r}; verdict uncertain.")
    return "uncertain", None


def _numeric_verdict(
    *,
    operator: str,
    submitted: float,
    required: float,
    unit: str | None,
    submitted_unit: str | None,
) -> tuple[Verdict, dict[str, Any]]:
    delta: dict[str, Any] = {
        "operator": operator,
        "required": required,
        "submitted": submitted,
        "unit": unit,
    }
    if submitted_unit and unit and submitted_unit != unit:
        # v1 doesn't do unit conversion; surface the mismatch as
        # uncertain rather than producing a wrong delta.
        delta["unit_mismatch"] = {"submitted_unit": submitted_unit, "required_unit": unit}
        return "uncertain", delta
    if operator == ">":
        ok = submitted > required
    elif operator == ">=":
        ok = submitted >= required
    elif operator == "<":
        ok = submitted < required
    elif operator == "<=":
        ok = submitted <= required
    elif operator == "==":
        ok = math.isclose(submitted, required, rel_tol=1e-9, abs_tol=1e-9)
    else:
        return "uncertain", delta
    if ok:
        return "pass", delta
    # Compute shortfall / excess so the UI can render "you need 1.3 m
    # more setback" without re-deriving from the operator.
    if operator in {">", ">="}:
        delta["shortfall"] = round(required - submitted, 4)
    elif operator in {"<", "<="}:
        delta["excess"] = round(submitted - required, 4)
    elif operator == "==":
        delta["difference"] = round(submitted - required, 4)
    return "fail", delta


def _aggregate_attribute_verdict(
    clauses: Iterable[ClauseEvaluation], *, has_clauses: bool
) -> Verdict:
    """Combine clause-level verdicts into a per-attribute verdict.

    Policy: any clause-level ``fail`` makes the attribute ``fail``.
    Otherwise, any ``uncertain`` (or no clauses at all) makes it
    ``uncertain``. Only all-pass aggregates to ``pass``. This mirrors
    how a planner would reason about it: a single clause prohibiting
    something is enough to block approval, but a single approving
    clause isn't enough to override unknowns.
    """
    clauses_list = list(clauses)
    if not has_clauses or not clauses_list:
        return "uncertain"
    if any(c.verdict == "fail" for c in clauses_list):
        return "fail"
    if any(c.verdict == "uncertain" for c in clauses_list):
        return "uncertain"
    return "pass"


def _aggregate_attribute_delta(
    *, submitted: SubmissionAttributeInput, clauses: list[ClauseEvaluation]
) -> dict[str, Any] | None:
    """Pick the most-impactful clause-level delta as the attribute delta.

    Heuristic: pick the largest shortfall / excess / difference among
    failing clauses, otherwise the highest-confidence passing clause's
    delta. Returns None when no clause produced a structured delta.
    """
    failing = [c for c in clauses if c.verdict == "fail" and c.delta]
    if failing:
        def magnitude(c: ClauseEvaluation) -> float:
            d = c.delta or {}
            for key in ("shortfall", "excess", "difference"):
                if key in d:
                    try:
                        return float(d[key])
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0

        worst = max(failing, key=magnitude)
        return worst.delta
    passing = [c for c in clauses if c.verdict == "pass" and c.delta]
    if passing:
        return passing[0].delta
    return None


def _aggregate_overall_status(
    attribute_results: list[AttributeResult],
    unevaluated_attributes: list[str],
) -> OverallStatus:
    """Aggregate per-attribute verdicts plus missing-input flags.

    Policy:
    * any per-attribute ``fail`` → ``non_compliant``.
    * else any ``uncertain`` and no ``fail`` → ``uncertain``.
    * else all ``pass`` and no missing inputs → ``compliant``.
    * else missing inputs but no ``fail``/``uncertain`` → ``incomplete``.

    "incomplete" is reserved for the "we couldn't evaluate everything
    we should have because the submission was missing X" case. It's
    a distinct signal from ``uncertain`` because it tells the agent
    to ASK the user for more inputs.
    """
    if any(r.verdict == "fail" for r in attribute_results):
        return "non_compliant"
    if any(r.verdict == "uncertain" for r in attribute_results):
        return "uncertain"
    if unevaluated_attributes:
        return "incomplete"
    return "compliant"


def _is_numericish(value: Any) -> bool:
    if isinstance(value, bool):
        return False  # bools coerce to 0/1 unhelpfully here
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False
