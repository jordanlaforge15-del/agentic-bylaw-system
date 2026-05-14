"""Case-based billing — replaces the subscription / monthly-quota model.

This migration is the single atomic step from the v1 cost model
(subscription + per-month query/token caps) to the case-credit model
(per-case credit purchases, per-tier token budgets, atomic upgrades).

Why one migration and not two
=============================
A phased migration would require shipping two backend versions, and
the case model is internally cohesive — there's no useful intermediate
state where (say) the new tables exist but the old quota columns still
gate chat. Doing it as one migration also lets the data conversion
(grant N starter credits to existing beta users) live alongside the
schema change rather than being a separate cron-driven step.

Steps
-----
1. Create the four new tables: ``advisor_case``,
   ``advisor_case_purchase``, ``advisor_case_credit``, ``advisor_case_event``.
2. Add ``case_id`` / ``tier`` / ``token_budget_remaining`` to
   ``advisor_chat_session``; add ``case_id`` to ``advisor_usage_event``.
3. Data migration: for each existing user with at least one chat
   session, insert one synthetic ``advisor_case_purchase`` row
   (``pack_sku='admin_grant'``, paid 0) and 3 ``advisor_case_credit``
   rows at Standard tier so beta users keep working post-migration.
4. Drop the legacy quota / subscription columns from ``advisor_user``.
5. Replace the ``invite_request.granted_*`` cap fields with
   ``granted_starter_credits`` / ``granted_starter_tier``.

Downgrade
---------
The ``downgrade()`` function is provided for completeness, but it will
LOSE DATA — case-credit ledger and case history can't be rebuilt from
the legacy quota columns. Don't run it in production.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_case_based_billing"
down_revision = "0011_advisor_user_caps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # -- 1. New tables -----------------------------------------------------

    op.create_table(
        "advisor_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("anchor_label", sa.String(length=500), nullable=False),
        sa.Column("anchor_key", sa.String(length=255), nullable=False),
        sa.Column("anchor_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="open",
        ),
        sa.Column("current_tier", sa.String(length=16), nullable=True),
        sa.Column(
            "tokens_consumed",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB, "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index(
        "ix_advisor_case_user_key_status",
        "advisor_case",
        ["user_id", "anchor_key", "status"],
    )
    op.create_index(
        "ix_advisor_case_user_last_activity",
        "advisor_case",
        ["user_id", "last_activity_at"],
    )

    op.create_table(
        "advisor_case_purchase",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("pack_sku", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("list_price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "discount_bps",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("amount_paid_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=8),
            nullable=False,
            server_default="CAD",
        ),
        sa.Column(
            "stripe_checkout_session_id",
            sa.String(length=255),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "stripe_payment_intent_id",
            sa.String(length=255),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "advisor_case_credit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "purchase_id",
            sa.Integer(),
            sa.ForeignKey("advisor_case_purchase.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="available",
        ),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("advisor_case.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("advisor_chat_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("upgraded_from_tier", sa.String(length=16), nullable=True),
        sa.Column(
            "upgraded_to_credit_id",
            sa.Integer(),
            sa.ForeignKey("advisor_case_credit.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "purchased_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB, "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index(
        "ix_advisor_case_credit_user_tier_state",
        "advisor_case_credit",
        ["user_id", "tier", "state"],
    )
    # Partial unique index: at most one live credit per session.
    if is_postgres:
        op.create_index(
            "uq_advisor_case_credit_active_session",
            "advisor_case_credit",
            ["session_id"],
            unique=True,
            postgresql_where=sa.text("state IN ('reserved', 'consumed')"),
        )
    else:
        # SQLite (test) supports partial indexes via raw SQL.
        op.execute(
            "CREATE UNIQUE INDEX uq_advisor_case_credit_active_session "
            "ON advisor_case_credit (session_id) "
            "WHERE state IN ('reserved', 'consumed')"
        )

    op.create_table(
        "advisor_case_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("advisor_case.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "credit_id",
            sa.Integer(),
            sa.ForeignKey("advisor_case_credit.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column(
            "payload_json",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB, "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )

    # -- 2. Mutate existing tables ----------------------------------------

    with op.batch_alter_table("advisor_chat_session") as batch:
        batch.add_column(
            sa.Column(
                "case_id",
                sa.Integer(),
                sa.ForeignKey("advisor_case.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("tier", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column("token_budget_remaining", sa.BigInteger(), nullable=True)
        )
    op.create_index(
        "ix_advisor_chat_session_case_id", "advisor_chat_session", ["case_id"]
    )

    with op.batch_alter_table("advisor_usage_event") as batch:
        batch.add_column(
            sa.Column(
                "case_id",
                sa.Integer(),
                sa.ForeignKey("advisor_case.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    op.create_index(
        "ix_advisor_usage_event_case_id", "advisor_usage_event", ["case_id"]
    )

    # -- 3. Data migration: gift 3 Standard credits per active user -------
    # Only runs on Postgres. SQLite test runs use empty fixtures, so the
    # data migration would be a no-op anyway.
    if is_postgres:
        op.execute(
            """
            WITH active_users AS (
                SELECT u.id AS user_id
                FROM advisor_user u
                WHERE EXISTS (
                    SELECT 1 FROM advisor_chat_session s WHERE s.user_id = u.id
                )
            ),
            new_purchases AS (
                INSERT INTO advisor_case_purchase
                    (user_id, pack_sku, tier, quantity,
                     list_price_cents, discount_bps, amount_paid_cents,
                     currency, created_at)
                SELECT user_id, 'admin_grant', 'standard', 3,
                       0, 0, 0, 'CAD', now()
                FROM active_users
                RETURNING id, user_id
            )
            INSERT INTO advisor_case_credit
                (user_id, purchase_id, tier, source, state,
                 purchased_at)
            SELECT np.user_id, np.id, 'standard', 'admin_grant',
                   'available', now()
            FROM new_purchases np
            CROSS JOIN generate_series(1, 3);
            """
        )

    # -- 4. Drop legacy User columns --------------------------------------

    with op.batch_alter_table("advisor_user") as batch:
        batch.drop_column("plan_tier")
        batch.drop_column("monthly_query_limit")
        batch.drop_column("monthly_queries_used")
        batch.drop_column("monthly_input_token_limit")
        batch.drop_column("monthly_output_token_limit")
        batch.drop_column("monthly_input_tokens_used")
        batch.drop_column("monthly_output_tokens_used")
        batch.drop_column("month_started_at")
        batch.drop_column("subscription_status")
        batch.drop_column("subscription_current_period_end")

    # ``stripe_subscription_id`` has its own index from migration 0008;
    # drop the index before the column.
    op.drop_index(
        "ix_advisor_user_stripe_subscription_id", table_name="advisor_user"
    )
    with op.batch_alter_table("advisor_user") as batch:
        batch.drop_column("stripe_subscription_id")

    # -- 5. Replace invite cap fields -------------------------------------

    with op.batch_alter_table("invite_request") as batch:
        batch.drop_column("granted_query_limit")
        batch.drop_column("granted_monthly_input_tokens")
        batch.drop_column("granted_monthly_output_tokens")
        batch.drop_column("granted_rpm")
        batch.add_column(
            sa.Column(
                "granted_starter_credits",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "granted_starter_tier", sa.String(length=16), nullable=True
            )
        )


def downgrade() -> None:
    """Reverse the schema changes — DATA-LOSSY for case-credit history.

    Restores the legacy User quota columns and ``invite_request``
    granted_* fields with their original defaults, then drops the new
    case-billing tables. Case credits, purchases, and events are LOST.
    """
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Restore invite cap fields.
    with op.batch_alter_table("invite_request") as batch:
        batch.drop_column("granted_starter_tier")
        batch.drop_column("granted_starter_credits")
        batch.add_column(
            sa.Column(
                "granted_query_limit",
                sa.Integer(),
                nullable=False,
                server_default="100",
            )
        )
        batch.add_column(
            sa.Column(
                "granted_monthly_input_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default="500000",
            )
        )
        batch.add_column(
            sa.Column(
                "granted_monthly_output_tokens",
                sa.BigInteger(),
                nullable=False,
                server_default="100000",
            )
        )
        batch.add_column(
            sa.Column(
                "granted_rpm",
                sa.Integer(),
                nullable=False,
                server_default="6",
            )
        )

    # Restore legacy User columns.
    with op.batch_alter_table("advisor_user") as batch:
        batch.add_column(
            sa.Column(
                "plan_tier",
                sa.String(length=32),
                nullable=False,
                server_default="free",
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_query_limit",
                sa.Integer(),
                nullable=False,
                server_default="100",
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_queries_used",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_input_token_limit",
                sa.BigInteger(),
                nullable=False,
                server_default="500000",
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_output_token_limit",
                sa.BigInteger(),
                nullable=False,
                server_default="100000",
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_input_tokens_used",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "monthly_output_tokens_used",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column(
                "month_started_at",
                sa.Date(),
                nullable=False,
                server_default=sa.text("CURRENT_DATE"),
            )
        )
        batch.add_column(
            sa.Column("subscription_status", sa.String(length=32), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "subscription_current_period_end",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "stripe_subscription_id",
                sa.String(length=255),
                nullable=True,
            )
        )
    op.create_index(
        "ix_advisor_user_stripe_subscription_id",
        "advisor_user",
        ["stripe_subscription_id"],
    )

    # Drop new tables and columns (children first).
    op.drop_index(
        "ix_advisor_usage_event_case_id", table_name="advisor_usage_event"
    )
    with op.batch_alter_table("advisor_usage_event") as batch:
        batch.drop_column("case_id")
    op.drop_index(
        "ix_advisor_chat_session_case_id", table_name="advisor_chat_session"
    )
    with op.batch_alter_table("advisor_chat_session") as batch:
        batch.drop_column("token_budget_remaining")
        batch.drop_column("tier")
        batch.drop_column("case_id")

    op.drop_table("advisor_case_event")
    op.drop_index(
        "uq_advisor_case_credit_active_session",
        table_name="advisor_case_credit",
    )
    op.drop_index(
        "ix_advisor_case_credit_user_tier_state",
        table_name="advisor_case_credit",
    )
    op.drop_table("advisor_case_credit")
    op.drop_table("advisor_case_purchase")
    op.drop_index(
        "ix_advisor_case_user_last_activity", table_name="advisor_case"
    )
    op.drop_index("ix_advisor_case_user_key_status", table_name="advisor_case")
    op.drop_table("advisor_case")

    if is_postgres:
        # No-op placeholder kept for symmetry; the data-migration grant
        # above can't be reversed (no provenance on which credits came
        # from the migration vs subsequent admin grants).
        pass
