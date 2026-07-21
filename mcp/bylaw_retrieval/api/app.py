from __future__ import annotations

from typing import Any

from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalRequest, RetrievalService
from bylaw_retrieval.settings import get_retrieval_settings
from layer1.db.session import session_scope


def create_app(db_url: str | None = None):
    try:
        from fastapi import FastAPI, HTTPException, Query
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI is not installed. Install the 'api' extra: pip install -e '.[api]'"
        ) from exc

    # Deliberately unscoped (ABS-413): this dev-only HTTP mirror is a raw
    # browsing surface over the whole corpus, including documents not yet
    # enabled for retrieval. Deployed surfaces (advisor app, MCP server)
    # scope with retrieval_enabled_resolver.
    app = FastAPI(
        title="Layer 1 Retrieval API",
        version="0.1.0",
        description=(
            "Read-only retrieval API for citation-grounded bylaw evidence. "
            "This mirrors the MCP tool surface so local integrations and future hosted "
            "deployments share the same core contract."
        ),
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/documents")
    def list_documents(
        municipality: str | None = None,
        bylaw_name: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            service = RetrievalService(session)
            return [doc.model_dump(mode="json") for doc in service.list_documents(municipality, bylaw_name, limit)]

    @app.get("/documents/{document_id}")
    def document_outline(
        document_id: int,
        max_fragments: int = Query(default=250, ge=1, le=500),
        include_text: bool = False,
    ) -> dict[str, Any]:
        try:
            with session_scope(db_url) as session:
                service = RetrievalService(session)
                return service.get_document_outline(
                    document_id=document_id,
                    max_fragments=max_fragments,
                    include_text=include_text,
                ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/search")
    def search(request: RetrievalRequest) -> dict[str, Any]:
        with session_scope(db_url) as session:
            service = RetrievalService(session)
            return service.search(request).model_dump(mode="json")

    @app.post("/lookup-citation")
    def lookup_citation(request: CitationLookupRequest) -> dict[str, Any]:
        # ABS-261: service.lookup_citation no longer raises ValueError on
        # path-not-found; it returns CitationLookupResponse with match=None
        # and suggestions=[...]. We still surface 404 to HTTP clients
        # (preserving the old contract) but enrich the detail with the
        # suggestion list so callers can resolve the right form. The
        # ValueError-on-ambiguous case is unchanged.
        try:
            with session_scope(db_url) as session:
                service = RetrievalService(session)
                response = service.lookup_citation(request)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if response.match is None:
            scope = (
                f" in document {request.document_id}"
                if request.document_id is not None
                else ""
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Citation '{request.citation_path}' not found{scope}",
                    "suggestions": list(response.suggestions),
                },
            )
        return response.match.model_dump(mode="json")

    return app


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is not installed. Install the 'api' extra: pip install -e '.[api]'"
        ) from exc

    settings = get_retrieval_settings()
    uvicorn.run(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
    )

