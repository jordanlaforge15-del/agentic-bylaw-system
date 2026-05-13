"""Clerk webhook handler: dispatch, idempotency, signature verification."""
from __future__ import annotations

import json
from pathlib import Path

from advisor.auth.webhooks import (
    ClerkWebhookEvent,
    handle_event,
    verify_signature,
)
from advisor.db import UsageEvent, User
from layer1.db.init_db import create_all
from layer1.db.session import session_scope


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'advisor.db'}"


def _make_user(s, **overrides) -> User:
    base = dict(
        clerk_user_id="user_abc",
        email="user@example.com",
        full_name="Original Name",
        plan_tier="free",
        monthly_query_limit=100,
        monthly_queries_used=0,
    )
    base.update(overrides)
    user = User(**base)
    s.add(user)
    s.flush()
    return user


# ---------- user.created ----------------------------------------------------


def test_user_created_inserts_new_row(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        event = ClerkWebhookEvent(
            id="msg_1",
            type="user.created",
            data={
                "id": "user_new",
                "primary_email_address_id": "idn_1",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "new@example.com"},
                ],
                "first_name": "Alex",
                "last_name": "Doe",
            },
        )
        result = handle_event(s, event)
        s.commit()
        assert result.handled is True
        assert result.note == "created"
        user = s.query(User).filter(User.clerk_user_id == "user_new").one()
        assert user.email == "new@example.com"
        assert user.full_name == "Alex Doe"


def test_user_created_existing_row_updates_drift(tmp_path: Path) -> None:
    """If a chat call already created the row, the webhook just refreshes it."""
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        _make_user(s, clerk_user_id="user_dupe", email="old@example.com")
        event = ClerkWebhookEvent(
            id="msg_2",
            type="user.created",
            data={
                "id": "user_dupe",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "new@example.com"},
                ],
                "primary_email_address_id": "idn_1",
                "first_name": "Updated",
                "last_name": "Name",
            },
        )
        result = handle_event(s, event)
        s.commit()
        assert result.handled is True
        assert result.note == "already_present"
        user = s.query(User).filter(User.clerk_user_id == "user_dupe").one()
        assert user.email == "new@example.com"
        assert user.full_name == "Updated Name"


# ---------- user.updated ----------------------------------------------------


def test_user_updated_syncs_profile(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        _make_user(s)
        event = ClerkWebhookEvent(
            id="msg_3",
            type="user.updated",
            data={
                "id": "user_abc",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "renamed@example.com"},
                ],
                "primary_email_address_id": "idn_1",
                "first_name": "Renamed",
                "last_name": "User",
            },
        )
        result = handle_event(s, event)
        s.commit()
        assert result.handled is True
        assert result.note == "updated"
        user = s.query(User).filter(User.clerk_user_id == "user_abc").one()
        assert user.email == "renamed@example.com"
        assert user.full_name == "Renamed User"


def test_user_updated_unknown_user_is_silent_noop(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        event = ClerkWebhookEvent(
            id="msg_4",
            type="user.updated",
            data={"id": "user_never_seen", "email_addresses": []},
        )
        result = handle_event(s, event)
        s.commit()
        assert result.handled is True
        assert result.note == "user_not_in_db"


def test_user_updated_missing_email_does_not_blank(tmp_path: Path) -> None:
    """An update with no email_addresses must not clear the existing email."""
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        _make_user(s, email="keep@example.com")
        event = ClerkWebhookEvent(
            id="msg_5",
            type="user.updated",
            data={"id": "user_abc"},  # no email_addresses key
        )
        handle_event(s, event)
        s.commit()
        user = s.query(User).filter(User.clerk_user_id == "user_abc").one()
        assert user.email == "keep@example.com"


# ---------- user.deleted ----------------------------------------------------


def test_user_deleted_removes_row(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        _make_user(s)
        event = ClerkWebhookEvent(
            id="msg_6",
            type="user.deleted",
            data={"id": "user_abc", "deleted": True, "object": "user"},
        )
        result = handle_event(s, event)
        s.commit()
        assert result.handled is True
        assert result.note is not None and result.note.startswith("deleted_id=")
        assert (
            s.query(User).filter(User.clerk_user_id == "user_abc").one_or_none()
            is None
        )


def test_user_deleted_unknown_user_is_silent_noop(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        event = ClerkWebhookEvent(
            id="msg_7",
            type="user.deleted",
            data={"id": "user_never_seen"},
        )
        result = handle_event(s, event)
        s.commit()
        assert result.handled is True
        assert result.note == "user_not_in_db"


# ---------- unhandled / idempotency / signature -----------------------------


def test_unhandled_event_type_returns_not_handled(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        event = ClerkWebhookEvent(
            id="msg_8",
            type="session.created",
            data={"id": "sess_xyz"},
        )
        result = handle_event(s, event)
        assert result.handled is False
        assert result.event_type == "session.created"


def test_duplicate_event_short_circuits(tmp_path: Path) -> None:
    """Replay protection: same svix-id processed twice is a no-op."""
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        _make_user(s)
        event = ClerkWebhookEvent(
            id="msg_dup",
            type="user.updated",
            data={
                "id": "user_abc",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "first@example.com"},
                ],
                "primary_email_address_id": "idn_1",
            },
        )
        first = handle_event(s, event)
        s.commit()
        assert first.handled is True
        # Same event id, different inner payload — second call should
        # short-circuit on the dedup stamp regardless of what we'd
        # otherwise do.
        event2 = ClerkWebhookEvent(
            id="msg_dup",
            type="user.updated",
            data={
                "id": "user_abc",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "second@example.com"},
                ],
                "primary_email_address_id": "idn_1",
            },
        )
        second = handle_event(s, event2)
        s.commit()
        assert second.handled is True
        assert second.note == "duplicate_event"
        user = s.query(User).filter(User.clerk_user_id == "user_abc").one()
        # First write stuck; second was a no-op on the dedup short-circuit.
        assert user.email == "first@example.com"


def test_verify_signature_rejects_bad_signature() -> None:
    """A garbage signature must raise — Clerk's retry queue depends on
    400s being unrecoverable, so we never silently treat bad sigs as OK."""
    import pytest

    payload = json.dumps({"type": "user.created", "data": {"id": "x"}}).encode()
    headers = {
        "svix-id": "msg_x",
        "svix-timestamp": "1700000000",
        "svix-signature": "v1,garbage",
    }
    with pytest.raises(ValueError):
        verify_signature(
            payload=payload, headers=headers, secret="whsec_anything"
        )


def test_verify_signature_round_trip() -> None:
    """Positive control: a payload signed with the same secret verifies."""
    import base64
    from datetime import datetime, timezone
    from svix.webhooks import Webhook

    # svix expects the secret to be ``whsec_<base64>``. Build one from a
    # known-good raw key so the round-trip is reproducible.
    raw_secret = b"test-secret-key-32-bytes-padding"
    secret = "whsec_" + base64.b64encode(raw_secret).decode()
    payload_dict = {
        "type": "user.created",
        "data": {"id": "user_round_trip"},
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    msg_id = "msg_round"
    # svix's sign() expects a datetime, not a unix timestamp.
    ts_dt = datetime.fromtimestamp(int(datetime.now(tz=timezone.utc).timestamp()), tz=timezone.utc)
    ts_unix = str(int(ts_dt.timestamp()))
    sig = Webhook(secret).sign(msg_id, ts_dt, payload_bytes.decode("utf-8"))
    headers = {
        "svix-id": msg_id,
        "svix-timestamp": ts_unix,
        "svix-signature": sig,
    }
    event = verify_signature(
        payload=payload_bytes, headers=headers, secret=secret
    )
    assert event.id == msg_id
    assert event.type == "user.created"
    assert event.data["id"] == "user_round_trip"


# ---------- usage-event dedup row -------------------------------------------


def test_processed_event_records_usage_event_marker(tmp_path: Path) -> None:
    create_all(_db_url(tmp_path))
    with session_scope(_db_url(tmp_path)) as s:
        _make_user(s)
        event = ClerkWebhookEvent(
            id="msg_dedup",
            type="user.updated",
            data={
                "id": "user_abc",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "x@example.com"},
                ],
                "primary_email_address_id": "idn_1",
            },
        )
        handle_event(s, event)
        s.commit()
        events = (
            s.query(UsageEvent)
            .filter(UsageEvent.event_type == "clerk_webhook")
            .all()
        )
        assert len(events) == 1
        assert events[0].metadata_json.get("clerk_event_id") == "msg_dedup"
        assert events[0].metadata_json.get("clerk_event_type") == "user.updated"
