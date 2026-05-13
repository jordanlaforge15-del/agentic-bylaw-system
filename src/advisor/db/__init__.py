"""Advisor user database layer.

Tables for the SaaS layer (users, chat sessions/messages, usage events)
that lives in the same Postgres instance as Layer 1's bylaw data but on
a logically separate ``advisor_*`` table prefix. Sharing one Alembic
chain (and one ``Base.metadata``) keeps deploys simple while leaving
the door open to splitting into a dedicated database later — only the
prefix has to be respected.

Public surface re-exported here:
- SQLAlchemy models: ``User``, ``ChatSession``, ``ChatMessage``,
  ``UsageEvent``.
- Pydantic read/write schemas: ``UserCreate``, ``UserOut``,
  ``ChatSessionCreate``, ``ChatSessionOut``, ``ChatMessageCreate``,
  ``ChatMessageOut``, ``UsageEventCreate``, ``UsageEventOut``,
  ``MonthlyQuota``.
- Quota helpers: ``get_monthly_quota``, ``record_query``,
  ``QuotaExceeded``.
"""
from advisor.db.models import (
    ChatMessage,
    ChatSession,
    InviteRequest,
    UsageEvent,
    User,
)
from advisor.db.quota import QuotaExceeded, get_monthly_quota, record_query
from advisor.db.schemas import (
    ChatMessageCreate,
    ChatMessageOut,
    ChatSessionCreate,
    ChatSessionOut,
    MonthlyQuota,
    UsageEventCreate,
    UsageEventOut,
    UserCreate,
    UserOut,
)

__all__ = [
    # Models
    "ChatMessage",
    "ChatSession",
    "InviteRequest",
    "UsageEvent",
    "User",
    # Schemas
    "ChatMessageCreate",
    "ChatMessageOut",
    "ChatSessionCreate",
    "ChatSessionOut",
    "MonthlyQuota",
    "UsageEventCreate",
    "UsageEventOut",
    "UserCreate",
    "UserOut",
    # Quota helpers
    "QuotaExceeded",
    "get_monthly_quota",
    "record_query",
]
