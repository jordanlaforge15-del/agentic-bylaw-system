from layer1.models.enums import FragmentType, ResolutionStatus
from layer1.models.schemas import FragmentData
from layer1.pipeline.crossrefs import detect_cross_references


def frag(text: str, label: str | None = None) -> FragmentData:
    return FragmentData(
        fragment_type=FragmentType.SECTION,
        citation_label=label,
        citation_path=label,
        page_start=1,
        page_end=1,
        text=text,
    )


def test_detects_and_resolves_section_reference():
    refs = detect_cross_references([frag("1.1 target", "1.1"), frag("Subject to section 1.1.")])
    assert len(refs) == 1
    assert refs[0].target_citation_guess == "1.1"
    assert refs[0].resolution_status == ResolutionStatus.RESOLVED


def test_stores_unresolved_schedule_reference():
    refs = detect_cross_references([frag("See Schedule B for details.")])
    assert refs[0].target_citation_guess == "Schedule B"
    assert refs[0].resolution_status == ResolutionStatus.UNRESOLVED
