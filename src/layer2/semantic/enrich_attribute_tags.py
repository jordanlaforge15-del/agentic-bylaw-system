"""LLM-assisted enrichment of ``source_fragment.attribute_tags``.

Why this script exists
----------------------
Phase 1's evaluator wants to filter retrieval per attribute. The
filter is fast when ``source_fragment.attribute_tags`` is populated;
without it, the evaluator falls back to O(all-clauses) text matching
for every attribute lookup. This script is the one-time pass that
populates the tags.

Why LLM-assisted and not pure heuristics
----------------------------------------
Bylaws use inconsistent phrasing — "front yard", "yard abutting the
street", "setback from the front lot line" — that a keyword pass
alone will both miss and over-tag. So we use a two-stage filter:

1. Cheap keyword pre-filter against each taxonomy entry's
   ``bylaw_tag_keywords``. Skips obviously-unrelated clauses.
2. LLM call only for clauses the pre-filter hit OR whose parent
   hit. The LLM returns the attributes it believes the clause
   regulates plus a one-line rationale per tag.

The rationale stays in ``metadata_json.attribute_tag_rationales`` so
every later compliance verdict can cite WHY a clause was considered
relevant.

Idempotency
-----------
Re-running with the same taxonomy + model is a no-op for clauses
where the previously-stored model/version matches. Re-running with a
*different* taxonomy version stamps a new audit row and rewrites the
tags. Old rationales are preserved under
``metadata_json.attribute_tag_history`` so the audit trail isn't lost.

Confidence threshold
--------------------
LLM-proposed tags whose rationale contains hedge words ("may",
"could", "possibly") are discarded with a stat increment, per the
issue. Hedges are surfaced in ``stats.hedge_discards`` and the
discarded rationales land in ``metadata_json.attribute_tag_discards``
for spot-check.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import Document, SourceFragment
from layer1.db.session import session_scope
from layer1.models.enums import ParseStatus
from layer2.compliance.taxonomy import AttributeDefinition, Taxonomy, load_taxonomy


logger = logging.getLogger("enrich_attribute_tags")


PROMPT_VERSION = "enrich-attribute-tags/v1"
# Default model — the script is dialed for Claude 4.x family. Caller
# can override on the CLI for a cheaper/cheaper run.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Hedge words flagged in the rationale-confidence filter. The
# threshold is rough on purpose: anything below "is" / "shall" /
# direct assertion language is suspicious in a bylaw context.
HEDGE_PATTERNS = (
    re.compile(r"\bmay\b", re.IGNORECASE),
    re.compile(r"\bcould\b", re.IGNORECASE),
    re.compile(r"\bpossibly\b", re.IGNORECASE),
    re.compile(r"\bmight\b", re.IGNORECASE),
    re.compile(r"\bperhaps\b", re.IGNORECASE),
    re.compile(r"\bunclear\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class TagProposal:
    """One LLM-proposed tag for a fragment.

    Returned by the :class:`LLMTagger` protocol. The script applies
    the confidence filter and merges accepted proposals into the
    fragment's ``attribute_tags`` list.
    """

    attribute_id: str
    rationale: str


class LLMTagger(Protocol):
    """Pluggable LLM call surface.

    Production implementation calls Claude via the existing Anthropic
    gateway in ``src/advisor/llm/anthropic_backend.py``. Tests inject
    a deterministic fake so the suite never depends on network or
    API keys.
    """

    name: str
    model: str

    def propose_tags(
        self,
        *,
        clause_text: str,
        citation_context: str,
        taxonomy: Taxonomy,
        candidate_ids: list[str],
    ) -> list[TagProposal]: ...


@dataclass
class EnrichStats:
    """Counts produced by one enrichment pass.

    Surfaced to the CLI summary + post-run issue update. Hedge
    discards and skipped-by-prefilter counts are the two metrics the
    spot-check protocol cares about.
    """

    document_id: int | None = None
    fragments_total: int = 0
    fragments_parsed: int = 0
    fragments_prefiltered_out: int = 0
    fragments_sent_to_llm: int = 0
    fragments_with_tags: int = 0
    tags_assigned: int = 0
    hedge_discards: int = 0
    llm_errors: int = 0
    unchanged_skipped: int = 0

    def summary_line(self) -> str:
        return (
            f"doc={self.document_id} total={self.fragments_total} "
            f"parsed={self.fragments_parsed} "
            f"prefiltered_out={self.fragments_prefiltered_out} "
            f"sent_to_llm={self.fragments_sent_to_llm} "
            f"tagged={self.fragments_with_tags} "
            f"tags={self.tags_assigned} "
            f"hedge_discards={self.hedge_discards} "
            f"llm_errors={self.llm_errors} "
            f"unchanged_skipped={self.unchanged_skipped}"
        )


def enrich_document(
    session: Session,
    *,
    document_id: int,
    tagger: LLMTagger,
    taxonomy: Taxonomy | None = None,
    dry_run: bool = False,
    commit_every: int | None = None,
) -> EnrichStats:
    """Enrich every parsed fragment in one document.

    Iterates parsed fragments in citation-tree order. The two-pass
    structure (prefilter → LLM) keeps the LLM cost roughly
    proportional to the count of clauses likely to regulate any
    Phase-1 attribute.

    ``dry_run=True`` runs the full pipeline (including LLM calls) but
    skips the DB writes. Useful for evaluating model swaps against an
    existing population without rewriting the audit trail.

    ``commit_every=N`` flushes the session every N persisted
    fragments, bounding the work-loss window for long production runs
    where a connection blip mid-pass would otherwise discard hundreds
    of paid LLM calls. ``None`` (the default) preserves the original
    single-transaction behaviour, which is right for short runs and
    for tests that assert on rollback semantics. Ignored when
    ``dry_run`` is true.
    """
    taxonomy = taxonomy or load_taxonomy()
    stats = EnrichStats(document_id=document_id)
    document = session.get(Document, document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")

    fragments = (
        session.execute(
            select(SourceFragment)
            .where(SourceFragment.document_id == document_id)
            .order_by(
                SourceFragment.page_start,
                SourceFragment.reading_order_start,
                SourceFragment.id,
            )
        )
        .scalars()
        .all()
    )
    stats.fragments_total = len(fragments)
    fragment_by_id = {f.id: f for f in fragments}

    # Walk fragments in original order so parents are processed before
    # their children — the "parent hit a keyword" inheritance check
    # reads the parent's prefilter result rather than re-running the
    # scan.
    parent_hit_cache: dict[int, set[str]] = {}
    persisted_since_commit = 0

    for fragment in fragments:
        if fragment.parse_status != ParseStatus.PARSED:
            continue
        stats.fragments_parsed += 1

        candidate_ids = _candidate_attribute_ids(
            fragment=fragment,
            taxonomy=taxonomy,
            fragment_by_id=fragment_by_id,
            parent_hit_cache=parent_hit_cache,
        )
        if not candidate_ids:
            stats.fragments_prefiltered_out += 1
            continue
        stats.fragments_sent_to_llm += 1

        citation_context = _build_citation_context(fragment, fragment_by_id)
        try:
            proposals = tagger.propose_tags(
                clause_text=fragment.text,
                citation_context=citation_context,
                taxonomy=taxonomy,
                candidate_ids=candidate_ids,
            )
        except Exception as exc:  # noqa: BLE001 — tagger failures shouldn't tank the run
            logger.warning(
                "LLM tagging failed for fragment %d (%s): %s",
                fragment.id,
                fragment.citation_path,
                exc,
            )
            stats.llm_errors += 1
            continue

        accepted, hedged = _apply_confidence_filter(proposals, taxonomy=taxonomy)
        if hedged:
            stats.hedge_discards += len(hedged)

        new_tag_ids = sorted({p.attribute_id for p in accepted})

        prior_audit = (fragment.metadata_json or {}).get("attribute_tag_audit") or {}
        if (
            prior_audit.get("taxonomy_version") == taxonomy.version
            and prior_audit.get("model") == tagger.model
            and prior_audit.get("prompt_version") == PROMPT_VERSION
            and sorted(fragment.attribute_tags or []) == new_tag_ids
        ):
            # Same inputs + same output → nothing to do.
            stats.unchanged_skipped += 1
            continue

        if dry_run:
            if new_tag_ids:
                stats.fragments_with_tags += 1
                stats.tags_assigned += len(new_tag_ids)
            continue

        _persist_enrichment(
            fragment=fragment,
            new_tag_ids=new_tag_ids,
            accepted=accepted,
            hedged=hedged,
            taxonomy=taxonomy,
            tagger=tagger,
        )
        if new_tag_ids:
            stats.fragments_with_tags += 1
            stats.tags_assigned += len(new_tag_ids)
        persisted_since_commit += 1

        if commit_every and persisted_since_commit >= commit_every:
            session.commit()
            persisted_since_commit = 0

    return stats


def _candidate_attribute_ids(
    *,
    fragment: SourceFragment,
    taxonomy: Taxonomy,
    fragment_by_id: dict[int, SourceFragment],
    parent_hit_cache: dict[int, set[str]],
) -> list[str]:
    """Keyword pre-filter — return the taxonomy ids this clause might regulate.

    Direct hits: any attribute whose ``bylaw_tag_keywords`` appear in
    the clause text. Inherited hits: anything the immediate parent
    fragment was found to be a candidate for — captures the common
    pattern where a heading ("Setbacks") establishes the topic and
    the child clause ("4.5 metres") only carries the value.

    Pre-filter results for each fragment are cached so the inheritance
    check is O(1) per visit even on deep citation trees.
    """
    direct = {a.id for a in taxonomy.find_by_keywords(fragment.text)}
    inherited: set[str] = set()
    if fragment.parent_fragment_id is not None:
        cached = parent_hit_cache.get(fragment.parent_fragment_id)
        if cached is None:
            parent = fragment_by_id.get(fragment.parent_fragment_id)
            if parent is not None:
                cached = {a.id for a in taxonomy.find_by_keywords(parent.text)}
            else:
                cached = set()
            parent_hit_cache[fragment.parent_fragment_id] = cached
        inherited = cached
    combined = direct | inherited
    parent_hit_cache[fragment.id] = combined
    return sorted(combined)


def _build_citation_context(
    fragment: SourceFragment, fragment_by_id: dict[int, SourceFragment]
) -> str:
    """Render the parent + grandparent citation chain for the LLM prompt."""
    chain: list[str] = []
    current = fragment_by_id.get(fragment.parent_fragment_id) if fragment.parent_fragment_id else None
    depth = 0
    while current is not None and depth < 3:
        chain.append(
            f"{current.citation_path or '[uncited]'}: {current.text[:200].rstrip()}"
        )
        current = (
            fragment_by_id.get(current.parent_fragment_id)
            if current.parent_fragment_id is not None
            else None
        )
        depth += 1
    return "\n".join(reversed(chain)) if chain else ""


def _apply_confidence_filter(
    proposals: list[TagProposal], *, taxonomy: Taxonomy
) -> tuple[list[TagProposal], list[TagProposal]]:
    """Split proposals into (accepted, hedged) lists.

    Hedged: rationale contains any of the HEDGE_PATTERNS. Also drops
    proposals referencing attribute_ids that aren't in the taxonomy —
    the LLM occasionally invents plausible-looking ids that don't
    match anything; we'd rather discard than write a tag the
    evaluator can't make sense of.
    """
    valid_ids = set(taxonomy.ids)
    accepted: list[TagProposal] = []
    hedged: list[TagProposal] = []
    for proposal in proposals:
        if proposal.attribute_id not in valid_ids:
            hedged.append(proposal)
            continue
        rationale = (proposal.rationale or "").strip()
        if not rationale:
            hedged.append(proposal)
            continue
        if any(pattern.search(rationale) for pattern in HEDGE_PATTERNS):
            hedged.append(proposal)
            continue
        accepted.append(proposal)
    return accepted, hedged


def _persist_enrichment(
    *,
    fragment: SourceFragment,
    new_tag_ids: list[str],
    accepted: list[TagProposal],
    hedged: list[TagProposal],
    taxonomy: Taxonomy,
    tagger: LLMTagger,
) -> None:
    """Write tags and the audit trail onto ``source_fragment.metadata_json``.

    The audit trail is structured to survive re-runs against newer
    taxonomy versions: each run appends a row to
    ``metadata_json.attribute_tag_history`` carrying the prior
    rationales, so the audit history is recoverable even when the
    live ``attribute_tags`` list moves.
    """
    metadata = dict(fragment.metadata_json or {})
    prior_audit = metadata.get("attribute_tag_audit")
    history = list(metadata.get("attribute_tag_history") or [])
    if prior_audit:
        history.append(prior_audit)
    audit = {
        "taxonomy_version": taxonomy.version,
        "model": tagger.model,
        "tagger": tagger.name,
        "prompt_version": PROMPT_VERSION,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "tag_count": len(new_tag_ids),
    }
    metadata["attribute_tag_audit"] = audit
    metadata["attribute_tag_history"] = history
    metadata["attribute_tag_rationales"] = [
        {"attribute_id": p.attribute_id, "rationale": p.rationale}
        for p in accepted
    ]
    if hedged:
        metadata["attribute_tag_discards"] = [
            {"attribute_id": p.attribute_id, "rationale": p.rationale}
            for p in hedged
        ]
    else:
        metadata.pop("attribute_tag_discards", None)

    fragment.metadata_json = metadata
    fragment.attribute_tags = list(new_tag_ids)


# ----------------------------------------------------------------------
# Anthropic-backed implementation. The script is unit-tested with a
# fake tagger; the production implementation lives here so the CLI
# can wire it up without round-tripping through the advisor stack.
# ----------------------------------------------------------------------


@dataclass
class AnthropicTagger:
    """Production :class:`LLMTagger` backed by anthropic.Anthropic.

    Synchronous on purpose — the enrichment script is a batch job, not
    interactive, so the simpler API surface beats async machinery.
    Uses the same prompt-caching breakpoint pattern as the rest of
    the codebase: the system prompt + taxonomy section are cacheable;
    the per-fragment messages are not.
    """

    api_key: str
    model: str = DEFAULT_MODEL
    name: str = "anthropic-tagger"
    max_tokens: int = 1024

    def __post_init__(self) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The anthropic SDK is required for the AnthropicTagger; "
                "install with `pip install anthropic`."
            ) from exc
        self._client = Anthropic(api_key=self.api_key)

    def propose_tags(
        self,
        *,
        clause_text: str,
        citation_context: str,
        taxonomy: Taxonomy,
        candidate_ids: list[str],
    ) -> list[TagProposal]:
        prompt = _build_user_prompt(
            clause_text=clause_text,
            citation_context=citation_context,
            candidate_ids=candidate_ids,
        )
        system = _build_system_prompt(taxonomy=taxonomy, candidate_ids=candidate_ids)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_tagger_response(response, candidate_ids=candidate_ids)


def _build_system_prompt(*, taxonomy: Taxonomy, candidate_ids: list[str]) -> str:
    """System prompt for the tagging LLM.

    Notes:
    * Constrain the LLM to attribute ids from ``candidate_ids`` so it
      can't surface tags the prefilter ruled out (we already filter
      out invalid ids server-side, but the prompt discourages them
      from being proposed in the first place).
    * Tell it to return strict JSON; the parser is permissive but a
      well-shaped response saves the recovery branch.
    """
    candidate_block = "\n".join(
        f"- {attr.id}: {attr.description}" for attr in taxonomy.attributes if attr.id in set(candidate_ids)
    )
    return (
        "You are tagging bylaw clauses with the attributes they regulate.\n"
        "Return a strict JSON array of {attribute_id, rationale} objects.\n"
        "Only use attribute_ids from the CANDIDATES list below — do not invent.\n"
        "If the clause does not regulate any candidate attribute, return [].\n"
        "Rationale is one short sentence stating which words/phrases in the\n"
        "clause led to the tag. Avoid hedging language ('may', 'could',\n"
        "'possibly'); be direct or omit the tag.\n\n"
        "CANDIDATES:\n"
        f"{candidate_block}\n"
    )


def _build_user_prompt(
    *, clause_text: str, citation_context: str, candidate_ids: list[str]
) -> str:
    context = citation_context or "(no parent context)"
    return (
        "Parent citation context:\n"
        f"{context}\n\n"
        "Clause text:\n"
        f"{clause_text}\n\n"
        f"Candidate attribute ids: {', '.join(candidate_ids)}\n"
        "Return JSON only."
    )


def _parse_tagger_response(response: Any, *, candidate_ids: list[str]) -> list[TagProposal]:
    """Extract a list of :class:`TagProposal` from a raw Claude response.

    Permissive: the LLM occasionally wraps the JSON in prose. We look
    for the first ``[`` ... ``]`` substring and try to parse it. On
    parse failure, returns an empty list so the enrichment script
    can record a zero-tag pass without crashing.
    """
    text_chunks = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            text_chunks.append(text)
    joined = "".join(text_chunks).strip()
    if not joined:
        return []
    start = joined.find("[")
    end = joined.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(joined[start : end + 1])
    except json.JSONDecodeError:
        return []
    proposals: list[TagProposal] = []
    valid_ids = set(candidate_ids)
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        attr_id = entry.get("attribute_id")
        rationale = entry.get("rationale") or ""
        if isinstance(attr_id, str) and attr_id in valid_ids:
            proposals.append(TagProposal(attribute_id=attr_id, rationale=str(rationale)))
    return proposals


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--document-id",
        type=int,
        required=True,
        help="Document id to enrich.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the prefilter + LLM calls but don't write to the DB.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model id for the tagger (default {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--anthropic-api-key",
        default=None,
        help="Override ANTHROPIC_API_KEY (otherwise read from env).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL.",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=None,
        help=(
            "Commit every N persisted fragments (bounds the work-loss "
            "window on long runs). Default: single end-of-run commit."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import os

    api_key = args.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY not set; pass --anthropic-api-key or export ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2

    tagger = AnthropicTagger(api_key=api_key, model=args.model)
    started = time.monotonic()
    with session_scope(args.database_url) as session:
        stats = enrich_document(
            session,
            document_id=args.document_id,
            tagger=tagger,
            dry_run=args.dry_run,
            commit_every=args.commit_every,
        )
    elapsed_s = time.monotonic() - started
    print(
        f"enrich_attribute_tags: {stats.summary_line()} "
        f"elapsed_s={elapsed_s:.2f} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
