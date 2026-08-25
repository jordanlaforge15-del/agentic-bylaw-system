"""ABS-523: a permission cell states every condition printed in it.

Table 1B of the Regional Centre LUB gives (ER-3, Multi-unit dwelling use) as::

    ⑮ ㉒

⑮ is a Halifax Grain Elevator carve-out. ㉒ is the only statement anywhere in
the corpus of how the ER-3 unit cap interacts with the two routes that exceed
it::

    ㉒ A multi-unit dwelling use that contains up to 8 dwelling units is
      permitted in the ER-3 zone, in accordance with Section 231.3, and a
      multi-unit dwelling use that contains more than 8 units is permitted in
      the ER-3 zone in accordance with Section 63 or Subsection 233(3).

Enrichment kept the first ordinal and dropped the rest, so ``get_zone_profile``
— the case-open shortcut the agent is instructed to call first — reported a
grain elevator and no route. Asked whether twelve units were achievable at
6363 Summit Street, the advisor said no and sent the developer to a rezoning.

16 (zone, use) cells across Tables 1A–1D carry two or more markers, and ㉒'s
ER-2 counterpart ㉖ is dropped from three of them, so the whole ER
residential-intensification family is in scope: every "how many units can I get
in ER-1/2/3" question.

These tests fail against the pre-ABS-523 first-marker-wins classifier.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalService
from layer1.db.base import Document, SourceTable, SourceTableCell
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics
from layer1.semantic.permission_markers import (
    cell_footnotes,
    classify_permission_marker,
)

GRAIN_ELEVATOR_LEGEND = (
    "⑮ Use is permitted, except within the Halifax Grain Elevator (HGE) "
    "Special Area, as shown on Schedule 3F."
)
CONVERSION_LEGEND = (
    "㉒ A multi-unit dwelling use that contains up to 8 dwelling units is "
    "permitted in the ER-3 zone, in accordance with Section 231.3, and a "
    "multi-unit dwelling use that contains more than 8 units is permitted in "
    "the ER-3 zone in accordance with Section 63 or Subsection 233(3)."
)

#: Every multi-marker cell in the live Regional Centre corpus (document 4),
#: re-derived by scripts/audit_permission_footnotes.py. The point of pinning the
#: whole census rather than only the ER-3 cell: ㉖ — the ER-2 internal
#: conversion route — is dropped from three separate ER-2 cells, and a fix
#: verified on TC-023 alone would leave those three exactly as they were.
LIVE_CORPUS_MULTI_MARKER_CELLS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("p45 HR-2 / Short-term bedroom rental use", (15, 24)),
    ("p45 HR-1 / Short-term bedroom rental use", (15, 24)),
    ("p45 HR-1 / Restaurant use", (2, 3)),
    ("p48 ER-2 / Two-unit dwelling use", (6, 14, 15)),
    ("p48 ER-1 / Two-unit dwelling use", (6, 14)),
    ("p48 ER-2 / Three-unit dwelling use", (6, 14, 15, 26)),
    ("p48 ER-1 / Three-unit dwelling use", (6, 14)),
    ("p48 ER-2 / Four-unit dwelling use", (14, 15, 26)),
    ("p48 ER-3 / Multi-unit dwelling use", (15, 22)),  # TC-023
    ("p48 ER-2 / Multi-unit dwelling use", (14, 15, 26)),
    ("p48 ER-2 / Small shared housing use", (15, 23)),
    ("p48 ER-1 / Small shared housing use", (15, 23)),
    ("p48 ER-2 / Short-term bedroom rental use", (15, 24)),
    ("p48 ER-1 / Short-term bedroom rental use", (15, 24)),
    ("p51 INS / Multi-unit dwelling use", (8, 27)),
    ("p54 HCD-SV / Four-unit dwelling use", (9, 17)),
)

#: ㉖ — the ER-2 internal-conversion route, the counterpart of the ㉒ that
#: TC-023 needed — is dropped from three separate ER-2 cells. Pinned as its own
#: number because it is the reason this is not a one-cell fix.
ER2_CONVERSION_ROUTE_CELLS = 3


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------


def test_the_er3_cell_states_both_of_its_conditions():
    """The reported defect, at the layer that caused it."""
    assert classify_permission_marker("⑮ ㉒") == {
        "permission_marker": "conditional",
        "footnote": 15,
        "footnotes": [15, 22],
    }


def test_markers_keep_the_order_the_table_prints_them_in():
    """A reader checking the answer against the table finds them the same way
    round. Sorting would be a different, quieter claim about the source."""
    assert classify_permission_marker("㉒ ⑮")["footnotes"] == [22, 15]


def test_a_repeated_marker_is_counted_once():
    assert classify_permission_marker("⑮ ⑮ ㉒")["footnotes"] == [15, 22]


def test_a_single_marker_cell_is_unchanged_apart_from_the_list():
    result = classify_permission_marker("③")
    assert result["permission_marker"] == "conditional"
    assert result["footnote"] == 3
    assert result["footnotes"] == [3]


def test_a_permitted_or_blank_cell_carries_no_footnotes():
    for text in ("●", "", "   "):
        assert "footnotes" not in classify_permission_marker(text)


@pytest.mark.parametrize("label,ordinals", LIVE_CORPUS_MULTI_MARKER_CELLS)
def test_every_multi_marker_cell_in_the_live_corpus_survives(label, ordinals):
    """The 16-cell census, pinned as glyphs so a regression names the cell.

    The blast radius is not one cell: ㉖ is dropped from three ER-2 rows and ⑮
    partners a different second marker in four different tables. A fix graded
    only on TC-023 would have left most of this in place.
    """
    glyphs = " ".join(_circled(ordinal) for ordinal in ordinals)
    result = classify_permission_marker(glyphs)
    assert result["footnotes"] == list(ordinals), label
    assert len(ordinals) > 1, f"{label} is not a multi-marker cell"


def test_the_census_is_sixteen_cells_and_names_the_er2_route_three_times():
    """The blast-radius numbers the ticket argues from, kept honest.

    ``scripts/audit_permission_footnotes.py`` re-derives both against the live
    corpus. If a re-ingest changes the table, that script is how you find out
    what the new numbers are — not by editing these until they pass.
    """
    assert len(LIVE_CORPUS_MULTI_MARKER_CELLS) == 16
    dropped_26 = sum(
        1
        for _label, ordinals in LIVE_CORPUS_MULTI_MARKER_CELLS
        if 26 in ordinals[1:]
    )
    assert dropped_26 == ER2_CONVERSION_ROUTE_CELLS


def _circled(ordinal: int) -> str:
    from layer1.semantic.permission_markers import ordinal_to_circled

    glyph = ordinal_to_circled(ordinal)
    assert glyph is not None
    return glyph


# ----------------------------------------------------------------------
# Recovery on a corpus annotated before the fix
# ----------------------------------------------------------------------


def test_a_pre_abs523_annotation_recovers_its_dropped_ordinals():
    """The live corpus is annotated ``{"footnote": 15}`` with ㉒ nowhere.

    Retrieval re-derives the list from the cell text rather than waiting for
    the backfill, so the fix reaches the reader on the corpus as it stands.
    """
    stale = {"permission_marker": "conditional", "footnote": 15}
    assert cell_footnotes(stale, "⑮ ㉒") == [15, 22]


def test_the_stored_list_wins_when_it_is_present():
    """A conventions change that stops the text classifying must not silently
    re-derive a different answer than the one recorded at ingest."""
    annotated = {"permission_marker": "conditional", "footnote": 15, "footnotes": [15, 22]}
    assert cell_footnotes(annotated, "unreadable") == [15, 22]


def test_a_cell_with_neither_list_nor_glyphs_falls_back_to_the_scalar():
    assert cell_footnotes({"footnote": 7}, "") == [7]
    assert cell_footnotes({}, "") == []


# ----------------------------------------------------------------------
# End to end through the retrieval service
# ----------------------------------------------------------------------


@pytest.fixture()
def matrix_db(tmp_path: Path) -> dict[str, int | str]:
    """A matrix whose ER-3 multi-unit cell carries both markers, plus legends."""
    db_url = f"sqlite:///{tmp_path / 'abs523.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Footnote Retention By-law",
            source_path="abs523.pdf",
            file_hash="abs523-multi-footnote",
            mime_type="application/pdf",
            page_count=60,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()

        from layer1.db.base import SourceFragment
        from layer1.models.enums import FragmentType

        for index, text in enumerate((GRAIN_ELEVATOR_LEGEND, CONVERSION_LEGEND)):
            session.add(
                SourceFragment(
                    document_id=document.id,
                    # PROSE, not FOOTNOTE: the Regional Centre's legend rows are
                    # typed that way and the matcher keys off the leading glyph.
                    fragment_type=FragmentType.PROSE,
                    page_start=50,
                    page_end=50,
                    reading_order_start=index + 1,
                    reading_order_end=index + 1,
                    text=text,
                    parse_status=ParseStatus.PARSED,
                    confidence=1.0,
                    source_block_ids_json=[],
                    metadata_json={},
                )
            )

        table = SourceTable(
            document_id=document.id,
            caption="Table 1F: Permitted uses by zone",
            page_start=50,
            page_end=50,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(table)
        session.flush()

        cells = [
            (0, 0, "Use", None, "Use"),
            (0, 1, "ER-3", None, "ER-3"),
            (0, 2, "ER-2", None, "ER-2"),
            (1, 0, "Multi-unit dwelling use", "Multi-unit dwelling use", None),
            (1, 1, "⑮ ㉒", "Multi-unit dwelling use", "ER-3"),
            (1, 2, "⑮", "Multi-unit dwelling use", "ER-2"),
            (2, 0, "Single-unit dwelling use", "Single-unit dwelling use", None),
            (2, 1, "●", "Single-unit dwelling use", "ER-3"),
            (2, 2, "●", "Single-unit dwelling use", "ER-2"),
        ]
        for row, col, text, row_header, col_header in cells:
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row,
                    col_index=col,
                    row_header_path=row_header,
                    col_header_path=col_header,
                    text=text,
                    metadata_json={},
                )
            )
        session.flush()
        document_id = document.id

    with session_scope(db_url) as session:
        enrich_document_semantics(session, document_id=document_id)

    return {"db_url": db_url, "document_id": document_id}


def test_lookup_permitted_use_returns_both_conditions_with_their_legends(matrix_db):
    """The acceptance criterion, on the call the agent makes."""
    with session_scope(matrix_db["db_url"]) as session:
        result = RetrievalService(session).lookup_permitted_use(
            use="Multi-unit dwelling use",
            zone="ER-3",
            document_id=matrix_db["document_id"],
        )

    assert result.indeterminate is False
    assert result.permission == "conditional"
    assert [condition.ordinal for condition in result.footnotes] == [15, 22]
    assert "Halifax Grain Elevator" in result.footnotes[0].text
    # The route that makes the answer right, and the two provisions it names.
    assert "Section 63" in result.footnotes[1].text
    assert "233(3)" in result.footnotes[1].text


def test_the_compact_projection_carries_every_condition_to_the_model(matrix_db):
    """The model reads the compact projection, not the DTO. ABS-523 is only
    fixed if the second condition survives that hop."""
    from advisor.chat.compact import compact_permitted_use

    with session_scope(matrix_db["db_url"]) as session:
        result = RetrievalService(session).lookup_permitted_use(
            use="Multi-unit dwelling use",
            zone="ER-3",
            document_id=matrix_db["document_id"],
        )
    out = compact_permitted_use(result)

    assert [c["footnote"] for c in out["conditions"]] == [15, 22]
    assert "Section 63" in out["conditions"][1]["text"]
    # The instruction has to say "all of them" — a writer handed two conditions
    # and told to quote "the footnote" will quote one.
    assert "EVERY" in out["instruction"]
    assert "22" in out["instruction"]


def test_a_single_marker_cell_still_projects_one_condition(matrix_db):
    """The fix must not manufacture conditions where the table states one."""
    from advisor.chat.compact import compact_permitted_use

    with session_scope(matrix_db["db_url"]) as session:
        result = RetrievalService(session).lookup_permitted_use(
            use="Multi-unit dwelling use",
            zone="ER-2",
            document_id=matrix_db["document_id"],
        )
    assert [c.ordinal for c in result.footnotes] == [15]
    assert len(compact_permitted_use(result)["conditions"]) == 1


def test_the_zone_profile_enumeration_carries_both_conditions(matrix_db):
    """``get_zone_profile`` is what the agent is told to call first, so this is
    where the ER-3 answer was decided before any search ran."""
    with session_scope(matrix_db["db_url"]) as session:
        profile = RetrievalService(session).get_zone_profile(zone="ER-3")

    multi = [
        item for item in profile.uses.conditional if item.use == "Multi-unit dwelling use"
    ]
    assert multi, "the conditional use is missing from the zone profile"
    assert [condition.ordinal for condition in multi[0].footnotes] == [15, 22]
    assert "Section 63" in multi[0].footnotes[1].text
