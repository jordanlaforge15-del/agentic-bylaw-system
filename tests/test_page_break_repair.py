"""Coverage for the ABS-461 corpus repair.

Seeds a sqlite database mirroring the exact dev-corpus damage around clause
198(1) of the Regional Centre LUB (document_id=4, fragments 7119-7133) plus the
Mainland LUB's amendment-log false positive, and verifies the repair end to end.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.page_break_repair import (
    REPAIR_MARKER,
    find_page_break_splits,
    repair_page_break_splits,
    revert_page_break_splits,
)

CLAUSE_A_TRUNCATED = (
    "(a) subject to Clauses 198(1)(b) and 198(1)(c), where a lot line abuts a lot, "
    "any portion of which, is zoned ER-3, ER-"
)
CONTINUATION = "2, ER-1, CH-2, CH-1, PCF, or RPK zone: (RCCC-Sep 4/24;E-Apr 17/25)"
SIDE_SETBACK = "[Side Setback Requirements]"


def _document(session, *, bylaw_name: str = "Regional Centre Land Use By-Law") -> Document:
    doc = Document(
        municipality="HRM",
        bylaw_name=bylaw_name,
        source_path=f"{bylaw_name}.pdf",
        file_hash=f"abs461-{bylaw_name}",
        mime_type="application/pdf",
        page_count=457,
        parser_version="docling:halifax",
    )
    session.add(doc)
    session.flush()
    return doc


def _fragment(
    session,
    document_id: int,
    *,
    fragment_type: FragmentType,
    text: str,
    order: int,
    page: int,
    label: str | None = None,
    path: str | None = None,
    parent_id: int | None = None,
    block_type: str = "list_item",
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=label,
        citation_path=path,
        parent_fragment_id=parent_id,
        page_start=page,
        page_end=page,
        reading_order_start=order,
        reading_order_end=order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=0.9,
        source_block_ids_json=[order],
        metadata_json={"block_type": block_type},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _seed_side_setback_damage(session) -> tuple[int, dict[str, int]]:
    """Reproduce fragments 7119-7133 as the defective parser left them."""
    doc = _document(session)
    part = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.PART,
        text="Part V Land Use",
        order=1700,
        page=170,
        label="Part V",
        path="Part V",
        block_type="heading",
    )
    heading = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.HEADING,
        text="Side Setback Requirements",
        order=1751,
        page=171,
        parent_id=part.id,
        block_type="heading",
    )
    section = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.SECTION,
        text="198 (1) Subject to Subsections 198(2) and 198(3), the minimum required side "
        "setback for any main building shall be:",
        order=1752,
        page=171,
        label="198",
        path="Part V > 198",
        parent_id=part.id,
    )
    clause_a = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.CLAUSE,
        text=CLAUSE_A_TRUNCATED,
        order=1753,
        page=171,
        label="(a)",
        path=f"Part V > 198 > {SIDE_SETBACK} > (a)",
        parent_id=heading.id,
    )
    phantom = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.SECTION,
        text=CONTINUATION,
        order=1754,
        page=172,
        label="2",
        path="Part V > 2",
        parent_id=part.id,
    )
    orphan_child = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.LIST_ITEM,
        text="Underground parking structures are not required to have a minimum side setback.",
        order=1755,
        page=172,
        parent_id=phantom.id,
    )
    ids = {
        "part": part.id,
        "heading": heading.id,
        "section": section.id,
        "clause_a": clause_a.id,
        "phantom": phantom.id,
        "orphan_child": orphan_child.id,
    }
    for order, (label, body) in enumerate(
        [
            ("(b)", "(b) for a townhouse dwelling use:"),
            ("(c)", "(c) for a semi-detached dwelling use or duplex apartment use:"),
            ("(d)", "(d) where a lot line abuts a lot ... zoned DD, DH, CEN-2, CEN-1, or COR zone, 0.0 metre;"),
            ("(e)", "(e) where a lot line abuts lands governed by the Downtown Halifax SMPS, 0.0 metre; or"),
            ("(f)", "(f) 2.5 metres elsewhere."),
        ],
        start=1,
    ):
        clause = _fragment(
            session,
            doc.id,
            fragment_type=FragmentType.CLAUSE,
            text=body,
            order=1755 + order * 2,
            page=172,
            label=label,
            path=f"Part V > 2 > {SIDE_SETBACK} > {label}",
            parent_id=heading.id,
        )
        ids[label] = clause.id
    sub_i = _fragment(
        session,
        doc.id,
        fragment_type=FragmentType.SUBCLAUSE,
        text="(i) 0.0 metre along a common wall between each unit, or",
        order=1758,
        page=172,
        label="(i)",
        path=f"Part V > 2 > {SIDE_SETBACK} > (b) > {SIDE_SETBACK} > (i)",
        parent_id=ids["(b)"],
    )
    ids["(b)(i)"] = sub_i.id
    return doc.id, ids


def test_dry_run_reports_the_split_without_writing(tmp_path):
    url = f"sqlite:///{tmp_path / 'repair.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, ids = _seed_side_setback_damage(session)

    with session_scope(url) as session:
        stats = repair_page_break_splits(session, document_id=document_id, dry_run=True)

    assert len(stats.splits) == 1
    (split,) = stats.splits
    assert (split.head_id, split.tail_id) == (ids["clause_a"], ids["phantom"])
    assert split.phantom_path == "Part V > 2"
    assert split.replacement_prefix == "Part V > 198"
    assert split.head_text_after.endswith("or RPK zone: (RCCC-Sep 4/24;E-Apr 17/25)")
    assert not split.unresolved
    # Six paths move: the five clauses (b)-(f) plus (b)(i).
    assert len(split.rewrites) == 6

    with session_scope(url) as session:
        phantom = session.get(SourceFragment, ids["phantom"])
        assert phantom is not None
        assert session.get(SourceFragment, ids["clause_a"]).text == CLAUSE_A_TRUNCATED


def test_repair_removes_the_phantom_and_rehomes_its_clauses(tmp_path):
    url = f"sqlite:///{tmp_path / 'repair.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, ids = _seed_side_setback_damage(session)

    with session_scope(url) as session:
        stats = repair_page_break_splits(session, document_id=document_id)
        assert stats.phantom_sections_removed == 1
        assert stats.paths_rewritten == 6

    with session_scope(url) as session:
        # DoD 1: nothing is left under the phantom.
        remaining = session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path.like("Part V > 2%"),
            )
        ).scalars().all()
        assert remaining == []
        assert session.get(SourceFragment, ids["phantom"]) is None

        # DoD 3: the clause carries the complete zone list.
        clause_a = session.get(SourceFragment, ids["clause_a"])
        assert "ER-3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone:" in clause_a.text
        assert not clause_a.text.rstrip().endswith("ER-")
        assert (clause_a.page_start, clause_a.page_end) == (171, 172)
        assert clause_a.source_block_ids_json == [1753, 1754]
        assert clause_a.metadata_json[REPAIR_MARKER] == ids["phantom"]

        # DoD 2: (b)-(f) sit under Part V > 198, matching (a).
        for label in ("(b)", "(c)", "(d)", "(e)", "(f)"):
            clause = session.get(SourceFragment, ids[label])
            assert clause.citation_path == f"Part V > 198 > {SIDE_SETBACK} > {label}"
        sub_clause = session.get(SourceFragment, ids["(b)(i)"])
        assert sub_clause.citation_path == (
            f"Part V > 198 > {SIDE_SETBACK} > (b) > {SIDE_SETBACK} > (i)"
        )

        # The phantom's child follows the text it belongs to.
        assert session.get(SourceFragment, ids["orphan_child"]).parent_fragment_id == ids["clause_a"]


def test_repair_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'repair.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, _ = _seed_side_setback_damage(session)
    with session_scope(url) as session:
        repair_page_break_splits(session, document_id=document_id)
    with session_scope(url) as session:
        second = repair_page_break_splits(session, document_id=document_id)
    assert second.splits == []


def test_revert_restores_the_pre_repair_state(tmp_path):
    """The sidecar has to put the phantom back, id and all."""
    url = f"sqlite:///{tmp_path / 'repair.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, ids = _seed_side_setback_damage(session)

    with session_scope(url) as session:
        payload = json.loads(
            json.dumps(repair_page_break_splits(session, document_id=document_id).revert_payload)
        )

    with session_scope(url) as session:
        revert_page_break_splits(session, payload)

    with session_scope(url) as session:
        phantom = session.get(SourceFragment, ids["phantom"])
        assert phantom is not None
        assert phantom.citation_path == "Part V > 2"
        assert phantom.text == CONTINUATION
        assert phantom.fragment_type == FragmentType.SECTION

        clause_a = session.get(SourceFragment, ids["clause_a"])
        assert clause_a.text == CLAUSE_A_TRUNCATED
        assert clause_a.page_end == 171
        assert clause_a.source_block_ids_json == [1753]
        assert REPAIR_MARKER not in clause_a.metadata_json

        assert session.get(SourceFragment, ids["(f)"]).citation_path == (
            f"Part V > 2 > {SIDE_SETBACK} > (f)"
        )
        assert session.get(SourceFragment, ids["orphan_child"]).parent_fragment_id == ids["phantom"]

    # And the repair can be applied again on top of a reverted database.
    with session_scope(url) as session:
        assert len(repair_page_break_splits(session, document_id=document_id).splits) == 1


def test_adjacent_table_cells_are_not_treated_as_a_continuation(tmp_path):
    """The Mainland LUB amendment log ends cells on a hyphen mid-column.

    "Added R-4B (Dunbrack Multi-" is followed by "Case 22332" from the next
    column, not by its own continuation. Both are HEADING blocks, which is what
    keeps them out of scope.
    """
    url = f"sqlite:///{tmp_path / 'repair.db'}"
    create_all(url)
    with session_scope(url) as session:
        doc = _document(session, bylaw_name="Halifax Mainland Land Use By-law")
        _fragment(
            session,
            doc.id,
            fragment_type=FragmentType.HEADING,
            text="Added R-4B (Dunbrack Multi-",
            order=2536,
            page=208,
            block_type="heading",
        )
        _fragment(
            session,
            doc.id,
            fragment_type=FragmentType.HEADING,
            text="Case 22332",
            order=2537,
            page=208,
            block_type="heading",
        )
        document_id = doc.id

    with session_scope(url) as session:
        assert find_page_break_splits(session, document_id=document_id) == []


def test_split_without_a_recoverable_prefix_is_flagged_not_guessed(tmp_path):
    """A phantom with no real section ahead of it is spliced but reported.

    The text repair is always safe; rehoming needs an anchor. Rather than
    inventing one, the split is marked ``unresolved`` so the operator sees it.
    """
    url = f"sqlite:///{tmp_path / 'repair.db'}"
    create_all(url)
    with session_scope(url) as session:
        doc = _document(session)
        head = _fragment(
            session,
            doc.id,
            fragment_type=FragmentType.LIST_ITEM,
            text="a lot containing an ER-",
            order=10,
            page=5,
        )
        _fragment(
            session,
            doc.id,
            fragment_type=FragmentType.SECTION,
            text="3, ER-2, ER-1 zone.",
            order=11,
            page=6,
            label="3",
            path="Part V > 3",
        )
        document_id, head_id = doc.id, head.id

    with session_scope(url) as session:
        stats = repair_page_break_splits(session, document_id=document_id)
        (split,) = stats.splits
        assert split.unresolved
        assert split.rewrites == []

    with session_scope(url) as session:
        assert session.get(SourceFragment, head_id).text == "a lot containing an ER-3, ER-2, ER-1 zone."
