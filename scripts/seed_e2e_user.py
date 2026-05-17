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
from sqlalchemy import select

from advisor.db.cases import grant_admin_credits
from advisor.db.models import CaseCredit, User
from layer1.db.session import session_scope


TIERS = ("quick", "standard", "complex")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default="demo-user-1")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--credits-per-tier", type=int, default=100)
    args = parser.parse_args()

    with session_scope() as db:
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
