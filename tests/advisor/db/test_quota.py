"""Monthly quota lifecycle: read, increment, exceed, roll over."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from advisor.db import (
    QuotaExceeded,
    UsageEvent,
    User,
    get_monthly_quota,
    record_query,
)
from advisor.db import quota as quota_module
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _make_user(s, **overrides) -> User:
    base = dict(
        clerk_user_id=overrides.pop("clerk_user_id", "clerk_quota"),
        email="quota@example.com",
        full_name="Quota User",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=0,
        month_started_at=date.today().replace(day=1),
    )
    base.update(overrides)
    user = User(**base)
    s.add(user)
    s.flush()
    return user


def test_get_monthly_quota_fresh_user(tmp_path: Path, monkeypatch) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)

    fixed_today = date(2026, 5, 15)
    monkeypatch.setattr(
        quota_module,
        "_utc_today",
        lambda: fixed_today,
    )

    with session_scope(db_url) as s:
        user = _make_user(s, month_started_at=date(2026, 5, 1))
        quota = get_monthly_quota(s, user)
        assert quota.limit == 100
        assert quota.used == 0
        assert quota.remaining == 100
        assert quota.window_start == date(2026, 5, 1)


def test_record_query_increments_and_emits_event(tmp_path: Path, monkeypatch) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    with session_scope(db_url) as s:
        user = _make_user(s, month_started_at=date(2026, 5, 1))
        event = record_query(
            s,
            user,
            tokens_input=10,
            tokens_output=20,
            model="claude-opus-4",
            provider="anthropic",
        )
        s.flush()

        assert event.event_type == "llm_call"
        assert event.tokens_input == 10
        assert event.tokens_output == 20
        assert user.monthly_queries_used == 1

    with session_scope(db_url) as s:
        events = s.query(UsageEvent).all()
        assert len(events) == 1
        assert events[0].event_type == "llm_call"
        # No reset event emitted on a non-roll-over day.
        assert all(e.event_type != "monthly_quota_reset" for e in events)


def test_record_query_repeated_to_just_below_limit(
    tmp_path: Path, monkeypatch
) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    with session_scope(db_url) as s:
        user = _make_user(
            s,
            monthly_query_limit=3,
            monthly_queries_used=0,
            month_started_at=date(2026, 5, 1),
        )
        record_query(s, user)
        record_query(s, user)
        # Last legal query should still succeed (used == limit - 1 -> limit).
        record_query(s, user)
        assert user.monthly_queries_used == 3


def test_record_query_raises_on_limit(tmp_path: Path, monkeypatch) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 5, 15)
    )

    with session_scope(db_url) as s:
        user = _make_user(
            s,
            monthly_query_limit=2,
            monthly_queries_used=2,
            month_started_at=date(2026, 5, 1),
        )
        with pytest.raises(QuotaExceeded) as excinfo:
            record_query(s, user)
        assert excinfo.value.limit == 2
        assert excinfo.value.used == 2

        # Telemetry: a monthly_quota_exceeded event should have been
        # added to the session even though record_query raised.
        s.flush()
        exceeded = (
            s.query(UsageEvent)
            .filter_by(event_type="monthly_quota_exceeded")
            .all()
        )
        assert len(exceeded) == 1


def test_get_monthly_quota_resets_on_new_month(tmp_path: Path, monkeypatch) -> None:
    db_url = _db_url(tmp_path)
    create_all(db_url)

    # Today is in June; the user's recorded window starts in May.
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 6, 7)
    )

    with session_scope(db_url) as s:
        user = _make_user(
            s,
            monthly_queries_used=42,
            month_started_at=date(2026, 5, 1),
        )

        quota = get_monthly_quota(s, user)
        assert quota.used == 0
        assert quota.remaining == 100
        assert quota.window_start == date(2026, 6, 1)
        assert user.month_started_at == date(2026, 6, 1)
        assert user.monthly_queries_used == 0

        s.flush()
        reset_events = (
            s.query(UsageEvent)
            .filter_by(event_type="monthly_quota_reset")
            .all()
        )
        assert len(reset_events) == 1
        assert reset_events[0].metadata_json["previous_used"] == 42


def test_record_query_after_reset_is_first_in_new_window(
    tmp_path: Path, monkeypatch
) -> None:
    """A user who maxed out in May but is back in June can query again."""
    db_url = _db_url(tmp_path)
    create_all(db_url)
    monkeypatch.setattr(
        quota_module, "_utc_today", lambda: date(2026, 6, 1)
    )

    with session_scope(db_url) as s:
        user = _make_user(
            s,
            monthly_query_limit=5,
            monthly_queries_used=5,
            month_started_at=date(2026, 5, 1),
        )
        # Should NOT raise — record_query triggers the reset first.
        event = record_query(s, user)
        assert event.event_type == "llm_call"
        assert user.monthly_queries_used == 1
        assert user.month_started_at == date(2026, 6, 1)
