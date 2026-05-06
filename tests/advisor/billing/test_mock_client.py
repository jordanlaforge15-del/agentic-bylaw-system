"""MockStripeClient: scripted responses + call recording."""
from __future__ import annotations

import json

import pytest

from advisor.billing.client import (
    CheckoutSessionResult,
    MockStripeClient,
    StripeCustomer,
    StripeEvent,
)


def test_create_checkout_session_records_call_and_returns_scripted_url() -> None:
    expected = CheckoutSessionResult(session_id="cs_test_1", url="https://stripe/c/1")
    client = MockStripeClient(checkout_result=expected)
    result = client.create_checkout_session(
        customer_id="cus_1",
        customer_email="u@example.com",
        price_id="price_pro",
        success_url="https://app/success",
        cancel_url="https://app/cancel",
        metadata={"advisor_user_id": "42", "target_tier": "pro"},
    )
    assert result is expected
    assert len(client.checkout_calls) == 1
    call = client.checkout_calls[0]
    assert call.customer_id == "cus_1"
    assert call.customer_email == "u@example.com"
    assert call.price_id == "price_pro"
    assert call.metadata == {"advisor_user_id": "42", "target_tier": "pro"}


def test_create_checkout_session_pops_through_a_queue() -> None:
    one = CheckoutSessionResult(session_id="cs_1", url="https://stripe/1")
    two = CheckoutSessionResult(session_id="cs_2", url="https://stripe/2")
    client = MockStripeClient(checkout_results=[one, two])
    first = client.create_checkout_session(
        customer_id=None,
        customer_email="a@b",
        price_id="p",
        success_url="s",
        cancel_url="c",
        metadata={},
    )
    second = client.create_checkout_session(
        customer_id=None,
        customer_email="a@b",
        price_id="p",
        success_url="s",
        cancel_url="c",
        metadata={},
    )
    assert first is one
    assert second is two


def test_create_checkout_session_raises_when_unconfigured() -> None:
    client = MockStripeClient()
    with pytest.raises(AssertionError):
        client.create_checkout_session(
            customer_id=None,
            customer_email="a@b",
            price_id="p",
            success_url="s",
            cancel_url="c",
            metadata={},
        )


def test_construct_webhook_event_decodes_json_payload_by_default() -> None:
    client = MockStripeClient()
    payload = json.dumps(
        {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_1", "customer": "cus_1"}},
        }
    ).encode("utf-8")
    event = client.construct_webhook_event(
        payload=payload, sig_header="t=1,v1=abc", secret="whsec_test"
    )
    assert event.id == "evt_test_1"
    assert event.type == "checkout.session.completed"
    assert event.data["customer"] == "cus_1"
    # The call was captured for assertion.
    assert client.webhook_calls[0].sig_header == "t=1,v1=abc"


def test_construct_webhook_event_returns_scripted_event() -> None:
    expected = StripeEvent(id="evt_x", type="customer.subscription.updated", data={})
    client = MockStripeClient(webhook_events=[expected])
    out = client.construct_webhook_event(
        payload=b"{}", sig_header="sig", secret="whsec"
    )
    assert out is expected


def test_construct_webhook_event_raises_signature_error_when_set() -> None:
    err = ValueError("bad sig")
    client = MockStripeClient(signature_error=err)
    with pytest.raises(ValueError, match="bad sig"):
        client.construct_webhook_event(
            payload=b"{}", sig_header="bad", secret="whsec"
        )


def test_get_customer_returns_configured_customer() -> None:
    cust = StripeCustomer(id="cus_42", email="x@y.com")
    client = MockStripeClient(customers={"cus_42": cust})
    assert client.get_customer("cus_42") is cust
    assert client.get_customer("cus_missing") is None
