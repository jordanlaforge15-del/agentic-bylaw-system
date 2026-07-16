"""Off-menu "Other" question pricing — the free LLM price quote (ABS-316).

The launch product (``docs/decisions/2026-06-priced-question-catalog.md``)
sells answers to a fixed menu of priced questions. The **"Other"** path
covers anything off-menu: the user types a free-form question, an LLM
analyses its difficulty, and we **quote a price**. The quote step is
ALWAYS FREE — we never charge to produce a price (decision doc, "Cost
safety"). The user then buys at the quoted price and we answer through the
same buy-an-answer flow (``advisor.billing.answers``), bounded by the
ABS-305 cumulative cost breaker so the served cost cannot exceed the
quote's envelope.

Anchoring (ABS-311)
-------------------
Off-menu quotes are anchored to the launch-menu envelope: ~$79 for the
simplest (permitted-use-like) up to ~$299 for the most involved
(variance-package-like). The LLM does NOT emit a dollar amount directly —
that would drift and produce illegible prices. Instead it classifies the
question into one of a small set of **difficulty tiers**, and this module
maps each tier to a fixed, anchored price (the same canonical prices as
the catalog) plus a per-answer cost ceiling. A question that is *clearly*
more complex than anything on the launch menu maps to an above-band
``exceptional`` tier; everything else lands within the $79–$299 band.

Keeping the tier→price mapping in code (not in the LLM's free-text output)
means prices stay deterministic, legible, and adjustable by code review —
exactly the rationale that keeps the fixed catalog in ``questions.py``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from advisor.llm import LLMGateway, Message, TextBlock

logger = logging.getLogger(__name__)


# The off-menu purchase is persisted on ``QuestionPurchase`` with this
# sentinel slug (rather than a catalog slug). The buy-an-answer flow
# branches on it to run the user's raw question text instead of a
# catalog ``prompt_template``. Stored as a string and round-tripped
# through Stripe metadata; never rename without a data migration.
OTHER_QUESTION_SLUG = "other"

# Marker embedded in the (tools-less) difficulty-classification prompt so
# a mock gateway can resolve a deterministic tier in tests, mirroring the
# ``FOLLOWUP_CLASSIFY_MARKER`` pattern in ``advisor.billing.answers``.
QUOTE_CLASSIFY_MARKER = "__QUOTE_DIFFICULTY__"

# The launch-menu price envelope (ABS-311), in CAD cents. Used to label a
# quote ("within the $79–$299 launch band") and to keep the anchored
# tiers honest.
BAND_LOW_CENTS = 7_900  # $79 — simplest catalog question (permitted-use)
BAND_HIGH_CENTS = 29_900  # $299 — most involved (variance package)


@dataclass(frozen=True)
class DifficultyTier:
    """One off-menu difficulty bucket and its anchored price + cost cap.

    Attributes:
        key: Stable classifier label the LLM returns (``simple`` …
            ``exceptional``). Lower-cased and matched exactly.
        display_name: Human-readable difficulty shown with the quote.
        price_cents: The **price** the customer pays in CAD cents —
            anchored to the launch menu (ABS-311). Independent of our API
            **cost**.
        cumulative_token_budget: The ABS-305 cumulative per-turn ceiling
            (billed-equivalent input tokens) used when running this
            answer. This is the "quote envelope": a harder, pricier
            question is allowed more reasoning, but every tier's ceiling
            still bounds the served cost to a small fraction of the
            price, preserving the catalog's >98% gross margin.
        anchor: Which launch-menu question this difficulty is pegged to —
            provenance for the price, mirroring ``Question.catalog_anchor``.
    """

    key: str
    display_name: str
    price_cents: int
    cumulative_token_budget: int
    anchor: str


# Difficulty tiers, cheapest → hardest. The first four reuse the canonical
# launch-menu prices (ABS-311) so an off-menu quote is legibly "the same
# as" a comparable catalog question; ``exceptional`` sits just above the
# band for questions clearly more complex than anything on the menu.
#
# Cost ceilings climb with difficulty but stay tiny next to the price: at
# Opus input rates ~165k billed-equivalent tokens ≈ $2.50, so even the
# ``exceptional`` ceiling caps input spend well under the cheapest price —
# the failed-question breaker (ABS-305) trips long before an off-menu
# answer could erode the margin.
TIER_ORDER: tuple[str, ...] = (
    "simple",
    "moderate",
    "involved",
    "complex",
    "exceptional",
)

TIERS: dict[str, DifficultyTier] = {
    "simple": DifficultyTier(
        key="simple",
        display_name="Simple",
        price_cents=7_900,  # $79 — permitted-use-like
        cumulative_token_budget=120_000,
        anchor="Permitted-use check ($79 band).",
    ),
    "moderate": DifficultyTier(
        key="moderate",
        display_name="Moderate",
        price_cents=14_900,  # $149 — development-standards-like
        cumulative_token_budget=165_000,
        anchor="Development-standards compliance check ($149 band).",
    ),
    "involved": DifficultyTier(
        key="involved",
        display_name="Involved",
        price_cents=19_900,  # $199 — due-diligence / legal-nonconforming-like
        cumulative_token_budget=210_000,
        anchor="Zoning due-diligence / legal non-conforming ($199 band).",
    ),
    "complex": DifficultyTier(
        key="complex",
        display_name="Complex",
        price_cents=29_900,  # $299 — variance-package-like
        cumulative_token_budget=260_000,
        anchor="Variance justification package ($299 band).",
    ),
    "exceptional": DifficultyTier(
        key="exceptional",
        display_name="Exceptional",
        price_cents=49_900,  # $499 — clearly beyond the launch menu
        cumulative_token_budget=330_000,
        anchor="Above the launch band — clearly more complex than the menu.",
    ),
}

# Where an unrecognised / unparseable classification lands. ``moderate``
# is a deliberate middle-of-band default: it neither under-charges a
# genuinely involved question nor over-charges a simple one, and the
# event is logged loudly so a misbehaving classifier is visible.
_FALLBACK_TIER = "moderate"


@dataclass(frozen=True)
class Quote:
    """A free price quote for an off-menu question.

    Produced by :func:`quote_question`. Carries everything the frontend
    needs to show the price and everything :func:`advisor.billing.answers
    .start_other_checkout` needs to bind the purchase: the price, the
    difficulty + rationale (so the quote is explainable), and the cost
    envelope the answer will run under.
    """

    question_text: str
    difficulty: str
    difficulty_display_name: str
    price_cents: int
    currency: str
    rationale: str
    cumulative_token_budget: int
    anchor: str
    band_low_cents: int = BAND_LOW_CENTS
    band_high_cents: int = BAND_HIGH_CENTS


class QuoteError(Exception):
    """Base class for off-menu quoting errors."""


class EmptyQuestionError(QuoteError):
    """The free-form question was empty or too short to price. → HTTP 400.

    Caught at the API edge. No LLM call is made — there is nothing to
    quote, so there is nothing to (freely) classify.
    """


# Minimum characters for a question we'll attempt to price. Below this it
# is noise ("hi", "?"), not a question — reject before spending an LLM
# call. Deliberately small; intake refinement (ABS-315) handles vague-but-
# real questions, not empty ones.
_MIN_QUESTION_CHARS = 8


def _default_model() -> str:
    from advisor.llm.registry import get_settings  # noqa: PLC0415

    return get_settings().main_model


def tier_for(key: str) -> DifficultyTier:
    """Map a classifier label to its tier, falling back safely.

    An unrecognised label (a classifier that returned a tier we don't
    know) resolves to the middle-of-band ``moderate`` tier and logs a
    warning rather than raising — a transient classifier quirk should
    degrade to a fair mid-band price, never crash a checkout.
    """
    tier = TIERS.get((key or "").strip().lower())
    if tier is None:
        logger.warning(
            "off-menu quote: unrecognised difficulty %r; defaulting to %s",
            key,
            _FALLBACK_TIER,
        )
        return TIERS[_FALLBACK_TIER]
    return tier


def _classification_prompt(question_text: str) -> str:
    tier_lines = "\n".join(
        f"- {t.key}: {t.anchor}" for t in (TIERS[k] for k in TIER_ORDER)
    )
    return (
        f"{QUOTE_CLASSIFY_MARKER}\n"
        "You are pricing an off-menu land-use by-law question for a "
        "Halifax zoning-analysis tool. Classify how much research and "
        "reasoning the question needs into ONE difficulty tier, anchored "
        "to this menu of comparable priced questions:\n"
        f"{tier_lines}\n\n"
        "Pick 'simple'..'complex' for anything comparable to the menu; "
        "use 'exceptional' ONLY when the question is clearly more complex "
        "than every menu item (e.g. multi-parcel assembly, layered "
        "overlays, and a rezoning all at once).\n\n"
        f"The question:\n{question_text!r}\n\n"
        'Respond with ONLY compact JSON: {"difficulty": "<tier>", '
        '"rationale": "<one sentence>"}.'
    )


async def quote_question(
    gateway: LLMGateway,
    question_text: str,
    *,
    model: str | None = None,
) -> Quote:
    """Produce a FREE price quote for an off-menu question.

    Runs a single tools-less LLM classification call (no tool loop, no
    charge — we never bill to produce a price), maps the returned
    difficulty tier to an anchored price + cost envelope, and returns a
    :class:`Quote`. Raises :class:`EmptyQuestionError` before any LLM call
    when there is nothing to price.

    Robust by construction: a transport/parse error or an unknown tier
    degrades to the middle-of-band ``moderate`` price (logged), so a
    classifier hiccup never blocks a customer from buying — it just gets a
    fair default price.
    """
    from advisor.llm import CompletionRequest  # noqa: PLC0415

    cleaned = (question_text or "").strip()
    if len(cleaned) < _MIN_QUESTION_CHARS:
        raise EmptyQuestionError(
            "question is empty or too short to price; please describe the "
            "by-law question you want answered."
        )

    request = CompletionRequest(
        model=model or _default_model(),
        system=(
            "You classify the difficulty of land-use by-law questions for "
            "pricing. Output only JSON."
        ),
        messages=[Message(role="user", content=_classification_prompt(cleaned))],
    )

    difficulty_key = _FALLBACK_TIER
    rationale = "Priced at the mid-band default."
    try:
        response = await gateway.complete(request)
        text = "".join(
            b.text for b in response.content if isinstance(b, TextBlock)
        )
        verdict = json.loads(text)
        difficulty_key = str(verdict.get("difficulty", _FALLBACK_TIER))
        rationale = str(
            verdict.get("rationale", "") or "Priced from question difficulty."
        )
    except Exception:  # noqa: BLE001 — never let a quote crash a checkout
        logger.warning(
            "off-menu quote classification failed; using mid-band default",
            exc_info=True,
        )

    tier = tier_for(difficulty_key)
    return Quote(
        question_text=cleaned,
        difficulty=tier.key,
        difficulty_display_name=tier.display_name,
        price_cents=tier.price_cents,
        currency="CAD",
        rationale=rationale.strip(),
        cumulative_token_budget=tier.cumulative_token_budget,
        anchor=tier.anchor,
    )
