"""Integration coverage for the table-citation backfill (ABS-409, T2+T3).

Seeds sqlite databases mirroring the prod/dev orphan state (unaddressed
caption PROSE fragments + orphan ``source_table`` rows, some with WRONG
semantic profiles) and verifies the backfill end to end:

* dry-run reports the caption->tables mapping and writes nothing;
* apply links, then re-enrichment re-profiles with captions in view — an
  ``unknown``/misprofiled matrix becomes ``permission_matrix`` with axis
  bindings (the Table 1D / continuation-page repair), and a parking table
  wrongly profiled ``permission_matrix`` is demoted to ``parking_matrix``
  (the Table 15 repair);
* a second run is a no-op;
* ``--revert`` semantics: the recorded before-values restore the citation
  columns.
"""
from __future__ import annotations

import importlib.util as _importlib_util
import sys as _sys
from pathlib import Path

from sqlalchemy import select

from layer1.db.base import (
    Document,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.table_captions import revert_table_captions

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "backfill_table_citations.py"
)
_spec = _importlib_util.spec_from_file_location("backfill_table_citations", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_mod = _importlib_util.module_from_spec(_spec)
_sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

backfill = _mod.backfill

DOT = "●"

PERMISSION_MATRIX = [
    ["Use", "DD", "DH", "COR"],
    ["Restaurant use", DOT, DOT, ""],
    ["Multi-unit dwelling use", DOT, DOT, DOT],
]

PARKING_MATRIX = [
    ["Use", "DD", "COR"],
    ["Restaurant use", "Not required", "Maximum 1 space"],
    ["Office use", "Not required", "Not required"],
]


def _add_document(session) -> Document:
    doc = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="rc.pdf",
        file_hash="abs409-backfill",
        mime_type="application/pdf",
        page_count=100,
        parser_version="docling:halifax",
    )
    session.add(doc)
    session.flush()
    return doc


def _add_caption(session, document_id: int, *, text: str, page: int) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=FragmentType.PROSE,
        page_start=page,
        page_end=page,
        reading_order_start=page * 10,
        reading_order_end=page * 10,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _add_matrix_table(
    session,
    document_id: int,
    *,
    page: int,
    grid: list[list[str]],
    profile_type: str | None = None,
) -> SourceTable:
    table = SourceTable(
        document_id=document_id,
        page_start=page,
        page_end=page,
        parse_status=ParseStatus.PARSED,
        caption=None,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    for row_index, row in enumerate(grid):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=row[0] if row_index else None,
                    col_header_path=grid[0][col_index] if row_index else None,
                    text=text,
                    metadata_json={},
                )
            )
    if profile_type is not None:
        session.add(
            TableSemanticProfile(
                table_id=table.id,
                profile_type=profile_type,
                confidence=0.4,
                review_status="auto",
                metadata_json={},
            )
        )
    session.flush()
    return table


def _seed(session) -> dict:
    """Orphan captions + tables with the two wrong-profile modes from prod."""
    doc = _add_document(session)
    cap_uses = _add_caption(
        session,
        doc.id,
        text="Table 1Z: Permitted uses by zone (DD, DH, and COR)",
        page=45,
    )
    # Misprofiled as 'unknown' — mirrors Table 1D's tables 1065/1066.
    t_uses = _add_matrix_table(
        session, doc.id, page=45, grid=PERMISSION_MATRIX, profile_type="unknown"
    )
    cap_parking = _add_caption(
        session,
        doc.id,
        text="Table 15: Required minimum or maximum number of motor vehicle parking spaces",
        page=60,
    )
    # Wrongly profiled permission_matrix — mirrors tables 1083-1085.
    t_parking = _add_matrix_table(
        session, doc.id, page=60, grid=PARKING_MATRIX, profile_type="permission_matrix"
    )
    return {"doc": doc, "cap_uses": cap_uses, "t_uses": t_uses,
            "cap_parking": cap_parking, "t_parking": t_parking}


def _profile_type(session, table_id: int) -> str | None:
    return session.execute(
        select(TableSemanticProfile.profile_type).where(
            TableSemanticProfile.table_id == table_id
        )
    ).scalar_one_or_none()


def test_dry_run_reports_and_writes_nothing(tmp_path):
    url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(url)
    with session_scope(url) as session:
        seeded = _seed(session)
        stats = backfill(
            session, document_id=seeded["doc"].id, profile="halifax", dry_run=True
        )
        assert stats.captions_linked == 2
        assert seeded["cap_uses"].citation_path is None
        assert seeded["t_uses"].caption is None
        # No enrichment ran: the wrong profiles are untouched.
        assert _profile_type(session, seeded["t_uses"].id) == "unknown"
        assert _profile_type(session, seeded["t_parking"].id) == "permission_matrix"


def test_apply_links_and_reprofiles_both_failure_modes(tmp_path):
    url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(url)
    with session_scope(url) as session:
        seeded = _seed(session)
        backfill(session, document_id=seeded["doc"].id, profile="halifax")

        # Linked + addressable.
        assert seeded["cap_uses"].citation_label == "Table 1Z"
        assert seeded["cap_uses"].citation_path == "[Table 1Z]"
        assert seeded["t_uses"].parent_fragment_id == seeded["cap_uses"].id
        assert seeded["t_uses"].caption.startswith("Table 1Z:")

        # T3 repair: 'unknown' -> permission_matrix with axis bindings, because
        # the classifier now sees "permitted uses by zone" in the caption.
        assert _profile_type(session, seeded["t_uses"].id) == "permission_matrix"
        bindings = session.execute(
            select(TableAxisBinding).where(
                TableAxisBinding.table_id == seeded["t_uses"].id
            )
        ).scalars().all()
        axes = {binding.axis for binding in bindings}
        assert "column" in axes and "row" in axes
        column_labels = {b.raw_label for b in bindings if b.axis == "column"}
        assert "COR" in column_labels

        # Table 15 repair: bogus permission_matrix demoted to parking_matrix.
        assert _profile_type(session, seeded["t_parking"].id) == "parking_matrix"


def test_second_apply_is_noop(tmp_path):
    url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(url)
    with session_scope(url) as session:
        seeded = _seed(session)
        first = backfill(session, document_id=seeded["doc"].id, profile="halifax")
        assert first.writes > 0
        second = backfill(session, document_id=seeded["doc"].id, profile="halifax")
        assert second.writes == 0
        assert second.already_linked == second.captions_seen


def test_revert_restores_citation_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'backfill.db'}"
    create_all(url)
    with session_scope(url) as session:
        seeded = _seed(session)
        stats = backfill(
            session,
            document_id=seeded["doc"].id,
            profile="halifax",
            enrich=False,
        )
        assert seeded["cap_uses"].citation_path is not None
        restored = revert_table_captions(session, stats.touched)
        assert restored > 0
        assert seeded["cap_uses"].citation_path is None
        assert seeded["cap_uses"].citation_label is None
        assert seeded["t_uses"].parent_fragment_id is None
        assert seeded["t_uses"].caption is None
