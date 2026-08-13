"""ABS-478: zone codes tokenize whole and text tokens match on word boundaries.

``_score_fragment`` used to split "HR-2" into "hr" + "2" and then test each
token with ``token in haystack``. "hr" is a substring of "through" and "2" of
every numeral, so a zone-scoped query banked +8 of pure noise against nearly
any fragment. These tests pin the fix: no points for a lookalike, full points
for a genuine hit.

The scorer only reads ``text`` / ``citation_label`` / ``citation_path`` /
``parse_status`` off the fragment and never touches ``self``, so it is
exercised here against a stub — no database, no session.
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
    assert _tokenize("HR-2 setback") == ["hr-2", "hr", "2", "setback"]


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
    # "hr-2", "hr" and "2" all hit the text as whole words (3 x 4);
    # "setback" does not appear, and neither does the query phrase.
    assert _token_points(fragment, "HR-2 setback") == 12.0


def test_zone_code_hit_survives_trailing_punctuation() -> None:
    fragment = _StubFragment(text="This section applies to lands zoned HR-2.")
    # +20 whole-query-in-haystack (unchanged behaviour), +12 for the tokens.
    assert _token_points(fragment, "HR-2") == 20.0 + 12.0


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


def test_exact_citation_path_scoring_is_unchanged() -> None:
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_path="Part V > 198",
    )
    # +100 exact-path, plus the per-token hits for "part", "v" and "198"
    # against the same path (3 x 12).
    assert _token_points(fragment, "Part V > 198") == 100.0 + 36.0


def test_substring_citation_path_scoring_is_unchanged() -> None:
    fragment = _StubFragment(
        text="irrelevant prose",
        citation_path="Part V > 198 > (f)",
    )
    # +35 for the query being a substring of the path, plus 3 x 12 tokens.
    assert _token_points(fragment, "Part V > 198") == 35.0 + 36.0


def test_unparsed_fragment_still_penalised() -> None:
    fragment = _StubFragment(
        text="In the HR-2 zone the minimum front yard is 6 metres.",
        parse_status=_ParseStatus("failed"),
    )
    # +20 phrase, +12 tokens, -2 for the failed parse.
    assert _score(fragment, "HR-2") == 20.0 + 12.0 - 2.0
