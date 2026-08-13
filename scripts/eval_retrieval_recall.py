"""Retrieval eval: Recall@k and MRR for ``search_bylaw_evidence`` (ABS-486).

Before this script, no Recall@k artifact existed anywhere in the repo, so every
scoring, fusion, chunking or citation-path change was unfalsifiable: the only
signal was whether an end-to-end answer still "looked right", which mixes the
retriever's behaviour with the model's. This measures the retriever alone.

    python scripts/eval_retrieval_recall.py            # writes evals/retrieval/BASELINE.json
    python scripts/eval_retrieval_recall.py --dry-run  # print, write nothing
    python scripts/eval_retrieval_recall.py --refresh-fragment-ids

What it measures
----------------
For each labelled question the harness issues exactly one
``RetrievalService.search`` — the same call ``search_bylaw_evidence`` makes,
through the same production document scope (``retrieval_enabled_resolver``) —
at ``--k`` (default 10) and reads the ranked ``fragment_id`` list off the
response. Three numbers come out of that ranking:

* ``recall_at_k`` — **hit rate**: the share of questions where *at least one*
  acceptable fragment is in the top k. The labels are a set of *acceptable*
  answers (a by-law standard is usually stated once but reachable through the
  section, its subsection, and a table row), so surfacing any one of them is a
  success. This is the headline number.
* ``set_recall_at_k`` — the mean of ``|acceptable ∩ topk| / |acceptable|``.
  Secondary, and deliberately reported next to the hit rate: it falls when the
  ranking finds one of three acceptable fragments, which is not a defect, so it
  is a sensitivity signal rather than a target.
* ``mrr`` — mean reciprocal rank of the *first* acceptable fragment within the
  top k, 0 when the question misses entirely. Answers "how far down the list
  does the agent have to read", which the hit rate cannot.

Determinism
-----------
The ranking is deterministic given a fixed corpus: ``_merge_channel_scores``
sorts by ``(-score, fragment_id)``, so ties break on the primary key rather
than on set-iteration order. The harness adds two rules of its own:

1. **No geocoder.** Spatial questions carry a literal ``location.geometry``
   point, which short-circuits ``_resolve_location_slot`` before it can reach
   the in-database civic dataset or the Google fallback. A labelled query set
   whose score moved with a network call would be worthless as a baseline.
2. **Labels are content-addressed, not id-addressed.** ``source_fragment.id``
   is a sequence value that a re-ingest reassigns wholesale, so every label is
   an anchor — a ``citation_path`` (unique per document by constraint) or an
   exact ``text_prefix`` — resolved against the database at run time. The
   ``fragment_ids`` in the query set are a *snapshot* of that resolution,
   verified on every run and rewritten only by ``--refresh-fragment-ids``. An
   anchor that resolves to zero or several fragments is a hard error: a label
   that has quietly stopped pointing at the clause it was written for would
   otherwise show up as a retrieval regression, which is the one failure this
   artifact must never fabricate.

The graded set is agent-drafted (see ``evals/retrieval/README.md``) and is not
the attested golden tier. It gates ranking work, not a deploy.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERY_SET = REPO_ROOT / "evals" / "retrieval" / "queries.json"
DEFAULT_BASELINE = REPO_ROOT / "evals" / "retrieval" / "BASELINE.json"
DEFAULT_K = 10

# Categories the query set is required to span. Named here rather than derived
# from the file so a set that silently loses a whole category — the failure
# mode a "≥50 queries" count check cannot see — fails the load.
REQUIRED_CATEGORIES = (
    "dimensional",
    "permitted_use",
    "definition",
    "zone_anchored",
    "spatial",
    "citation_lookup",
)

MIN_QUERIES = 50


class QuerySetError(RuntimeError):
    """The labelled query set is malformed or does not resolve."""


@dataclass(frozen=True)
class Anchor:
    """A content-addressed locator for one acceptable fragment.

    Exactly one of ``citation_path`` / ``text_prefix`` is set. ``bylaw`` is
    matched with a case-insensitive ``LIKE %…%`` against ``document.bylaw_name``
    so a label survives the "(Consolidated to …)" qualifiers a re-ingest can
    add to a title.
    """

    bylaw: str
    citation_path: str | None = None
    text_prefix: str | None = None

    def describe(self) -> str:
        if self.citation_path is not None:
            return f"{self.bylaw} :: citation_path={self.citation_path!r}"
        return f"{self.bylaw} :: text_prefix={self.text_prefix!r}"


@dataclass(frozen=True)
class LabelledQuery:
    id: str
    category: str
    question: str
    anchors: tuple[Anchor, ...]
    fragment_ids: tuple[int, ...]
    location: dict[str, Any] | None = None
    notes: str | None = None


@dataclass
class QueryResult:
    id: str
    category: str
    question: str
    acceptable_fragment_ids: list[int]
    ranked_fragment_ids: list[int]
    hit: bool
    first_hit_rank: int | None
    reciprocal_rank: float
    set_recall: float


@dataclass
class Report:
    k: int
    query_count: int
    recall_at_k: float
    set_recall_at_k: float
    mrr: float
    by_category: dict[str, dict[str, float | int]]
    results: list[QueryResult] = field(default_factory=list)


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


def load_query_set(path: Path) -> tuple[dict[str, Any], list[LabelledQuery]]:
    """Parse and validate the labelled query set.

    Validation is strict on purpose. Every check here corresponds to a way the
    file could be wrong that would silently *raise* the measured score — a
    duplicate id collapsing two questions into one, a query with no acceptable
    fragment scoring a free miss, an unknown category dodging the coverage
    requirement — and a baseline that flatters the retriever is worse than none.
    """
    raw = json.loads(path.read_text())
    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        raise QuerySetError(f"{path}: missing 'provenance' header")
    for required in ("tier", "authored_by", "review_status"):
        if not provenance.get(required):
            raise QuerySetError(f"{path}: provenance.{required} is required")

    entries = raw.get("queries")
    if not isinstance(entries, list):
        raise QuerySetError(f"{path}: 'queries' must be a list")
    if len(entries) < MIN_QUERIES:
        raise QuerySetError(
            f"{path}: {len(entries)} queries, at least {MIN_QUERIES} required"
        )

    queries: list[LabelledQuery] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        where = entry.get("id") or f"queries[{index}]"
        for required in ("id", "category", "question", "acceptable"):
            if not entry.get(required):
                raise QuerySetError(f"{where}: '{required}' is required")
        if entry["id"] in seen_ids:
            raise QuerySetError(f"{where}: duplicate query id")
        seen_ids.add(entry["id"])
        if entry["category"] not in REQUIRED_CATEGORIES:
            raise QuerySetError(
                f"{where}: unknown category {entry['category']!r}; "
                f"expected one of {list(REQUIRED_CATEGORIES)}"
            )
        anchors = tuple(_parse_anchor(where, item) for item in entry["acceptable"])
        queries.append(
            LabelledQuery(
                id=entry["id"],
                category=entry["category"],
                question=entry["question"],
                anchors=anchors,
                fragment_ids=tuple(entry.get("fragment_ids") or ()),
                location=entry.get("location"),
                notes=entry.get("notes"),
            )
        )

    missing = sorted(set(REQUIRED_CATEGORIES) - {q.category for q in queries})
    if missing:
        raise QuerySetError(f"{path}: no queries in category/categories {missing}")
    return provenance, queries


def _parse_anchor(where: str, item: Any) -> Anchor:
    if not isinstance(item, dict):
        raise QuerySetError(f"{where}: each 'acceptable' entry must be an object")
    bylaw = item.get("bylaw")
    if not bylaw:
        raise QuerySetError(f"{where}: acceptable entry missing 'bylaw'")
    citation_path = item.get("citation_path")
    text_prefix = item.get("text_prefix")
    if bool(citation_path) == bool(text_prefix):
        raise QuerySetError(
            f"{where}: acceptable entry needs exactly one of "
            "'citation_path' / 'text_prefix'"
        )
    return Anchor(bylaw=bylaw, citation_path=citation_path, text_prefix=text_prefix)


# ----------------------------------------------------------------------
# Scoring (pure)
# ----------------------------------------------------------------------

# An anchor resolver maps one Anchor to the fragment ids it addresses. The
# database implementation is _db_anchor_resolver; tests pass a dict-backed stub.
AnchorResolver = Callable[[Anchor], list[int]]

# A search function maps one labelled query to the ranked fragment ids the
# retriever returns for it, best first.
SearchFn = Callable[[LabelledQuery], list[int]]


def resolve_acceptable_ids(
    query: LabelledQuery, resolver: AnchorResolver
) -> list[int]:
    """Resolve every anchor on ``query`` to the fragment ids it addresses.

    Raises when an anchor addresses no fragment or more than one: an ambiguous
    anchor grades against a clause nobody chose, and a dangling one grades
    against nothing. Both would be read as a retrieval regression.
    """
    resolved: list[int] = []
    for anchor in query.anchors:
        ids = resolver(anchor)
        if not ids:
            raise QuerySetError(
                f"{query.id}: anchor resolved to no fragment — {anchor.describe()}"
            )
        if len(ids) > 1:
            raise QuerySetError(
                f"{query.id}: anchor is ambiguous ({len(ids)} fragments: "
                f"{sorted(ids)}) — {anchor.describe()}"
            )
        resolved.append(ids[0])
    # Dedupe while preserving label order; two anchors may legitimately name
    # the same fragment through different routes.
    return list(dict.fromkeys(resolved))


def score_ranking(
    ranked_ids: Sequence[int], acceptable_ids: Sequence[int], k: int
) -> tuple[bool, int | None, float, float]:
    """Score one ranking against one acceptable set.

    Returns ``(hit, first_hit_rank, reciprocal_rank, set_recall)``.
    ``first_hit_rank`` is 1-based and ``None`` on a miss.
    """
    acceptable = set(acceptable_ids)
    top = list(ranked_ids)[:k]
    first_hit_rank: int | None = None
    for position, fragment_id in enumerate(top, start=1):
        if fragment_id in acceptable:
            first_hit_rank = position
            break
    hit = first_hit_rank is not None
    reciprocal_rank = 1.0 / first_hit_rank if first_hit_rank is not None else 0.0
    set_recall = (
        len(acceptable & set(top)) / len(acceptable) if acceptable else 0.0
    )
    return hit, first_hit_rank, reciprocal_rank, set_recall


def evaluate(
    queries: Iterable[LabelledQuery],
    search: SearchFn,
    resolver: AnchorResolver,
    k: int = DEFAULT_K,
) -> Report:
    """Run every query through ``search`` and aggregate the three metrics."""
    results: list[QueryResult] = []
    for query in queries:
        acceptable = resolve_acceptable_ids(query, resolver)
        ranked = list(search(query))
        hit, rank, rr, set_recall = score_ranking(ranked, acceptable, k)
        results.append(
            QueryResult(
                id=query.id,
                category=query.category,
                question=query.question,
                acceptable_fragment_ids=acceptable,
                ranked_fragment_ids=ranked[:k],
                hit=hit,
                first_hit_rank=rank,
                reciprocal_rank=rr,
                set_recall=set_recall,
            )
        )

    count = len(results)
    by_category: dict[str, dict[str, float | int]] = {}
    for category in sorted({r.category for r in results}):
        subset = [r for r in results if r.category == category]
        by_category[category] = {
            "query_count": len(subset),
            "recall_at_k": _mean(1.0 if r.hit else 0.0 for r in subset),
            "set_recall_at_k": _mean(r.set_recall for r in subset),
            "mrr": _mean(r.reciprocal_rank for r in subset),
        }

    return Report(
        k=k,
        query_count=count,
        recall_at_k=_mean(1.0 if r.hit else 0.0 for r in results),
        set_recall_at_k=_mean(r.set_recall for r in results),
        mrr=_mean(r.reciprocal_rank for r in results),
        by_category=by_category,
        results=results,
    )


def _mean(values: Iterable[float]) -> float:
    collected = list(values)
    if not collected:
        return 0.0
    return round(sum(collected) / len(collected), 4)


def report_to_dict(
    report: Report, *, provenance: dict[str, Any], corpus: dict[str, Any]
) -> dict[str, Any]:
    """Project a :class:`Report` into the BASELINE.json shape.

    Per-query rows are included: an aggregate that moved is only actionable if
    you can see *which* questions moved, and the file is the diff a future
    ranking change is reviewed against.
    """
    return {
        "_comment": (
            "Generated by scripts/eval_retrieval_recall.py (ABS-486). "
            "Recall@k is the share of questions with at least one acceptable "
            "fragment in the top k; set_recall_at_k is the mean fraction of the "
            "acceptable set retrieved; mrr is the mean reciprocal rank of the "
            "first acceptable fragment. Labels are agent-drafted and pending "
            "human spot-check — see evals/retrieval/README.md."
        ),
        "query_set_provenance": provenance,
        "corpus": corpus,
        "k": report.k,
        "query_count": report.query_count,
        "recall_at_k": report.recall_at_k,
        "set_recall_at_k": report.set_recall_at_k,
        "mrr": report.mrr,
        "by_category": report.by_category,
        "queries": [
            {
                "id": r.id,
                "category": r.category,
                "question": r.question,
                "acceptable_fragment_ids": r.acceptable_fragment_ids,
                "ranked_fragment_ids": r.ranked_fragment_ids,
                "hit": r.hit,
                "first_hit_rank": r.first_hit_rank,
                "reciprocal_rank": round(r.reciprocal_rank, 4),
                "set_recall": round(r.set_recall, 4),
            }
            for r in report.results
        ],
    }


# ----------------------------------------------------------------------
# Database-backed adapters
# ----------------------------------------------------------------------


def _db_anchor_resolver(session) -> AnchorResolver:
    """Resolve anchors against a live corpus.

    Scoped to the documents an operator has published to retrieval, which is
    exactly the corpus ``search_bylaw_evidence`` searches — labelling against a
    fragment the retriever cannot reach would grade an impossible target.
    """
    from sqlalchemy import select

    from bylaw_retrieval.retrieval.service import retrieval_enabled_resolver
    from layer1.db.base import Document, SourceFragment

    scoped_ids = retrieval_enabled_resolver(session)

    def resolve(anchor: Anchor) -> list[int]:
        stmt = (
            select(SourceFragment.id)
            .join(Document, Document.id == SourceFragment.document_id)
            .where(SourceFragment.document_id.in_(scoped_ids))
            .where(Document.bylaw_name.ilike(f"%{anchor.bylaw}%"))
        )
        if anchor.citation_path is not None:
            stmt = stmt.where(SourceFragment.citation_path == anchor.citation_path)
        else:
            # startswith, not equality: the fragment text carries amendment
            # stamps and trailing clause text that would make a full-text label
            # unreadable and brittle. The prefix is required to be long enough
            # to be unique, and the ambiguity check above enforces that.
            stmt = stmt.where(SourceFragment.text.startswith(anchor.text_prefix))
        return sorted(session.execute(stmt).scalars().all())

    return resolve


def _db_search_fn(service, k: int) -> SearchFn:
    """Issue the production ``search_bylaw_evidence`` call for one query."""
    from bylaw_retrieval.retrieval.schemas import LocationSlot, RetrievalRequest

    def search(query: LabelledQuery) -> list[int]:
        request = RetrievalRequest(
            query=query.question,
            limit=k,
            location=LocationSlot(**query.location) if query.location else None,
        )
        return [match.fragment_id for match in service.search(request).matches]

    return search


def _corpus_fingerprint(session) -> dict[str, Any]:
    """Identify the corpus a baseline was measured against.

    A Recall@k number is meaningless without it: the same harness over a
    re-ingested corpus is a different measurement, not a regression.
    """
    from sqlalchemy import func, select

    from bylaw_retrieval.retrieval.service import retrieval_enabled_resolver
    from layer1.db.base import Document, SourceFragment

    scoped_ids = retrieval_enabled_resolver(session)
    documents = []
    for document_id in scoped_ids:
        document = session.get(Document, document_id)
        fragment_count = session.execute(
            select(func.count(SourceFragment.id)).where(
                SourceFragment.document_id == document_id
            )
        ).scalar_one()
        documents.append(
            {
                "document_id": document_id,
                "municipality": document.municipality if document else None,
                "bylaw_name": document.bylaw_name if document else None,
                "fragment_count": fragment_count,
            }
        )
    return {"retrieval_enabled_documents": documents}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _refresh_fragment_ids(
    path: Path, queries: list[LabelledQuery], resolver: AnchorResolver
) -> None:
    """Rewrite the ``fragment_ids`` snapshot in place after a re-ingest."""
    raw = json.loads(path.read_text())
    resolved_by_id = {q.id: resolve_acceptable_ids(q, resolver) for q in queries}
    for entry in raw["queries"]:
        entry["fragment_ids"] = resolved_by_id[entry["id"]]
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    print(f"Refreshed fragment_ids snapshot in {path}")


def _check_snapshot(queries: list[LabelledQuery], resolver: AnchorResolver) -> list[str]:
    """Compare each query's snapshot ids against a live resolution."""
    drift: list[str] = []
    for query in queries:
        resolved = resolve_acceptable_ids(query, resolver)
        if query.fragment_ids and list(query.fragment_ids) != resolved:
            drift.append(
                f"{query.id}: snapshot {list(query.fragment_ids)} != resolved {resolved}"
            )
    return drift


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_SET)
    parser.add_argument("--out", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the summary; do not write the baseline file.",
    )
    parser.add_argument(
        "--refresh-fragment-ids",
        action="store_true",
        help=(
            "Re-resolve every anchor and rewrite the fragment_ids snapshot in "
            "the query set. Run after a re-ingest; commit the diff."
        ),
    )
    args = parser.parse_args(argv)

    from bylaw_retrieval.retrieval import RetrievalService
    from bylaw_retrieval.retrieval.service import retrieval_enabled_resolver
    from layer1.db.session import session_scope

    try:
        provenance, queries = load_query_set(args.queries)
    except QuerySetError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    with session_scope(args.database_url) as session:
        resolver = _db_anchor_resolver(session)

        if args.refresh_fragment_ids:
            try:
                _refresh_fragment_ids(args.queries, queries, resolver)
            except QuerySetError as error:
                print(f"FAIL: {error}", file=sys.stderr)
                return 2
            return 0

        try:
            drift = _check_snapshot(queries, resolver)
        except QuerySetError as error:
            print(f"FAIL: {error}", file=sys.stderr)
            return 2
        if drift:
            print(
                "FAIL: fragment_ids snapshot is stale — the corpus moved under "
                "the labels. Re-run with --refresh-fragment-ids and review the "
                "diff before trusting any score:",
                file=sys.stderr,
            )
            for line in drift:
                print(f"  {line}", file=sys.stderr)
            return 2

        service = RetrievalService(
            session, default_document_id_resolver=retrieval_enabled_resolver
        )
        report = evaluate(queries, _db_search_fn(service, args.k), resolver, k=args.k)
        payload = report_to_dict(
            report, provenance=provenance, corpus=_corpus_fingerprint(session)
        )

    print(f"queries        : {report.query_count}")
    print(f"Recall@{args.k:<8}: {report.recall_at_k:.4f}")
    print(f"SetRecall@{args.k:<5}: {report.set_recall_at_k:.4f}")
    print(f"MRR@{args.k:<11}: {report.mrr:.4f}")
    print("")
    print(f"{'category':<16} {'n':>4} {'recall':>8} {'setrec':>8} {'mrr':>8}")
    for category, stats in report.by_category.items():
        print(
            f"{category:<16} {stats['query_count']:>4} "
            f"{stats['recall_at_k']:>8.4f} {stats['set_recall_at_k']:>8.4f} "
            f"{stats['mrr']:>8.4f}"
        )
    misses = [r for r in report.results if not r.hit]
    if misses:
        print("")
        print(f"misses ({len(misses)}):")
        for result in misses:
            print(f"  {result.id} [{result.category}] {result.question}")

    if not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print("")
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
