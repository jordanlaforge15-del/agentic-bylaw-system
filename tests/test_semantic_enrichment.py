from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from layer1.db.base import (
    Document,
    SemanticEntity,
    SemanticFact,
    SemanticProvenance,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableSemanticProfile,
)
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics, validate_document_semantics
from layer1.semantic.extractors import extract_zones
from layer2.db.init_db import create_all
from layer2.retrieval.planner import normalize_zone_code


@pytest.fixture()
def semantic_db(tmp_path: Path) -> dict:
    db_url = f"sqlite:///{tmp_path / 'semantic.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="regional.pdf",
            file_hash="semantic",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table = SourceTable(
            document_id=document.id,
            caption="Table 1A: Permitted uses by zone (DD, DH, CEN-2, CEN-1, COR, HR-2, and HR-1)",
            page_start=45,
            page_end=47,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()
        _add_table_rows(
            session,
            table.id,
            [
                ["Residential", "DD", "DH", "CEN-2", "CEN-1", "COR", "HR-2", "HR-1"],
                ["Restaurant use", "●", "●", "●", "●", "●", "③", "② ③"],
                ["Cluster housing use", "", "", "", "", "㉑", "㉑", "㉑"],
                ["Blank example use", "", "", "", "", "", "", ""],
            ],
        )
        dimensional = SourceTable(
            document_id=document.id,
            caption="Table 2: Dimensional standards by zone",
            page_start=60,
            page_end=60,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(dimensional)
        session.flush()
        _add_table_rows(
            session,
            dimensional.id,
            [
                ["Zone", "Minimum Lot Area", "Building Height"],
                ["R1", "400 m2", "11 metres"],
            ],
        )
        for text in [
            "② Use is permitted in HR-1 only when located in a local commercial area.",
            "③ Use is permitted subject to commercial use conditions.",
            "㉑ Use is permitted subject to cluster housing conditions.",
        ]:
            session.add(
                SourceFragment(
                    document_id=document.id,
                    fragment_type=FragmentType.FOOTNOTE,
                    page_start=47,
                    page_end=47,
                    text=text,
                    parse_status=ParseStatus.PARSED,
                    source_block_ids_json=[],
                    metadata_json={},
                )
            )
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}


def test_semantic_enrichment_profiles_tables_and_emits_source_grounded_facts(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        report = enrich_document_semantics(session, document_id=semantic_db["document_id"])
        assert report.table_profiles == 2
        assert report.axis_bindings >= 10
        assert report.facts >= 10

        permission_profile = (
            session.query(TableSemanticProfile)
            .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
            .filter(SourceTable.caption.ilike("%Permitted uses by zone%"))
            .one()
        )
        assert permission_profile.profile_type == "permission_matrix"
        assert permission_profile.row_axis_type == "use"
        assert permission_profile.column_axis_type == "zone"

        restaurant = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="use", canonical_name="restaurant use")
            .one()
        )
        hr1 = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="zone", canonical_name="HR-1")
            .one()
        )
        fact = (
            session.query(SemanticFact)
            .filter_by(
                document_id=semantic_db["document_id"],
                relation_type="permission",
                primary_subject_entity_id=restaurant.id,
                primary_scope_entity_id=hr1.id,
            )
            .one()
        )
        assert fact.normalized_value_json["permission"] == "conditional"
        assert fact.normalized_value_json["markers"] == ["②", "③"]
        assert (
            session.query(SemanticProvenance)
            .filter_by(object_type="semantic_fact", object_id=fact.id, source_type="source_table_cell")
            .count()
            == 1
        )


def test_semantic_enrichment_does_not_emit_negative_facts_for_blank_cells(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        blank_use = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="use", canonical_name="blank example use")
            .one()
        )
        assert (
            session.query(SemanticFact)
            .filter_by(
                document_id=semantic_db["document_id"],
                relation_type="permission",
                primary_subject_entity_id=blank_use.id,
            )
            .count()
            == 0
        )


def test_semantic_enrichment_is_rerunnable_and_validates(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        first = enrich_document_semantics(session, document_id=semantic_db["document_id"])
        second = enrich_document_semantics(session, document_id=semantic_db["document_id"])
        assert first.entities == second.entities
        assert first.facts == second.facts
        validation = validate_document_semantics(session, document_id=semantic_db["document_id"])
        assert validation["ok"] is True


def test_semantic_enrichment_emits_dimensional_standard_fact(semantic_db):
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=semantic_db["document_id"])
        r1 = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="zone", canonical_name="R-1")
            .one()
        )
        lot_area = (
            session.query(SemanticEntity)
            .filter_by(document_id=semantic_db["document_id"], entity_type="standard", canonical_name="minimum lot area")
            .one()
        )
        fact = (
            session.query(SemanticFact)
            .filter_by(
                document_id=semantic_db["document_id"],
                relation_type="dimensional_standard",
                primary_subject_entity_id=lot_area.id,
                primary_scope_entity_id=r1.id,
            )
            .one()
        )
        assert fact.value_text == "400 m2"


def test_zone_normalization_corrects_leading_ocr_character():
    assert extract_zones("What uses are permitted in lHR-1?") == ["HR-1"]
    assert normalize_zone_code("lHR-1") == "HR-1"


def test_zone_extraction_does_not_treat_square_metres_as_zone():
    assert extract_zones("Minimum 1 parking space per 100 m2 of floor area") == []


# --------------------------------------------------------------------- ABS-104
# Mainland Halifax LUB has amendment markers like "(RC-May 9/24)" and
# schedule labels "Schedule ZM-1" throughout. The zone regex matched
# substrings of these (MAY-9, ZM-1, etc.) and emitted them as zone
# entities. Add universal exclusions.

def test_abs104_amendment_month_markers_excluded():
    # Real Mainland-style amendment marker context.
    text = "Single Family Dwelling permitted (RC-May 9/24; E-Jun 13/24)."
    zones = extract_zones(text)
    assert "MAY-9" not in zones
    assert "JUN-13" not in zones
    assert "MAY" not in zones


def test_abs104_schedule_zm_labels_excluded():
    text = "see Schedule ZM-1 and Schedule ZM-2 for boundary delineation"
    zones = extract_zones(text)
    assert "ZM-1" not in zones
    assert "ZM-2" not in zones


def test_abs104_real_zones_still_extracted():
    """The exclusions must not collateral-damage actual zone codes."""
    text = "R-1 zone permits ER-1 and CEN-2 uses; CDD-1 not permitted."
    zones = extract_zones(text)
    assert "R-1" in zones
    assert "ER-1" in zones
    assert "CEN-2" in zones
    assert "CDD-1" in zones


# --------------------------------------------------------------------- ABS-103
# Mainland Halifax LUB's definitions use a leading-quote convention that the
# original regex (`^\s*[A-Z]...means`) rejected. Strip leading quote, trailing
# quote-before-means, and interposing qualifier clauses before matching.

def test_abs103_normalize_definition_text_strips_leading_quote_and_space():
    from layer1.semantic.enrichment import _normalize_definition_text
    assert _normalize_definition_text(
        "' ACCESSORY HEN USE ' means the keeping of hens"
    ) == "ACCESSORY HEN USE  means the keeping of hens"


def test_abs103_normalize_definition_text_strips_hereinafter_clause():
    from layer1.semantic.enrichment import _normalize_definition_text
    src = (
        "'Construction and demolition materials disposal site' "
        "hereinafter referred to as a C&D Disposal Site, means land"
    )
    out = _normalize_definition_text(src)
    assert "hereinafter" not in out
    assert "C&D Disposal Site" not in out
    assert "means land" in out
    assert out.startswith("Construction")


def test_abs103_normalize_definition_text_strips_when_applied_clause():
    from layer1.semantic.enrichment import _normalize_definition_text
    out = _normalize_definition_text(
        '"Height" when applied to a building, means the vertical distance'
    )
    assert "when applied to" not in out
    assert out.startswith("Height")
    assert " means " in out


def test_abs103_normalize_definition_text_preserves_clean_rc_format():
    """RC's already-clean Term means... format should pass through unchanged."""
    from layer1.semantic.enrichment import _normalize_definition_text
    src = "Accessory Parking Lot means a parking lot, not contained within"
    assert _normalize_definition_text(src) == src


def test_abs103_normalize_definition_text_handles_smart_quotes():
    """Some PDFs use smart quotes (curly) instead of straight ones."""
    from layer1.semantic.enrichment import _normalize_definition_text
    out = _normalize_definition_text("‘HEN’ means adult female chicken")
    assert out.startswith("HEN")
    assert " means adult female chicken" in out


# --------------------------------------------------------------------- ABS-105
# Section-number row labels — bylaws that index permission/parking tables
# by section number (Mainland LUB: "11(1)", "5A", "28AO(1)", "62EB") instead
# of use-class name should still trigger table extraction.

def test_abs105_looks_like_section_label_recognizes_common_shapes():
    from layer1.semantic.extractors import looks_like_section_label
    # Plain section + numeric subsection.
    assert looks_like_section_label("11(1)")
    assert looks_like_section_label("5A")
    assert looks_like_section_label("28AO(1)")
    assert looks_like_section_label("62EB")
    assert looks_like_section_label("3")
    assert looks_like_section_label("13AA")
    # With clause-letter suffix.
    assert looks_like_section_label("11(1)(a)")
    # With surrounding whitespace.
    assert looks_like_section_label("  11(1)  ")


def test_abs105_looks_like_section_label_rejects_use_names():
    from layer1.semantic.extractors import looks_like_section_label
    assert not looks_like_section_label("Accessory Use")
    assert not looks_like_section_label("Single Family Dwelling")
    assert not looks_like_section_label("")
    assert not looks_like_section_label("Schedule A")
    # A zone code looks similar (R-1) but has a hyphen — shouldn't match.
    assert not looks_like_section_label("R-1")
    # Section refs with the literal word "Section" don't match — those go
    # through extract_section_refs upstream.
    assert not looks_like_section_label("Section 11(1)")


# --------------------------------------------------------------------- ABS-106
# Prose-only conditions — bylaws (e.g. Halifax Mainland LUB) that don't use
# circled-number / [N] markers still express conditions in plain prose.

def _stub_fragment(text: str, citation: str = "Section 7"):
    """Lightweight stand-in for a SourceFragment for unit-testing the
    prose-condition detector (which doesn't need a real ORM object)."""
    from types import SimpleNamespace
    return SimpleNamespace(text=text, citation_path=citation, id=999)


def test_abs106_prose_condition_subject_to_with_permission_lang():
    from layer1.semantic.enrichment import _detect_prose_condition_markers
    frag = _stub_fragment(
        "A retail use is permitted subject to the approval of the "
        "Development Officer pursuant to Section 11(1).",
        citation="Section 11(1)",
    )
    markers = _detect_prose_condition_markers(frag)
    assert markers == ["prose:Section 11(1)"]


def test_abs106_prose_condition_provided_that_with_prohibition():
    from layer1.semantic.enrichment import _detect_prose_condition_markers
    frag = _stub_fragment(
        "Adult entertainment uses shall not be permitted provided that "
        "they are within 200 metres of a school.",
        citation="Section 28AO(1)",
    )
    markers = _detect_prose_condition_markers(frag)
    assert markers == ["prose:Section 28AO(1)"]


def test_abs106_prose_condition_notwithstanding_with_required():
    from layer1.semantic.enrichment import _detect_prose_condition_markers
    frag = _stub_fragment(
        "Notwithstanding Section 9, a development permit is required "
        "for any expansion of an existing non-conforming use.",
        citation="Section 9(2)",
    )
    markers = _detect_prose_condition_markers(frag)
    assert markers == ["prose:Section 9(2)"]


def test_abs106_prose_condition_rejects_fragments_without_permission_lang():
    """'Subject to' alone (without permission/restriction wording) is too
    weak a signal to fire — would over-flag definitional fragments."""
    from layer1.semantic.enrichment import _detect_prose_condition_markers
    frag = _stub_fragment(
        "Subject to the policy direction outlined in Section 4."
    )
    assert _detect_prose_condition_markers(frag) == []


def test_abs106_prose_condition_rejects_fragments_without_discourse_marker():
    """Plain permission language without a conditional marker shouldn't
    fire — those are vanilla permission statements, not conditions."""
    from layer1.semantic.enrichment import _detect_prose_condition_markers
    frag = _stub_fragment(
        "A single family dwelling is permitted in the R-1 zone."
    )
    assert _detect_prose_condition_markers(frag) == []


def test_abs106_prose_condition_rejects_short_fragments():
    """Length filter prevents matching against fragmentary text (titles, etc.)."""
    from layer1.semantic.enrichment import _detect_prose_condition_markers
    frag = _stub_fragment("Subject to permitted.")
    assert _detect_prose_condition_markers(frag) == []


# --------------------------------------------------------------------- ABS-283
# Mainland LUB permitted-use extraction (not symbol-dot) + classifier
# false-positive fix. The Mainland LUB does NOT use the symbol-font ●
# convention (0 U+F098 cells); it lists permitted uses as prose under each
# zone's section. The structural classifier also false-positived two Mainland
# section/amendment-history tables as permission_matrix.


def _classify_rows(caption: str, rows: list[list[str]], conventions=None):
    """Run ``_classify_table`` on lightweight in-memory cells (no DB)."""
    from layer1.semantic.enrichment import _classify_table, _rows_by_index

    cells = []
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            cells.append(
                SourceTableCell(
                    table_id=1,
                    row_index=row_index,
                    col_index=col_index,
                    text=text,
                    metadata_json={},
                )
            )
    table = SourceTable(document_id=1, caption=caption, metadata_json={})
    return _classify_table(table, _rows_by_index(cells), conventions)


def test_abs283_amendment_history_table_not_classified_permission_matrix():
    """AC3: an amendment/section-history table — section-number rows plus
    amendment-date and council-case columns ("Sep 1/20", "Case 21162") — must
    NOT classify as permission_matrix."""
    rows = [
        ["Section", "Sep 1/20", "Nov 7/20", "Case 21162"],
        ["14QB(2)", "", "x", ""],
        ["62(1)", "x", "", ""],
        ["28AO(1)", "", "", "x"],
    ]
    profile_type, *_ = _classify_rows(caption="", rows=rows)
    assert profile_type != "permission_matrix"


def test_abs283_section_table_with_single_stray_zone_not_permission_matrix():
    """AC1: a section-indexed table with section-number rows but only ONE stray
    zone-bearing column (the false-positive shape) must not classify as a
    permission matrix — a real zone×section grid carries several zone columns."""
    rows = [
        # Only one column ("DD") resolves to a zone; the rest are amendment meta.
        ["Section", "DD", "Sep 1/20", "Case 30551"],
        ["62(1)", "x", "", ""],
        ["62EA(1)", "", "x", ""],
        ["28AO(1)", "x", "", ""],
    ]
    profile_type, *_ = _classify_rows(caption="", rows=rows)
    assert profile_type != "permission_matrix"


def test_abs283_genuine_section_indexed_zone_grid_still_classifies():
    """The tightened heuristic must not collateral-damage a real section-indexed
    permission grid — multiple zone columns, section-number rows, no amendment
    markers — which still classifies as permission_matrix."""
    rows = [
        ["Section", "DD", "DH", "CEN-2", "HR-1"],
        ["11(1)", "●", "●", "", "③"],
        ["28AO(1)", "●", "", "●", "●"],
        ["62EA(1)", "", "●", "●", "●"],
    ]
    profile_type, *_ = _classify_rows(caption="", rows=rows)
    assert profile_type == "permission_matrix"


# --------------------------------------------------------------------- ABS-284
# Profile-driven enrichment classification. The permission-encoding convention
# now rides on the active profile/overlay instead of being hardcoded: a
# ``symbol_matrix`` bylaw detects use×zone / section×zone symbol matrices, while
# a ``section_indexed`` bylaw (permissions in section prose) never classifies a
# table as a permission_matrix.


def _rc_shaped_rows() -> list[list[str]]:
    """A Regional-Centre-shaped use×zone symbol matrix (● permitted markers)."""
    return [
        ["Use", "DD", "DH", "CEN-2", "HR-1"],
        ["Restaurant use", "●", "●", "", "③"],
        ["Cluster housing use", "", "●", "●", "●"],
        ["Office use", "●", "", "●", "●"],
    ]


def test_abs284_symbol_matrix_profile_classifies_and_recovers_markers():
    """AC1: under a ``symbol_matrix`` profile, an RC-shaped fixture classifies as
    permission_matrix and its ● markers are recovered as ``permitted``."""
    from layer1.semantic.conventions import SYMBOL_MATRIX, EnrichmentConventions
    from layer1.semantic.permission_markers import (
        annotate_value_cells,
        classify_permission_marker,
    )

    from types import SimpleNamespace

    conventions = EnrichmentConventions(permission_encoding=SYMBOL_MATRIX)
    profile_type, row_axis, col_axis, *_ = _classify_rows(
        caption="", rows=_rc_shaped_rows(), conventions=conventions
    )
    assert profile_type == "permission_matrix"
    assert (row_axis, col_axis) == ("use", "zone")
    # ● recovers as permitted under the symbol-matrix glyph set.
    assert classify_permission_marker("●", conventions) == {
        "permission_marker": "permitted"
    }
    cells = [
        SimpleNamespace(row_index=0, col_index=0, text="Use", metadata_json={}),
        SimpleNamespace(row_index=0, col_index=1, text="DD", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=0, text="Restaurant use", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=1, text="●", metadata_json={}),
    ]
    assert annotate_value_cells(cells, conventions=conventions) == 1
    assert cells[3].metadata_json == {"permission_marker": "permitted"}


def test_abs284_section_indexed_profile_does_not_classify_permission_matrix():
    """AC1: under a ``section_indexed`` profile, a section×zone grid that WOULD
    classify as permission_matrix under the default symbol-matrix encoding does
    NOT — that bylaw's permissions live in section prose, so the table is not a
    permission matrix."""
    from layer1.semantic.conventions import SECTION_INDEXED, EnrichmentConventions

    rows = [
        ["Section", "DD", "DH", "CEN-2", "HR-1"],
        ["11(1)", "●", "●", "", "③"],
        ["28AO(1)", "●", "", "●", "●"],
        ["62EA(1)", "", "●", "●", "●"],
    ]
    # Sanity: this exact grid classifies as a permission matrix under the default
    # (symbol_matrix) encoding — that's the behavior section_indexed overrides.
    default_type, *_ = _classify_rows(caption="", rows=rows)
    assert default_type == "permission_matrix"

    section_type, *_ = _classify_rows(
        caption="",
        rows=rows,
        conventions=EnrichmentConventions(permission_encoding=SECTION_INDEXED),
    )
    assert section_type != "permission_matrix"


def test_abs284_amendment_table_not_permission_matrix_under_either_encoding():
    """AC1: an amendment/section-history fixture never classifies as a permission
    matrix — under the symbol-matrix default (via the amendment disqualifier) or
    under section_indexed (no symbol-matrix detection at all)."""
    from layer1.semantic.conventions import SECTION_INDEXED, EnrichmentConventions

    rows = [
        ["Section", "DD", "Sep 1/20", "Case 21162"],
        ["14QB(2)", "●", "", ""],
        ["62(1)", "", "x", ""],
        ["28AO(1)", "", "", "x"],
    ]
    default_type, *_ = _classify_rows(caption="", rows=rows)
    assert default_type != "permission_matrix"
    section_type, *_ = _classify_rows(
        caption="",
        rows=rows,
        conventions=EnrichmentConventions(permission_encoding=SECTION_INDEXED),
    )
    assert section_type != "permission_matrix"


def test_abs284_no_profile_path_reproduces_default_classification():
    """AC4: passing no conventions reproduces the historical (default-encoding)
    classification exactly, across the matrix shapes the corpus exercises."""
    from layer1.semantic.conventions import DEFAULT_CONVENTIONS

    fixtures = [
        ("", _rc_shaped_rows()),
        ("Table 1A: Permitted uses by zone", _rc_shaped_rows()),
        ("", [["Zone", "Minimum Lot Area", "Building Height"], ["R1", "400 m2", "11 metres"]]),
        ("", [["Section", "DD", "Sep 1/20", "Case 21162"], ["62(1)", "x", "", ""]]),
    ]
    for caption, rows in fixtures:
        assert _classify_rows(caption, rows) == _classify_rows(
            caption, rows, conventions=DEFAULT_CONVENTIONS
        )


def test_abs283_looks_like_amendment_cell_recognizes_dates_and_cases():
    from layer1.semantic.enrichment import _looks_like_amendment_cell

    assert _looks_like_amendment_cell("Sep 1/20")
    assert _looks_like_amendment_cell("Nov 7/20")
    assert _looks_like_amendment_cell("Case 21162")
    assert _looks_like_amendment_cell("Case No. 30551")
    # Not amendment markers.
    assert not _looks_like_amendment_cell("Restaurant use")
    assert not _looks_like_amendment_cell("DD")
    assert not _looks_like_amendment_cell("●")


def test_abs283_mainland_intro_regex_and_use_list_parsing():
    from layer1.semantic.enrichment import (
        _MAINLAND_PERMITTED_INTRO_RE,
        _parse_mainland_use_list,
    )

    text = (
        "62EA(1) The following uses shall be permitted in any ICH Zone:\n"
        "(1) Single Unit Dwellings\n"
        "(2) Open Space Uses\n"
        "62EA(2) No person shall in any ICH Zone carry out any development "
        "for any purpose other than the uses set out in subsection (1)."
    )
    match = _MAINLAND_PERMITTED_INTRO_RE.search(text)
    assert match is not None
    assert match.group(1) == "ICH"
    uses = _parse_mainland_use_list(text[match.end():])
    assert "Single Unit Dwellings" in uses
    assert "Open Space Uses" in uses
    # The trailing exhaustiveness clause must not leak into the use list.
    assert all("No person" not in use for use in uses)


@pytest.fixture()
def mainland_db(tmp_path: Path) -> dict:
    """A Mainland-shaped doc: prose permitted-use section + a section/amendment
    table that the classifier previously false-positived as permission_matrix.
    No U+F098 cells anywhere (the whole point of the Mainland convention)."""
    db_url = f"sqlite:///{tmp_path / 'mainland.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Halifax Mainland Land Use By-law",
            source_path="mainland.pdf",
            file_hash="abs283-mainland",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        # Prose permitted-use section (the Mainland convention).
        session.add(
            SourceFragment(
                document_id=document.id,
                fragment_type=FragmentType.PROSE,
                citation_path="Section 62EA(1)",
                citation_label="62EA(1)",
                page_start=133,
                page_end=133,
                text=(
                    "62EA(1) The following uses shall be permitted in any ICH "
                    "Zone:\n(1) Single Unit Dwellings\n(2) Open Space Uses\n"
                    "62EA(2) No person shall in any ICH Zone carry out any "
                    "development for any purpose other than one or more of the "
                    "uses set out in subsection (1)."
                ),
                parse_status=ParseStatus.PARSED,
                source_block_ids_json=[],
                metadata_json={},
            )
        )
        # Amendment/section-history table — the false-positive shape.
        amendment = SourceTable(
            document_id=document.id,
            caption="",
            page_start=199,
            page_end=199,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(amendment)
        session.flush()
        _add_table_rows(
            session,
            amendment.id,
            [
                ["Section", "DD", "Sep 1/20", "Case 21162"],
                ["14QB(2)", "x", "", ""],
                ["62(1)", "", "x", ""],
                ["28AO(1)", "", "", "x"],
            ],
        )
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}


def test_abs283_mainland_enrichment_produces_no_false_permission_matrix(mainland_db):
    """AC1: re-enriching the Mainland doc produces 0 permission_matrix profiles
    on the amendment/section table."""
    with session_scope(mainland_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=mainland_db["document_id"])
        enrich_document_semantics(session, document_id=mainland_db["document_id"])
        matrices = (
            session.query(TableSemanticProfile)
            .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
            .filter(
                SourceTable.document_id == mainland_db["document_id"],
                TableSemanticProfile.profile_type == "permission_matrix",
            )
            .count()
        )
        assert matrices == 0


def test_abs283_mainland_permitted_use_resolves_via_section_prose(mainland_db):
    """AC2: a Mainland (use, zone) query resolves via the section-prose path,
    not the symbol-dot resolver."""
    from layer1.semantic.enrichment import resolve_mainland_permitted_use

    with session_scope(mainland_db["db_url"]) as session:
        enrich_document_semantics(session, document_id=mainland_db["document_id"])

        permitted = resolve_mainland_permitted_use(
            session, use_name="single unit dwelling", zone="ICH"
        )
        assert permitted is not None
        assert permitted["permission"] == "permitted"
        assert permitted["convention"] == "mainland_section_prose"

        # Plural / "use"-suffixed phrasing still resolves.
        permitted_suffixed = resolve_mainland_permitted_use(
            session, use_name="Open Space use", zone="ICH"
        )
        assert permitted_suffixed["permission"] == "permitted"

        # A use absent from the closed list is grounded not_permitted (the
        # Mainland exhaustiveness clause), not a silent miss.
        denied = resolve_mainland_permitted_use(
            session, use_name="restaurant", zone="ICH"
        )
        assert denied is not None
        assert denied["permission"] == "not_permitted"

        # A zone with no permitted-use list returns None (fall-through), so the
        # caller can report indeterminate rather than a bogus verdict.
        unknown_zone = resolve_mainland_permitted_use(
            session, use_name="single unit dwelling", zone="CEN-2"
        )
        assert unknown_zone is None


def test_abs284_section_indexed_profile_threads_through_enrichment(mainland_db):
    """AC3: enriching the Mainland doc under a ``section_indexed`` profile yields
    0 permission_matrix profiles — the profile threads end-to-end through
    ``enrich_document_semantics`` (not just ``_classify_table`` in isolation)."""
    from layer1.profiles import ParsingProfile
    from layer1.semantic.conventions import SECTION_INDEXED

    profile = ParsingProfile(name="mainland-test", permission_encoding=SECTION_INDEXED)
    with session_scope(mainland_db["db_url"]) as session:
        enrich_document_semantics(
            session, document_id=mainland_db["document_id"], profile=profile
        )
        matrices = (
            session.query(TableSemanticProfile)
            .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
            .filter(
                SourceTable.document_id == mainland_db["document_id"],
                TableSemanticProfile.profile_type == "permission_matrix",
            )
            .count()
        )
        assert matrices == 0
        # The prose permitted-use path still resolves under the profile.
        from layer1.semantic.enrichment import resolve_mainland_permitted_use

        permitted = resolve_mainland_permitted_use(
            session, use_name="single unit dwelling", zone="ICH"
        )
        assert permitted is not None
        assert permitted["permission"] == "permitted"


def test_abs284_symbol_matrix_profile_still_classifies_rc_matrix(semantic_db):
    """The symbol_matrix encoding (RC default behavior) still produces a
    permission_matrix when threaded through enrichment as an explicit profile."""
    from layer1.profiles import ParsingProfile
    from layer1.semantic.conventions import SYMBOL_MATRIX

    profile = ParsingProfile(name="rc-test", permission_encoding=SYMBOL_MATRIX)
    with session_scope(semantic_db["db_url"]) as session:
        enrich_document_semantics(
            session, document_id=semantic_db["document_id"], profile=profile
        )
        matrices = (
            session.query(TableSemanticProfile)
            .join(SourceTable, SourceTable.id == TableSemanticProfile.table_id)
            .filter(
                SourceTable.document_id == semantic_db["document_id"],
                TableSemanticProfile.profile_type == "permission_matrix",
            )
            .count()
        )
        assert matrices == 1


def _add_table_rows(session, table_id: int, rows: list[list[str]]) -> None:
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table_id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=rows[0][col_index] if row_index and col_index < len(rows[0]) else None,
                    text=text,
                    metadata_json={},
                )
            )
