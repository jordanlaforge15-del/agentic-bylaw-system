"""ABS-492: a clause is scored under the scope its containers supply.

The corpus states most rules the way a printed by-law does — the zone in the
chapter heading, the dimension in the section, the number in an unlabelled list
item underneath. Retrieval only ever read the leaf, so "(f) 2.5 metres
elsewhere." was a five-token fragment with no visible scope and no way to be
found by the terms that actually govern it.

These tests pin the two halves of the fix and the two guards that keep it from
becoming noise:

* the ancestor chain reaches the leaf (``test_stripped_list_item_*``), and the
  control proves it is the context channel doing it and not the fixture;
* the container prose ABS-488 folded into ``citation_path`` is scored as
  context, not at path weight (``test_bracketed_*``);
* scope a fragment already states itself is not paid for twice;
* a token that describes the whole corpus carries no scope.

The service is driven end to end against a seeded sqlite corpus rather than a
stub, because the thing under test is the interaction between the scope query,
the ancestor index and the scorer — a stub would only re-assert the arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService
from bylaw_retrieval.retrieval.context import AncestorIndex, split_citation_path
from bylaw_retrieval.retrieval.service import score_fragment_detail
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

#: The chapter heading is the only place the zone is named, the section is the
#: only place the dimension is named, and the list item states neither — which
#: is exactly how the Regional Centre by-law states its side-setback rule
#: (fragment 7134 in the dev corpus, cited in the issue).
ZONE_CHAPTER = (
    "Part V, Chapter 9: Built Form and Siting Requirements within the ER-3 Zone"
)
SECTION_229 = (
    "229 (1) The minimum required side setback for a main building is 2.5 metres."
)
STRIPPED_LIST_ITEM = "(f) 2.5 metres elsewhere."

#: Query naming the chapter's zone and the section's dimension, and nothing the
#: list item itself contains.
SCOPING_QUERY = "ER-3 side setback"


def _add_document(session) -> Document:
    document = Document(
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        source_path="regional-centre.txt",
        source_url=None,
        file_hash="abs492-fixture",
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
    fragment_type: FragmentType = FragmentType.CLAUSE,
    citation_label: str | None = None,
    citation_path: str | None = None,
    parent: SourceFragment | None = None,
    attribute_tags: list[str] | None = None,
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
        attribute_tags=attribute_tags or [],
    )
    session.add(fragment)
    session.flush()
    return fragment


@dataclass(frozen=True)
class SeededCorpus:
    db_url: str
    chapter_id: int
    section_id: int
    list_item_id: int
    unparented_twin_id: int


@pytest.fixture()
def corpus(tmp_path: Path) -> SeededCorpus:
    """A three-level chapter/section/list-item tree plus unrelated filler.

    The filler is what makes the document-frequency cut meaningful: without a
    body of fragments to measure against, every query token would look rare and
    the cut would never fire.
    """
    db_url = f"sqlite:///{tmp_path / 'context.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = _add_document(session)
        chapter = _add_fragment(
            session,
            document.id,
            text=ZONE_CHAPTER,
            fragment_type=FragmentType.PART,
            citation_label="Part V, Chapter 9",
            citation_path="Part V, Chapter 9",
        )
        section = _add_fragment(
            session,
            document.id,
            text=SECTION_229,
            fragment_type=FragmentType.SECTION,
            citation_label="229",
            citation_path="Part V > 229",
            parent=chapter,
            attribute_tags=["side_setback_left_m"],
        )
        list_item = _add_fragment(
            session,
            document.id,
            text=STRIPPED_LIST_ITEM,
            fragment_type=FragmentType.LIST_ITEM,
            citation_path="Part V > 229 > (f)",
            parent=section,
            attribute_tags=["side_setback_left_m"],
        )
        # Same words as the section, no container at all. Anything the section
        # scores above this twin is scope, not text.
        twin = _add_fragment(
            session,
            document.id,
            text=SECTION_229,
            fragment_type=FragmentType.SECTION,
            citation_label="631",
            citation_path="Part IX > 631",
        )
        # Filler: a parking chapter that names neither the zone nor the
        # dimension, so it moves the document frequency of the common words
        # ("a", "for", "the") without touching the discriminating ones.
        parking = _add_fragment(
            session,
            document.id,
            text="Part VII, Chapter 2: Parking and Loading Requirements",
            fragment_type=FragmentType.PART,
            citation_label="Part VII, Chapter 2",
            citation_path="Part VII, Chapter 2",
        )
        for index in range(20):
            _add_fragment(
                session,
                document.id,
                text=(
                    f"({chr(ord('a') + index % 26)}) A parking space for a "
                    "vehicle shall be at least 2.6 metres wide."
                ),
                citation_path=f"Part VII > {700 + index}",
                parent=parking,
            )
        return SeededCorpus(
            db_url=db_url,
            chapter_id=chapter.id,
            section_id=section.id,
            list_item_id=list_item.id,
            unparented_twin_id=twin.id,
        )


def _search(corpus: SeededCorpus, query: str, **kwargs) -> dict[int, float]:
    """Return {fragment_id: score} for the text channel, unranked."""
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        return service._text_channel_scores(RetrievalRequest(query=query, **kwargs))


def _ranked_ids(corpus: SeededCorpus, query: str, limit: int = 10) -> list[int]:
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        response = service.search(RetrievalRequest(query=query, limit=limit))
        return [match.fragment_id for match in response.matches]


# ----------------------------------------------------------------------
# The definition of done: a stripped list item is reachable through its
# parent section's terms.
# ----------------------------------------------------------------------


def test_stripped_list_item_is_retrievable_via_its_containers_terms(
    corpus: SeededCorpus,
) -> None:
    """"(f) 2.5 metres elsewhere." contains no word of "ER-3 side setback"."""
    assert not any(
        term in STRIPPED_LIST_ITEM.lower() for term in ("er-3", "side", "setback")
    )
    assert corpus.list_item_id in _ranked_ids(corpus, SCOPING_QUERY)


def test_stripped_list_item_is_unreachable_without_the_context_channel(
    corpus: SeededCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the test above.

    With the context weight at zero the fragment scores nothing but the
    parse-status bonus and drops out of the channel entirely — so the hit above
    is the ancestor chain reaching it, not the fixture being small.
    """
    monkeypatch.setattr(RetrievalService, "_CONTEXT_TOKEN_SCORE", 0.0)
    assert corpus.list_item_id not in _search(corpus, SCOPING_QUERY)


def test_the_section_stating_the_standard_outranks_its_own_list_item(
    corpus: SeededCorpus,
) -> None:
    """Inherited scope must not invert the tree.

    Both fragments sit under the chapter that names the zone, so both inherit
    "ER-3"; only the section states the dimension, and it has to stay ahead of
    the clause it introduces.
    """
    ranked = _ranked_ids(corpus, SCOPING_QUERY)
    assert ranked.index(corpus.section_id) < ranked.index(corpus.list_item_id)


def test_context_reaches_a_fragment_whose_ancestors_are_outside_the_filter(
    corpus: SeededCorpus,
) -> None:
    """An attribute_tag_filter narrows what is *scored*, not what supplies scope.

    The chapter carries no attribute tags, so the filter excludes it from the
    candidate set — but it is still the only place the zone is named, and the
    tagged clause beneath it is still an ER-3 clause. This is the path where
    the ancestor index has to go back to the database.
    """
    scores = _search(
        corpus, SCOPING_QUERY, attribute_tag_filter=["side_setback_left_m"]
    )
    assert corpus.list_item_id in scores
    assert corpus.chapter_id not in scores


# ----------------------------------------------------------------------
# Container prose in the citation path is context, not path
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _ParseStatus:
    value: str


@dataclass
class _StubFragment:
    text: str
    citation_label: str | None = None
    citation_path: str | None = None
    parse_status: _ParseStatus = _ParseStatus("parsed")


def test_split_citation_path_separates_the_container_sentence() -> None:
    structural, descriptive = split_citation_path(
        "Part V > 135 > [The maximum required side setback for any main "
        "building shall be] > (a)"
    )
    assert structural == "part v > 135 > (a)"
    assert descriptive == "the maximum required side setback for any main building shall be"


def test_split_citation_path_leaves_an_ordinary_path_whole() -> None:
    assert split_citation_path("Part V > 198 > (f)") == ("part v > 198 > (f)", "")
    assert split_citation_path(None) == ("", "")


def test_bracketed_container_prose_earns_no_path_weight() -> None:
    """The inversion ABS-488 introduced, stated as arithmetic.

    Before ABS-492 this fragment banked +35 for the query phrase sitting inside
    its path, and +12 for each of "side" and "setback" — 59 points for words
    only its container says, against the 9 the section that states the rule
    earns for its own text. The clause now scores the parse-status bonus alone.
    """
    clause = _StubFragment(
        text="(a) 3 metres.",
        citation_path=(
            "Part V > 135 > [The maximum required side setback for any main "
            "building shall be] > (a)"
        ),
    )
    detail = score_fragment_detail(clause, "side setback")
    assert detail.matched_tokens == frozenset()
    assert detail.score == 1.0


def test_structural_path_tokens_keep_their_weight() -> None:
    """Demoting the prose must not demote the citation itself.

    +35 for the query sitting inside the structural path, +12 for the token
    hitting it, +1 parsed — the citation rungs a path-shaped query has always
    earned, unchanged.
    """
    clause = _StubFragment(
        text="(a) 3 metres.",
        citation_path="Part V > 135 > [Anything at all] > (a)",
    )
    detail = score_fragment_detail(clause, "135")
    assert detail.matched_tokens == frozenset({"135"})
    assert detail.score == 35.0 + 12.0 + 1.0


def test_an_exact_echo_of_the_stored_path_still_scores_as_an_exact_path() -> None:
    """Both spellings of "this is the path" reach the top rung.

    A model that echoes the path as the ingest stores it (container sentence
    and all) and one that cites it the way a reader would must land in the same
    place — otherwise demoting the prose would quietly break exact citations.
    """
    stored = "Part V > 135 > [Anything at all] > (a)"
    clause = _StubFragment(text="(a) 3 metres.", citation_path=stored)
    assert score_fragment_detail(clause, stored).score >= 100.0
    assert score_fragment_detail(clause, "Part V > 135 > (a)").score >= 100.0


# ----------------------------------------------------------------------
# The guards that keep inherited scope from becoming a constant
# ----------------------------------------------------------------------


def test_scope_a_fragment_already_states_is_not_paid_for_twice(
    corpus: SeededCorpus,
) -> None:
    """Section 229 and its unparented twin share every word.

    Query only the dimension the two of them state themselves: the section's
    chapter repeats nothing the section does not already say, so the two must
    score identically. Any gap would be the context channel double-counting.
    """
    scores = _search(corpus, "side setback")
    assert scores[corpus.section_id] == scores[corpus.unparented_twin_id]


def test_a_token_that_describes_the_whole_corpus_carries_no_scope(
    corpus: SeededCorpus,
) -> None:
    """"a" is in the chapter heading and in nearly every filler clause.

    Crediting it as inherited scope would add a near-constant to every fragment
    that happens to have a parent, which is the failure mode the
    document-frequency cut exists to prevent — so the parented section and its
    unparented twin stay level.
    """
    scores = _search(corpus, "a metres")
    assert scores[corpus.section_id] == scores[corpus.unparented_twin_id]


def test_a_rare_token_from_the_chapter_does_carry_scope(
    corpus: SeededCorpus,
) -> None:
    """The paired positive: the same comparison, with a discriminating term."""
    scores = _search(corpus, "ER-3 metres")
    assert scores[corpus.section_id] > scores[corpus.unparented_twin_id]


# ----------------------------------------------------------------------
# The ancestor index
# ----------------------------------------------------------------------


def test_ancestor_index_returns_the_chain_nearest_parent_first(
    corpus: SeededCorpus,
) -> None:
    with session_scope(corpus.db_url) as session:
        fragments = session.query(SourceFragment).all()
        index = AncestorIndex.build(session, fragments)
        assert index.chain(corpus.list_item_id) == (
            corpus.section_id,
            corpus.chapter_id,
        )
        assert index.chain(corpus.chapter_id) == ()


def test_ancestor_index_survives_a_parent_cycle(corpus: SeededCorpus) -> None:
    """A malformed tree is a shorter chain, never a hang.

    ``parent_fragment_id`` is a plain self-FK with no cycle constraint, and the
    scorer walks it for every in-scope fragment on every request — the one
    place where a bad ingest would turn into an unkillable request.
    """
    with session_scope(corpus.db_url) as session:
        chapter = session.get(SourceFragment, corpus.chapter_id)
        chapter.parent_fragment_id = corpus.list_item_id
        session.flush()
        fragments = session.query(SourceFragment).all()
        index = AncestorIndex.build(session, fragments)
        chain = index.chain(corpus.list_item_id)
        assert chain == (corpus.section_id, corpus.chapter_id)
