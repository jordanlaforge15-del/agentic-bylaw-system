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
    assert fragments[1].citation_path is None
    assert fragments[1].parse_status == ParseStatus.UNCERTAIN


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
