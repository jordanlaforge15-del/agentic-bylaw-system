"""Post-generation hedge injection for high-liability feasibility answers.

ABS-263 (surfaced by the ABS-260 production-readiness sweep, TC-005): the
advisor was handing developers and architects feasibility-grade quantitative
answers — height, FAR, lot coverage, setbacks, parking all stacked together —
without ever telling them to verify with HRM or a planner, or qualifying the
numbers as general / non-legal information. Foundation models hedge naturally
on this kind of question; our system prompt was suppressing it.

The primary fix is a system-prompt instruction (``docs/agent/persona.md``)
telling the model to close feasibility answers with a verify-with-a-planner
note. This module is the deterministic safety net underneath that: after the
tool loop produces the final assistant turn, we check whether it's a
feasibility-grade answer that *forgot* to hedge, and if so append a templated
qualifier. The net effect is that a bare, un-hedged feasibility answer can
never reach the user — regardless of how the live model phrased its turn — and
e2e can assert the behaviour without depending on live-model wording.

Two guards keep this from bloating simple answers (an explicit ABS-263
acceptance criterion — "what's the rear-yard setback for ER-1?" must NOT get
the full hedge dance):

  1. We only act on *feasibility-grade* answers — ones that stack two or more
     distinct quantitative built-form dimensions (height, FAR, coverage,
     parking, setbacks, density, buildable area). A single-dimension lookup
     ("the rear-yard setback in ER-1 is 7.5 m") is one dimension, so it's left
     untouched.
  2. We never double-hedge. If the answer already points the user at HRM / a
     planner / professional review (the same markers the ABS-260 verifier
     scans for), we leave it alone. This also makes ``apply_hedge`` idempotent.
"""
from __future__ import annotations

from advisor.llm.base import ContentBlock, TextBlock

# Phrases that signal the answer already hedges toward professional review.
# Kept in sync with ``detect_hedging`` in ``scripts/verify_test_prompts.py``
# (the ABS-260 rubric) so a hedge we inject is one the verifier will credit,
# and an answer the verifier counts as hedged is one we won't double up on.
HEDGE_MARKERS: tuple[str, ...] = (
    "confirm with",
    "consult",
    "professional",
    "planner",
    "lawyer",
    "hrm",
    "halifax regional municipality",
    "i recommend",
    "i'd recommend",
    "would recommend",
    "verify",
    "before proceeding",
    "may vary",
    "not legal advice",
    "this is general",
    "for a specific",
    "site-specific",
)

# Distinct quantitative built-form dimensions. An answer that references two or
# more of these groups is doing feasibility scoping, not a narrow lookup. Each
# group is a set of lowercase substrings; presence of any one counts the group.
_DIMENSION_GROUPS: dict[str, tuple[str, ...]] = {
    "height": ("height", "storey", "storeys", "stories", "story"),
    "far": ("far", "floor area ratio", "floor-area ratio"),
    "coverage": ("lot coverage", "site coverage", "coverage"),
    "parking": ("parking",),
    "setback": ("setback", "setbacks", "yard"),
    "density": ("density", "dwelling unit", "units per", "unit count"),
    "buildable": ("gross floor area", "gfa", "buildable", "footprint"),
}

# The templated qualifier appended to an un-hedged feasibility answer. Phrased
# to carry several ABS-260 hedging markers ("this is general", "not legal
# advice", "site-specific", "planner", "HRM", "before proceeding") so a single
# injection satisfies the rubric, and led with a distinctive sentence the e2e
# can assert on (or assert the ABSENCE of, for simple answers).
HEDGE_SUFFIX = (
    "\n\n---\n\n"
    "**Before you act on these numbers:** this is general bylaw information, "
    "not legal advice or a site-specific compliance determination. Precinct "
    "boundaries, overlays, and on-the-ground site conditions can change what "
    "actually applies to a given parcel. Confirm the figures for your exact "
    "site with HRM Planning & Development or a qualified planner before "
    "proceeding — especially before you commit budget or design work."
)


def already_hedged(text: str) -> bool:
    """True when ``text`` already points the user at professional review."""
    low = text.lower()
    return any(marker in low for marker in HEDGE_MARKERS)


def _is_feasibility_answer(text: str) -> bool:
    """True when the answer stacks two or more distinct built-form dimensions
    alongside at least one numeric figure — i.e. it's feasibility scoping a
    developer could act on, not a single-fact lookup."""
    if not any(ch.isdigit() for ch in text):
        return False
    low = text.lower()
    hits = 0
    for keywords in _DIMENSION_GROUPS.values():
        if any(kw in low for kw in keywords):
            hits += 1
            if hits >= 2:
                return True
    return False


def should_hedge(text: str) -> bool:
    """Decide whether a feasibility qualifier should be appended to ``text``.

    Returns ``False`` for empty answers, answers that already hedge, and
    narrow single-dimension lookups — the cases the ABS-263 acceptance
    criteria say must stay lean.
    """
    if not text or not text.strip():
        return False
    if already_hedged(text):
        return False
    return _is_feasibility_answer(text)


def apply_hedge(content: list[ContentBlock]) -> list[ContentBlock]:
    """Return ``content`` with a feasibility qualifier appended when warranted.

    Inspects the concatenated text of the assistant turn. When it's a
    feasibility-grade answer that doesn't already hedge, the qualifier is
    appended to the last text block (or added as a new block if the turn has
    no text). When no hedge is needed the original list object is returned
    unchanged, so callers can cheaply detect a no-op with ``is``.
    """
    text = "".join(b.text for b in content if isinstance(b, TextBlock))
    if not should_hedge(text):
        return content

    new_content = list(content)
    for i in range(len(new_content) - 1, -1, -1):
        block = new_content[i]
        if isinstance(block, TextBlock):
            new_content[i] = block.model_copy(
                update={"text": block.text + HEDGE_SUFFIX}
            )
            return new_content
    # No text block to append to (e.g. a tool-only turn that slipped through).
    new_content.append(TextBlock(text=HEDGE_SUFFIX.lstrip()))
    return new_content
