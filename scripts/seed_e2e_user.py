"""Seed the e2e test database with a deterministic demo user + credits.

The Playwright suite logs in via the ``X-Test-User-Id`` header bypass
(see ``advisor.api.app:_build_user_dependency``). For the case-credit
endpoints to work, an ``advisor_user`` row must exist whose
``clerk_user_id`` matches the header value, and that user must hold
enough available credits to open cases at each tier.

This script is idempotent: running it twice on the same database is
safe and leaves the user with the same number of available credits
(any extras from prior runs are left alone).

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \
        .venv/bin/python scripts/seed_e2e_user.py

Args (all optional):
    --user-id          External id used in X-Test-User-Id header. Default
                       ``demo-user-1`` (matches ``ADVISOR_DEMO_USER_ID``
                       fallback in ``web/lib/advisor-auth.ts``).
    --email            Email column. Default ``demo@example.com``.
    --credits-per-tier Number of available credits to maintain per tier.
                       Default ``5`` — enough for a full smoke + functional
                       run without running out.
"""
from __future__ import annotations

import argparse
import sys
from sqlalchemy import select, text

from advisor.db.cases import grant_admin_credits
from advisor.db.models import CaseCredit, TermsAcceptance, User
from advisor.db.wallet import get_balance, grant_tokens
from advisor.legal import get_current_terms
from layer1.db.session import session_scope


TIERS = ("quick", "standard", "complex")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default="demo-user-1")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--credits-per-tier", type=int, default=100)
    parser.add_argument(
        "--token-balance",
        type=int,
        default=100_000_000,
        help=(
            "Minimum token-wallet balance to maintain for the shared demo "
            "user (ABS-383). Every chat turn now burns measured tokens from "
            "the account wallet, so the shared demo user — hit by dozens of "
            "chat turns across a full parallel suite — would otherwise drain "
            "past the pre-flight floor and start 402ing mid-run. Top the "
            "wallet up to this large floor so a whole suite can't exhaust it. "
            "The per-turn burn/floor semantics are exercised by the ABS-383 "
            "spec, which mints its own throwaway identities."
        ),
    )
    parser.add_argument(
        "--unlimited",
        action="store_true",
        help="Set unlimited_credits=True on the user (ABS-12).",
    )
    parser.add_argument(
        "--free-questions",
        type=int,
        default=None,
        help=(
            "Set free_questions_remaining to this value. "
            "When omitted, the column is left at its current value "
            "(0 for new users, unchanged for existing ones)."
        ),
    )
    args = parser.parse_args()

    with session_scope() as db:
        # ABS-207: serialise against concurrent Playwright workers.
        # globalSetup runs this once, but abs9-credit-adoption.spec.ts,
        # credit-gating-consistency.spec.ts, and unlimited-credits.spec.ts
        # also call it from their per-worker beforeAll hooks where two
        # workers can race the User INSERT and the per-tier credit grants.
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601203))

        user = (
            db.query(User)
            .filter(User.clerk_user_id == args.user_id)
            .one_or_none()
        )
        if user is None:
            user = User(
                clerk_user_id=args.user_id,
                email=args.email,
                requests_per_minute_limit=600,
            )
            db.add(user)
            db.flush()
            print(f"created advisor_user id={user.id} clerk_user_id={args.user_id}")
        else:
            # Always bump the rpm limit so the parallel Playwright run
            # doesn't hit 429s. Existing users may carry the production
            # default of 6 rpm, which is far too low for an e2e suite
            # running 6 workers.
            if user.requests_per_minute_limit < 600:
                user.requests_per_minute_limit = 600
                print(
                    f"raised requests_per_minute_limit to 600 on "
                    f"advisor_user id={user.id}"
                )
            print(
                f"using existing advisor_user id={user.id} "
                f"clerk_user_id={args.user_id}"
            )

        if args.unlimited and not user.unlimited_credits:
            user.unlimited_credits = True
            print(f"set unlimited_credits=True on user {user.id}")
        elif user.unlimited_credits and not args.unlimited:
            pass  # leave existing flag untouched unless explicitly set

        if args.free_questions is not None:
            user.free_questions_remaining = args.free_questions
            # Explicitly seeding a free-question count means "this user is
            # already onboarded to the free-question model with exactly N
            # questions" — record the grant flag so the ABS-323 self-heal
            # in resolve_or_create_user treats the seeded count as
            # authoritative (no-op) rather than overwriting it with the
            # default grant on the next request. Users seeded WITHOUT
            # --free-questions are left flag-unset on purpose: they model
            # legacy / pre-feature accounts that should self-heal to the
            # default grant when they next authenticate.
            user.metadata_json["free_question_grant_issued"] = True
            print(
                f"set free_questions_remaining={args.free_questions} "
                f"on user {user.id}"
            )

        for tier in TIERS:
            available = db.scalar(
                select(CaseCredit)
                .where(
                    CaseCredit.user_id == user.id,
                    CaseCredit.tier == tier,
                    CaseCredit.state == "available",
                )
                .with_only_columns(CaseCredit.id)
                .order_by(CaseCredit.id)
            )
            count = (
                db.query(CaseCredit)
                .filter(
                    CaseCredit.user_id == user.id,
                    CaseCredit.tier == tier,
                    CaseCredit.state == "available",
                )
                .count()
            )
            missing = max(0, args.credits_per_tier - count)
            if missing:
                grant_admin_credits(
                    db,
                    user=user,
                    tier=tier,
                    quantity=missing,
                    reason="e2e_seed",
                )
                print(f"granted {missing} {tier} credit(s) to user {user.id}")
            else:
                print(
                    f"user {user.id} already holds {count} available "
                    f"{tier} credit(s); nothing to grant"
                )
            _ = available  # touch to satisfy linter — we use count above

        # ABS-383: keep the shared demo user's token wallet topped up. The
        # chat route now burns measured tokens from the account wallet on
        # every turn and refuses the turn with a 402 once the balance falls
        # to the pre-flight floor. A full parallel Playwright suite fires far
        # more chat turns at the single demo user than the one-time signup
        # grant (25k) covers, so without this top-up the wallet drains
        # mid-suite and every later chat spec (smoke 04, multi-turn,
        # message-feedback, sidebar-case-title, …) starts failing with 402.
        # Grant the shortfall up to the target floor; idempotent because we
        # only add what's missing (a suite may have burned some down between
        # the globalSetup seed and a per-worker beforeAll re-seed).
        if args.token_balance > 0:
            current_balance = get_balance(db, user_id=user.id)
            shortfall = args.token_balance - current_balance
            if shortfall > 0:
                grant_tokens(
                    db,
                    user=user,
                    amount=shortfall,
                    reason="e2e_seed_topup",
                )
                print(
                    f"topped up token wallet by {shortfall} to "
                    f"{args.token_balance} on user {user.id}"
                )
            else:
                print(
                    f"user {user.id} token wallet already at "
                    f"{current_balance} (>= {args.token_balance}); "
                    f"nothing to top up"
                )

        # Ensure the demo user has accepted the current T&C version so
        # the gate at /app doesn't redirect existing chat/case specs to
        # /app/terms. The acceptance is idempotent on
        # (user_id, version_hash); inserting one we already have is a
        # no-op via the unique constraint.
        current = get_current_terms()
        already = (
            db.query(TermsAcceptance)
            .filter(
                TermsAcceptance.user_id == user.id,
                TermsAcceptance.version_hash == current.version_hash,
            )
            .first()
        )
        if already is None:
            db.add(
                TermsAcceptance(
                    user_id=user.id,
                    version_hash=current.version_hash,
                    ip="127.0.0.1",
                    user_agent="seed_e2e_user.py",
                )
            )
            print(
                f"seeded TermsAcceptance for user {user.id} "
                f"version={current.version_hash[:16]}…"
            )
        else:
            print(
                f"user {user.id} already accepted current T&C version "
                f"{current.version_hash[:16]}…"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
