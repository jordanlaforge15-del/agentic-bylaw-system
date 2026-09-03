"""Compact-citation suggestion ranking for ``lookup_citation`` (ABS-461).

The advisor reads "Clause 198(1)(f)" out of the corpus and asks for exactly
that. The stored path is ``Part V > 198 > (f)``: no ``(1)`` at all, because the
ingest folded subsection 198(1) into its section fragment. rapidfuzz scores the
whole string, so it ranked short unrelated paths ending in "(f)" above the right
one and the clause was unreachable.

DoD 4 of ABS-461 requires ``lookup_citation(document_id=4,
citation_path="198(1)(f)")`` to resolve to the "2.5 metres elsewhere" clause,
exactly or via the ABS-261 suggestion path.

**ABS-488 moved the corpus under this test.** A clause used to carry the sticky
heading it sat under (``Part V > 198 > [Side Setback Requirements] > (f)``);
it now carries the container that actually scopes it, which for 198's clauses is
the section itself. The paths below are lifted from the repathed dev corpus, and
a heading segment survives in the 173/209 entries because there the heading does
interrupt the subsection — the ranker has to stay indifferent to both shapes.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalService
from bylaw_retrieval.retrieval.service import _structural_citation_rank

from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

SEPARATION = "[Minimum Separation Distances]"

# Paths lifted from document_id=4 (Regional Centre LUB) after the ABS-461
# repair and the ABS-488 repath, including the near-misses that used to
# outrank the right answer.
CORPUS_PATHS = [
    "Part V > 198",
    "Part V > 198 > (a)",
    "Part V > 198 > (a) > (i)",
    "Part V > 198 > (b)",
    "Part V > 198 > (d)",
    "Part V > 198 > (f)",
    f"Part V > 173 > (2.5) > {SEPARATION} > (f)",
    f"Part V > 209 > (2.5) > {SEPARATION} > (f)",
    "Part I > 76 > 76.5 > [Dartmouth Cove (DC) Special Area] > (f)",
    "Part X > 499 > (171) > (b) > (i)",
    "Part I > [Table 1A]",
]


def _seed(db_url: str) -> int:
    create_all(db_url)
    with session_scope(db_url) as session:
        document = Document(
            municipality="HRM",
            bylaw_name="Regional Centre Land Use By-Law",
            source_path="rc.pdf",
            file_hash="abs461-structural-rank",
            mime_type="application/pdf",
            page_count=457,
            parser_version="docling:halifax",
        )
        session.add(document)
        session.flush()
        for order, path in enumerate(CORPUS_PATHS):
            session.add(
                SourceFragment(
                    document_id=document.id,
                    fragment_type=FragmentType.CLAUSE,
                    citation_label=path.rsplit(" > ", 1)[-1],
                    citation_path=path,
                    page_start=1,
                    page_end=1,
                    reading_order_start=order,
                    reading_order_end=order,
                    text=(
                        "(f) 2.5 metres elsewhere."
                        if path == "Part V > 198 > (f)"
                        else f"Body text for {path}."
                    ),
                    parse_status=ParseStatus.PARSED,
                    confidence=0.9,
                    source_block_ids_json=[order],
                    metadata_json={},
                )
            )
        return document.id


def test_compact_citation_resolves_to_the_catch_all_setback(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'rank.db'}"
    document_id = _seed(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).lookup_citation(
            CitationLookupRequest(citation_path="198(1)(f)", document_id=document_id)
        )

    assert response.suggestions[0] == "Part V > 198 > (f)"


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("198(1)(a)", "Part V > 198 > (a)"),
        ("198(1)(d)", "Part V > 198 > (d)"),
        # A subclause one level further down resolves the same way.
        ("198(1)(a)(i)", "Part V > 198 > (a) > (i)"),
        # Decimal section anchors work the same way.
        ("173(2.5)(f)", f"Part V > 173 > (2.5) > {SEPARATION} > (f)"),
    ],
)
def test_sibling_clauses_rank_first_too(tmp_path: Path, requested: str, expected: str):
    db_url = f"sqlite:///{tmp_path / 'rank.db'}"
    document_id = _seed(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).lookup_citation(
            CitationLookupRequest(citation_path=requested, document_id=document_id)
        )

    assert response.suggestions[0] == expected


def test_prose_lookups_are_left_to_the_fuzzy_ranker(tmp_path: Path):
    """ABS-261's "Table 1A" case must keep working untouched."""
    db_url = f"sqlite:///{tmp_path / 'rank.db'}"
    document_id = _seed(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).lookup_citation(
            CitationLookupRequest(citation_path="Table 1A", document_id=document_id)
        )

    assert response.match is None
    assert response.suggestions[0] == "Part I > [Table 1A]"


def test_exact_paths_still_short_circuit(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'rank.db'}"
    document_id = _seed(db_url)

    with session_scope(db_url) as session:
        response = RetrievalService(session).lookup_citation(
            CitationLookupRequest(
                citation_path="Part V > 198 > (f)", document_id=document_id
            )
        )

    assert response.match is not None
    assert response.match.text == "(f) 2.5 metres elsewhere."
    assert response.suggestions == []


class TestStructuralRank:
    """Unit-level checks on the ranker's admission rules."""

    def test_only_the_matching_section_and_leaf_qualify(self):
        """Both the section anchor and the leaf token have to line up.

        Every other "(f)" in the corpus belongs to a different section, and
        198's other clauses are not clause (f).
        """
        assert _structural_citation_rank("198(1)(f)", CORPUS_PATHS) == ["Part V > 198 > (f)"]

    def test_a_leaf_match_under_the_wrong_section_is_rejected(self):
        assert _structural_citation_rank("198(1)(f)", [f"Part V > 173 > (2.5) > {SEPARATION} > (f)"]) == []

    def test_the_right_section_with_the_wrong_leaf_is_rejected(self):
        assert _structural_citation_rank("198(1)(f)", ["Part V > 198 > (a)"]) == []

    def test_non_citation_requests_are_declined(self):
        assert _structural_citation_rank("Table 1A", CORPUS_PATHS) == []
        assert _structural_citation_rank("Side Setback Requirements", CORPUS_PATHS) == []
        # A bare clause letter names no section to anchor to.
        assert _structural_citation_rank("(f)", CORPUS_PATHS) == []

    def test_a_full_token_match_outranks_a_partial_one(self):
        candidates = [
            "Part V > 198 > (b)",  # 2 of 3 tokens: no "(1)"
            "Part V > 198 > (1) > (b)",  # all 3
        ]
        assert _structural_citation_rank("198(1)(b)", candidates)[0] == "Part V > 198 > (1) > (b)"

    def test_shallower_paths_win_ties(self):
        """Between two equal-token matches, prefer the one with less extra depth."""
        candidates = [
            "Part V > 198 > (9) > (b)",
            "Part V > 198 > (b)",
        ]
        assert _structural_citation_rank("198(1)(b)", candidates)[0] == "Part V > 198 > (b)"
