"""One signup token grant per user, enforced by the schema (ABS-415).

``grant_signup_tokens_if_needed`` was idempotent only in application code:
it read ``metadata_json['token_grant_issued']``, and (before ABS-404) read
it *outside* the user row's write lock. Two concurrent authenticated
requests for the same fresh user both saw the flag unset and both wrote a
grant. Production did exactly that on 2026-07-17 — ledger ids 1 and 2, two
``+25,000`` ``grant`` rows for user 2, identical timestamps — doubling that
user's free allowance.

ABS-404 closed the race in the service. This migration makes the rule a
property of the data rather than of the caller, mirroring the UNIQUE on
``stripe_checkout_session_id`` that already makes Stripe top-ups
exactly-once.

Data repair
-----------
The index cannot be created while prod still holds the duplicate rows, and
the ledger is append-only — we do not delete history. So for every user with
more than one ``grant``/``signup_grant`` row we keep the earliest (lowest
id) and, for each extra:

1. Write a compensating ``adjust`` entry of ``-amount_tokens`` (reason
   ``abs415_duplicate_signup_grant_reversal``) and decrement
   ``advisor_user.token_balance`` by the same amount, so
   ``SUM(amount_tokens) == token_balance`` still holds and the user is left
   holding exactly one grant's worth of free tokens. The balance may go
   negative if the doubled grant was already spent — overdraw is by design
   in this wallet (see ``advisor.db.wallet``).
2. Re-label the duplicate row's ``reason`` to
   ``signup_grant_duplicate_voided``. The row, its amount and its
   ``balance_after`` snapshot survive verbatim for audit; only the
   provenance label changes, which is what lets the partial unique index
   below be created. This is the one place a ledger row is ever updated,
   and it is a one-shot repair of rows that should never have existed.

Constraint
----------
Partial unique index ``uq_advisor_token_transaction_signup_grant`` on
``(user_id) WHERE entry_type = 'grant' AND reason = 'signup_grant'``.
Scoped to the signup reason on purpose: admin gifts are also ``grant``
entries and must stay repeatable.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_signup_grant_unique"
down_revision = "0024_document_retrieval_enabled"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_advisor_token_transaction_signup_grant"
SIGNUP_REASON = "signup_grant"
VOIDED_REASON = "signup_grant_duplicate_voided"
REVERSAL_REASON = "abs415_duplicate_signup_grant_reversal"

# Every signup grant beyond the earliest one the user received.
_DUPLICATE_ROWS = f"""
    SELECT t.id, t.user_id, t.amount_tokens
    FROM advisor_token_transaction t
    JOIN (
        SELECT user_id, MIN(id) AS keep_id
        FROM advisor_token_transaction
        WHERE entry_type = 'grant' AND reason = '{SIGNUP_REASON}'
        GROUP BY user_id
        HAVING COUNT(*) > 1
    ) d ON d.user_id = t.user_id
    WHERE t.entry_type = 'grant'
      AND t.reason = '{SIGNUP_REASON}'
      AND t.id <> d.keep_id
    ORDER BY t.id
"""


def upgrade() -> None:
    conn = op.get_bind()
    is_postgres = conn.dialect.name == "postgresql"
    # ``metadata_json`` is JSONB on Postgres, plain JSON (text) elsewhere: a
    # bare text bind param won't implicitly cast into a jsonb column.
    meta_expr = "CAST(:meta AS jsonb)" if is_postgres else ":meta"

    duplicates = list(conn.execute(sa.text(_DUPLICATE_ROWS)))
    for dup_id, user_id, amount in duplicates:
        # Reverse the extra grant: balance down, compensating ledger row in
        # the same statement pair so the SUM invariant never breaks.
        conn.execute(
            sa.text(
                "UPDATE advisor_user SET token_balance = token_balance - :amt "
                "WHERE id = :uid"
            ),
            {"amt": amount, "uid": user_id},
        )
        balance_after = conn.execute(
            sa.text("SELECT token_balance FROM advisor_user WHERE id = :uid"),
            {"uid": user_id},
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO advisor_token_transaction "
                "(user_id, entry_type, amount_tokens, balance_after, reason, "
                " metadata_json, created_at) "
                f"VALUES (:uid, 'adjust', :amt, :bal, :reason, {meta_expr}, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "uid": user_id,
                "amt": -amount,
                "bal": balance_after,
                "reason": REVERSAL_REASON,
                "meta": f'{{"voided_transaction_id": {dup_id}}}',
            },
        )
        # Re-label so the partial unique index below can be created.
        conn.execute(
            sa.text(
                "UPDATE advisor_token_transaction SET reason = :voided "
                "WHERE id = :tid"
            ),
            {"voided": VOIDED_REASON, "tid": dup_id},
        )

    where = sa.text(f"entry_type = 'grant' AND reason = '{SIGNUP_REASON}'")
    if conn.dialect.name == "postgresql":
        op.create_index(
            INDEX_NAME,
            "advisor_token_transaction",
            ["user_id"],
            unique=True,
            postgresql_where=where,
        )
    else:
        # sqlite has supported partial indexes since 3.8.0; alembic's
        # ``postgresql_where`` kwarg is dialect-specific, so emit the DDL
        # directly (same shape as 0015_enforce_one_active_credit_per_case).
        op.execute(
            f"CREATE UNIQUE INDEX {INDEX_NAME} "
            "ON advisor_token_transaction (user_id) "
            f"WHERE entry_type = 'grant' AND reason = '{SIGNUP_REASON}'"
        )


def downgrade() -> None:
    # Only the constraint is reversible. The duplicate-grant reversals are a
    # data repair of rows that were a bug: re-doubling those wallets on a
    # downgrade would be the actual defect, so they stay as written.
    op.drop_index(INDEX_NAME, table_name="advisor_token_transaction")
