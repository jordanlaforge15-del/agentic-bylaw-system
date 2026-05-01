from layer1.models.enums import ResolutionStatus
from layer1.models.schemas import FragmentData
from layer1.pipeline.crossrefs import detect_cross_references


def fragment(text: str, label: str | None = None) -> FragmentData:
    return FragmentData(
        fragment_type="prose",
        citation_label=label,
        citation_path=label,
        page_start=1,
        page_end=1,
        text=text,
        parse_status="parsed",
    )


def test_detects_section_range_cross_reference():
    refs = detect_cross_references(
        [
            fragment("44 rules", label="44"),
            fragment("The regulations contained in Sections 44 to 47 inclusive shall apply."),
        ]
    )
    assert len(refs) == 1
    assert refs[0].raw_reference_text == "Sections 44 to 47 inclusive"
    assert refs[0].target_citation_guess == "44 to 47"
    assert refs[0].target_fragment_index == 0
    assert refs[0].resolution_status == ResolutionStatus.RESOLVED
