# Layer 1 + Layer 2 Architecture

Layer 1 is a source-normalization pipeline. It stores provenance, layout blocks, a conservative fragment tree, tables, cross-references, and validation results. It does not extract legal rules or infer zoning permissions.

Layer 2 is the retrieval, prompt, answer, claim, and feedback layer. It treats Layer 1 as immutable legal source-of-truth storage and persists all question-time reasoning artifacts in Layer 2-owned tables.

Repo integration anchors:

- Layer 1 base metadata and models: `/workspace/src/layer1/db/base.py`
- Layer 1 sessions: `/workspace/src/layer1/db/session.py`
- Layer 1 settings: `/workspace/src/layer1/config.py`
- Layer 2 tables registered into shared Alembic metadata through `/workspace/alembic/env.py`

## Flow

1. `document ingest`: hash the local file, detect MIME type, and create `document` plus `ingestion_run` records.
2. `parse source`: use Docling as the primary PDF parser, collect PDF geometry with PyMuPDF fallback, and use a deterministic text parser for plain text and tests.
3. `page block extraction`: classify blocks as headings, paragraphs, list items, footnotes, table regions, headers, footers, or unknown.
4. `hierarchy reconstruction`: infer a fragment tree from `Part`, `Schedule`, numeric section labels, and list markers. Ambiguous content is preserved as `parse_status='uncertain'`.
5. `table handling`: table regions are stored as `source_table` and `source_table_cell` records when detected by simple text fallback or optional Camelot.
6. `cross-reference detection`: deterministic regexes capture municipal references such as `section 5.4`, `subsection 8.2.1`, and `Schedule B`.
7. `validation`: checks block accounting, tree validity, page ranges, citation uniqueness, table linkage, and cross-reference consistency.
8. `audit`: a review workflow ranks pages by extraction risk and can optionally attach structured LLM spot-check verdicts for sampled pages.

## Parser Tradeoffs

Docling is part of the default Layer 1 parser stack and is attempted first for PDFs. PyMuPDF remains the required geometry and fallback parser so a local machine can still ingest text-layer PDFs when Docling fails on a document or local runtime. PaddleOCR and Camelot remain optional because they are heavier and installation-sensitive. OCR is surfaced as a CLI flag and warning path, but production OCR tuning should be added against real scanned municipal PDFs.

The hierarchy builder is conservative: it favors traceability over aggressive merging. When a heading or paragraph cannot be confidently attached, it is preserved with explicit uncertainty rather than discarded.

## Audit Workflow

The audit path is intentionally separate from ingestion. It uses existing stored Layer 1 records to build page snapshots containing:

- source-page text from the original file when available
- page blocks
- fragments
- tables
- cross-references
- deterministic risk checks

Pages can then be sampled by risk or selected explicitly. Optional LLM review is used only as a triage layer and returns structured verdicts for human follow-up.

## Future Layers

## Layer 2 Flow

1. `query session`: persist the question, known facts, and normalized question text in `query_session`.
2. `retrieval`: combine metadata filtering, full-text search, vector similarity, table lookup, cross-reference expansion, hierarchy expansion, and verified-claim reuse into `retrieval_run` and `retrieval_result`.
3. `prompt assembly`: write exact system prompt, user prompt, model parameters, selected fragment IDs, and reused claim IDs to `prompt_log`.
4. `answering`: call a configurable LLM adapter, require grounded JSON output, persist raw output and final answer text in `answer_log`.
5. `claim emission`: persist atomic structured claims in `generated_claim` with source fragment IDs, optional table cell IDs, confidence, and verification state.
6. `feedback`: persist answer, retrieval, and claim reviews in `answer_feedback`, `retrieval_feedback`, and `claim_feedback`.
7. `reuse`: later queries can prefer verified claims and use prior retrieval feedback to boost missing fragments or suppress repeatedly irrelevant ones.

## Retrieval-First Design Notes

- Layer 2 is not a giant one-time extractor. Claims are generated at answer time only when a user asks a question.
- Layer 2 keeps the exact prompt text and raw model output so answer provenance is inspectable.
- PostgreSQL plus pgvector is the intended runtime because it supports `tsvector` search and vector indexes in the same store as Layer 1.
- SQLite remains supported for tests and smoke loops by falling back to JSON-backed embeddings and heuristic text matching.

## Current Weaknesses

- The deterministic mock LLM is suitable for tests, not for quality benchmarking.
- Table retrieval is intentionally simple and should be extended with row/column-aware reranking on real zoning schedules.
- Cross-reference and hierarchy expansion are conservative and may miss distant dependencies in larger bylaws.

## MCP Retrieval Interface

The top-level `mcp/` directory exposes a read-only retrieval interface on top of the normalized Layer 1 storage model. It is intended for external MCP clients and local tool-calling integrations, not for Layer 2's internal prompt, answer, claim, and feedback pipeline.

- All MCP-facing code lives under top-level `mcp/`.
- The MCP retrieval core is transport-agnostic and lives in `mcp/bylaw_retrieval/retrieval`.
- The MCP server is a thin wrapper over that core for tool-based LLM integrations.
- The plain HTTP API mirrors the MCP retrieval contract for local service use and future hosted deployment.
- OpenAI-local adapter code lives in `mcp/bylaw_retrieval/openai_tools.py` so provider-specific tool schemas do not leak into the MCP transport.

The MCP retrieval interface is deliberately evidence-oriented rather than conclusion-oriented. It returns cited fragments, ancestor context, related tables, and cross-references, but it does not decide what built form is legally permitted. That reasoning remains with the calling agent, Layer 2, or another downstream consumer.
