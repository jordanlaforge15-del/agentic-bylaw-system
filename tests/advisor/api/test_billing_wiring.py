"""ABS-341: the dev entrypoint must honor ``ADVISOR_BILLING_ENABLED``.

Regression coverage for the bug where ``advisor.api.dev`` mounted the
dormant billing stub unconditionally — it never passed billing kwargs to
``create_app``, so setting ``ADVISOR_BILLING_ENABLED=true`` on the local
dev server was a silent no-op and ``/cases/new`` stayed on the
"coming soon" fallback. The flag-honoring wiring now lives in the shared
:func:`advisor.api.billing_wiring.build_billing_kwargs` helper, consumed by
both ``advisor.api.main`` (prod) and ``advisor.api.dev``.

The observable discriminator between the live and dormant billing routers
is ``GET /v1/billing/questions``: the live router reports ``enabled: true``
(and ``available: true`` questions, which is what makes the buy/free-trial
CTA render); the dormant stub hardcodes ``enabled: false`` (every question
``available: false`` → "coming soon").
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from advisor.api import create_app
from advisor.api.billing_wiring import build_billing_kwargs
from advisor.auth.mock_clerk import build_mock_verifier
from advisor.billing.settings import AdvisorBillingSettings, get_settings
from advisor.llm.mock import MockGateway, text_response


def _mock_gateway() -> MockGateway:
    return MockGateway(scripted=[text_response("ok")])


def _questions_enabled(app) -> bool:
    """``enabled`` flag the billing menu reports — live True, dormant False."""
    resp = TestClient(app).get("/v1/billing/questions")
    assert resp.status_code == 200, resp.text
    return resp.json()["enabled"]


def _app_with(kwargs: dict):
    return create_app(
        gateway=_mock_gateway(),
        retrieval_service_factory=lambda: None,
        **kwargs,
    )


# ----- the shared helper -----------------------------------------------------


def test_build_billing_kwargs_disabled_returns_settings_only():
    settings = AdvisorBillingSettings(ADVISOR_BILLING_ENABLED=False)
    assert build_billing_kwargs(verifier=None, billing_settings=settings) == {
        "billing_settings": settings
    }


def test_build_billing_kwargs_enabled_requires_clerk():
    settings = AdvisorBillingSettings(ADVISOR_BILLING_ENABLED=True)
    with pytest.raises(RuntimeError, match="Clerk verifier"):
        build_billing_kwargs(verifier=None, billing_settings=settings)


def test_build_billing_kwargs_payments_on_requires_stripe_key():
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True, ADVISOR_PAYMENTS_ENABLED=True
    )
    with pytest.raises(RuntimeError, match="STRIPE_API_KEY"):
        build_billing_kwargs(
            verifier=build_mock_verifier(), billing_settings=settings
        )


def test_build_billing_kwargs_payments_off_wires_live_deps_without_stripe():
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True, ADVISOR_PAYMENTS_ENABLED=False
    )
    kwargs = build_billing_kwargs(
        verifier=build_mock_verifier(), billing_settings=settings
    )
    # Payments-off: no Stripe client, but the live router still gets its
    # DB + user wiring so create_app mounts it.
    assert kwargs["stripe_client_factory"] is None
    assert kwargs["billing_db_session_factory"] is not None
    assert kwargs["billing_user_dependency"] is not None
    assert kwargs["billing_user_resolver"] is not None


# ----- create_app mount decision (observable via the menu's enabled flag) ----


def test_create_app_mounts_live_router_with_enabled_kwargs():
    settings = AdvisorBillingSettings(
        ADVISOR_BILLING_ENABLED=True, ADVISOR_PAYMENTS_ENABLED=False
    )
    kwargs = build_billing_kwargs(
        verifier=build_mock_verifier(), billing_settings=settings
    )
    assert _questions_enabled(_app_with(kwargs)) is True


def test_create_app_mounts_dormant_router_when_disabled():
    settings = AdvisorBillingSettings(ADVISOR_BILLING_ENABLED=False)
    kwargs = build_billing_kwargs(verifier=None, billing_settings=settings)
    assert _questions_enabled(_app_with(kwargs)) is False


# ----- the dev entrypoint (the actual ABS-341 regression) -------------------


@pytest.fixture
def dev_module(monkeypatch):
    """Import ``advisor.api.dev`` and stub its heavy bits.

    ``advisor.api.dev`` builds its app at import time (the uvicorn target
    ``advisor.api.dev:app``), which constructs the real LLM gateway via
    ``build_gateway`` — that needs a real ANTHROPIC_API_KEY. Patch
    ``build_gateway`` at the registry source BEFORE importing ``dev`` so
    its ``from advisor.llm.registry import build_gateway`` binds the mock,
    and patch it on ``dev`` too for the per-test ``build_dev_app()`` calls.
    Nothing here touches the network.
    """
    import advisor.llm.registry as registry

    monkeypatch.setattr(registry, "build_gateway", _mock_gateway)
    import advisor.api.dev as dev

    monkeypatch.setattr(dev, "build_gateway", _mock_gateway)
    monkeypatch.setattr(dev, "setup_logging", lambda **_: None)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    monkeypatch.delenv("ADVISOR_PAYMENTS_ENABLED", raising=False)
    yield dev
    # ``get_settings`` is @lru_cache'd process-wide; reset it so the next
    # test (and the rest of the suite) re-reads the environment.
    get_settings.cache_clear()


def test_dev_app_billing_disabled_is_dormant(monkeypatch, dev_module):
    monkeypatch.delenv("ADVISOR_BILLING_ENABLED", raising=False)
    get_settings.cache_clear()
    assert _questions_enabled(dev_module.build_dev_app()) is False


def test_dev_app_billing_enabled_without_clerk_degrades_to_dormant(
    monkeypatch, caplog, dev_module
):
    # Enabled flag, but no Clerk — the dev server must still boot (it also
    # serves chat/cases) and stay dormant with a loud warning, not crash.
    monkeypatch.setenv("ADVISOR_BILLING_ENABLED", "true")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING):
        app = dev_module.build_dev_app()
    assert _questions_enabled(app) is False
    assert "DORMANT" in caplog.text
