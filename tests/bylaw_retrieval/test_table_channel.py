"""ABS-500: a dimensional standard that lives in a table is retrievable.

Before this, ``RetrievalService.search`` ranked ``source_fragment`` rows and
nothing else. Tables reached the model only as ``related_tables`` attached to a
fragment that had *already* ranked, so "what is the maximum height in HR-2" —
whose answer is a cell of a matrix, not a sentence — was reachable only by luck:
some prose fragment near the table had to rank first on its own words.

The definition of done these tests pin:

1. **The cell is reachable on its own.** A dimensional question whose answer is
   a cell retrieves that cell without any neighbouring prose fragment ranking
   first. ``test_cell_retrieved_without_neighbouring_prose`` deletes the crutch
   — the table's introducing sentence says nothing the query asks about — and
   the answer still comes back.
2. **The binding does the work, not the keywords.** The answer cell's text is
   "12.0 metres"; the zone it belongs to is stated only by
   ``table_axis_binding``. A ranker that keyword-matched cell text could not
   find it, and ``test_binding_selects_the_right_column`` proves the *right*
   column is chosen rather than a sibling zone's.
3. **The citation shape holds.** A cell is not a ``source_fragment``; it is
   cited through the provision that introduces its table, and the match names
   the table, the coordinates and the axis labels
   (``test_cell_match_citation_shape``, ``test_anchor_falls_back_to_reading_order``).
   Documented in ``docs/ABS-500-TABLE-CHANNEL.md``.
4. **The guards hold.** Glyph-only permission markers and header cells are not
   answers (``test_glyph_and_header_cells_are_not_answers``), and a table
   addressed on only one axis does not rank
   (``test_single_axis_match_does_not_rank``).

The service is driven end to end against a seeded sqlite corpus rather than a
stub: the thing under test is the interaction between the scope query, the
axis-binding index, the anchor resolver and the fuser.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval import RetrievalRequest, RetrievalService
from bylaw_retrieval.retrieval.tables import _is_value_cell
from layer1.db.base import (
    Document,
    SemanticEntity,
    SourceFragment,
    SourceTable,
    SourceTableCell,
    TableAxisBinding,
    TableSemanticProfile,
)
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

#: The sentence that introduces the height matrix. Deliberately says nothing
#: about heights or about HR-2 — it is the crutch the old behaviour needed, and
#: removing it is what makes the test about the table channel.
INTRODUCING_PROVISION = (
    "94 Every main building shall comply with the following requirements:"
)

#: The question. Its answer, "12.0 metres", is a cell; the zone that selects the
#: column is stated by the axis binding, not by the cell's text.
DIMENSIONAL_QUERY = "What is the maximum building height in the HR-2 zone?"


def _add_document(session, *, bylaw_name="Regional Centre Land Use By-Law") -> Document:
    document = Document(
        municipality="HRM",
        bylaw_name=bylaw_name,
        source_path="regional-centre.txt",
        source_url=None,
        file_hash=f"abs500-{bylaw_name}",
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
    page: int = 1,
    source_block_ids: list[int] | None = None,
) -> SourceFragment:
    fragment = SourceFragment(
        document_id=document_id,
        fragment_type=fragment_type,
        citation_label=citation_label,
        citation_path=citation_path,
        parent_fragment_id=parent.id if parent is not None else None,
        page_start=page,
        page_end=page,
        reading_order_start=1,
        reading_order_end=1,
        text=text,
        parse_status=ParseStatus.PARSED,
        confidence=1.0,
        source_block_ids_json=source_block_ids or [],
        metadata_json={},
        attribute_tags=[],
    )
    session.add(fragment)
    session.flush()
    return fragment


def _add_table(
    session,
    document_id: int,
    *,
    grid: list[list[str]],
    caption: str | None = None,
    parent: SourceFragment | None = None,
    page: int = 1,
    source_block_id: int | None = None,
) -> SourceTable:
    table = SourceTable(
        document_id=document_id,
        parent_fragment_id=parent.id if parent is not None else None,
        caption=caption,
        page_start=page,
        page_end=page,
        parse_status=ParseStatus.PARSED,
        metadata_json={"source_block_id": source_block_id} if source_block_id else {},
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
                    row_header_path=None,
                    col_header_path=None,
                    text=text,
                    bbox_json=None,
                    metadata_json={},
                )
            )
    session.flush()
    return table


def _bind_axis(
    session,
    table: SourceTable,
    *,
    axis: str,
    index: int,
    raw_label: str,
    entity_type: str,
    canonical_name: str,
) -> None:
    entity = (
        session.query(SemanticEntity)
        .filter_by(
            document_id=table.document_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
        )
        .one_or_none()
    )
    if entity is None:
        entity = SemanticEntity(
            document_id=table.document_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            aliases_json=[],
            source_text=None,
            confidence=1.0,
            metadata_json={},
        )
        session.add(entity)
        session.flush()
    session.add(
        TableAxisBinding(
            table_id=table.id,
            axis=axis,
            index=index,
            entity_id=entity.id,
            raw_label=raw_label,
            confidence=1.0,
            metadata_json={},
        )
    )
    session.flush()


@dataclass(frozen=True)
class SeededCorpus:
    db_url: str
    document_id: int
    intro_fragment_id: int
    table_id: int
    parentless_table_id: int
    parentless_anchor_id: int


@pytest.fixture()
def corpus(tmp_path: Path) -> SeededCorpus:
    """A height matrix under a provision that gives the query no purchase.

        94 Every main building shall comply with the following requirements:

            |             | Maximum Building Height | Maximum Lot Coverage |
            | HR-1 Zone   | 8.0 metres              | 45%                  |
            | HR-2 Zone   | 12.0 metres             | 60%                  |

    Nothing in the introducing provision names a height or a zone, and no cell
    text contains "HR-2" — the HR-2 row is identified by its axis binding. Prose
    decoys elsewhere in the document *do* say "HR-2" and *do* say "height", the
    way a real by-law's abutting-zone clauses do, so a ranker that scored a
    passing mention would surface them instead.
    """
    db_url = f"sqlite:///{tmp_path / 'tables.db'}"
    create_all(db_url)
    with session_scope(db_url) as session:
        document = _add_document(session)
        intro = _add_fragment(
            session,
            document.id,
            text=INTRODUCING_PROVISION,
            citation_label="94",
            citation_path="Part V > 94",
            source_block_ids=[10],
        )
        table = _add_table(
            session,
            document.id,
            grid=[
                ["", "Maximum Building Height", "Maximum Lot Coverage"],
                ["HR-1 Zone", "8.0 metres", "45%"],
                ["HR-2 Zone", "12.0 metres", "60%"],
            ],
            caption=None,
            parent=intro,
            source_block_id=11,
        )
        session.add(
            TableSemanticProfile(
                table_id=table.id,
                profile_type="dimensional_matrix",
                row_axis_type="zone",
                column_axis_type="standard",
                value_type="numeric_or_text",
                metadata_json={},
            )
        )
        _bind_axis(
            session,
            table,
            axis="row",
            index=1,
            raw_label="HR-1 Zone",
            entity_type="zone",
            canonical_name="HR-1",
        )
        _bind_axis(
            session,
            table,
            axis="row",
            index=2,
            raw_label="HR-2 Zone",
            entity_type="zone",
            canonical_name="HR-2",
        )

        # Prose decoys: they name the zone and the dimension the way an
        # abutting-zone or landscaping clause does, without answering anything.
        for index, decoy in enumerate(
            (
                "Where a lot abuts another lot, any portion of which, is zoned "
                "HR-2, HR-1, ER-3 or ER-2, a landscaped buffer is required.",
                "The maximum building height of a communication tower is not "
                "regulated by this Part.",
                "In any HR-2 zone, refuse containers shall be screened from a "
                "street by an opaque enclosure.",
            ),
            start=1,
        ):
            _add_fragment(
                session,
                document.id,
                text=decoy,
                citation_label=f"41{index}",
                citation_path=f"Part X > 41{index}",
                source_block_ids=[100 + index],
            )

        # Filler. The table channel scores on the terms rare enough in the
        # corpus to carry scope — the same document-frequency cut the text
        # channel applies — so without a body of fragments to measure against,
        # every term looks common, nothing survives the cut and the channel
        # correctly declines to rank anything. Real corpora are 7,100 fragments;
        # this is the smallest stand-in that makes the cut behave like one.
        for index in range(40):
            _add_fragment(
                session,
                document.id,
                text=(
                    f"5{index:02d} No person shall deposit refuse, debris or fill "
                    "upon a watercourse bank without a permit issued under this "
                    "By-law."
                ),
                citation_label=f"5{index:02d}",
                citation_path=f"Part XI > 5{index:02d}",
                source_block_ids=[300 + index],
            )

        # A second, parentless table: the ingest set no parent_fragment_id, so
        # the anchor has to come from the reading order.
        parentless_anchor = _add_fragment(
            session,
            document.id,
            text="203 Off-street loading spaces shall comply with the following:",
            citation_label="203",
            citation_path="Part X > 203",
            page=2,
            source_block_ids=[200],
        )
        parentless = _add_table(
            session,
            document.id,
            grid=[
                ["", "Minimum Loading Space Length"],
                ["CH-1 Zone", "9.0 metres"],
            ],
            caption=None,
            parent=None,
            page=2,
            source_block_id=201,
        )
        _bind_axis(
            session,
            parentless,
            axis="row",
            index=1,
            raw_label="CH-1 Zone",
            entity_type="zone",
            canonical_name="CH-1",
        )
        return SeededCorpus(
            db_url=db_url,
            document_id=document.id,
            intro_fragment_id=intro.id,
            table_id=table.id,
            parentless_table_id=parentless.id,
            parentless_anchor_id=parentless_anchor.id,
        )


def _search(corpus: SeededCorpus, query: str, limit: int = 10):
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        response = service.search(RetrievalRequest(query=query, limit=limit))
        # Detach from the session: the assertions read pydantic models, and the
        # session closes with the context manager.
        return list(response.matches)


def _table_match(matches):
    """The first match the table channel surfaced, or None."""
    for match in matches:
        if "table" in match.retrieval_channels and match.table_matches:
            return match
    return None


# ----------------------------------------------------------------------
# The definition of done.
# ----------------------------------------------------------------------


def test_cell_retrieved_without_neighbouring_prose(corpus: SeededCorpus) -> None:
    """The answer cell is retrieved though no prose fragment states the answer.

    The introducing provision says "Every main building shall comply with the
    following requirements" — no height, no zone. Under the old behaviour that
    fragment could not rank on this query, and the table was only ever attached
    to a fragment that had ranked, so the cell was unreachable.
    """
    matches = _search(corpus, DIMENSIONAL_QUERY)
    surfaced = _table_match(matches)
    assert surfaced is not None, "the table channel surfaced nothing"
    cell = surfaced.table_matches[0]
    assert cell.text == "12.0 metres"
    assert surfaced.fragment_id == corpus.intro_fragment_id


def test_binding_selects_the_right_column(corpus: SeededCorpus) -> None:
    """HR-2's row is chosen over HR-1's, and the height column over coverage.

    Neither choice is available to keyword matching: "12.0 metres" shares no
    term with the query, and both rows sit under identically-worded headers.
    """
    cell = _table_match(_search(corpus, DIMENSIONAL_QUERY)).table_matches[0]
    assert cell.row_label == "HR-2 Zone"
    assert cell.col_label == "Maximum Building Height"
    assert cell.bound_by == ["row bound to zone 'HR-2'"]

    # The sibling zone resolves to the sibling row, which is the whole point of
    # binding rather than matching: one query term apart, a different answer.
    sibling = _table_match(
        _search(corpus, "What is the maximum building height in the HR-1 zone?")
    ).table_matches[0]
    assert sibling.row_label == "HR-1 Zone"
    assert sibling.text == "8.0 metres"


def test_cell_match_citation_shape(corpus: SeededCorpus) -> None:
    """A ranked cell is cited through the provision that introduces its table.

    This is the contract documented in ``docs/ABS-500-TABLE-CHANNEL.md``: a cell
    is not a ``source_fragment``, so the citation is the anchor's
    ``citation_path`` / ``citation_label``, and the cell's own address (table,
    coordinates, axis labels, page) rides alongside it.
    """
    surfaced = _table_match(_search(corpus, DIMENSIONAL_QUERY))
    cell = surfaced.table_matches[0]

    assert cell.anchor_fragment_id == corpus.intro_fragment_id
    assert cell.citation_path == "Part V > 94"
    assert cell.citation_label == "94"
    assert cell.table_id == corpus.table_id
    assert (cell.row_index, cell.col_index) == (2, 1)
    assert cell.page_start == 1
    assert cell.municipality == "HRM"
    assert cell.bylaw_name == "Regional Centre Land Use By-Law"
    assert cell.profile_type == "dimensional_matrix"
    # The enclosing match still identifies itself as fragment-shaped, so every
    # existing consumer of RetrievalMatch keeps working.
    assert surfaced.fragment_id == cell.anchor_fragment_id


def test_anchor_falls_back_to_reading_order(corpus: SeededCorpus) -> None:
    """A table the ingest left parentless is still citable.

    63 of the 96 tables in the dev corpus carry no ``parent_fragment_id``, and
    every table in the Halifax Mainland by-law does. The anchor is then the
    fragment immediately before the table in the parser's block ordering — the
    provision a reader would cite the table under.
    """
    surfaced = _table_match(
        _search(corpus, "What is the minimum loading space length in the CH-1 zone?")
    )
    assert surfaced is not None
    cell = surfaced.table_matches[0]
    assert cell.table_id == corpus.parentless_table_id
    assert cell.anchor_fragment_id == corpus.parentless_anchor_id
    assert cell.citation_path == "Part X > 203"


def test_table_channel_is_named_in_retrieval_channels(corpus: SeededCorpus) -> None:
    """The caller can see which channel found the match."""
    surfaced = _table_match(_search(corpus, DIMENSIONAL_QUERY))
    assert "table" in surfaced.retrieval_channels


def test_cells_are_attached_even_when_include_tables_is_false(
    corpus: SeededCorpus,
) -> None:
    """``include_tables=False`` suppresses the bulk dump, not the citation.

    A match that ranked *because of* a cell is not groundable without the cell.
    ``related_tables`` — everything near the fragment, query-relevant or not —
    is the thing the flag governs.
    """
    with session_scope(corpus.db_url) as session:
        service = RetrievalService(session)
        response = service.search(
            RetrievalRequest(query=DIMENSIONAL_QUERY, limit=10, include_tables=False)
        )
        surfaced = _table_match(response.matches)
        assert surfaced is not None
        assert surfaced.table_matches[0].text == "12.0 metres"
        assert surfaced.related_tables == []


# ----------------------------------------------------------------------
# Guards.
# ----------------------------------------------------------------------


def test_single_axis_match_does_not_rank(corpus: SeededCorpus) -> None:
    """A table addressed on one axis only is attached, never ranked.

    "HR-2" alone identifies a row but says nothing about which column answers,
    so promoting the table would be promoting an arbitrary cell of it.
    """
    matches = _search(corpus, "HR-2")
    assert _table_match(matches) is None


@pytest.mark.parametrize(
    ("text", "row_label", "col_label", "expected"),
    [
        ("12.0 metres", "HR-2 Zone", "Maximum Building Height", True),
        ("50%", "North End Halifax 2 (NEH-2)", "Maximum Required Lot Coverage", True),
        # Permission matrices mark cells with symbols from a private-use font
        # range; stripped of whitespace they are non-empty but carry nothing a
        # reader can quote back. The marker's *meaning* is resolved by
        # get_permitted_use through the permission-marker vocabulary, never by
        # quoting the glyph, so a glyph cell is not an answer this channel can
        # return.
        ("\uf020", "Residential use", "HR-2 Zone", False),
        ("\u2463 \uf020", "Residential use", "HR-2 Zone", False),
        ("", "HR-2 Zone", "Maximum Building Height", False),
        ("   ", "HR-2 Zone", "Maximum Building Height", False),
        # A header repeated as its own axis label is the question, not the
        # answer: "ER-3 is what you find under the column ER-3" tells a reader
        # nothing.
        ("ER-3", "Prohibited in all zones", "ER-3", False),
        ("hr-2 zone", "HR-2 Zone", "Maximum Building Height", False),
    ],
)
def test_glyph_and_header_cells_are_not_answers(
    text: str, row_label: str, col_label: str, expected: bool
) -> None:
    """Which cells can be an answer at all — the rule, stated as a rule.

    Pure, so it is tested as a pure function rather than through a contrived
    corpus: the classification depends on nothing but the cell and its two axis
    labels.
    """
    assert _is_value_cell(text, row_label, col_label) is expected
