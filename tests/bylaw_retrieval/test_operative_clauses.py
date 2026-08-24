"""ABS-521: a provision that ends on a colon is not an answer.

s.333 of the Regional Centre states two caps on a new accessory structure and
they bind together::

    Part V, Chapter 19: Accessory Structures, Backyard Suite Uses, …
      Accessory Structure Footprint and Area                   <- HEADING
      333 (1) Any new accessory structure shall have no restriction on the
              maximum size of its footprint, except:           <- SECTION
        (a) … in any DD, DH, CEN-2, CEN1, COR, HR-2, HR-1, ER-3, ER-2, ER-1,
            CH-2, or CH-1 zone: 60.0 square metres; or         <- footprint
        (b) in the Westmount Subdivision (WS) Special Area … 6.0 square metres
        (1.5) In any DD … zone, any new accessory structure shall not have a
              floor area greater than 93.0 square metres.      <- floor area

TC-024 asked how big a garage-to-suite conversion could be at 1107 Lucknow
Street (ER-2). Retrieval returned ``Part V > 333`` and ``Part V > 333 > (1.5)``
across five attempts — including a direct ``lookup_citation`` on the section —
and never ``(a)``. The advisor answered "must not exceed 93.0 m² of floor area"
and never mentioned 60. An owner who designs to 93 and ignores 60 fails.

Two structural facts, both reproduced below, put ``(a)`` out of reach:

1. **It has no topic words.** Strip the zone list and ``(a)`` is a number. On
   the very query it answers it scores below its own section, and no channel
   weight closes that without promoting every bare list item in the corpus.
2. **Its tree parent is the heading, not the section.** ``(a)`` and ``(b)``
   carry ``parent_fragment_id`` pointing at "Accessory Structure Footprint and
   Area", a *sibling* of s.333. Only ``citation_path`` records that they belong
   to s.333, and it is not one bad row — 1,906 CLAUSE/SUBCLAUSE fragments in the
   dev corpus are attached this way.

So the fix is not ordinal. Whatever the ranker returns has to arrive complete:
a provision carries its own clauses down, and a clause carries its provision's
other clauses sideways. The sideways direction is the one that matters most
here — on the footprint question the ranker returns ``(1.5)`` and *not* the
section, so completing downwards alone would still lose the 60.0 figure.

These tests fail without ``bylaw_retrieval.retrieval.provision``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalService
from bylaw_retrieval.retrieval.provision import OPERATIVE_CLAUSE_LIMIT
from bylaw_retrieval.retrieval.schemas import (
    CitationLookupRequest,
    RetrievalRequest,
)
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

CHAPTER = (
    "Part V, Chapter 19: Accessory Structures, Backyard Suite Uses, and "
    "Shipping Containers"
)
#: The heading the parser hung the clauses off. Real, and real context — it is
#: just not the sentence the clauses finish.
HEADING = "Accessory Structure Footprint and Area"
#: The stem. Note that it stops on a colon: on its own it states no standard.
SECTION_333 = (
    "333 (1) Any new accessory structure shall have no restriction on the "
    "maximum size of its footprint, except: (RCCC-Oct 26/22;E-Nov 11/22)"
)
#: The clause TC-024 never saw. Every topical word it might have matched on
#: lives in the stem above; what it has of its own is a zone list and 60.0.
CLAUSE_A_FOOTPRINT = (
    "(a) subject to Clause 333(1)(b), in any DD, DH, CEN-2, CEN1, COR, HR-2, "
    "HR- 1, ER-3, ER-2, ER-1, CH-2, or CH-1 zone: 60.0 square metres; or"
)
CLAUSE_B_WESTMOUNT = (
    "(b) in the Westmount Subdivision (WS) Special Area, as shown on Schedule "
    "3C, 6.0 square metres within a front yard."
)
#: The sibling that *is* self-contained, and therefore the only one that ever
#: ranked. It states the second cap — which binds alongside (a), not instead.
SUBSECTION_1_5_FLOOR_AREA = (
    "(1.5) In any DD, DH, CEN-2, CEN-1, COR, HR-2, HR-1, ER-3, ER-2, ER-1, "
    "CH-2, or CH-1 zone, any new accessory structure shall not have a floor "
    "area greater than 93.0 square metres. (RCCCOct 26/22;E-Nov 11/22)"
)
#: A section elsewhere in the same chapter that states its rule whole. It is
#: here to prove completion is not "attach something to everything".
SECTION_332_SELF_CONTAINED = (
    "332 One accessory structure per lot, which has a footprint that is no "
    "greater than 20.0 square metres, shall be exempted from the maximum "
    "required lot coverage of the lot."
)

FOOTPRINT_QUERY = (
    "What is the maximum footprint for a new accessory structure in the ER-2 "
    "zone?"
)


def _add_document(session) -> Document:
    document = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="regional-centre.txt",
        source_url=None,
        file_hash="abs521-operative-clauses",
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
    reading_order: int = 1,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        parent_fragment_id=parent.id if parent is not None else None,
        page_start=238,
        page_end=238,
        reading_order_start=reading_order,
        reading_order_end=reading_order,
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


@dataclass(frozen=True)
class SeededCorpus:
    db_url: str
    document_id: int
    chapter_id: int
    heading_id: int
    section_333_id: int
    clause_a_id: int
    clause_b_id: int
    subsection_1_5_id: int
    section_332_id: int


@pytest.fixture()
def corpus(tmp_path: Path) -> SeededCorpus:
    """s.333 as the dev corpus actually holds it — mis-parented clauses and all.

    The parentage is copied from the real rows, not invented: ``(a)`` and ``(b)``
    hang off the *heading*, ``(1.5)`` hangs off the *section*, and only the
    citation paths agree that all three belong to s.333. Fixing the parentage in
    the fixture would make these tests pass against the unfixed retriever, which
    is the one thing a reproduction must not do.
    """
    db_url = f"sqlite:///{tmp_path / 'operative_clauses.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = _add_document(session)
        chapter = _add_fragment(
            session,
            document.id,
            text=CHAPTER,
            fragment_type=FragmentType.PART,
            citation_label="Part V, Chapter 19",
            citation_path="Part V, Chapter 19",
            reading_order=1,
        )
        section_332 = _add_fragment(
            session,
            document.id,
            text=SECTION_332_SELF_CONTAINED,
            citation_label="332",
            citation_path="Part V > 332",
            parent=chapter,
            reading_order=2,
        )
        heading = _add_fragment(
            session,
            document.id,
            text=HEADING,
            fragment_type=FragmentType.HEADING,
            parent=chapter,
            reading_order=3,
        )
        section_333 = _add_fragment(
            session,
            document.id,
            text=SECTION_333,
            citation_label="333",
            citation_path="Part V > 333",
            parent=chapter,
            reading_order=4,
        )
        # The defect, verbatim: pathed under s.333, parented to the heading.
        clause_a = _add_fragment(
            session,
            document.id,
            text=CLAUSE_A_FOOTPRINT,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(a)",
            citation_path="Part V > 333 > (a)",
            parent=heading,
            reading_order=5,
        )
        clause_b = _add_fragment(
            session,
            document.id,
            text=CLAUSE_B_WESTMOUNT,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(b)",
            citation_path="Part V > 333 > (b)",
            parent=heading,
            reading_order=6,
        )
        subsection = _add_fragment(
            session,
            document.id,
            text=SUBSECTION_1_5_FLOOR_AREA,
            fragment_type=FragmentType.SUBSECTION,
            citation_label="(1.5)",
            citation_path="Part V > 333 > (1.5)",
            parent=section_333,
            reading_order=7,
        )
        return SeededCorpus(
            db_url=db_url,
            document_id=document.id,
            chapter_id=chapter.id,
            heading_id=heading.id,
            section_333_id=section_333.id,
            clause_a_id=clause_a.id,
            clause_b_id=clause_b.id,
            subsection_1_5_id=subsection.id,
            section_332_id=section_332.id,
        )


def _service(session) -> RetrievalService:
    return RetrievalService(session)


def _paths(clauses) -> list[str]:
    return [clause.citation_path for clause in clauses]


# ----------------------------------------------------------------------
# The reported defect
# ----------------------------------------------------------------------


def test_lookup_citation_on_the_section_returns_its_operative_clauses(corpus):
    """The exact call from the TC-024 transcript, and what it must now return.

    ``lookup_citation {"citation_path": "Part V > 333"}`` returned one fragment
    whose text ends "…except:". That is not a partial answer, it is a sentence
    with its predicate missing.
    """
    with session_scope(corpus.db_url) as session:
        response = _service(session).lookup_citation(
            CitationLookupRequest(citation_path="Part V > 333")
        )
        assert response.match is not None
        assert _paths(response.match.operative_clauses) == [
            "Part V > 333 > (a)",
            "Part V > 333 > (b)",
            "Part V > 333 > (1.5)",
        ]
        assert "60.0 square metres" in response.match.operative_clauses[0].text
        assert response.match.operative_clauses_omitted == 0


def test_lookup_citation_defaults_carry_the_clauses(corpus):
    """``include_context`` defaults to False on ``lookup_citation``.

    The transcript's call passed nothing but the path, so a completion gated on
    ``include_context`` would have left the reported defect exactly where it
    was. Pinned because the gating is a one-word change away.
    """
    with session_scope(corpus.db_url) as session:
        request = CitationLookupRequest(citation_path="Part V > 333")
        assert request.include_context is False
        response = _service(session).lookup_citation(request)
        assert response.match is not None
        assert response.match.ancestor_chain == []
        assert response.match.operative_clauses != []


def test_both_caps_reach_a_reader_asking_about_size(corpus):
    """The acceptance criterion, asserted on the payload the agent receives.

    60.0 (footprint, ``(a)``) and 93.0 (floor area, ``(1.5)``) both bind. The
    ranker surfaces one of them; completion has to deliver the other, whichever
    way round the ranking happens to fall.
    """
    with session_scope(corpus.db_url) as session:
        response = _service(session).search(
            RetrievalRequest(query=FOOTPRINT_QUERY, limit=10)
        )
        assert response.matches, "the query reached nothing at all"
        delivered = "\n".join(
            "\n".join([match.text, *(c.text for c in match.operative_clauses)])
            for match in response.matches
        )
        assert "60.0 square metres" in delivered, (
            "the footprint cap in s.333(1)(a) is still unreachable — this is "
            "the ABS-521 defect"
        )
        assert "93.0 square metres" in delivered


def test_a_ranked_subsection_carries_its_siblings(corpus):
    """Completion runs sideways, not only downwards.

    On the footprint question the corpus ranks ``(1.5)`` — the *floor-area* cap
    — because it is the only limb of s.333 that states its own subject. Its
    siblings are the rest of the rule.
    """
    with session_scope(corpus.db_url) as session:
        service = _service(session)
        subsection = session.get(SourceFragment, corpus.subsection_1_5_id)
        clauses, omitted = service._provisions().operative_clauses(subsection)
        assert _paths(clauses) == ["Part V > 333 > (a)", "Part V > 333 > (b)"]
        assert omitted == 0


def test_a_clause_never_repeats_itself(corpus):
    """``(a)``'s completion is ``(b)`` and ``(1.5)``, and not ``(a)``."""
    with session_scope(corpus.db_url) as session:
        service = _service(session)
        clause_a = session.get(SourceFragment, corpus.clause_a_id)
        clauses, _ = service._provisions().operative_clauses(clause_a)
        assert _paths(clauses) == ["Part V > 333 > (b)", "Part V > 333 > (1.5)"]


# ----------------------------------------------------------------------
# Lineage: the path is the truth, the tree is additional
# ----------------------------------------------------------------------


def test_the_ancestor_chain_reaches_the_stem_the_clause_finishes(corpus):
    """Before ABS-521 the chain over ``(a)`` never named s.333.

    ``(a).parent`` is the heading, so the tree walk climbed heading → chapter
    and stopped. The agent was shown a clause reading "…60.0 square metres; or"
    under a chapter title, with the sentence it completes nowhere in the payload.
    """
    with session_scope(corpus.db_url) as session:
        clause_a = session.get(SourceFragment, corpus.clause_a_id)
        chain = _service(session)._ancestor_chain(clause_a)
        ids = [ancestor.id for ancestor in chain]
        assert corpus.section_333_id in ids, "the stem is still missing"
        # The tree lineage is kept, not replaced: the heading really is what
        # the clause was printed under and really does say what it is about.
        assert corpus.heading_id in ids
        assert corpus.chapter_id in ids


def test_lineage_is_a_union_and_holds_no_duplicates(corpus):
    with session_scope(corpus.db_url) as session:
        clause_a = session.get(SourceFragment, corpus.clause_a_id)
        chain = _service(session)._ancestor_chain(clause_a)
        ids = [ancestor.id for ancestor in chain]
        assert len(ids) == len(set(ids))
        assert clause_a.id not in ids


def test_lineage_is_in_document_order(corpus):
    """The chain reads top-down, not in whatever order the ascent produced.

    Two lineages that disagree about the shape of the tree can be walked in
    several defensible orders, and the walk's own order is the one that changes
    when the disagreement does. Reading order is a property of the by-law: a
    container is printed before what it contains, so for a well-formed document
    this is root-first and it stays root-first.
    """
    with session_scope(corpus.db_url) as session:
        clause_a = session.get(SourceFragment, corpus.clause_a_id)
        chain = _service(session)._ancestor_chain(clause_a)
        assert [ancestor.id for ancestor in chain] == [
            corpus.chapter_id,
            corpus.heading_id,
            corpus.section_333_id,
        ]


# ----------------------------------------------------------------------
# What completion must NOT do
# ----------------------------------------------------------------------


def test_a_self_contained_section_gains_nothing(corpus):
    """s.332 states its rule whole. Completion is for provisions split across
    clauses, not a licence to staple neighbours onto every match."""
    with session_scope(corpus.db_url) as session:
        response = _service(session).lookup_citation(
            CitationLookupRequest(citation_path="Part V > 332")
        )
        assert response.match is not None
        assert response.match.operative_clauses == []


def test_a_container_is_never_completed(corpus):
    """A Part is not a provision.

    ``Part V, Chapter 19`` has no path-children here, but the guard that matters
    is the type check: the dev corpus has a Part with 297 direct path-children,
    and "complete the provision" turning into "return the chapter" is the one
    way this feature could cost more than it is worth.
    """
    with session_scope(corpus.db_url) as session:
        service = _service(session)
        chapter = session.get(SourceFragment, corpus.chapter_id)
        assert service._provisions().governing_provision(chapter) is None
        assert service._provisions().operative_clauses(chapter) == ([], 0)


def test_a_capped_provision_says_how_much_it_dropped(tmp_path: Path):
    """Truncation is reported, never swallowed.

    A provision shown short reads exactly like a provision that *is* short —
    the ABS-521 defect with a different cause. The count is what lets a caller
    tell the two apart and go read the rest.
    """
    db_url = f"sqlite:///{tmp_path / 'capped.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = _add_document(session)
        section = _add_fragment(
            session,
            document.id,
            text="400 The following shall apply:",
            citation_label="400",
            citation_path="Part V > 400",
            reading_order=1,
        )
        for index in range(OPERATIVE_CLAUSE_LIMIT + 3):
            _add_fragment(
                session,
                document.id,
                text=f"({index}) limb {index}.",
                fragment_type=FragmentType.CLAUSE,
                citation_label=f"({index})",
                citation_path=f"Part V > 400 > ({index})",
                parent=section,
                reading_order=index + 2,
            )
    with session_scope(db_url) as session:
        response = _service(session).lookup_citation(
            CitationLookupRequest(citation_path="Part V > 400")
        )
        assert response.match is not None
        assert len(response.match.operative_clauses) == OPERATIVE_CLAUSE_LIMIT
        assert response.match.operative_clauses_omitted == 3
