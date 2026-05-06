"""FastAPI surface for the Halifax Bylaw Advisor chat backend.

Public surface:
- ``create_app``: the application factory; tests inject mocks, prod
  uses the defaults.
- ``SessionStore`` / ``InMemorySessionStore``: v1 session persistence
  (workstream 2's DB schema will replace the in-memory impl).
"""
from advisor.api.app import create_app
from advisor.api.sessions import InMemorySessionStore, SessionStore

__all__ = ["InMemorySessionStore", "SessionStore", "create_app"]
