"""Compact-projection coverage for the ABS-500 table channel.

The advisor LLM reads ``compact_match``'s output, not the raw
``RetrievalMatch``. A table-channel hit is anchored on the provision that
introduces the table — "94 Every main building shall comply with the following
requirements:" — which on its own says nothing. If the cell that made the match
rank does not survive projection, the model receives an anchor and no answer,
which is a worse outcome than not ranking the table at all.

Same argument ABS-492 made for ``ancestor_chain``: the evidence that earned the
rank has to travel with the match.
"""
from __future__ import annotations

from advisor.chat.compact import compact_match
from bylaw_retrieval.retrieval.schemas import RetrievalMatch, TableCellMatch


def _cell(**kwargs) -> TableCellMatch:
    base = dict(
        table_id=1076,
        document_id=4,
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        caption="Table 10: Maximum required lot coverage",
        profile_type="dimensional_matrix",
        anchor_fragment_id=7357,
        citation_path="Part V > [Table 10]",
        citation_label="Table 10",
        page_start=191,
        page_end=191,
        row_index=3,
        col_index=1,
        row_label="North End Halifax 2 (NEH-2)",
        col_label="Maximum Required Lot Coverage (%)",
        text="50%",
        score=54.0,
        bound_by=["row bound to zone 'NEH-2'"],
    )
    base.update(kwargs)
    return TableCellMatch(**base)


def _match(**kwargs) -> RetrievalMatch:
    base = dict(
        fragment_id=7357,
        document_id=4,
        municipality="HRM",
        bylaw_name="Regional Centre Land Use By-Law",
        fragment_type="section",
        page_start=191,
        page_end=191,
        parse_status="parsed",
        text="94 Every main building shall comply with the following requirements:",
        score=54.0,
        retrieval_channels=["table", "text"],
    )
    base.update(kwargs)
    return RetrievalMatch(**base)


def test_the_ranked_cell_survives_projection_with_its_citation():
    out = compact_match(_match(table_matches=[_cell()]))

    assert out["retrieval_channels"] == ["table", "text"]
    cell = out["table_matches"][0]
    # The value the model quotes, and the two labels that let it say which
    # standard for which zone.
    assert cell["value"] == "50%"
    assert cell["row_label"] == "North End Halifax 2 (NEH-2)"
    assert cell["col_label"] == "Maximum Required Lot Coverage (%)"
    # The citation the answer is grounded in.
    assert cell["citation_path"] == "Part V > [Table 10]"
    assert cell["citation_label"] == "Table 10"
    assert cell["table_id"] == 1076
    assert cell["page_start"] == 191
    assert cell["bound_by"] == ["row bound to zone 'NEH-2'"]


def test_a_match_with_no_table_cell_gains_no_key():
    """The common case — a text-channel match — pays nothing for this."""
    out = compact_match(_match(retrieval_channels=["text"]))
    assert "table_matches" not in out


def test_optional_labels_are_omitted_rather_than_nulled():
    """A table with no caption and unbound axes projects without the noise.

    40 of the corpus's 96 tables carry no caption — every Mainland table — so
    nulling instead of omitting would put dead keys in the majority of hits.
    """
    out = compact_match(
        _match(
            table_matches=[
                _cell(caption=None, row_label=None, col_label=None, bound_by=[])
            ]
        )
    )
    cell = out["table_matches"][0]
    assert set(cell) == {
        "table_id",
        "page_start",
        "page_end",
        "value",
        "citation_path",
        "citation_label",
    }
