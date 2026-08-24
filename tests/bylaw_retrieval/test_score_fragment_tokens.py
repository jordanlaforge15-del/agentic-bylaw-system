"""ABS-478: zone codes tokenize whole and text tokens match on word boundaries.

``_score_fragment`` used to split "HR-2" into "hr" + "2" and then test each
token with ``token in haystack``. "hr" is a substring of "through" and "2" of
every numeral, so a zone-scoped query banked +8 of pure noise against nearly
any fragment. These tests pin the fix: no points for a lookalike, full points
for a genuine hit.

The scorer only reads ``text`` / ``citation_label`` / ``citation_path`` /
``parse_status`` off the fragment and never touches ``self``, so it is
exercised here against a stub — no database, no session.

ABS-518 narrowed the split without reopening the substring hole. "HR-2" still
tokenizes to a whole ``hr-2`` and still keeps the ``hr`` stem, but the bare
ordinal ``2`` is gone: as a *whole word* it hits "38BI(1)", "Table 1" and
"Subsection (1)" all over the corpus, and at the citation-path rung each of
those is worth +12. The parts of an ordinary compound ("single-family") are
untouched — a by-law may genuinely write them apart.
"""
from __future__ import annotations

from dataclasses import dataclass

from bylaw_retrieval.retrieval.service import RetrievalService, _tokenize


@dataclass(frozen=True)
class _ParseStatus:
    value: str


@dataclass
class _StubFragment:
    text: str
    citation_label: str | None = None
    citation_path: str | None = None
    parse_status: _ParseStatus = _ParseStatus("parsed")


#: What ``_score_fragment`` adds for a fragment whose parse succeeded,
#: independent of any token hit. Subtracting it leaves the token score.
PARSED_BONUS = 1.0


def _score(fragment: _StubFragment, query: str) -> float:
    return RetrievalService._score_fragment(None, fragment, query)


def _token_points(fragment: _StubFragment, query: str) -> float:
    return _score(fragment, query) - PARSED_BONUS


def test_zone_code_survives_as_a_whole_token() -> None:
    # The compound and its alphabetic stem survive; the bare ordinal does not
    # (ABS-518) — see the module docstring.
    assert _tokenize("HR-2 setback") == ["hr-2", "hr", "setback"]


def test_single_letter_stem_is_dropped_with_the_ordinal() -> None:
    """"R-1" leaves nothing addressable behind but the compound itself.

    A one-letter stem is the same coincidence as a bare ordinal: "r" as a whole
    word appears in tables, unit labels and clause markers throughout both
    by-laws, and none of those occurrences is about the R-1 zone. The compound
    ``r-1`` still matches every place the by-law actually writes the code.
    """
    assert _tokenize("R-1 zone") == ["r-1", "zone"]


def test_hyphenated_prose_yields_compound_and_parts() -> None:
    # The parts are kept so a query for "single-family" still matches text
    # that writes it without the hyphen.
    assert _tokenize("single-family dwelling") == [
        "single-family",
        "single",
        "family",
        "dwelling",
    ]


def test_lookalike_fragment_scores_zero_text_token_points() -> None:
    """"hr" must not hit "through"; "2" must not hit "2nd"."""
    fragment = _StubFragment(
        text="No part of a building shall project through the 2nd storey.",
    )
    assert _token_points(fragment, "HR-2 setback") == 0.0


def test_real_zone_code_still_scores_the_token_hit() -> None:
    fragment = _StubFragment(
        text="In the HR-2 zone the minimum front yard is 6 metres.",
    )
    # "hr-2" and "hr" both hit the text as whole words (2 x 4); "setback" does
    # not appear, and neither does the query phrase. The bare "2" no longer
    # tokenizes, so the genuine hit is worth 8 rather than 12 (ABS-518) — the
    # point it lost is the point it used to bank against "the 2nd storey" too.
    assert _token_points(fragment, "HR-2 setback") == 8.0


def test_zone_code_hit_survives_trailing_punctuation() -> None:
    fragment = _StubFragment(text="This section applies to lands zoned HR-2.")
    # +20 whole-query-in-haystack (unchanged behaviour), +8 for "hr-2" and "hr".
    assert _token_points(fragment, "HR-2") == 20.0 + 8.0


def test_word_boundary_applies_to_plain_tokens_too() -> None:
    fragment = _StubFragment(text="A rear yard abuts the through street.")
    # "roughs" would be a substring hit on "through"; as a word it is not
    # there, and neither is the query phrase.
    assert _token_points(fragment, "rough yard") == 4.0


def test_inflected_forms_still_match() -> None:
    """The one useful thing substring matching did is deliberately kept."""
    fragment = _StubFragment(text="Buildings shall not exceed the stated storeys.")
    assert _token_points(fragment, "building storey") == 8.0


def test_citation_path_token_outranks_text_token() -> None:
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_label="Section 198",
        citation_path="Part V > 198",
    )
    # +35 for the query inside the citation path, then +12 for the token
    # hitting the path — the label/text branches are elif, so they add nothing.
    assert _token_points(fragment, "198") == 35.0 + 12.0


def test_exact_citation_path_still_scores_the_phrase_rung() -> None:
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_path="Part V > 198",
    )
    # +100 exact-path — the phrase rungs read the *whole* stored path, because
    # "Part V > 198" is a citation someone would type. Then +12 for the one
    # token that survives the locator strip: "198". "part" and "v" no longer
    # find a haystack (ABS-518).
    assert _token_points(fragment, "Part V > 198") == 100.0 + 12.0


def test_substring_citation_path_still_scores_the_phrase_rung() -> None:
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_path="Part V > 198 > (f)",
    )
    # +35 for the query being a substring of the path, plus the surviving
    # "198" token.
    assert _token_points(fragment, "Part V > 198") == 35.0 + 12.0


def test_locator_segment_alone_earns_no_citation_points() -> None:
    """The word "part" is prose, not an address.

    Before ABS-518 the per-token rung tested every query token against the
    stored path, so a question containing "a" matched ``Schedule A > …`` and
    banked +12 — the citation rung, three times what a section earns for
    stating the standard in its own text — on every one of the ~4,000 Halifax
    Mainland fragments whose path begins that way. That is how an amendment
    stamp came back ahead of the section an HR-1 setback question asked for.
    """
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_path="Schedule A > 31 > 38BI(1)",
    )
    assert _token_points(fragment, "a schedule") == 0.0


def test_locator_strip_keeps_the_addressing_segments() -> None:
    """Only Part / Schedule / Appendix go; the clause address stays."""
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_path="Schedule A > 31 > 38BI(1)",
    )
    # +35 for the query sitting inside the stored path, then the two tokens
    # "38bi" and "1" against the locator-free "31 > 38BI(1)" (2 x 12). The
    # strip removes "Schedule A" and nothing else.
    assert _token_points(fragment, "38BI(1)") == 35.0 + 24.0


def test_unparsed_fragment_still_penalised() -> None:
    fragment = _StubFragment(
        text="In the HR-2 zone the minimum front yard is 6 metres.",
        parse_status=_ParseStatus("failed"),
    )
    # +20 phrase, +8 tokens, -2 for the failed parse.
    assert _score(fragment, "HR-2") == 20.0 + 8.0 - 2.0
