"""FastAPI surface for the Halifax Bylaw Advisor chat backend.

Public surface:
- ``create_app``: the application factory; tests inject mocks, prod
  uses the defaults.
- ``SessionStore`` / ``InMemorySessionStore`` / ``DbSessionStore``:
  v1 in-memory session storage and the SQLAlchemy-backed replacement.
- ``enforce_and_record_query``: HTTP-edge wrapper around
  ``advisor.db.quota.record_query`` that raises a structured 429.
"""
from advisor.api.app import create_app
from advisor.api.db_session_store import DbSessionStore, default_resolve_user
from advisor.api.quota import enforce_and_record_query
from advisor.api.sessions import InMemorySessionStore, SessionStore

__all__ = [
    "DbSessionStore",
    "InMemorySessionStore",
    "SessionStore",
    "create_app",
    "default_resolve_user",
    "enforce_and_record_query",
]
