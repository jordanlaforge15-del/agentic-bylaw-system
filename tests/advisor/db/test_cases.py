"""Case-credit lifecycle: open / reserve / commit / refund / upgrade / 30-day match."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from advisor.db.cases import (
    FREE_QUESTION_GRANT,
    NoAvailableCreditError,
    REOPEN_WINDOW,
    STARTER_GRANT_QUANTITY,
    STARTER_GRANT_TIER,
    UnknownTierError,
    close_case,
    commit_credit_for_session,
    consume_free_question,
    credit_balance_for,
    grant_admin_credits,
    grant_starter_credits_if_needed,
    list_user_cases,
    match_case,
    normalise_anchor,
    open_case,
    refund_credit_for_session,
    refund_orphaned_case_reservations,
    upgrade_case_credit,
)
from advisor.db.models import (
    Case,
    CaseCredit,
    CaseEvent,
    CasePurchase,
    ChatSession,
    User,
)
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


# ---------- normalise_anchor ----------------------------------------------


def test_normalise_anchor_address_variants_collapse_to_same_key() -> None:
    a = normalise_anchor("1234 Main St, Halifax, NS B3J 1A1", "address")
    b = normalise_anchor("1234 main street halifax", "address")
    assert a == b


def test_normalise_anchor_strips_unit_marker() -> None:
    full = normalise_anchor("1234 Main St #401, Halifax", "address")
    bare = normalise_anchor("1234 main st halifax", "address")
    assert full == bare


def test_normalise_anchor_project_ref_collapses_separators() -> None:
    a = normalise_anchor("DA-2024-12345", "project_ref")
    b = normalise_anchor("da 2024 12345", "project_ref")
    c = normalise_anchor("DA_2024_12345", "project_ref")
    assert a == b == c == "da-2024-12345"


def test_normalise_anchor_empty_returns_empty() -> None:
    assert normalise_anchor("", "address") == ""


# ---------- match_case ----------------------------------------------------


def test_match_case_returns_none_when_no_history(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        result = match_case(
            s,
            user_id=user_id,
            anchor_label="1234 Main St",
            anchor_kind="address",
        )
        assert result.case is None


def test_match_case_finds_in_window_case(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        # Seed one credit so open_case succeeds.
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=1,
            reason="test",
        )
        case, _ = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="1234 Main St, Halifax",
            anchor_kind="address",
            tier="standard",
        )
        case_id = case.id

    with session_scope(db_url) as s:
        result = match_case(
            s,
            user_id=user_id,
            anchor_label="1234 main street halifax",
            anchor_kind="address",
        )
        assert result.case is not None
        assert result.case.id == case_id


def test_match_case_skips_out_of_window(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="quick",
            quantity=1,
            reason="test",
        )
        case, _ = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="999 Elm St",
            anchor_kind="address",
            tier="quick",
        )
        # Manually age the case past the reopen window.
        case.last_activity_at = datetime.now(timezone.utc) - REOPEN_WINDOW - timedelta(days=1)
        s.add(case)

    with session_scope(db_url) as s:
        result = match_case(
            s,
            user_id=user_id,
            anchor_label="999 elm st",
            anchor_kind="address",
        )
        assert result.case is None


# ---------- open_case + reserve_credit ------------------------------------


def test_open_case_without_credits_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        with pytest.raises(NoAvailableCreditError) as exc_info:
            open_case(
                s,
                user=s.get(User, user_id),
                anchor_label="111 Test Rd",
                anchor_kind="address",
                tier="standard",
            )
        assert exc_info.value.tier == "standard"


def test_open_case_reserves_credit_atomically(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=2,
            reason="test",
        )
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="1234 Main St",
            anchor_kind="address",
            tier="standard",
        )
        assert credit.state == "reserved"
        assert credit.case_id == case.id
        assert case.current_tier == "standard"
        # 1 of 2 credits reserved → 1 still available.
        balances = credit_balance_for(s, user_id=user_id)
        std = next(b for b in balances if b.tier == "standard")
        assert std.available == 1
        assert std.reserved == 1


def test_open_case_is_idempotent_for_same_anchor(tmp_path: Path) -> None:
    """A second open_case for an already-open in-window case returns the
    case's existing reserved credit instead of claiming a second one.

    Regression test for ABS-8 / ABS-11: a double-click or page refresh on
    "Open case" used to reserve a second credit against the same case,
    leaving the first orphaned in ``reserved`` and silently burning one
    of the user's credits.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=2,
            reason="test",
        )
        case_a, credit_a = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="1234 Main St",
            anchor_kind="address",
            tier="standard",
        )

    with session_scope(db_url) as s:
        case_b, credit_b = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="1234 Main St",
            anchor_kind="address",
            tier="standard",
        )
        assert case_b.id == case_a.id, "must reuse the matched case"
        assert credit_b.id == credit_a.id, "must reuse the existing credit"
        # Available count untouched — second call did not claim.
        balances = credit_balance_for(s, user_id=user_id)
        std = next(b for b in balances if b.tier == "standard")
        assert std.available == 1
        assert std.reserved == 1
        assert std.consumed == 0


def test_open_case_idempotent_when_only_one_credit_left(tmp_path: Path) -> None:
    """The double-click path must not 402 when the user has exactly one
    credit. Without the idempotency guard, the first call reserves the
    only credit and the second 402s on no_available_credit."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=1,
            reason="test",
        )
        open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="1234 Main St",
            anchor_kind="address",
            tier="standard",
        )

    with session_scope(db_url) as s:
        # Must NOT raise — idempotent reuse instead of a fresh claim.
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="1234 Main St",
            anchor_kind="address",
            tier="standard",
        )
        assert credit.state == "reserved"
        assert credit.case_id == case.id


def test_open_case_records_credit_reused_audit_event(tmp_path: Path) -> None:
    """Idempotent re-open writes a ``credit_reused`` event so the
    distinction between a fresh reservation and an idempotent reuse
    survives in audit history."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="quick",
            quantity=1,
            reason="t",
        )
        open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )

    with session_scope(db_url) as s:
        open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )

    with session_scope(db_url) as s:
        kinds = [e.event_type for e in s.query(CaseEvent).all()]
        assert kinds.count("credit_reserved") == 1, (
            "must not record a second credit_reserved on idempotent reopen"
        )
        assert "credit_reused" in kinds


def test_open_case_unknown_tier_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        with pytest.raises(UnknownTierError):
            open_case(
                s,
                user=s.get(User, user_id),
                anchor_label="anchor",
                anchor_kind="address",
                tier="enterprise",  # not a tier
            )


# ---------- commit / refund -----------------------------------------------


def test_commit_credit_moves_reserved_to_consumed(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s, user=s.get(User, user_id), tier="quick", quantity=1, reason="t"
        )
        # Manually attach the credit to a session so commit_credit_for_session
        # has a session_id to look up by.
        sess_id = _new_session_for(s, user_id=user_id)
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        credit.session_id = sess_id
        s.flush()

    with session_scope(db_url) as s:
        committed = commit_credit_for_session(s, session_id=sess_id)
        assert committed is not None
        assert committed.state == "consumed"
        assert committed.consumed_at is not None


def test_commit_credit_is_idempotent(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s, user=s.get(User, user_id), tier="quick", quantity=1, reason="t"
        )
        sess_id = _new_session_for(s, user_id=user_id)
        _, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        credit.session_id = sess_id

    with session_scope(db_url) as s:
        commit_credit_for_session(s, session_id=sess_id)

    with session_scope(db_url) as s:
        # Second call: still finds the now-consumed credit, returns it,
        # doesn't error.
        result = commit_credit_for_session(s, session_id=sess_id)
        assert result is not None
        assert result.state == "consumed"


def test_refund_credit_releases_reservation(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s, user=s.get(User, user_id), tier="quick", quantity=1, reason="t"
        )
        sess_id = _new_session_for(s, user_id=user_id)
        _, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        credit.session_id = sess_id

    with session_scope(db_url) as s:
        refunded = refund_credit_for_session(
            s, session_id=sess_id, reason="abandoned"
        )
        assert refunded is not None
        assert refunded.state == "available"
        assert refunded.session_id is None
        assert refunded.case_id is None


def test_refund_does_nothing_to_consumed_credit(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s, user=s.get(User, user_id), tier="quick", quantity=1, reason="t"
        )
        sess_id = _new_session_for(s, user_id=user_id)
        _, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        credit.session_id = sess_id

    with session_scope(db_url) as s:
        commit_credit_for_session(s, session_id=sess_id)

    with session_scope(db_url) as s:
        # Refund-after-consume returns None (no-op) because the query
        # filters on state='reserved'.
        result = refund_credit_for_session(
            s, session_id=sess_id, reason="too_late"
        )
        assert result is None


# ---------- upgrade -------------------------------------------------------


def test_upgrade_swaps_credit_atomically(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        # Quick credit reserved on a session, then a Standard credit
        # available for the upgrade target.
        grant_admin_credits(
            s, user=s.get(User, user_id), tier="quick", quantity=1, reason="t"
        )
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=1,
            reason="t",
        )
        sess_id = _new_session_for(s, user_id=user_id)
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        credit.session_id = sess_id
        case_id = case.id
        original_credit_id = credit.id

    with session_scope(db_url) as s:
        case = s.get(Case, case_id)
        burned, new = upgrade_case_credit(
            s, case=case, target_tier="standard", trigger="user_manual"
        )
        assert burned.id == original_credit_id
        assert burned.state == "upgraded_out"
        assert burned.upgraded_to_credit_id == new.id
        # ``upgraded_from_tier`` lives on the NEW credit (what tier
        # did it come from?), not on the burned one.
        assert new.upgraded_from_tier == "quick"
        assert new.state == "reserved"
        assert new.session_id == sess_id
        assert new.case_id == case_id
        assert case.current_tier == "standard"


def test_upgrade_without_higher_credit_raises(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s, user=s.get(User, user_id), tier="quick", quantity=1, reason="t"
        )
        sess_id = _new_session_for(s, user_id=user_id)
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="quick",
        )
        credit.session_id = sess_id

    with session_scope(db_url) as s:
        case = s.get(Case, list(s.query(Case)).pop().id)
        with pytest.raises(NoAvailableCreditError) as exc_info:
            upgrade_case_credit(
                s, case=case, target_tier="standard", trigger="classifier"
            )
        assert exc_info.value.tier == "standard"


# ---------- close ---------------------------------------------------------


def test_close_case_refunds_reserved_credit(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=1,
            reason="t",
        )
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="standard",
        )
        case_id = case.id

    with session_scope(db_url) as s:
        case = s.get(Case, case_id)
        close_case(s, case=case, reason="user_request")
        assert case.status == "closed"
        # Credit returned to available, no longer attached.
        balances = credit_balance_for(s, user_id=user_id)
        std = next(b for b in balances if b.tier == "standard")
        assert std.available == 1
        assert std.reserved == 0


# ---------- audit events --------------------------------------------------


def test_admin_grant_records_event(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="quick",
            quantity=3,
            reason="beta_seed",
        )

    with session_scope(db_url) as s:
        events = list(s.query(CaseEvent).all())
        kinds = [e.event_type for e in events]
        assert "admin_credit_grant" in kinds


def test_open_case_records_open_event(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="quick",
            quantity=1,
            reason="t",
        )
        open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="111 Anchor",
            anchor_kind="address",
            tier="quick",
        )

    with session_scope(db_url) as s:
        kinds = [e.event_type for e in s.query(CaseEvent).all()]
        # admin_credit_grant + opened + credit_reserved.
        assert "opened" in kinds
        assert "credit_reserved" in kinds


# ---------- pack purchase issues N credits --------------------------------


# ---------- grant_starter_credits_if_needed -------------------------------


def test_grant_starter_credits_grants_default_pack_to_new_user(
    tmp_path: Path,
) -> None:
    """A new user with no credits gets the free-question entitlement counter."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        granted = grant_starter_credits_if_needed(s, user=s.get(User, user_id))
        assert granted is True

    with session_scope(db_url) as s:
        # No CaseCredit rows — the new model uses an entitlement counter.
        credits = list(s.query(CaseCredit).filter(CaseCredit.user_id == user_id))
        assert len(credits) == 0
        # The counter on the user row is set to FREE_QUESTION_GRANT.
        user = s.get(User, user_id)
        assert user.free_questions_remaining == FREE_QUESTION_GRANT
        assert user.metadata_json.get("free_question_grant_issued") is True


def test_grant_starter_credits_issues_for_legacy_credit_user(
    tmp_path: Path,
) -> None:
    """ABS-323: a user holding legacy tier credits STILL gets the grant.

    Pre-ABS-323 this was skipped on the "holds any CaseCredit ⇒ already
    onboarded" assumption. After the tier→question pivot legacy tier
    credits can't buy answers, so suppressing the grant locked every
    existing user out of the free trial with payments off. The grant
    must now issue regardless of legacy credits, gated only on the
    idempotency flag.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="quick",
            quantity=1,
            reason="test_seed",
        )

    with session_scope(db_url) as s:
        granted = grant_starter_credits_if_needed(s, user=s.get(User, user_id))
        assert granted is True

    with session_scope(db_url) as s:
        # The legacy tier credit is untouched and coexists with the grant.
        credits = list(s.query(CaseCredit).filter(CaseCredit.user_id == user_id))
        assert len(credits) == 1
        assert credits[0].tier == "quick"
        user = s.get(User, user_id)
        assert user.free_questions_remaining == FREE_QUESTION_GRANT
        assert user.metadata_json.get("free_question_grant_issued") is True


def test_grant_starter_credits_flag_is_sole_gate_even_with_legacy_credits(
    tmp_path: Path,
) -> None:
    """ABS-323: once the flag is set, legacy credits don't re-trigger a grant.

    The ``free_question_grant_issued`` flag is the only idempotency gate.
    A user who already holds the grant AND legacy tier credits is a
    no-op — the counter is not topped back up.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        grant_admin_credits(s, user=user, tier="quick", quantity=1, reason="seed")
        # Grant once, then consume so the counter sits below the default.
        assert grant_starter_credits_if_needed(s, user=user) is True
        consume_free_question(s, user=user)

    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        # Flag set + legacy credits present → no-op, counter not restored.
        assert grant_starter_credits_if_needed(s, user=user) is False
        assert user.free_questions_remaining == FREE_QUESTION_GRANT - 1


def test_grant_starter_credits_is_noop_when_already_issued(
    tmp_path: Path,
) -> None:
    """Calling grant twice for the same creditless user is a no-op on the second call."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        first = grant_starter_credits_if_needed(s, user=s.get(User, user_id))
        assert first is True

    with session_scope(db_url) as s:
        second = grant_starter_credits_if_needed(s, user=s.get(User, user_id))
        assert second is False

    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        # Counter unchanged — not incremented a second time.
        assert user.free_questions_remaining == FREE_QUESTION_GRANT


def test_consume_free_question_decrements_counter(
    tmp_path: Path,
) -> None:
    """consume_free_question decrements the counter and returns True."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_starter_credits_if_needed(s, user=s.get(User, user_id))

    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        consumed = consume_free_question(s, user=user)
        assert consumed is True

    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        assert user.free_questions_remaining == FREE_QUESTION_GRANT - 1


def test_consume_free_question_returns_false_when_counter_zero(
    tmp_path: Path,
) -> None:
    """consume_free_question returns False when no free questions remain."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        assert user.free_questions_remaining == 0
        consumed = consume_free_question(s, user=user)
        assert consumed is False

    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        assert user.free_questions_remaining == 0


# ---------- refund_orphaned_case_reservations -----------------------------


def test_refund_orphaned_case_reservations_recovers_pre_abs9_leak(
    tmp_path: Path,
) -> None:
    """The recovery sweep refunds a reserved credit stuck on a case
    that already has a sessioned active credit at the same tier.

    Mirrors the prod-observed data on chris.rafuse credit 3:
    case has one consumed credit (with session) plus a leaked reserved
    credit with ``session_id IS NULL``. After the sweep the leak is
    back in ``available`` and the consumed credit is untouched.
    """
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    # Build the leaked state by hand (the pre-ABS-9 buggy flow that
    # produced it is no longer reachable via open_case after this fix).
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=2,
            reason="pre_seed",
        )
        sess_id = _new_session_for(s, user_id=user_id)
        case = Case(
            user_case_number=1,
            user_id=user_id,
            anchor_label="addr",
            anchor_key="addr",
            anchor_kind="address",
            status="open",
            current_tier="standard",
            tokens_consumed=0,
            opened_at=datetime.now(timezone.utc),
            last_activity_at=datetime.now(timezone.utc),
        )
        s.add(case)
        s.flush()
        credits = list(
            s.query(CaseCredit).filter(CaseCredit.user_id == user_id)
        )
        leaked, consumed = credits[0], credits[1]
        leaked.case_id = case.id
        leaked.session_id = None
        leaked.state = "reserved"
        leaked.reserved_at = datetime.now(timezone.utc)
        consumed.case_id = case.id
        consumed.session_id = sess_id
        consumed.state = "consumed"
        consumed.reserved_at = datetime.now(timezone.utc)
        consumed.consumed_at = datetime.now(timezone.utc)
        leaked_id = leaked.id
        consumed_id = consumed.id

    with session_scope(db_url) as s:
        refunded = refund_orphaned_case_reservations(s)
        assert refunded == 1

    with session_scope(db_url) as s:
        leak = s.get(CaseCredit, leaked_id)
        assert leak.state == "available"
        assert leak.case_id is None
        assert leak.session_id is None
        cons = s.get(CaseCredit, consumed_id)
        # Consumed credit untouched.
        assert cons.state == "consumed"
        assert cons.session_id is not None


def test_refund_orphaned_case_reservations_skips_solo_reservation(
    tmp_path: Path,
) -> None:
    """A case with a single reserved-no-session credit is left alone —
    that's a legitimate in-flight open (user just hasn't started chat
    yet) and is the abandon-sweep's job after 24h, not ours."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=1,
            reason="t",
        )
        open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="addr",
            anchor_kind="address",
            tier="standard",
        )

    with session_scope(db_url) as s:
        refunded = refund_orphaned_case_reservations(s)
        assert refunded == 0
        # Credit still reserved on the case.
        credit = s.query(CaseCredit).filter(CaseCredit.user_id == user_id).one()
        assert credit.state == "reserved"


def test_refund_orphaned_case_reservations_is_idempotent(
    tmp_path: Path,
) -> None:
    """Re-running the sweep after a recovery finds nothing to refund."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=2,
            reason="t",
        )
        sess_id = _new_session_for(s, user_id=user_id)
        case = Case(
            user_case_number=1,
            user_id=user_id,
            anchor_label="addr",
            anchor_key="addr",
            anchor_kind="address",
            status="open",
            current_tier="standard",
            tokens_consumed=0,
            opened_at=datetime.now(timezone.utc),
            last_activity_at=datetime.now(timezone.utc),
        )
        s.add(case)
        s.flush()
        credits = list(
            s.query(CaseCredit).filter(CaseCredit.user_id == user_id)
        )
        credits[0].case_id = case.id
        credits[0].session_id = None
        credits[0].state = "reserved"
        credits[0].reserved_at = datetime.now(timezone.utc)
        credits[1].case_id = case.id
        credits[1].session_id = sess_id
        credits[1].state = "reserved"
        credits[1].reserved_at = datetime.now(timezone.utc)

    with session_scope(db_url) as s:
        first = refund_orphaned_case_reservations(s)
        assert first == 1
    with session_scope(db_url) as s:
        second = refund_orphaned_case_reservations(s)
        assert second == 0


def test_db_constraint_rejects_second_unsessioned_reservation_on_same_case(
    tmp_path: Path,
) -> None:
    """The partial unique index ``uq_advisor_case_credit_one_unsessioned_reserve``
    rejects a second ``reserved / session_id IS NULL`` credit on the same case.

    Defence-in-depth for ABS-11: even if the application-level idempotency
    guard in ``open_case`` were accidentally removed, the DB prevents the
    double-reservation that leaks a credit.
    """
    from sqlalchemy.exc import IntegrityError

    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)

    # Seed credits and case, then reserve the first credit.
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=2,
            reason="t",
        )
        case = Case(
            user_case_number=1,
            user_id=user_id,
            anchor_label="addr",
            anchor_key="addr",
            anchor_kind="address",
            status="open",
            current_tier="standard",
            tokens_consumed=0,
            opened_at=datetime.now(timezone.utc),
            last_activity_at=datetime.now(timezone.utc),
        )
        s.add(case)
        s.flush()
        credits = list(
            s.query(CaseCredit)
            .filter(CaseCredit.user_id == user_id, CaseCredit.state == "available")
            .order_by(CaseCredit.id)
            .all()
        )
        credits[0].case_id = case.id
        credits[0].state = "reserved"
        credits[0].session_id = None
        credits[0].reserved_at = datetime.now(timezone.utc)
        case_id = case.id
        second_credit_id = credits[1].id

    # Attempt a second unsessioned reservation in a fresh session — must fail.
    with pytest.raises((IntegrityError, Exception)):
        with session_scope(db_url) as s:
            credit2 = s.get(CaseCredit, second_credit_id)
            credit2.case_id = case_id
            credit2.state = "reserved"
            credit2.session_id = None
            credit2.reserved_at = datetime.now(timezone.utc)
            s.flush()


def test_db_constraint_allows_sessioned_credit_alongside_unsessioned(
    tmp_path: Path,
) -> None:
    """A sessioned credit (from chat start) can coexist with an
    unsessioned reservation — the constraint only targets the
    ``session_id IS NULL`` window."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=2,
            reason="t",
        )
        sess_id = _new_session_for(s, user_id=user_id)
        case = Case(
            user_case_number=1,
            user_id=user_id,
            anchor_label="addr",
            anchor_key="addr",
            anchor_kind="address",
            status="open",
            current_tier="standard",
            tokens_consumed=0,
            opened_at=datetime.now(timezone.utc),
            last_activity_at=datetime.now(timezone.utc),
        )
        s.add(case)
        s.flush()

        credits = list(
            s.query(CaseCredit)
            .filter(CaseCredit.user_id == user_id, CaseCredit.state == "available")
            .order_by(CaseCredit.id)
            .all()
        )
        # Credit 1: unsessioned reservation (from open_case).
        credits[0].case_id = case.id
        credits[0].state = "reserved"
        credits[0].session_id = None
        credits[0].reserved_at = datetime.now(timezone.utc)
        s.flush()

        # Credit 2: sessioned reservation (from chat start) — must succeed.
        credits[1].case_id = case.id
        credits[1].state = "reserved"
        credits[1].session_id = sess_id
        credits[1].reserved_at = datetime.now(timezone.utc)
        s.flush()  # No error expected.


def test_grant_admin_credits_creates_one_row_per_credit(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        grant_admin_credits(
            s,
            user=s.get(User, user_id),
            tier="standard",
            quantity=20,
            reason="pro_pack_test",
        )

    with session_scope(db_url) as s:
        credits = list(s.query(CaseCredit).all())
        assert len(credits) == 20
        # All linked to one purchase row.
        purchase_ids = {c.purchase_id for c in credits}
        assert len(purchase_ids) == 1
        # Purchase row is the synthetic admin_grant.
        purchase = s.get(CasePurchase, next(iter(purchase_ids)))
        assert purchase.pack_sku == "admin_grant"
        assert purchase.quantity == 20


# ---------- unlimited credits (ABS-12) ------------------------------------


def _seed_unlimited_user(db_url: str, *, clerk_user_id: str = "u_test") -> int:
    with session_scope(db_url) as s:
        user = User(
            clerk_user_id=clerk_user_id,
            email=f"{clerk_user_id}@x.com",
            unlimited_credits=True,
        )
        s.add(user)
        s.flush()
        return user.id


def test_unlimited_credits_defaults_to_false(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        assert user.unlimited_credits is False


def test_open_case_auto_mints_credit_for_unlimited_user(tmp_path: Path) -> None:
    """An unlimited-credits user can open a case without pre-granted credits."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_unlimited_user(db_url)
    with session_scope(db_url) as s:
        case, credit = open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="123 Test St",
            anchor_kind="address",
            tier="standard",
        )
        assert credit.state == "reserved"
        assert credit.source == "test"
        assert credit.tier == "standard"
        assert credit.case_id == case.id


def test_unlimited_user_minted_credit_has_test_source(tmp_path: Path) -> None:
    """Auto-minted credits are tagged with source='test' for analytics filtering."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_unlimited_user(db_url)
    with session_scope(db_url) as s:
        open_case(
            s,
            user=s.get(User, user_id),
            anchor_label="456 Test St",
            anchor_kind="address",
            tier="quick",
        )

    with session_scope(db_url) as s:
        credits = list(
            s.query(CaseCredit).filter(CaseCredit.user_id == user_id)
        )
        assert len(credits) == 1
        assert credits[0].source == "test"
        purchase = s.get(CasePurchase, credits[0].purchase_id)
        assert purchase.pack_sku == "test"
        assert purchase.amount_paid_cents == 0


def test_unlimited_user_can_open_multiple_cases(tmp_path: Path) -> None:
    """Each case open auto-mints its own test credit."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_unlimited_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        case1, c1 = open_case(
            s, user=user, anchor_label="A", anchor_kind="address", tier="standard"
        )
        case2, c2 = open_case(
            s, user=user, anchor_label="B", anchor_kind="address", tier="standard"
        )
        assert c1.id != c2.id
        assert c1.source == "test"
        assert c2.source == "test"


def test_unlimited_user_upgrade_auto_mints(tmp_path: Path) -> None:
    """upgrade_case_credit auto-mints a higher-tier credit for unlimited users."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_unlimited_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        case, credit = open_case(
            s, user=user, anchor_label="X", anchor_kind="address", tier="quick"
        )
        burned, new = upgrade_case_credit(
            s, case=case, target_tier="standard", trigger="test_upgrade"
        )
        assert burned.state == "upgraded_out"
        assert new.source == "test"
        assert new.tier == "standard"
        assert new.case_id == case.id


def test_normal_user_still_gets_no_credit_error(tmp_path: Path) -> None:
    """unlimited_credits=False (default) does not auto-mint."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_user(db_url)
    with session_scope(db_url) as s:
        with pytest.raises(NoAvailableCreditError):
            open_case(
                s,
                user=s.get(User, user_id),
                anchor_label="No credits",
                anchor_kind="address",
                tier="standard",
            )


def test_unlimited_user_prefers_real_credits_over_minting(tmp_path: Path) -> None:
    """If an unlimited user has real available credits, those are used first."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    user_id = _seed_unlimited_user(db_url)
    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        grant_admin_credits(s, user=user, tier="standard", quantity=1, reason="test")

    with session_scope(db_url) as s:
        user = s.get(User, user_id)
        _, credit = open_case(
            s, user=user, anchor_label="R", anchor_kind="address", tier="standard"
        )
        assert credit.source == "admin_grant", (
            "should use the real credit, not auto-mint"
        )
