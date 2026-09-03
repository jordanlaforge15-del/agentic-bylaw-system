"""Token wallet service — grant / top-up / burn / adjust / read (ABS-380).

The prepaid token wallet (design spec D1) is a signed running balance on
``User.token_balance`` backed by the append-only ``advisor_token_transaction``
ledger. This module is the *only* code that mutates the balance: callers
(the signup grant, the top-up webhook, chat settlement, admin corrections)
go through these helpers rather than touching ``token_balance`` directly, so
every balance change writes a matching ledger row in the same transaction
and the invariant ``SUM(amount_tokens) == token_balance`` holds forever.

Transaction discipline mirrors ``advisor.db.cases``:

* We never commit — the caller owns the transaction boundary (a webhook can
  roll back on failure; the auth dependency batches the grant with the user
  insert).
* Every mutation locks the user row with ``SELECT … FOR UPDATE`` so
  concurrent burns/top-ups serialise on Postgres (a no-op on sqlite, which
  is why the concurrency test is Postgres-gated).

Overdraw is by design: ``burn_tokens`` has **no floor**. A chat turn burns
its actual measured usage; if that overshoots the remaining balance the
wallet goes negative. The floor is a *pre-flight* concern (refuse to start a
turn at ``balance <= floor``), enforced upstream — not a ledger invariant.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from advisor.db.models import TokenTransaction, User

logger = logging.getLogger(__name__)

# Ledger entry types (mirror ``TokenTransaction.entry_type``).
ENTRY_GRANT = "grant"
ENTRY_TOPUP = "topup"
ENTRY_BURN = "burn"
ENTRY_ADJUST = "adjust"

# The ``reason`` stamped on the one-time signup free-trial grant. Load-bearing
# beyond provenance: the partial unique index
# ``uq_advisor_token_transaction_signup_grant`` keys off this exact literal
# (see ``TokenTransaction.__table_args__``), so it is the DB's definition of
# "the signup grant" and must not drift.
REASON_SIGNUP_GRANT = "signup_grant"

# The ``reason`` stamped on a self-serve beta refill (ABS-405). Load-bearing:
# the cooldown and the lifetime cap are both computed by counting ledger rows
# carrying exactly this reason, so there is no separate counter to drift out
# of sync with the ledger — and an operator reading a statement can tell a
# refill apart from the signup grant and from a hand-made admin gift.
REASON_BETA_REFILL = "beta_refill"

# ``BetaRefillState.status`` values. "available" means a claim would succeed
# right now; the rest are the reasons it wouldn't.
REFILL_AVAILABLE = "available"
REFILL_COOLDOWN = "cooldown"
REFILL_EXHAUSTED = "exhausted"
REFILL_DISABLED = "disabled"
REFILL_GRANTED = "granted"


def _apply_entry(
    db: Session,
    *,
    user_id: int,
    entry_type: str,
    amount: int,
    reason: str | None,
    stripe_checkout_session_id: str | None = None,
    session_id: int | None = None,
    case_id: int | None = None,
    usage_event_id: int | None = None,
    metadata: dict | None = None,
) -> TokenTransaction:
    """Lock the user row, move the balance by ``amount``, write one ledger row.

    ``amount`` is the *signed* delta actually applied to ``token_balance``
    (positive for grant/topup, negative for burn). ``balance_after`` is
    snapshotted post-move so a statement view never has to re-sum the ledger.
    Flushes so the caller sees ``.id`` / ``.balance_after``; does not commit.
    """
    # ``populate_existing`` is load-bearing for correctness under
    # concurrency: without it, if the caller already loaded this ``User``
    # into the session's identity map (e.g. the auth dependency resolved it,
    # or chat settlement read the row a moment earlier), the ``FOR UPDATE``
    # re-select returns that *cached* instance with its stale ``token_balance``
    # — and the read-modify-write races into a lost update. Forcing a
    # refresh makes the locked read reflect the row's true current value.
    locked = db.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    new_balance = locked.token_balance + amount
    locked.token_balance = new_balance
    txn = TokenTransaction(
        user_id=locked.id,
        entry_type=entry_type,
        amount_tokens=amount,
        balance_after=new_balance,
        reason=reason,
        stripe_checkout_session_id=stripe_checkout_session_id,
        session_id=session_id,
        case_id=case_id,
        usage_event_id=usage_event_id,
        metadata_json=dict(metadata or {}),
    )
    db.add(txn)
    db.flush()
    return txn


def grant_tokens(
    db: Session,
    *,
    user: User,
    amount: int,
    reason: str = "grant",
    session_id: int | None = None,
    case_id: int | None = None,
    metadata: dict | None = None,
) -> TokenTransaction:
    """Add ``amount`` tokens to the wallet as a ``grant`` entry.

    Used by the signup free-trial grant and by admin gifts. ``amount`` must
    be positive — a grant only ever adds tokens. Returns the ledger row.
    """
    if amount <= 0:
        raise ValueError(f"grant amount must be positive, got {amount}")
    return _apply_entry(
        db,
        user_id=user.id,
        entry_type=ENTRY_GRANT,
        amount=amount,
        reason=reason,
        session_id=session_id,
        case_id=case_id,
        metadata=metadata,
    )


def credit_topup(
    db: Session,
    *,
    user: User,
    amount: int,
    stripe_checkout_session_id: str,
    reason: str = "topup",
    metadata: dict | None = None,
) -> TokenTransaction | None:
    """Credit a paid top-up, idempotent on ``stripe_checkout_session_id``.

    The Stripe webhook can fire more than once for the same checkout
    (retries, event replay). The UNIQUE constraint on
    ``stripe_checkout_session_id`` guarantees the wallet is credited exactly
    once: the insert and the balance move happen inside a SAVEPOINT, so when
    the duplicate insert trips the unique index the ``IntegrityError`` rolls
    back *both* — no double credit — and we return the pre-existing entry
    (or ``None`` if it can't be re-read). The caller therefore never sees a
    5xx from a replayed webhook.

    ``amount`` must be positive.
    """
    if amount <= 0:
        raise ValueError(f"top-up amount must be positive, got {amount}")
    try:
        with db.begin_nested():
            return _apply_entry(
                db,
                user_id=user.id,
                entry_type=ENTRY_TOPUP,
                amount=amount,
                reason=reason,
                stripe_checkout_session_id=stripe_checkout_session_id,
                metadata=metadata,
            )
    except IntegrityError:
        logger.info(
            "duplicate token top-up for checkout session %s absorbed",
            stripe_checkout_session_id,
        )
        return db.execute(
            select(TokenTransaction).where(
                TokenTransaction.stripe_checkout_session_id
                == stripe_checkout_session_id
            )
        ).scalar_one_or_none()


def burn_tokens(
    db: Session,
    *,
    user: User,
    amount: int,
    reason: str = "chat_turn",
    session_id: int | None = None,
    case_id: int | None = None,
    usage_event_id: int | None = None,
    metadata: dict | None = None,
) -> TokenTransaction:
    """Subtract ``amount`` tokens as a ``burn`` entry — **no floor**.

    ``amount`` is the positive token count to burn (measured chat usage);
    the ledger stores it as a negative delta. There is deliberately no
    lower bound: if the burn exceeds the remaining balance the wallet goes
    negative (overdraw by design — the floor is enforced pre-flight). Never
    raises for insufficient balance.
    """
    if amount <= 0:
        raise ValueError(f"burn amount must be positive, got {amount}")
    return _apply_entry(
        db,
        user_id=user.id,
        entry_type=ENTRY_BURN,
        amount=-amount,
        reason=reason,
        session_id=session_id,
        case_id=case_id,
        usage_event_id=usage_event_id,
        metadata=metadata,
    )


def adjust_tokens(
    db: Session,
    *,
    user: User,
    amount: int,
    reason: str,
    metadata: dict | None = None,
) -> TokenTransaction:
    """Apply a signed manual correction as an ``adjust`` entry.

    ``amount`` may be positive (credit) or negative (debit) but not zero.
    ``reason`` is required — a manual adjustment must record why.
    """
    if amount == 0:
        raise ValueError("adjust amount must be non-zero")
    if not reason:
        raise ValueError("adjust requires a reason")
    return _apply_entry(
        db,
        user_id=user.id,
        entry_type=ENTRY_ADJUST,
        amount=amount,
        reason=reason,
        metadata=metadata,
    )


def grant_signup_tokens_if_needed(db: Session, *, user: User) -> bool:
    """Issue the one-time signup token grant to a user's wallet.

    Idempotent on the ``token_grant_issued`` metadata flag — the *sole*
    gate. Because it runs on every authenticated request (from
    ``resolve_or_create_user``), it self-heals pre-wallet users the first
    time they sign in after this ships: no backfill migration needed. Once
    the flag is set it is a no-op, so a user who burned their grant down to
    zero (or negative) never receives a second one.

    The grant amount is read fresh from ``ADVISOR_SIGNUP_TOKEN_GRANT`` so a
    launch-time re-calibration takes effect without a restart. A configured
    amount of 0 still marks the flag (the user is considered onboarded) but
    writes no ledger row.

    Concurrency (ABS-404)
    ---------------------
    The flag check and the flag set MUST happen under the user row's
    write lock. This function runs on *every* authenticated request, and
    a fresh sign-in fires several of them in parallel; when the
    check-and-set was a plain in-memory read of ``user.metadata_json``,
    two concurrent requests both saw the flag unset, both set it, and
    both wrote a grant row. Production did exactly that on 2026-07-17:
    two ``+25,000`` ledger entries for one user, same timestamp to the
    second. ``grant_tokens`` locks the row, but only *after* the race
    has already been lost, so the lock protected the arithmetic and not
    the idempotency.

    So we take ``SELECT … FOR UPDATE`` here and re-read the flag from
    the locked row. ``populate_existing=True`` is load-bearing for the
    same reason it is in ``_apply_entry``: ``user`` is already in the
    session's identity map (the auth dependency just resolved it), so
    without it the locked re-select hands back the cached instance and
    its stale ``metadata_json`` — re-opening the very race the lock is
    here to close. On sqlite ``FOR UPDATE`` is a no-op, which is why the
    regression test for this is Postgres-gated, alongside the existing
    lost-update test in ``test_wallet_concurrency_pg.py``.

    Schema backstop (ABS-415)
    -------------------------
    The lock above is an *application* invariant: it holds only for
    callers that take it, in a transaction that reaches Postgres. It
    does not survive a future caller that grants the signup tokens by
    some other path, a hand-run SQL insert, or a refactor that drops the
    ``FOR UPDATE``. So the "at most one signup grant per user" rule is
    also written into the schema as the partial unique index
    ``uq_advisor_token_transaction_signup_grant`` — the same
    defence-in-depth shape ``credit_topup`` already gets from the UNIQUE
    on ``stripe_checkout_session_id``.

    Because that index turns a lost race into an ``IntegrityError``, the
    insert runs inside a SAVEPOINT: tripping the index rolls the ledger
    row *and* the balance move back together (the balance never moves
    without its ledger row), and we return ``False`` instead of letting
    a 500 escape into an ordinary authenticated request. The flag is
    still set on the way out — the user demonstrably holds a grant, so
    every subsequent request should short-circuit at the check above
    rather than re-trip the index.

    Returns ``True`` if the grant was applied this call, ``False`` if the
    user already held it. Does not commit — the caller owns the transaction.
    """
    from advisor.billing.turns import signup_token_grant  # noqa: PLC0415

    locked = db.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if locked.metadata_json.get("token_grant_issued"):
        return False
    amount = signup_token_grant()
    if amount > 0:
        try:
            # SAVEPOINT so a tripped unique index rolls back the ledger
            # row and the balance move as one, leaving the outer
            # transaction (and the row lock) intact.
            with db.begin_nested():
                grant_tokens(
                    db,
                    user=locked,
                    amount=amount,
                    reason=REASON_SIGNUP_GRANT,
                )
        except IntegrityError:
            # Schema said no: this user already holds a signup grant that
            # our locked read didn't see. Absorb it, record the flag so we
            # stop trying, and report "already granted".
            logger.warning(
                "duplicate signup token grant for user %s refused by "
                "uq_advisor_token_transaction_signup_grant; "
                "the wallet was not double-credited",
                locked.id,
            )
            locked.metadata_json["token_grant_issued"] = True
            db.flush()
            return False
    # Set the flag *after* the grant: a SAVEPOINT rollback expires the
    # objects it touched, and setting it first would make the flag's fate
    # depend on whether the insert succeeded.
    locked.metadata_json["token_grant_issued"] = True
    db.flush()
    return True


@dataclass(frozen=True)
class BetaRefillState:
    """Whether the payments-off self-serve refill is claimable (ABS-405).

    ``status`` is one of ``available`` / ``cooldown`` / ``exhausted`` /
    ``disabled`` — or ``granted``, which only ever comes back from
    ``claim_beta_refill`` and means the tokens landed on this call.

    ``tokens`` is what one claim is worth; ``tokens_granted`` is what this
    call actually credited (0 for every read and for a refused claim).
    ``next_available_at`` is set only for ``cooldown`` — it is the instant
    the next claim unlocks, so the UI can say "more turns in 4h" instead of
    a bare "no".
    """

    status: str
    tokens: int
    grants_used: int
    grants_remaining: int
    next_available_at: datetime | None = None
    tokens_granted: int = 0


def _as_utc(value: datetime) -> datetime:
    """Coerce a ledger timestamp to an aware UTC datetime.

    ``created_at`` is ``DateTime(timezone=True)`` and always written aware,
    but sqlite (unit tests) has no native tz storage and hands the value
    back naive. Treating a naive read as UTC keeps the cooldown arithmetic
    correct on both backends instead of raising on the comparison.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _beta_refill_history(
    db: Session, *, user_id: int
) -> tuple[int, datetime | None]:
    """Return ``(claims_made, newest_claim_at)`` from the ledger.

    The ledger is the only bookkeeping: refills are ``grant`` rows stamped
    with ``REASON_BETA_REFILL``, so there is no counter column that can
    disagree with the audit trail. Bounded by the lifetime cap, so reading
    the rows rather than aggregating them is cheap and dodges the
    backend-dependent typing of ``MAX()`` over a timestamp.
    """
    rows = list(
        db.execute(
            select(TokenTransaction.created_at)
            .where(TokenTransaction.user_id == user_id)
            .where(TokenTransaction.entry_type == ENTRY_GRANT)
            .where(TokenTransaction.reason == REASON_BETA_REFILL)
            .order_by(TokenTransaction.created_at.desc())
        ).scalars()
    )
    newest = _as_utc(rows[0]) if rows else None
    return len(rows), newest


def beta_refill_state(
    db: Session, *, user_id: int, now: datetime | None = None
) -> BetaRefillState:
    """Evaluate the beta-refill policy for a user without granting anything.

    Read-only: safe to call from ``GET /wallet`` on every page load. The
    policy is (in order) the enabled flag and a non-zero grant size, then
    the lifetime cap, then the cooldown since the newest claim. Callers
    still have to layer the payments-off gate on top — this function knows
    the wallet, not the deployment's payment posture.
    """
    from advisor.billing import turns  # noqa: PLC0415

    tokens = turns.beta_refill_tokens()
    max_grants = turns.beta_refill_max_grants()
    if not turns.beta_refill_enabled() or tokens <= 0 or max_grants <= 0:
        return BetaRefillState(
            status=REFILL_DISABLED,
            tokens=0,
            grants_used=0,
            grants_remaining=0,
        )

    used, newest = _beta_refill_history(db, user_id=user_id)
    remaining = max(0, max_grants - used)
    if remaining == 0:
        return BetaRefillState(
            status=REFILL_EXHAUSTED,
            tokens=tokens,
            grants_used=used,
            grants_remaining=0,
        )

    cooldown = timedelta(hours=turns.beta_refill_cooldown_hours())
    moment = now or datetime.now(timezone.utc)
    if newest is not None and cooldown:
        unlocks_at = newest + cooldown
        if moment < unlocks_at:
            return BetaRefillState(
                status=REFILL_COOLDOWN,
                tokens=tokens,
                grants_used=used,
                grants_remaining=remaining,
                next_available_at=unlocks_at,
            )
    return BetaRefillState(
        status=REFILL_AVAILABLE,
        tokens=tokens,
        grants_used=used,
        grants_remaining=remaining,
    )


def claim_beta_refill(
    db: Session, *, user: User, now: datetime | None = None
) -> BetaRefillState:
    """Claim one self-serve beta refill, or explain why it was refused.

    Returns a ``granted`` state (with ``tokens_granted`` set) when the
    tokens landed, otherwise the same refusal state ``beta_refill_state``
    would have reported. Never raises for a refused claim — "you're on
    cooldown" is a normal answer, not an error.

    Concurrency mirrors ``grant_signup_tokens_if_needed``: the policy is
    re-evaluated **under the user row's write lock**, so two clicks racing
    (a double-tapped button, a retried request) serialise. The second one
    blocks until the first commits, then counts the row the first wrote and
    correctly reports cooldown. ``populate_existing`` is load-bearing for
    the same reason it is there — the caller's ``user`` is already in the
    identity map, and without a forced refresh the locked re-select hands
    back the cached instance and its stale balance.

    Does not commit — the caller owns the transaction boundary.
    """
    locked = db.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    state = beta_refill_state(db, user_id=locked.id, now=now)
    if state.status != REFILL_AVAILABLE:
        return state
    grant_tokens(
        db,
        user=locked,
        amount=state.tokens,
        reason=REASON_BETA_REFILL,
    )
    logger.info(
        "beta refill granted: user=%s tokens=%d claim=%d/%d",
        locked.id,
        state.tokens,
        state.grants_used + 1,
        state.grants_used + state.grants_remaining,
    )
    return BetaRefillState(
        status=REFILL_GRANTED,
        tokens=state.tokens,
        grants_used=state.grants_used + 1,
        grants_remaining=state.grants_remaining - 1,
        tokens_granted=state.tokens,
    )


def get_balance(db: Session, *, user_id: int) -> int:
    """Return the user's current wallet balance (0 if the user is unknown)."""
    balance = db.execute(
        select(User.token_balance).where(User.id == user_id)
    ).scalar_one_or_none()
    return int(balance) if balance is not None else 0


def list_transactions(
    db: Session,
    *,
    user_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> list[TokenTransaction]:
    """Return the user's ledger rows newest-first, id-cursor paged.

    Ownership-scoped: only rows for ``user_id`` are ever returned.
    ``before_id`` is an exclusive cursor — pass the id of the oldest row you
    have to fetch the next page. ``limit`` is the page size.
    """
    query = select(TokenTransaction).where(TokenTransaction.user_id == user_id)
    if before_id is not None:
        query = query.where(TokenTransaction.id < before_id)
    query = query.order_by(TokenTransaction.id.desc()).limit(limit)
    return list(db.execute(query).scalars().all())
