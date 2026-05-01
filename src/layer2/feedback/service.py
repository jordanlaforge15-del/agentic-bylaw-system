from __future__ import annotations

from sqlalchemy.orm import Session

from layer2.db.models import AnswerFeedback, ClaimFeedback, RetrievalFeedback
from layer2.models.enums import VerificationStatus


def submit_answer_feedback(
    session: Session,
    *,
    answer_log_id: int,
    rating: int | None,
    is_correct: bool | None,
    is_incomplete: bool | None,
    notes: str | None,
) -> AnswerFeedback:
    feedback = AnswerFeedback(
        answer_log_id=answer_log_id,
        rating=rating,
        is_correct=is_correct,
        is_incomplete=is_incomplete,
        notes=notes,
    )
    session.add(feedback)
    session.flush()
    return feedback


def submit_claim_feedback(
    session: Session,
    *,
    generated_claim,
    is_correct: bool | None,
    corrected_value_text: str | None,
    corrected_structured_json: dict | None,
    notes: str | None,
    reviewer_type: str | None,
) -> ClaimFeedback:
    feedback = ClaimFeedback(
        generated_claim_id=generated_claim.id,
        is_correct=is_correct,
        corrected_value_text=corrected_value_text,
        corrected_structured_json=corrected_structured_json or {},
        notes=notes,
        reviewer_type=reviewer_type,
    )
    if is_correct is True:
        generated_claim.verification_status = VerificationStatus.VERIFIED
    elif is_correct is False:
        generated_claim.verification_status = VerificationStatus.DISPUTED
    session.add(feedback)
    session.flush()
    return feedback


def submit_retrieval_feedback(
    session: Session,
    *,
    retrieval_run_id: int,
    missing_source_fragment_id: int | None,
    irrelevant_source_fragment_id: int | None,
    notes: str | None,
) -> RetrievalFeedback:
    feedback = RetrievalFeedback(
        retrieval_run_id=retrieval_run_id,
        missing_source_fragment_id=missing_source_fragment_id,
        irrelevant_source_fragment_id=irrelevant_source_fragment_id,
        notes=notes,
    )
    session.add(feedback)
    session.flush()
    return feedback
