"""Unit tests for scripts/eval_retrieval_recall.py (ABS-486).

The harness is the instrument that will be used to judge every future ranking
change, so the thing under test here is the *instrument*, not the retriever: if
the arithmetic, the label validation or the anchor resolution is wrong, a real
regression and a real improvement are indistinguishable.

Runs entirely offline. The retrieval service is a stub whose ``search`` returns
a scripted ranking, and anchors resolve through a dict — no database, no
corpus, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval_retrieval_recall import (
    MIN_QUERIES,
    REQUIRED_CATEGORIES,
    Anchor,
    LabelledQuery,
    QuerySetError,
    _db_search_fn,
    evaluate,
    load_query_set,
    report_to_dict,
    resolve_acceptable_ids,
    score_ranking,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
QUERY_SET = REPO_ROOT / "evals" / "retrieval" / "queries.json"
BASELINE = REPO_ROOT / "evals" / "retrieval" / "BASELINE.json"


# ----------------------------------------------------------------------
# Stubs
# ----------------------------------------------------------------------


class _StubMatch:
    def __init__(self, fragment_id: int) -> None:
        self.fragment_id = fragment_id


class _StubResponse:
    def __init__(self, fragment_ids: list[int]) -> None:
        self.matches = [_StubMatch(fid) for fid in fragment_ids]


class _StubService:
    """Stands in for RetrievalService: returns a scripted ranking per query.

    Records the requests it received so the tests can assert the harness built
    the production-shaped call (limit == k, the location slot populated) rather
    than only that the arithmetic downstream was right.
    """

    def __init__(self, rankings: dict[str, list[int]]) -> None:
        self._rankings = rankings
        self.requests: list[object] = []

    def search(self, request):
        self.requests.append(request)
        return _StubResponse(self._rankings.get(request.query, []))


def _query(
    query_id: str = "Q1",
    *,
    category: str = "dimensional",
    question: str = "how tall?",
    fragment_ids: tuple[int, ...] = (),
    location: dict | None = None,
) -> LabelledQuery:
    return LabelledQuery(
        id=query_id,
        category=category,
        question=question,
        anchors=(Anchor(bylaw="Regional Centre", citation_path="Part V > 1"),),
        fragment_ids=fragment_ids,
        location=location,
    )


def _resolver(mapping: dict[str, list[int]]):
    def resolve(anchor: Anchor) -> list[int]:
        return mapping.get(anchor.citation_path or anchor.text_prefix or "", [])

    return resolve


# ----------------------------------------------------------------------
# score_ranking — the arithmetic every future comparison rests on
# ----------------------------------------------------------------------


def test_hit_at_rank_one_scores_a_full_reciprocal_rank():
    hit, rank, rr, set_recall = score_ranking([7, 8, 9], [7], k=10)
    assert (hit, rank, rr, set_recall) == (True, 1, 1.0, 1.0)


def test_hit_at_rank_three_scores_one_third():
    hit, rank, rr, _ = score_ranking([1, 2, 7], [7], k=10)
    assert (hit, rank) == (True, 3)
    assert rr == pytest.approx(1 / 3)


def test_a_hit_past_k_is_a_miss():
    """The cut-off is the whole contract: rank 11 is not in the top 10.

    Off-by-one here would silently inflate every score in the file.
    """
    ranked = list(range(1, 11)) + [99]
    assert score_ranking(ranked, [99], k=10)[0] is False
    assert score_ranking(ranked, [99], k=11)[0] is True


def test_first_acceptable_fragment_sets_the_rank_not_the_best_one():
    """Any acceptable fragment ends the search — the labels are a set."""
    hit, rank, rr, set_recall = score_ranking([5, 6], [6, 5], k=10)
    assert (hit, rank, rr) == (True, 1, 1.0)
    assert set_recall == 1.0


def test_set_recall_is_the_fraction_of_the_acceptable_set_retrieved():
    _, _, rr, set_recall = score_ranking([1, 2, 3], [2, 9, 8], k=10)
    assert set_recall == pytest.approx(1 / 3)
    assert rr == pytest.approx(1 / 2)


def test_empty_ranking_is_a_clean_miss_not_a_crash():
    assert score_ranking([], [7], k=10) == (False, None, 0.0, 0.0)


# ----------------------------------------------------------------------
# evaluate — aggregation and per-category breakdown
# ----------------------------------------------------------------------


def test_evaluate_aggregates_across_queries_and_categories():
    queries = [
        _query("Q1", category="dimensional", question="a"),
        _query("Q2", category="dimensional", question="b"),
        _query("Q3", category="definition", question="c"),
    ]
    rankings = {"a": [10], "b": [99, 99, 10], "c": [1, 2, 3]}
    service = _StubService(rankings)
    report = evaluate(
        queries,
        _db_search_fn(service, 10),
        _resolver({"Part V > 1": [10]}),
        k=10,
    )

    # Q1 hits at 1, Q2 at 3, Q3 misses.
    assert report.query_count == 3
    assert report.recall_at_k == pytest.approx(2 / 3, abs=1e-4)
    assert report.mrr == pytest.approx((1.0 + 1 / 3 + 0.0) / 3, abs=1e-4)
    assert report.by_category["dimensional"]["query_count"] == 2
    assert report.by_category["dimensional"]["recall_at_k"] == 1.0
    assert report.by_category["definition"]["recall_at_k"] == 0.0


def test_evaluate_issues_the_production_shaped_search_call():
    """limit must equal k, and a spatial query's geometry must reach the slot.

    A harness that quietly searched at limit=5 while reporting Recall@10, or
    that dropped the location, would produce a number that looks like a
    retrieval result and is not one.
    """
    point = {"type": "Point", "coordinates": [-63.5, 44.6]}
    queries = [
        _query("Q1", question="a"),
        _query("Q2", category="spatial", question="b", location={"geometry": point}),
    ]
    service = _StubService({"a": [10], "b": [10]})
    evaluate(queries, _db_search_fn(service, 10), _resolver({"Part V > 1": [10]}), k=10)

    assert [r.limit for r in service.requests] == [10, 10]
    assert service.requests[0].location is None
    assert service.requests[1].location is not None
    assert service.requests[1].location.geometry == point


def test_report_is_reproducible_for_the_same_stubbed_ranking():
    queries = [_query("Q1", question="a"), _query("Q2", question="b")]
    resolver = _resolver({"Part V > 1": [10]})
    rankings = {"a": [1, 10], "b": []}

    first = report_to_dict(
        evaluate(queries, _db_search_fn(_StubService(rankings), 10), resolver),
        provenance={"tier": "t"},
        corpus={},
    )
    second = report_to_dict(
        evaluate(queries, _db_search_fn(_StubService(rankings), 10), resolver),
        provenance={"tier": "t"},
        corpus={},
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ----------------------------------------------------------------------
# Anchor resolution — a bad label must not read as a retrieval regression
# ----------------------------------------------------------------------


def test_an_anchor_that_resolves_to_nothing_is_an_error_not_a_miss():
    with pytest.raises(QuerySetError, match="resolved to no fragment"):
        resolve_acceptable_ids(_query(), _resolver({}))


def test_an_ambiguous_anchor_is_an_error_not_a_coin_flip():
    with pytest.raises(QuerySetError, match="ambiguous"):
        resolve_acceptable_ids(_query(), _resolver({"Part V > 1": [10, 11]}))


def test_duplicate_anchors_collapse_to_one_acceptable_fragment():
    """Two routes to the same clause must not double its weight in set-recall."""
    query = LabelledQuery(
        id="Q1",
        category="dimensional",
        question="q",
        anchors=(
            Anchor(bylaw="Regional Centre", citation_path="Part V > 1"),
            Anchor(bylaw="Regional Centre", text_prefix="1 The rule"),
        ),
        fragment_ids=(),
    )
    resolver = _resolver({"Part V > 1": [10], "1 The rule": [10]})
    assert resolve_acceptable_ids(query, resolver) == [10]


# ----------------------------------------------------------------------
# Query-set validation
# ----------------------------------------------------------------------


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(payload))
    return path


def _valid_payload(count: int = MIN_QUERIES) -> dict:
    queries = []
    for index in range(count):
        queries.append(
            {
                "id": f"Q{index:03d}",
                "category": REQUIRED_CATEGORIES[index % len(REQUIRED_CATEGORIES)],
                "question": f"question {index}",
                "acceptable": [
                    {"bylaw": "Regional Centre", "citation_path": f"Part V > {index}"}
                ],
                "fragment_ids": [index],
            }
        )
    return {
        "provenance": {
            "tier": "agent-drafted, pending human spot-check",
            "authored_by": "test",
            "review_status": "unreviewed",
        },
        "queries": queries,
    }


def test_a_valid_set_loads(tmp_path: Path):
    provenance, queries = load_query_set(_write(tmp_path, _valid_payload()))
    assert provenance["tier"].startswith("agent-drafted")
    assert len(queries) == MIN_QUERIES


def test_a_set_without_a_provenance_tier_is_rejected(tmp_path: Path):
    """The tier is the thing that keeps this from being read as golden."""
    payload = _valid_payload()
    payload["provenance"].pop("tier")
    with pytest.raises(QuerySetError, match="provenance.tier"):
        load_query_set(_write(tmp_path, payload))


def test_a_short_set_is_rejected(tmp_path: Path):
    with pytest.raises(QuerySetError, match=f"at least {MIN_QUERIES}"):
        load_query_set(_write(tmp_path, _valid_payload(MIN_QUERIES - 1)))


def test_a_missing_category_is_rejected(tmp_path: Path):
    """A count check cannot see a whole category quietly disappearing."""
    payload = _valid_payload()
    for entry in payload["queries"]:
        if entry["category"] == "spatial":
            entry["category"] = "dimensional"
    with pytest.raises(QuerySetError, match="spatial"):
        load_query_set(_write(tmp_path, payload))


def test_a_duplicate_query_id_is_rejected(tmp_path: Path):
    payload = _valid_payload()
    payload["queries"][1]["id"] = payload["queries"][0]["id"]
    with pytest.raises(QuerySetError, match="duplicate query id"):
        load_query_set(_write(tmp_path, payload))


def test_an_unknown_category_is_rejected(tmp_path: Path):
    payload = _valid_payload()
    payload["queries"][0]["category"] = "vibes"
    with pytest.raises(QuerySetError, match="unknown category"):
        load_query_set(_write(tmp_path, payload))


def test_an_entry_with_no_acceptable_fragment_is_rejected(tmp_path: Path):
    """A query nobody can hit would score a free miss forever."""
    payload = _valid_payload()
    payload["queries"][0]["acceptable"] = []
    with pytest.raises(QuerySetError, match="'acceptable' is required"):
        load_query_set(_write(tmp_path, payload))


def test_an_anchor_with_both_locator_kinds_is_rejected(tmp_path: Path):
    payload = _valid_payload()
    payload["queries"][0]["acceptable"][0]["text_prefix"] = "also this"
    with pytest.raises(QuerySetError, match="exactly one of"):
        load_query_set(_write(tmp_path, payload))


def test_an_anchor_with_neither_locator_kind_is_rejected(tmp_path: Path):
    payload = _valid_payload()
    payload["queries"][0]["acceptable"][0].pop("citation_path")
    with pytest.raises(QuerySetError, match="exactly one of"):
        load_query_set(_write(tmp_path, payload))


# ----------------------------------------------------------------------
# The committed artifacts
# ----------------------------------------------------------------------


def test_the_committed_query_set_loads_and_spans_every_category():
    provenance, queries = load_query_set(QUERY_SET)
    assert provenance["tier"] == "agent-drafted, pending human spot-check"
    assert len(queries) >= MIN_QUERIES
    assert {q.category for q in queries} == set(REQUIRED_CATEGORIES)
    # Every entry carries a resolved snapshot, one id per anchor.
    for query in queries:
        assert len(query.fragment_ids) == len(query.anchors), query.id


def test_every_spatial_query_carries_a_literal_geometry():
    """No spatial entry may name an address.

    An address would send the harness through the geocoder — network, an API
    key, and a result that can change between runs — and the baseline would
    stop being a baseline.
    """
    _, queries = load_query_set(QUERY_SET)
    spatial = [q for q in queries if q.category == "spatial"]
    assert spatial
    for query in spatial:
        assert query.location is not None, query.id
        assert "geometry" in query.location, query.id
        assert query.location["geometry"]["type"] == "Point", query.id


def test_the_baseline_matches_the_committed_query_set():
    """The recorded baseline must describe the query set sitting next to it."""
    baseline = json.loads(BASELINE.read_text())
    _, queries = load_query_set(QUERY_SET)
    assert baseline["k"] == 10
    assert baseline["query_count"] == len(queries)
    assert [row["id"] for row in baseline["queries"]] == [q.id for q in queries]
    assert (
        baseline["query_set_provenance"]["tier"]
        == "agent-drafted, pending human spot-check"
    )
    assert baseline["corpus"]["retrieval_enabled_documents"]


def test_the_baseline_aggregates_agree_with_its_own_per_query_rows():
    """Guards against a hand-edited headline number.

    The aggregate is the number that gets quoted; the rows are the evidence.
    If someone ever adjusts one without the other, this fails.
    """
    baseline = json.loads(BASELINE.read_text())
    rows = baseline["queries"]
    recall = sum(1 for row in rows if row["hit"]) / len(rows)
    mrr = sum(row["reciprocal_rank"] for row in rows) / len(rows)
    assert baseline["recall_at_k"] == pytest.approx(recall, abs=1e-4)
    assert baseline["mrr"] == pytest.approx(mrr, abs=1e-4)
    for row in rows:
        if row["hit"]:
            assert row["first_hit_rank"] is not None
            assert row["ranked_fragment_ids"][row["first_hit_rank"] - 1] in (
                row["acceptable_fragment_ids"]
            )
        else:
            assert row["first_hit_rank"] is None
            assert row["reciprocal_rank"] == 0.0
