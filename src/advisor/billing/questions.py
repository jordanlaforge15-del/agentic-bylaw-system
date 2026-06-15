"""Priced-question catalog — single source of truth for the launch menu.

The "buy an answer" launch product (decision:
``docs/decisions/2026-06-priced-question-catalog.md``) sells **answers to
questions**, not free-form chat and not turn packs. The user picks one
question from a fixed menu, pays a fixed price, and gets the answer the
existing chat engine produces today.

This module is the question-pricing analogue of the retired
``packs.py`` tier/pack catalog: it lives in code (not env, not DB) for
the same reason — question wording, prices, and the engine binding are
product copy. Changing them is a code review + test + deploy, not an
ops twiddle.

Checkout shape is **pure per-question** (decided 2026-06-12): one
Stripe Price per catalog question, no pack SKUs, no credit/balance
ledger, no token currency. The user pays for exactly the question
asked. The mapping ``Stripe Price ID -> question`` is environment-
specific (one set in test mode, another in live mode), so the Price IDs
live on ``AdvisorBillingSettings`` under the
``STRIPE_PRICE_QUESTION_<SLUG>`` convention;
``question_for_stripe_price_id`` does the reverse lookup at webhook time.

Launch menu (Stage-1 products from the productization catalog; all
low-discretion / anyone-can-author, priced within the doc's bands and
trivially adjustable):

==  ============================================  ======  ====================================================
#   Question                                      Price   Backing engine calls
==  ============================================  ======  ====================================================
1   Permitted-use check                           $79     get_address_profile + get_zone_profile
2   Development-standards compliance check         $149    get_zone_profile + evaluate_submission_against_bylaws
3   Zoning due-diligence summary (preliminary)    $199    get_address_profile + search_bylaw_evidence
4   Legal non-conforming determination            $199    evaluate_submission_against_bylaws + search_bylaw_evidence
5   Variance justification package                $299    evaluate_submission_against_bylaws
==  ============================================  ======  ====================================================

The off-menu "Other" path (LLM-quoted) is handled separately in
ABS-316; it does not use a pre-created Stripe Price, so it is not in
this catalog.
"""
from __future__ import annotations

from dataclasses import dataclass


# Question slug identifiers — persisted on the purchase row and round-
# tripped through Stripe checkout metadata. Stored as strings; never
# rename without a data migration.
QUESTION_PERMITTED_USE = "permitted_use"
QUESTION_DEVELOPMENT_STANDARDS = "development_standards"
QUESTION_DUE_DILIGENCE = "due_diligence"
QUESTION_LEGAL_NONCONFORMING = "legal_nonconforming"
QUESTION_VARIANCE_JUSTIFICATION = "variance_justification"


@dataclass(frozen=True)
class InputField:
    """One required-or-optional input a question needs before it can be
    answered.

    The buy-an-answer flow (ABS-312) collects these up front; the
    consultant-style LLM intake (ABS-315) detects which are missing and
    asks for them conversationally. Either way this is the schema of
    what the engine needs.

    Attributes:
        name: Machine identifier; matches a ``{placeholder}`` in the
            owning question's ``prompt_template``.
        label: Human-readable prompt shown when collecting the input.
        required: Whether the question can be answered without it. A
            missing required input is an "unworkable inputs" failure
            under the failed-question rule (no charge / void the hold).
        description: One-line help text / example for the input.
    """

    name: str
    label: str
    required: bool
    description: str


@dataclass(frozen=True)
class Question:
    """One priced catalog question — what the user buys.

    Attributes:
        slug: Stable identifier (e.g. ``permitted_use``). Persisted and
            round-tripped through Stripe metadata; never rename without
            a migration.
        display_name: Human-readable name for the question menu UI.
        price_cents: Fixed list price in CAD cents. This is the **price**
            (what the customer pays), independent of our API **cost**
            (bounded separately by the ABS-305 per-answer breaker).
        summary: Short blurb for the menu card — what the answer covers.
        backing_calls: Names of the existing chat-engine tools this
            question's answer is grounded in. Informational/binding
            metadata; the engine still selects tools per turn, but a
            question whose backing calls can't run is ungroundable
            (failed-question rule).
        required_inputs: The input schema — what the engine needs before
            it can answer. See ``InputField``.
        prompt_template: The prompt/handler binding to the chat engine.
            ``str.format``-style template with ``{placeholder}`` slots
            matching ``required_inputs`` names. The buy-an-answer flow
            fills it and sends it as the opening user turn.
        catalog_anchor: Provenance — which productization-catalog item
            and consultant proxy price this question maps to. Kept so the
            price's justification travels with the definition.
    """

    slug: str
    display_name: str
    price_cents: int
    summary: str
    backing_calls: tuple[str, ...]
    required_inputs: tuple[InputField, ...]
    prompt_template: str
    catalog_anchor: str

    @property
    def stripe_price_env_var(self) -> str:
        """Env-var name holding the Stripe Price ID for this question.

        Convention: ``STRIPE_PRICE_QUESTION_<SLUG>``, all uppercase.
        e.g. ``STRIPE_PRICE_QUESTION_PERMITTED_USE``. Looked up against
        ``AdvisorBillingSettings`` at checkout / webhook time.
        """
        return f"STRIPE_PRICE_QUESTION_{self.slug.upper()}"


# -- The launch catalog -----------------------------------------------------
# Prices are grounded defaults within the productization catalog's bands;
# adjusting any is a code change + test update, never an env/DB twiddle.

QUESTION_PERMITTED_USE_DEF = Question(
    slug=QUESTION_PERMITTED_USE,
    display_name="Permitted-use check",
    price_cents=7_900,  # $79 CAD
    summary=(
        "Is a specific use allowed at a specific address? We resolve the "
        "address to its zone and report whether the use is permitted, "
        "permitted-as-of-right, conditional, or prohibited, with the "
        "governing by-law provisions."
    ),
    backing_calls=("get_address_profile", "get_zone_profile"),
    required_inputs=(
        InputField(
            name="address",
            label="Property address",
            required=True,
            description="Civic address within the Halifax Regional Centre.",
        ),
        InputField(
            name="proposed_use",
            label="Proposed use",
            required=True,
            description="The use to check, e.g. 'a four-unit dwelling'.",
        ),
    ),
    prompt_template=(
        "Is {proposed_use} permitted at {address}? Resolve the address to "
        "its zone, then state whether the use is permitted as-of-right, "
        "conditional, or prohibited, citing the governing by-law provisions."
    ),
    catalog_anchor="Catalog item 2 — permitted-use check ($79 band).",
)

QUESTION_DEVELOPMENT_STANDARDS_DEF = Question(
    slug=QUESTION_DEVELOPMENT_STANDARDS,
    display_name="Development-standards compliance check",
    price_cents=14_900,  # $149 CAD
    summary=(
        "Does a proposed project comply with the as-of-right development "
        "standards of its zone — height, setbacks, lot coverage, floor-area "
        "ratio, and parking? We evaluate the supplied dimensions against the "
        "zone's standards and flag every non-conformity."
    ),
    backing_calls=("get_zone_profile", "evaluate_submission_against_bylaws"),
    required_inputs=(
        InputField(
            name="address",
            label="Property address",
            required=True,
            description="Civic address within the Halifax Regional Centre.",
        ),
        InputField(
            name="project_details",
            label="Project dimensions",
            required=True,
            description=(
                "Proposed height, setbacks, lot coverage, FAR, and parking "
                "count to test against the zone standards."
            ),
        ),
    ),
    prompt_template=(
        "Evaluate whether this project at {address} meets the as-of-right "
        "development standards of its zone (height, setbacks, lot coverage, "
        "FAR, parking). Project details: {project_details}. List each "
        "standard, the proposed value, and pass/fail with the governing "
        "by-law provision."
    ),
    catalog_anchor=(
        "Catalog item 6 / Stage-1 #4 — as-of-right compliance check "
        "($149 band)."
    ),
)

QUESTION_DUE_DILIGENCE_DEF = Question(
    slug=QUESTION_DUE_DILIGENCE,
    display_name="Zoning due-diligence summary",
    price_cents=19_900,  # $199 CAD
    summary=(
        "A preliminary zoning due-diligence summary for an address: the "
        "zone, permitted and conditional uses, applicable overlays, and "
        "flags for any apparent non-conformity. Anchors a purchase or "
        "financing decision; not a substitute for a full consultant memo."
    ),
    backing_calls=("get_address_profile", "search_bylaw_evidence"),
    required_inputs=(
        InputField(
            name="address",
            label="Property address",
            required=True,
            description="Civic address within the Halifax Regional Centre.",
        ),
    ),
    prompt_template=(
        "Produce a preliminary zoning due-diligence summary for {address}: "
        "the zone, permitted and conditional uses, applicable overlays, and "
        "any apparent non-conformity flags, each grounded in the by-law."
    ),
    catalog_anchor=(
        "Catalog item 3 — zoning due-diligence summary (consultant proxy "
        "$1,500+)."
    ),
)

QUESTION_LEGAL_NONCONFORMING_DEF = Question(
    slug=QUESTION_LEGAL_NONCONFORMING,
    display_name="Legal non-conforming determination",
    price_cents=19_900,  # $199 CAD
    summary=(
        "Is an existing use or structure legally non-conforming (i.e. "
        "lawfully established before the current by-law and protected), or "
        "simply non-compliant? We evaluate the situation against the "
        "current standards and the non-conforming provisions."
    ),
    backing_calls=(
        "evaluate_submission_against_bylaws",
        "search_bylaw_evidence",
    ),
    required_inputs=(
        InputField(
            name="address",
            label="Property address",
            required=True,
            description="Civic address within the Halifax Regional Centre.",
        ),
        InputField(
            name="existing_use_or_structure",
            label="Existing use or structure",
            required=True,
            description=(
                "The use or structure in question, e.g. 'a corner store in "
                "an R-zone' or 'a building 1.2m from the side lot line'."
            ),
        ),
        InputField(
            name="establishment_date",
            label="When it was established",
            required=False,
            description=(
                "Approximate date the use/structure was established, if "
                "known — bears on whether it predates the current by-law."
            ),
        ),
    ),
    prompt_template=(
        "Determine whether {existing_use_or_structure} at {address} is "
        "legally non-conforming under the current by-law. Establishment "
        "date (if known): {establishment_date}. Evaluate it against the "
        "current standards and the non-conforming provisions, and state the "
        "determination with reasoning."
    ),
    catalog_anchor=(
        "Catalog item 8 — legal non-conforming determination (consultant "
        "proxy $800-$2,500)."
    ),
)

QUESTION_VARIANCE_JUSTIFICATION_DEF = Question(
    slug=QUESTION_VARIANCE_JUSTIFICATION,
    display_name="Variance justification package",
    price_cents=29_900,  # $299 CAD
    summary=(
        "A justification package for a requested variance, addressing the "
        "three statutory criteria (the variance is minor, the strict "
        "application creates undue hardship, and the variance is desirable "
        "and consistent with the intent of the by-law and plan)."
    ),
    backing_calls=("evaluate_submission_against_bylaws",),
    required_inputs=(
        InputField(
            name="address",
            label="Property address",
            required=True,
            description="Civic address within the Halifax Regional Centre.",
        ),
        InputField(
            name="requested_variance",
            label="Requested variance",
            required=True,
            description=(
                "The standard being varied and by how much, e.g. 'reduce "
                "the required rear-yard setback from 7.5m to 5.0m'."
            ),
        ),
        InputField(
            name="hardship_rationale",
            label="Hardship rationale",
            required=False,
            description=(
                "Any context on why strict application creates hardship — "
                "lot shape, grade, existing structures, etc."
            ),
        ),
    ),
    prompt_template=(
        "Prepare a variance justification package for {address}. Requested "
        "variance: {requested_variance}. Hardship context: "
        "{hardship_rationale}. Address each of the three statutory criteria "
        "(minor in nature; undue hardship from strict application; "
        "desirable and consistent with the intent of the by-law and plan), "
        "grounded in the applicable provisions."
    ),
    catalog_anchor=(
        "Catalog item 1 — variance justification package (consultant proxy "
        "$800-$5,000)."
    ),
)


# Menu order (cheapest → most expensive, matching the launch-menu listing).
# The question menu renders this order.
QUESTION_ORDER: tuple[str, ...] = (
    QUESTION_PERMITTED_USE,
    QUESTION_DEVELOPMENT_STANDARDS,
    QUESTION_DUE_DILIGENCE,
    QUESTION_LEGAL_NONCONFORMING,
    QUESTION_VARIANCE_JUSTIFICATION,
)

QUESTIONS: dict[str, Question] = {
    q.slug: q
    for q in (
        QUESTION_PERMITTED_USE_DEF,
        QUESTION_DEVELOPMENT_STANDARDS_DEF,
        QUESTION_DUE_DILIGENCE_DEF,
        QUESTION_LEGAL_NONCONFORMING_DEF,
        QUESTION_VARIANCE_JUSTIFICATION_DEF,
    )
}


def question_for(slug: str) -> Question:
    """Return the ``Question`` for a slug.

    Raises ``KeyError`` if the slug is unknown — callers translate that
    to HTTP 400 at the API edge.
    """
    return QUESTIONS[slug]


def all_questions() -> list[Question]:
    """Every catalog question in menu order (cheapest → most expensive)."""
    return [QUESTIONS[slug] for slug in QUESTION_ORDER]


def question_for_stripe_price_id(
    price_id: str, settings: object
) -> Question | None:
    """Reverse-lookup a question from a Stripe Price ID.

    Used by the webhook handler to resolve which question a completed
    checkout paid for. Compares ``price_id`` against the configured
    ``STRIPE_PRICE_QUESTION_<SLUG>`` values on ``settings``.

    Returns ``None`` if the price isn't recognised; callers should treat
    that as a misconfigured question and log loudly rather than silently
    delivering the wrong answer.

    ``settings: object`` (rather than the concrete type) avoids a
    circular import with ``advisor.billing.settings``.
    """
    if not price_id:
        return None
    for question in all_questions():
        configured = getattr(
            settings, question.stripe_price_env_var.lower(), None
        )
        if configured and configured == price_id:
            return question
    return None
