from __future__ import annotations

import argparse

from typing import Any

from bylaw_retrieval.retrieval import (
    CitationLookupRequest,
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
    retrieval_enabled_resolver,
)
from layer1.db.session import session_scope
from layer2.compliance.evaluator import (
    DocumentFilters,
    EvaluationRequest,
    EvaluatorService,
    SubmissionAttributeInput,
)
from layer2.compliance.db.models import SubmissionAttributeSource

SERVER_NAME = "Bylaw Retrieval MCP"


def create_mcp_server(db_url: str | None = None, *, all_documents: bool = False):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "The MCP SDK is not installed. Install the 'mcp' extra: pip install -e '.[mcp]'"
        ) from exc

    scope_resolver = None if all_documents else retrieval_enabled_resolver
    scope_note = (
        ""
        if all_documents
        else (
            " Every retrieval is hard-scoped to the set of documents "
            "explicitly enabled for retrieval (operator-published via the "
            "layer1 CLI). The document_id, municipality, and bylaw_name "
            "filters on requests are still accepted but they AND with the "
            "enabled set — they cannot reach a disabled document. If no "
            "documents are enabled, all queries return empty results."
        )
    )

    def _service(session) -> RetrievalService:
        return RetrievalService(session, default_document_id_resolver=scope_resolver)

    mcp = FastMCP(
        SERVER_NAME,
        json_response=True,
        instructions=(
            "These read-only tools return citation-grounded bylaw source "
            "fragments, tables, cross-references, and spatial features from "
            "linked geo datasets. They return source EVIDENCE; they do not "
            "determine what is legally permitted — that reasoning belongs to "
            "the calling agent.\n\n"
            "ADDRESS-AWARE QUERIES — READ THIS FIRST.\n"
            "When the user mentions ANY address, parcel, intersection, or "
            "named place (e.g. '6321 Quinpool Road', 'PID 00012345', 'corner "
            "of Spring Garden and Queen', 'Halifax Citadel'), you MUST set "
            "the structured 'location' field on search_bylaw_evidence and "
            "evaluate_submission_against_bylaws. Do NOT rely on putting the "
            "address in the 'query' string alone — that produces text-only "
            "matches and silently skips zone, height, FAR, heritage, and "
            "bonus-zoning datasets, which are the exact data needed to "
            "answer most planning questions.\n\n"
            "Example: for '6321 Quinpool Road' send "
            "location={civic_number: '6321', street: 'Quinpool Road'}. "
            "If the response contains a 'notes' array warning that the "
            "location was missing, re-issue the call with the slot set.\n\n"
            "CASE-OPEN SHORTCUT — get_address_profile.\n"
            "When a case-bound conversation opens on a specific address, "
            "parcel, or named place, call get_address_profile(address) FIRST. "
            "It resolves the address and returns the zone, overlay precincts "
            "(height, FAR), heritage status, bonus-zoning eligibility, whether "
            "the lot abuts a Schedule 7 pedestrian-oriented commercial street "
            "(the s.38(2)-vs-s.69(d) ground-floor-use branch), and "
            "citations in a single call — collapsing what would otherwise be "
            "several search_bylaw_evidence round-trips into one. Spend the "
            "rest of the tool budget on the actual question. If the profile "
            "comes back with unresolvable=true, fall back to "
            "search_bylaw_evidence with the location slot. If it comes back "
            "with resolution_quality other than 'rooftop', or with "
            "outside_mapped_area=true, the zone may be a neighbouring "
            "parcel's — qualify the answer with the profile's caveats "
            "instead of stating the zone as fact.\n\n"
            "WHEN TO USE evaluate_submission_against_bylaws.\n"
            "Use this fifth tool ONLY when the user has stated specific "
            "proposed attribute values (height, setbacks, use class, "
            "parking, etc.) AND a location. For exploratory questions "
            "('what rules apply here?', 'what's allowed in this zone?') "
            "use search_bylaw_evidence instead — the evaluator is the "
            "wrong tool when there's nothing concrete to compare against. "
            "Output is ADVISORY: surface clause citations to the user, "
            "never present a verdict without the citing clause. When "
            "overall_status is 'incomplete', ask the user for the missing "
            "attributes listed in unevaluated_attributes before proceeding."
            + scope_note
        ),
    )

    @mcp.tool()
    def list_documents(
        municipality: str | None = None,
        bylaw_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Use this when you need to discover which bylaws are available before retrieving evidence."""
        with session_scope(db_url) as session:
            service = _service(session)
            return [doc.model_dump(mode="json") for doc in service.list_documents(municipality, bylaw_name, limit)]

    @mcp.tool()
    def get_document_outline(
        document_id: int,
        max_fragments: int = 250,
        include_text: bool = False,
    ) -> dict:
        """Use this when you need the high-level document structure or citation map for one bylaw."""
        with session_scope(db_url) as session:
            service = _service(session)
            return service.get_document_outline(
                document_id=document_id,
                max_fragments=max_fragments,
                include_text=include_text,
            ).model_dump(mode="json")

    @mcp.tool()
    def lookup_citation(
        citation_path: str | None = None,
        structured: dict[str, Any] | None = None,
        document_id: int | None = None,
        include_context: bool = True,
        include_cross_references: bool = True,
        include_tables: bool = True,
    ) -> dict:
        """Retrieve the authoritative source fragment for a citation.

        Provide EXACTLY ONE of:
          - ``citation_path``: the exact path string, e.g. '4.2' or
            'Schedule B > 3'. Use when you already know the path.
          - ``structured``: a structured query that the server resolves
            internally, so you don't have to guess the format:
              * {"kind": "zone_attribute", "zone": "HR-2", "attribute": "max_height"}
              * {"kind": "schedule_row", "schedule": "Table 1A", "row": "HR-2"}
              * {"kind": "permitted_use", "use": "Restaurant use", "zone": "HR-2"}

        Supplying both, or neither, is a validation error. Accepted
        ``attribute`` values: max_height, max_height_storeys,
        max_lot_coverage, min_front_setback, min_side_setback,
        min_rear_setback, max_far, permitted_uses, parking_requirement.

        The ``permitted_use`` kind addresses a single use × zone cell of the
        permission matrix and returns its result under the ``permitted_use``
        key — a typed permission (permitted / conditional / not_permitted)
        plus any footnote condition, or a typed indeterminate result with a
        reason when the use or zone isn't in the matrix.
        """
        request = CitationLookupRequest(
            citation_path=citation_path,
            structured=structured,
            document_id=document_id,
            include_context=include_context,
            include_cross_references=include_cross_references,
            include_tables=include_tables,
        )
        with session_scope(db_url) as session:
            service = _service(session)
            response = service.lookup_citation(request)
            # ABS-261: envelope unwrapping for MCP backward compat —
            # see openai_tools.py for the rationale.
            if response.match is not None:
                return response.match.model_dump(mode="json")
            return response.model_dump(mode="json")

    @mcp.tool()
    def search_bylaw_evidence(
        query: str,
        document_id: int | None = None,
        municipality: str | None = None,
        bylaw_name: str | None = None,
        citation_path_prefix: str | None = None,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        location: dict[str, Any] | None = None,
        attribute_tag_filter: list[str] | None = None,
        include_context: bool = False,
        include_cross_references: bool = False,
        include_tables: bool = False,
        include_datasets: bool = False,
        limit: int = 8,
    ) -> dict:
        """Search for citation-grounded bylaw evidence.

        Use this when translating a user question into a citation-grounded
        retrieval request across one or more bylaws.

        ====================================================================
        CRITICAL: ADDRESSES MUST GO IN THE 'location' FIELD, NOT IN 'query'.
        ====================================================================

        If the user mentions ANY of:
          - a street address ("6321 Quinpool Road", "5648 Bilby Street")
          - a parcel id ("PID 00012345")
          - an intersection ("corner of Spring Garden and Queen")
          - a named place ("Halifax Citadel", "Public Gardens")

        you MUST populate the structured ``location`` argument. Embedding the
        address only in ``query`` produces TEXT-ONLY matches and silently
        skips the spatial datasets (zone, height precinct, FAR, heritage
        district, bonus zoning, shadow impact) — exactly the data needed to
        answer most planning questions about a specific property.

        Example call for "what's the max height at 6321 Quinpool Road":

            search_bylaw_evidence(
                query="maximum building height",
                location={
                    "civic_number": "6321",
                    "street": "Quinpool Road"
                }
            )

        Other ``location`` shapes:
          - civic_number + street (+ optional unit) for street addresses
          - parcel_id when known
          - named_place for landmarks
          - intersection_streets: list of 2+ street names
          - geometry: caller-supplied GeoJSON Point/Polygon in EPSG:4326

        --------------------------------------------------------------------
        Reading the response:

        Each match's ``linked_datasets[*].location_confidence`` reports the
        geocoder's confidence in the address-to-coordinate step (0..1).
        Values below ~0.85 mean the geocoder fell back to
        RANGE_INTERPOLATED or GEOMETRIC_CENTER quality. When you see a
        low-confidence value, qualify your answer accordingly — the
        spatial match may have hit a neighbouring precinct rather than
        the actual property.

        The response's top-level ``notes`` array carries server-side
        advisories. If you see a note saying the address should have been
        in the 'location' field, RE-ISSUE the call with the slot populated
        — do not just ignore it.
        """
        request = RetrievalRequest(
            query=query,
            document_id=document_id,
            municipality=municipality,
            bylaw_name=bylaw_name,
            citation_path_prefix=citation_path_prefix,
            page=page,
            page_start=page_start,
            page_end=page_end,
            location=LocationSlot.model_validate(location) if location else None,
            attribute_tag_filter=attribute_tag_filter,
            include_context=include_context,
            include_cross_references=include_cross_references,
            include_tables=include_tables,
            include_datasets=include_datasets,
            limit=limit,
        )
        with session_scope(db_url) as session:
            service = _service(session)
            return service.search(request).model_dump(mode="json")

    @mcp.tool()
    def get_address_profile(address: str) -> dict:
        """Use this at the start of a case-bound conversation when the user mentions an address, parcel, or named place.

        Returns the zone, overlay precincts, heritage status, and citations
        in one call. Saves multiple lookups.

        The ``address`` argument is free text in the same shape the
        ``search_bylaw_evidence`` ``location`` slot accepts — a civic address
        ("100 Robie Street") or a parcel id ("PID 00012345"). The tool
        resolves the address spatially, then composes the zone plus every
        linked overlay (height precinct, FAR precinct, heritage district,
        bonus zoning, Schedule 7 pedestrian-oriented commercial street) into a
        single ``AddressProfile``. ``abuts_pedestrian_street`` is a definitive
        true/false (not just null) whenever a Schedule 7 dataset is in scope,
        so the ground-floor-use answer can pick s.38(2) or s.69(d) without
        hedging both scenarios.

        If the address can't be resolved, the response carries
        ``unresolvable: true`` with empty citations rather than an error —
        fall back to the thin tools (``search_bylaw_evidence``) in that case.

        READ THE RESOLUTION QUALITY BEFORE STATING THE ZONE (ABS-466).
        ``resolution_quality`` says how the address became a point:
        ``rooftop`` matched the building; ``interpolated`` means the civic
        number was NOT found and the position was estimated along the street
        from surrounding numbering; ``centroid`` / ``approximate`` are coarser
        still. Anything below ``rooftop`` means the point may sit on a
        neighbouring parcel, so the zone — and every setback, height and
        floor-area figure derived from it — may belong to the wrong property.
        The response then carries ``caveats``: surface their substance to the
        user rather than stating the zone as fact. ``outside_mapped_area:
        true`` means a point WAS found but falls outside every mapped
        boundary — report that, do not report a zone.

        DOES THE ADDRESS EXIST? (ABS-469) ``civic_address_status`` is checked
        against the municipality's own data before anything is looked up.
        ``not_found`` means the street is known and NO published civic address
        or street-segment range covers this number — the address does not
        exist. The response then carries no zone at all, plus
        ``valid_civic_number_ranges`` and ``suggested_civic_numbers``: tell
        the user the address could not be found, quote those, and ask them to
        confirm. Do not re-issue the lookup with a geocoder — a geocoder
        answers a fabricated address by estimating a position from the
        surrounding numbering, which is the failure this check exists to
        stop. ``confirmed`` means the number exists; ``unverifiable`` means no
        municipal address data was in scope and says nothing either way.

        IS THE ZONE SAFE TO RELY ON? Independent of the geocode's quality:
        ``zone_boundary_distance_m`` (with ``nearest_other_zone``) reports
        when the point sits within ~25 m of a different zone, and
        ``parcel_zones`` lists every zone the parcel intersects when the lot
        is split between more than one. A split lot has no single governing
        zone — say so and ask where on the lot the work is proposed.
        """
        with session_scope(db_url) as session:
            service = _service(session)
            return service.get_address_profile(address).model_dump(mode="json")

    @mcp.tool()
    def get_adjacent_zoning(address: str) -> dict:
        """Use this when a setback (or any standard) is conditional on the zone of an ABUTTING property.

        Returns the subject parcel's own zone plus every abutting parcel's
        zone (with a coarse compass direction), so you can resolve the
        governing setback row and give a DEFINITIVE pass/fail instead of
        deferring the abutting-zone question to the customer — e.g. a Downtown
        (DH) lot whose required side yard is 0.0 m where it abuts another DH
        lot but greater where it abuts a residential zone.

        The ``address`` argument is free text in the same shape the
        ``search_bylaw_evidence`` ``location`` slot accepts — a civic address
        ("1250 Robie Street") or a parcel id ("PID 00012345").

        ``distinct_neighbour_zones`` is the set of zones among the neighbours:
        a single-element list means every abutting lot shares one zone, so the
        abutting-zone condition is unambiguous. A neighbour whose ``zone`` is
        null abuts but its centroid matched no zone polygon (sliver /
        right-of-way). If the address can't be resolved or no parcels dataset
        is ingested, the response carries ``unresolvable: true`` or a ``note``
        — fall back to ``search_bylaw_evidence`` in that case.
        """
        with session_scope(db_url) as session:
            service = _service(session)
            return service.get_adjacent_zoning(address).model_dump(mode="json")

    @mcp.tool()
    def get_zone_profile(
        zone: str,
        include: list[str] | None = None,
    ) -> dict:
        """Use this when the user asks about a specific zone's standards.

        Returns a one-call structured ``ZoneProfile`` — height, lot
        coverage, setbacks and floor area ratio under ``dimensions``;
        permitted / not-permitted use lists under ``uses``; parking
        applicability under ``parking``; and a ``citations`` list that
        backs every populated field.

        Prefer this over issuing several ``search_bylaw_evidence`` calls
        for the same zone — it collapses that sequence into one call.
        The implementation still composes semantic retrieval internally,
        so edge cases the DTO doesn't anticipate can fall back to
        ``search_bylaw_evidence``.

        ``include`` filters the sections (any of ``dimensions``, ``uses``,
        ``parking``, ``citations``); omit it for everything. A field is
        ``null`` when the bylaw is silent or retrieval couldn't extract it
        confidently; ``unknown_zone`` is ``true`` when the zone wasn't
        found (no exception is raised). For drill-down on a single
        citation, pass its ``citation_path`` to ``lookup_citation``.
        """
        with session_scope(db_url) as session:
            service = _service(session)
            return service.get_zone_profile(zone=zone, include=include).model_dump(mode="json")

    @mcp.tool()
    def evaluate_submission_against_bylaws(
        attributes: list[dict[str, Any]],
        location: dict[str, Any] | None = None,
        document_filters: dict[str, Any] | None = None,
        taxonomy_version: str | None = None,
        per_attribute_limit: int = 8,
        submission_id: int | None = None,
        persist_decision: bool = False,
    ) -> dict:
        """Evaluate a submission's attributes against applicable bylaw clauses.

        Returns a structured compliance matrix — per-attribute applicable
        clauses, computed deltas, and pass/fail/uncertain verdicts. Output
        is ADVISORY; surface clause citations alongside any verdict.

        WHEN TO USE
        -----------
        Call this tool ONLY when the user has stated specific proposed
        attribute values (height, setbacks, use class, parking, etc.)
        AND a location. If only one of attributes or location is
        provided, fall back to ``search_bylaw_evidence`` for the
        exploratory case.

        INPUT
        -----
        * ``attributes``: list of ``{attribute_key, value, unit?, source?}``.
          ``attribute_key`` MUST be a valid ID from the Phase-1 taxonomy
          (``src/layer2/compliance/attributes/taxonomy.yaml``).
        * ``location``: same ``LocationSlot`` shape used by
          ``search_bylaw_evidence`` (civic_number + street, parcel_id,
          intersection_streets, named_place, or geometry).
        * ``document_filters``: optional ``{municipality, bylaw_name,
          citation_path_prefix, document_id}`` to scope the corpus.
        * ``taxonomy_version``: defaults to the running version.
        * ``per_attribute_limit``: per-attribute retrieval limit; default 8.
        * ``submission_id`` + ``persist_decision``: when both are
          provided, the tool writes an ``approval_decision`` row pinned
          to the running evaluator version. Default is ``persist=False``
          for what-if queries from agents.

        OUTPUT
        ------
        ``EvaluationResponse``:
        * ``overall_status``: compliant | non_compliant | uncertain | incomplete.
        * ``attribute_results``: per-attribute ``{attribute_key,
          submitted_value, applicable_clauses[], verdict, delta}``.
        * ``unevaluated_attributes``: regulated attributes for this zone
          with no submitted value — prompt the user for them before
          presenting a verdict.
        * ``notes``: human-readable advisories (location unresolved,
          missing conditional inputs, low-confidence clause matches).

        IMPORTANT
        ---------
        Always populate ``location`` — same rule as
        ``search_bylaw_evidence``. An evaluation without a location
        skips spatial filtering and may surface clauses from the wrong
        zone. If ``overall_status`` is ``incomplete``, the agent's next
        turn should ASK the user for the missing attributes from
        ``unevaluated_attributes`` rather than guessing.
        """
        parsed_location = (
            LocationSlot.model_validate(location) if location else None
        )
        attribute_inputs: list[SubmissionAttributeInput] = []
        for entry in attributes:
            attr_key = entry.get("attribute_key")
            if not isinstance(attr_key, str) or not attr_key:
                continue
            source_token = entry.get("source") or "manual"
            try:
                source = SubmissionAttributeSource(source_token)
            except ValueError:
                source = SubmissionAttributeSource.MANUAL
            attribute_inputs.append(
                SubmissionAttributeInput(
                    attribute_key=attr_key,
                    value=entry.get("value"),
                    unit=entry.get("unit"),
                    source=source,
                    confidence=float(entry.get("confidence", 1.0)),
                )
            )
        filters = (
            DocumentFilters(
                municipality=document_filters.get("municipality"),
                bylaw_name=document_filters.get("bylaw_name"),
                citation_path_prefix=document_filters.get("citation_path_prefix"),
                document_id=document_filters.get("document_id"),
            )
            if document_filters
            else None
        )
        request = EvaluationRequest(
            attributes=attribute_inputs,
            location=parsed_location,
            document_filters=filters,
            taxonomy_version=taxonomy_version,
            per_attribute_limit=per_attribute_limit,
            submission_id=submission_id,
            persist_decision=persist_decision,
        )
        with session_scope(db_url) as session:
            retrieval_service = _service(session)
            evaluator = EvaluatorService(
                session, retrieval_service=retrieval_service
            )
            response = evaluator.evaluate(request)
            return response.to_json()

    @mcp.tool()
    def bylaw_query(
        intent: str,
        address: str | None = None,
        zone: str | None = None,
        proposed: dict[str, Any] | None = None,
    ) -> dict:
        """Use this when the user has stated both a location AND a specific intent (feasibility, use permission, dimensional check).

        Saves multiple round-trips by composing the full answer
        server-side. For exploratory questions where you don't yet know the
        intent, use the thin tools.

        ``intent`` is one of ``zone_feasibility``, ``address_lookup``,
        ``use_check``, ``dimensional_check``:

        * ``zone_feasibility`` (pass ``zone``) — full ``ZoneProfile``
          (dimensions + uses + parking) in one call.
        * ``address_lookup`` (pass ``address``) — the ``AddressProfile``
          (zone + overlays + citations).
        * ``use_check`` (pass ``zone``) — the zone's permitted /
          not-permitted use lists.
        * ``dimensional_check`` (pass ``zone`` and ``proposed`` such as
          ``{"height_m": 80}``) — the dimensions plus a ``ConformanceCheck``
          flagging each proposed value pass / fail / inconclusive.

        Internally dispatches to ``get_zone_profile`` / ``get_address_profile``
        (no duplicated retrieval logic). An ``intent`` outside the list
        returns ``unrecognized_intent: true`` with a ``suggested_tools``
        list rather than an error.
        """
        with session_scope(db_url) as session:
            service = _service(session)
            return service.bylaw_query(
                intent=intent,
                address=address,
                zone=zone,
                proposed=proposed,
            ).model_dump(mode="json")

    @mcp.resource("bylaw://documents")
    def documents_resource() -> str:
        with session_scope(db_url) as session:
            service = _service(session)
            documents = service.list_documents(limit=200)
            return "\n".join(
                f"{doc.id}: {doc.municipality} - {doc.bylaw_name}"
                for doc in documents
            )

    @mcp.resource("bylaw://documents/{document_id}/outline")
    def outline_resource(document_id: str) -> str:
        with session_scope(db_url) as session:
            service = _service(session)
            outline = service.get_document_outline(int(document_id), max_fragments=250, include_text=False)
            lines = [f"Document {outline.document.id}: {outline.document.municipality} - {outline.document.bylaw_name}"]
            lines.extend(
                f"{item.citation_path or '[uncited]'} | {item.fragment_type} | p.{item.page_start}-{item.page_end} | {item.text}"
                for item in outline.fragments
            )
            return "\n".join(lines)

    return mcp


def run_stdio(db_url: str | None = None, *, all_documents: bool = False) -> None:
    create_mcp_server(db_url, all_documents=all_documents).run()


def run_streamable_http(db_url: str | None = None, *, all_documents: bool = False) -> None:
    create_mcp_server(db_url, all_documents=all_documents).run(transport="streamable-http")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bylaw retrieval MCP server.")
    parser.add_argument("--db-url", default=None, help="Database URL override")
    parser.add_argument("--http", action="store_true", help="Use streamable HTTP transport")
    parser.add_argument(
        "--all-documents",
        action="store_true",
        help=(
            "Disable published-document scoping and expose every document "
            "in the database, including ones not enabled for retrieval. "
            "Dev/debug only — never use for a deployment."
        ),
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help=argparse.SUPPRESS,  # deprecated no-op, kept so stale launch configs don't crash
    )
    args = parser.parse_args()

    if args.latest_only:
        import sys  # noqa: PLC0415

        print(
            "layer1-mcp: --latest-only is deprecated and ignored. Retrieval "
            "is scoped to documents enabled via the layer1 CLI "
            "(enable-retrieval/disable-retrieval); remove the flag from "
            "your launch config.",
            file=sys.stderr,
        )

    if args.http:
        run_streamable_http(args.db_url, all_documents=args.all_documents)
        return
    run_stdio(args.db_url, all_documents=args.all_documents)


if __name__ == "__main__":
    main()

