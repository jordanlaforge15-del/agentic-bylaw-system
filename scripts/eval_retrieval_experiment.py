"""Retrieval scoring/fusion experiment matrix (ABS-494).

``scripts/eval_retrieval_recall.py`` measures **one** retriever — whatever
``RetrievalService`` currently does. This script measures **several** on the
same labelled set in one run, so a scoring or fusion change can be argued from
a table instead of an anecdote.

    python scripts/eval_retrieval_experiment.py --database-url ... --dry-run
    python scripts/eval_retrieval_experiment.py --database-url ... --arms current,rrf_hybrid

Every arm is the **production** ``search()`` with one or two seams swapped:

* ``_text_channel_scores`` — what the text channel scores a fragment on.
* ``_merge_channel_scores`` — how per-channel scores become one ranking.

Nothing else is re-implemented. An arm that reproduced the pipeline instead of
overriding its two seams would be measuring a program we do not ship, and the
whole point of the matrix is that the winning arm's numbers survive the move
into ``RetrievalService`` unchanged.

The three families the ticket puts on trial
-------------------------------------------
``path`` — the shipped ladder (``_score_fragment``): +12 per query token found
in ``citation_path``, +8 in ``citation_label``, +4 in the body, plus flat
bonuses for a verbatim path hit. Hand-tuned constants, no IDF, no length
normalisation, summed over tokens.

``fts`` — Postgres full-text search over
``to_tsvector('english', citation_label || ' ' || text)``. That expression is
*exactly* the one indexed by ``ix_source_fragment_text_tsv`` (0002:243), so the
predicate is index-eligible; it is also the reason FTS cannot see
``citation_path``, which is why this is a **hybrid** and not a replacement.
The query is an OR of the query's terms (``to_tsquery('english', 'a | b')``),
which is what makes the english dictionary drop stop words for us — the single
biggest defect in the ladder is that ``a``/``in``/``for`` each earn +12 when
they land inside a heading-decorated path.

``spatial`` — untouched. Overlay intersection, already at Recall@10 = 1.00.

Fusion is either the shipped ``max()`` + ``+10`` both-channels bonus, or
Reciprocal Rank Fusion (``sum 1/(k + rank)``). RRF is scale-free: it reads only
the order of each channel, so it cannot be broken by the fact that a spatial hit
scores 100.0 and a good text hit scores 37.0 for reasons nobody can restate.

Reading the output
------------------
``RESULTS.md`` is the committed artifact: one row per arm, plus a per-category
breakdown, plus the per-query deltas against the control arm. ``arms/*.json``
carries the same data machine-readably, including each arm's top-k ranking, so
a later change can be diffed against it rather than re-argued.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from eval_retrieval_recall import (  # noqa: E402
    DEFAULT_K,
    DEFAULT_QUERY_SET,
    LabelledQuery,
    QuerySetError,
    Report,
    _check_snapshot,
    _corpus_fingerprint,
    _db_anchor_resolver,
    evaluate,
    load_query_set,
)

DEFAULT_OUT_DIR = REPO_ROOT / "evals" / "retrieval" / "experiments"

# ----------------------------------------------------------------------
# Pure fusion primitives
# ----------------------------------------------------------------------

# RRF's only knob. 60 is the value from Cormack, Clarke & Buettcher (2009),
# reported there as robust across TREC collections and adopted unchanged by
# every mainstream implementation since. It is a *smoothing* constant: it sets
# how quickly the contribution decays with rank, so a large k flattens the
# channels toward equal votes and a small k lets rank 1 dominate. We do not
# tune it here — a constant fitted on 68 unreviewed labels would be exactly the
# hand-tuned-magic-number problem this issue exists to remove.
RRF_K = 60


def rank_channel(scores: dict[int, float]) -> dict[int, int]:
    """Rank a channel's fragments, best first, 1-based.

    Ties break on ``fragment_id`` — the same ``(-score, fragment_id)`` rule
    ``_merge_channel_scores`` already uses, so a re-run is byte-identical. At
    the current scoring the top of most rankings is a block of tied scores, so
    this is load-bearing rather than pedantic.
    """
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return {fragment_id: rank for rank, (fragment_id, _) in enumerate(ordered, start=1)}


def rrf_fuse(
    channels: dict[str, dict[int, float]],
    *,
    rrf_k: int = RRF_K,
    depth: int | None = None,
    weights: dict[str, float] | None = None,
) -> dict[int, float]:
    """Reciprocal Rank Fusion: ``score(f) = sum over channels of 1/(k + rank)``.

    A fragment absent from a channel contributes nothing from it — not a zero
    score, which would be a different (and worse) rule, because a fragment that
    two channels rank 40th would then beat one that a single channel ranks 1st.

    ``depth`` truncates each channel to its top-N before fusing, which is how
    RRF is posed in the literature: it fuses *result lists*, and a result list
    is what a retriever returns, not its entire scored corpus. It matters here
    more than usual. The path ladder scores thousands of fragments and its
    ranks past the first page are noise — mostly ties broken on primary key —
    so an untruncated path list casts a full-strength vote for junk that no
    caller would ever have been shown.

    ``weights`` scales a channel's vote. Unweighted RRF gives every channel an
    equal say, which is its virtue when the channels are comparably reliable
    and its weakness when one is not. The weights exist here to *test* that —
    an arm that has to be weighted to win is telling you the unweighted
    channels are not the ones you should be fusing.
    """
    fused: dict[int, float] = {}
    for name, scores in channels.items():
        weight = 1.0 if weights is None else weights.get(name, 1.0)
        if weight <= 0:
            continue
        for fragment_id, rank in rank_channel(scores).items():
            if depth is not None and rank > depth:
                continue
            fused[fragment_id] = fused.get(fragment_id, 0.0) + weight / (rrf_k + rank)
    return fused


def normalised_weighted_sum(
    channels: dict[str, dict[int, float]], weights: dict[str, float]
) -> dict[int, float]:
    """Per-query max-normalise each channel to [0, 1], then weight and add.

    Max-normalisation rather than min-max: the channels have a meaningful zero
    (nothing matched) and no meaningful floor among the fragments that *did*
    match, so subtracting the minimum would inflate the worst surviving
    candidate of a weak channel into a 0.0 instead of leaving it near it.
    """
    combined: dict[int, float] = {}
    for name, scores in channels.items():
        weight = weights.get(name, 0.0)
        if not weight or not scores:
            continue
        top = max(scores.values())
        if top <= 0:
            continue
        for fragment_id, score in scores.items():
            combined[fragment_id] = combined.get(fragment_id, 0.0) + weight * (score / top)
    return combined


# ----------------------------------------------------------------------
# Arms
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """One retriever configuration on trial.

    ``text_channels`` names the sub-channels the text side contributes and how
    they are combined *within* the text side; ``fusion`` is how the text side
    then meets the spatial side. Splitting it that way is not cosmetic: the
    shipped both-channels bonus is defined over ``(text, spatial)``, so an arm
    that changed the text side alone must still be able to hand ``search()`` a
    single text score dict, or it would be changing two things at once and the
    matrix would not attribute the delta.
    """

    name: str
    summary: str
    #: Weight per text sub-channel ("path", "fts"). Empty weight = channel off.
    text_weights: dict[str, float] = field(default_factory=lambda: {"path": 1.0})
    #: How the text sub-channels combine: "weighted" or "rrf".
    text_fusion: str = "weighted"
    #: How text meets spatial: "max_bonus" (shipped) or "rrf".
    fusion: str = "max_bonus"
    #: Postgres ranking function and normalisation flag for the FTS channel.
    fts_rank_fn: str = "ts_rank_cd"
    fts_normalisation: int = 1
    #: Truncate each channel to its top-N before RRF. None = fuse the full list.
    rrf_depth: int | None = None

    @property
    def uses_fts(self) -> bool:
        return self.text_weights.get("fts", 0.0) > 0


# The matrix. Ordered so each row differs from an earlier one by one decision:
# control, then fusion alone, then the text channel alone, then both.
ARMS: tuple[Arm, ...] = (
    Arm(
        name="current",
        summary="Shipped: path ladder, max() + both-channels bonus. The control.",
    ),
    Arm(
        name="rrf_fusion_only",
        summary="Path ladder unchanged; RRF replaces max()+bonus across text/spatial.",
        fusion="rrf",
    ),
    Arm(
        name="fts_only",
        summary="Diagnostic: FTS body/label channel alone, shipped fusion. No path scoring.",
        text_weights={"fts": 1.0},
    ),
    Arm(
        name="fts_only_ts_rank",
        summary="Diagnostic: as fts_only but ts_rank (term frequency) instead of ts_rank_cd.",
        text_weights={"fts": 1.0},
        fts_rank_fn="ts_rank",
        fts_normalisation=0,
    ),
    Arm(
        name="fts_hybrid_50",
        summary="Path ladder + FTS, max-normalised 50/50, shipped fusion.",
        text_weights={"path": 0.5, "fts": 0.5},
    ),
    Arm(
        name="fts_hybrid_25",
        summary="Path ladder + FTS, max-normalised 25/75 toward FTS, shipped fusion.",
        text_weights={"path": 0.25, "fts": 0.75},
    ),
    Arm(
        name="fts_hybrid_rrf_text",
        summary="Path ladder + FTS fused by RRF within the text channel, shipped fusion.",
        text_weights={"path": 1.0, "fts": 1.0},
        text_fusion="rrf",
    ),
    Arm(
        name="rrf_all_channels",
        summary="RRF over all three ranked lists: path, FTS, spatial.",
        text_weights={"path": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
    ),
    Arm(
        name="rrf_all_channels_ts_rank",
        summary="As rrf_all_channels but ts_rank (term frequency) for the FTS list.",
        text_weights={"path": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
        fts_rank_fn="ts_rank",
        fts_normalisation=0,
    ),
    Arm(
        name="rrf_all_channels_path_half",
        summary="Diagnostic: rrf_all_channels with the path list's vote halved.",
        text_weights={"path": 0.5, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
    ),
    Arm(
        name="rrf_all_channels_top50",
        summary="RRF over path/FTS/spatial, each list truncated to its top 50 first.",
        text_weights={"path": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
        rrf_depth=50,
    ),
    Arm(
        name="rrf_all_channels_top50_ts_rank",
        summary="As rrf_all_channels_top50 but ts_rank (term frequency) for the FTS list.",
        text_weights={"path": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
        rrf_depth=50,
        fts_rank_fn="ts_rank",
        fts_normalisation=0,
    ),
)

ARMS_BY_NAME = {arm.name: arm for arm in ARMS}
CONTROL_ARM = "current"


# ----------------------------------------------------------------------
# The FTS channel
# ----------------------------------------------------------------------

# The indexed expression, verbatim from 0002_layer2_retrieval_schema.py:243.
# Any drift between this string and the index turns an index scan into a
# 7,100-row sequential re-tsvectorisation that still returns the right answer,
# which is the kind of regression a correctness test cannot see.
FTS_VECTOR_SQL = (
    "to_tsvector('english', coalesce(citation_label, '') || ' ' || coalesce(text, ''))"
)

_FTS_TERM_RE = re.compile(r"[A-Za-z0-9]+")


def fts_or_query(query: str) -> str:
    """Build an OR tsquery string from a natural-language question.

    ``"side setback in the ER-3 zone"`` -> ``"side | setback | in | the | er | 3 | zone"``.

    Two deliberate choices:

    * **OR, not AND.** ``websearch_to_tsquery`` conjoins terms, and a nine-term
      conjunction over a by-law clause matches nothing — every dimensional
      question would return an empty channel. Retrieval wants a graded ranking
      over partial matches, which is what disjunction plus a rank function is.
    * **The stop words are left in the string.** ``to_tsquery`` runs them
      through the english dictionary and drops them itself
      (``to_tsquery('english', 'the | cat')`` is ``'cat'``), so the tokenizer
      here does not need a stop list of its own to maintain. That dropping is
      the single largest correction to the ladder, where each stop word earns
      +12 whenever it lands inside a heading-decorated citation path.

    Hyphenated compounds are split rather than quoted: ``er-3`` would parse as
    a phrase (``'er-3' & 'er' & '3'``) and smuggle a conjunction into a
    disjunction.
    """
    terms = [term.lower() for term in _FTS_TERM_RE.findall(query)]
    return " | ".join(dict.fromkeys(terms))


# ----------------------------------------------------------------------
# The experiment service
# ----------------------------------------------------------------------


def build_experiment_service(session, arm: Arm, resolver):
    """Return a ``RetrievalService`` whose two scoring seams follow ``arm``."""
    from bylaw_retrieval.retrieval import RetrievalService

    class _ExperimentService(RetrievalService):
        """Production ``search()``; ``arm`` decides the two scoring seams."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._subchannels: dict[str, dict[int, float]] = {}

        # -- text side ---------------------------------------------------
        def _text_channel_scores(self, request) -> dict[int, float]:
            channels: dict[str, dict[int, float]] = {}
            if arm.text_weights.get("path", 0.0) > 0:
                channels["path"] = super()._text_channel_scores(request)
            if arm.uses_fts:
                channels["fts"] = self._fts_channel_scores(request)
            self._subchannels = channels
            if len(channels) == 1:
                return next(iter(channels.values()))
            if arm.text_fusion == "rrf":
                return rrf_fuse(channels, depth=arm.rrf_depth, weights=arm.text_weights)
            return normalised_weighted_sum(channels, arm.text_weights)

        def _fts_channel_scores(self, request) -> dict[int, float]:
            """Rank the in-scope corpus by Postgres FTS over label + body.

            Reuses ``_fragment_scope_statement`` for scoping rather than
            rebuilding the WHERE clause, so the FTS channel sees exactly the
            documents, pages and attribute tags the ladder sees. An arm that
            searched a wider corpus than the control would post a better recall
            for a reason that has nothing to do with scoring.
            """
            from sqlalchemy import select, text as sql_text

            from layer1.db.base import SourceFragment

            tsquery = fts_or_query(request.query or "")
            if not tsquery:
                return {}

            scope = (
                self._fragment_scope_statement(request)
                .with_only_columns(SourceFragment.id)
                .order_by(None)
            )
            scoped_ids = select(scope.subquery().c.id)

            rank_expr = sql_text(
                f"{arm.fts_rank_fn}({FTS_VECTOR_SQL}, to_tsquery('english', :tsq), "
                f"{arm.fts_normalisation})"
            ).bindparams(tsq=tsquery)
            matches_expr = sql_text(
                f"{FTS_VECTOR_SQL} @@ to_tsquery('english', :tsq)"
            ).bindparams(tsq=tsquery)
            stmt = (
                select(SourceFragment.id, rank_expr)
                .where(SourceFragment.id.in_(scoped_ids))
                .where(matches_expr)
            )
            rows = self.session.execute(stmt).all()
            return {int(fragment_id): float(rank) for fragment_id, rank in rows if rank > 0}

        # -- channel fusion ----------------------------------------------
        def _merge_channel_scores(self, text_scored, spatial_scored):
            if arm.fusion != "rrf":
                return super()._merge_channel_scores(text_scored, spatial_scored)

            channels = {name: scores for name, scores in self._subchannels.items() if scores}
            if spatial_scored:
                channels["spatial"] = spatial_scored
            fused = rrf_fuse(channels, depth=arm.rrf_depth, weights=arm.text_weights)

            merged: list[tuple[float, int, list[str]]] = []
            for fragment_id, score in fused.items():
                labels: list[str] = []
                if fragment_id in text_scored:
                    labels.append("text")
                if fragment_id in spatial_scored:
                    labels.append("spatial")
                merged.append((score, fragment_id, labels))
            merged.sort(key=lambda entry: (-entry[0], entry[1]))
            return merged

    return _ExperimentService(session, default_document_id_resolver=resolver)


# ----------------------------------------------------------------------
# Running the matrix
# ----------------------------------------------------------------------


def _search_fn(service, k: int):
    from bylaw_retrieval.retrieval.schemas import LocationSlot, RetrievalRequest

    def search(query: LabelledQuery) -> list[int]:
        request = RetrievalRequest(
            query=query.question,
            limit=k,
            location=LocationSlot(**query.location) if query.location else None,
        )
        return [match.fragment_id for match in service.search(request).matches]

    return search


def run_arm(session, arm: Arm, queries, anchor_resolver, k: int) -> Report:
    from bylaw_retrieval.retrieval.service import retrieval_enabled_resolver

    service = build_experiment_service(session, arm, retrieval_enabled_resolver)
    return evaluate(queries, _search_fn(service, k), anchor_resolver, k=k)


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------


def _fmt_delta(value: float, control: float) -> str:
    delta = value - control
    if abs(delta) < 5e-5:
        return f"{value:.4f} (=)"
    return f"{value:.4f} ({delta:+.4f})"


def render_results_markdown(
    reports: dict[str, Report],
    *,
    corpus: dict[str, Any],
    k: int,
    provenance: dict[str, Any],
) -> str:
    """Render the committed results table.

    Per-category is not an optional extra here: the aggregate hides the one
    outcome that would block a ship, which is an arm that buys ``definition``
    recall by losing ``spatial`` or ``citation_lookup``.
    """
    control = reports.get(CONTROL_ARM)
    lines: list[str] = []
    lines.append(f"# ABS-494 — scoring & fusion experiment matrix (k={k})")
    lines.append("")
    lines.append(
        "Generated by `scripts/eval_retrieval_experiment.py`. Every arm is the "
        "production `RetrievalService.search()` with `_text_channel_scores` "
        "and/or `_merge_channel_scores` swapped — see that script's docstring "
        "for what each family is."
    )
    lines.append("")
    lines.append(
        f"**Label tier:** {provenance.get('tier')} · `review_status: "
        f"{provenance.get('review_status')}`. These numbers grade **ranking, "
        "never correctness**, and are never averaged with a golden pass rate "
        "(`evals/retrieval/README.md`)."
    )
    lines.append("")
    documents = corpus.get("retrieval_enabled_documents", [])
    lines.append("**Corpus:** " + "; ".join(
        f"{d['bylaw_name']} (id {d['document_id']}, {d['fragment_count']} fragments)"
        for d in documents
    ))
    lines.append("")

    lines.append("## Headline")
    lines.append("")
    lines.append(f"| Arm | Recall@{k} | SetRecall@{k} | MRR@{k} | What changed |")
    lines.append("|---|---|---|---|---|")
    for name, report in reports.items():
        if control is None or name == CONTROL_ARM:
            recall = f"{report.recall_at_k:.4f}"
            set_recall = f"{report.set_recall_at_k:.4f}"
            mrr = f"{report.mrr:.4f}"
        else:
            recall = _fmt_delta(report.recall_at_k, control.recall_at_k)
            set_recall = _fmt_delta(report.set_recall_at_k, control.set_recall_at_k)
            mrr = _fmt_delta(report.mrr, control.mrr)
        lines.append(
            f"| `{name}` | {recall} | {set_recall} | {mrr} | "
            f"{ARMS_BY_NAME[name].summary} |"
        )
    lines.append("")

    categories = sorted({c for r in reports.values() for c in r.by_category})
    lines.append(f"## Recall@{k} by query class")
    lines.append("")
    lines.append("| Arm | " + " | ".join(categories) + " |")
    lines.append("|---" * (len(categories) + 1) + "|")
    for name, report in reports.items():
        cells = []
        for category in categories:
            stats = report.by_category.get(category)
            if stats is None:
                cells.append("—")
                continue
            hits = round(float(stats["recall_at_k"]) * int(stats["query_count"]))
            cells.append(f"{float(stats['recall_at_k']):.2f} ({hits}/{stats['query_count']})")
        lines.append(f"| `{name}` | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append(f"## MRR@{k} by query class")
    lines.append("")
    lines.append("| Arm | " + " | ".join(categories) + " |")
    lines.append("|---" * (len(categories) + 1) + "|")
    for name, report in reports.items():
        cells = [
            f"{float(report.by_category[category]['mrr']):.3f}"
            if category in report.by_category
            else "—"
            for category in categories
        ]
        lines.append(f"| `{name}` | " + " | ".join(cells) + " |")
    lines.append("")

    if control is not None:
        lines.append("## Per-query movement against `current`")
        lines.append("")
        lines.append(
            "`+` the arm hit a question the control missed; `-` the arm lost a "
            "question the control hit. A ship candidate with a non-empty `-` "
            "column is a trade, not an improvement, and has to be argued as one."
        )
        lines.append("")
        control_hits = {r.id: r.hit for r in control.results}
        lines.append("| Arm | gained | lost |")
        lines.append("|---|---|---|")
        for name, report in reports.items():
            if name == CONTROL_ARM:
                continue
            gained = [r.id for r in report.results if r.hit and not control_hits.get(r.id)]
            lost = [r.id for r in report.results if not r.hit and control_hits.get(r.id)]
            lines.append(
                f"| `{name}` | {', '.join(gained) or '—'} | {', '.join(lost) or '—'} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def report_payload(arm: Arm, report: Report, *, corpus: dict[str, Any]) -> dict[str, Any]:
    return {
        "arm": arm.name,
        "summary": arm.summary,
        "config": {
            "text_weights": arm.text_weights,
            "text_fusion": arm.text_fusion,
            "fusion": arm.fusion,
            "fts_rank_fn": arm.fts_rank_fn if arm.uses_fts else None,
            "fts_normalisation": arm.fts_normalisation if arm.uses_fts else None,
            "rrf_k": RRF_K if "rrf" in (arm.fusion, arm.text_fusion) else None,
            "rrf_depth": arm.rrf_depth,
        },
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
                "acceptable_fragment_ids": r.acceptable_fragment_ids,
                "ranked_fragment_ids": r.ranked_fragment_ids,
                "hit": r.hit,
                "first_hit_rank": r.first_hit_rank,
            }
            for r in report.results
        ],
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_SET)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument(
        "--arms",
        default=",".join(arm.name for arm in ARMS),
        help="Comma-separated arm names; defaults to the whole matrix.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print only; write nothing.")
    args = parser.parse_args(argv)

    from layer1.db.session import session_scope

    try:
        provenance, queries = load_query_set(args.queries)
    except QuerySetError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    selected = []
    for name in args.arms.split(","):
        name = name.strip()
        if not name:
            continue
        if name not in ARMS_BY_NAME:
            print(f"FAIL: unknown arm {name!r}", file=sys.stderr)
            return 2
        selected.append(ARMS_BY_NAME[name])

    reports: dict[str, Report] = {}
    with session_scope(args.database_url) as session:
        anchor_resolver = _db_anchor_resolver(session)
        try:
            drift = _check_snapshot(queries, anchor_resolver)
        except QuerySetError as error:
            print(f"FAIL: {error}", file=sys.stderr)
            return 2
        if drift:
            print(
                "FAIL: fragment_ids snapshot is stale — the corpus moved under "
                "the labels. Re-run eval_retrieval_recall.py --refresh-fragment-ids "
                "and review the diff before trusting any score:",
                file=sys.stderr,
            )
            for line in drift:
                print(f"  {line}", file=sys.stderr)
            return 2

        corpus = _corpus_fingerprint(session)
        for arm in selected:
            print(f"running arm {arm.name} ...", flush=True)
            reports[arm.name] = run_arm(session, arm, queries, anchor_resolver, args.k)

    markdown = render_results_markdown(
        reports, corpus=corpus, k=args.k, provenance=provenance
    )
    print()
    print(markdown)

    if not args.dry_run:
        arms_dir = args.out_dir / "arms"
        arms_dir.mkdir(parents=True, exist_ok=True)
        for arm in selected:
            payload = report_payload(arm, reports[arm.name], corpus=corpus)
            (arms_dir / f"{arm.name}.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            )
        (args.out_dir / "RESULTS.md").write_text(markdown)
        print(f"Wrote {args.out_dir / 'RESULTS.md'} and {len(selected)} arm files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
