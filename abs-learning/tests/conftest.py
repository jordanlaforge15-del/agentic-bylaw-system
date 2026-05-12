"""Shared pytest fixtures for the abs-learning Phase 1 test suite.

Adds the project's ``src/`` directory to ``sys.path`` so tests can
import ``bootstrap``, ``manifest``, and ``agents`` packages directly.

Also exposes session-scoped fixtures for the integration test suite:

- ``api_call_budget``: shared dict tracking real Anthropic API calls and
  the per-session ceiling.
- ``anthropic_client``: a real ``anthropic.Anthropic`` instance whose
  ``messages.create`` is wrapped to count and cap calls. The fixture is
  lazy: it loads ``ANTHROPIC_API_KEY`` from the parent repo's ``.env``
  on first use and skips the test if the key is unavailable. Unit tests
  that don't request the fixture incur no API setup.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for _path in (str(ROOT), str(SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


@pytest.fixture(scope="session")
def api_call_budget():
    """Shared counter + ceiling for the integration session."""
    return {"count": 0, "limit": 8}


@pytest.fixture(scope="session")
def anthropic_client(api_call_budget):
    """Real Anthropic client with a counted ``messages.create``.

    The wrapper raises ``RuntimeError`` if the per-session call budget
    is exceeded; this is the budget enforcement mechanism for the
    integration tests.
    """
    import os

    env_path = Path("/Users/christopherrafuse/dev/agentic-bylaw-system/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    import anthropic

    real = anthropic.Anthropic()
    real_create = real.messages.create

    def counted_create(*args, **kwargs):
        api_call_budget["count"] += 1
        if api_call_budget["count"] > api_call_budget["limit"]:
            raise RuntimeError(
                f"Test run exceeded {api_call_budget['limit']} Anthropic API calls"
            )
        return real_create(*args, **kwargs)

    real.messages.create = counted_create
    return real
