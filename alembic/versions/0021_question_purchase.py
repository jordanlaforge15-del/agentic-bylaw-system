"""Add advisor_question_purchase table (ABS-312).

The priced-question "buy an answer" launch product sells answers to
fixed-price catalog questions with one Stripe charge per question,
authorized at checkout and captured only when a grounded answer is
delivered (the failed-question rule → authorize-then-capture). This
table is independent of the case-credit ledger: no balance, no packs.

See ``docs/decisions/2026-06-priced-question-catalog.md`` and
``advisor.db.models.QuestionPurchase``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021_question_purchase"
down_revision = "0020_user_case_number"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    # Mirror layer1.db.base.json_type(): JSONB on Postgres, JSON elsewhere
    # (sqlite for unit tests).
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_question_purchase",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("question_slug", sa.String(64), nullable=False),
        sa.Column(
            "inputs_json",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency", sa.String(8), nullable=False, server_default="CAD"
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="authorizing",
        ),
        sa.Column(
            "stripe_checkout_session_id", sa.String(255), nullable=True, unique=True
        ),
        sa.Column(
            "stripe_payment_intent_id", sa.String(255), nullable=True, index=True
        ),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.String(64), nullable=True),
        sa.Column(
            "transcript_json",
            _json_type(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "refinement_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata_json",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    # NOTE: the ``index=True`` flags on the user_id / stripe_payment_intent_id
    # columns above already emit ix_advisor_question_purchase_user_id and
    # ix_advisor_question_purchase_stripe_payment_intent_id, matching the
    # model's column-level indexes. Only the composite index is added
    # explicitly here.
    op.create_index(
        "ix_advisor_question_purchase_user_status",
        "advisor_question_purchase",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisor_question_purchase_user_status",
        table_name="advisor_question_purchase",
    )
    op.drop_table("advisor_question_purchase")
