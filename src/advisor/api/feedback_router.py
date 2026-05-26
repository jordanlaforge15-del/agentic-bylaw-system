"""Router for general (non-message-specific) user feedback."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from advisor.db.models import GeneralFeedback, User

VALID_CATEGORIES = frozenset(
    ["ux_issue", "feature_request", "general", "bug_report"]
)


class FeedbackRequest(BaseModel):
    category: str = Field(
        ...,
        description="One of: ux_issue, feature_request, general, bug_report",
    )
    body: str = Field(..., min_length=1, max_length=5000)


class FeedbackResponse(BaseModel):
    id: int
    category: str
    created_at: str


def build_feedback_router(
    *,
    db_session_factory: Callable[[], AbstractContextManager[Session]],
    user_dependency: Callable[..., Any],
    user_resolver: Callable[[Any, Session], User],
) -> APIRouter:
    router = APIRouter(prefix="/v1/feedback", tags=["feedback"])

    @router.post("", response_model=FeedbackResponse)
    def submit_feedback(
        body: FeedbackRequest,
        user: Any = Depends(user_dependency),
    ) -> FeedbackResponse:
        if body.category not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_category",
                    "message": (
                        f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
                    ),
                },
            )

        with db_session_factory() as db:
            db_user = user_resolver(user, db)
            feedback = GeneralFeedback(
                user_id=db_user.id,
                category=body.category,
                body=body.body,
            )
            db.add(feedback)
            db.commit()
            db.refresh(feedback)
            return FeedbackResponse(
                id=feedback.id,
                category=feedback.category,
                created_at=feedback.created_at.isoformat(),
            )

    return router
