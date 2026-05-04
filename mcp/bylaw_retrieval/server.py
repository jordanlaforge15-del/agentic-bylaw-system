from __future__ import annotations

import argparse

from typing import Any

from bylaw_retrieval.retrieval import (
    CitationLookupRequest,
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
    latest_document_id_resolver,
)
from layer1.db.session import session_scope

SERVER_NAME = "Bylaw Retrieval MCP"


def create_mcp_server(db_url: str | None = None, *, latest_only: bool = False):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "The MCP SDK is not installed. Install the 'mcp' extra: pip install -e '.[mcp]'"
        ) from exc

    scope_resolver = latest_document_id_resolver if latest_only else None
    scope_note = (
        " This server is launched with --latest-only: every retrieval is "
        "automatically scoped to the most recently ingested document. To "
        "search a different document, pass an explicit document_id, "
        "municipality, or bylaw_name on the request."
        if latest_only
        else ""
    )

    def _service(session) -> RetrievalService:
        return RetrievalService(session, default_document_id_resolver=scope_resolver)

    mcp = FastMCP(
        SERVER_NAME,
        json_response=True,
        instructions=(
            "Use these read-only tools to retrieve citation-grounded bylaw source fragments, "
            "tables, and cross-references. These tools do not determine what is legally "
            "permitted; they return source evidence for the agent to reason over."
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
        citation_path: str,
        document_id: int | None = None,
        include_context: bool = True,
        include_cross_references: bool = True,
        include_tables: bool = True,
    ) -> dict:
        """Use this when the user or agent already knows a citation and needs the authoritative source fragment."""
        request = CitationLookupRequest(
            citation_path=citation_path,
            document_id=document_id,
            include_context=include_context,
            include_cross_references=include_cross_references,
            include_tables=include_tables,
        )
        with session_scope(db_url) as session:
            service = _service(session)
            return service.lookup_citation(request).model_dump(mode="json")

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
        include_context: bool = True,
        include_cross_references: bool = True,
        include_tables: bool = True,
        include_datasets: bool = True,
        limit: int = 8,
    ) -> dict:
        """Search for citation-grounded bylaw evidence.

        Use this when translating a user question into a citation-grounded retrieval
        request across one or more bylaws.

        IMPORTANT — location handling: if the user question references a specific
        address, parcel, intersection, named place, or coordinate, populate the
        ``location`` argument rather than embedding the address in ``query``. The
        retrieval API uses ``location`` to drive spatial filtering of any geo
        datasets linked to matching fragments (e.g. height precincts).

        ``location`` is an object with optional fields:
          - civic_number + street (and optional unit) for street addresses
          - parcel_id (PID) when known
          - named_place for landmarks ("Halifax Citadel")
          - intersection_streets (list of 2+ street names)
          - geometry for caller-supplied GeoJSON Point/Polygon (EPSG:4326)
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
            include_context=include_context,
            include_cross_references=include_cross_references,
            include_tables=include_tables,
            include_datasets=include_datasets,
            limit=limit,
        )
        with session_scope(db_url) as session:
            service = _service(session)
            return service.search(request).model_dump(mode="json")

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


def run_stdio(db_url: str | None = None, *, latest_only: bool = False) -> None:
    create_mcp_server(db_url, latest_only=latest_only).run()


def run_streamable_http(db_url: str | None = None, *, latest_only: bool = False) -> None:
    create_mcp_server(db_url, latest_only=latest_only).run(transport="streamable-http")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bylaw retrieval MCP server.")
    parser.add_argument("--db-url", default=None, help="Database URL override")
    parser.add_argument("--http", action="store_true", help="Use streamable HTTP transport")
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help=(
            "Scope every retrieval to the most recently ingested document. "
            "Useful during development when re-ingesting the same bylaw "
            "leaves stale duplicates in the database. Explicit document_id, "
            "municipality, or bylaw_name filters in the request override."
        ),
    )
    args = parser.parse_args()

    if args.http:
        run_streamable_http(args.db_url, latest_only=args.latest_only)
        return
    run_stdio(args.db_url, latest_only=args.latest_only)


if __name__ == "__main__":
    main()

