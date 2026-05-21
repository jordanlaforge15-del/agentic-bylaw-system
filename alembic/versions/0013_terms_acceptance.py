"""Terms-and-conditions acceptance ledger.

Click-wrap enforceability requires a per-user, per-version record of
acceptance — without it the document the user clicked through is not
materially different from a footer link. See ``docs/TERMS_AND_CONDITIONS.md``
for the document itself and ``src/advisor/legal/`` for the
hash-the-file-at-import logic that turns "edit the markdown" into "the
next user login re-prompts for re-acceptance."

Columns
-------
* ``user_id`` — the advisor user who clicked I Agree.
* ``version_hash`` — sha256 hex of the terms body the user agreed to.
  This is what makes the row evidence: if the document changes, the
  hash changes, and the old row no longer satisfies the gate.
* ``accepted_at`` — server timestamp when the row was written.
* ``ip`` — client IP captured from the request. NULLABLE because the
  upstream proxy may not set ``X-Forwarded-For`` in every deployment.
* ``user_agent`` — UA string from the request. Truncated to 500 chars.

Indexes
-------
* Unique ``(user_id, version_hash)`` — a user accepting the same
  version twice is a no-op idempotency, not a new evidence row.
* ``(user_id, accepted_at)`` — supports "show me my acceptance
  history" for an account-management UI later, and for the audit
  trail an operator might pull after a dispute.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_terms_acceptance"
down_revision = "0012_case_based_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_terms_acceptance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("advisor_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # sha256 hex is 64 chars. Stored as TEXT-via-String for
        # cross-dialect compatibility; we don't index on prefix.
        sa.Column("version_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # IPv4 fits in 15 chars, IPv6 in 45. 45 is the canonical
        # max; we leave a small margin to absorb any encoded forms.
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.UniqueConstraint(
            "user_id", "version_hash", name="uq_advisor_terms_user_version"
        ),
    )
    op.create_index(
        "ix_advisor_terms_user_accepted_at",
        "advisor_terms_acceptance",
        ["user_id", "accepted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_advisor_terms_user_accepted_at",
        table_name="advisor_terms_acceptance",
    )
    op.drop_table("advisor_terms_acceptance")
