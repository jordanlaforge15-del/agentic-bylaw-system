from layer1.models.enums import FragmentType
from layer1.pipeline.citations import parse_citation_label
from layer1.profiles import get_parsing_profile


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

    match = parse_citation_label("(ba) a home occupation")
    assert match is not None
    assert match.fragment_type == FragmentType.CLAUSE
    assert match.label == "(ba)"


def test_parse_compound_bylaw_labels():
    match = parse_citation_label("43I Notwithstanding Sections 37 to 40")
    assert match is not None
    assert match.fragment_type == FragmentType.SECTION
    assert match.label == "43I"

    match = parse_citation_label("13(3)A Parking Amendments")
    assert match is not None
    assert match.fragment_type == FragmentType.SUBSECTION
    assert match.label == "13(3)A"

    match = parse_citation_label("16B(14)(c) Home occupation")
    assert match is not None
    assert match.fragment_type == FragmentType.CLAUSE
    assert match.label == "16B(14)(c)"

    match = parse_citation_label("43 AD Buildings altered or used for R-2A uses")
    assert match is not None
    assert match.fragment_type == FragmentType.SECTION
    assert match.label == "43AD"

    match = parse_citation_label("41 A building in existence may be converted")
    assert match is not None
    assert match.fragment_type == FragmentType.SECTION
    assert match.label == "41"


def test_default_profile_does_not_parse_compound_bylaw_labels():
    assert parse_citation_label("43I Notwithstanding Sections 37 to 40", profile=get_parsing_profile("default")) is None


def test_address_heading_is_not_parsed_as_numeric_section():
    assert parse_citation_label("5515/17/19 and 5523 Inglis Street") is None


def test_measurement_value_is_not_a_citation_label():
    assert parse_citation_label("16M TEMPORARY CONSTRUCTION USES PERMITTED") is not None
    assert parse_citation_label("40 ANGLE") is not None
