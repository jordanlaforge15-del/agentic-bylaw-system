"""FastAPI router for chat message feedback (thumbs + flag).

Two endpoints, both auth-required:

* ``POST /v1/chat/sessions/{session_id}/messages/{message_id}/feedback``
  — upsert a feedback row (thumbs up/down and/or flag with reason).

* ``GET /v1/chat/sessions/{session_id}/feedback``
  — return all of the calling user's feedback for the given session,
  keyed by message_id. The frontend uses this to restore button state
  when switching sessions.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from advisor.db.models import (
    ChatMessage,
    ChatMessageFeedback,
    ChatSession,
    User,
)
from layer1.db.base import utcnow

logger = logging.getLogger(__name__)

UserResolver = Callable[[Any, Session], User]


class FeedbackRequest(BaseModel):
    rating: str | None = Field(
        default=None,
        description="'up' or 'down', or null to clear.",
    )
    flag_reason: str | None = Field(
        default=None,
        description="'bad_citation', 'wrong_zone', 'hallucination', or 'other'.",
    )
    flag_notes: str | None = Field(
        default=None,
        description="Free-text detail when flag_reason is set.",
        max_length=2000,
    )


class FeedbackItem(BaseModel):
    message_id: int
    rating: str | None = None
    flag_reason: str | None = None
    flag_notes: str | None = None


class SessionFeedbackResponse(BaseModel):
    feedback: list[FeedbackItem]


VALID_RATINGS = {"up", "down", None}
VALID_FLAG_REASONS = {"bad_citation", "wrong_zone", "hallucination", "other", None}


def build_feedback_router(
    *,
    db_session_factory: Callable[[], Any],
    user_dependency: Callable[..., Any],
    user_resolver: UserResolver,
) -> APIRouter:
    router = APIRouter(prefix="/v1/chat/sessions", tags=["feedback"])

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

    @router.post(
        "/{session_id}/messages/{message_id}/feedback",
        response_model=FeedbackItem,
    )
    def submit_feedback(
        session_id: int,
        message_id: int,
        body: FeedbackRequest,
        auth_session: Any = Depends(user_dependency),
    ) -> FeedbackItem:
        if body.rating not in VALID_RATINGS:
            raise HTTPException(
                status_code=422,
                detail=f"rating must be 'up', 'down', or null; got {body.rating!r}",
            )
        if body.flag_reason not in VALID_FLAG_REASONS:
            raise HTTPException(
                status_code=422,
                detail=f"flag_reason must be one of {VALID_FLAG_REASONS!r}",
            )

        with _open_db() as db:
            user = _resolve(auth_session, db)

            chat_session = db.get(ChatSession, session_id)
            if chat_session is None or chat_session.user_id != user.id:
                raise HTTPException(status_code=404, detail="Session not found")

            msg = db.get(ChatMessage, message_id)
            if msg is None or msg.session_id != session_id:
                raise HTTPException(status_code=404, detail="Message not found")

            existing = (
                db.query(ChatMessageFeedback)
                .filter(
                    ChatMessageFeedback.message_id == message_id,
                    ChatMessageFeedback.user_id == user.id,
                )
                .one_or_none()
            )

            if existing is not None:
                existing.rating = body.rating
                existing.flag_reason = body.flag_reason
                existing.flag_notes = body.flag_notes
                existing.updated_at = utcnow()
                db.flush()
                db.commit()
                return FeedbackItem(
                    message_id=message_id,
                    rating=existing.rating,
                    flag_reason=existing.flag_reason,
                    flag_notes=existing.flag_notes,
                )

            feedback = ChatMessageFeedback(
                message_id=message_id,
                user_id=user.id,
                rating=body.rating,
                flag_reason=body.flag_reason,
                flag_notes=body.flag_notes,
            )
            db.add(feedback)
            db.flush()
            db.commit()
            return FeedbackItem(
                message_id=message_id,
                rating=feedback.rating,
                flag_reason=feedback.flag_reason,
                flag_notes=feedback.flag_notes,
            )

    @router.get(
        "/{session_id}/feedback",
        response_model=SessionFeedbackResponse,
    )
    def get_session_feedback(
        session_id: int,
        auth_session: Any = Depends(user_dependency),
    ) -> SessionFeedbackResponse:
        with _open_db() as db:
            user = _resolve(auth_session, db)

            chat_session = db.get(ChatSession, session_id)
            if chat_session is None or chat_session.user_id != user.id:
                raise HTTPException(status_code=404, detail="Session not found")

            rows = (
                db.query(ChatMessageFeedback)
                .filter(
                    ChatMessageFeedback.user_id == user.id,
                    ChatMessageFeedback.message_id.in_(
                        db.query(ChatMessage.id).filter(
                            ChatMessage.session_id == session_id
                        )
                    ),
                )
                .all()
            )

            return SessionFeedbackResponse(
                feedback=[
                    FeedbackItem(
                        message_id=r.message_id,
                        rating=r.rating,
                        flag_reason=r.flag_reason,
                        flag_notes=r.flag_notes,
                    )
                    for r in rows
                ]
            )

    return router
