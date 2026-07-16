"""Unit tests for the general-feedback router (ABS-129 + ABS-215).

ABS-129 introduced categorized general feedback (UX issue, feature request,
…). ABS-215 adds a global free-text widget that submits feedback with NO
category. These tests pin both paths against a real sqlite-backed router:

* a request with no category is stored as ``"uncategorized"`` so the
  downstream LLM categorization pipeline knows it still needs a label;
* a request with a valid category keeps it (ABS-129 parity);
* an empty message is rejected (422);
* an unknown category is rejected (422).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from advisor.api.general_feedback_router import build_general_feedback_router
from advisor.db.models import GeneralFeedback, User
from layer1.db.base import Base

CLERK_SUB = "user_e2e"


@pytest.fixture()
def db_session_factory():
    # Single shared in-memory sqlite engine (StaticPool) so the seeded user,
    # the route handler, and the assertion phase all see the same rows.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with SessionLocal() as seed:
        seed.add(User(clerk_user_id=CLERK_SUB, email="user@e2e.test"))
        seed.commit()

    @contextmanager
    def factory():
        session: Session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    return factory


@pytest.fixture()
def client(db_session_factory):
    def user_dependency() -> dict[str, Any]:
        return {"sub": CLERK_SUB}

    def user_resolver(auth_session: dict[str, Any], db: Session) -> User:
        return (
            db.query(User)
            .filter(User.clerk_user_id == auth_session["sub"])
            .one()
        )

    app = FastAPI()
    app.include_router(
        build_general_feedback_router(
            db_session_factory=db_session_factory,
            user_dependency=user_dependency,
            user_resolver=user_resolver,
        )
    )
    return TestClient(app)


def test_submit_without_category_defaults_to_uncategorized(client, db_session_factory):
    resp = client.post(
        "/v1/feedback",
        json={"message": "Your pricing model doesn't work for me."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["category"] == "uncategorized"

    with db_session_factory() as db:
        rows = db.query(GeneralFeedback).all()
    assert len(rows) == 1
    assert rows[0].message == "Your pricing model doesn't work for me."
    assert rows[0].category == "uncategorized"


def test_submit_with_valid_category_is_preserved(client):
    resp = client.post(
        "/v1/feedback",
        json={"category": "feature_request", "message": "Please add dark mode."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["category"] == "feature_request"


def test_empty_message_is_rejected(client):
    resp = client.post("/v1/feedback", json={"message": ""})
    assert resp.status_code == 422


def test_unknown_category_is_rejected(client):
    resp = client.post(
        "/v1/feedback",
        json={"category": "bogus", "message": "hi"},
    )
    assert resp.status_code == 422
