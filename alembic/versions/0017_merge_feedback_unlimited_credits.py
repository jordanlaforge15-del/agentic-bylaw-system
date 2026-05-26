"""Merge general_feedback and unlimited_credits heads.

Resolves the dual-head created when ABS-129 (general_feedback) and
ABS-12 (unlimited_credits) both branched from 0014_compliance_schema.
"""
from __future__ import annotations

from alembic import op  # noqa: F401

revision = "0017_merge_feedback_unlimited_credits"
down_revision = ("0015_general_feedback", "0016_unlimited_credits")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
