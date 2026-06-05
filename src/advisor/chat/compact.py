"""Compact projections of bylaw-retrieval responses for the LLM tool loop.

Every tool_result block we hand back to the LLM is replayed verbatim on
every subsequent turn — so a 20kB blob gets re-billed N times during a
multi-step tool conversation. The full Pydantic response models carry
fields the model never reads (internal database IDs, raw GeoJSON
descriptors, verbose dataset summaries, redundant request echoes) and
ship every search match regardless of how many the model can actually
use.

The functions here project the Pydantic models to a smaller dict that
keeps only what the LLM needs to produce a citation-grounded answer:

- Citations (citation_path, citation_label, page range, municipality
  + bylaw_name).
- Fragment text and ancestor citations (collapsed to citation paths
  plus a short text stem rather than full ancestor text).
- Cross-references reduced to the resolved citation path plus
  resolution status — the LLM can ``lookup_citation`` for detail.
- Tables reduced to caption + a short tabular preview plus the
  ``table_id`` handle.
- Linked datasets reduced to dataset_id + name + the canonical
  attribute values from the spatial match plus the geocoder confidence
  signal. The verbose ``summary_text``, ``feature_count``, ``crs``,
  ``publisher`` and internal ``feature_id``/``feature_key``/
  ``overlap_metric`` fields are dropped — none are required to answer
  the user.

Anything dropped that the LLM might still want is recoverable via a
follow-up tool call using the surviving handles (``fragment_id``,
``document_id``, ``dataset_id``, ``citation_path``).

Search responses are additionally truncated to ``max_matches`` results;
the count of dropped matches is surfaced as a one-line
``truncation_note`` so the model knows to narrow the query if it
needs more.

The external MCP server keeps the full response shape unchanged for
backward compatibility with non-chat MCP clients.

Byte stability matters here: tool_result content forms part of the
prompt-cache prefix, so we don't sort keys or otherwise reshape
content based on non-deterministic inputs.
"""
from __future__ import annotations

import os
from typing import Any

from bylaw_retrieval.retrieval.schemas import (
    AddressProfile,
    AncestorFragment,
    CitationLookupResponse,
    CrossReferenceSummary,
    DocumentOutlineResponse,
    DocumentSummary,
    LinkedDataset,
    RetrievalMatch,
    RetrievalResponse,
    TableSummary,
    ZoneProfile,
)


_ANCESTOR_TEXT_CHARS = 160
_TABLE_PREVIEW_CHARS = 500
_TABLE_PREVIEW_CELLS = 24


def _compact_ceiling() -> int:
    """Hard ceiling on matches returned in compact mode.

    ``ADVISOR_COMPACT_MAX_MATCHES`` lets ops lower this without a
    redeploy (e.g. to protect against runaway payloads on constrained
    infra). Default 50 matches the schema's max ``limit`` value so
    that a caller requesting ``limit=50`` gets all 50 results rather
    than being silently clipped. The ceiling is applied as
    ``min(request.limit, ceiling)`` in ``compact_search_response``.
    """
    raw = os.environ.get("ADVISOR_COMPACT_MAX_MATCHES", "50")
    try:
        value = int(raw)
    except ValueError:
        return 50
    return value if value > 0 else 50


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}..."


def compact_document_summary(doc: DocumentSummary) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": doc.id,
        "municipality": doc.municipality,
        "bylaw_name": doc.bylaw_name,
    }
    if doc.version_label:
        out["version_label"] = doc.version_label
    if doc.consolidation_date:
        out["consolidation_date"] = doc.consolidation_date
    if doc.page_count is not None:
        out["page_count"] = doc.page_count
    return out


def compact_ancestor(ancestor: AncestorFragment) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if ancestor.citation_path:
        out["citation_path"] = ancestor.citation_path
    if ancestor.citation_label:
        out["citation_label"] = ancestor.citation_label
    if ancestor.text:
        out["text_excerpt"] = _truncate(ancestor.text, _ANCESTOR_TEXT_CHARS)
    return out


def compact_cross_reference(ref: CrossReferenceSummary) -> dict[str, Any]:
    out: dict[str, Any] = {"resolution_status": ref.resolution_status}
    if ref.target_citation_path:
        out["target_citation_path"] = ref.target_citation_path
    elif ref.target_citation_guess:
        out["target_citation_guess"] = ref.target_citation_guess
    return out


def compact_table(table: TableSummary) -> dict[str, Any]:
    out: dict[str, Any] = {
        "table_id": table.id,
        "page_start": table.page_start,
        "page_end": table.page_end,
    }
    if table.caption:
        out["caption"] = table.caption
    if table.cells:
        rows: dict[int, list[tuple[int, str]]] = {}
        for cell in table.cells[:_TABLE_PREVIEW_CELLS]:
            rows.setdefault(cell.row_index, []).append((cell.col_index, cell.text))
        rendered_rows: list[str] = []
        for row_idx in sorted(rows):
            cells_sorted = sorted(rows[row_idx], key=lambda c: c[0])
            rendered_rows.append(" | ".join(text for _, text in cells_sorted))
        preview = "\n".join(rendered_rows)
        out["preview"] = _truncate(preview, _TABLE_PREVIEW_CHARS)
    return out


def compact_linked_dataset(ds: LinkedDataset) -> dict[str, Any]:
    """Drop the verbose dataset metadata; keep the values the LLM
    actually quotes when answering ("max height is X meters").
    """
    out: dict[str, Any] = {
        "dataset_id": ds.dataset_id,
        "name": ds.name,
    }
    if ds.location_resolver:
        out["location_resolver"] = ds.location_resolver
    if ds.location_confidence is not None:
        out["location_confidence"] = ds.location_confidence
    if ds.feature_matches:
        out["feature_matches"] = [
            {
                "canonical_attributes": dict(fm.canonical_attributes),
                "contains_input": fm.contains_input,
            }
            for fm in ds.feature_matches
        ]
    return out


def compact_match(match: RetrievalMatch) -> dict[str, Any]:
    out: dict[str, Any] = {
        "fragment_id": match.fragment_id,
        "document_id": match.document_id,
        "municipality": match.municipality,
        "bylaw_name": match.bylaw_name,
        "page_start": match.page_start,
        "page_end": match.page_end,
        "text": match.text,
        "score": match.score,
    }
    if match.citation_path:
        out["citation_path"] = match.citation_path
    if match.citation_label:
        out["citation_label"] = match.citation_label
    if match.retrieval_channels:
        out["retrieval_channels"] = list(match.retrieval_channels)
    if match.ancestor_chain:
        out["ancestor_chain"] = [
            compact_ancestor(a) for a in match.ancestor_chain
        ]
    if match.cross_references:
        out["cross_references"] = [
            compact_cross_reference(ref) for ref in match.cross_references
        ]
    if match.related_tables:
        out["tables"] = [compact_table(t) for t in match.related_tables]
    if match.linked_datasets:
        out["linked_datasets"] = [
            compact_linked_dataset(ds) for ds in match.linked_datasets
        ]
    return out


def compact_citation_lookup(response: CitationLookupResponse) -> dict[str, Any]:
    """Project ``CitationLookupResponse`` into a tool_result payload.

    Three shapes the model can dispatch on without re-parsing:

    * Exact hit: ``{"match": {...compact_match...}}``.
    * No exact hit + suggestions: ``{"match": null, "suggestions":
      [...], "instruction": "Re-issue lookup_citation with the closest
      candidate; do not guess further variants."}``.
    * No exact hit + nothing similar: ``{"match": null, "suggestions":
      [], "instruction": "Path not present in this document. Switch to
      search_bylaw_evidence or get_document_outline; do not retry
      lookup_citation."}``.

    The inline ``instruction`` field is what stops ABS-261's
    max_iterations thrash — the model would otherwise see ``match: null``
    and treat it as a transient failure to retry. The instruction
    converts the empty result into an explicit next action.
    """
    out: dict[str, Any] = {}
    if response.match is not None:
        out["match"] = compact_match(response.match)
        # Even on a hit, surface suggestions if the service decided to
        # ship them (currently it doesn't, but the schema allows it
        # and future tuning might use them for disambiguation hints).
        if response.suggestions:
            out["suggestions"] = list(response.suggestions)
        return out
    out["match"] = None
    out["suggestions"] = list(response.suggestions)
    if response.suggestions:
        out["instruction"] = (
            "No exact citation_path match. Re-issue lookup_citation with the "
            "closest candidate from suggestions; do NOT guess further variants."
        )
    else:
        out["instruction"] = (
            "Citation path not present in this document and no near matches "
            "found. Switch to search_bylaw_evidence or get_document_outline "
            "instead of retrying lookup_citation."
        )
    return out


def compact_search_response(
    response: RetrievalResponse,
    *,
    max_matches: int | None = None,
) -> dict[str, Any]:
    """Strip the full ``RetrievalResponse`` to its LLM-essential fields
    and cap match count.

    The original ``request`` echo is dropped — the LLM already knows
    what it sent, and re-shipping the entire request (with its echoed
    location slot and ``include_*`` toggles) on every tool turn was
    pure cache bloat.
    """
    cap = max_matches if max_matches is not None else min(response.request.limit, _compact_ceiling())
    matches = response.matches[:cap]
    out: dict[str, Any] = {
        "total_matches": response.total_matches,
        "shown_matches": len(matches),
        "matches": [compact_match(m) for m in matches],
    }
    if response.notes:
        out["notes"] = list(response.notes)
    dropped = len(response.matches) - len(matches)
    if dropped > 0:
        out["truncation_note"] = (
            f"{dropped} additional lower-scored match(es) not shown. "
            "Narrow the query with citation_path_prefix, page range, or "
            "a more specific location to surface them."
        )
    return out


def compact_zone_profile(profile: ZoneProfile) -> dict[str, Any]:
    """Project a ``ZoneProfile`` to its LLM-essential fields.

    Unlike search/citation payloads, the zone profile is already
    structured and small. The compact rule here is to PRESERVE the
    structured shape (FR-2 implementation note: "compact should preserve
    the structured shape, not flatten to a prose blob") while dropping
    null fields so the model isn't billed for keys carrying no
    information. The nested ``dimensions``/``uses``/``parking`` objects
    keep their field names so the model can read e.g.
    ``dimensions.max_height_m`` directly.
    """
    out: dict[str, Any] = {"zone": profile.zone}
    if profile.unknown_zone:
        # Mirror the lookup_citation "miss" convention: an explicit
        # instruction so the model doesn't retry the same zone code.
        out["unknown_zone"] = True
        out["instruction"] = (
            "Zone not found. Verify the zone code, or use "
            "search_bylaw_evidence / get_document_outline to discover the "
            "correct code; do not retry get_zone_profile with the same value."
        )
        return out

    if profile.zone_full_name:
        out["zone_full_name"] = profile.zone_full_name
    if profile.chapter:
        out["chapter"] = profile.chapter

    if profile.dimensions is not None:
        dims = {
            key: value
            for key, value in profile.dimensions.model_dump().items()
            if value is not None
        }
        if dims:
            out["dimensions"] = dims

    if profile.uses is not None and (
        profile.uses.permitted or profile.uses.not_permitted
    ):
        uses: dict[str, Any] = {}
        if profile.uses.permitted:
            uses["permitted"] = list(profile.uses.permitted)
        if profile.uses.not_permitted:
            uses["not_permitted"] = list(profile.uses.not_permitted)
        out["uses"] = uses

    if profile.parking is not None:
        parking = {
            key: value
            for key, value in profile.parking.model_dump().items()
            if value is not None
        }
        if parking:
            out["parking"] = parking

    if profile.citations:
        out["citations"] = [
            {
                "citation_path": c.citation_path,
                **({"backs": list(c.backs)} if c.backs else {}),
            }
            for c in profile.citations
        ]
    if profile.confidence:
        out["confidence"] = dict(profile.confidence)
    return out


def compact_outline(outline: DocumentOutlineResponse) -> dict[str, Any]:
    return {
        "document": compact_document_summary(outline.document),
        "fragments": [
            {
                **(
                    {"citation_path": item.citation_path}
                    if item.citation_path
                    else {}
                ),
                **(
                    {"citation_label": item.citation_label}
                    if item.citation_label
                    else {}
                ),
                "page_start": item.page_start,
                "page_end": item.page_end,
                "text": item.text,
            }
            for item in outline.fragments
        ],
    }


def compact_document_list(docs: list[DocumentSummary]) -> dict[str, Any]:
    return {"documents": [compact_document_summary(d) for d in docs]}


def compact_address_profile(profile: AddressProfile) -> dict[str, Any]:
    """Project ``AddressProfile`` to the fields the LLM grounds an answer on.

    Drops null facets and the per-overlay ``attributes`` blob — the headline
    value already lives on the dedicated field / overlay ``label``, and the
    raw canonical attributes duplicate it byte-for-byte on every replayed
    turn. The ``citations`` list is kept whole (it's the grounding contract),
    with empty fields elided.
    """
    out: dict[str, Any] = {"address": profile.address}
    if profile.unresolvable:
        out["unresolvable"] = True
        out["instruction"] = (
            "Address could not be resolved spatially. Fall back to "
            "search_bylaw_evidence with the location slot, or ask the user "
            "to verify the address."
        )
        return out

    for field in (
        "civic_number",
        "street",
        "pid",
        "zone",
        "zone_chapter",
        "height_precinct",
        "far_precinct",
    ):
        value = getattr(profile, field)
        if value is not None:
            out[field] = value
    if profile.heritage is not None:
        out["heritage"] = profile.heritage
    if profile.bonus_zoning_eligible is not None:
        out["bonus_zoning_eligible"] = profile.bonus_zoning_eligible
    if profile.overlays:
        out["overlays"] = [
            {
                "kind": o.kind,
                "dataset_name": o.dataset_name,
                **({"label": o.label} if o.label else {}),
                **({"citation": o.citation} if o.citation else {}),
            }
            for o in profile.overlays
        ]
    if profile.citations:
        out["citations"] = [
            {
                k: v
                for k, v in {
                    "backs": c.backs,
                    "citation_path": c.citation_path,
                    "citation_label": c.citation_label,
                    "document_id": c.document_id,
                    "municipality": c.municipality,
                    "bylaw_name": c.bylaw_name,
                }.items()
                if v is not None
            }
            for c in profile.citations
        ]
    return out
