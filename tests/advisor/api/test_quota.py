"""HTTP-edge credit lifecycle: ``reserve_credit_for_session``.

The chat route calls this helper on every turn. The interesting paths
exercised here are the two non-claim branches — the session-bound
existing credit (resume) and the case-bound pre-reserved credit (first
turn after ``open_case`` reserved the credit but before any chat
session existed). Regression coverage for ABS-9, where the chat route
used to bypass the case-bound credit and 402 a user out of their last
available credit.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from advisor.api.quota import reserve_credit_for_session
from advisor.db.cases import (
    credit_balance_for,
    grant_admin_credits,
    open_case,
)
from advisor.db.models import Case, CaseCredit, CaseEvent, ChatSession, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _seed_user(db_url: str, *, clerk_user_id: str = "u1") -> int:
    with session_scope(db_url) as s:
        user = User(clerk_user_id=clerk_user_id, email=f"{clerk_user_id}@x.com")
        s.add(user)
        s.flush()
        return user.id


def _new_session_for(s, *, user_id: int) -> int:
    chat = ChatSession(user_id=user_id)
    s.add(chat)
    s.flush()
    return chat.id


def test_first_chat_after_open_case_adopts_the_case_reserved_credit(
    tmp_path: Path,
) -> None:
    """ABS-9 regression.

    Sequence: user has exactly one available credit. They open a case
    (which reserves it against the case). They then send the first
    chat message. The reservation must be adopted — claiming a second
    credit would 402 because none are available.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        grant_admin_credits(
            s, user=user, tier="standard", quantity=1, reason="seed"
        )
        case, opened_credit = open_case(
            s,
            user=user,
            anchor_label="1991 Prince Arthur",
            anchor_kind="address",
            tier="standard",
        )
        # Sanity: open_case reserved the only available credit.
        assert opened_credit.state == "reserved"
        assert opened_credit.case_id == case.id
        assert opened_credit.session_id is None
        balances = credit_balance_for(s, user_id=user_id)
        std = next(b for b in balances if b.tier == "standard")
        assert std.available == 0
        assert std.reserved == 1

        # First chat turn: brand-new chat session, no credit attached.
        sess_id = _new_session_for(s, user_id=user_id)
        session = s.get(ChatSession, sess_id)
        adopted = reserve_credit_for_session(
            s,
            user,
            session=session,
            case=case,
            tier="standard",
        )

        # The case's credit is now attached to the session — no new
        # credit was claimed (which would have 402'd).
        assert adopted.id == opened_credit.id
        assert adopted.session_id == sess_id
        assert adopted.case_id == case.id
        assert adopted.state == "reserved"

        # Still exactly one credit owned by the user, and it's the
        # adopted one. (Two ``credit_reserved`` events is correct: one
        # from open_case, one from the adoption.)
        all_credits = s.query(CaseCredit).filter_by(user_id=user_id).all()
        assert len(all_credits) == 1


def test_reserve_credit_for_session_is_idempotent_on_already_attached(
    tmp_path: Path,
) -> None:
    """Second call for the same session returns the same credit row."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        grant_admin_credits(
            s, user=user, tier="quick", quantity=1, reason="seed"
        )
        case, _ = open_case(
            s,
            user=user,
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        sess_id = _new_session_for(s, user_id=user_id)
        session = s.get(ChatSession, sess_id)

        first = reserve_credit_for_session(
            s, user, session=session, case=case, tier="quick"
        )
        second = reserve_credit_for_session(
            s, user, session=session, case=case, tier="quick"
        )
        assert first.id == second.id


def test_reserve_credit_for_session_claims_available_when_no_case_reservation(
    tmp_path: Path,
) -> None:
    """Resume-with-no-prior-credit path still works.

    Edge case: a chat session is created against a case whose credit
    was previously consumed (e.g. a fresh follow-up session on an
    older case). With another available credit on hand, the helper
    claims it as before.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        grant_admin_credits(
            s, user=user, tier="standard", quantity=2, reason="seed"
        )
        case, opened_credit = open_case(
            s,
            user=user,
            anchor_label="addr",
            anchor_kind="address",
            tier="standard",
        )
        # Simulate the case-reserved credit being already consumed by
        # a previous chat session — wipe its case binding to make sure
        # the adoption lookup misses.
        opened_credit.state = "consumed"
        opened_credit.session_id = _new_session_for(s, user_id=user_id)
        s.flush()

        # New session for a follow-up turn on the same case.
        sess_id = _new_session_for(s, user_id=user_id)
        session = s.get(ChatSession, sess_id)
        credit = reserve_credit_for_session(
            s, user, session=session, case=case, tier="standard"
        )

        assert credit.id != opened_credit.id
        assert credit.state == "reserved"
        assert credit.session_id == sess_id
        assert credit.case_id == case.id


def test_reserve_credit_for_session_raises_402_when_nothing_available(
    tmp_path: Path,
) -> None:
    """No case credit + no available credit → HTTPException(402)."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        # No credits granted.
        case = Case(
            user_id=user_id,
            anchor_label="addr",
            anchor_key="addr",
            anchor_kind="address",
            status="open",
            current_tier="standard",
        )
        s.add(case)
        s.flush()

        sess_id = _new_session_for(s, user_id=user_id)
        session = s.get(ChatSession, sess_id)
        with pytest.raises(HTTPException) as exc_info:
            reserve_credit_for_session(
                s, user, session=session, case=case, tier="standard"
            )
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["code"] == "no_available_credit"


def test_adoption_records_audit_event_with_adopted_marker(
    tmp_path: Path,
) -> None:
    """The audit row for adoption is distinguishable from a fresh claim."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        grant_admin_credits(
            s, user=user, tier="standard", quantity=1, reason="seed"
        )
        case, _ = open_case(
            s,
            user=user,
            anchor_label="addr",
            anchor_kind="address",
            tier="standard",
        )
        sess_id = _new_session_for(s, user_id=user_id)
        session = s.get(ChatSession, sess_id)
        reserve_credit_for_session(
            s, user, session=session, case=case, tier="standard"
        )

    with session_scope(db_url) as s:
        events = (
            s.query(CaseEvent)
            .filter(CaseEvent.event_type == "credit_reserved")
            .all()
        )
        # One from open_case, one from the adoption.
        assert len(events) == 2
        adopted = [
            e for e in events if e.payload_json.get("adopted_from_case")
        ]
        assert len(adopted) == 1
