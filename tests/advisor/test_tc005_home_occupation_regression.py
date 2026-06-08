"""Regression: TC-005 T5 — home occupation in HR-2 (ABS-280).

Phase 4 acceptance criteria AC4: The structured resolver's output for the
TC-005 (use, zone) pair must be asserted so the behavior can't silently regress.

TC-005 T5 asks: "Is home occupation permitted in HR-2?"

The correct answer per Table 1A of the Regional Centre LUB is **conditional** —
the HR-2 cell carries the circled-number footnote ⑮ ("Use is permitted, except
within the Halifax Grain Elevator (HGE) Special Area..."), not a blank
(not-permitted) and not a bare ● (permitted). The pre-Phase-3 advisor hedged
because it couldn't read the permission matrix at all; an earlier draft of this
regression pinned the wrong verdict (``not_permitted``) against a fabricated
blank-cell fixture. This version pins the *real* verdict, verified against doc 4
table 1056 cell (row=Home occupation use, col=HR-2): a conditional cell with
footnote ordinal 15 and its joined condition text.

The condition text only resolves because ``_footnote_condition_text`` matches the
footnote legend by its *leading* circled glyph regardless of how ingest typed the
fragment. The Regional Centre legend rows were ingested as PROSE, not FOOTNOTE, so
the fixture below deliberately seeds the legend as a PROSE fragment to guard that
fix (deeper ingest-typing fix tracked in ABS-284).

Matrix seeded here (mirrors doc 4 semantics):
                  HR-2      DD
  multi-unit       ⑮        ●      (conditional in HR-2; permitted in DD)
  home occupation  ⑮        ●      (conditional in HR-2; permitted in DD)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

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

# ● = permitted dot (symbol-font Private Use Area codepoint U+F098, as stored in
# the real corpus). ⑮ = circled-15 conditional footnote (U+246E).
DOT = chr(0xF098)
COND15 = chr(0x246E)

MATRIX = [
    ["Use", "HR-2", "DD"],
    ["multi-unit dwelling use", COND15, DOT],
    ["home occupation use", COND15, DOT],
]

# Footnote-15 legend, deliberately typed PROSE to mirror the real (mis-typed)
# Regional Centre ingest and guard the resolver's type-agnostic legend match.
FOOTNOTE_LEGEND = (
    f"{COND15} Use is permitted, except within the Halifax Grain Elevator (HGE) "
    "Special Area, as shown on Schedule 3F."
)


def _build_matrix(session, document_id: int) -> int:
    table = SourceTable(
        document_id=document_id,
        caption=CAPTION,
        page_start=12,
        page_end=13,
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
    # Footnote legend lives elsewhere in the document body (PROSE, as ingested).
    session.add(
        SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.PROSE,
            page_start=14,
            page_end=14,
            text=FOOTNOTE_LEGEND,
            parse_status=ParseStatus.PARSED,
        )
    )
    session.flush()
    return table.id


@pytest.fixture()
def tc005_db(tmp_path: Path) -> dict:
    """SQLite DB with HR-2 permission matrix, axes enriched by Phase-2."""
    db_url = f"sqlite:///{tmp_path / 'tc005_regression.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="Halifax",
            bylaw_name="Regional Centre Land Use By-law",
            source_path="regional_centre.pdf",
            file_hash="tc005-abs280-regression",
            mime_type="application/pdf",
            ingestion_timestamp=datetime.now(timezone.utc),
            parser_version="test",
        )
        session.add(document)
        session.flush()
        table_id = _build_matrix(session, document.id)
        enrich_document_semantics(session, document_id=document.id)
        document_id = document.id
    return {"db_url": db_url, "document_id": document_id, "table_id": table_id}


# ---------------------------------------------------------------------------
# AC4a — TC-005 T5: home occupation in HR-2 → conditional ⑮ + condition text
# ---------------------------------------------------------------------------


def test_tc005_t5_home_occupation_conditional_in_hr2(tc005_db):
    """The resolver must return a CONDITIONAL verdict for home occupation in HR-2.

    This is the core regression guard for TC-005 T5. The HR-2 cell carries
    footnote ⑮, so the answer is "permitted, subject to condition 15" — not a
    hedge, not not_permitted, and not a bare permitted. If this regresses to any
    other verdict, or loses the footnote condition text, the advisor answer
    silently degrades.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="home occupation use", zone="HR-2"
        )

    assert result.indeterminate is False, (
        f"Expected a definitive answer, got indeterminate with reason={result.reason_code!r}"
    )
    assert result.permission == "conditional", (
        f"home occupation must be CONDITIONAL (footnote ⑮) in HR-2 per Table 1A; "
        f"got permission={result.permission!r}"
    )
    # AC2: a conditional cell must carry its footnote ordinal AND condition text.
    assert result.footnote_ordinal == 15
    assert result.condition_text is not None, (
        "conditional cell must surface the footnote legend text (AC2); "
        "got condition_text=None — the legend lookup is not matching"
    )
    assert "Halifax Grain Elevator" in result.condition_text
    # Citation must ground the answer in Table 1A.
    assert result.citation is not None
    assert result.citation.bylaw_name == "Regional Centre Land Use By-law"
    assert result.table_id == tc005_db["table_id"]


def test_tc005_t5_bare_use_form_resolves_conditional(tc005_db):
    """ABS-282: the bare use form ("home occupation") resolves identically.

    Guards the resolver's use-form tolerance — a user asking "is home occupation
    allowed in HR-2" (no trailing "use") must reach the same conditional cell.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(use="home occupation", zone="HR-2")

    assert result.indeterminate is False
    assert result.permission == "conditional"
    assert result.footnote_ordinal == 15


# ---------------------------------------------------------------------------
# AC4b — TC-005 T3: multi-unit dwelling in HR-2 → also conditional ⑮
# ---------------------------------------------------------------------------


def test_tc005_t3_multi_unit_dwelling_conditional_in_hr2(tc005_db):
    """multi-unit dwelling in HR-2 is conditional (⑮), not plainly permitted.

    TC-005 T3 asks whether multi-unit dwelling is a permitted use. The real
    Table 1A HR-2 cell carries the same ⑮ footnote, so the grounded answer is
    "conditional", and the advisor must not over-state it as unconditionally
    permitted. This pins that the structured path returns the cited conditional.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="multi-unit dwelling use", zone="HR-2"
        )

    assert result.indeterminate is False
    assert result.permission == "conditional"
    assert result.footnote_ordinal == 15
    assert result.citation is not None


# ---------------------------------------------------------------------------
# Contrast: home occupation IS plainly permitted in DD — confirms zone-awareness
# AND guards the ● (permitted) marker path.
# ---------------------------------------------------------------------------


def test_home_occupation_permitted_in_dd_confirms_zone_specificity(tc005_db):
    """Same use, different zone → different verdict.

    In DD the cell is a bare ● (permitted, no footnote). Guards against a
    regression where the resolver ignores the zone argument (it would return
    conditional for both zones) or mis-reads the ● marker.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="home occupation use", zone="DD"
        )

    assert result.indeterminate is False
    assert result.permission == "permitted", (
        "home occupation is permitted (●) in DD in this matrix; "
        "if both DD and HR-2 return the same verdict the resolver is zone-blind"
    )
    assert result.footnote_ordinal is None
    assert result.condition_text is None
