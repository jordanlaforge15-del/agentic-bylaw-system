# Bylaw Retrieval MCP

This top-level directory contains the retrieval API and MCP server for the Layer 1 bylaw database.

The main `src/layer1` package remains focused on source ingestion, normalization, validation, and export. MCP-facing code lives here so transport, retrieval, and provider-adapter concerns are easy to find and remove or deploy independently.

## Layout

- `bylaw_retrieval/retrieval/`: read-only retrieval service and request/response schemas
- `bylaw_retrieval/server.py`: MCP tool and resource registration
- `bylaw_retrieval/api/app.py`: HTTP API with the same retrieval contract
- `bylaw_retrieval/openai_tools.py`: local OpenAI function-tool schemas and dispatcher
- `bylaw_retrieval/settings.py`: MCP/API-specific runtime settings

The package name is `bylaw_retrieval`, not `mcp`, so it does not shadow the external MCP SDK package imported as `mcp.server.fastmcp`.

## Installation

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[mcp]"
docker compose up -d postgres
alembic upgrade head
```

If you need API or development extras too:

```bash
python -m pip install -e ".[api,mcp,dev]"
```

## Configuration

The retrieval service reads the Layer 1 database through `layer1.db.session.session_scope`.

Relevant environment variables:

```bash
DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1
RETRIEVAL_API_HOST=127.0.0.1
RETRIEVAL_API_PORT=8001
```

`DATABASE_URL` is still owned by Layer 1 because the database belongs to the core ingestion system. API/MCP host and port settings are owned here.

## Run

Stdio MCP transport:

```bash
layer1-mcp
```

Streamable HTTP MCP transport:

```bash
python -m bylaw_retrieval.server --http
```

Plain HTTP retrieval API:

```bash
layer1-retrieval-api
```

## MCP Tools

The server exposes four read-only tools:

- `list_documents`: discover loaded bylaws
- `get_document_outline`: inspect a document citation map
- `lookup_citation`: retrieve an exact citation path
- `search_bylaw_evidence`: search citation-grounded source fragments

The tools return evidence from the normalized source model. They do not decide what is legally permitted.
