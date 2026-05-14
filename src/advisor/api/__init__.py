"""FastAPI surface for the Halifax Bylaw Advisor chat backend.

Public surface:
- ``create_app``: the application factory; tests inject mocks, prod
  uses the defaults.
- ``SessionStore`` / ``InMemorySessionStore`` / ``DbSessionStore``:
  v1 in-memory session storage and the SQLAlchemy-backed replacement.
- Case-credit lifecycle helpers re-exported from ``advisor.api.quota``:
  ``reserve_credit_for_session``, ``commit_credit_for``,
  ``refund_credit_for``, ``enforce_request_rate``.
"""
from advisor.api.app import create_app
from advisor.api.db_session_store import DbSessionStore, default_resolve_user
from advisor.api.quota import (
    add_case_tokens,
    commit_credit_for,
    enforce_request_rate,
    record_llm_call,
    refund_credit_for,
    reserve_credit_for_session,
    update_usage_event_tokens,
)
from advisor.api.sessions import InMemorySessionStore, SessionStore

__all__ = [
    "DbSessionStore",
    "InMemorySessionStore",
    "SessionStore",
    "add_case_tokens",
    "commit_credit_for",
    "create_app",
    "default_resolve_user",
    "enforce_request_rate",
    "record_llm_call",
    "refund_credit_for",
    "reserve_credit_for_session",
    "update_usage_event_tokens",
]
