from layer1.models.enums import BlockType, FragmentType, ParseStatus
from layer1.models.schemas import PageBlockData
from layer1.pipeline.hierarchy import reconstruct_hierarchy


def block(text: str, order: int, block_type: BlockType = BlockType.PARAGRAPH) -> PageBlockData:
    return PageBlockData(
        page_number=1,
        block_type=block_type,
        reading_order=order,
        raw_text=text,
        normalized_text=text,
        parser_source="test",
    )


def test_reconstructs_numbered_hierarchy():
    fragments = reconstruct_hierarchy(
        [
            block("Part 1 Administration", 0, BlockType.HEADING),
            block("1.1 Purpose", 1, BlockType.HEADING),
            block("The purpose text.", 2),
            block("(a) A clause.", 3, BlockType.LIST_ITEM),
        ]
    )
    assert [f.citation_label for f in fragments[:3]] == ["Part 1", "1.1", None]
    assert fragments[1].parent_index == 0
    assert fragments[2].parent_index == 1
    assert fragments[3].citation_label == "(a)"


def test_uncertain_fragment_is_preserved():
    fragments = reconstruct_hierarchy([block("General unparented statement.", 0)])
    assert len(fragments) == 1
    assert fragments[0].parse_status == ParseStatus.UNCERTAIN


def test_orphan_clause_does_not_claim_global_citation_path():
    fragments = reconstruct_hierarchy(
        [
            block("(a) First orphan clause.", 0, BlockType.LIST_ITEM),
            block("(a) Second orphan clause.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert len(fragments) == 2
    assert fragments[0].citation_label == "(a)"
    assert fragments[0].citation_path is None
    assert fragments[0].parse_status == ParseStatus.UNCERTAIN
    assert fragments[1].citation_label == "(a)"
    assert fragments[1].citation_path is None
    assert fragments[1].parse_status == ParseStatus.UNCERTAIN

def test_numbered_footnote_is_not_promoted_to_section():
    fragments = reconstruct_hierarchy(
        [
            block("1 space per 500m² GFA 50% Class A/ 50% Class B Minimum 2 Class B spaces", 0, BlockType.FOOTNOTE),
        ]
    )
    assert len(fragments) == 1
    assert fragments[0].fragment_type.value == "footnote"
    assert fragments[0].citation_label is None
    assert fragments[0].citation_path is None


def test_composite_numeric_sections_keep_distinct_citation_paths():
    fragments = reconstruct_hierarchy(
        [
            block("10(1) First rule.", 0, BlockType.PARAGRAPH),
            block("10(2) Second rule.", 1, BlockType.PARAGRAPH),
            block("10(3) Third rule.", 2, BlockType.PARAGRAPH),
        ]
    )
    assert [fragment.citation_label for fragment in fragments] == ["10(1)", "10(2)", "10(3)"]
    assert [fragment.citation_path for fragment in fragments] == ["10(1)", "10(2)", "10(3)"]


def test_numbered_section_sentence_is_not_classified_as_footnote():
    fragments = reconstruct_hierarchy(
        [
            block("1 This By-law enables:", 0, BlockType.PARAGRAPH),
            block("(a) as-of-right development;", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].citation_label == "1"
    assert fragments[0].citation_path == "1"
    assert fragments[1].parent_index == 0


def test_parenthesized_numeric_subsections_are_addressable_under_section():
    fragments = reconstruct_hierarchy(
        [
            block("178 Minimum front setback.", 0, BlockType.PARAGRAPH),
            block("(2) If no setback is specified, the minimum setback is 1.5 metres.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[1].citation_label == "(2)"
    assert fragments[1].citation_path == "178 > (2)"
    assert fragments[1].parent_index == 0


def test_footnote_like_numeric_line_is_not_treated_as_section():
    fragments = reconstruct_hierarchy(
        [
            block(
                "1 space per 500m2 GFA 50% Class A/ 50% Class B Minimum 2 Class B spaces",
                0,
                BlockType.FOOTNOTE,
            )
        ]
    )
    assert len(fragments) == 1
    assert fragments[0].citation_label is None
    assert fragments[0].citation_path is None
    assert fragments[0].fragment_type.value == "footnote"


def test_duplicate_citation_paths_are_downgraded_to_uncertain():
    fragments = reconstruct_hierarchy(
        [
            block("26 Parking", 0, BlockType.HEADING),
            block("(g) First clause under section 26.", 1, BlockType.LIST_ITEM),
            block("(g) Second clause under section 26.", 2, BlockType.LIST_ITEM),
        ]
    )
    duplicate_clauses = [fragment for fragment in fragments if fragment.citation_label == "(g)"]
    assert len(duplicate_clauses) == 2
    assert all(fragment.citation_path is None for fragment in duplicate_clauses)
    assert all(fragment.parse_status == ParseStatus.UNCERTAIN for fragment in duplicate_clauses)
    assert all(fragment.metadata.get("duplicate_citation_path") == "26 > (g)" for fragment in duplicate_clauses)


def test_footer_blocks_are_not_converted_to_fragments():
    fragments = reconstruct_hierarchy(
        [
            block("43I Notwithstanding Sections 37 to 40", 0, BlockType.PARAGRAPH),
            block("Halifax Peninsula Land Use By-law Page 61", 1, BlockType.FOOTER),
        ]
    )
    assert len(fragments) == 1
    assert "Page 61" not in fragments[0].text


def test_splits_definition_block_before_hierarchy():
    fragments = reconstruct_hierarchy(
        [
            block(
                '"Building" means a structure.\n\n"Building Line" means a line on a plan.\n\n"CGVD28" means a datum.',
                0,
                BlockType.PARAGRAPH,
            )
        ]
    )
    assert len(fragments) == 3
    assert all(fragment.fragment_type == FragmentType.PROSE for fragment in fragments)


def test_splits_merged_schedule_block_before_hierarchy():
    fragments = reconstruct_hierarchy(
        [
            block(
                "Schedule B\nResidential uses.\nSchedule C\nCommercial uses.",
                0,
                BlockType.HEADING,
            )
        ]
    )
    assert [fragment.citation_label for fragment in fragments[:2]] == ["Schedule B", "Schedule C"]


def test_splits_merged_compound_section_block_before_hierarchy():
    fragments = reconstruct_hierarchy(
        [
            block(
                "48DB(1)\nCommercial uses.\n48DB(2)\nUses shall be located on the ground floor.",
                0,
                BlockType.HEADING,
            )
        ]
    )
    assert [fragment.citation_label for fragment in fragments[:2]] == ["48DB(1)", "48DB(2)"]


def test_does_not_split_clause_on_wrapped_policy_reference():
    fragments = reconstruct_hierarchy(
        [
            block(
                "(a)\npermit reconstruction of any building in accordance with Policies\n1.7 and 2.5",
                0,
                BlockType.LIST_ITEM,
            )
        ]
    )
    assert len(fragments) == 1
    assert fragments[0].citation_label == "(a)"
    assert "1.7 and 2.5" in fragments[0].text


def test_splits_multiline_toc_block_into_separate_entries():
    fragments = reconstruct_hierarchy(
        [
            block(
                "ZM-21\nWater Access Areas – Northwest Arm ................................................................143\n"
                "ZM-22\nFront Yard Setbacks on Existing Streets ............................................................144\n"
                "AMENDMENTS ................................................................................................146",
                0,
                BlockType.PARAGRAPH,
            )
        ]
    )
    assert len(fragments) == 3
    assert "ZM-21" in fragments[0].text
    assert "ZM-22" in fragments[1].text
    assert fragments[2].text.startswith("AMENDMENTS")


def test_docling_numbered_section_in_list_item_starts_new_section():
    docling_blocks = [
        PageBlockData(
            page_number=1,
            block_type=BlockType.LIST_ITEM,
            reading_order=0,
            raw_text="(f) Prior clause text.",
            normalized_text="(f) Prior clause text.",
            parser_source="docling",
        ),
        PageBlockData(
            page_number=1,
            block_type=BlockType.LIST_ITEM,
            reading_order=1,
            raw_text="34R Where any building is erected, altered or used for a day care facility in an R1A Zone, such building shall comply with the following requirements:",
            normalized_text="34R Where any building is erected, altered or used for a day care facility in an R1A Zone, such building shall comply with the following requirements:",
            parser_source="docling",
        ),
    ]
    fragments = reconstruct_hierarchy(docling_blocks)
    assert fragments[0].citation_label == "(f)"
    assert fragments[1].citation_label == "34R"
    assert fragments[1].parent_index is None


def test_roman_subclauses_attach_under_clause_parent():
    fragments = reconstruct_hierarchy(
        [
            block("10(3) Multiple residential buildings may be considered.", 0, BlockType.LIST_ITEM),
            block("(a) FOR R-2 USES", 1, BlockType.HEADING),
            block("(i) first requirement", 2, BlockType.LIST_ITEM),
            block("(ii) second requirement", 3, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[2].fragment_type == FragmentType.SUBCLAUSE
    assert fragments[2].parent_index == 1
    assert fragments[3].fragment_type == FragmentType.SUBCLAUSE
    assert fragments[3].parent_index == 1


def test_roman_subclauses_attach_under_short_lead_in_clause():
    fragments = reconstruct_hierarchy(
        [
            block("43G(2) Where there is no majority of buildings on the block:", 0, BlockType.LIST_ITEM),
            block("(b) where there is no residential building on either adjacent lot", 1, BlockType.LIST_ITEM),
            block("(i) 10 feet in all zones except in the U-1 zone", 2, BlockType.LIST_ITEM),
            block("(ii) 0 feet in the U-1 zone", 3, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[1].fragment_type == FragmentType.CLAUSE
    assert fragments[2].fragment_type == FragmentType.SUBCLAUSE
    assert fragments[2].parent_index == 1
    assert fragments[3].fragment_type == FragmentType.SUBCLAUSE
    assert fragments[3].parent_index == 1


def test_repeated_roman_subclause_continues_under_same_clause():
    fragments = reconstruct_hierarchy(
        [
            block("10(3) More than one residential building may be considered.", 0, BlockType.LIST_ITEM),
            block("(a) FOR R-2 USES", 1, BlockType.HEADING),
            block("(iv) the distance between each of the buildings shall not be less than 10 feet; and", 2, BlockType.LIST_ITEM),
            block("(v) the minimum lot frontage and lot area shall be 60 feet and 6,000 square feet respectively.", 3, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[2].fragment_type == FragmentType.SUBCLAUSE
    assert fragments[2].parent_index == 1
    assert fragments[3].fragment_type == FragmentType.SUBCLAUSE
    assert fragments[3].parent_index == 1


def test_roman_clause_stays_clause_when_previous_clause_is_not_lead_in():
    fragments = reconstruct_hierarchy(
        [
            block("(h) View Plane 8 means the plane bordered by points C, S and C, T.", 0, BlockType.LIST_ITEM),
            block("(i) View Plane 9 means the plane bordered by points E, U and E, V.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].fragment_type == FragmentType.CLAUSE
    assert fragments[1].fragment_type == FragmentType.CLAUSE
    assert fragments[1].parent_index is None


def test_roman_multi_item_stays_clause_when_definition_list_is_not_a_lead_in():
    fragments = reconstruct_hierarchy(
        [
            block('"Permanent Open Space" means:', 0, BlockType.PARAGRAPH),
            block("(i) publicly owned land;", 1, BlockType.LIST_ITEM),
            block("(ii) cemeteries;", 2, BlockType.LIST_ITEM),
            block("(iii) land permanently covered by water.", 3, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[1].fragment_type == FragmentType.CLAUSE
    assert fragments[1].parent_index == 0
    assert fragments[2].fragment_type == FragmentType.CLAUSE
    assert fragments[2].parent_index == 0
    assert fragments[3].fragment_type == FragmentType.CLAUSE
    assert fragments[3].parent_index == 0


def test_heading_after_clause_becomes_root_without_heading_context():
    fragments = reconstruct_hierarchy(
        [
            block("48(2) Open space requirements", 0, BlockType.LIST_ITEM),
            block("(d) rooftop landscaped open space is permitted if:", 1, BlockType.LIST_ITEM),
            block("OPEN SPACE FOR SPECIAL CARE HOME", 2, BlockType.HEADING),
        ]
    )
    assert fragments[2].fragment_type == FragmentType.HEADING
    assert fragments[2].parent_index is None


def test_definition_like_prose_detaches_from_last_clause():
    fragments = reconstruct_hierarchy(
        [
            block("(j) View Plane 10 means a protected sightline.", 0, BlockType.LIST_ITEM),
            block("Volume means the cubic capacity of a building.", 1, BlockType.PARAGRAPH),
        ]
    )
    assert fragments[1].fragment_type == FragmentType.PROSE
    assert fragments[1].parent_index is None


def test_definition_heading_and_intro_anchor_definition_entries():
    fragments = reconstruct_hierarchy(
        [
            block("DEFINITIONS", 0, BlockType.HEADING),
            block("In this by-law:", 1, BlockType.LIST_ITEM),
            block('"Accessory Building" means a building that is:', 2, BlockType.PARAGRAPH),
            block("(a) not used for human habitation;", 3, BlockType.LIST_ITEM),
            block("(b) located on the same lot as the main building;", 4, BlockType.LIST_ITEM),
            block('"Flankage Yard" means a side yard that abuts a streetline.', 5, BlockType.PARAGRAPH),
        ]
    )
    assert fragments[1].parent_index == 0
    assert fragments[2].parent_index == 1
    assert fragments[2].parse_status == ParseStatus.PARSED
    assert fragments[3].parent_index == 2
    assert fragments[4].parent_index == 2
    assert fragments[5].parent_index == 1
    assert fragments[5].parse_status == ParseStatus.PARSED


def test_definition_like_text_with_space_after_quote_still_attaches():
    fragments = reconstruct_hierarchy(
        [
            block("DEFINITIONS", 0, BlockType.HEADING),
            block("In this by-law:", 1, BlockType.LIST_ITEM),
            block("' Watercourse' means a lake, river, stream, ocean or other natural body of water.", 2, BlockType.PARAGRAPH),
        ]
    )
    assert fragments[2].fragment_type == FragmentType.PROSE
    assert fragments[2].parent_index == 1
    assert fragments[2].parse_status == ParseStatus.PARSED


def test_definition_anchor_survives_intervening_headings_until_numbered_section():
    fragments = reconstruct_hierarchy(
        [
            block("DEFINITIONS", 0, BlockType.HEADING),
            block("In this by-law:", 1, BlockType.LIST_ITEM),
            block("LAND USE BY-LAW - PENINSULA AREA", 2, BlockType.HEADING),
            block('"Rear Yard" shall mean a yard extending across the full width of the lot.', 3, BlockType.PARAGRAPH),
            block("2(1) This by-law shall be administered by the Development Officer.", 4, BlockType.LIST_ITEM),
            block('"Setback" means the setting back of the exterior walls of a building.', 5, BlockType.PARAGRAPH),
        ]
    )
    assert fragments[3].parent_index == 1
    assert fragments[3].parse_status == ParseStatus.PARSED
    assert fragments[4].citation_label == "2(1)"
    assert fragments[5].parent_index is None


def test_definition_reference_detaches_from_last_clause():
    fragments = reconstruct_hierarchy(
        [
            block("(f) Psychologist.", 0, BlockType.LIST_ITEM),
            block('"Property" - see "Lot....."', 1, BlockType.PARAGRAPH),
        ]
    )
    assert fragments[1].fragment_type == FragmentType.PROSE
    assert fragments[1].parent_index is None


def test_deleted_schedule_entry_is_treated_as_definition_like_list_item():
    fragments = reconstruct_hierarchy(
        [
            block("(f) Prior clause.", 0, BlockType.LIST_ITEM),
            block('"Schedule F" (Deleted - RC-Jun 16/09;E-Oct 24/09)', 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[1].fragment_type == FragmentType.LIST_ITEM
    assert fragments[1].parent_index is None


def test_heading_after_subsection_is_root_when_no_heading_context():
    fragments = reconstruct_hierarchy(
        [
            block("2(1) Administered by the Development Officer.", 0, BlockType.LIST_ITEM),
            block("GENERAL PROHIBITION", 1, BlockType.HEADING),
            block("2(2) No person shall undertake development without a permit.", 2, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[1].fragment_type == FragmentType.HEADING
    assert fragments[1].parent_index is None


def test_requirement_value_does_not_become_numeric_section():
    fragments = reconstruct_hierarchy(
        [
            PageBlockData(
                page_number=1,
                block_type=BlockType.LIST_ITEM,
                reading_order=0,
                raw_text="28 Buildings shall comply with the following requirements:",
                normalized_text="28 Buildings shall comply with the following requirements:",
                parser_source="docling",
            ),
            PageBlockData(
                page_number=1,
                block_type=BlockType.PARAGRAPH,
                reading_order=1,
                raw_text="Lot frontage minimum",
                normalized_text="Lot frontage minimum",
                parser_source="docling",
            ),
            PageBlockData(
                page_number=1,
                block_type=BlockType.HEADING,
                reading_order=2,
                raw_text="40 ft.",
                normalized_text="40 ft.",
                parser_source="docling",
            ),
            PageBlockData(
                page_number=1,
                block_type=BlockType.PARAGRAPH,
                reading_order=3,
                raw_text="Lot area minimum",
                normalized_text="Lot area minimum",
                parser_source="docling",
            ),
            PageBlockData(
                page_number=1,
                block_type=BlockType.HEADING,
                reading_order=4,
                raw_text="4,000 sq.ft.",
                normalized_text="4,000 sq.ft.",
                parser_source="docling",
            ),
        ]
    )
    assert fragments[0].citation_label == "28"
    assert fragments[2].fragment_type == FragmentType.PROSE
    assert fragments[2].parent_index == 1
    assert fragments[3].fragment_type == FragmentType.PROSE
    assert fragments[3].parent_index == 0
    assert fragments[4].fragment_type == FragmentType.PROSE
    assert fragments[4].parent_index == 3


def test_clause_after_heading_uses_heading_as_context_parent():
    fragments = reconstruct_hierarchy(
        [
            block("5515/17/19 and 5523 Inglis Street", 0, BlockType.HEADING),
            block("(o) permit a multiple unit residential building.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].fragment_type == FragmentType.HEADING
    assert fragments[1].fragment_type == FragmentType.CLAUSE
    assert fragments[1].parent_index == 0


def test_numbered_provision_after_heading_uses_heading_as_parent():
    fragments = reconstruct_hierarchy(
        [
            block("GENERAL PROVISIONS", 0, BlockType.HEADING),
            block("2(1) This by-law shall be administered by the Development Officer.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].fragment_type == FragmentType.HEADING
    assert fragments[1].fragment_type == FragmentType.SUBSECTION
    assert fragments[1].parent_index == 0


def test_heading_context_disambiguates_duplicate_clause_labels():
    fragments = reconstruct_hierarchy(
        [
            block("94(1) Council may permit the following:", 0, BlockType.LIST_ITEM),
            block("5515/17/19 and 5523 Inglis Street", 1, BlockType.HEADING),
            block("(p) deleted", 2, BlockType.LIST_ITEM),
            block("Cathedral Church of All Saints", 3, BlockType.HEADING),
            block("(p) permit a mixed use building", 4, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[2].parent_index == 1
    assert fragments[4].parent_index == 3
    assert fragments[2].citation_path == "94(1) > [5515/17/19 and 5523 Inglis Street] > (p)"
    assert fragments[4].citation_path == "94(1) > [Cathedral Church of All Saints] > (p)"


def test_heading_context_persists_across_multiple_low_level_items():
    fragments = reconstruct_hierarchy(
        [
            block("94(1) Council may permit the following:", 0, BlockType.LIST_ITEM),
            block("5515/17/19 and 5523 Inglis Street", 1, BlockType.HEADING),
            block("(o) permit a multiple unit residential building.", 2, BlockType.LIST_ITEM),
            block("(p) deleted", 3, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[2].parent_index == 1
    assert fragments[3].parent_index == 1
    assert fragments[3].citation_path == "94(1) > [5515/17/19 and 5523 Inglis Street] > (p)"


def test_angle_heading_is_not_treated_as_numeric_section():
    fragments = reconstruct_hierarchy(
        [
            block("40 ANGLE", 0, BlockType.HEADING),
            block("(c) The distance between external walls shall be not less than 50 feet.", 1, BlockType.LIST_ITEM),
        ]
    )
    assert fragments[0].fragment_type == FragmentType.HEADING
    assert fragments[1].parent_index == 0


def test_split_compound_section_label_parses_with_suffix():
    fragments = reconstruct_hierarchy(
        [
            block("43 AD Buildings altered or used for R-2A uses in the R-2A zone shall comply with the following requirements:", 0, BlockType.HEADING),
        ]
    )
    assert fragments[0].fragment_type == FragmentType.SECTION
    assert fragments[0].citation_label == "43AD"
    assert fragments[0].citation_path == "43AD"


def test_article_a_after_numeric_section_is_not_joined_into_suffix():
    fragments = reconstruct_hierarchy(
        [
            PageBlockData(
                page_number=1,
                block_type=BlockType.LIST_ITEM,
                reading_order=0,
                raw_text="41 A building in existence on or before the 11th of May, 1950 may be converted.",
                normalized_text="41 A building in existence on or before the 11th of May, 1950 may be converted.",
                parser_source="docling",
            ),
        ]
    )
    assert fragments[0].fragment_type == FragmentType.SECTION
    assert fragments[0].citation_label == "41"
