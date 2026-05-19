"""Unit coverage for the T&C version + acceptance module.

Pins three things the click-wrap gate hinges on:

* Editing the document changes the hash that future acceptances bind
  to (re-prompt-on-amendment mechanic).
* ``user_has_accepted_current_terms`` distinguishes accept-for-this-
  version from accept-for-some-other-version.
* ``record_acceptance`` is idempotent under the unique constraint so
  a retried POST doesn't 500 on duplicate insert.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from advisor.db.models import TermsAcceptance, User
from advisor.legal import (
    _load_terms,
    get_current_terms,
    record_acceptance,
    set_terms_document_path_for_tests,
    user_has_accepted_current_terms,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'legal.db'}"


@pytest.fixture
def custom_terms(tmp_path: Path):
    """Point ``_load_terms`` at a temp markdown file and clear the cache.

    The fixture yields a callable ``set_body(text) -> hash`` so a test
    can mutate the live document mid-test and assert that the hash
    changes — the same mechanic that re-prompts users on a material
    amendment in production.
    """
    doc = tmp_path / "TERMS.md"

    def set_body(text: str) -> str:
        doc.write_text(text, encoding="utf-8")
        _load_terms.cache_clear()
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    set_terms_document_path_for_tests(doc)
    try:
        yield set_body
    finally:
        set_terms_document_path_for_tests(None)
        _load_terms.cache_clear()


def _seed_user(db) -> User:
    user = User(
        clerk_user_id="user_legal_1",
        email="legal@example.com",
        full_name="Legal Tester",
    )
    db.add(user)
    db.flush()
    return user


def test_editing_the_document_changes_the_current_version(custom_terms) -> None:
    v1 = custom_terms("# Terms\n\nVersion one body.\n")
    assert get_current_terms().version_hash == v1

    v2 = custom_terms("# Terms\n\nVersion two body — amended.\n")
    assert get_current_terms().version_hash == v2
    assert v1 != v2


def test_user_has_accepted_current_only_when_hash_matches(
    custom_terms, tmp_path: Path
) -> None:
    v1 = custom_terms("# Terms v1\n")
    db_url = _db_url(tmp_path)
    create_all(db_url)

    with session_scope(db_url) as db:
        user = _seed_user(db)
        # No row → not accepted.
        assert user_has_accepted_current_terms(db, user) is False

        # Insert a v1 acceptance → accepted while v1 is live.
        record_acceptance(
            db, user=user, version_hash=v1, ip="127.0.0.1", user_agent="ua/1"
        )
        db.commit()
        assert user_has_accepted_current_terms(db, user) is True

    # Now edit the document — v1's hash is stale, the user no longer
    # satisfies the gate against v2.
    custom_terms("# Terms v2 — amended\n")
    with session_scope(db_url) as db:
        user = db.query(User).filter(User.clerk_user_id == "user_legal_1").one()
        assert user_has_accepted_current_terms(db, user) is False


def test_record_acceptance_is_idempotent_on_repeated_calls(
    custom_terms, tmp_path: Path
) -> None:
    v1 = custom_terms("# Terms v1\n")
    db_url = _db_url(tmp_path)
    create_all(db_url)

    with session_scope(db_url) as db:
        user = _seed_user(db)
        row1 = record_acceptance(
            db, user=user, version_hash=v1, ip="1.1.1.1", user_agent="ua/1"
        )
        db.commit()

        row2 = record_acceptance(
            db, user=user, version_hash=v1, ip="2.2.2.2", user_agent="ua/2"
        )
        db.commit()

        # Same row returned the second time — no new insert, original
        # ip/ua are preserved (we don't overwrite evidence on retry).
        assert row1.id == row2.id
        count = (
            db.query(TermsAcceptance)
            .filter(TermsAcceptance.user_id == user.id)
            .count()
        )
        assert count == 1
        assert row1.ip == "1.1.1.1"
        assert row1.user_agent == "ua/1"


def test_record_acceptance_captures_ip_and_user_agent(
    custom_terms, tmp_path: Path
) -> None:
    v1 = custom_terms("# Terms\n")
    db_url = _db_url(tmp_path)
    create_all(db_url)

    with session_scope(db_url) as db:
        user = _seed_user(db)
        row = record_acceptance(
            db,
            user=user,
            version_hash=v1,
            ip="203.0.113.5",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) GoodBrowser/9.0",
        )
        db.commit()
        assert row.ip == "203.0.113.5"
        assert row.user_agent.startswith("Mozilla/5.0")
        assert row.version_hash == v1
        assert row.accepted_at is not None
