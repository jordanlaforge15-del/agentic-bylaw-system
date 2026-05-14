"""Clerk webhook event handling.

Clerk publishes user lifecycle events (``user.created``,
``user.updated``, ``user.deleted``, ``session.*``, ...) to a webhook URL
the operator configures in the Clerk dashboard. We only act on the user
lifecycle events: the Bearer-token verification path (see
``advisor.auth.clerk``) keeps the user row roughly in sync on every
chat call, but the webhook is what closes the gaps:

* A user whose email or name changes in Clerk but never logs in again
  would otherwise show stale data in our DB forever.
* A user deleted in Clerk would still own ``advisor_user`` rows (and
  the cascade-deleted ``advisor_chat_session`` / ``advisor_usage_event``
  history) until we explicitly remove them — relevant for GDPR-style
  erasure requests, and for keeping FK references clean.

Signature verification uses ``svix`` (Clerk's webhook provider). The
caller passes us the raw request body + the three ``svix-*`` headers
and we verify the HMAC. Failed verification raises ``ValueError`` so
the FastAPI route can return a 400 — Clerk treats non-2xx as
"please retry," which is the right behaviour for transient errors but
NOT for a bad-signature attack (where retry is pointless and we want
the operator to notice the alert in Clerk's dashboard).

Idempotency: Clerk retries with the same ``svix-id`` on non-2xx. We
record processed event ids in ``advisor_usage_event.metadata_json``
(``clerk_event_id``, ``event_type="clerk_webhook"``) and short-circuit
on second receipt — same pattern as the Stripe webhook so analytics
queries can find all webhook deliveries through one schema.

We deliberately do NOT raise from the per-event handlers: a 500 here
would trigger Clerk's retry queue, which only amplifies a bug. We log,
return ``handled=False``, and the route returns 200.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from advisor.db.models import UsageEvent, User
from layer1.db.base import utcnow

logger = logging.getLogger(__name__)


# Event types we act on. Clerk emits many more (``session.created``,
# ``organization.*``, etc.); anything outside this set returns
# ``handled=False`` and is logged at INFO so an operator can spot a
# misconfigured endpoint without log-spam.
_HANDLED_EVENT_TYPES = frozenset(
    {
        "user.created",
        "user.updated",
        "user.deleted",
    }
)


@dataclass(frozen=True)
class ClerkWebhookEvent:
    """A signature-verified Clerk webhook event.

    The ``id`` field is the ``svix-id`` value, which is what Clerk
    uses as the idempotency key on retries — NOT the Clerk-side
    ``id`` of the inner payload. Both are unique per delivery but
    only ``svix-id`` survives retries unchanged.
    """

    id: str
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of processing one Clerk webhook delivery."""

    handled: bool
    event_type: str
    event_id: str
    user_id: int | None = None
    note: str | None = None


def verify_signature(
    *,
    payload: bytes,
    headers: dict[str, str],
    secret: str,
) -> ClerkWebhookEvent:
    """Verify svix signature and return the parsed event.

    Args:
        payload: Raw request body bytes (NOT a parsed JSON dict — svix
            HMACs the byte sequence Clerk sent).
        headers: Dict-like view of the request headers. We need
            ``svix-id``, ``svix-timestamp``, ``svix-signature``;
            anything else is ignored.
        secret: ``CLERK_WEBHOOK_SECRET`` from env (starts with
            ``whsec_``). Comes from the Clerk dashboard, NOT the
            ``CLERK_SECRET_KEY`` used for the backend API.

    Raises:
        ValueError: Signature missing, malformed, or doesn't match.
    """
    # Lazy import: svix is in the [advisor] extra so dev installs
    # without that extra (some lint / docs builds) can still import
    # this module.
    try:
        from svix.webhooks import Webhook, WebhookVerificationError
    except ImportError as exc:  # pragma: no cover — only fires in misinstalled envs
        raise RuntimeError(
            "svix is required for Clerk webhook verification; install "
            "the advisor extra (pip install '.[advisor]')"
        ) from exc

    svix_headers = {
        "svix-id": headers.get("svix-id") or headers.get("Svix-Id") or "",
        "svix-timestamp": (
            headers.get("svix-timestamp") or headers.get("Svix-Timestamp") or ""
        ),
        "svix-signature": (
            headers.get("svix-signature") or headers.get("Svix-Signature") or ""
        ),
    }
    if not all(svix_headers.values()):
        raise ValueError("svix-id, svix-timestamp, and svix-signature are required")

    try:
        # Webhook.verify returns the parsed JSON payload as a dict on
        # success. It raises WebhookVerificationError on any failure
        # (bad signature, expired timestamp, malformed header).
        parsed = Webhook(secret).verify(payload, svix_headers)
    except WebhookVerificationError as exc:
        raise ValueError(f"signature verification failed: {exc}") from exc

    event_type = parsed.get("type")
    data = parsed.get("data") or {}
    if not isinstance(event_type, str) or not isinstance(data, dict):
        raise ValueError("clerk webhook payload missing type/data")
    return ClerkWebhookEvent(
        id=svix_headers["svix-id"],
        type=event_type,
        data=data,
    )


def handle_event(db: Session, event: ClerkWebhookEvent) -> WebhookResult:
    """Apply a verified Clerk event to the database.

    Caller is responsible for committing. We stage the changes and let
    the FastAPI route control the transaction boundary so a failed
    commit rolls back the whole effect cleanly.
    """
    if event.type not in _HANDLED_EVENT_TYPES:
        logger.info(
            "clerk webhook: ignoring unhandled event type %s (id=%s)",
            event.type,
            event.id,
        )
        return WebhookResult(
            handled=False, event_type=event.type, event_id=event.id
        )

    if _is_duplicate_event(db, event.id):
        logger.info(
            "clerk webhook: duplicate event %s (id=%s); skipping",
            event.type,
            event.id,
        )
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="duplicate_event",
        )

    try:
        if event.type == "user.created":
            result = _handle_user_created(db, event)
        elif event.type == "user.updated":
            result = _handle_user_updated(db, event)
        elif event.type == "user.deleted":
            result = _handle_user_deleted(db, event)
        else:  # pragma: no cover — guarded by _HANDLED_EVENT_TYPES
            return WebhookResult(
                handled=False, event_type=event.type, event_id=event.id
            )
    except Exception:  # noqa: BLE001 — see module docstring
        logger.exception(
            "clerk webhook: error handling event %s (id=%s); returning "
            "handled=False so the route can 200 and Clerk stops retrying",
            event.type,
            event.id,
        )
        return WebhookResult(
            handled=False,
            event_type=event.type,
            event_id=event.id,
            note="exception",
        )

    # Only stamp the dedup marker on successful effects. If the handler
    # returned early because the user wasn't in our DB (e.g. a Clerk
    # user that signed up but never hit the chat endpoint), there's
    # nothing to dedup against — the next delivery will repeat the
    # same no-op cheaply.
    if result.user_id is not None:
        _record_processed_event(db, event=event, user_id=result.user_id)
    return result


# ---------------------------------------------------------------------------
# Per-event-type handlers.
# ---------------------------------------------------------------------------


def _handle_user_created(
    db: Session, event: ClerkWebhookEvent
) -> WebhookResult:
    """Upsert a User row when Clerk announces account creation.

    We don't strictly need to act on this event — ``resolve_or_create_user``
    will create the row on first chat anyway — but mirroring it now
    keeps the admin invite-tracking surface accurate (operators see
    new users before they make their first query) and makes it
    possible to pre-populate user metadata from Clerk's profile.

    Idempotent: if the row already exists (e.g. created lazily by a
    chat call before the webhook reached us), we update the mutable
    profile fields and move on.
    """
    clerk_user_id = _string(event.data.get("id"))
    if not clerk_user_id:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="missing_clerk_user_id",
        )

    email = _primary_email(event.data)
    full_name = _full_name(event.data)

    user = (
        db.query(User).filter(User.clerk_user_id == clerk_user_id).one_or_none()
    )
    if user is None:
        user = User(
            clerk_user_id=clerk_user_id,
            email=email or "",
            full_name=full_name,
        )
        db.add(user)
        db.flush()
        # Issue the default trial credit pack so a webhook-created user
        # can open a case immediately. Idempotent — re-deliveries of
        # this event would find the row already present and never reach
        # this branch. We deliberately don't run the invite-redemption
        # check here; that lives in resolve_or_create_user and fires
        # the first time the user actually authenticates against the
        # API. If both fire, the starter helper is a no-op the second
        # time because credits already exist.
        from advisor.db.cases import (  # noqa: PLC0415
            grant_starter_credits_if_needed,
        )

        grant_starter_credits_if_needed(db, user=user)
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            user_id=user.id,
            note="created",
        )

    # Already exists — apply profile drift but don't blank out fields
    # if Clerk's payload happens to omit them.
    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if full_name is not None and user.full_name != full_name:
        user.full_name = full_name
        changed = True
    if changed:
        user.updated_at = utcnow()
        db.flush()
    return WebhookResult(
        handled=True,
        event_type=event.type,
        event_id=event.id,
        user_id=user.id,
        note="already_present",
    )


def _handle_user_updated(
    db: Session, event: ClerkWebhookEvent
) -> WebhookResult:
    """Sync profile changes (email, name) into the User row.

    Missing fields in the payload are NOT used to blank existing
    values — only an explicit Clerk update can clear a field, and the
    safer default is to keep what we have until that explicit clear
    arrives.
    """
    clerk_user_id = _string(event.data.get("id"))
    if not clerk_user_id:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="missing_clerk_user_id",
        )

    user = (
        db.query(User).filter(User.clerk_user_id == clerk_user_id).one_or_none()
    )
    if user is None:
        # The user updated their Clerk profile but has never hit our
        # backend. Nothing to do — first chat will create the row
        # with the current Clerk profile via resolve_or_create_user.
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="user_not_in_db",
        )

    email = _primary_email(event.data)
    full_name = _full_name(event.data)
    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if full_name is not None and user.full_name != full_name:
        user.full_name = full_name
        changed = True
    if changed:
        user.updated_at = utcnow()
        db.flush()
    return WebhookResult(
        handled=True,
        event_type=event.type,
        event_id=event.id,
        user_id=user.id,
        note="updated" if changed else "no_change",
    )


def _handle_user_deleted(
    db: Session, event: ClerkWebhookEvent
) -> WebhookResult:
    """Hard-delete the User row + cascade chat history / usage events.

    Clerk's user.deleted event arrives when a user deletes their
    account from the Clerk-hosted UserButton menu, when an admin
    deletes them from the Clerk dashboard, or via the Clerk API.
    The cascade is configured on the FK definitions in
    ``advisor.db.models`` (``ondelete="CASCADE"``), so removing the
    User row alone is sufficient — Postgres handles the rest.

    Rationale for hard-delete (vs soft-delete with a ``deleted_at``
    column): we have no product flow that distinguishes "deleted but
    recoverable" from "gone." GDPR-style erasure requests expect the
    rows to actually be gone. A soft-delete would only add complexity
    here and require teaching every downstream query to filter it
    out.
    """
    clerk_user_id = _string(event.data.get("id"))
    if not clerk_user_id:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="missing_clerk_user_id",
        )

    user = (
        db.query(User).filter(User.clerk_user_id == clerk_user_id).one_or_none()
    )
    if user is None:
        return WebhookResult(
            handled=True,
            event_type=event.type,
            event_id=event.id,
            note="user_not_in_db",
        )

    # Capture the id BEFORE deletion — the WebhookResult uses it for
    # log correlation and the dedup marker, but db.delete() detaches
    # the instance and reading attributes after that would error on
    # some SQLAlchemy configurations.
    deleted_id = user.id
    db.delete(user)
    db.flush()
    return WebhookResult(
        handled=True,
        event_type=event.type,
        event_id=event.id,
        # Intentionally NOT passing user_id here: the user row is
        # gone, and the dedup record we'd otherwise stamp uses it as
        # a FK. Skip the dedup record for deletes — a redelivery of
        # user.deleted for the same user will hit the "user_not_in_db"
        # branch above and short-circuit anyway.
        user_id=None,
        note=f"deleted_id={deleted_id}",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_duplicate_event(db: Session, event_id: str) -> bool:
    """True iff we've already recorded a usage-event for this svix id.

    Same shape as the Stripe webhook dedup (see
    ``advisor.billing.webhooks._is_duplicate_event``): fetch a small
    candidate set keyed on ``event_type="clerk_webhook"`` and filter
    the JSON column in Python so this works on sqlite (tests) as well
    as Postgres (prod).
    """
    if not event_id:
        return False
    stmt = (
        select(UsageEvent.id)
        .where(UsageEvent.event_type == "clerk_webhook")
        .limit(50)
    )
    for row in db.execute(stmt).all():
        usage_event = db.get(UsageEvent, row.id)
        if (
            usage_event is not None
            and usage_event.metadata_json.get("clerk_event_id") == event_id
        ):
            return True
    return False


def _record_processed_event(
    db: Session, *, event: ClerkWebhookEvent, user_id: int
) -> None:
    """Stamp a usage-event so a redelivery of ``event.id`` short-circuits."""
    db.add(
        UsageEvent(
            user_id=user_id,
            event_type="clerk_webhook",
            metadata_json={
                "clerk_event_id": event.id,
                "clerk_event_type": event.type,
            },
        )
    )


def _primary_email(data: dict[str, Any]) -> str | None:
    """Pull the primary email from a Clerk user payload.

    Clerk's payload shape:
        {
          "primary_email_address_id": "idn_...",
          "email_addresses": [
            {"id": "idn_...", "email_address": "user@example.com", ...},
            ...
          ],
        }

    We prefer the entry whose ``id`` matches ``primary_email_address_id``;
    if Clerk didn't mark one as primary (rare) we fall back to the
    first entry.
    """
    addrs = data.get("email_addresses")
    if not isinstance(addrs, list) or not addrs:
        # Some Clerk event shapes flatten this to a top-level
        # ``email_address`` string. Try that as a fallback.
        email = data.get("email_address")
        return email if isinstance(email, str) and email else None

    primary_id = data.get("primary_email_address_id")
    if isinstance(primary_id, str):
        for entry in addrs:
            if isinstance(entry, dict) and entry.get("id") == primary_id:
                email = entry.get("email_address")
                if isinstance(email, str) and email:
                    return email
    first = addrs[0]
    if isinstance(first, dict):
        email = first.get("email_address")
        if isinstance(email, str) and email:
            return email
    return None


def _full_name(data: dict[str, Any]) -> str | None:
    """Build a display name from Clerk's first_name / last_name fields.

    Returns ``None`` if both are missing — callers treat ``None`` as
    "leave the existing value alone" rather than "blank the field."
    """
    first = data.get("first_name")
    last = data.get("last_name")
    parts = [p for p in (first, last) if isinstance(p, str) and p.strip()]
    if not parts:
        return None
    return " ".join(p.strip() for p in parts)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
