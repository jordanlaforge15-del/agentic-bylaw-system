"""Unit + integration coverage for structured permitted-use retrieval (ABS-279).

Phase 3 reads a permission-matrix cell directly: given a ``(use, zone)`` pair it
resolves the Table 1A cell via the Phase-2 axis bindings and returns a typed
permission (permitted / conditional / not_permitted) plus, for a conditional
cell, the footnote ordinal and its joined condition text. Every partial-match
case (unknown use, unknown zone, no matrix in scope) returns a typed
indeterminate result with a reason rather than a silent empty success.

The tests build an in-memory SQLite matrix, run the real Phase-2 enrichment to
bind the axes, then exercise ``RetrievalService.lookup_permitted_use`` and the
``lookup_citation`` tool dispatch end-to-end.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from advisor.chat.tools import _SCHEMA_LOOKUP_CITATION, build_bylaw_tools
from bylaw_retrieval.retrieval.service import RetrievalService
from layer1.db.base import (
    Document,
    SourceFragment,
    SourceTable,
    SourceTableCell,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics

CAPTION = "Table 1A: Permitted uses by zone — Residential"

# Use × zone matrix:
#                DD   DH(③ conditional)  COR(blank)
#   Restaurant   ●    ③                  ""
#   Office       ●    ●                  ●
MATRIX = [
    ["Use", "DD", "DH", "COR"],
    ["Restaurant use", "●", "③", ""],
    ["Office use", "●", "●", "●"],
    ["Multi-unit dwelling use", "●", "●", ""],
]

CONDITION_TEXT = (
    "③ Use is permitted subject to a maximum gross floor area of 100 square "
    "metres and frontage on a collector street."
)


def _build_matrix(session, document_id: int) -> int:
    table = SourceTable(
        document_id=document_id,
        caption=CAPTION,
        page_start=45,
        page_end=46,
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    for row_index, row in enumerate(MATRIX):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=(
                        MATRIX[0][col_index]
                        if row_index and col_index < len(MATRIX[0])
                        else None
                    ),
                    text=text,
                    metadata_json={},
                )
            )
    # The footnote fragment that the ③ conditional cell joins to.
    session.add(
        SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.FOOTNOTE,
            page_start=46,
            page_end=46,
            reading_order_start=900,
            reading_order_end=900,
            text=CONDITION_TEXT,
            parse_status=ParseStatus.PARSED,
            confidence=0.9,
        )
    )
    session.flush()
    return table.id


@pytest.fixture()
def matrix_db(tmp_path: Path) -> dict:
    """A sqlite DB with a permission matrix whose axes are bound by enrichment."""
    db_url = f"sqlite:///{tmp_path / 'permitted_use.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="regional.pdf",
            file_hash="abs279-permitted-use",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table_id = _build_matrix(session, document.id)
        # Phase-2: bind columns→zone, rows→use so resolve_permission_cell works.
        enrich_document_semantics(session, document_id=document.id)
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id, "table_id": table_id}


# --------------------------------------------------------------------------
# AC1-AC4 — service resolver
# --------------------------------------------------------------------------


def test_ac1_permitted_cell_returns_permitted_with_citation(matrix_db):
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Restaurant use", zone="DD")

    assert result.indeterminate is False
    assert result.permission == "permitted"
    assert result.footnote_ordinal is None
    assert result.condition_text is None
    # Citation keeps the answer grounded.
    assert result.citation is not None
    assert result.citation.bylaw_name == "Regional Centre Land Use By-law"
    assert result.citation.page_start == 45
    assert result.table_id == matrix_db["table_id"]


def test_ac2_conditional_cell_returns_footnote_and_condition_text(matrix_db):
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Restaurant use", zone="DH")

    assert result.indeterminate is False
    assert result.permission == "conditional"
    # ③ → ordinal 3, with the joined condition text.
    assert result.footnote_ordinal == 3
    assert result.condition_text
    assert "maximum gross floor area" in result.condition_text


def test_ac3_blank_cell_returns_not_permitted(matrix_db):
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Restaurant use", zone="COR")

    assert result.indeterminate is False
    assert result.permission == "not_permitted"
    assert result.footnote_ordinal is None


def test_ac4_unknown_use_is_typed_indeterminate(matrix_db):
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Brewery use", zone="DD")

    assert result.indeterminate is True
    assert result.permission is None
    assert result.reason_code == "unknown_use"
    assert result.reason  # non-empty reason string
    assert "Brewery use" in result.reason


def test_ac4_unknown_zone_is_typed_indeterminate(matrix_db):
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Restaurant use", zone="ZZ-9")

    assert result.indeterminate is True
    assert result.permission is None
    assert result.reason_code == "unknown_zone"
    assert result.reason


# --------------------------------------------------------------------------
# ABS-483 — an unreadable cell is an extraction gap, never a prohibition
# --------------------------------------------------------------------------


def _drop_cell(session, table_id: int, row_index: int, col_index: int) -> None:
    session.query(SourceTableCell).filter(
        SourceTableCell.table_id == table_id,
        SourceTableCell.row_index == row_index,
        SourceTableCell.col_index == col_index,
    ).delete(synchronize_session=False)
    session.flush()


def test_abs483_missing_cell_is_indeterminate_not_not_permitted(matrix_db):
    """(Multi-unit dwelling use, COR) addresses a cell the parser lost. The
    answer must be a typed indeterminate — the old three-value vocabulary was
    forced to call it ``not_permitted``, i.e. to invent a prohibition."""
    with session_scope(matrix_db["db_url"]) as session:
        _drop_cell(session, matrix_db["table_id"], 3, 3)
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="Multi-unit dwelling use", zone="COR"
        )

    assert result.permission is None
    assert result.indeterminate is True
    assert result.reason_code == "unreadable_cell"
    # The reason has to say WHY, so the model doesn't restate it as a refusal.
    assert "does NOT mean the use is prohibited" in result.reason
    # Still grounded: the caller can go read the table it came from.
    assert result.citation is not None
    assert result.table_id == matrix_db["table_id"]


def test_abs483_unmapped_glyph_cell_is_indeterminate(matrix_db):
    """Same contract for the other producer: a symbol-font glyph this bylaw's
    profile does not map. We read *something*, we just can't decode it."""
    with session_scope(matrix_db["db_url"]) as session:
        cell = (
            session.query(SourceTableCell)
            .filter(
                SourceTableCell.table_id == matrix_db["table_id"],
                SourceTableCell.row_index == 3,
                SourceTableCell.col_index == 3,
            )
            .one()
        )
        cell.text = chr(0xF0AA)  # unmapped private-use codepoint
        cell.metadata_json = {}
        session.flush()
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="Multi-unit dwelling use", zone="COR"
        )

    assert result.indeterminate is True
    assert result.permission is None
    assert result.reason_code == "unreadable_cell"


def test_abs483_a_gap_in_one_matrix_slice_falls_through_to_the_next(matrix_db):
    """Table 1A spans several ``source_table`` rows. A gap in the slice that
    happens to be scanned first must not end the search — the slice that DOES
    carry the cell answers."""
    with session_scope(matrix_db["db_url"]) as session:
        document_id = matrix_db["document_id"]
        # A second slice of the same logical matrix, carrying a ● where the
        # first slice's cell is about to go missing.
        second = SourceTable(
            document_id=document_id,
            caption="Table 1A: Permitted uses by zone — Commercial",
            page_start=47,
            page_end=47,
            parse_status=ParseStatus.PARSED,
            metadata_json={},
        )
        session.add(second)
        session.flush()
        for row_index, row in enumerate(
            [["Use", "COR"], ["Multi-unit dwelling use", "●"]]
        ):
            for col_index, text in enumerate(row):
                session.add(
                    SourceTableCell(
                        table_id=second.id,
                        row_index=row_index,
                        col_index=col_index,
                        row_header_path=row[0] if row_index else None,
                        col_header_path="COR" if row_index and col_index else None,
                        text=text,
                        metadata_json={},
                    )
                )
        _drop_cell(session, matrix_db["table_id"], 3, 3)
        enrich_document_semantics(session, document_id=document_id)

        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="Multi-unit dwelling use", zone="COR"
        )

    assert result.indeterminate is False
    assert result.permission == "permitted"
    assert result.table_id == second.id


def test_abs483_blank_cell_still_resolves_to_not_permitted(matrix_db):
    """Guard the other side of the line: the blank-cell convention is real
    bylaw content and must NOT be swept into the new indeterminate branch."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Restaurant use", zone="COR")

    assert result.indeterminate is False
    assert result.permission == "not_permitted"


# --------------------------------------------------------------------------
# ABS-351 — near-miss use terms resolve or suggest, never silently mis-pick
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "use",
    ["Multiple-unit dwelling", "multi unit dwelling", "Multi-Unit Dwelling"],
)
def test_abs351_near_miss_use_resolves_to_canonical_row(matrix_db, use):
    """A deterministic-key near miss addresses the "Multi-unit dwelling use" row
    directly — no wasted guess call — for CEN-2-style (use, DD)."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use=use, zone="DD")

    assert result.indeterminate is False
    assert result.permission == "permitted"
    assert result.citation is not None


def test_abs351_ambiguous_use_suggests_ranked_rows(matrix_db):
    """"Dwelling unit" overlaps several rows, so the resolver stays indeterminate
    (unknown_use) but ships ranked suggestions rather than picking a row."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Dwelling unit", zone="DD")

    assert result.indeterminate is True
    assert result.reason_code == "unknown_use"
    assert result.permission is None
    # The intended canonical row is present in the advisory list, spelled as the
    # bylaw prints it so it can be re-issued verbatim.
    assert "Multi-unit dwelling use" in result.suggested_uses


def test_abs351_suggestion_reissue_resolves_in_one_more_call(matrix_db):
    """Transcript-replay contract: the first miss carries a suggestion; feeding
    it straight back resolves. So use-row resolution costs at most one wasted
    call, not the three the ABS-351 transcript burned."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        first = service.lookup_permitted_use(use="Dwelling unit", zone="DD")
        assert first.indeterminate is True
        assert first.suggested_uses  # the self-correction hint
        second = service.lookup_permitted_use(use=first.suggested_uses[0], zone="DD")

    assert second.indeterminate is False
    assert second.permission == "permitted"


def test_abs351_unrelated_use_has_no_misleading_suggestions(matrix_db):
    """A token-poor unknown use ("Brewery use") stays a clean unknown_use with no
    coincidental suggestions — better an empty list than a wrong row."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Brewery use", zone="DD")

    assert result.indeterminate is True
    assert result.reason_code == "unknown_use"
    assert result.suggested_uses == []


@pytest.mark.asyncio
async def test_abs351_tool_dispatch_surfaces_suggestions_and_instruction(matrix_db):
    """Through the advisor tool handler, an unknown-use miss ships suggested_uses
    plus a next-action instruction in the compact tool_result."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        _, handlers = build_bylaw_tools(service)
        output = await handlers["lookup_citation"](
            {
                "structured": {
                    "kind": "permitted_use",
                    "use": "Dwelling unit",
                    "zone": "DD",
                },
                "document_id": matrix_db["document_id"],
            }
        )

    parsed = json.loads(output)["permitted_use"]
    assert parsed["indeterminate"] is True
    assert parsed["reason_code"] == "unknown_use"
    assert "Multi-unit dwelling use" in parsed["suggested_uses"]
    assert "suggested_uses" in parsed["instruction"]


def test_no_permission_matrix_in_scope_is_typed_indeterminate(matrix_db):
    """FR4 + FR3: scoping to a doc with no matrix never crosses bylaws and never
    returns a silent empty — it returns a typed indeterminate reason."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        # A document id that holds no permission matrix.
        result = service.lookup_permitted_use(
            use="Restaurant use", zone="DD", document_id=999_999
        )

    assert result.indeterminate is True
    assert result.reason_code == "no_permission_matrix"


# --------------------------------------------------------------------------
# AC5 — MCP tool schema validates + invocable through advisor tool dispatch
# --------------------------------------------------------------------------


def test_ac5_permitted_use_payload_validates_against_tool_schema():
    """The permitted_use structured variant is a valid lookup_citation input."""
    payload = {
        "structured": {
            "kind": "permitted_use",
            "use": "Restaurant use",
            "zone": "DH",
        }
    }
    # Raises jsonschema.ValidationError if the oneOf branch is missing/wrong.
    jsonschema.validate(payload, _SCHEMA_LOOKUP_CITATION)


# --------------------------------------------------------------------------
# ABS-283 — Mainland section-prose permitted-use path (no symbol-dot matrix)
# --------------------------------------------------------------------------


@pytest.fixture()
def mainland_db(tmp_path: Path) -> dict:
    """A Mainland LUB doc whose permitted uses live in prose section text, not a
    Table-1A symbol-dot matrix."""
    db_url = f"sqlite:///{tmp_path / 'mainland_lookup.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Halifax Mainland Land Use By-law",
            source_path="mainland.pdf",
            file_hash="abs283-mainland-lookup",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
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
                confidence=0.9,
            )
        )
        session.flush()
        enrich_document_semantics(session, document_id=document.id)
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id}


def test_abs283_mainland_permitted_use_resolves_through_service(mainland_db):
    """AC2: lookup_permitted_use resolves a Mainland (use, zone) query via the
    section-prose path even though no permission matrix exists in scope."""
    with session_scope(mainland_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Single Unit Dwelling", zone="ICH")

    assert result.indeterminate is False
    assert result.permission == "permitted"
    # Grounded back to the source section fragment.
    assert result.citation is not None
    assert result.citation.bylaw_name == "Halifax Mainland Land Use By-law"
    assert result.citation.page_start == 133


def test_abs283_mainland_absent_use_is_grounded_not_permitted(mainland_db):
    """A use absent from the closed Mainland list resolves to not_permitted, not
    a 'no_permission_matrix' indeterminate."""
    with session_scope(mainland_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Restaurant use", zone="ICH")

    assert result.indeterminate is False
    assert result.permission == "not_permitted"


def test_abs283_mainland_zone_without_list_is_indeterminate(mainland_db):
    """A zone with no Mainland permitted-use list (and no matrix) still returns a
    typed indeterminate, never a silent empty."""
    with session_scope(mainland_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="Single Unit Dwelling", zone="R-1")

    assert result.indeterminate is True
    assert result.reason_code == "no_permission_matrix"


@pytest.mark.asyncio
async def test_ac5_tool_dispatch_resolves_permitted_use_end_to_end(matrix_db):
    """Invoke lookup_citation through the advisor handler registry with a
    structured permitted_use query and assert the typed permission comes back."""
    with session_scope(matrix_db["db_url"]) as session:
        service = RetrievalService(session)
        _, handlers = build_bylaw_tools(service)
        output = await handlers["lookup_citation"](
            {
                "structured": {
                    "kind": "permitted_use",
                    "use": "Restaurant use",
                    "zone": "DH",
                },
                "document_id": matrix_db["document_id"],
            }
        )

    parsed = json.loads(output)
    assert "permitted_use" in parsed
    assert parsed["permitted_use"]["permission"] == "conditional"
    # ABS-523: the compact projection carries the whole condition list, never
    # the lossy first-only scalars.
    conditions = parsed["permitted_use"]["conditions"]
    assert [c["footnote"] for c in conditions] == [3]
    assert "text" in conditions[0]
