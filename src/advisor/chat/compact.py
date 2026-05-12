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
    AncestorFragment,
    CrossReferenceSummary,
    DocumentOutlineResponse,
    DocumentSummary,
    LinkedDataset,
    RetrievalMatch,
    RetrievalResponse,
    TableSummary,
)


_ANCESTOR_TEXT_CHARS = 160
_TABLE_PREVIEW_CHARS = 500
_TABLE_PREVIEW_CELLS = 24


def _max_matches() -> int:
    """Cap on matches returned in compact mode.

    ``ADVISOR_COMPACT_MAX_MATCHES`` lets ops tune this without a
    redeploy. The default of 10 covers the common search shape where
    the request defaults to ``limit=8`` — most calls won't truncate.
    Higher-limit callers (e.g. an outline-style sweep) get clipped.
    """
    raw = os.environ.get("ADVISOR_COMPACT_MAX_MATCHES", "10")
    try:
        value = int(raw)
    except ValueError:
        return 10
    return value if value > 0 else 10


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
    cap = max_matches if max_matches is not None else _max_matches()
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
