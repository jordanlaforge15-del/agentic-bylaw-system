"""Coverage for the ABS-488 corpus repath migration.

Seeds a sqlite database mirroring the exact dev-corpus damage around section 9
of the Regional Centre LUB (document_id=4, fragments 5450-5473): two clause
groups whose paths collided and were blanked, and a chapter heading that parsed
as the bare "Part I". Verifies the migration recovers them, holds the unique
constraint, and reverts to the byte-identical starting state.
"""
from __future__ import annotations

from sqlalchemy import select

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.corpus_repath import (
    DUPLICATE_KEY,
    REPATH_MARKER,
    repath_corpus,
    revert_corpus_repath,
)

COLLIDED = "Part I > 9 > [Development Permit Exemptions] > (a)"
COLLIDED_B = "Part I > 9 > [Development Permit Exemptions] > (b)"
HERITAGE_STEM = "On a registered heritage property, a development permit is required for:"


def _document(session) -> Document:
    doc = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="regional-centre.pdf",
        file_hash="abs488-regional-centre",
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
    label: str | None = None,
    path: str | None = None,
    metadata: dict | None = None,
    status: ParseStatus = ParseStatus.PARSED,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=label,
        citation_path=path,
        page_start=1,
        page_end=1,
        reading_order_start=order,
        reading_order_end=order,
        text=text,
        parse_status=status,
        confidence=0.85,
        source_block_ids_json=[order],
        metadata_json={"block_type": "list_item", **(metadata or {})},
    )
    session.add(fragment)
    session.flush()
    return fragment


def _seed_section_nine_damage(session) -> tuple[int, dict[str, int]]:
    doc = _document(session)
    ids: dict[str, int] = {}
    ids["part"] = _fragment(
        session, doc.id, fragment_type=FragmentType.PART, text="Part I: Administration",
        order=10, label="Part I", path="Part I",
    ).id
    ids["chapter"] = _fragment(
        session, doc.id, fragment_type=FragmentType.PART,
        text="Part I, Chapter 2: Development Permit", order=20, label="Part I",
        metadata={DUPLICATE_KEY: "Part I"},
    ).id
    _fragment(
        session, doc.id, fragment_type=FragmentType.HEADING,
        text="Development Permit Exemptions", order=30,
    )
    ids["section"] = _fragment(
        session, doc.id, fragment_type=FragmentType.SECTION,
        text="9 (1) The following developments are exempt:", order=40, label="9", path="Part I > 9",
    ).id
    ids["a1"] = _fragment(
        session, doc.id, fragment_type=FragmentType.CLAUSE,
        text="(a) accessory structures of 20.0 square metres or less;", order=50,
        label="(a)", metadata={DUPLICATE_KEY: COLLIDED},
    ).id
    ids["b1"] = _fragment(
        session, doc.id, fragment_type=FragmentType.CLAUSE,
        text="(b) kiosks of 20.0 square metres or less.", order=60,
        label="(b)", metadata={DUPLICATE_KEY: COLLIDED_B},
    ).id
    ids["stem"] = _fragment(
        session, doc.id, fragment_type=FragmentType.LIST_ITEM, text=HERITAGE_STEM, order=70,
    ).id
    ids["a2"] = _fragment(
        session, doc.id, fragment_type=FragmentType.CLAUSE,
        text="(a) uncovered structures less than 0.6 metre in height;", order=80,
        label="(a)", metadata={DUPLICATE_KEY: COLLIDED},
    ).id
    ids["b2"] = _fragment(
        session, doc.id, fragment_type=FragmentType.CLAUSE,
        text="(b) fences.", order=90, label="(b)", metadata={DUPLICATE_KEY: COLLIDED_B},
    ).id
    return doc.id, ids


def _paths(session, document_id: int) -> dict[int, str | None]:
    rows = session.execute(
        select(SourceFragment).where(SourceFragment.document_id == document_id)
    ).scalars()
    return {row.id: row.citation_path for row in rows}


def test_repath_recovers_every_blanked_clause(tmp_path):
    url = f"sqlite:///{tmp_path/'repath.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, ids = _seed_section_nine_damage(session)
        stats = repath_corpus(session, document_id=document_id)
        paths = _paths(session, document_id)

    document = stats.documents[0]
    assert document.citable_before == 5, "4 clauses + the chapter heading start unreachable"
    assert document.citable_after == 0
    assert document.recovered == 5
    assert paths[ids["a1"]] == "Part I > 9 > (a)"
    assert paths[ids["b1"]] == "Part I > 9 > (b)"
    assert paths[ids["a2"]] == f"Part I > 9 > [{HERITAGE_STEM.rstrip(':')}] > (a)"
    assert paths[ids["b2"]] == f"Part I > 9 > [{HERITAGE_STEM.rstrip(':')}] > (b)"
    assert paths[ids["chapter"]] == "Part I, Chapter 2"
    assert paths[ids["part"]] == "Part I", "the un-chaptered Part heading must not move"
    assert paths[ids["section"]] == "Part I > 9", "sections keep citing the bare Part"


def test_repath_clears_the_collision_marker_it_resolved(tmp_path):
    url = f"sqlite:///{tmp_path/'marker.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, ids = _seed_section_nine_damage(session)
        repath_corpus(session, document_id=document_id)
        recovered = session.get(SourceFragment, ids["a2"])
        assert DUPLICATE_KEY not in recovered.metadata_json
        assert recovered.metadata_json[REPATH_MARKER] is True
        assert recovered.parse_status == ParseStatus.PARSED


def test_dry_run_writes_nothing(tmp_path):
    url = f"sqlite:///{tmp_path/'dry.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, _ = _seed_section_nine_damage(session)
        before = _paths(session, document_id)
        stats = repath_corpus(session, document_id=document_id, dry_run=True)
        assert stats.rows_changed == 5
        assert not stats.revert_payload
        assert _paths(session, document_id) == before


def test_revert_restores_the_pre_repath_state(tmp_path):
    url = f"sqlite:///{tmp_path/'revert.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, ids = _seed_section_nine_damage(session)
        before = {
            row_id: (
                session.get(SourceFragment, row_id).citation_label,
                session.get(SourceFragment, row_id).citation_path,
                session.get(SourceFragment, row_id).parse_status,
                dict(session.get(SourceFragment, row_id).metadata_json),
            )
            for row_id in ids.values()
        }
        stats = repath_corpus(session, document_id=document_id)
        assert revert_corpus_repath(session, stats.revert_payload) == stats.rows_changed
        for row_id, expected in before.items():
            fragment = session.get(SourceFragment, row_id)
            assert (
                fragment.citation_label,
                fragment.citation_path,
                fragment.parse_status,
                dict(fragment.metadata_json),
            ) == expected


def test_repath_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path/'idempotent.db'}"
    create_all(url)
    with session_scope(url) as session:
        document_id, _ = _seed_section_nine_damage(session)
        repath_corpus(session, document_id=document_id)
        again = repath_corpus(session, document_id=document_id)
        assert again.rows_changed == 0


def test_a_path_that_still_collides_stays_blanked_and_recorded(tmp_path):
    """Two verbatim-identical stems cannot be told apart; the audit trail says so."""
    url = f"sqlite:///{tmp_path/'collide.db'}"
    create_all(url)
    with session_scope(url) as session:
        doc = _document(session)
        _fragment(
            session, doc.id, fragment_type=FragmentType.SECTION,
            text="24 Non-conforming structures.", order=10, label="24", path="Part I > 24",
        )
        for order in (20, 40):
            _fragment(
                session, doc.id, fragment_type=FragmentType.LIST_ITEM,
                text="Where a non-conforming use exists, providing:", order=order,
            )
            _fragment(
                session, doc.id, fragment_type=FragmentType.CLAUSE,
                text="(a) the structure is located in an ER-3 zone;", order=order + 10, label="(a)",
            )
        stats = repath_corpus(session, document_id=doc.id)
        blanked = [
            row
            for row in session.execute(
                select(SourceFragment).where(SourceFragment.fragment_type == FragmentType.CLAUSE)
            ).scalars()
        ]
        assert stats.documents[0].collided_paths
        assert all(row.citation_path is None for row in blanked)
        assert all(row.metadata_json[DUPLICATE_KEY].endswith("> (a)") for row in blanked)
        # ABS-480: a collision is a naming failure, not a parse failure.
        assert all(row.parse_status == ParseStatus.PARSED for row in blanked)
