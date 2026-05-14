"""Advisor user database layer — case-credit billing model.

Tables for the SaaS layer (users, chat sessions/messages, usage events,
case credits) that lives in the same Postgres instance as Layer 1's
bylaw data but on a logically separate ``advisor_*`` table prefix.
Sharing one Alembic chain (and one ``Base.metadata``) keeps deploys
simple while leaving the door open to splitting into a dedicated
database later — only the prefix has to be respected.

Public surface re-exported here:
- SQLAlchemy models: ``User``, ``ChatSession``, ``ChatMessage``,
  ``UsageEvent``, ``Case``, ``CaseCredit``, ``CasePurchase``,
  ``CaseEvent``, ``InviteRequest``.
- Pydantic read/write schemas: ``UserCreate``, ``UserOut``,
  ``ChatSessionCreate``, ``ChatSessionOut``, ``ChatMessageCreate``,
  ``ChatMessageOut``, ``UsageEventCreate``, ``UsageEventOut``,
  ``CaseOut``, ``CaseCreditOut``, ``CreditBalanceSummary``.
- Case lifecycle helpers: ``open_case``, ``close_case``, ``match_case``,
  ``commit_credit_for_session``, ``refund_credit_for_session``,
  ``upgrade_case_credit``, ``grant_admin_credits``,
  ``credit_balance_for``.
"""
from advisor.db.cases import (
    NoAvailableCreditError,
    REOPEN_WINDOW,
    UnknownTierError,
    close_case,
    commit_credit_for_session,
    credit_balance_for,
    grant_admin_credits,
    issue_credits_from_pack_purchase,
    list_user_cases,
    match_case,
    normalise_anchor,
    open_case,
    refund_credit_for_session,
    upgrade_case_credit,
)
from advisor.db.models import (
    Case,
    CaseCredit,
    CaseEvent,
    CasePurchase,
    ChatMessage,
    ChatSession,
    InviteRequest,
    UsageEvent,
    User,
)
from advisor.db.schemas import (
    CaseCreditOut,
    CaseOut,
    ChatMessageCreate,
    ChatMessageOut,
    ChatSessionCreate,
    ChatSessionOut,
    CreditBalanceSummary,
    UsageEventCreate,
    UsageEventOut,
    UserCreate,
    UserOut,
)

__all__ = [
    # Models
    "Case",
    "CaseCredit",
    "CaseEvent",
    "CasePurchase",
    "ChatMessage",
    "ChatSession",
    "InviteRequest",
    "UsageEvent",
    "User",
    # Schemas
    "CaseCreditOut",
    "CaseOut",
    "ChatMessageCreate",
    "ChatMessageOut",
    "ChatSessionCreate",
    "ChatSessionOut",
    "CreditBalanceSummary",
    "UsageEventCreate",
    "UsageEventOut",
    "UserCreate",
    "UserOut",
    # Case lifecycle helpers
    "NoAvailableCreditError",
    "REOPEN_WINDOW",
    "UnknownTierError",
    "close_case",
    "commit_credit_for_session",
    "credit_balance_for",
    "grant_admin_credits",
    "issue_credits_from_pack_purchase",
    "list_user_cases",
    "match_case",
    "normalise_anchor",
    "open_case",
    "refund_credit_for_session",
    "upgrade_case_credit",
]
