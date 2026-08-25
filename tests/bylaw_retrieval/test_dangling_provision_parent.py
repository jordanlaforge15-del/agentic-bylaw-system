"""ABS-523: a clause whose path parent names nothing still finds its stem.

s.233 of the Regional Centre reads::

    233 (1) Excluding any structure below 0.6 metres above the average finished
            grade … no building shall exceed:                       <- SECTION
      (a) except as provided in Subsection 233(2) or 233(3), a building width
          of 20.0 metres; and                                       <- pathed
      (b) a building depth of 30.0 metres.                          <- pathed
      The maximum building width of a townhouse block is 64.0 metres …
      An addition to an existing main building shall only be permitted in the
      rear yard but shall not exceed the building width or footprint of the
      existing main building, if the addition causes the main building to
      contain                                                       <- NO PATH
        (a) more than 2 dwelling units in an ER-2 zone; or
        (b) more than 8 dwelling units in an ER-3 zone.

The stem in the middle is s.233(3) and it was never given a ``citation_path``.
Its clauses were, so they read::

    Part V > 233 > [An addition to an existing main building ... to contain] > (b)

Nothing stands at that bracketed segment. ``lookup_citation("Part V > 233")``
returned the width and depth clauses and stopped; ``citation_path_prefix``
scoped to "Part V > 233" reported five matches with the stem absent; and a
search that ranked the clause delivered "(b) more than 8 dwelling units in an
ER-3 zone." with no stem, no sibling and no section in its ancestor chain.

TC-023 is what that costs. The model made exactly the right verification call,
hit a dead end that looked complete, and wrote "Section 233(3) enables site plan
approval … it does not override the 8-unit cap" — a reading that appears in no
fragment of the corpus — then told a developer to seek a rezoning.

This is ABS-521's other bucket: not "path and tree disagree" (2,410 fragments,
fixed there) but "the parent path names nothing" (1,208 in doc 4 alone, 1,005
of them under bracketed prose). The bracketed segment is
``context_segment(stem.text)``, a pure function of the stem's own words, so
resolving it is an identity check rather than a guess.

These tests fail against the pre-ABS-523 lineage.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalService
from bylaw_retrieval.retrieval.schemas import CitationLookupRequest
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.citation_repath import context_segment

SECTION_PATH = "Part V > 233"
SECTION_TEXT = (
    "233 (1) Excluding any structure below 0.6 metres above the average "
    "finished grade, a low-density dwelling use, or any public use, no "
    "building shall exceed:"
)
CLAUSE_WIDTH = (
    "(a) except as provided in Subsection 233(2) or 233(3), a building width "
    "of 20.0 metres; and"
)
CLAUSE_DEPTH = "(b) a building depth of 30.0 metres."
TOWNHOUSE = (
    "The maximum building width of a townhouse block is 64.0 metres and the "
    "maximum number of permitted townhouse units in a townhouse block located "
    "in a ER-3 Zone is eight."
)
STEM = (
    "An addition to an existing main building shall only be permitted in the "
    "rear yard but shall not exceed the building width or footprint of the "
    "existing main building, if the addition causes the main building to "
    "contain"
)
ADDITION_ER2 = "(a) more than 2 dwelling units in an ER-2 zone; or"
ADDITION_ER3 = "(b) more than 8 dwelling units in an ER-3 zone."

#: Derived exactly as the ingest derives it. Writing the string out by hand
#: would let the fixture and the repather drift, and the test would then be
#: grading a path shape the corpus no longer produces.
SEGMENT = context_segment(STEM)
CONTAINER_PATH = f"{SECTION_PATH} > {SEGMENT}"


def _add_fragment(
    session,
    document_id: int,
    *,
    text: str,
    fragment_type: FragmentType,
    reading_order: int,
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
    heading_id: int
    section_id: int
    width_id: int
    depth_id: int
    stem_id: int
    addition_er2_id: int
    addition_er3_id: int


@pytest.fixture()
def corpus(tmp_path: Path) -> SeededCorpus:
    """s.233 with the parentage and the missing path the dev corpus has.

    Two defects are reproduced, not repaired: the clauses hang off the *heading*
    (ABS-521's shape), and the stem has no ``citation_path`` at all (ABS-523's).
    Fixing either in the fixture would let the unfixed retriever pass.
    """
    db_url = f"sqlite:///{tmp_path / 'dangling.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="regional-centre.txt",
            file_hash="abs523-dangling-parent",
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()

        heading = _add_fragment(
            session,
            document.id,
            text="Maximum Building Dimensions",
            fragment_type=FragmentType.HEADING,
            reading_order=1,
        )
        section = _add_fragment(
            session,
            document.id,
            text=SECTION_TEXT,
            fragment_type=FragmentType.SECTION,
            citation_label="233",
            citation_path=SECTION_PATH,
            reading_order=2,
        )
        width = _add_fragment(
            session,
            document.id,
            text=CLAUSE_WIDTH,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(a)",
            citation_path=f"{SECTION_PATH} > (a)",
            parent=heading,
            reading_order=3,
        )
        depth = _add_fragment(
            session,
            document.id,
            text=CLAUSE_DEPTH,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(b)",
            citation_path=f"{SECTION_PATH} > (b)",
            parent=heading,
            reading_order=4,
        )
        townhouse = _add_fragment(
            session,
            document.id,
            text=TOWNHOUSE,
            fragment_type=FragmentType.LIST_ITEM,
            parent=depth,
            reading_order=5,
        )
        stem = _add_fragment(
            session,
            document.id,
            text=STEM,
            fragment_type=FragmentType.LIST_ITEM,
            parent=townhouse,
            reading_order=6,
        )
        er2 = _add_fragment(
            session,
            document.id,
            text=ADDITION_ER2,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(a)",
            citation_path=f"{CONTAINER_PATH} > (a)",
            parent=heading,
            reading_order=7,
        )
        er3 = _add_fragment(
            session,
            document.id,
            text=ADDITION_ER3,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(b)",
            citation_path=f"{CONTAINER_PATH} > (b)",
            parent=heading,
            reading_order=8,
        )
        return SeededCorpus(
            db_url=db_url,
            document_id=document.id,
            heading_id=heading.id,
            section_id=section.id,
            width_id=width.id,
            depth_id=depth.id,
            stem_id=stem.id,
            addition_er2_id=er2.id,
            addition_er3_id=er3.id,
        )


def _texts(clauses) -> list[str]:
    return [clause.text for clause in clauses]


# ----------------------------------------------------------------------
# The reported defect
# ----------------------------------------------------------------------


def test_the_stem_is_unreachable_by_path(corpus):
    """The premise. If a later ingest gives s.233(3) a path this test fails,
    and that is the right outcome — the workaround below is then unnecessary
    and should be reconsidered rather than kept because it is green."""
    with session_scope(corpus.db_url) as session:
        stem = session.get(SourceFragment, corpus.stem_id)
        assert stem.citation_path is None
        response = RetrievalService(session).lookup_citation(
            CitationLookupRequest(citation_path=CONTAINER_PATH)
        )
        assert response.match is None, "nothing stands at the bracketed segment"


def test_the_er3_clause_arrives_with_the_sentence_it_finishes(corpus):
    """The payload TC-023 needed and did not get.

    "(b) more than 8 dwelling units in an ER-3 zone." states no rule on its own.
    Without the stem it does not even say what happens when a building contains
    them — which is why the model invented "site plan approval".
    """
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        clause = session.get(SourceFragment, corpus.addition_er3_id)

        provision = service._provisions().governing_provision(clause)
        assert provision is not None, "the clause still has no governing provision"
        assert provision.id == corpus.stem_id

        clauses, omitted = service._provisions().operative_clauses(clause)
        assert omitted == 0
        delivered = _texts(clauses)
        # The stem goes in the list, not merely in the ancestor chain: it has no
        # citation path, so completion is the only route to it.
        assert STEM in delivered
        assert ADDITION_ER2 in delivered
        assert ADDITION_ER3 not in delivered, "a match never repeats itself"


def test_lookup_citation_on_the_section_returns_the_addition_limb(corpus):
    """The acceptance criterion: ``lookup_citation('Part V > 233')`` returns the
    addition stem and its (a)/(b) clauses.

    Before ABS-523 it returned the width and depth clauses and stopped, and the
    stop looked like completeness — five matches, no error, nothing to suggest a
    whole limb of the section was one level further down under a segment that
    named nothing.
    """
    with session_scope(corpus.db_url) as session:
        response = RetrievalService(session).lookup_citation(
            CitationLookupRequest(citation_path=SECTION_PATH)
        )
        assert response.match is not None
        delivered = _texts(response.match.operative_clauses)

    assert delivered == [CLAUSE_WIDTH, CLAUSE_DEPTH, STEM, ADDITION_ER2, ADDITION_ER3]
    # Reading order, so an expanded limb lands where the by-law prints it.
    assert delivered.index(STEM) < delivered.index(ADDITION_ER2)


def test_the_ancestor_chain_over_the_clause_reaches_the_stem(corpus):
    with session_scope(corpus.db_url) as session:
        clause = session.get(SourceFragment, corpus.addition_er3_id)
        chain = RetrievalService(session)._ancestor_chain(clause)
        assert corpus.stem_id in [ancestor.id for ancestor in chain]


def test_a_search_that_ranks_the_clause_delivers_the_rule(corpus):
    """End to end, on the shape of query TC-023 asked.

    The clause has no topic words of its own — "ER-3", "dwelling units" and a
    number — so what makes the answer right is that whatever the ranker returns
    arrives whole.
    """
    from bylaw_retrieval.retrieval.schemas import RetrievalRequest

    with session_scope(corpus.db_url) as session:
        response = RetrievalService(session).search(
            RetrievalRequest(
                query="more than 8 dwelling units in an ER-3 zone addition", limit=10
            )
        )
        assert response.matches, "the query reached nothing at all"
        delivered = "\n".join(
            "\n".join([match.text, *(c.text for c in match.operative_clauses)])
            for match in response.matches
        )

    assert "more than 8 dwelling units in an ER-3 zone" in delivered
    assert "rear yard" in delivered, (
        "the addition rule reached the caller without the stem that states it — "
        "this is the ABS-523 defect"
    )


# ----------------------------------------------------------------------
# What the fallback must NOT do
# ----------------------------------------------------------------------


def test_a_clause_under_a_real_parent_is_unchanged(corpus):
    """The width clause's parent path resolves normally. Nothing about it goes
    near the content-addressed lookup."""
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        width = session.get(SourceFragment, corpus.width_id)
        provision = service._provisions().governing_provision(width)
        assert provision is not None and provision.id == corpus.section_id


def test_an_unbracketed_missing_parent_is_still_a_dead_end(corpus, tmp_path: Path):
    """The 203 non-bracketed danglers are a different problem (a path segment
    naming a section that was never extracted) and guessing at them would be
    inventing lineage rather than recovering it."""
    db_url = f"sqlite:///{tmp_path / 'unbracketed.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Unbracketed",
            source_path="x.txt",
            file_hash="abs523-unbracketed",
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()
        orphan = _add_fragment(
            session,
            document.id,
            text="(a) something.",
            fragment_type=FragmentType.CLAUSE,
            citation_label="(a)",
            citation_path="Part V > 999 > (a)",
            reading_order=1,
        )
        orphan_id = orphan.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        orphan = session.get(SourceFragment, orphan_id)
        assert service._provisions().governing_provision(orphan) is None
        assert service._provisions().operative_clauses(orphan) == ([], 0)


def test_a_segment_matching_nothing_resolves_to_nothing(corpus, tmp_path: Path):
    """A bracketed segment whose stem was never ingested must not attach to the
    nearest fragment that happens to be in the window."""
    db_url = f"sqlite:///{tmp_path / 'nostem.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="No stem",
            source_path="x.txt",
            file_hash="abs523-no-stem",
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()
        _add_fragment(
            session,
            document.id,
            text="Something else entirely.",
            fragment_type=FragmentType.PROSE,
            reading_order=1,
        )
        clause = _add_fragment(
            session,
            document.id,
            text="(a) more than 2 dwelling units in an ER-2 zone; or",
            fragment_type=FragmentType.CLAUSE,
            citation_label="(a)",
            citation_path="Part V > 233 > [A stem that was never ingested] > (a)",
            reading_order=2,
        )
        clause_id = clause.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        clause = session.get(SourceFragment, clause_id)
        assert service._provisions().governing_provision(clause) is None
        # ...and nothing is attached in its place: the only other fragment in
        # the window is unrelated prose.
        assert service._provisions().operative_clauses(clause) == ([], 0)


def test_siblings_complete_each_other_even_with_the_stem_missing(tmp_path: Path):
    """Two limbs under one bracketed segment belong together whether or not the
    sentence they finish survived ingest.

    ``governing_provision`` says None — there is no citable provision — and the
    completion runs sideways anyway. Reporting one limb of a two-limb list as
    the whole rule is the error this module exists to prevent, and it does not
    stop being one because the stem is also missing.
    """
    db_url = f"sqlite:///{tmp_path / 'stemless.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Stemless",
            source_path="x.txt",
            file_hash="abs523-stemless-siblings",
            mime_type="text/plain",
            page_count=1,
            parser_version="test",
            retrieval_enabled=True,
        )
        session.add(document)
        session.flush()
        container = "Part V > 233 > [A stem that was never ingested]"
        first = _add_fragment(
            session,
            document.id,
            text=ADDITION_ER2,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(a)",
            citation_path=f"{container} > (a)",
            reading_order=1,
        )
        _add_fragment(
            session,
            document.id,
            text=ADDITION_ER3,
            fragment_type=FragmentType.CLAUSE,
            citation_label="(b)",
            citation_path=f"{container} > (b)",
            reading_order=2,
        )
        first_id = first.id

    with session_scope(db_url) as session:
        service = RetrievalService(session)
        first = session.get(SourceFragment, first_id)
        assert service._provisions().governing_provision(first) is None
        clauses, omitted = service._provisions().operative_clauses(first)
        assert _texts(clauses) == [ADDITION_ER3]
        assert omitted == 0
