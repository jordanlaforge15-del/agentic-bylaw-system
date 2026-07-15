"""Shared fixtures for the billing test package.

ABS-384 makes the priced-report catalog deny-by-default: with
``ADVISOR_ENABLED_QUESTIONS`` unset, the question menu is empty and every
purchase path 503s. The existing buy-an-answer / intake / free-start
suites all assume the launch catalog is purchasable, so an autouse
fixture opens the gate (``*`` = all slugs enabled) for the whole package.

The per-report gate tests (``test_report_gates.py``) override this with an
explicit ``monkeypatch.setenv`` per case, so they still exercise the
subset / deny-by-default behaviour precisely.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_all_report_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable every report slug by default so the legacy buy-flow suites
    (which predate ABS-384's deny-by-default gate) keep exercising the
    catalog. Individual gate tests override this via ``monkeypatch.setenv``.
    """
    monkeypatch.setenv("ADVISOR_ENABLED_QUESTIONS", "*")
