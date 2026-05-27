#!/usr/bin/env python
"""CLI for issuing and revoking ABS API keys (ABS-59).

Usage:
    # Issue a new key for a user identified by email:
    python scripts/issue_api_key.py --email designer@example.com --name "Speckle Automate"

    # Revoke an existing key by its database ID:
    python scripts/issue_api_key.py --revoke --key-id 7

The raw key is printed once on stdout. It cannot be recovered — the DB
only stores the SHA-256 hash.
"""
from __future__ import annotations

import argparse
import os
import sys

# Ensure the repo's src/ is on the path.
_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


def _issue(email: str, name: str) -> None:
    from advisor.api.api_key_auth import issue_api_key
    from advisor.db.models import User
    from layer1.db.session import session_scope

    with session_scope() as db:
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            print(f"ERROR: No user with email {email!r}", file=sys.stderr)
            sys.exit(1)

        row, raw_key = issue_api_key(db, user_id=user.id, name=name)
        db.commit()

    print(f"API key issued (id={row.id}, user={email}):")
    print(raw_key)
    print("\nStore this key securely — it will not be shown again.")


def _revoke(key_id: int) -> None:
    from advisor.api.api_key_auth import revoke_api_key
    from advisor.db.models import AdvisorApiKey
    from layer1.db.session import session_scope

    with session_scope() as db:
        row = db.get(AdvisorApiKey, key_id)
        if row is None:
            print(f"ERROR: No API key with id={key_id}", file=sys.stderr)
            sys.exit(1)

        revoked = revoke_api_key(db, key_id=key_id, user_id=row.user_id)
        db.commit()

    if revoked:
        print(f"API key {key_id} revoked.")
    else:
        print(f"API key {key_id} was already revoked or not found.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage ABS API keys.")
    parser.add_argument("--email", help="User email (required for --issue).")
    parser.add_argument("--name", default="API Key", help="Descriptive name for the key.")
    parser.add_argument("--revoke", action="store_true", help="Revoke a key.")
    parser.add_argument("--key-id", type=int, help="Key ID to revoke.")
    args = parser.parse_args()

    if args.revoke:
        if not args.key_id:
            parser.error("--key-id is required with --revoke")
        _revoke(args.key_id)
    else:
        if not args.email:
            parser.error("--email is required when issuing a key")
        _issue(args.email, args.name)


if __name__ == "__main__":
    main()
