"""Invite-request table backing the private-beta gate.

Replaces the file-on-disk ``web/data/invites.jsonl`` with a real
Postgres table. Used by:

* ``POST /api/invite`` (web) — public invite-request form inserts
  rows with ``status='pending'``.
* ``/admin/invites`` (web) — admin reviews pending requests and
  approves / rejects. Approval also calls the Clerk Backend API to
  add the email to the allowlist; the returned ``alid_…`` id is
  stored in ``clerk_allowlist_id`` so the row carries the link back
  to Clerk's record.
* ``resolve_or_create_user`` (advisor) — on first chat call from a
  new user, looks up their email in this table and copies the
  ``granted_*`` caps onto the new ``advisor_user`` row.

Why columns live where they do:

* ``granted_query_limit`` / ``granted_monthly_*_tokens`` / ``granted_rpm``
  are denormalised onto the invite row so the admin can override the
  defaults at approval time per-invite, without altering the global
  default. The values are then COPIED (not joined) onto
  ``advisor_user`` at first sign-in. Joining at chat time would be a
  hot-path read against a table the user no longer interacts with.

* ``expires_at`` is NULLABLE because pending and rejected rows have
  no meaningful expiry. We populate it at approval time (decided_at +
  14 days). A daily sweep (see ``/api/admin/invites/sweep-expired``)
  finds approved-but-unredeemed-and-expired rows, deletes the Clerk
  allowlist entry, and flips status to ``'expired'``.

* ``redeemed_at`` captures when an approved invite was first used to
  create an ``advisor_user`` row. NULL = approved but not yet signed
  in. NOT NULL = signed in, the expiry sweep should leave it alone.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_invite_request"
down_revision = "0009_postgis_spatial_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invite_request",
        # Short human-readable id, e.g. "ABS-1234". Generated in the
        # /api/invite handler; the random 4-digit suffix collides
        # rarely enough for the demo phase and the email-uniqueness
        # constraint below prevents the only failure mode that
        # matters.
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=200), nullable=True),
        sa.Column("project", sa.Text(), nullable=True),
        # Lifecycle. The CHECK constraint keeps an admin from putting
        # the row into a bogus state via direct SQL.
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="ck_invite_request_status",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        # Link back to the Clerk-side allowlist entry created at
        # approval time. We need this when the expiry sweep wants to
        # DELETE the allowlist entry (Clerk's API takes the alid by
        # path) and when an admin manually rejects a previously
        # approved invite.
        sa.Column("clerk_allowlist_id", sa.String(length=64), nullable=True),
        # Per-invite cap overrides. Populated at approval time. Copied
        # into advisor_user on first chat call.
        sa.Column(
            "granted_query_limit", sa.Integer(), nullable=False, server_default="100"
        ),
        sa.Column(
            "granted_monthly_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="500000",
        ),
        sa.Column(
            "granted_monthly_output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="100000",
        ),
        sa.Column(
            "granted_rpm", sa.Integer(), nullable=False, server_default="6"
        ),
        # Audit trail captured at submit time. Useful for spotting
        # abuse patterns (same IP submitting many requests).
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("email", name="uq_invite_request_email"),
    )
    op.create_index(
        "ix_invite_request_status", "invite_request", ["status"]
    )
    op.create_index(
        "ix_invite_request_email", "invite_request", ["email"]
    )


def downgrade() -> None:
    op.drop_index("ix_invite_request_email", table_name="invite_request")
    op.drop_index("ix_invite_request_status", table_name="invite_request")
    op.drop_table("invite_request")
