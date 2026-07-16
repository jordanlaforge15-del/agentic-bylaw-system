"""FastAPI router for general (non-message-specific) feedback (ABS-129, ABS-215).

Single endpoint, auth-required:

* ``POST /v1/feedback`` — submit a general feedback entry. The category is
  optional: the categorized sidebar widget (ABS-129) supplies one of the real
  categories, while the global free-text widget (ABS-215) submits with no
  category at all. A missing category is stored as ``"uncategorized"`` so the
  downstream LLM categorization pipeline knows the entry still needs a label.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from advisor.db.models import GeneralFeedback, User

logger = logging.getLogger(__name__)

UserResolver = Callable[[Any, Session], User]

# Placeholder stored when the submitter does not pick a category (ABS-215).
# The downstream LLM categorization pipeline overwrites it with a real label.
UNCATEGORIZED = "uncategorized"

VALID_CATEGORIES = {
    "ux_issue",
    "feature_request",
    "general_satisfaction",
    "other",
    UNCATEGORIZED,
}


class GeneralFeedbackRequest(BaseModel):
    category: str | None = Field(
        default=None,
        description=(
            "Optional. One of 'ux_issue', 'feature_request', "
            "'general_satisfaction', or 'other'. Omit it for unsolicited "
            "free-text feedback (ABS-215); the entry is then stored as "
            "'uncategorized' for downstream LLM categorization."
        ),
    )
    message: str = Field(
        description="Free-text feedback message.",
        min_length=1,
        max_length=2000,
    )


class GeneralFeedbackResponse(BaseModel):
    id: int
    category: str
    message: str


def build_general_feedback_router(
    *,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["general-feedback"])

    @contextmanager
    def _open_db() -> Any:
        result = db_session_factory()
        if hasattr(result, "__enter__"):
            with result as session:
                yield session
        else:
            try:
                yield result
            finally:
                close = getattr(result, "close", None)
                if callable(close):
                    close()

    def _resolve(auth_session: Any, db: Session) -> User:
        try:
            return user_resolver(auth_session, db)
        except LookupError as exc:
            raise HTTPException(status_code=401, detail="Unknown user") from exc

    @router.post("/feedback", response_model=GeneralFeedbackResponse)
    def submit_general_feedback(
        body: GeneralFeedbackRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> GeneralFeedbackResponse:
        # ABS-215: a missing category means unsolicited free-text feedback.
        # Store the placeholder rather than rejecting — the schema stays
        # NOT NULL and the LLM pipeline has a clear "needs a label" marker.
        category = body.category or UNCATEGORIZED
        if category not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail=f"category must be one of {sorted(VALID_CATEGORIES)!r}",
            )

        with _open_db() as db:
            user = _resolve(auth_session, db)

            feedback = GeneralFeedback(
                user_id=user.id,
                category=category,
                message=body.message,
            )
            db.add(feedback)
            db.flush()
            db.commit()
            return GeneralFeedbackResponse(
                id=feedback.id,
                category=feedback.category,
                message=feedback.message,
            )

    return router
