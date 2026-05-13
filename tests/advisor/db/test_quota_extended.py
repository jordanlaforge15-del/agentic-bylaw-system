"""New cap dimensions added with the invite-flow rollout:
- per-user monthly input/output token caps
- per-user requests-per-minute rate cap

Existing query-count quota tests still cover that dimension. These
tests cover the three new ones, plus the QuotaExceeded.kind field
that the API edge dispatches on.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from advisor.db.models import UsageEvent, User
from advisor.db.quota import QuotaExceeded, record_query
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _make_user(s, **overrides) -> User:
    base = dict(
        clerk_user_id="clerk_q",
        email="q@example.com",
        full_name="Quota User",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=0,
        monthly_input_token_limit=10_000,
        monthly_output_token_limit=5_000,
        monthly_input_tokens_used=0,
        monthly_output_tokens_used=0,
        requests_per_minute_limit=6,
        month_started_at=date(2026, 5, 1),
    )
    base.update(overrides)
    user = User(**base)
    s.add(user)
    s.flush()
    return user


def test_input_token_cap_raises_with_kind(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s, monthly_input_tokens_used=10_000)
        with pytest.raises(QuotaExceeded) as exc:
            record_query(s, user)
        assert exc.value.kind == "input_tokens"
        assert exc.value.limit == 10_000
        # Audit row exists.
        s.commit()
        rows = (
            s.query(UsageEvent)
            .filter(UsageEvent.event_type == "monthly_quota_exceeded")
            .all()
        )
        assert any(
            r.metadata_json.get("limit_kind") == "input_tokens" for r in rows
        )


def test_output_token_cap_raises_with_kind(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s, monthly_output_tokens_used=5_000)
        with pytest.raises(QuotaExceeded) as exc:
            record_query(s, user)
        assert exc.value.kind == "output_tokens"


def test_rpm_cap_raises_with_kind(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s, requests_per_minute_limit=2)
        # Two prior recent llm_call events fill the bucket.
        for _ in range(2):
            record_query(s, user)
        s.flush()
        with pytest.raises(QuotaExceeded) as exc:
            record_query(s, user)
        assert exc.value.kind == "rpm"
        assert exc.value.limit == 2


def test_kind_ordering_rpm_before_queries(tmp_path: Path) -> None:
    """When both limits would fire, the rate cap takes priority —
    runaway clients should see "slow down" before "you're out of
    monthly budget" so the user diagnoses the right thing."""
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(
            s,
            requests_per_minute_limit=1,
            monthly_query_limit=10,
            monthly_queries_used=10,  # at monthly cap
        )
        # Pre-seed a recent llm_call so the rpm bucket is already
        # full when record_query runs. Both rpm and queries should
        # trip; rpm wins because we check it first.
        s.add(UsageEvent(user_id=user.id, event_type="llm_call"))
        s.flush()
        with pytest.raises(QuotaExceeded) as exc:
            record_query(s, user)
        assert exc.value.kind == "rpm"


def test_token_counters_increment_on_record(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        user = _make_user(s)
        record_query(s, user, tokens_input=100, tokens_output=50)
        s.commit()
        s.refresh(user)
        assert user.monthly_input_tokens_used == 100
        assert user.monthly_output_tokens_used == 50
        assert user.monthly_queries_used == 1


def test_quota_exceeded_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        QuotaExceeded(kind="bogus", limit=1, used=1)
