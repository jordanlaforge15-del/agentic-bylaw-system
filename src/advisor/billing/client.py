"""Protocol-based Stripe client + live/mock implementations.

We can't take a hard dependency on the ``stripe`` SDK at module level
because:

1. The user doesn't have a Stripe account yet, so the SDK isn't
   exercised in tests.
2. Even when the SDK IS installed, importing it on every test that
   touches the billing module is wasteful — it pulls in ``requests``
   and a few hundred class definitions.

The fix: expose a ``StripeClient`` Protocol and two implementations.
``LiveStripeClient`` lazy-imports the SDK inside ``__init__``, so the
module can be imported and the Protocol referenced without the SDK
being present. ``MockStripeClient`` lets tests assert on call args
without any Stripe code in the loop at all.

This mirrors the pattern in ``advisor.llm.base.LLMGateway`` /
``advisor.llm.mock.MockGateway`` / ``advisor.llm.anthropic_backend
.AnthropicGateway``.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CheckoutSessionResult:
    """What a successful Checkout-session creation returns.

    We surface only the fields the rest of the app needs:
    ``url`` for redirecting the browser, and ``session_id`` for
    bookkeeping if we ever want to correlate redirect-success pages
    with the originating session.
    """

    session_id: str
    url: str


@dataclass(frozen=True)
class StripeSubscriptionItem:
    """One line-item on a Stripe subscription.

    A subscription has a list of items, each pointing at a Price. For
    our single-tier-per-subscription model we always look at
    ``items[0]``, but the data structure is general.
    """

    price_id: str


@dataclass(frozen=True)
class StripeCustomer:
    """Subset of a Stripe customer we care about.

    Carries the email and metadata so the webhook can resolve back to
    our user record if the Checkout-session metadata is missing (e.g.
    user resubscribed after we lost track).
    """

    id: str
    email: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StripeEvent:
    """A Stripe webhook event after signature verification.

    ``id`` is the Stripe event id (``evt_...``), used for
    idempotency. ``type`` is the event type
    (``"checkout.session.completed"`` etc.). ``data`` is the
    ``data.object`` field from the webhook payload — we don't model
    every Stripe object as a typed structure because:

    * The shape varies by event type.
    * The webhook handler only needs a handful of fields per type.
    * Converting to typed structures would be busy-work that adds
      lag every time Stripe extends an event payload.

    Tests build ``StripeEvent`` instances directly with hand-shaped
    dicts; the live path passes through whatever Stripe sends.
    """

    id: str
    type: str
    data: dict[str, Any]


@runtime_checkable
class StripeClient(Protocol):
    """Provider-agnostic Stripe interface.

    Concrete implementations live in this module
    (``LiveStripeClient`` and ``MockStripeClient``). Everything else
    in the billing module — the router, the checkout helper, the
    webhook handler — depends only on this Protocol, so swapping
    implementations is a one-line change at the FastAPI factory.
    """

    name: str  # implementation id, for logging

    def create_checkout_session(
        self,
        *,
        customer_id: str | None,
        customer_email: str,
        price_id: str | None = None,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        mode: str = "payment",
        capture_method: str | None = None,
        amount_cents: int | None = None,
        product_name: str | None = None,
        currency: str = "cad",
    ) -> CheckoutSessionResult:
        """Create a Stripe Checkout session.

        ``mode`` is ``"payment"`` for one-time pack purchases (the
        case-credit model) and ``"subscription"`` for the legacy plan
        flow. Defaults to one-time because subscriptions have been
        removed from the user-facing surface, but the parameter is
        kept so a future restore would be a one-line change.

        ``capture_method`` controls how the resulting PaymentIntent is
        settled. The default (``None``) means immediate capture —
        Stripe charges the card when the session completes. Pass
        ``"manual"`` for the priced-question "authorize then
        capture/void" flow (ABS-312): the card is only AUTHORIZED at
        checkout, and the caller later captures the hold on a grounded
        answer or voids it on a failed question. Only meaningful for
        ``mode="payment"``.

        **Pricing — fixed Price vs ad-hoc amount.** A catalog purchase
        passes ``price_id`` (a pre-created Stripe Price). The off-menu
        "Other" path (ABS-316) has no pre-created Price — its amount is
        the LLM-quoted price — so it passes ``amount_cents`` +
        ``product_name`` instead and Stripe builds an inline
        ``price_data`` line item in ``currency`` (lower-case ISO, e.g.
        ``"cad"``). Exactly one of ``price_id`` / ``amount_cents`` must
        be supplied.

        ``customer_id`` may be ``None`` for first-time customers;
        Stripe will create the customer record and the webhook will
        then back-fill ``advisor_user.stripe_customer_id``.
        """

    def construct_webhook_event(
        self, *, payload: bytes, sig_header: str, secret: str
    ) -> StripeEvent:
        """Verify the signature on a webhook payload and return the
        decoded event. Raises on signature failure."""

    def get_customer(self, customer_id: str) -> StripeCustomer | None:
        """Fetch a Stripe customer by id, or ``None`` if not found."""

    def capture_payment_intent(self, payment_intent_id: str) -> str:
        """Capture a previously-authorized (manual-capture) PaymentIntent.

        Used by the priced-question flow when the engine delivers a
        grounded answer: the held authorization becomes a real charge.
        Returns the PaymentIntent status string (``"succeeded"`` on a
        clean capture). Raises on a Stripe error.
        """

    def cancel_payment_intent(self, payment_intent_id: str) -> str:
        """Void (cancel) an uncaptured PaymentIntent — the customer is
        never charged.

        This is the failed-question mechanism: when a question is
        ungroundable / zero-evidence / hits the cost ceiling, the
        authorization is released so nothing hits the customer's
        statement. Returns the PaymentIntent status (``"canceled"``).
        Raises on a Stripe error.
        """


def _build_line_item(
    *,
    price_id: str | None,
    amount_cents: int | None,
    product_name: str | None,
    currency: str,
) -> dict[str, Any]:
    """Build the single Checkout line item for either pricing shape.

    Catalog purchases reference a pre-created Stripe ``price_id``; the
    off-menu "Other" path (ABS-316) has no Price object and supplies an
    inline ``price_data`` amount instead. Exactly one of the two must be
    given — passing both, or neither, is a programming error and raises
    ``ValueError`` rather than letting Stripe reject the call opaquely.
    """
    if price_id and amount_cents is not None:
        raise ValueError(
            "create_checkout_session: pass price_id OR amount_cents, not both."
        )
    if price_id:
        return {"price": price_id, "quantity": 1}
    if amount_cents is not None:
        if amount_cents <= 0:
            raise ValueError(
                f"create_checkout_session: amount_cents must be positive; "
                f"got {amount_cents}."
            )
        return {
            "price_data": {
                "currency": currency,
                "product_data": {
                    "name": product_name or "Bylaw answer (custom question)"
                },
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }
    raise ValueError(
        "create_checkout_session: one of price_id / amount_cents is required."
    )


# ---------------------------------------------------------------------------
# Live implementation. The ``stripe`` SDK is lazy-imported inside
# ``__init__`` so the rest of the module can be imported in
# environments that don't have the SDK installed (tests, local dev
# without Stripe creds).
# ---------------------------------------------------------------------------


class LiveStripeClient:
    """``StripeClient`` backed by the real ``stripe`` SDK.

    Instantiated only when ``settings.enabled`` is True AND
    ``settings.stripe_api_key`` is set. The SDK is imported inside
    ``__init__`` rather than at module top so that:

    * The billing module imports cleanly without ``stripe`` installed.
    * Tests that don't exercise the live client never pay the import
      cost.
    """

    name = "live"

    def __init__(self, *, api_key: str, stripe_module: Any | None = None) -> None:
        if not api_key:
            # Fail loud rather than silently using whatever
            # ``stripe.api_key`` already happens to be (which is
            # process-global state and a security footgun).
            raise ValueError(
                "LiveStripeClient requires api_key; got an empty string."
            )
        if stripe_module is None:
            # Lazy import so the rest of the billing module works
            # without the SDK present. We pass the imported module
            # around as an attribute rather than rebinding the global
            # ``stripe`` module's ``api_key`` so unit tests can
            # construct multiple clients with different keys.
            import stripe as stripe_module  # noqa: PLC0415 — lazy

        # Note: ``stripe.api_key`` is a process-global. We set it for
        # backwards compatibility with helpers that read it, but our
        # own calls go through the captured module reference and
        # explicit ``api_key=`` kwargs where the SDK supports them.
        stripe_module.api_key = api_key
        self._stripe = stripe_module
        self._api_key = api_key

    def create_checkout_session(
        self,
        *,
        customer_id: str | None,
        customer_email: str,
        price_id: str | None = None,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        mode: str = "payment",
        capture_method: str | None = None,
        amount_cents: int | None = None,
        product_name: str | None = None,
        currency: str = "cad",
    ) -> CheckoutSessionResult:
        line_item = _build_line_item(
            price_id=price_id,
            amount_cents=amount_cents,
            product_name=product_name,
            currency=currency,
        )
        params: dict[str, Any] = {
            "mode": mode,
            "line_items": [line_item],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": dict(metadata),
        }
        # The legacy subscription flow needs the metadata mirrored
        # onto the subscription itself so it survives the
        # ``customer.subscription.*`` lifecycle events. One-time
        # ``payment`` mode produces a payment_intent + the metadata
        # round-trips through the checkout.session.completed event
        # directly, so no mirroring is needed.
        if mode == "subscription":
            params["subscription_data"] = {"metadata": dict(metadata)}
        if capture_method is not None and mode == "payment":
            # ABS-312: authorize-then-capture. Mirror the metadata onto
            # the PaymentIntent too so a manual capture/void can resolve
            # the originating question purchase from the PI alone.
            params["payment_intent_data"] = {
                "capture_method": capture_method,
                "metadata": dict(metadata),
            }
        if customer_id:
            params["customer"] = customer_id
        else:
            # First-time buyer — let Stripe create the customer with
            # the supplied email. The webhook back-fills
            # ``advisor_user.stripe_customer_id`` afterwards.
            params["customer_email"] = customer_email
        session = self._stripe.checkout.Session.create(**params)
        return CheckoutSessionResult(
            session_id=session["id"],
            url=session["url"],
        )

    def construct_webhook_event(
        self, *, payload: bytes, sig_header: str, secret: str
    ) -> StripeEvent:
        # ``stripe.Webhook.construct_event`` raises
        # ``stripe.error.SignatureVerificationError`` on bad sigs;
        # callers translate to 400.
        event = self._stripe.Webhook.construct_event(payload, sig_header, secret)
        # The SDK returns a dict-like object with ``.id``, ``.type``,
        # ``.data.object`` — normalise to our dataclass shape.
        data = event["data"]["object"]
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        return StripeEvent(id=event["id"], type=event["type"], data=dict(data))

    def get_customer(self, customer_id: str) -> StripeCustomer | None:
        try:
            customer = self._stripe.Customer.retrieve(customer_id)
        except Exception:  # pragma: no cover — defensive; SDK raises various
            return None
        return StripeCustomer(
            id=customer["id"],
            email=customer.get("email"),
            metadata=dict(customer.get("metadata") or {}),
        )

    def capture_payment_intent(self, payment_intent_id: str) -> str:
        intent = self._stripe.PaymentIntent.capture(payment_intent_id)
        return str(intent["status"])

    def cancel_payment_intent(self, payment_intent_id: str) -> str:
        intent = self._stripe.PaymentIntent.cancel(payment_intent_id)
        return str(intent["status"])


# ---------------------------------------------------------------------------
# Mock implementation. Tests script responses, then assert on the
# captured call records. Mirrors the ergonomics of
# ``advisor.llm.mock.MockGateway``.
# ---------------------------------------------------------------------------


@dataclass
class _CheckoutCall:
    customer_id: str | None
    customer_email: str
    price_id: str | None
    success_url: str
    cancel_url: str
    metadata: dict[str, str]
    mode: str = "payment"
    capture_method: str | None = None
    # Off-menu "Other" ad-hoc pricing (ABS-316). ``None`` for catalog
    # purchases that pass a ``price_id``.
    amount_cents: int | None = None
    product_name: str | None = None
    currency: str = "cad"


@dataclass
class _WebhookCall:
    payload: bytes
    sig_header: str
    secret: str


@dataclass
class _PaymentIntentCall:
    """A capture or cancel call against a PaymentIntent id."""

    payment_intent_id: str
    action: str  # "capture" | "cancel"


class MockStripeClient:
    """Scriptable ``StripeClient`` for tests.

    ``checkout_results`` is a queue of ``CheckoutSessionResult``
    objects to return from successive ``create_checkout_session``
    calls (or a single value reused for every call).
    ``webhook_events`` is a queue of ``StripeEvent`` results, or a
    callable that builds one from the payload — useful when the test
    wants to assert on the payload + sig header.
    """

    name = "mock"

    def __init__(
        self,
        *,
        checkout_result: CheckoutSessionResult | None = None,
        checkout_results: list[CheckoutSessionResult] | None = None,
        webhook_events: list[StripeEvent] | None = None,
        webhook_event_factory: Callable[[bytes, str, str], StripeEvent]
        | None = None,
        customers: dict[str, StripeCustomer] | None = None,
        signature_error: Exception | None = None,
    ) -> None:
        self._checkout_results = list(checkout_results or [])
        if checkout_result is not None:
            self._checkout_results.append(checkout_result)
        self._webhook_events = list(webhook_events or [])
        self._webhook_event_factory = webhook_event_factory
        self._customers = dict(customers or {})
        self._signature_error = signature_error
        self.checkout_calls: list[_CheckoutCall] = []
        self.webhook_calls: list[_WebhookCall] = []
        # ABS-312: record manual capture/void calls so tests can assert
        # the failed-question rule (capture on success, void on failure).
        self.payment_intent_calls: list[_PaymentIntentCall] = []

    def create_checkout_session(
        self,
        *,
        customer_id: str | None,
        customer_email: str,
        price_id: str | None = None,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        mode: str = "payment",
        capture_method: str | None = None,
        amount_cents: int | None = None,
        product_name: str | None = None,
        currency: str = "cad",
    ) -> CheckoutSessionResult:
        # Validate the pricing shape exactly as the live client does, so
        # the "exactly one of price_id / amount_cents" contract is
        # exercised in tests rather than only against real Stripe.
        _build_line_item(
            price_id=price_id,
            amount_cents=amount_cents,
            product_name=product_name,
            currency=currency,
        )
        self.checkout_calls.append(
            _CheckoutCall(
                customer_id=customer_id,
                customer_email=customer_email,
                price_id=price_id,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=dict(metadata),
                mode=mode,
                capture_method=capture_method,
                amount_cents=amount_cents,
                product_name=product_name,
                currency=currency,
            )
        )
        if not self._checkout_results:
            raise AssertionError(
                "MockStripeClient: no scripted checkout_result; pass "
                "checkout_result= or checkout_results= when constructing."
            )
        if len(self._checkout_results) == 1:
            # Single value sticks around — convenient for tests that
            # don't care how many times the route gets hit.
            return self._checkout_results[0]
        return self._checkout_results.pop(0)

    def construct_webhook_event(
        self, *, payload: bytes, sig_header: str, secret: str
    ) -> StripeEvent:
        self.webhook_calls.append(
            _WebhookCall(payload=payload, sig_header=sig_header, secret=secret)
        )
        if self._signature_error is not None:
            raise self._signature_error
        if self._webhook_event_factory is not None:
            return self._webhook_event_factory(payload, sig_header, secret)
        if not self._webhook_events:
            # Fall back to decoding the payload as JSON so simple
            # tests can pass an event in the body without scripting.
            decoded = json.loads(payload.decode("utf-8"))
            return StripeEvent(
                id=decoded.get("id", "evt_mock"),
                type=decoded.get("type", "unknown"),
                data=dict(decoded.get("data", {}).get("object", {})),
            )
        if len(self._webhook_events) == 1:
            return self._webhook_events[0]
        return self._webhook_events.pop(0)

    def get_customer(self, customer_id: str) -> StripeCustomer | None:
        return self._customers.get(customer_id)

    def capture_payment_intent(self, payment_intent_id: str) -> str:
        self.payment_intent_calls.append(
            _PaymentIntentCall(
                payment_intent_id=payment_intent_id, action="capture"
            )
        )
        return "succeeded"

    def cancel_payment_intent(self, payment_intent_id: str) -> str:
        self.payment_intent_calls.append(
            _PaymentIntentCall(
                payment_intent_id=payment_intent_id, action="cancel"
            )
        )
        return "canceled"
