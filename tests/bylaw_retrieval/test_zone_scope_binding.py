"""ABS-500: a clause is scored under the zone its chapter declares.

The measurement that opened the issue said tables were the dimensional gap.
Reading the ABS-486 labels says otherwise: 17 of the 18 dimensional questions
are answered by a *prose section*, and those sections never name the zone. The
Regional Centre states it once, in the chapter heading:

    Part V, Chapter 9: Built Form and Siting Requirements within the ER3,
                       ER-2, and ER-1 Zones
      231 (1) … the maximum required lot coverage shall be:

Meanwhile dozens of clauses elsewhere list ER-1 among *abutting* zones. Before
this change the passing mention scored +4 (own text) and the governing chapter
+2 (inherited context), so a landscaping clause about abutting land outranked
the section stating the ER-1 standard — which is why the class sat at Recall@10
= 0.056 and no amount of channel re-weighting moved it.

These tests pin the rule and the three things that keep it honest:

* the governed clause outranks the clause that merely mentions the zone;
* the declaring container is bound to its own zone, so a question asking *for
  the chapter* is not buried under the sections it scopes;
* a query naming no zone binds nothing, and a zone code the corpus never
  extracted is not a binding term.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService
from bylaw_retrieval.retrieval.binding import build_zone_scope_index
from layer1.db.base import Document, SemanticEntity, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

ZONE_CHAPTER = (
    "Part V, Chapter 9: Built Form and Siting Requirements within the ER3, "
    "ER-2, and ER-1 Zones"
)
#: The section that answers "maximum lot coverage in ER-1" — and never says
#: "ER-1".
GOVERNED_SECTION = (
    "231 (1) Subject to Subsections 231(2) and 231(3), the maximum required "
    "lot coverage shall be 45%."
)
#: A clause that says "ER-1" and answers nothing, in the shape the corpus
#: produces them: an abutting-zone rule under an unrelated Part.
MENTIONING_CLAUSE = (
    "425 (1) Where a lot abuts another lot, any portion of which, is zoned "
    "HR-2, HR-1, ER-3, ER-2 or ER-1, a landscaped buffer shall be provided."
)

ZONE_QUERY = "What is the maximum required lot coverage in the ER-1 zone?"


def _add_document(session) -> Document:
    document = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="regional-centre.txt",
        source_url=None,
        file_hash="abs500-zone-scope",
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


def _add_zone_entity(session, document_id: int, code: str) -> None:
    session.add(
        SemanticEntity(
            document_id=document_id,
            entity_type="zone",
            canonical_name=code,
            aliases_json=[],
            source_text=None,
            confidence=1.0,
            metadata_json={},
        )
    )
    session.flush()


@dataclass(frozen=True)
class SeededCorpus:
    db_url: str
    document_id: int
    chapter_id: int
    governed_id: int
    mentioning_id: int


@pytest.fixture()
def corpus(tmp_path: Path) -> SeededCorpus:
    db_url = f"sqlite:///{tmp_path / 'zone_scope.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = _add_document(session)
        for code in ("ER-1", "ER-2", "ER-3", "HR-1", "HR-2", "COR"):
            _add_zone_entity(session, document.id, code)

        chapter = _add_fragment(
            session,
            document.id,
            text=ZONE_CHAPTER,
            fragment_type=FragmentType.PART,
            citation_label="Part V, Chapter 9",
            citation_path="Part V, Chapter 9",
        )
        governed = _add_fragment(
            session,
            document.id,
            text=GOVERNED_SECTION,
            citation_label="231",
            citation_path="Part V > 231",
            parent=chapter,
        )
        mentioning = _add_fragment(
            session,
            document.id,
            text=MENTIONING_CLAUSE,
            citation_label="425",
            citation_path="Part X > 425",
        )
        # Filler so the document-frequency cut behaves like it does on a real
        # corpus rather than treating every term as ubiquitous.
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
            chapter_id=chapter.id,
            governed_id=governed.id,
            mentioning_id=mentioning.id,
        )


def _ranked(corpus: SeededCorpus, query: str, limit: int = 10) -> list[int]:
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        response = service.search(RetrievalRequest(query=query, limit=limit))
        return [match.fragment_id for match in response.matches]


def test_governed_clause_outranks_a_passing_mention(corpus: SeededCorpus) -> None:
    """The section under the ER chapter beats the clause that lists ER-1.

    This is the inversion the dimensional class was losing to. Note the
    governed section does not contain "ER-1" at all, and the mentioning clause
    contains it in its own text — under own-text-plus-context scoring alone,
    the mentioning clause wins.
    """
    ranked = _ranked(corpus, ZONE_QUERY)
    assert corpus.governed_id in ranked
    assert ranked.index(corpus.governed_id) < ranked.index(corpus.mentioning_id)


def test_hyphenless_spelling_still_binds(corpus: SeededCorpus) -> None:
    """"ER-3" binds through a heading that prints "ER3".

    Not leniency — the Regional Centre's own Chapter 9 heading writes "ER3,
    ER-2, and ER-1" in one breath, so a hyphen-strict matcher reads the
    document differently from how it was printed.
    """
    ranked = _ranked(corpus, "What is the maximum required lot coverage in the ER-3 zone?")
    assert ranked.index(corpus.governed_id) < ranked.index(corpus.mentioning_id)


def test_the_declaring_chapter_is_bound_to_its_own_zone(corpus: SeededCorpus) -> None:
    """A question asking *for the chapter* still finds the chapter.

    Binding the subtree without binding the container would sink the heading
    beneath every section it scopes — measured as a real regression on the
    ABS-486 zone-anchored class before the container was included.
    """
    ranked = _ranked(
        corpus, "Where are the built form and siting requirements for the ER-1 zone?"
    )
    assert corpus.chapter_id in ranked


def test_a_query_naming_no_zone_binds_nothing(corpus: SeededCorpus) -> None:
    """Without a zone in the query there is no scope to bind to.

    The mentioning clause wins here, and should: it is the only fragment that
    says anything about landscaped buffers.
    """
    ranked = _ranked(corpus, "When is a landscaped buffer required?")
    assert ranked[0] == corpus.mentioning_id


def test_vocabulary_comes_from_the_corpus_not_a_regex(corpus: SeededCorpus) -> None:
    """A hyphenated token the corpus never extracted as a zone is not a zone.

    "Section 3-1" and "Table 1A" are shaped like zone codes. Reading the
    vocabulary off ``semantic_entity`` rather than a pattern is what keeps them
    from binding half the by-law.
    """
    with session_scope(corpus.db_url) as session:
        index = build_zone_scope_index(session, [corpus.document_id])
        assert index.zones_named_in("the ER-1 zone") == frozenset({"ER-1"})
        assert index.zones_named_in("Section 3-1 and Table 1A") == frozenset()
        # Word-bounded: ER-1 must not match inside ER-10.
        assert index.zones_named_in("the ER-10 zone") == frozenset()
        assert index.containers_declaring(frozenset({"ER-1"})) == frozenset(
            {corpus.chapter_id}
        )
        assert index.containers_declaring(frozenset()) == frozenset()
