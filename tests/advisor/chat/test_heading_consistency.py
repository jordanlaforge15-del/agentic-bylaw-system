"""ABS-519: a heading may not assert the opposite of its own section body.

The canonical case is TC-026 (6051 Oakland Road, ER-2, "can I build four
townhouses?"): the body correctly says townhouse use is permitted in ER-3 and
not in ER-2, under a heading reading "Permitted in ER-2 (with conditions)".
"""
from __future__ import annotations

from advisor.chat.heading_consistency import (
    NEGATIVE,
    POSITIVE,
    apply_heading_consistency,
    body_claims,
    find_contradictions,
    heading_claim,
    repair_headings,
)
from advisor.llm.base import TextBlock, ToolUseBlock

# The TC-026 shape, verbatim in structure: right body, wrong heading.
_TC026 = (
    "### 1. Townhouse Dwelling Use — Permitted in ER-2 (with conditions)\n"
    "\n"
    "Table 1B confirms that **townhouse dwelling use is permitted in the ER-3 "
    "zone** (marked with ⑭), but **not in ER-2**.\n"
)

_AGREEING = (
    "### 1. Townhouse Dwelling Use — Not Permitted in ER-2\n"
    "\n"
    "Table 1B confirms that townhouse dwelling use is permitted in the ER-3 "
    "zone, but not in ER-2.\n"
)


# --------------------------------------------------------------------------
# Claim extraction
# --------------------------------------------------------------------------


def test_heading_claim_reads_polarity_and_zone():
    claim = heading_claim("1. Townhouse Dwelling Use — Permitted in ER-2 (with conditions)")
    assert claim is not None
    assert claim.polarity == POSITIVE
    assert claim.zone == "ER-2"


def test_heading_claim_reads_negated_form():
    claim = heading_claim("Townhouse Use — **Not Permitted** in ER-2")
    assert claim is not None
    assert claim.polarity == NEGATIVE
    assert claim.zone == "ER-2"


def test_heading_with_no_permission_word_makes_no_claim():
    assert heading_claim("Built-form standards for ER-2") is None


def test_body_claims_are_clause_local():
    claims = body_claims(
        "Table 1B confirms that **townhouse dwelling use is permitted in the "
        "ER-3 zone** (marked with ⑭), but **not in ER-2**."
    )
    by_zone = {c.zone: c.polarity for c in claims}
    assert by_zone["ER-3"] == POSITIVE
    assert by_zone["ER-2"] == NEGATIVE


def test_body_claims_handle_inherently_negative_words():
    claims = body_claims("Townhouse dwelling use is prohibited in ER-2.")
    assert [(c.zone, c.polarity) for c in claims] == [("ER-2", NEGATIVE)]


# --------------------------------------------------------------------------
# Topic vs. verdict: a permission word qualifying a noun names what the
# section is ABOUT, not what it concludes — unless it is negated.
# --------------------------------------------------------------------------


def test_attributive_heading_makes_no_claim():
    # "Permitted Uses in ER-2" is a section title, not a verdict. Reading it
    # as one would let the guard rewrite it to "Not Permitted Uses in ER-2"
    # over a body that denies one particular use.
    assert heading_claim("Permitted Uses in ER-2") is None
    assert heading_claim("Prohibited Structures in ER-2") is None


def test_attributive_heading_is_not_flagged():
    text = (
        "### Permitted Uses in ER-2\n\n"
        "Townhouse dwelling use is not permitted in ER-2.\n"
    )
    assert find_contradictions(text) == []


def test_attributive_body_sentence_states_no_verdict():
    assert body_claims("Table 1B lists the permitted uses in ER-2.") == []


def test_negated_attributive_is_a_verdict_not_a_topic():
    # "is not a permitted use in ER-2" is the by-law's ordinary denial, and
    # nobody titles a section "Not Permitted Uses" — the negator settles it.
    claims = body_claims("Townhouse dwelling is not a permitted use in ER-2.")
    assert [(c.zone, c.polarity) for c in claims] == [("ER-2", NEGATIVE)]
    assert body_claims("A fourplex is not an allowed use in HR-1.")[0].polarity == NEGATIVE


def test_heading_contradicting_a_negated_attributive_body_is_flagged():
    text = (
        "### Townhouse Use — Permitted in ER-2\n\n"
        "Townhouse dwelling is not a permitted use in ER-2.\n"
    )
    found = find_contradictions(text)
    assert len(found) == 1
    assert found[0].heading_polarity == POSITIVE
    assert found[0].suggested_heading == "Townhouse Use — Not Permitted in ER-2"


# --------------------------------------------------------------------------
# Contradiction detection
# --------------------------------------------------------------------------


def test_tc026_heading_is_flagged():
    found = find_contradictions(_TC026)
    assert len(found) == 1
    assert found[0].zone == "ER-2"
    assert found[0].heading_polarity == POSITIVE
    assert found[0].body_polarity == NEGATIVE
    assert "asserts permission about ER-2" in found[0].describe()


def test_agreeing_heading_is_not_flagged():
    assert find_contradictions(_AGREEING) == []


def test_heading_about_a_different_zone_than_the_body_denies_is_not_flagged():
    text = (
        "### Townhouse Use — Permitted in ER-3\n\n"
        "Townhouse dwelling use is permitted in the ER-3 zone, but not in ER-2.\n"
    )
    assert find_contradictions(text) == []


def test_ambiguous_multi_zone_section_with_unanchored_heading_is_skipped():
    text = (
        "### Townhouse Use — Permitted\n\n"
        "Townhouse dwelling use is permitted in ER-3, but not in ER-2.\n"
    )
    assert find_contradictions(text) == []


def test_unanchored_heading_over_a_single_zone_body_is_flagged():
    text = (
        "### Townhouse Use — Permitted\n\n"
        "Townhouse dwelling use is not permitted in ER-2.\n"
    )
    found = find_contradictions(text)
    assert len(found) == 1
    assert found[0].heading_polarity == POSITIVE


def test_empty_section_body_is_never_flagged():
    assert find_contradictions("### Permitted in ER-2\n") == []


def test_prose_without_headings_is_never_flagged():
    assert find_contradictions(
        "Townhouse dwelling use is not permitted in ER-2."
    ) == []


# --------------------------------------------------------------------------
# Repair
# --------------------------------------------------------------------------


def test_repair_flips_an_over_permissive_heading_and_drops_the_qualifier():
    repaired = repair_headings(_TC026)
    heading = repaired.splitlines()[0]
    assert heading == "### 1. Townhouse Dwelling Use — Not Permitted in ER-2"
    # The body is untouched.
    assert "permitted in the ER-3" in repaired


def test_repair_is_idempotent():
    once = repair_headings(_TC026)
    assert repair_headings(once) == once


def test_repair_is_a_no_op_for_an_agreeing_answer():
    assert repair_headings(_AGREEING) is _AGREEING


def test_repair_neutralises_rather_than_asserting_permission():
    # Heading denies, body asserts. The guard must NOT write "Permitted" —
    # a false positive here would tell a user to build.
    text = (
        "### Townhouse Use — Not Permitted in ER-3\n\n"
        "Townhouse dwelling use is permitted in ER-3.\n"
    )
    repaired = repair_headings(text)
    heading = repaired.splitlines()[0]
    assert heading == "### Townhouse Use — Permission in ER-3"
    assert "Not Permitted" not in heading


def test_neutralising_drops_the_noun_the_permission_word_qualified():
    # "Not a Permitted Use" → "Permission", not "Permission Use".
    text = (
        "### Townhouse — Not a Permitted Use in ER-3\n\n"
        "Townhouse dwelling use is permitted in ER-3.\n"
    )
    assert repair_headings(text).splitlines()[0] == "### Townhouse — Permission in ER-3"


def test_repair_preserves_heading_level_and_trailing_newline():
    text = "#### Accessory Use — Allowed in HR-1\n\nAccessory use is not allowed in HR-1.\n"
    repaired = repair_headings(text)
    assert repaired.startswith("#### Accessory Use — Not Allowed in HR-1\n")
    assert repaired.endswith("\n")


# --------------------------------------------------------------------------
# Content-block wiring
# --------------------------------------------------------------------------


def test_apply_returns_the_same_list_when_nothing_contradicts():
    content = [TextBlock(text=_AGREEING)]
    assert apply_heading_consistency(content) is content


def test_apply_rewrites_only_the_text_block():
    tool = ToolUseBlock(id="t1", name="search_bylaw_evidence", input={})
    content = [TextBlock(text=_TC026), tool]
    out = apply_heading_consistency(content)
    assert out is not content
    assert isinstance(out[0], TextBlock)
    assert out[0].text.splitlines()[0].endswith("Not Permitted in ER-2")
    assert out[1] is tool
