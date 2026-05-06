"""Chat orchestration for the Halifax Bylaw Advisor.

This package wires the LLM gateway (``advisor.llm``) to the bylaw
retrieval service (``mcp.bylaw_retrieval``) through a small set of
tool definitions, persona-loaded system prompt, and a stateful
``ChatSession`` that drives multi-turn conversations.

Public surface:
- ``ChatSession``: per-user conversation state.
- ``build_bylaw_tools``: tool definitions + handlers for retrieval.
- ``load_persona``: system prompt loaded from ``docs/agent/persona.md``.
"""
from advisor.chat.persona import load_persona
from advisor.chat.session import ChatSession
from advisor.chat.tools import build_bylaw_tools

__all__ = ["ChatSession", "build_bylaw_tools", "load_persona"]
