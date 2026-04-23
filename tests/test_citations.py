from layer1.models.enums import FragmentType
from layer1.pipeline.citations import parse_citation_label


def test_parse_numeric_section_label():
    match = parse_citation_label("4.1.1 Accessory Buildings")
    assert match is not None
    assert match.label == "4.1.1"
    assert match.fragment_type == FragmentType.SUBSECTION


def test_parse_part_and_schedule_labels():
    assert parse_citation_label("Part 4 - Parking").label == "Part 4"
    assert parse_citation_label("Schedule B").label == "Schedule B"


def test_parse_clause_label():
    match = parse_citation_label("(a) No person shall park")
    assert match is not None
    assert match.fragment_type == FragmentType.CLAUSE
    assert match.label == "(a)"
