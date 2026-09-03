"""ABS-518: a chapter that declares other zones is evidence *against* itself.

ABS-500 credited a clause for sitting under a chapter that declares the query's
zone. That is half the rule, and the missing half is what let the advisor answer
an HR-1 question about side and rear setbacks with the ER zones' Table 9.

The Regional Centre states the same rule shape once per built-form chapter, over
different numbers:

    Part V, Chapter 7: Built Form and Siting Requirements within the HR-2 and
                       HR-1 Zones
      198 (1) … the minimum required side setback for any main building shall be
    Part V, Chapter 9: Built Form and Siting Requirements within the ER3, ER-2,
                       and ER-1 Zones
      229 (1) … the minimum required side setback for any main building shall be

Neither section names its own zone — the heading does. So on words alone an
HR-1 side-setback question matches s.229 exactly as well as s.198, and when the
ER side additionally carries ``Table 9: Minimum required side setbacks …``,
whose caption echoes the question, the wrong chapter wins outright. Crediting
the right chapter cannot break that tie, because both candidates are equally
*uncredited*: only one of them is under a heading, and it is the wrong one that
gets the extra caption points.

The rule these tests pin: a heading that names ER and not HR is a positive
statement that its sections do not govern HR-1, and it is debited at the same
structural rung the binding is credited at. A heading that names no zone at all
(``Chapter 1: General Built Form and Siting Requirements``) is silent, not
adverse, and moves nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService
from bylaw_retrieval.retrieval.binding import build_zone_scope_index
from layer1.db.base import (
    Document,
    SemanticEntity,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableSemanticProfile,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

HR_CHAPTER = (
    "Part V, Chapter 7: Built Form and Siting Requirements within the HR-2 "
    "and HR-1 Zones"
)
ER_CHAPTER = (
    "Part V, Chapter 9: Built Form and Siting Requirements within the ER3, "
    "ER-2, and ER-1 Zones"
)
GENERAL_CHAPTER = "Part V, Chapter 1: General Built Form and Siting Requirements"

#: The clause the HR-1 question is asking for. Says nothing about HR-1.
HR_SIDE_SETBACK = (
    "198 (1) Subject to Subsections 198(2) and 198(3), the minimum required "
    "side setback for any main building shall be 0.0 metres where the lot "
    "abuts a lane and 2.5 metres elsewhere."
)
HR_REAR_SETBACK = (
    "199 (1) Subject to Subsections 199(2), 199(3), and 199(4), the minimum "
    "required rear setback for any main building shall be 3.0 metres "
    "elsewhere."
)
#: The ER chapter's counterpart, near-identical prose over different numbers.
ER_SIDE_SETBACK = (
    "229 (1) Subject to Subsections 229(2) and 229(3), the minimum required "
    "side setback for any main building shall be 1.2 metres where the lot "
    "abuts a lane and 4.0 metres elsewhere."
)
#: The ER table's caption, which the corpus also stores as a prose fragment.
#: Note how much of the HR-1 question it repeats.
TABLE_9_CAPTION = (
    "Table 9: Minimum required side setbacks for Established Residential "
    "Special Areas"
)
#: Neither chapter's — a general siting rule that names no zone anywhere.
GENERAL_CLAUSE = (
    "195 With the exception of main buildings within a heritage conservation "
    "district, the minimum required side setback for any main building shall "
    "be measured from the side lot line."
)

HR_SIDE_QUERY = (
    "What is the minimum required side setback for a main building in the "
    "HR-1 zone?"
)
HR_REAR_QUERY = (
    "What is the minimum required rear setback for a main building in the "
    "HR-1 zone?"
)
ER_SIDE_QUERY = (
    "What is the minimum required side setback for a main building in the "
    "ER-1 zone?"
)


def _add_document(session) -> Document:
    document = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="regional-centre.txt",
        source_url=None,
        file_hash="abs518-zone-scope-exclusion",
        version_label=None,
        consolidation_date=None,
        mime_type="text/plain",
        page_count=1,
        parser_version="test",
        retrieval_enabled=True,
    )
    session.add(document)
    session.flush()
    return document


def _add_fragment(
    session,
    document_id: int,
    *,
    text: str,
    fragment_type: FragmentType = FragmentType.SECTION,
    citation_label: str | None = None,
    citation_path: str | None = None,
    parent: SourceFragment | None = None,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        parent_fragment_id=parent.id if parent is not None else None,
        page_start=1,
        page_end=1,
        reading_order_start=1,
        reading_order_end=1,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=[],
        metadata_json={},
        attribute_tags=[],
    )
    session.add(fragment)
    session.flush()
    return fragment


def _add_zone_entity(session, document_id: int, code: str) -> SemanticEntity:
    entity = SemanticEntity(
        document_id=document_id,
        entity_type="zone",
        canonical_name=code,
        aliases_json=[],
        source_text=None,
        confidence=1.0,
        metadata_json={},
    )
    session.add(entity)
    session.flush()
    return entity


def _add_table_9(session, document_id: int, anchor: SourceFragment) -> SourceTable:
    """``Table 9`` as the corpus actually holds it — rows, labels and all.

    Two properties matter and both are copied from the real row, not invented:

    * It has **no axis bindings** and an ``unknown`` profile. The axis-binding
      half of ABS-500 therefore has nothing to say about it: enrichment never
      resolved "Grant Street (GS)" or "North End Halifax 1 (NEH-1)" to a zone,
      because they are not zones. Whatever addresses this table addresses it by
      keyword.
    * One of those row labels ends in a bare **"1"**. That is the whole story
      of how an HR-1 question addressed the ER zones' table: "HR-1" split into
      "hr", "1", and "1" is a whole-word match against "North End Halifax 1".
      With both axes matched the table cleared the channel's admission bar and
      ranked — reported back, in the transcript that opened ABS-518, as
      "ER-zone setback tables (Table 9 — not applicable to HR-1)".
    """
    table = SourceTable(
        document_id=document_id,
        parent_fragment_id=anchor.id,
        caption=TABLE_9_CAPTION,
        page_start=1,
        page_end=1,
        parse_status=ParseStatus.PARSED,
        metadata_json={},
    )
    session.add(table)
    session.flush()
    grid = [
        ["Established Residential Special Area", "Minimum Required Side Setback"],
        ["Grant Street (GS)", "1.5 metres"],
        ["Young Avenue (YA)", "3.0 metres"],
        ["North End Halifax 1 (NEH-1)", "1.5 metres on one side"],
        ["Dartmouth North 2 (DN-2)", "2.5 metres"],
    ]
    for row_index, row in enumerate(grid):
        for col_index, text in enumerate(row):
            session.add(
                SourceTableCell(
                    table_id=table.id,
                    row_index=row_index,
                    col_index=col_index,
                    row_header_path=None,
                    col_header_path=None,
                    text=text,
                    bbox_json=None,
                    metadata_json={},
                )
            )
    session.add(
        TableSemanticProfile(
            table_id=table.id,
            profile_type="unknown",
            row_axis_type=None,
            column_axis_type=None,
            value_type="numeric_or_text",
            metadata_json={},
        )
    )
    session.flush()
    return table


@dataclass(frozen=True)
class SeededCorpus:
    db_url: str
    document_id: int
    hr_chapter_id: int
    er_chapter_id: int
    general_chapter_id: int
    hr_side_id: int
    hr_rear_id: int
    er_side_id: int
    general_id: int
    table_anchor_id: int


@pytest.fixture()
def corpus(tmp_path: Path) -> SeededCorpus:
    """Two built-form chapters saying the same thing about different zones.

    Plus the ER chapter's captioned Table 9, and a zone-silent general chapter
    to prove the debit is not simply "everything that is not the query's
    chapter".
    """
    db_url = f"sqlite:///{tmp_path / 'zone_scope_exclusion.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = _add_document(session)
        for code in ("ER-1", "ER-2", "ER-3", "HR-1", "HR-2"):
            _add_zone_entity(session, document.id, code)

        hr_chapter = _add_fragment(
            session,
            document.id,
            text=HR_CHAPTER,
            fragment_type=FragmentType.PART,
            citation_label="Part V, Chapter 7",
            citation_path="Part V, Chapter 7",
        )
        hr_side = _add_fragment(
            session,
            document.id,
            text=HR_SIDE_SETBACK,
            citation_label="198",
            citation_path="Part V > 198",
            parent=hr_chapter,
        )
        hr_rear = _add_fragment(
            session,
            document.id,
            text=HR_REAR_SETBACK,
            citation_label="199",
            citation_path="Part V > 199",
            parent=hr_chapter,
        )

        er_chapter = _add_fragment(
            session,
            document.id,
            text=ER_CHAPTER,
            fragment_type=FragmentType.PART,
            citation_label="Part V, Chapter 9",
            citation_path="Part V, Chapter 9",
        )
        er_side = _add_fragment(
            session,
            document.id,
            text=ER_SIDE_SETBACK,
            citation_label="229",
            citation_path="Part V > 229",
            parent=er_chapter,
        )
        # The corpus holds a table's caption as a PROSE fragment hanging off
        # the section that introduces it, and that fragment is what a table is
        # cited through. Two levels below the ER chapter, so the debit has to
        # reach it through the ancestor chain rather than the parent alone.
        table_anchor = _add_fragment(
            session,
            document.id,
            text=TABLE_9_CAPTION,
            fragment_type=FragmentType.PROSE,
            citation_label="Table 9",
            citation_path="Part V > [Table 9]",
            parent=er_side,
        )
        _add_table_9(session, document.id, table_anchor)

        general_chapter = _add_fragment(
            session,
            document.id,
            text=GENERAL_CHAPTER,
            fragment_type=FragmentType.PART,
            citation_label="Part V, Chapter 1",
            citation_path="Part V, Chapter 1",
        )
        general = _add_fragment(
            session,
            document.id,
            text=GENERAL_CLAUSE,
            citation_label="195",
            citation_path="Part V > 195",
            parent=general_chapter,
        )

        # Filler, so the document-frequency cut behaves as it does on a real
        # corpus instead of treating every term as ubiquitous.
        for index in range(40):
            _add_fragment(
                session,
                document.id,
                text=(
                    f"6{index:02d} No person shall obstruct a watercourse or "
                    "deposit fill within a riparian buffer."
                ),
                citation_label=f"6{index:02d}",
                citation_path=f"Part XI > 6{index:02d}",
            )

        return SeededCorpus(
            db_url=db_url,
            document_id=document.id,
            hr_chapter_id=hr_chapter.id,
            er_chapter_id=er_chapter.id,
            general_chapter_id=general_chapter.id,
            hr_side_id=hr_side.id,
            hr_rear_id=hr_rear.id,
            er_side_id=er_side.id,
            general_id=general.id,
            table_anchor_id=table_anchor.id,
        )


def _search(corpus: SeededCorpus, query: str, limit: int = 10):
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        return service.search(RetrievalRequest(query=query, limit=limit)).matches


def _ranked(corpus: SeededCorpus, query: str, limit: int = 10) -> list[int]:
    return [match.fragment_id for match in _search(corpus, query, limit)]


def _rank_of(ranked: list[int], fragment_id: int) -> int:
    """1-based rank, or a number past the end when the fragment did not rank."""
    return ranked.index(fragment_id) + 1 if fragment_id in ranked else len(ranked) + 1


def _table_cells(matches) -> list[str]:
    """Every table cell the response offers as an answer, in rank order."""
    return [cell.text for match in matches for cell in (match.table_matches or [])]


def test_the_er_tables_numbers_are_not_offered_for_an_hr1_question(
    corpus: SeededCorpus,
) -> None:
    """No Established Residential number comes back for an HR-1 question.

    This is the failure itself. Before the fix the response's second-ranked
    match was Table 9 carrying the cell "1.5 metres on one side" — the Grant
    Street / North End Halifax standard — offered as an answer about HR-1,
    ahead of s.199. The table was addressed on its row axis by the token "1",
    which existed only because "HR-1" had been split into "hr" and "1", and
    "1" is a whole word in "North End Halifax 1 (NEH-1)".
    """
    cells = _table_cells(_search(corpus, HR_SIDE_QUERY))
    assert cells == []


def test_the_er_chapters_prose_ranks_below_a_zone_silent_clause(
    corpus: SeededCorpus,
) -> None:
    """The debit, measured where it is visible.

    s.229 states the ER side setback in near-identical words to s.198 and, like
    s.198, never names its own zone — so on text alone it outscores the general
    Chapter 1 clause that answers less of the question. Under the debit it
    lands below that clause instead: off-chapter is worse than merely
    unspecific, which is what the heading actually says.
    """
    ranked = _ranked(corpus, HR_SIDE_QUERY)
    assert _rank_of(ranked, corpus.general_id) < _rank_of(ranked, corpus.er_side_id)
    assert _rank_of(ranked, corpus.general_id) < _rank_of(
        ranked, corpus.table_anchor_id
    )


def test_the_hr_sections_lead_the_response_for_their_own_questions(
    corpus: SeededCorpus,
) -> None:
    """The pair the advisor could not surface: s.198 and s.199.

    TC-027 asked for both numbers in one breath, so "the top match is right" is
    the wrong bar — the two sections have to lead *together*, ahead of anything
    the ER chapter offers. They tie on the rear question, which is honest: the
    sections are near-identical prose and the query words do not separate them.
    Being adjacent at the top of the response is what the answer needs.
    """
    for query in (HR_SIDE_QUERY, HR_REAR_QUERY):
        ranked = _ranked(corpus, query)
        assert set(ranked[:2]) == {corpus.hr_side_id, corpus.hr_rear_id}, query
        assert _rank_of(ranked, corpus.hr_rear_id) < _rank_of(
            ranked, corpus.er_side_id
        ), query


def test_the_rule_is_symmetric_not_a_preference_for_the_hr_chapter(
    corpus: SeededCorpus,
) -> None:
    """Asked about ER-1, the same corpus answers with the ER chapter.

    Without this the fix would be indistinguishable from hard-coding the
    chapter the failing case happened to need — and Table 9, which really is
    the ER chapter's table, has to keep ranking for the zone it governs.
    """
    matches = _search(corpus, ER_SIDE_QUERY)
    ranked = [match.fragment_id for match in matches]
    assert ranked[0] == corpus.er_side_id
    assert _rank_of(ranked, corpus.er_side_id) < _rank_of(ranked, corpus.hr_side_id)
    assert _rank_of(ranked, corpus.table_anchor_id) < _rank_of(
        ranked, corpus.hr_side_id
    )


def test_the_table_is_still_reachable_by_a_question_that_addresses_it(
    corpus: SeededCorpus,
) -> None:
    """Dropping the bare ordinal removed a coincidence, not the table.

    A question that names the special area the row is about still retrieves the
    row, with its cell — so the channel that ABS-500 built is intact and it is
    only the spurious address that is gone.
    """
    matches = _search(
        corpus,
        "What is the minimum required side setback for North End Halifax 1?",
    )
    assert matches[0].fragment_id == corpus.table_anchor_id
    assert "1.5 metres on one side" in _table_cells(matches)


def test_a_chapter_naming_no_zone_is_silent_rather_than_adverse(
    corpus: SeededCorpus,
) -> None:
    """The general chapter is neither credited nor debited.

    A debit here would be the fix overreaching: Chapter 1 applies to HR-1 as
    much as to anything else, and the by-law never claims otherwise. So it
    still ranks for an HR-1 question — behind the sections that answer it.
    """
    ranked = _ranked(corpus, HR_SIDE_QUERY)
    assert corpus.general_id in ranked
    assert _rank_of(ranked, corpus.hr_side_id) < _rank_of(ranked, corpus.general_id)


def test_excluding_and_declaring_partition_only_the_zone_naming_containers(
    corpus: SeededCorpus,
) -> None:
    """The index-level contract, stated directly.

    ``containers_excluding`` is the complement of ``containers_declaring`` over
    the *declaring* containers only — a container that names no zone belongs to
    neither set.
    """
    with session_scope(corpus.db_url) as session:
        index = build_zone_scope_index(session, [corpus.document_id])

        hr = frozenset({"HR-1"})
        assert index.containers_declaring(hr) == frozenset({corpus.hr_chapter_id})
        assert index.containers_excluding(hr) == frozenset({corpus.er_chapter_id})

        er = frozenset({"ER-1"})
        assert index.containers_declaring(er) == frozenset({corpus.er_chapter_id})
        assert index.containers_excluding(er) == frozenset({corpus.hr_chapter_id})

        # The zone-silent chapter is in neither, and a query that named no zone
        # binds and debits nothing.
        assert corpus.general_chapter_id not in index.containers_excluding(hr)
        assert corpus.general_chapter_id not in index.containers_declaring(hr)
        assert index.containers_excluding(frozenset()) == frozenset()


def test_binding_wins_when_a_fragment_is_under_both(corpus: SeededCorpus) -> None:
    """Nesting is not a contradiction — the declaring container is the specific
    statement.

    A section can sit under a broad heading that lists one set of zones and a
    narrower chapter that lists another. The narrower one is the section's own,
    and the adjustment must credit rather than debit it.
    """
    service = RetrievalService.__new__(RetrievalService)
    declaring = frozenset({10})
    excluding = frozenset({20})
    assert (
        service._zone_scope_adjustment(99, (10, 20), declaring, excluding)
        == RetrievalService._ZONE_SCOPE_BINDING_SCORE
    )
    assert (
        service._zone_scope_adjustment(99, (20,), declaring, excluding)
        == -RetrievalService._ZONE_SCOPE_EXCLUSION_PENALTY
    )
    assert service._zone_scope_adjustment(99, (30,), declaring, excluding) == 0.0
