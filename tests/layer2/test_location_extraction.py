from layer2.retrieval.location import (
    LocationReference,
    RegexLocationExtractor,
    extract_location_references,
)


def test_extracts_civic_address():
    refs = extract_location_references(
        "What's the maximum building height at 1234 Barrington Street?"
    )
    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "civic_address"
    assert ref.civic_number == "1234"
    assert ref.street.lower().startswith("barrington")
    assert "barrington" in ref.street.lower()
    assert ref.raw_text.startswith("1234 Barrington")


def test_extracts_pid():
    refs = extract_location_references("Look up PID 00012345 please.")
    assert len(refs) == 1
    assert refs[0].kind == "parcel_id"
    assert refs[0].parcel_id == "00012345"


def test_handles_multiple_addresses_in_one_question():
    refs = extract_location_references(
        "Compare 100 Spring Garden Road with 250 Hollis Street."
    )
    civic = [r for r in refs if r.kind == "civic_address"]
    assert len(civic) == 2
    civic_numbers = sorted(r.civic_number for r in civic)
    assert civic_numbers == ["100", "250"]


def test_returns_empty_for_questions_with_no_address():
    refs = extract_location_references(
        "What is the maximum building height in the regional centre?"
    )
    assert refs == []


def test_named_place_not_recognized_by_regex_extractor():
    """Named places ('Halifax Citadel') are LLM territory — the regex
    extractor must not fabricate a civic_address from one. Returns empty
    so callers can route to an LLM extractor for these cases.
    """
    refs = extract_location_references("How tall can buildings be near the Halifax Citadel?")
    # The deterministic extractor returns nothing rather than guessing:
    assert refs == []


def test_intersection_not_recognized_by_regex_extractor():
    refs = extract_location_references(
        "What's the height limit at the corner of Barrington and Spring Garden?"
    )
    assert refs == []


def test_swappable_extractor_protocol():
    """The LocationExtractor protocol allows swapping implementations.
    A mock extractor should plug in without code changes elsewhere.
    """

    class FixedExtractor:
        def extract(self, question_text: str) -> list[LocationReference]:
            return [
                LocationReference(
                    raw_text="halifax citadel",
                    kind="named_place",
                    name="Halifax Citadel",
                    confidence=0.95,
                )
            ]

    refs = extract_location_references("anything", extractor=FixedExtractor())
    assert len(refs) == 1
    assert refs[0].kind == "named_place"
    assert refs[0].name == "Halifax Citadel"


def test_civic_address_pattern_handles_common_suffix_variants():
    cases = [
        "999 Main St",
        "999 Main St.",
        "999 Main Street",
        "999 North End Avenue",
    ]
    extractor = RegexLocationExtractor()
    for case in cases:
        refs = extractor.extract(f"check {case}")
        assert len(refs) == 1, case
        assert refs[0].civic_number == "999", case
