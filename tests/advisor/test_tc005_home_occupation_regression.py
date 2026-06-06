"""Regression: TC-005 T5 — home occupation in HR-2 (ABS-280).

Phase 4 acceptance criteria AC4: The structured resolver's output for the
TC-005 (use, zone) pair must be asserted so the behavior can't silently regress.

TC-005 T5 asks: "Is home occupation permitted in HR-2?"
The correct answer per Table 1A of the Regional Centre LUB is **not_permitted**
(the cell is blank). The pre-Phase-3 advisor hedged because it couldn't read
the permission matrix. This test pins the resolver so any future regression
from "not_permitted" back to a hedge or wrong verdict is caught before deploy.

The test also covers the T3 assertion ("is multi-unit dwelling permitted in
HR-2?") which the advisor correctly stated but only via general reasoning —
now it must be grounded in a Table 1A cell.

Matrix seeded here:
                HR-2      DD
  multi-unit      ●     ●       (permitted)
  home occupation ""    ●       (blank → not_permitted in HR-2; permitted in DD)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval.service import RetrievalService
from layer1.db.base import Document, SourceTable, SourceTableCell
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer1.semantic.enrichment import enrich_document_semantics

CAPTION = "Table 1A: Permitted uses by zone — Residential"

MATRIX = [
    ["Use", "HR-2", "DD"],
    ["multi-unit dwelling use", "●", "●"],
    ["home occupation use", "", "●"],
]


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
# AC4a — TC-005 T5: home occupation in HR-2 → not_permitted
# ---------------------------------------------------------------------------


def test_tc005_t5_home_occupation_not_permitted_in_hr2(tc005_db):
    """The resolver must return not_permitted for home occupation in HR-2.

    This is the core regression guard for TC-005 T5. If this ever changes to
    'permitted' or 'indeterminate', the advisor answer will silently regress
    back to the pre-Phase-3 hedging / wrong-committed behavior.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="home occupation use", zone="HR-2"
        )

    assert result.indeterminate is False, (
        f"Expected a definitive answer, got indeterminate with reason={result.reason_code!r}"
    )
    assert result.permission == "not_permitted", (
        f"home occupation must be NOT PERMITTED in HR-2 per Table 1A; "
        f"got permission={result.permission!r}"
    )
    assert result.footnote_ordinal is None
    assert result.condition_text is None
    # Citation must ground the answer in Table 1A.
    assert result.citation is not None
    assert result.citation.bylaw_name == "Regional Centre Land Use By-law"
    assert result.table_id == tc005_db["table_id"]


# ---------------------------------------------------------------------------
# AC4b — TC-005 T3: multi-unit dwelling in HR-2 → permitted
# ---------------------------------------------------------------------------


def test_tc005_t3_multi_unit_dwelling_permitted_in_hr2(tc005_db):
    """The resolver must confirm multi-unit dwelling is permitted in HR-2.

    TC-005 T3 asks whether multi-unit dwelling is confirmed as a permitted
    use. Pre-Phase-3 the advisor answered via general reasoning, not a Table
    1A citation. This test ensures the structured path returns a cited answer.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="multi-unit dwelling use", zone="HR-2"
        )

    assert result.indeterminate is False
    assert result.permission == "permitted"
    assert result.citation is not None


# ---------------------------------------------------------------------------
# Contrast: home occupation IS permitted in DD — confirms resolver is zone-aware
# ---------------------------------------------------------------------------


def test_home_occupation_permitted_in_dd_confirms_zone_specificity(tc005_db):
    """Same use, different zone → different verdict.

    Guards against a regression where the resolver ignores the zone argument
    and returns the same answer for any zone.
    """
    with session_scope(tc005_db["db_url"]) as session:
        service = RetrievalService(session)
        result = service.lookup_permitted_use(
            use="home occupation use", zone="DD"
        )

    assert result.indeterminate is False
    assert result.permission == "permitted", (
        "home occupation is permitted in DD in this matrix; "
        "if both DD and HR-2 return not_permitted the resolver is zone-blind"
    )
