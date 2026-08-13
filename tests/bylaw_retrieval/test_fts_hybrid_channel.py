"""ABS-494: the FTS hybrid text channel, and the blend that folds it in.

The scoring/fusion decision this issue settled is recorded in
``docs/decisions/ABS-494-SCORING-FUSION-DECISION.md``; the evidence is
``evals/retrieval/experiments/RESULTS.md``. What shipped is a **hybrid**: a
Postgres full-text ranking blended into the text channel beside the existing
path/context ladder, at a 50/50 split chosen from a weight sweep.

What is worth pinning here is not "the blend computes a weighted sum" — that is
three lines and restating them in a test proves nothing. It is the set of
properties the decision doc claims and the production comments rely on:

* the ranked SQL expression is **byte-identical to the indexed one**, because
  drift between them silently downgrades an index scan to a full-corpus
  re-tsvectorisation that still returns the right answer;
* the tsquery is a disjunction, because a conjunction over a nine-term question
  matches no by-law clause at all;
* the blend hands the text side back on the **ladder's scale**, because fusion
  downstream is a ``max()`` across channels denominated in ladder units and a
  normalised text side would lose every comparison it entered;
* ``discriminating`` survives the blend, because ``search()`` reads it off this
  object to scope the table channel;
* a fragment only FTS can see enters the channel — that is the recall the
  hybrid was shipped to buy;
* the channel is inert off Postgres, so the sqlite-backed suites still measure
  the ladder alone.

The blend is exercised directly rather than through a seeded corpus: it is a
pure function of two score maps, and driving it through the service would test
the seeding more than the rule.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bylaw_retrieval.retrieval.channels import TextChannelScores
from bylaw_retrieval.retrieval.service import (
    FTS_VECTOR_SQL,
    RetrievalService,
    fts_or_query,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "alembic" / "versions" / "0002_layer2_retrieval_schema.py"


# ----------------------------------------------------------------------
# The indexed expression
# ----------------------------------------------------------------------


def test_ranked_expression_is_the_indexed_expression():
    """The tsvector we rank on must be the one 0002 built a GIN index over.

    This is the test that justifies the constant existing at all. A drifted
    expression does not fail: Postgres cheerfully computes ``to_tsvector`` over
    all 7,100 rows and returns a correct ranking, several hundred milliseconds
    later. Correctness tests are blind to it by construction, so the guard has
    to be a textual one against the migration itself.
    """
    source = MIGRATION.read_text(encoding="utf8")
    index_call = source[source.index('"ix_source_fragment_text_tsv"') :]
    indexed = re.search(r'sa\.text\(\s*"([^"]+)"\s*\)', index_call)
    assert indexed, "could not locate the indexed expression in 0002"
    assert FTS_VECTOR_SQL == indexed.group(1)


# ----------------------------------------------------------------------
# fts_or_query
# ----------------------------------------------------------------------


def test_query_is_a_disjunction_not_a_conjunction():
    # The whole reason websearch_to_tsquery is not used. A conjunction over
    # this many terms matches nothing in the corpus.
    built = fts_or_query("side setback in the ER-3 zone")
    assert "&" not in built
    assert built == "side | setback | in | the | er | 3 | zone"


def test_hyphenated_compounds_are_split_not_quoted():
    """``'er-3'`` would parse as a phrase and smuggle an AND into the OR."""
    assert fts_or_query("ER-3") == "er | 3"
    assert "'" not in fts_or_query("HR-2 CDD-1")


def test_stop_words_are_left_for_the_english_dictionary_to_drop():
    # Deliberate: to_tsquery('english', …) drops them itself, so this tokenizer
    # has no stop list of its own to maintain and drift.
    assert "the" in fts_or_query("the setback")


def test_terms_are_deduplicated_preserving_order():
    assert fts_or_query("setback setback side") == "setback | side"


def test_empty_and_punctuation_only_queries_produce_no_query():
    assert fts_or_query("") == ""
    assert fts_or_query("   ") == ""
    assert fts_or_query("?? -- ,") == ""


# ----------------------------------------------------------------------
# The blend
# ----------------------------------------------------------------------


def blend(text: dict[int, float], fts: dict[int, float], *, discriminating=frozenset()):
    scored = TextChannelScores(text, discriminating=discriminating)
    return RetrievalService._blend_fts_into_text(
        RetrievalService.__new__(RetrievalService), scored, fts
    )


def test_blend_returns_the_text_side_on_the_ladders_own_scale():
    """The invariant fusion depends on: magnitude in, magnitude out.

    ``_merge_channel_scores`` takes a max() across text, spatial (100.0 for a
    containment) and table (ladder-scored). A text side returned on [0, 1]
    would lose every one of those comparisons, and the retriever would quietly
    become "table channel with the text side deleted" — while still passing
    every correctness test, because the fragments it does return are fine.
    """
    result = blend({1: 40.0, 2: 10.0}, {1: 0.9, 3: 0.5})
    assert max(result.values()) == pytest.approx(40.0)


def test_blend_admits_a_fragment_only_fts_can_see():
    # This is the recall the hybrid was shipped to buy: definitions are
    # ingested as PROSE with a NULL citation_path, so the path ladder cannot
    # see them at all and the body tsvector is the only channel that can.
    result = blend({1: 40.0}, {2: 0.9})
    assert 2 in result


def test_blend_lets_fts_reorder_the_ladders_ranking():
    """Agreement between the two sub-channels beats a lone ladder score."""
    # Fragment 2 is mid-table on the ladder but top of the FTS ranking;
    # fragment 1 leads the ladder and FTS does not rank it at all.
    result = blend({1: 40.0, 2: 30.0}, {2: 1.0})
    assert result[2] > result[1]


def test_blend_preserves_discriminating_tokens():
    """search() reads this off the blended object to scope the table channel.

    Dropping it here would silently re-scope a channel this method has no
    business touching, and the table channel's recall would move for a reason
    no one would think to look for in the text blend.
    """
    tokens = frozenset({"setback", "er-3"})
    result = blend({1: 40.0}, {2: 0.9}, discriminating=tokens)
    assert isinstance(result, TextChannelScores)
    assert result.discriminating == tokens


def test_blend_is_a_no_op_when_fts_is_empty():
    """Off Postgres the channel returns {}, and the ladder must pass through
    untouched — not rescaled, not reordered. Every sqlite-backed suite in this
    repo depends on it."""
    ladder = {1: 40.0, 2: 10.0}
    result = blend(ladder, {})
    assert dict(result) == ladder


def test_blend_is_a_no_op_when_the_ladder_is_empty():
    # There is no ladder scale to borrow, and inventing one would let a query
    # the ladder rejected outright come back through the FTS side at full
    # strength.
    result = blend({}, {1: 0.9})
    assert dict(result) == {}


def test_blend_ignores_a_degenerate_all_zero_channel():
    assert dict(blend({1: 0.0}, {1: 0.9})) == {1: 0.0}


def test_weight_is_the_swept_midpoint():
    """Pinned so a future edit has to argue with the sweep, not just the diff.

    Every weight in [0.35, 0.70] beats the control in
    evals/retrieval/experiments/RESULTS.md; 0.40 and 0.50 tie for best recall
    and 0.50 posts the better MRR. A change here is a re-decision, and should
    come with a re-run.
    """
    assert RetrievalService._FTS_TEXT_WEIGHT == 0.5
