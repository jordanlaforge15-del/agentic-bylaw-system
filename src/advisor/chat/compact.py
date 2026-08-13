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
    AdjacentZoningProfile,
    AncestorFragment,
    BylawQueryResponse,
    CitationLookupResponse,
    CrossReferenceSummary,
    DocumentOutlineResponse,
    DocumentSummary,
    LinkedDataset,
    PermittedUseResult,
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
    # ABS-279: a structured permitted_use query resolves a single matrix
    # cell rather than a citation_path. Surface it as its own key (and stop
    # here) so the model reads the typed permission directly instead of
    # treating the empty match/suggestions as a miss to retry.
    if response.permitted_use is not None:
        out["permitted_use"] = compact_permitted_use(response.permitted_use)
        return out
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


def compact_permitted_use(result: PermittedUseResult) -> dict[str, Any]:
    """Project a ``PermittedUseResult`` (ABS-279) to its LLM-essential fields.

    Preserves the typed shape — ``permission`` plus the footnote condition on
    a conditional cell, or ``indeterminate``/``reason`` on a miss — and drops
    null fields so the model isn't billed for empty keys. The ``citation`` is
    collapsed to the same compact citation shape used elsewhere.
    """
    out: dict[str, Any] = {"use": result.use, "zone": result.zone}
    if result.indeterminate:
        out["indeterminate"] = True
        if result.reason_code is not None:
            out["reason_code"] = result.reason_code
        if result.reason is not None:
            out["reason"] = result.reason
        # ABS-351: an unknown-use miss ships the closest real matrix rows plus an
        # inline next-action so the model re-issues with the intended row rather
        # than guessing spellings (the ABS-261 anti-thrash pattern, applied to
        # the permitted-use axis).
        if result.suggested_uses:
            out["suggested_uses"] = list(result.suggested_uses)
            out["instruction"] = (
                "No matrix row matched this use. Re-issue the permitted_use "
                "query with the closest label from suggested_uses verbatim; do "
                "NOT guess further use variants."
            )
        elif result.reason_code == "unreadable_cell":
            # ABS-484: an UNKNOWN cell yields no citable support, so the
            # compact result deliberately carries none (the service keeps the
            # table pointer for callers that want to locate the gap). Without
            # this instruction the writer treats "no permission returned" as a
            # prohibition — the collapse the UNKNOWN state exists to prevent.
            out["instruction"] = (
                "This use's permission is NOT determinable from the ingested "
                "source — the matrix cell could not be read. State that it "
                "cannot be determined from the bylaw as ingested and refer the "
                "reader to the permission table itself; do NOT state or imply "
                "the use is permitted or prohibited, and do not cite anything "
                "as support for a permission verdict here."
            )
        return out

    out["permission"] = result.permission
    if result.footnote_ordinal is not None:
        out["footnote_ordinal"] = result.footnote_ordinal
    if result.condition_text is not None:
        out["condition_text"] = result.condition_text
    # A conditional cell's verdict hinges on the Table 1A footnote, not on the
    # use's general operating standards. Without this nudge the writer commits
    # to "conditional" but paraphrases unrelated operating requirements and
    # drops the footnote carve-out (observed on TC-005 T5 — ABS-280 AC2). The
    # inline instruction is the same writer-steering pattern ABS-261 uses for
    # citation-lookup misses.
    if result.permission == "conditional":
        footnote_ref = (
            f" (footnote {result.footnote_ordinal})"
            if result.footnote_ordinal is not None
            else ""
        )
        out["instruction"] = (
            "This use is CONDITIONALLY permitted in this zone. State the verdict "
            "as conditional — not a plain 'permitted' — and quote the "
            f"'condition_text' footnote verbatim{footnote_ref} as the governing "
            "condition on permission. Do not substitute the use's general "
            "operating standards for this footnote condition."
        )
    if result.citation is not None:
        citation: dict[str, Any] = {}
        if result.citation.citation_path is not None:
            citation["citation_path"] = result.citation.citation_path
        if result.citation.citation_label is not None:
            citation["citation_label"] = result.citation.citation_label
        if result.citation.page_start is not None:
            citation["page_start"] = result.citation.page_start
        if result.citation.page_end is not None:
            citation["page_end"] = result.citation.page_end
        if result.citation.municipality is not None:
            citation["municipality"] = result.citation.municipality
        if result.citation.bylaw_name is not None:
            citation["bylaw_name"] = result.citation.bylaw_name
        out["citation"] = citation
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
    cap = max_matches if max_matches is not None else _compact_ceiling()
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


# ABS-409: bounds for matrix-enumerated use lists in the compact projection.
# A full Regional Centre zone column carries ~80-90 uses across the matrix
# pages; the cap keeps the per-call token cost bounded while the '+N more'
# marker tells the model the list is truncated (drill down via
# lookup_citation on the table's citation, or a permitted_use cell query).
_USE_LIST_CAP = 40
_CONDITION_TEXT_CAP = 160


def _capped_list(items: list[Any]) -> list[Any]:
    if len(items) <= _USE_LIST_CAP:
        return items
    return items[:_USE_LIST_CAP] + [f"+{len(items) - _USE_LIST_CAP} more"]


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
        profile.uses.permitted
        or profile.uses.not_permitted
        or profile.uses.conditional
        or profile.uses.undetermined
    ):
        uses: dict[str, Any] = {}
        if profile.uses.permitted:
            uses["permitted"] = _capped_list(list(profile.uses.permitted))
        if profile.uses.not_permitted:
            uses["not_permitted"] = _capped_list(list(profile.uses.not_permitted))
        if profile.uses.conditional:
            # ABS-409: matrix-enumerated conditional uses. Condition text is
            # capped per item — footnote legends run long and repeat across
            # items; the model can lookup_citation the table for full text.
            conditional = [
                {
                    "use": item.use,
                    **(
                        {"footnote": item.footnote_ordinal}
                        if item.footnote_ordinal is not None
                        else {}
                    ),
                    **(
                        {"condition": item.condition[:_CONDITION_TEXT_CAP]}
                        if item.condition
                        else {}
                    ),
                }
                for item in profile.uses.conditional
            ]
            uses["conditional"] = _capped_list(conditional)
        if profile.uses.undetermined:
            # ABS-484: the UNKNOWN list. Without the inline instruction the
            # writer reads "absent from permitted" as "prohibited" — exactly
            # the collapse the undetermined list exists to prevent.
            uses["undetermined"] = _capped_list(list(profile.uses.undetermined))
            uses["instruction"] = (
                "The uses under 'undetermined' could NOT be determined from "
                "the ingested source — their permission cell was missing or "
                "unreadable and no prose stated it. Say the answer is not "
                "determinable from the ingested source and refer the reader to "
                "the bylaw's permission table itself; do NOT report them as "
                "permitted, as prohibited, or as absent from the zone, and do "
                "not cite anything as support for them."
            )
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
        # ABS-409: table-backed citations may lack a citation_path on corpora
        # whose captions haven't been backfilled — fall back to label + pages
        # so the model can still ground and page-scope its follow-ups.
        out["citations"] = [
            {
                **(
                    {"citation_path": c.citation_path}
                    if c.citation_path
                    else {
                        **({"citation_label": c.citation_label} if c.citation_label else {}),
                        **({"pages": [c.page_start, c.page_end]} if c.page_start else {}),
                    }
                ),
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
    # ABS-469: an address that does not exist is answered first and alone.
    # Anything else in the projection (a zone, an overlay, a precinct) would
    # be read as a property of a property that isn't there — the model is
    # given the refusal and the correction, and nothing to answer from.
    if profile.civic_address_status == "not_found":
        out["civic_address_status"] = "not_found"
        if profile.civic_address_evidence:
            out["civic_address_evidence"] = profile.civic_address_evidence
        if profile.valid_civic_number_ranges:
            out["valid_civic_number_ranges"] = profile.valid_civic_number_ranges
        if profile.suggested_civic_numbers:
            out["suggested_civic_numbers"] = profile.suggested_civic_numbers
        if profile.caveats:
            out["caveats"] = profile.caveats
        out["instruction"] = (
            "This civic number does not exist in the municipality's own "
            "address data. Do NOT state a zone or any figure derived from "
            "one, and do NOT retry with a geocoder — tell the user the "
            "address could not be found, quote the civic-number ranges that "
            "do exist on that street, and ask them to confirm the address."
        )
        return out
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
                # ABS-473: an overlay from a by-law we don't hold arrives with
                # its citation already stripped, which on its own reads as
                # "no citation handy" rather than "wrong by-law". Compacting
                # these two out was what let a Suburban Housing Accelerator
                # height precinct present as an uncited Schedule 15 precinct.
                **(
                    {"governing_bylaw": o.governing_bylaw}
                    if o.governing_bylaw and o.governing_bylaw_held is False
                    else {}
                ),
                **(
                    {"governing_bylaw_held": False}
                    if o.governing_bylaw_held is False
                    else {}
                ),
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
    # ABS-466: the resolution's quality rides along with the zone it produced.
    # Dropping it here is what let an interpolated point present to the model
    # as indistinguishable from a rooftop match.
    if profile.resolution_quality is not None:
        out["resolution_quality"] = profile.resolution_quality
    if profile.location_confidence is not None:
        out["location_confidence"] = profile.location_confidence
    if profile.outside_mapped_area:
        out["outside_mapped_area"] = True
    # ABS-469: a zone is only as safe as the parcel it names. These say when
    # the point sits on a zone line or the lot is split between zones —
    # unsafe for reasons the resolution quality above cannot express, because
    # they apply to a perfect rooftop match too.
    if profile.zone_boundary_distance_m is not None:
        out["zone_boundary_distance_m"] = profile.zone_boundary_distance_m
        out["nearest_other_zone"] = profile.nearest_other_zone
    if profile.parcel_zones:
        out["parcel_zones"] = profile.parcel_zones
    # ABS-472: which by-law the zone above actually belongs to, and whether we
    # hold it. Without this the model sees a zone code and a citation to
    # whichever document the zoning layer happens to be linked to, and reasons
    # the parcel's standards out of a by-law that does not govern it.
    if profile.governing_bylaw is not None:
        out["governing_bylaw"] = profile.governing_bylaw
    if profile.governing_bylaw_status in {"held", "not_held"}:
        out["governing_bylaw_status"] = profile.governing_bylaw_status
    if profile.caveats:
        out["caveats"] = profile.caveats
        out["instruction"] = _address_profile_instruction(profile)
    return out


def _address_profile_instruction(profile: AddressProfile) -> str:
    """The one next-step sentence a caveated profile carries.

    Ordered by how wrong the answer would be if ignored: a zone whose by-law
    we don't hold beats no zone at all beats a split lot beats a boundary a
    few metres away beats an imprecise point.
    """
    # ABS-472: the others say the zone might be the wrong parcel's. This one
    # says the standards behind it are in a document we do not have — so
    # answering from the by-laws we DO have is not an approximation, it is a
    # different property's rules.
    if profile.governing_bylaw_status == "not_held":
        return (
            f"This parcel is governed by the {profile.governing_bylaw}, which "
            "is not in this corpus. State the zone and name that by-law, then "
            "stop: do NOT give permitted uses, height, setbacks, floor area "
            "or any other standard, and do NOT substitute a figure from "
            "another by-law. Tell the user the governing by-law must be "
            "confirmed with HRM Planning & Development."
        )
    if profile.outside_mapped_area:
        return (
            "No zone could be assigned: the resolved point falls outside "
            "every mapped boundary. Do not state a zone — tell the user the "
            "address is outside the mapped plan area and must be confirmed "
            "with HRM."
        )
    # ABS-473: the zone is fine, but an overlay over it — height precinct,
    # FAR precinct — belongs to a by-law we do not hold. Ranked above the
    # split-lot and proximity cases below because those say the figure may be
    # the neighbour's; this says the figure's rules are in a document we do
    # not have, which no amount of geocoding precision fixes.
    unheld = next(
        (
            o
            for o in profile.overlays
            if o.governing_bylaw_held is False and o.governing_bylaw
        ),
        None,
    )
    if unheld is not None:
        return (
            f"The {unheld.kind.replace('_', ' ')} here is mapped under the "
            f"{unheld.governing_bylaw}, which is not in this corpus. State "
            "the mapped value and name that by-law, then stop: do NOT give "
            "the standard that applies it, and do NOT substitute the "
            "equivalent schedule from a by-law that IS held — it does not "
            "govern this ground. Tell the user it must be confirmed with HRM "
            "Planning & Development."
        )
    if profile.parcel_zones:
        return (
            "This lot is split between "
            f"{' and '.join(profile.parcel_zones)}. Do not answer as though "
            "one zone governed the whole parcel — say the lot is split and "
            "ask where on it the work is proposed."
        )
    if profile.zone_boundary_distance_m is not None:
        return (
            f"The point is {profile.zone_boundary_distance_m:.0f} m from the "
            f"{profile.nearest_other_zone} boundary, so the zone above may be "
            "the adjoining parcel's. State the proximity and tell the user to "
            "confirm the zoning with HRM before relying on any figure."
        )
    return (
        "This address did not resolve precisely to the property, so the zone "
        "above may belong to a neighbouring parcel. State the uncertainty in "
        "your answer — do not present the zone or any figure derived from it "
        "as settled fact."
    )


def compact_adjacent_zoning(profile: AdjacentZoningProfile) -> dict[str, Any]:
    """Project ``AdjacentZoningProfile`` to the fields the LLM grounds on (ABS-375).

    Keeps the subject zone, the per-neighbour (pid, zone, direction), the
    distinct-zone summary, and one citation. Elides null fields so the
    replayed-every-turn tool_result stays small.
    """
    out: dict[str, Any] = {"address": profile.address}
    if profile.unresolvable:
        out["unresolvable"] = True
        out["instruction"] = (
            "Address could not be resolved spatially, so abutting parcels "
            "could not be found. Fall back to search_bylaw_evidence or ask "
            "the user to verify the address."
        )
        return out
    if profile.subject_pid is not None:
        out["subject_pid"] = profile.subject_pid
    if profile.subject_zone is not None:
        out["subject_zone"] = profile.subject_zone
    if profile.neighbours:
        out["neighbours"] = [
            {
                k: v
                for k, v in {
                    "pid": n.pid,
                    "zone": n.zone,
                    "direction": n.direction,
                }.items()
                if v is not None
            }
            for n in profile.neighbours
        ]
    if profile.distinct_neighbour_zones:
        out["distinct_neighbour_zones"] = profile.distinct_neighbour_zones
    if profile.citation is not None:
        c = profile.citation
        out["citation"] = {
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
    if profile.note is not None:
        out["note"] = profile.note
    return out


def compact_bylaw_query(response: BylawQueryResponse) -> dict[str, Any]:
    """Project a ``BylawQueryResponse`` to its LLM-essential fields (ABS-274).

    Reuses ``compact_zone_profile`` / ``compact_address_profile`` for the
    composed sub-DTOs so the projection rules stay in one place, and elides
    null facets. An unrecognised intent carries an explicit fall-back
    instruction (mirroring the lookup_citation / unknown-zone convention) so
    the model pivots to the thin tools without re-issuing the same intent.
    """
    out: dict[str, Any] = {"intent": response.intent}
    if response.unrecognized_intent:
        out["unrecognized_intent"] = True
        out["suggested_tools"] = list(response.suggested_tools)
        out["instruction"] = (
            "Intent not recognised. Use one of the suggested_tools (thin "
            "tools) to answer this question; do not retry bylaw_query with "
            "the same intent."
        )
        return out

    if response.suggested_tools:
        # Recognised intent but a required slot (zone/address) was missing.
        out["suggested_tools"] = list(response.suggested_tools)
        out["instruction"] = (
            "This intent needs a zone or address. Supply it, or fall back to "
            "the suggested_tools."
        )
        return out

    if response.zone_profile is not None:
        out["zone_profile"] = compact_zone_profile(response.zone_profile)
    if response.address_profile is not None:
        out["address_profile"] = compact_address_profile(response.address_profile)
    if response.conformance_check is not None:
        check = response.conformance_check
        out["conformance_check"] = {
            "zone": check.zone,
            "overall": check.overall,
            "results": [
                {
                    k: v
                    for k, v in {
                        "attribute": r.attribute,
                        "proposed": r.proposed,
                        "limit": r.limit,
                        "comparison": r.comparison,
                        "status": r.status,
                        "note": r.note,
                    }.items()
                    if v is not None
                }
                for r in check.results
            ],
        }
    if response.citations:
        out["citations"] = [
            {
                k: v
                for k, v in {
                    "backs": c.backs,
                    "citation_path": c.citation_path,
                    "citation_label": c.citation_label,
                }.items()
                if v
            }
            for c in response.citations
        ]
    return out
