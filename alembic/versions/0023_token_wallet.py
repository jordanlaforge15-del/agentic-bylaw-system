"""Token wallet foundation: balance column + append-only ledger (ABS-380).

The beta pivot replaces per-case tier credits with a prepaid token wallet
presented to the user as "~N turns" (see
``docs/decisions/2026-07-beta-pivot-turn-wallet-gated-reports.md``, D1).
This migration adds:

* ``advisor_user.token_balance`` — a signed BigInteger running balance
  (server_default 0). Signed because chat settlement burns actual usage
  with no floor, so a turn can drive the balance negative (overdraw by
  design).
* ``advisor_token_transaction`` — the append-only ledger behind that
  balance. One row per balance change; ``SUM(amount_tokens) ==
  token_balance`` holds forever.

**No backfill.** Existing users land with ``token_balance = 0`` and are
lazily self-healed on their next authenticated request by
``grant_signup_tokens_if_needed`` (idempotent on
``metadata_json['token_grant_issued']``).

See ``advisor.db.models.TokenTransaction`` and ``advisor.db.wallet``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023_token_wallet"
down_revision = "0022_question_purchase"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    # Mirror layer1.db.base.json_type(): JSONB on Postgres, JSON elsewhere
    # (sqlite for unit tests).
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "advisor_user",
        sa.Column(
            "token_balance",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "advisor_token_transaction",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("amount_tokens", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column(
            "stripe_checkout_session_id",
            sa.String(255),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("advisor_chat_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("advisor_case.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "usage_event_id",
            sa.Integer(),
            sa.ForeignKey("advisor_usage_event.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Composite (user_id, id) serves the statement view's
    # ``WHERE user_id = ? ORDER BY id DESC`` cursor pagination.
    op.create_index(
        "ix_advisor_token_transaction_user_id_id",
        "advisor_token_transaction",
        ["user_id", "id"],
    )
    op.create_index(
        "ix_advisor_token_transaction_created_at",
        "advisor_token_transaction",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisor_token_transaction_created_at",
        table_name="advisor_token_transaction",
    )
    op.drop_index(
        "ix_advisor_token_transaction_user_id_id",
        table_name="advisor_token_transaction",
    )
    op.drop_table("advisor_token_transaction")
    op.drop_column("advisor_user", "token_balance")
