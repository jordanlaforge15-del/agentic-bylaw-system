"""Unit coverage for the table-caption linking pass (ABS-409).

Seeds sqlite databases mirroring the orphan state the Regional Centre LUB
ingested into: caption text as unaddressed PROSE fragments
(``citation_path=NULL``), ``source_table`` rows with ``caption=NULL`` /
``parent_fragment_id=NULL``. Exercises every claiming mode the corpus
exhibits — the multi-page Table 1A run, the same-page caption pairs
(Tables 2/3 on one page), HEADING-typed captions (Table 10), the adjacency
boundary between consecutive captioned tables — plus the safety properties:
idempotency, dry-run, collision skip, no-overwrite, and sidecar revert.
"""
from __future__ import annotations

from pathlib import Path

from layer1.db.base import Document, SourceFragment, SourceTable
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.table_captions import link_table_captions, revert_table_captions
from layer1.profiles import HALIFAX_PROFILE, ParsingProfile


def _db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path}/table_captions.db"
    create_all(url)
    return url


def _add_document(session) -> Document:
    doc = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="rc.pdf",
        file_hash="rc-test",
        mime_type="application/pdf",
        page_count=200,
        parser_version="docling:halifax",
    )
    session.add(doc)
    session.flush()
    return doc


def _add_fragment(
    session,
    document_id: int,
    *,
    text: str,
    page: int,
    fragment_type: FragmentType = FragmentType.PROSE,
    citation_path: str | None = None,
    citation_label: str | None = None,
    order: int = 1,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        page_start=page,
        page_end=page,
        reading_order_start=order,
        reading_order_end=order,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _add_table(
    session,
    document_id: int,
    *,
    page: int,
    caption: str | None = None,
    parent_fragment_id: int | None = None,
) -> SourceTable:
    table = SourceTable(
        document_id=document_id,
        parent_fragment_id=parent_fragment_id,
        page_start=page,
        page_end=page,
        parse_status=ParseStatus.PARSED,
        caption=caption,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    return table


def _seed_regional_centre_shape(session) -> dict:
    """The corpus shape the fix targets, in miniature.

    Page 44: an addressed section (supplies the "Part I" prefix).
    Page 45: "Table 1A" caption + table, continuations on 46-47.
    Page 48: "Table 1B" caption + table, continuation on 49 — the adjacency
             boundary: Table 1A's run must stop at page 48.
    Page 60: "Table 15" parking caption + table (blast-radius coverage).
    """
    doc = _add_document(session)
    _add_fragment(
        session,
        doc.id,
        text="39 A publicly sponsored convention centre …",
        page=44,
        fragment_type=FragmentType.SECTION,
        citation_path="Part I > 39",
        citation_label="39",
    )
    cap_1a = _add_fragment(
        session,
        doc.id,
        text="Table 1A: Permitted uses by zone (DD, DH, CEN-2, CEN-1, COR, HR-2, and HR-1)",
        page=45,
    )
    t45 = _add_table(session, doc.id, page=45)
    t46 = _add_table(session, doc.id, page=46)
    t47 = _add_table(session, doc.id, page=47)
    cap_1b = _add_fragment(
        session,
        doc.id,
        text="Table 1B: Permitted uses by zone (ER-3, ER-2, ER-1, CH-2, and CH-1)",
        page=48,
    )
    t48 = _add_table(session, doc.id, page=48)
    t49 = _add_table(session, doc.id, page=49)
    cap_15 = _add_fragment(
        session,
        doc.id,
        text="Table 15: Required minimum or maximum number of motor vehicle parking spaces",
        page=60,
    )
    t60 = _add_table(session, doc.id, page=60)
    return {
        "doc": doc,
        "cap_1a": cap_1a,
        "cap_1b": cap_1b,
        "cap_15": cap_15,
        "t45": t45,
        "t46": t46,
        "t47": t47,
        "t48": t48,
        "t49": t49,
        "t60": t60,
    }


def test_multi_page_run_links_caption_and_claims_continuations(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        seeded = _seed_regional_centre_shape(session)
        stats = link_table_captions(
            session, document_id=seeded["doc"].id, profile=HALIFAX_PROFILE
        )
        assert stats.captions_seen == 3
        assert stats.captions_linked == 3
        assert stats.ambiguous_skipped == 0

        cap_1a = seeded["cap_1a"]
        assert cap_1a.citation_label == "Table 1A"
        assert cap_1a.citation_path == "Part I > [Table 1A]"
        # Table 1A claims pages 45-47; the run stops at Table 1B's page.
        for key in ("t45", "t46", "t47"):
            assert seeded[key].parent_fragment_id == cap_1a.id
            assert seeded[key].caption.startswith("Table 1A:")
        cap_1b = seeded["cap_1b"]
        assert cap_1b.citation_path == "Part I > [Table 1B]"
        for key in ("t48", "t49"):
            assert seeded[key].parent_fragment_id == cap_1b.id
        # Blast radius: the parking caption links too, with its own text.
        assert seeded["t60"].parent_fragment_id == seeded["cap_15"].id
        assert "parking" in seeded["t60"].caption.lower()


def test_page_span_caps_the_run(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        cap = _add_fragment(session, doc.id, text="Table 1D: Permitted uses by zone (HCD-SV)", page=54)
        t54 = _add_table(session, doc.id, page=54)
        t56 = _add_table(session, doc.id, page=56)
        t57 = _add_table(session, doc.id, page=57)  # beyond span 2 — unclaimed
        link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert t54.parent_fragment_id == cap.id
        assert t56.parent_fragment_id == cap.id
        assert t57.parent_fragment_id is None


def test_same_page_captions_pair_when_counts_match(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        cap2 = _add_fragment(session, doc.id, text="Table 2: Rooming unit floor area", page=96, order=1)
        cap3 = _add_fragment(session, doc.id, text="Table 3: Shared kitchen requirements", page=96, order=2)
        t_a = _add_table(session, doc.id, page=96)
        t_b = _add_table(session, doc.id, page=96)
        stats = link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert stats.ambiguous_skipped == 0
        assert t_a.parent_fragment_id == cap2.id
        assert t_b.parent_fragment_id == cap3.id
        assert t_a.caption.startswith("Table 2:")
        assert t_b.caption.startswith("Table 3:")


def test_same_page_captions_skip_when_counts_mismatch(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        _add_fragment(session, doc.id, text="Table 2: Rooming unit floor area", page=96, order=1)
        _add_fragment(session, doc.id, text="Table 3: Shared kitchen requirements", page=96, order=2)
        lone = _add_table(session, doc.id, page=96)
        stats = link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert stats.ambiguous_skipped == 2
        assert stats.captions_linked == 0
        assert lone.parent_fragment_id is None
        assert lone.caption is None
        actions = {entry["action"] for entry in stats.mapping}
        assert actions == {"ambiguous_skip"}


def test_heading_typed_caption_links(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        cap = _add_fragment(
            session,
            doc.id,
            text="Table 10: Maximum required lot coverage",
            page=191,
            fragment_type=FragmentType.HEADING,
        )
        t = _add_table(session, doc.id, page=191)
        link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert cap.citation_label == "Table 10"
        assert t.parent_fragment_id == cap.id


def test_adjacent_caption_does_not_steal_next_tables(tmp_path):
    """Table 9's run must not claim Table 10's table on the following page."""
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        cap9 = _add_fragment(session, doc.id, text="Table 9: Minimum landscaped area", page=190)
        t9 = _add_table(session, doc.id, page=190)
        cap10 = _add_fragment(
            session,
            doc.id,
            text="Table 10: Maximum required lot coverage",
            page=191,
            fragment_type=FragmentType.HEADING,
        )
        t10 = _add_table(session, doc.id, page=191)
        link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert t9.parent_fragment_id == cap9.id
        assert t10.parent_fragment_id == cap10.id


def test_collision_skips_path_but_still_claims_tables(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        _add_fragment(
            session,
            doc.id,
            text="5 General provisions",
            page=10,
            fragment_type=FragmentType.SECTION,
            citation_path="Part I > 5",
            citation_label="5",
        )
        # An unrelated fragment already owns the path the caption would get.
        _add_fragment(
            session,
            doc.id,
            text="unrelated",
            page=11,
            fragment_type=FragmentType.SECTION,
            citation_path="Part I > [Table 1A]",
            citation_label="Table 1A",
        )
        cap = _add_fragment(session, doc.id, text="Table 1A: Permitted uses by zone (COR)", page=45)
        t = _add_table(session, doc.id, page=45)
        stats = link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert stats.collisions_skipped == 1
        assert cap.citation_path is None
        # The classifier fix (caption on the table) still lands.
        assert t.parent_fragment_id == cap.id
        assert t.caption.startswith("Table 1A:")


def test_never_overwrites_existing_values(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        doc = _add_document(session)
        cap = _add_fragment(
            session,
            doc.id,
            text="Table 1A: Permitted uses by zone (COR)",
            page=45,
            citation_path="Part II > [Table 1A]",
            citation_label="Table 1A",
        )
        t = _add_table(
            session, doc.id, page=45, caption="Hand-set caption", parent_fragment_id=None
        )
        stats = link_table_captions(session, document_id=doc.id, profile=HALIFAX_PROFILE)
        assert cap.citation_path == "Part II > [Table 1A]"  # untouched
        assert stats.already_linked == 1
        assert t.parent_fragment_id == cap.id  # NULL parent still fills in
        assert t.caption == "Hand-set caption"  # non-NULL caption untouched


def test_idempotent_second_run_writes_nothing(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        seeded = _seed_regional_centre_shape(session)
        first = link_table_captions(session, document_id=seeded["doc"].id, profile=HALIFAX_PROFILE)
        assert first.writes > 0
        second = link_table_captions(session, document_id=seeded["doc"].id, profile=HALIFAX_PROFILE)
        assert second.writes == 0
        assert second.captions_linked == 0
        assert second.already_linked == second.captions_seen


def test_dry_run_writes_nothing_but_reports_mapping(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        seeded = _seed_regional_centre_shape(session)
        stats = link_table_captions(
            session, document_id=seeded["doc"].id, profile=HALIFAX_PROFILE, dry_run=True
        )
        assert stats.dry_run is True
        assert stats.captions_linked == 3
        assert stats.tables_claimed == 6
        assert seeded["cap_1a"].citation_path is None
        assert seeded["t45"].parent_fragment_id is None
        assert seeded["t45"].caption is None
        labels = {entry["label"] for entry in stats.mapping}
        assert labels == {"Table 1A", "Table 1B", "Table 15"}
        assert not stats.touched["fragments"] and not stats.touched["tables"]


def test_sidecar_revert_restores_before_state(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        seeded = _seed_regional_centre_shape(session)
        stats = link_table_captions(session, document_id=seeded["doc"].id, profile=HALIFAX_PROFILE)
        assert seeded["cap_1a"].citation_path is not None
        # Round-trip through JSON-ish string keys, as the backfill sidecar does.
        touched = {
            kind: {str(row_id): before for row_id, before in rows.items()}
            for kind, rows in stats.touched.items()
        }
        restored = revert_table_captions(session, touched)
        assert restored > 0
        assert seeded["cap_1a"].citation_path is None
        assert seeded["cap_1a"].citation_label is None
        for key in ("t45", "t46", "t47", "t48", "t49", "t60"):
            assert seeded[key].parent_fragment_id is None
            assert seeded[key].caption is None


def test_profile_without_convention_is_skipped(tmp_path):
    url = _db_url(tmp_path)
    with session_scope(url) as session:
        seeded = _seed_regional_centre_shape(session)
        stats = link_table_captions(
            session,
            document_id=seeded["doc"].id,
            profile=ParsingProfile(name="default"),
        )
        assert stats.captions_seen == 0
        assert stats.writes == 0
        assert seeded["cap_1a"].citation_path is None
