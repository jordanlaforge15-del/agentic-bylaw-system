"""One-shot backfill: populate blank `advisor_user.email` from Clerk.

Repairs ``advisor_user`` rows that were created before the JIT path
learned to call the Clerk Backend API on missing-email JWTs. Looks at
every row with ``email = ''`` and a Clerk-shaped ``clerk_user_id``,
calls ``GET /v1/users/{id}`` to fetch the real email + name, and
updates the row.

Usage:
    # Dry-run (default — prints what would change but does not write):
    python scripts/backfill_user_emails.py

    # Apply changes:
    python scripts/backfill_user_emails.py --apply

    # Limit which rows to touch:
    python scripts/backfill_user_emails.py --apply --clerk-prefix user_3Df

The script reads ``CLERK_SECRET_KEY`` from the environment (same value
used by the advisor container at runtime) and ``DATABASE_URL`` /
``layer1.config`` for the DB connection. Smoke-test fixture rows
(``clerk_user_id`` not starting with ``user_``) are always skipped.
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy.orm import Session

from advisor.auth.clerk_backend import ClerkBackendClient, ClerkBackendError
from advisor.db.models import User
from layer1.db.base import utcnow
from layer1.db.session import session_scope

logger = logging.getLogger("backfill_user_emails")


def _select_rows(db: Session, clerk_prefix: str) -> list[User]:
    """Return rows that need a backfill — empty email and a real Clerk id.

    Restricting to ``clerk_user_id LIKE clerk_prefix%`` keeps the smoke
    fixture (``clerk_user_id='smoke-test-1'``) out and lets the operator
    target a specific Clerk environment if needed.
    """
    return (
        db.query(User)
        .filter(User.email == "")
        .filter(User.clerk_user_id.like(f"{clerk_prefix}%"))
        .order_by(User.id)
        .all()
    )


def _backfill_one(db: Session, user: User, client: ClerkBackendClient, *, apply: bool) -> str:
    try:
        profile = client.fetch_user(user.clerk_user_id)
    except ClerkBackendError as exc:
        return f"FAIL  id={user.id} clerk_user_id={user.clerk_user_id} error={exc}"

    if not profile.email:
        return (
            f"SKIP  id={user.id} clerk_user_id={user.clerk_user_id} "
            f"(clerk has no primary email)"
        )

    before_email = user.email
    before_name = user.full_name
    if apply:
        user.email = profile.email
        if not user.full_name and profile.full_name:
            user.full_name = profile.full_name
        user.updated_at = utcnow()
        db.add(user)

    name_change = ""
    if not before_name and profile.full_name:
        name_change = f" full_name='' -> '{profile.full_name}'"
    return (
        f"{'APPLY' if apply else 'DRY  '} id={user.id} "
        f"clerk_user_id={user.clerk_user_id} "
        f"email='{before_email}' -> '{profile.email}'{name_change}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the script only prints what it would do.",
    )
    parser.add_argument(
        "--clerk-prefix",
        default="user_",
        help="Only touch rows whose clerk_user_id starts with this prefix (default: 'user_').",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override the database URL (default: layer1.config / DATABASE_URL).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    client = ClerkBackendClient()
    if not client.configured:
        print(
            "error: CLERK_SECRET_KEY is not set. Export it in the environment "
            "(same value used by the advisor container) and rerun.",
            file=sys.stderr,
        )
        return 2

    touched = 0
    with session_scope(args.db_url) as db:
        rows = _select_rows(db, args.clerk_prefix)
        if not rows:
            print(f"No rows with email='' and clerk_user_id LIKE '{args.clerk_prefix}%'.")
            return 0
        print(f"Found {len(rows)} row(s) to inspect:")
        for user in rows:
            line = _backfill_one(db, user, client, apply=args.apply)
            print(line)
            if line.startswith("APPLY"):
                touched += 1
        if not args.apply:
            print("\nDry-run only. Re-run with --apply to commit.")
            # session_scope commits on success — but with apply=False
            # we never mutated anything, so the commit is a no-op.
    print(f"\nDone. {touched} row(s) updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
