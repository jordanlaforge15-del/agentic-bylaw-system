"""Add per-user token and rate caps to advisor_user.

The existing ``monthly_query_limit`` / ``monthly_queries_used`` columns
cap by request count. Beta invitees can blow through a 100-request
budget with a single long conversation that uses cached context for
every turn, OR get away with thousands of cheap calls each well under
the per-call ceiling. Two extra dimensions close those gaps:

* **Monthly token cap (input + output, separately).** A single chat
  turn with a 50k-token context is one query; that's intentional —
  the user cares about "did I get an answer" not "how many tokens it
  cost." But Anthropic charges us per-token, so we need to bound the
  unit we actually pay for. Two separate counters (input/output)
  because the price ratio between them differs by ~4x; collapsing
  them into one counter would either over- or under-charge depending
  on the conversation shape.

* **Rate cap (requests per minute).** Stops bursting and keeps a
  runaway client from accidentally DDoSing the Anthropic gateway
  through us. Enforced as a count of ``advisor_usage_event`` rows in
  the trailing minute — no separate sliding-window table, the usage
  log already has the index we need.

Defaults match the invite_request defaults so a free-tier user with
no explicit invite caps lands on the same numbers an invite would
have granted.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_advisor_user_caps"
down_revision = "0010_invite_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("advisor_user") as batch:
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
                "requests_per_minute_limit",
                sa.Integer(),
                nullable=False,
                server_default="6",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("advisor_user") as batch:
        batch.drop_column("requests_per_minute_limit")
        batch.drop_column("monthly_output_tokens_used")
        batch.drop_column("monthly_input_tokens_used")
        batch.drop_column("monthly_output_token_limit")
        batch.drop_column("monthly_input_token_limit")
