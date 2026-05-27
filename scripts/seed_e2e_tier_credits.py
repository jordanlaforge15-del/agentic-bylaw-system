"""Grant credits for a single tier to an e2e test user.

Used by Playwright specs that need a user with credits at one tier but
not another (e.g. standard credits only, to reproduce the credit-gating
inconsistency from ABS-183).

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test \\
        .venv/bin/python scripts/seed_e2e_tier_credits.py \\
        --user-id my-test-user --tier standard --quantity 3
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from advisor.db.cases import grant_admin_credits
from advisor.db.models import CaseCredit, User
from layer1.db.session import session_scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--tier",
        required=True,
        choices=["quick", "standard", "complex"],
    )
    parser.add_argument("--quantity", type=int, default=3)
    args = parser.parse_args()

    with session_scope() as db:
        user = (
            db.query(User)
            .filter(User.clerk_user_id == args.user_id)
            .one_or_none()
        )
        if user is None:
            print(
                f"error: no user found with clerk_user_id={args.user_id!r}",
                file=sys.stderr,
            )
            return 1

        existing_count = (
            db.query(CaseCredit)
            .filter(
                CaseCredit.user_id == user.id,
                CaseCredit.tier == args.tier,
                CaseCredit.state == "available",
            )
            .count()
        )
        grant_admin_credits(
            db,
            user=user,
            tier=args.tier,
            quantity=args.quantity,
            reason="e2e_tier_seed",
        )
        print(
            f"granted {args.quantity} {args.tier} credit(s) to user "
            f"id={user.id} (had {existing_count} existing)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
