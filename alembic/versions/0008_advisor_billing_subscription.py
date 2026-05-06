"""Subscription bookkeeping fields on advisor_user.

Adds three columns the Stripe billing webhook populates:

* ``stripe_subscription_id`` — Stripe subscription id (sub_...).
  Indexed because the webhook handler queries by it on subscription
  events; sparse for free-tier users.
* ``subscription_status`` — Stripe's subscription state machine
  (``active`` / ``past_due`` / ``canceled`` / etc.). Used by the
  frontend to show "your subscription is past due" warnings.
* ``subscription_current_period_end`` — when the current paid
  period ends. Lets the frontend show the next billing date.

The migration is sqlite-safe because tests use sqlite — we
``op.add_column`` directly which works on both Postgres and sqlite
without needing ``batch_alter_table`` (sqlite handles ALTER TABLE
ADD COLUMN natively for new nullable columns).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_advisor_billing_subscription"
down_revision = "0007_advisor_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_user",
        sa.Column(
            "stripe_subscription_id", sa.String(length=255), nullable=True
        ),
    )
    op.add_column(
        "advisor_user",
        sa.Column("subscription_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "advisor_user",
        sa.Column(
            "subscription_current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_advisor_user_stripe_subscription_id",
        "advisor_user",
        ["stripe_subscription_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisor_user_stripe_subscription_id", table_name="advisor_user"
    )
    # On sqlite, drop_column requires batch_alter_table. Use it
    # uniformly so the downgrade works on both backends.
    with op.batch_alter_table("advisor_user") as batch:
        batch.drop_column("subscription_current_period_end")
        batch.drop_column("subscription_status")
        batch.drop_column("stripe_subscription_id")
