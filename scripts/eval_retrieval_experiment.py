"""Retrieval scoring/fusion experiment matrix (ABS-494).

``scripts/eval_retrieval_recall.py`` measures **one** retriever — whatever
``RetrievalService`` currently does. This script measures **several** on the
same labelled set in one run, so a scoring or fusion change can be argued from
a table instead of an anecdote.

    python scripts/eval_retrieval_experiment.py --database-url ... --dry-run
    python scripts/eval_retrieval_experiment.py --database-url ... --arms current,rrf_hybrid

Every arm is the **production** ``search()`` with one seam swapped:

* ``_merge_channel_scores`` — how per-channel scores become one ranking, and
  (for the FTS family) what channels there are to merge in the first place.

Nothing else is re-implemented. An arm that reproduced the pipeline instead of
overriding that seam would be measuring a program we do not ship, and the whole
point of the matrix is that the winning arm's numbers survive the move into
``RetrievalService`` unchanged. The ``current`` arm overrides nothing.

The families the ticket puts on trial
-------------------------------------
``text`` — the shipped ladder (``_score_fragment``): +12 per query token found
in ``citation_path``, +8 in ``citation_label``, +4 in the body, plus flat
bonuses for a verbatim path hit, and since ABS-492 the scope a fragment's
containers supply. Hand-tuned constants, no IDF, no length normalisation,
summed over tokens.

``table`` — ABS-500's direct ranking of ``source_table_cell``, keyed by the
anchor fragment its cells are cited through. Untouched by every arm: it is
scored before fusion and merely arrives as one more list to fuse.

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

Fusion is either the shipped ``max()`` + ``+10`` spatial-agreement bonus, or
Reciprocal Rank Fusion (``sum 1/(k + rank)``). RRF is scale-free: it reads only
the order of each channel, so it cannot be broken by the fact that a spatial hit
scores 100.0 and a good text hit scores 37.0 for reasons nobody can restate.

A note on what this matrix is measuring, which changed under it
--------------------------------------------------------------
The first run of this script (commit 702cb4c) graded its arms against a control
that scored Recall@10 = 0.1618, and several arms beat it by +0.34. That control
no longer exists: ABS-492 added provision-in-context scoring and ABS-500 added
the table channel, and the shipped retriever now scores 0.5588 on the same set
without any of the changes this issue proposed. Those results are therefore not
comparable to these and are not carried forward — the arms are re-derived here
against the retriever we actually ship.

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

    ``text_weights`` names the sub-channels the **text side** contributes and
    how they combine *within* it; ``fusion`` is how the text side then meets
    the other production channels. Splitting it that way is not cosmetic: the
    shipped agreement bonus is defined over ``(spatial, everything else)``, so
    an arm that changed the text side alone must still hand ``search()`` a
    single text score dict, or it would be changing two things at once and the
    matrix could not attribute the delta.

    Re-derived for the post-ABS-492/ABS-500 retriever. There are now **four**
    production channels, not two: ``text`` (the path ladder plus ABS-492's
    provision-in-context scoring), ``fts``, ``spatial``, and ABS-500's
    ``table``. The first matrix predates the last two and its control scored
    Recall@10 = 0.1618; the control here scores 0.5588, so every arm below is
    being asked a genuinely harder question than its same-named predecessor.
    """

    name: str
    summary: str
    #: Weight per text sub-channel ("text", "fts"). Zero/absent = channel off.
    text_weights: dict[str, float] = field(default_factory=lambda: {"text": 1.0})
    #: How the text sub-channels combine: "weighted" or "rrf".
    text_fusion: str = "weighted"
    #: How the channels meet: "production" (shipped max()+bonus) or "rrf".
    fusion: str = "production"
    #: Weight per channel under RRF fusion. Absent = 1.0.
    channel_weights: dict[str, float] = field(default_factory=dict)
    #: Postgres ranking function and normalisation flag for the FTS channel.
    fts_rank_fn: str = "ts_rank_cd"
    fts_normalisation: int = 1
    #: Truncate each channel to its top-N before RRF. None = fuse the full list.
    rrf_depth: int | None = None

    @property
    def uses_fts(self) -> bool:
        return self.text_weights.get("fts", 0.0) > 0

    @property
    def is_control(self) -> bool:
        """True when the arm is production, untouched, byte for byte.

        The control must run through ``super()`` rather than through a
        faithful-looking reimplementation. A control that merely *resembles*
        production would make every delta in ``RESULTS.md`` a comparison
        against a program we do not ship.
        """
        return (
            not self.uses_fts
            and self.fusion == "production"
            and self.text_weights.get("text", 0.0) == 1.0
        )


# The matrix. Ordered so each row differs from an earlier one by one decision:
# control, then fusion alone, then the text channel alone, then both.
ARMS: tuple[Arm, ...] = (
    Arm(
        name="current",
        summary="Production as shipped, untouched. The control every delta is quoted against.",
    ),
    Arm(
        name="rrf_fusion_only",
        summary="Channels unchanged; RRF replaces max()+bonus across text/spatial/table.",
        fusion="rrf",
    ),
    Arm(
        name="fts_only",
        summary="Diagnostic: FTS body/label as the whole text side, shipped fusion. No ladder.",
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
        summary="Text ladder + FTS, max-normalised 50/50 within the text side, shipped fusion.",
        text_weights={"text": 0.5, "fts": 0.5},
    ),
    Arm(
        name="fts_hybrid_25",
        summary="Text ladder + FTS, max-normalised 25/75 toward FTS, shipped fusion.",
        text_weights={"text": 0.25, "fts": 0.75},
    ),
    Arm(
        name="fts_hybrid_rrf_text",
        summary="Text ladder + FTS fused by RRF within the text side, shipped fusion.",
        text_weights={"text": 1.0, "fts": 1.0},
        text_fusion="rrf",
    ),
    # --- weight sweep -------------------------------------------------
    # `fts_hybrid_50` posts the best Recall@10 in this matrix and
    # `fts_hybrid_25` posts a *worse-than-control* one. Two neighbouring
    # settings of the same knob cannot straddle the control by 0.12 unless
    # either the effect is real and steep, or 0.5 happens to fit these 68
    # unreviewed labels. Those two have opposite consequences for shipping, and
    # nothing else in the matrix distinguishes them, so the knob gets swept.
    #
    # A plateau across neighbouring weights is an effect. A spike at exactly
    # the round number someone would have guessed first is a constant fitted to
    # the eval set — which is the defect this issue exists to remove, not a fix
    # for it.
    Arm(
        name="fts_hybrid_35",
        summary="Sweep: text ladder + FTS, 35/65 toward FTS.",
        text_weights={"text": 0.35, "fts": 0.65},
    ),
    Arm(
        name="fts_hybrid_40",
        summary="Sweep: text ladder + FTS, 40/60 toward FTS.",
        text_weights={"text": 0.40, "fts": 0.60},
    ),
    Arm(
        name="fts_hybrid_60",
        summary="Sweep: text ladder + FTS, 60/40 toward the ladder.",
        text_weights={"text": 0.60, "fts": 0.40},
    ),
    Arm(
        name="fts_hybrid_70",
        summary="Sweep: text ladder + FTS, 70/30 toward the ladder.",
        text_weights={"text": 0.70, "fts": 0.30},
    ),
    Arm(
        name="fts_hybrid_85",
        summary="Sweep: text ladder + FTS, 85/15 toward the ladder.",
        text_weights={"text": 0.85, "fts": 0.15},
    ),
    Arm(
        name="rrf_all_channels",
        summary="RRF over all four ranked lists: text, FTS, spatial, table.",
        text_weights={"text": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
    ),
    Arm(
        name="rrf_all_channels_ts_rank",
        summary="As rrf_all_channels but ts_rank (term frequency) for the FTS list.",
        text_weights={"text": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
        fts_rank_fn="ts_rank",
        fts_normalisation=0,
    ),
    Arm(
        name="rrf_all_channels_text_half",
        summary="Diagnostic: rrf_all_channels with the ladder list's vote halved.",
        text_weights={"text": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
        channel_weights={"text": 0.5},
    ),
    Arm(
        name="rrf_all_channels_top50",
        summary="RRF over text/FTS/spatial/table, each list truncated to its top 50 first.",
        text_weights={"text": 1.0, "fts": 1.0},
        text_fusion="rrf",
        fusion="rrf",
        rrf_depth=50,
    ),
    Arm(
        name="rrf_all_channels_top50_ts_rank",
        summary="As rrf_all_channels_top50 but ts_rank (term frequency) for the FTS list.",
        text_weights={"text": 1.0, "fts": 1.0},
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

# Imported from the service rather than restated here. ABS-494 shipped the FTS
# channel, so these are now production's own definitions: a copy in this file
# would let the arm that gets measured and the channel that gets served drift
# apart silently, which is precisely the failure the matrix exists to prevent.
from bylaw_retrieval.retrieval.service import (  # noqa: E402
    FTS_VECTOR_SQL,
    fts_or_query,
)


# ----------------------------------------------------------------------
# The experiment service
# ----------------------------------------------------------------------


def build_experiment_service(session, arm: Arm, resolver):
    """Return a ``RetrievalService`` whose scoring seams follow ``arm``."""
    from bylaw_retrieval.retrieval import RetrievalService

    class _ExperimentService(RetrievalService):
        """Production ``search()``; ``arm`` decides only the scoring seams.

        Production's seams are ``_text_channel_scores`` (the path ladder plus
        ABS-492's provision-in-context scoring), ``_table_channel_scores``
        (ABS-500), ``_spatial_channel_scores``, and ``_merge_channel_scores``.
        Arms override the last one and add an FTS channel beside it; the
        ``current`` arm overrides nothing at all and runs straight through
        ``super()``.

        The text channel is computed on **every** arm, including the ones that
        give it zero weight. ``search()`` reads ``text_scored.discriminating``
        to scope the table channel, so an arm that switched the text side off
        at the source would silently be changing the table channel too, and the
        matrix would attribute that delta to FTS.
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._experiment_request = None

        def _text_channel_scores(self, request):
            self._experiment_request = request
            return super()._text_channel_scores(request)

        def _blend_fts_into_text(self, text_scored, fts_scored):
            """Hand non-control arms the bare ladder.

            Since ABS-494 shipped the hybrid, production's own ``search()``
            blends FTS into the text channel before fusion. Every arm below
            then builds its *own* text side from that same FTS ranking, so
            without this the channel would be blended twice — once at
            production's weight and again at the arm's — and the matrix would
            be grading a configuration nothing could ship.

            The control is exempt on purpose: it must remain production
            untouched, which now includes the blend. A re-run after this ship
            should therefore show ``current`` and ``fts_hybrid_50`` agreeing
            exactly, and that agreement is a useful check rather than a
            redundancy.
            """
            if arm.is_control:
                return super()._blend_fts_into_text(text_scored, fts_scored)
            return text_scored

        def _fts_channel_scores(self, request) -> dict[int, float]:
            """Rank the in-scope corpus by FTS, with the arm's ranker knobs.

            Scoping comes from ``_fragment_scope_statement`` — the same
            statement the text channel is scored over — so an arm cannot post a
            better recall by searching a wider corpus than the control.
            """
            # The control is production, and production has had its own FTS
            # channel since ABS-494 shipped. Short-circuiting it here would
            # freeze the control at the pre-ship retriever and quietly recreate
            # the exact staleness this matrix was re-derived to escape: a
            # candidate would then be quoted against a program we no longer
            # serve. A re-run after the ship therefore shows `current` and
            # `fts_hybrid_50` agreeing, which is the correct steady state.
            if arm.is_control:
                return super()._fts_channel_scores(request)
            if not arm.uses_fts or self._dialect_name() != "postgresql":
                return {}

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
            rank_expr = sql_text(
                f"{arm.fts_rank_fn}({FTS_VECTOR_SQL}, to_tsquery('english', :tsq), "
                f"{arm.fts_normalisation})"
            ).bindparams(tsq=tsquery)
            matches_expr = sql_text(
                f"{FTS_VECTOR_SQL} @@ to_tsquery('english', :tsq)"
            ).bindparams(tsq=tsquery)
            stmt = (
                select(SourceFragment.id, rank_expr)
                .where(SourceFragment.id.in_(select(scope.subquery().c.id)))
                .where(matches_expr)
            )
            rows = self.session.execute(stmt).all()
            return {int(fragment_id): float(rank) for fragment_id, rank in rows if rank > 0}

        # -- fusion -------------------------------------------------------
        def _merge_channel_scores(self, text_scored, spatial_scored, table_scored=None):
            if arm.is_control:
                return super()._merge_channel_scores(
                    text_scored, spatial_scored, table_scored
                )

            fts_scored = self._fts_channel_scores(self._experiment_request)
            spatial_scored = dict(spatial_scored or {})
            table_scored = dict(table_scored or {})
            ladder_scored = (
                dict(text_scored) if arm.text_weights.get("text", 0.0) > 0 else {}
            )

            if arm.fusion == "rrf":
                channels = {
                    "text": ladder_scored,
                    "fts": fts_scored,
                    "spatial": spatial_scored,
                    "table": table_scored,
                }
                live = {name: scores for name, scores in channels.items() if scores}
                fused = rrf_fuse(
                    live, depth=arm.rrf_depth, weights=arm.channel_weights or None
                )
            else:
                # Production fusion, with the text side rebuilt from the arm's
                # sub-channels first. Collapsing text before the merge is what
                # lets the matrix attribute a delta: an arm that changed the
                # text side *and* the fusion at once would not say which moved.
                merged_text = self._combine_text_side(ladder_scored, fts_scored)
                live = {
                    name: scores
                    for name, scores in {
                        "text": merged_text,
                        "spatial": spatial_scored,
                        "table": table_scored,
                    }.items()
                    if scores
                }
                fused = {}
                for fragment_id in set().union(*live.values()) if live else set():
                    text_s = merged_text.get(fragment_id, 0.0)
                    spatial_s = spatial_scored.get(fragment_id, 0.0)
                    table_s = table_scored.get(fragment_id, 0.0)
                    score = max(text_s, spatial_s, table_s)
                    if spatial_s > 0 and (text_s > 0 or table_s > 0):
                        score += self._SPATIAL_TEXT_BOTH_BONUS
                    fused[fragment_id] = score
                # Label from the real sub-channels, not the collapsed one.
                live = {
                    name: scores
                    for name, scores in {
                        "text": ladder_scored,
                        "fts": fts_scored,
                        "spatial": spatial_scored,
                        "table": table_scored,
                    }.items()
                    if scores
                }

            merged = [
                (
                    score,
                    fragment_id,
                    sorted(
                        name for name, scores in live.items() if fragment_id in scores
                    ),
                )
                for fragment_id, score in fused.items()
            ]
            merged.sort(key=lambda entry: (-entry[0], entry[1]))
            return merged

        def _combine_text_side(
            self, ladder_scored: dict[int, float], fts_scored: dict[int, float]
        ) -> dict[int, float]:
            """Fold the text sub-channels into one production-scale dict.

            The rescale at the end is load-bearing and easy to miss. Production
            fusion is a ``max()`` across channels whose scores share the ladder
            scale — a spatial containment is 100.0, a table cell is scored by
            the same token ladder the text channel uses. A text side handed
            back on a normalised [0, 1] scale would therefore lose every
            ``max()`` it entered, and the arm would be measuring "table channel
            with the text side deleted" while claiming to measure a hybrid. So
            the combined ranking is mapped back onto the ladder's own top score
            before it is returned: the arm changes the text side's *order*,
            which is the hypothesis, and nothing about its magnitude.
            """
            sub_channels = {
                name: scores
                for name, scores in {"text": ladder_scored, "fts": fts_scored}.items()
                if scores
            }
            if not sub_channels:
                return {}
            if len(sub_channels) == 1:
                only = next(iter(sub_channels.values()))
                # A lone FTS side still has to arrive on the ladder's scale.
                return only if only is ladder_scored else _rescale(only, ladder_scored)

            if arm.text_fusion == "rrf":
                combined = rrf_fuse(
                    sub_channels, depth=arm.rrf_depth, weights=arm.text_weights
                )
            else:
                combined = normalised_weighted_sum(sub_channels, arm.text_weights)
            return _rescale(combined, ladder_scored)

    return _ExperimentService(session, default_document_id_resolver=resolver)


def _rescale(scores: dict[int, float], reference: dict[int, float]) -> dict[int, float]:
    """Map ``scores`` onto ``reference``'s top value, preserving order.

    Used to hand a re-ordered text side back to production fusion on the scale
    that fusion's ``max()`` was calibrated against. When the reference is empty
    there is no ladder score to borrow, so the ladder's own top-of-scale
    constant stands in — otherwise an arm whose ladder found nothing would
    contribute a text side pinned at zero.
    """
    if not scores:
        return {}
    top = max(scores.values())
    if top <= 0:
        return {}
    target = max(reference.values()) if reference else 100.0
    return {fragment_id: (score / top) * target for fragment_id, score in scores.items()}


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
# The zone-profile regression gate
# ----------------------------------------------------------------------

# The zones the labelled query set names. Drawn from an artifact already in
# the repo rather than invented here, so the gate cannot be accused of having
# been chosen to make a candidate look good. get_zone_profile is the thick
# tool that consumes search() output, so it is the surface a ranking change
# can silently break: a re-ranking that surfaces a *different* fragment first
# changes which value gets extracted, and Recall@10 would not notice.
ZONE_GATE_CODES = (
    "ER-3", "ER-2", "ER-1", "CH-1", "CH-2", "LI", "INS", "UC-2", "PCF",
    "WA", "CDD-2", "HR-2", "DND", "COR", "DD", "DH", "CLI", "HRI", "RPK",
)

#: (section, field) pairs a profile can populate. Flattened so an arm's
#: profile is one comparable set of populated keys.
_ZONE_FIELDS = (
    ("dimensions", "max_height_m"),
    ("dimensions", "max_lot_coverage_pct"),
    ("dimensions", "front_setback_m"),
    ("dimensions", "side_setback_m"),
    ("dimensions", "rear_setback_m"),
    ("dimensions", "max_far"),
    ("parking", "applies"),
    ("parking", "min_spaces_per_dwelling_unit"),
    ("parking", "schedule_reference"),
)


def zone_profile_summary(service, zone: str) -> dict[str, Any]:
    """Reduce one ``get_zone_profile`` call to what a regression would move."""
    profile = service.get_zone_profile(zone)
    populated = sorted(
        f"{section}.{field}"
        for section, field in _ZONE_FIELDS
        if getattr(getattr(profile, section, None), field, None) is not None
    )
    uses = profile.uses
    return {
        "zone": zone,
        "unknown_zone": profile.unknown_zone,
        "zone_full_name": profile.zone_full_name,
        "chapter": profile.chapter,
        "populated_fields": populated,
        "permitted_use_count": len(uses.permitted) if uses else 0,
        "citation_count": len(profile.citations),
        "confidence": {key: round(value, 3) for key, value in sorted(profile.confidence.items())},
    }


def run_zone_gate(session, arm: Arm, zones: Sequence[str]) -> list[dict[str, Any]]:
    from bylaw_retrieval.retrieval.service import retrieval_enabled_resolver

    service = build_experiment_service(session, arm, retrieval_enabled_resolver)
    return [zone_profile_summary(service, zone) for zone in zones]


def diff_zone_gate(
    control: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Field-level movement per zone: what the candidate lost, kept and gained.

    "Lost" is the only column that can block a ship. A field the control
    populated and the candidate does not means the agent stopped being able to
    answer a question it could answer yesterday, whatever the aggregate says.
    """
    by_zone = {row["zone"]: row for row in candidate}
    rows: list[dict[str, Any]] = []
    for before in control:
        after = by_zone[before["zone"]]
        lost = sorted(set(before["populated_fields"]) - set(after["populated_fields"]))
        gained = sorted(set(after["populated_fields"]) - set(before["populated_fields"]))
        rows.append(
            {
                "zone": before["zone"],
                "lost": lost,
                "gained": gained,
                "became_unknown": after["unknown_zone"] and not before["unknown_zone"],
                "permitted_use_delta": after["permitted_use_count"] - before["permitted_use_count"],
            }
        )
    return rows


def render_zone_gate_markdown(
    control_arm: str, gates: dict[str, list[dict[str, Any]]]
) -> str:
    lines = ["# ABS-494 — zone-profile regression gate", ""]
    lines.append(
        "`get_zone_profile` composes five `search()` calls and extracts a value "
        "from whichever fragment ranks first, so a ranking change moves it "
        "without moving Recall@10. This table is the ship gate the issue names: "
        "a candidate with a non-empty **lost** column is a regression."
    )
    lines.append("")
    lines.append(f"Zones: {', '.join(ZONE_GATE_CODES)} (every zone the labelled query set names).")
    lines.append("")

    control = gates[control_arm]

    # State the gate's power before quoting its verdict. On a corpus where the
    # control populates no dimensional fields at all, "0 field(s) lost" is not
    # a pass — there was nothing available to lose, and a reader who takes it
    # for a pass has been misled by this script rather than by the candidate.
    # The other two signals (unknown-zone flips, permitted-use counts) do have
    # power on such a corpus, so the gate is weakened here, not empty.
    control_populated = sum(len(row["populated_fields"]) for row in control)
    if control_populated == 0:
        lines.append(
            "> **Gate power: field comparison is VACUOUS on this corpus.** The "
            "control populates **zero** dimensional/parking fields across all "
            f"{len(control)} zones, so no candidate can lose one and "
            "`0 field(s) lost` below carries no information. What still has "
            "power: `unknown_zone` flips and permitted-use counts, both "
            "reported per zone. Read the verdict accordingly — and treat a "
            "corpus that populates fields as a prerequisite for using this as "
            "the ship gate the issue names."
        )
        lines.append("")

    for name, rows in gates.items():
        if name == control_arm:
            continue
        diff = diff_zone_gate(control, rows)
        total_lost = sum(len(row["lost"]) for row in diff)
        total_gained = sum(len(row["gained"]) for row in diff)
        unknown = [row["zone"] for row in diff if row["became_unknown"]]
        lines.append(f"## `{name}` vs `{control_arm}`")
        lines.append("")
        use_delta = sum(row["permitted_use_delta"] for row in diff)
        moved_zones = [row["zone"] for row in diff if row["permitted_use_delta"]]
        lines.append(
            f"**{total_lost} field(s) lost, {total_gained} gained** across "
            f"{len(diff)} zones"
            + (" (vacuous — see above)" if control_populated == 0 else "")
            + f". Zones newly unknown: {', '.join(unknown) or 'none'}. "
            f"Permitted-use count Δ: {use_delta:+d}"
            + (f" (moved: {', '.join(moved_zones)})" if moved_zones else " (no zone moved)")
            + "."
        )
        lines.append("")
        lines.append("| Zone | lost | gained | permitted-use count Δ |")
        lines.append("|---|---|---|---|")
        for row in diff:
            lines.append(
                f"| {row['zone']} | {', '.join(row['lost']) or '—'} | "
                f"{', '.join(row['gained']) or '—'} | {row['permitted_use_delta']:+d} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


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
        "production `RetrievalService.search()` with `_merge_channel_scores` "
        "swapped (and, for the FTS family, one extra channel to merge) — see "
        "that script's docstring for what each family is. The `current` arm "
        "overrides nothing and runs straight through production."
    )
    lines.append("")
    lines.append(
        "**The `current` arm always tracks production.** It is not a frozen "
        "historical control: it runs the shipped `search()` with nothing "
        "overridden, so every delta here is quoted against the retriever we "
        "actually serve. This matters because this matrix has already been "
        "wrong once the other way — its first run (commit `702cb4c`) graded a "
        "control scoring 0.1618 that ABS-492 and ABS-500 had already moved to "
        "0.5588, and recommended arms dev had overtaken. Those numbers are not "
        "comparable to these and are not carried forward."
    )
    lines.append("")
    lines.append(
        "ABS-494 shipped `fts_hybrid_50` "
        "(`docs/decisions/ABS-494-SCORING-FUSION-DECISION.md`). A run made "
        "*after* that ship should therefore show `current` and `fts_hybrid_50` "
        "agreeing exactly — that agreement is the check that the arm which was "
        "measured and the channel that is served are the same program, not a "
        "redundant row."
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
    parser.add_argument(
        "--zone-gate",
        action="store_true",
        help=(
            "Instead of Recall@k, run the zone-profile regression gate: "
            "get_zone_profile for every zone the query set names, under each "
            "arm, diffed field-by-field against the control."
        ),
    )
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

    if args.zone_gate:
        if CONTROL_ARM not in {arm.name for arm in selected}:
            print(
                f"FAIL: the zone gate is a diff, so --arms must include "
                f"{CONTROL_ARM!r}",
                file=sys.stderr,
            )
            return 2
        gates: dict[str, list[dict[str, Any]]] = {}
        with session_scope(args.database_url) as session:
            for arm in selected:
                print(f"zone gate: {arm.name} ...", flush=True)
                gates[arm.name] = run_zone_gate(session, arm, ZONE_GATE_CODES)
        markdown = render_zone_gate_markdown(CONTROL_ARM, gates)
        print()
        print(markdown)
        if not args.dry_run:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            (args.out_dir / "ZONE_GATE.md").write_text(markdown)
            (args.out_dir / "zone_gate.json").write_text(
                json.dumps(gates, indent=2, ensure_ascii=False) + "\n"
            )
            print(f"Wrote {args.out_dir / 'ZONE_GATE.md'}")
        return 0

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

        # Drop arm files that this matrix no longer defines. Renaming an arm
        # (rrf_all_channels_path_half -> ..._text_half) used to leave the old
        # file behind holding numbers from a superseded run, with no row in
        # RESULTS.md pointing at it — a measurement that looks committed and
        # current while describing a retriever that no longer exists. That is
        # the same failure ABS-502 was written against, one directory down.
        #
        # Only prunes on a FULL run: `--arms a,b` deliberately writes a subset
        # and must not delete the arms it was not asked to measure.
        pruned: list[str] = []
        if len(selected) == len(ARMS):
            keep = {f"{arm.name}.json" for arm in ARMS}
            for stale in sorted(arms_dir.glob("*.json")):
                if stale.name not in keep:
                    stale.unlink()
                    pruned.append(stale.stem)

        print(f"Wrote {args.out_dir / 'RESULTS.md'} and {len(selected)} arm files")
        if pruned:
            print(f"Pruned {len(pruned)} arm file(s) no longer in the matrix: {', '.join(pruned)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
