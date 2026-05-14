"""Case lifecycle service — open / close / match / credit reserve / commit / refund / upgrade.

Replaces the v1 monthly-quota model in ``advisor.db.quota``. Callers
(the cases API router, the chat route's pre/post hooks, the admin
endpoints) call into this module rather than mutating ``CaseCredit``
rows directly. Every state-changing helper:

* opens its work inside the caller's ``Session`` (we never commit
  ourselves — the caller controls the transaction boundary so a chat
  route can wrap multiple operations in one commit, and a webhook can
  roll back on failure),
* uses ``with_for_update()`` on the credit/case rows it mutates, so
  concurrent upgrade attempts serialise correctly under Postgres
  (no-op on SQLite tests, but the partial unique index on
  ``case_credit.session_id WHERE state IN ('reserved','consumed')``
  still guarantees "one live credit per session"),
* writes a ``CaseEvent`` row capturing the transition for audit /
  analytics.

Anchor normalisation
--------------------
``anchor_key`` is the value the 30-day match runs against. We
normalise the raw user input so trivial differences ("1234 Main St,
Halifax, NS B3J 1A1" vs "1234 main street halifax") collapse to the
same key. The ``anchor_label`` field on the case keeps the verbatim
user text for display.

Address normalisation in particular is intentionally rough — we don't
do full address parsing because the tradeoff is wrong: a false
negative just means the user re-uses a credit unnecessarily (annoying
but recoverable), while a false positive silently merges what the
user thought were two distinct cases. Conservative collapse rules
keep us in the safe direction.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from advisor.db.models import (
    Case,
    CaseCredit,
    CaseEvent,
    CasePurchase,
    ChatSession as DbChatSession,
    User,
)


# Tier identifiers — duplicated locally rather than imported from
# ``advisor.billing.packs`` to break a circular import: this module is
# imported by ``advisor.billing.webhooks``, and going through
# ``advisor.billing.__init__`` would re-enter via the router/webhooks
# imports. Source of truth is ``advisor.billing.packs.TIER_ORDER``;
# keeping these in lock-step is enforced by the
# ``test_tier_constants_match_packs`` test.
TIER_ORDER: tuple[str, ...] = ("quick", "standard", "complex")
TIERS: frozenset[str] = frozenset(TIER_ORDER)


logger = logging.getLogger(__name__)


# 30-day reopen window from the brief. After this, a match against the
# same anchor opens a fresh case (and burns a fresh credit).
REOPEN_WINDOW = timedelta(days=30)

# Threshold for the in-memory abandon sweep — sessions that have a
# reserved credit but no qualifying tool output after this duration are
# auto-refunded by ``refund_abandoned_credits``.
ABANDONED_RESERVATION_GRACE = timedelta(hours=24)


class CaseError(Exception):
    """Base for case-service errors. Routes translate these to 4xx."""


class UnknownTierError(CaseError):
    """Caller asked for a tier identifier outside the catalog."""


class NoAvailableCreditError(CaseError):
    """User has no available credit of the requested tier.

    The cases router translates this to HTTP 409 with code
    ``no_available_credit`` so the frontend can prompt the user to
    purchase a PAYG credit of the requested tier.
    """

    def __init__(self, *, tier: str) -> None:
        super().__init__(f"no available credit of tier {tier!r}")
        self.tier = tier


class CaseStateError(CaseError):
    """Operation is invalid in the case's current state.

    Examples: trying to upgrade a case with no active credit, trying to
    consume an already-consumed credit, trying to upgrade DOWN.
    """


@dataclass(frozen=True)
class MatchResult:
    """Result of ``match_case`` — either an existing case to continue,
    or a hint that no match was found (caller mints a new case).

    ``case`` is set when an open or recently-closed case with the same
    anchor exists for the user within the 30-day window. ``case`` is
    ``None`` when no match exists; the caller should call ``open_case``
    to create one.
    """

    case: Case | None


# ---------------------------------------------------------------------------
# Anchor normalisation
# ---------------------------------------------------------------------------


# Order matters: postal codes and unit markers are stripped FIRST so
# the trailing province token surfaces at the end of the string,
# then the province pattern can match on its $-anchor. Reordering
# these silently breaks address matching.
_ADDRESS_NOISE_PATTERNS = (
    # Postal codes (Canadian "A1A 1A1" + permissive variants).
    re.compile(r"\b[a-z]\d[a-z]\s*\d[a-z]\d\b", re.I),
    # Unit / suite / apartment markers and the value after them. The
    # ``#`` form is also accepted; we tolerate trailing punctuation by
    # stopping at the first non-word character.
    re.compile(r"\b(apt|apartment|unit|suite|ste)\s*[\w-]+", re.I),
    re.compile(r"#\s*[\w-]+"),
    # Trailing province / country tokens — we normalise Canadian-only
    # for now; expand as we sell into other jurisdictions.
    re.compile(
        r",\s*(ns|nova\s*scotia|ontario|on|bc|british\s*columbia|alberta|ab|quebec|qc|canada|ca)\s*$",
        re.I,
    ),
)

_PROJECT_REF_NOISE = re.compile(r"[^a-z0-9-]+", re.I)


def normalise_anchor(label: str, kind: str) -> str:
    """Return the deterministic key used for the 30-day match.

    Rules:
      * ``address`` — lowercase, NFKD-normalise (strip diacritics),
        strip postal codes and unit markers and province suffixes,
        collapse whitespace.
      * ``project_ref`` / ``development_application`` — lowercase, keep
        only alphanumerics and hyphens. (``DA-2024-12345`` and
        ``da 2024 12345`` both collapse to ``da-2024-12345``.)
      * Anything else — lowercase + collapse whitespace as a fallback.

    Returns an empty string for an empty input — the caller is
    expected to validate non-empty before calling.
    """
    if not label:
        return ""
    text = unicodedata.normalize("NFKD", label)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    if kind == "address":
        for pattern in _ADDRESS_NOISE_PATTERNS:
            text = pattern.sub("", text)
        # Common abbreviations: collapse "street" → "st", "avenue" → "ave",
        # "road" → "rd", "drive" → "dr" so longhand and shorthand match.
        text = re.sub(r"\bstreet\b", "st", text)
        text = re.sub(r"\bavenue\b", "ave", text)
        text = re.sub(r"\broad\b", "rd", text)
        text = re.sub(r"\bdrive\b", "dr", text)
        text = re.sub(r"\bboulevard\b", "blvd", text)
        # Strip stray commas / periods the patterns above may have left.
        text = re.sub(r"[,.]", " ", text)
        # Collapse whitespace runs.
        text = re.sub(r"\s+", " ", text).strip()
        return text
    if kind in {"project_ref", "development_application"}:
        # Replace any non-[a-z0-9-] with a hyphen, collapse runs.
        text = _PROJECT_REF_NOISE.sub("-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text
    # Generic fallback.
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Match + open + close
# ---------------------------------------------------------------------------


def match_case(
    db: Session, *, user_id: int, anchor_label: str, anchor_kind: str
) -> MatchResult:
    """Find an active case for ``(user, anchor)`` within the 30-day window.

    Returns the most recently active match (so a user with an open and
    a closed case both within window gets the open one back). The
    frontend renders a "continue case?" banner from this; we do NOT
    auto-continue silently because that would invisibly merge what the
    user might think are distinct files.
    """
    key = normalise_anchor(anchor_label, anchor_kind)
    if not key:
        return MatchResult(case=None)
    cutoff = _utcnow() - REOPEN_WINDOW
    stmt = (
        select(Case)
        .where(
            Case.user_id == user_id,
            Case.anchor_key == key,
            Case.status.in_(["open", "closed"]),
            Case.last_activity_at >= cutoff,
        )
        .order_by(Case.last_activity_at.desc())
        .limit(1)
    )
    case = db.execute(stmt).scalar_one_or_none()
    return MatchResult(case=case)


def open_case(
    db: Session,
    *,
    user: User,
    anchor_label: str,
    anchor_kind: str,
    tier: str,
) -> tuple[Case, CaseCredit]:
    """Open a new case (or reopen a recent one) and reserve a credit.

    The caller must verify with ``match_case`` first if they want the
    "continue existing case?" UX — calling ``open_case`` always mints
    a fresh case (or reopens an in-window matching one), then reserves
    one credit of the requested tier against it.

    Raises:
        UnknownTierError: ``tier`` is not in the catalog.
        NoAvailableCreditError: the user has zero available credits at
            the requested tier.
    """
    if tier not in TIERS:
        raise UnknownTierError(f"unknown tier {tier!r}")

    # Reopen an in-window match if one exists; otherwise create.
    match = match_case(
        db, user_id=user.id, anchor_label=anchor_label, anchor_kind=anchor_kind
    )
    case = match.case
    now = _utcnow()
    if case is None:
        case = Case(
            user_id=user.id,
            anchor_label=anchor_label,
            anchor_key=normalise_anchor(anchor_label, anchor_kind),
            anchor_kind=anchor_kind,
            status="open",
            current_tier=tier,
            tokens_consumed=0,
            opened_at=now,
            last_activity_at=now,
        )
        db.add(case)
        db.flush()
        _record_event(
            db,
            case=case,
            user=user,
            credit=None,
            event_type="opened",
            payload={
                "anchor_kind": anchor_kind,
                "anchor_key": case.anchor_key,
                "tier": tier,
            },
        )
    elif case.status == "closed":
        case.status = "open"
        case.last_activity_at = now
        case.closed_at = None
        _record_event(
            db,
            case=case,
            user=user,
            credit=None,
            event_type="reopened",
            payload={"tier": tier},
        )

    credit = _claim_available_credit(db, user_id=user.id, tier=tier)
    if credit is None:
        raise NoAvailableCreditError(tier=tier)

    credit.case_id = case.id
    credit.state = "reserved"
    credit.reserved_at = now
    case.current_tier = credit.tier
    case.last_activity_at = now
    _record_event(
        db,
        case=case,
        user=user,
        credit=credit,
        event_type="credit_reserved",
        payload={"tier": credit.tier, "source": credit.source},
    )
    return case, credit


def close_case(db: Session, *, case: Case, reason: str = "user_request") -> None:
    """Mark a case closed. Reserved-but-uncommitted credits are refunded.

    Closing is idempotent — calling it on an already-closed case is a
    no-op except for an audit event (so the caller can record an admin
    "force-close" trail without crashing).
    """
    if case.status == "closed":
        return
    now = _utcnow()
    case.status = "closed"
    case.closed_at = now
    case.last_activity_at = now
    # Refund any still-reserved credit; consumed credits stay consumed.
    reserved = (
        db.execute(
            select(CaseCredit).where(
                CaseCredit.case_id == case.id,
                CaseCredit.state == "reserved",
            )
        )
        .scalars()
        .all()
    )
    for credit in reserved:
        _refund_credit(db, credit=credit, reason=f"case_closed:{reason}")
    _record_event(
        db,
        case=case,
        user=case.user,
        credit=None,
        event_type="closed",
        payload={"reason": reason, "refunded_credits": len(reserved)},
    )


# ---------------------------------------------------------------------------
# Credit lifecycle
# ---------------------------------------------------------------------------


def commit_credit_for_session(
    db: Session, *, session_id: int
) -> CaseCredit | None:
    """Flip the reserved credit for ``session_id`` to consumed.

    Idempotent: if the credit is already consumed (e.g. concurrent
    request, retry), this is a no-op. Returns the credit row (or
    ``None`` if no reserved credit was attached to the session — this
    happens for legacy sessions that opened before case-billing
    landed).

    Caller should invoke this from the chat session's
    ``on_turn_complete`` hook ONLY when the turn produced a "qualifying"
    result (non-empty assistant text + at least one prior tool call).
    See ``advisor.chat.session`` for the heuristic.
    """
    credit = (
        db.execute(
            select(CaseCredit)
            .where(
                CaseCredit.session_id == session_id,
                CaseCredit.state.in_(["reserved", "consumed"]),
            )
            .with_for_update()
        )
        .scalar_one_or_none()
    )
    if credit is None:
        return None
    if credit.state == "consumed":
        return credit
    now = _utcnow()
    credit.state = "consumed"
    credit.consumed_at = now
    if credit.case is not None:
        credit.case.last_activity_at = now
    _record_event(
        db,
        case=credit.case,
        user=credit.user,
        credit=credit,
        event_type="credit_consumed",
        payload={"tier": credit.tier},
    )
    return credit


def refund_credit_for_session(
    db: Session, *, session_id: int, reason: str
) -> CaseCredit | None:
    """Return a still-reserved credit to ``available`` state.

    No-op when the credit has already been consumed (we don't refund
    what the user got value from). Returns the credit row that was
    refunded, or ``None`` when no reserved credit is attached.
    """
    credit = (
        db.execute(
            select(CaseCredit)
            .where(
                CaseCredit.session_id == session_id,
                CaseCredit.state == "reserved",
            )
            .with_for_update()
        )
        .scalar_one_or_none()
    )
    if credit is None:
        return None
    return _refund_credit(db, credit=credit, reason=reason)


def add_tokens_to_case(
    db: Session, *, case_id: int, tokens: int
) -> int | None:
    """Bump the case's running token counter; returns the new total.

    Called from the chat session's per-turn hook so the per-case ledger
    feeds Layer-1 budget enforcement on the next turn. Returns
    ``None`` when the case row is missing.
    """
    case = db.get(Case, case_id, with_for_update=True)
    if case is None:
        return None
    case.tokens_consumed = max(0, (case.tokens_consumed or 0) + tokens)
    case.last_activity_at = _utcnow()
    return case.tokens_consumed


def upgrade_case_credit(
    db: Session,
    *,
    case: Case,
    target_tier: str,
    trigger: str,
) -> tuple[CaseCredit, CaseCredit]:
    """Atomically swap the case's active credit for one at a higher tier.

    Returns ``(burned_credit, new_credit)``. The burned credit ends up
    in state ``upgraded_out`` with ``upgraded_to_credit_id`` pointing
    at the new one; the new credit inherits the burned one's
    ``case_id`` / ``session_id`` / ``reserved_at`` / ``consumed_at`` so
    the chat session can keep going without losing context.

    Raises:
        UnknownTierError: ``target_tier`` is not in the catalog.
        CaseStateError: target tier is not strictly higher than current,
            or no active credit is attached to the case.
        NoAvailableCreditError: user has no available credit at
            ``target_tier``.
    """
    if target_tier not in TIERS:
        raise UnknownTierError(f"unknown tier {target_tier!r}")
    current = (
        db.execute(
            select(CaseCredit)
            .where(
                CaseCredit.case_id == case.id,
                CaseCredit.state.in_(["reserved", "consumed"]),
            )
            .with_for_update()
        )
        .scalar_one_or_none()
    )
    if current is None:
        raise CaseStateError(
            f"case {case.id} has no active credit; cannot upgrade"
        )
    if not _is_strict_upgrade(current.tier, target_tier):
        raise CaseStateError(
            f"target tier {target_tier!r} is not strictly higher than "
            f"current tier {current.tier!r}"
        )
    new = _claim_available_credit(db, user_id=case.user_id, tier=target_tier)
    if new is None:
        raise NoAvailableCreditError(tier=target_tier)

    now = _utcnow()
    # Capture the binding we want to transfer BEFORE we mutate either
    # row, then release the burned credit's session_id BEFORE we
    # assign it to the new credit. The partial unique index on
    # ``(session_id) WHERE state IN ('reserved','consumed')`` would
    # otherwise reject the simultaneous-write — it doesn't know we
    # intend to flip current's state to ``upgraded_out`` in the same
    # transaction.
    inherited_session_id = current.session_id
    inherited_reserved_at = current.reserved_at or now
    inherited_consumed_at = current.consumed_at
    inherited_state = current.state
    inherited_from_tier = current.tier

    # Step 1 — burn the original. Move it out of the partial-unique
    # window first.
    current.state = "upgraded_out"
    current.session_id = None
    current.case_id = None
    db.flush()

    # Step 2 — activate the new credit, inheriting the original's
    # session/case binding so the chat session continues seamlessly.
    new.case_id = case.id
    new.session_id = inherited_session_id
    new.state = inherited_state
    new.reserved_at = inherited_reserved_at
    new.consumed_at = inherited_consumed_at
    new.upgraded_from_tier = inherited_from_tier
    db.flush()

    current.upgraded_to_credit_id = new.id

    case.current_tier = target_tier
    case.last_activity_at = now

    _record_event(
        db,
        case=case,
        user=case.user,
        credit=new,
        event_type="upgrade_accepted",
        payload={
            "from_tier": current.tier,
            "to_tier": target_tier,
            "trigger": trigger,
        },
    )
    return current, new


# ---------------------------------------------------------------------------
# Credit issuance (admin / invite onboarding)
# ---------------------------------------------------------------------------


def grant_admin_credits(
    db: Session,
    *,
    user: User,
    tier: str,
    quantity: int,
    reason: str,
) -> list[CaseCredit]:
    """Issue ``quantity`` credits at ``tier`` to a user as an admin grant.

    Used by:
      * The invite-redemption path (first sign-in for an invitee with
        ``granted_starter_credits > 0``).
      * Admin tooling (``POST /v1/admin/users/{id}/credits``).

    Creates a synthetic ``CasePurchase`` row with
    ``pack_sku='admin_grant'`` and ``amount_paid_cents=0`` so the
    foreign-key constraint on ``CaseCredit.purchase_id`` holds without
    inventing a special-case nullable.
    """
    if tier not in TIERS:
        raise UnknownTierError(f"unknown tier {tier!r}")
    if quantity <= 0:
        return []
    purchase = CasePurchase(
        user_id=user.id,
        pack_sku="admin_grant",
        tier=tier,
        quantity=quantity,
        list_price_cents=0,
        discount_bps=0,
        amount_paid_cents=0,
        currency="CAD",
        stripe_checkout_session_id=None,
        stripe_payment_intent_id=None,
    )
    db.add(purchase)
    db.flush()
    credits: list[CaseCredit] = []
    for _ in range(quantity):
        credit = CaseCredit(
            user_id=user.id,
            purchase_id=purchase.id,
            tier=tier,
            source="admin_grant",
            state="available",
        )
        db.add(credit)
        credits.append(credit)
    db.flush()
    _record_event(
        db,
        case=None,
        user=user,
        credit=None,
        event_type="admin_credit_grant",
        payload={"tier": tier, "quantity": quantity, "reason": reason},
    )
    return credits


# Default trial allocation for any newly-created user that doesn't
# arrive with invite-driven starter credits. Mirrors the 3-standard
# gift the 0012_case_based_billing migration applied to pre-existing
# active users so the on-ramp is consistent across migration cohorts.
STARTER_GRANT_TIER = "standard"
STARTER_GRANT_QUANTITY = 3


def grant_starter_credits_if_needed(db: Session, *, user: User) -> bool:
    """Issue the default trial credit pack to a user with no credits yet.

    Idempotent by construction: we only grant when the user has zero
    ``CaseCredit`` rows of any state. Invite redemptions (which run
    earlier in the user-creation flow) leave behind ``available``
    credits, so invited users with starter packs are correctly skipped.

    Returns ``True`` if credits were granted, ``False`` if the user
    already had credits and the call was a no-op.
    """
    has_any_credit = (
        db.query(CaseCredit.id)
        .filter(CaseCredit.user_id == user.id)
        .first()
        is not None
    )
    if has_any_credit:
        return False
    grant_admin_credits(
        db,
        user=user,
        tier=STARTER_GRANT_TIER,
        quantity=STARTER_GRANT_QUANTITY,
        reason="signup_starter_grant",
    )
    return True


def issue_credits_from_pack_purchase(
    db: Session,
    *,
    user: User,
    purchase: CasePurchase,
) -> list[CaseCredit]:
    """Insert one ``CaseCredit`` per quantity-unit of the purchase.

    Called from the Stripe webhook handler after the ``CasePurchase``
    row has been built and added to the session. The unique constraint
    on ``CasePurchase.stripe_checkout_session_id`` is the second
    idempotency layer beneath the existing event-id dedupe — even if a
    duplicate webhook slips past the event check, the purchase insert
    fails and we never double-issue credits.
    """
    if purchase.id is None:
        db.flush()
    credits: list[CaseCredit] = []
    for _ in range(purchase.quantity):
        credit = CaseCredit(
            user_id=user.id,
            purchase_id=purchase.id,
            tier=purchase.tier,
            source=purchase.pack_sku,
            state="available",
        )
        db.add(credit)
        credits.append(credit)
    db.flush()
    return credits


# ---------------------------------------------------------------------------
# Read helpers (frontend / admin queries)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditBalance:
    """Per-tier credit balance for a user, broken down by state."""

    tier: str
    available: int
    reserved: int
    consumed: int


def credit_balance_for(db: Session, *, user_id: int) -> list[CreditBalance]:
    """Return per-tier credit counts for the user, ordered by tier rank.

    One row per tier even when the user has zero credits at that tier
    — keeps the frontend rendering simple (no holes to handle).
    """
    rows = db.execute(
        select(
            CaseCredit.tier,
            CaseCredit.state,
            func.count(CaseCredit.id),
        )
        .where(CaseCredit.user_id == user_id)
        .group_by(CaseCredit.tier, CaseCredit.state)
    ).all()
    by_tier: dict[str, dict[str, int]] = {
        t: {"available": 0, "reserved": 0, "consumed": 0} for t in TIER_ORDER
    }
    for tier, state, count in rows:
        if tier not in by_tier:
            by_tier[tier] = {"available": 0, "reserved": 0, "consumed": 0}
        if state in by_tier[tier]:
            by_tier[tier][state] += int(count)
    return [
        CreditBalance(
            tier=tier,
            available=counts["available"],
            reserved=counts["reserved"],
            consumed=counts["consumed"],
        )
        for tier, counts in by_tier.items()
    ]


def list_user_cases(
    db: Session, *, user_id: int, limit: int = 50
) -> list[Case]:
    """Newest-first list of the user's cases, capped at ``limit``."""
    stmt = (
        select(Case)
        .where(Case.user_id == user_id)
        .order_by(Case.last_activity_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# Background sweeps
# ---------------------------------------------------------------------------


def close_expired_cases(db: Session) -> int:
    """Flip status='open' cases past the 30-day idle window to 'closed'.

    Returns the count of cases closed. Run from a cron / scheduled
    sweep (e.g. once a day) so old cases don't linger in 'open'
    forever and inflate the active-case dashboard.
    """
    cutoff = _utcnow() - REOPEN_WINDOW
    expired = (
        db.execute(
            select(Case).where(
                Case.status == "open",
                Case.last_activity_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    for case in expired:
        close_case(db, case=case, reason="idle_timeout")
    return len(expired)


def refund_abandoned_credits(db: Session) -> int:
    """Refund credits whose session never produced a qualifying turn.

    Runs the abandoned-session sweep: any credit reserved for a session
    older than ``ABANDONED_RESERVATION_GRACE`` whose case has no
    activity in that window is refunded back to ``available``. Returns
    the count refunded.
    """
    cutoff = _utcnow() - ABANDONED_RESERVATION_GRACE
    candidates = (
        db.execute(
            select(CaseCredit)
            .where(
                CaseCredit.state == "reserved",
                CaseCredit.reserved_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    refunded = 0
    for credit in candidates:
        # Only refund when the case shows no activity since the credit
        # was reserved — protects against refunding a slow-but-active
        # case where the user is just thinking between turns.
        if credit.case is not None and credit.case.last_activity_at >= cutoff:
            continue
        _refund_credit(db, credit=credit, reason="abandoned_session_timeout")
        refunded += 1
    return refunded


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _claim_available_credit(
    db: Session, *, user_id: int, tier: str
) -> CaseCredit | None:
    """FIFO-claim one available credit at ``tier`` for ``user_id``.

    Uses ``with_for_update(skip_locked=True)`` so two concurrent
    case-open calls for the same user/tier don't deadlock — the second
    request just grabs the next row in line.
    """
    stmt = (
        select(CaseCredit)
        .where(
            CaseCredit.user_id == user_id,
            CaseCredit.tier == tier,
            CaseCredit.state == "available",
        )
        .order_by(CaseCredit.purchased_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return db.execute(stmt).scalar_one_or_none()


def _refund_credit(
    db: Session, *, credit: CaseCredit, reason: str
) -> CaseCredit:
    """State transition: reserved → available + audit row.

    Pulled out because two callers need it (per-session refund and
    the case-close cascade). The credit's session/case binding is
    cleared so a subsequent reserve picks it up cleanly.
    """
    if credit.state != "reserved":
        return credit
    user = credit.user
    case = credit.case
    credit.state = "available"
    credit.session_id = None
    credit.case_id = None
    credit.reserved_at = None
    _record_event(
        db,
        case=case,
        user=user,
        credit=credit,
        event_type="credit_refunded",
        payload={"tier": credit.tier, "reason": reason},
    )
    return credit


def _record_event(
    db: Session,
    *,
    case: Case | None,
    user: User | None,
    credit: CaseCredit | None,
    event_type: str,
    payload: dict,
) -> None:
    """Write one row to ``advisor_case_event``.

    Audit-only — never raises (a logging-style helper). The
    ``user`` argument may be ``None`` only for system-generated
    events that aren't user-attributable; case-events almost always
    carry a user since cases are user-owned.
    """
    user_id = user.id if user is not None else (
        case.user_id if case is not None else None
    )
    if user_id is None:
        # Defensive: every event in the case lifecycle is user-bound;
        # if we ever try to write a userless event, log loudly rather
        # than crash the calling transaction.
        logger.warning(
            "case event %r dropped: no user_id resolvable", event_type
        )
        return
    db.add(
        CaseEvent(
            case_id=case.id if case is not None else None,
            user_id=user_id,
            credit_id=credit.id if credit is not None else None,
            event_type=event_type,
            payload_json=dict(payload),
        )
    )


def _is_strict_upgrade(current: str, target: str) -> bool:
    """True iff ``target`` is strictly higher in the tier order."""
    if current not in TIER_ORDER or target not in TIER_ORDER:
        return False
    return TIER_ORDER.index(target) > TIER_ORDER.index(current)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Backwards-compat shim — the old ``advisor.db.quota`` module exposed a
# minimal ``QuotaExceeded`` class. Some test fixtures import it; keep a
# stub here so a casual ``from advisor.db.cases import QuotaExceeded``
# (typo) raises an ImportError loudly rather than a confusing AttributeError.
# ---------------------------------------------------------------------------


__all__: Iterable[str] = (
    "CaseError",
    "UnknownTierError",
    "NoAvailableCreditError",
    "CaseStateError",
    "MatchResult",
    "CreditBalance",
    "REOPEN_WINDOW",
    "ABANDONED_RESERVATION_GRACE",
    "normalise_anchor",
    "match_case",
    "open_case",
    "close_case",
    "commit_credit_for_session",
    "refund_credit_for_session",
    "add_tokens_to_case",
    "upgrade_case_credit",
    "grant_admin_credits",
    "grant_starter_credits_if_needed",
    "STARTER_GRANT_TIER",
    "STARTER_GRANT_QUANTITY",
    "issue_credits_from_pack_purchase",
    "credit_balance_for",
    "list_user_cases",
    "close_expired_cases",
    "refund_abandoned_credits",
)
