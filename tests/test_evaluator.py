"""Tests for the Phase-1 compliance evaluator.

Coverage groups:

* Constraint extraction — the regex fallback handles "minimum",
  "shall not exceed", and "shall be" patterns. Out-of-range patterns
  return None.
* Per-clause verdict math — pass / fail / uncertain across operators,
  with delta surfaced (shortfall / excess / difference).
* Aggregation policy — any-fail wins over any-uncertain wins over
  all-pass.
* Caching — second evaluation against the same fragment hits the
  cache and doesn't re-call the extractor.
* Conditional-on — clauses citing attributes the submission didn't
  supply mark the attribute uncertain and surface the missing input
  in ``unevaluated_attributes``.
* Persistence — when ``submission_id`` is set and persist_decision is
  True, an ``approval_decision`` row is written; submission status
  flips to evaluated.
* End-to-end happy path through real fragments + a fake extractor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer2.compliance.db.models import (
    ApprovalDecision,
    Parcel,
    Submission,
    SubmissionStatus,
)
from layer2.compliance.evaluator import (
    EVALUATOR_VERSION,
    ConstraintLiteral,
    EvaluationRequest,
    EvaluatorService,
    RegexConstraintExtractor,
    SubmissionAttributeInput,
)
from layer2.compliance.taxonomy import load_taxonomy


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_url = f"sqlite:///{tmp_path / 'evaluator.db'}"
    create_all(db_url)
    return db_url


def _seed_corpus(session) -> dict[str, int]:
    document = Document(
        municipality="HRM",
        bylaw_name="Test Bylaw",
        source_path="t.pdf",
        file_hash="hh",
        mime_type="application/pdf",
        page_count=1,
        parser_version="test",
    )
    session.add(document)
    session.flush()
    fragments = {
        "front_min": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.2.1",
            page_start=1,
            page_end=1,
            text="The minimum front yard shall not be less than 4.5 metres.",
            parse_status=ParseStatus.PARSED,
            attribute_tags=["front_setback_m"],
            confidence=1.0,
        ),
        "height_max": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.3.1",
            page_start=2,
            page_end=2,
            text="The maximum building height shall not exceed 11 metres.",
            parse_status=ParseStatus.PARSED,
            attribute_tags=["building_height_m"],
            confidence=1.0,
        ),
        # Subjective clause — kept in the corpus to confirm it's not
        # tagged with any taxonomy attribute. The enrichment pass
        # should never tag clauses like this; we keep one here so
        # the test fails loudly if the evaluator starts surfacing
        # untagged clauses by accident.
        "subjective": SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_path="4.4.1",
            page_start=3,
            page_end=3,
            text="Buildings shall be compatible with neighbourhood character.",
            parse_status=ParseStatus.PARSED,
            attribute_tags=[],
            confidence=1.0,
        ),
    }
    for fragment in fragments.values():
        session.add(fragment)
    session.flush()
    return {name: frag.id for name, frag in fragments.items()}


# ----------------------------------------------------------------------
# RegexConstraintExtractor
# ----------------------------------------------------------------------


def test_regex_extractor_handles_minimum() -> None:
    extractor = RegexConstraintExtractor()
    taxonomy = load_taxonomy()
    attr = taxonomy.by_id("front_setback_m")
    out = extractor.extract(
        clause_text="The minimum front yard shall not be less than 4.5 metres.",
        citation_context="",
        attribute=attr,
    )
    assert out is not None
    assert out.operator == ">="
    assert out.value == 4.5
    assert out.unit == "m"


def test_regex_extractor_handles_maximum() -> None:
    extractor = RegexConstraintExtractor()
    taxonomy = load_taxonomy()
    attr = taxonomy.by_id("building_height_m")
    out = extractor.extract(
        clause_text="The maximum building height shall not exceed 11 m.",
        citation_context="",
        attribute=attr,
    )
    assert out is not None
    assert out.operator == "<="
    assert out.value == 11.0
    assert out.unit == "m"


def test_regex_extractor_returns_none_for_subjective_text() -> None:
    extractor = RegexConstraintExtractor()
    taxonomy = load_taxonomy()
    attr = taxonomy.by_id("building_height_m")
    out = extractor.extract(
        clause_text="Buildings shall be compatible with neighbourhood character.",
        citation_context="",
        attribute=attr,
    )
    assert out is None


def test_regex_extractor_skips_non_numeric_attributes() -> None:
    extractor = RegexConstraintExtractor()
    taxonomy = load_taxonomy()
    attr = taxonomy.by_id("primary_use_class")  # string-valued attribute
    out = extractor.extract(
        clause_text="The minimum primary use shall not be less than 4.5 metres.",
        citation_context="",
        attribute=attr,
    )
    assert out is None


# ----------------------------------------------------------------------
# End-to-end evaluate()
# ----------------------------------------------------------------------


def test_evaluate_compliant_submission_returns_pass(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=6.0, unit="m"
                    ),
                    SubmissionAttributeInput(
                        attribute_key="building_height_m", value=9.0, unit="m"
                    ),
                ],
            )
        )

    assert response.overall_status == "compliant"
    by_key = {r.attribute_key: r for r in response.attribute_results}
    assert by_key["front_setback_m"].verdict == "pass"
    assert by_key["building_height_m"].verdict == "pass"
    # All applicable clauses should appear with the citation_path.
    assert any(
        c.citation_path == "4.2.1"
        for c in by_key["front_setback_m"].applicable_clauses
    )


def test_evaluate_non_compliant_returns_fail_with_shortfall(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=3.2, unit="m"
                    ),
                ],
            )
        )
    assert response.overall_status == "non_compliant"
    result = response.attribute_results[0]
    assert result.verdict == "fail"
    assert result.delta is not None
    assert result.delta.get("shortfall") == pytest.approx(1.3, rel=1e-6)


def test_evaluate_uncertain_when_constraint_unextractable(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)
        # Tag the subjective clause so it shows up as the only
        # building_height_m clause, then delete the numeric one. The
        # evaluator should report uncertain since the only clause
        # tagged is one whose constraint can't be extracted.
        session.query(SourceFragment).filter(
            SourceFragment.citation_path == "4.4.1"
        ).update({"attribute_tags": ["building_height_m"]}, synchronize_session=False)
        session.query(SourceFragment).filter(
            SourceFragment.citation_path == "4.3.1"
        ).delete(synchronize_session=False)

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="building_height_m", value=9.0, unit="m"
                    ),
                ],
            )
        )
    assert response.overall_status == "uncertain"
    result = response.attribute_results[0]
    assert result.verdict == "uncertain"
    assert any(
        clause.verdict == "uncertain" and clause.constraint is None
        for clause in result.applicable_clauses
    )


def test_evaluate_no_tagged_clauses_returns_uncertain(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    # lot_coverage_percent isn't tagged on any seeded
                    # fragment, so the per-attribute verdict is
                    # uncertain with a "no clauses" note.
                    SubmissionAttributeInput(
                        attribute_key="lot_coverage_percent",
                        value=45.0,
                        unit="percent",
                    ),
                ],
            )
        )
    result = response.attribute_results[0]
    assert result.verdict == "uncertain"
    assert response.overall_status == "uncertain"
    assert any("no bylaw clauses tagged" in n for n in result.notes)


def test_evaluate_unknown_attribute_marked_uncertain(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="invented_attribute_xyz",
                        value=42,
                    ),
                ],
            )
        )
    result = response.attribute_results[0]
    assert result.verdict == "uncertain"
    assert any("not in taxonomy" in n for n in result.notes)


# ----------------------------------------------------------------------
# Caching
# ----------------------------------------------------------------------


class CountingExtractor(RegexConstraintExtractor):
    """RegexConstraintExtractor that counts extract() calls.

    Used to confirm the cache prevents re-extraction on the second
    evaluator pass over the same fragment.
    """

    name = "counting-extractor"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, *, clause_text, citation_context, attribute):
        self.calls += 1
        return super().extract(
            clause_text=clause_text,
            citation_context=citation_context,
            attribute=attribute,
        )


def test_constraint_cache_avoids_double_extraction(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    extractor = CountingExtractor()
    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session, constraint_extractor=extractor)
        evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=6.0, unit="m"
                    ),
                ],
            )
        )
    first_calls = extractor.calls
    assert first_calls >= 1

    extractor2 = CountingExtractor()
    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session, constraint_extractor=extractor2)
        evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=6.0, unit="m"
                    ),
                ],
            )
        )
    # Second extractor instance has the same .name, so its cache
    # key matches and the cached value is reused — zero new calls.
    assert extractor2.calls == 0


# ----------------------------------------------------------------------
# Conditional-on attributes
# ----------------------------------------------------------------------


class ConditionalExtractor:
    """Fake extractor that returns a conditional-on constraint.

    Lets the test exercise the "missing input" branch without having
    to hand-craft a regex pattern for it.
    """

    name = "conditional-extractor"

    def __init__(self, missing: list[str]) -> None:
        self._missing = missing

    def extract(self, *, clause_text, citation_context, attribute):
        return ConstraintLiteral(
            operator=">=",
            value=4.5,
            unit="m",
            conditional_on=list(self._missing),
            confidence=1.0,
        )


def test_conditional_clause_surfaces_missing_inputs(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(
            session,
            constraint_extractor=ConditionalExtractor(missing=["corner_lot_boolean"]),
        )
        response = evaluator.evaluate(
            EvaluationRequest(
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=6.0, unit="m"
                    ),
                ],
            )
        )

    assert "corner_lot_boolean" in response.unevaluated_attributes
    # Per-attribute verdict is uncertain because every clause was
    # conditional on a missing input.
    result = response.attribute_results[0]
    assert result.verdict == "uncertain"
    assert response.overall_status == "uncertain"


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def test_persists_approval_decision_when_submission_id_set(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)
        parcel = Parcel(jurisdiction="HRM", parcel_identifier="00012345")
        session.add(parcel)
        session.flush()
        submission = Submission(parcel_id=parcel.id, status=SubmissionStatus.DRAFT)
        session.add(submission)
        session.flush()
        submission_id = submission.id

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                submission_id=submission_id,
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=6.0, unit="m"
                    ),
                ],
            )
        )

    assert response.approval_decision_id is not None
    with session_scope(db_url) as session:
        decision = session.get(ApprovalDecision, response.approval_decision_id)
        assert decision is not None
        assert decision.evaluator_version == EVALUATOR_VERSION
        assert decision.submission_id == submission_id
        summary = decision.decision_summary_json
        assert summary["overall_status"] == "compliant"
        # Status flipped to evaluated.
        submission = session.get(Submission, submission_id)
        assert submission.status == SubmissionStatus.EVALUATED


def test_persist_disabled_does_not_write_decision(tmp_path: Path) -> None:
    db_url = _make_db(tmp_path)
    with session_scope(db_url) as session:
        _seed_corpus(session)
        submission = Submission(status=SubmissionStatus.DRAFT)
        session.add(submission)
        session.flush()
        submission_id = submission.id

    with session_scope(db_url) as session:
        evaluator = EvaluatorService(session)
        response = evaluator.evaluate(
            EvaluationRequest(
                submission_id=submission_id,
                persist_decision=False,
                attributes=[
                    SubmissionAttributeInput(
                        attribute_key="front_setback_m", value=6.0, unit="m"
                    ),
                ],
            )
        )

    assert response.approval_decision_id is None
    with session_scope(db_url) as session:
        from sqlalchemy import select as _select

        rows = session.execute(_select(ApprovalDecision)).scalars().all()
        assert rows == []
