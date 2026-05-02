# Layer 1 + Layer 2 Bylaw Assistant

Local-only source normalization and retrieval-first answering for municipal land-use bylaws. Layer 1 ingests official source files into auditable source tables. Layer 2 retrieves from those Layer 1 artifacts, builds grounded prompts, generates answers plus reusable claims, and persists feedback and trace logs.

Repo specifics: this is a Python 3.11+ package using SQLAlchemy, Alembic, Pydantic, Typer, Docling as the primary PDF parser, and PyMuPDF/pdfplumber fallbacks with optional Camelot/PaddleOCR integrations.

Layer 1 integration paths used by Layer 2:

- Layer 1 ORM base and source tables: `/workspace/src/layer1/db/base.py`
- Layer 1 session utilities: `/workspace/src/layer1/db/session.py`
- Layer 1 settings using `DATABASE_URL`: `/workspace/src/layer1/config.py`
- Alembic metadata entrypoint: `/workspace/alembic/env.py`

## Setup

Fast path for a fresh shell:

```bash
./scripts/dev-setup.sh
source .venv/bin/activate
```

Install heavier parser integrations such as Docling, Camelot, and PaddleOCR:

```bash
./scripts/dev-setup.sh --with-parsers
source .venv/bin/activate
```

Manual setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
```

`docker compose` now uses `pgvector/pgvector:pg16` so Layer 2 can use PostgreSQL full-text search and pgvector in the same database. SQLite remains useful for fast unit and smoke tests, but full Layer 2 behavior should be validated on PostgreSQL.

Install the heavier optional parser integrations when needed:

```bash
python -m pip install -e ".[parsers]"
```

`docling` is part of the default Layer 1 install and is attempted first for PDF ingest. The `parsers` extra is now only for optional Camelot and PaddleOCR integrations.

## CLI

```bash
layer1 init-db
layer1 ingest tests/fixtures/synthetic_bylaw.txt --municipality Sampleton --bylaw-name "Synthetic Zoning Bylaw"
layer1 ingest-dir ./bylaws --ocr --debug
layer1 validate 1
layer1 show-summary 1
layer1 export-json 1 --out examples/synthetic_bylaw_export.json
layer1 audit-pages 1 --sample 5
layer1 audit-page 1 26 --llm --model gpt-5.4-mini
```

Every command accepts `--db-url`. For quick local tests, SQLite also works:

```bash
layer1 ingest tests/fixtures/synthetic_bylaw.txt --db-url sqlite:///layer1.db --create-schema
```

## Layer 2 CLI

Layer 2 owns retrieval, prompt construction, answer generation, structured claims, trace logs, and feedback.

## What Layer 2 Does

Layer 2 is the question-time reasoning layer on top of Layer 1's canonical source model.

Given a bylaw question, Layer 2:

- retrieves relevant `source_fragment`, `source_table`, and `cross_reference` records from Layer 1
- expands context with hierarchy and resolved cross-references
- optionally reuses previously verified claims
- assembles a grounded prompt with the selected source context
- calls a configurable LLM adapter
- persists the retrieval run, prompt, raw model output, final answer, and generated claims
- accepts answer, retrieval, and claim feedback for later reuse

Layer 2 is retrieval-first. It does not try to pre-extract the whole bylaw into a static rule base before questions are asked.

## How Layer 2 Works

The default pipeline is:

1. `query understanding`
   Normalize the question and extract likely topics, legal concepts, section hints, zone codes, and use keywords.
2. `metadata filtering`
   Narrow the search space by `document_id`, municipality, and any known facts supplied at runtime.
3. `full-text retrieval`
   Search `source_fragment` text and citation labels. On PostgreSQL this uses `tsvector` and GIN indexes.
4. `vector retrieval`
   Compare the question embedding against `fragment_embedding` rows stored in PostgreSQL with pgvector.
5. `table retrieval`
   Pull table cells that match the question, including simple row-aware boosts for zone-style lookups such as `R1`.
6. `expansion`
   Add parent fragments, sibling fragments, and resolved cross-reference targets where they help complete context.
7. `reranking`
   Apply deterministic scoring boosts for keyword overlap, definition needs, zone matches, use matches, and feedback.
8. `prompt assembly`
   Build a versioned system prompt plus user prompt, including selected fragments and safe cached claims.
9. `answer generation`
   Call the configured LLM, require structured grounded output, and persist the full trace.
10. `claim persistence and feedback`
    Store reusable claims and allow later verification or correction through CLI feedback commands.

## Layer 2 Storage

Layer 2 adds the following tables:

- `query_session`
- `fragment_embedding`
- `retrieval_run`
- `retrieval_result`
- `prompt_log`
- `answer_log`
- `generated_claim`
- `answer_feedback`
- `claim_feedback`
- `retrieval_feedback`

These tables are append-oriented trace logs. They are meant to preserve what was retrieved, what was shown to the model, what the model answered, and what later reviewers said about that run.

## Runtime Environment

The app reads configuration from environment variables and from `.env` when running from the repo. The same `DATABASE_URL` is used by Layer 1, Layer 2, and Alembic.

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://layer1:layer1@localhost:5432/layer1` | Layer 1, Layer 2, Alembic | SQLAlchemy database URL. Use `postgres` as the hostname inside Docker Compose and `localhost` from the host. |
| `OCR_ENABLED` | `false` | Layer 1 | Enables OCR-capable parsing paths when the installed parser stack supports them. |
| `CAMELOT_ENABLED` | `false` | Layer 1 | Enables Camelot table extraction when parser dependencies are installed. |
| `LOG_LEVEL` | `INFO` | Layer 1 | Logging verbosity. |
| `BOILERPLATE_REPETITION_THRESHOLD` | `2` | Layer 1 | Repetition threshold used when detecting boilerplate headers/footers. |
| `LAYER2_LLM_BASE_URL` | unset | Layer 2 | OpenAI-compatible chat completions base URL. For OpenAI, use `https://api.openai.com/v1`. If unset, Layer 2 uses the mock LLM. |
| `LAYER2_LLM_API_KEY` | unset | Layer 2 | API key for the configured LLM endpoint. For OpenAI, this can be the same value as `OPENAI_API_KEY`. |
| `LAYER2_LLM_MODEL` | `mock-layer2` | Layer 2 | LLM model name. For OpenAI-compatible endpoints, set this to the model you want Layer 2 to call. |
| `LAYER2_EMBEDDING_BASE_URL` | unset | Layer 2 | OpenAI-compatible embeddings base URL. If unset, Layer 2 uses the local hashing embedder. |
| `LAYER2_EMBEDDING_API_KEY` | unset | Layer 2 | API key for the configured embedding endpoint. |
| `LAYER2_EMBEDDING_MODEL` | `hashing-bge-small-en-v1.5` | Layer 2 | Embedding model name. Values starting with `hashing` or `mock` use the local hashing client. Values starting with `sentence-transformers:` use the local sentence-transformers client. |
| `LAYER2_EMBEDDING_DIMENSIONS` | `384` | Layer 2 | Embedding vector dimension used by the configured embedding client. |
| `LAYER2_PROMPT_VERSION` | `v1` | Layer 2 | Prompt template/version label stored with prompt logs. |
| `LAYER2_RETRIEVAL_VERSION` | `v1` | Layer 2 | Retrieval version label stored with retrieval runs. |
| `LAYER2_TOKEN_BUDGET` | `3000` | Layer 2 | Approximate source-context token budget for prompt assembly. |
| `LAYER2_TOP_K` | `8` | Layer 2 | Default number of retrieval candidates per retrieval channel. |
| `LAYER2_MAX_CACHED_CLAIMS` | `4` | Layer 2 | Maximum verified claims to reuse in prompt context. |
| `OPENAI_API_KEY` | unset | Docker Compose / optional local use | Passed into the Codex container and commonly reused as `LAYER2_LLM_API_KEY` or `LAYER2_EMBEDDING_API_KEY`. |
| `POSTGRES_DB` | `layer1` in `docker-compose.yml` | PostgreSQL container | Database created by the local Postgres container. |
| `POSTGRES_USER` | `layer1` in `docker-compose.yml` | PostgreSQL container | Database user created by the local Postgres container. |
| `POSTGRES_PASSWORD` | `layer1` in `docker-compose.yml` | PostgreSQL container | Database password created by the local Postgres container. |
| `CODEX_HOME` | `/home/codex/.codex` in `docker-compose.yml` | Codex container | Codex home directory inside the dev container. |
| `NPM_CONFIG_PREFIX` | `/home/codex/.npm-global` in `docker-compose.yml` | Codex container | npm global install prefix inside the dev container. |
| `PIP_BREAK_SYSTEM_PACKAGES` | `1` in `docker-compose.yml` | Codex container | Allows package installation in the container image/runtime. |

Common local OpenAI configuration:

```bash
LAYER2_LLM_BASE_URL=https://api.openai.com/v1
LAYER2_LLM_API_KEY=${OPENAI_API_KEY}
LAYER2_LLM_MODEL=gpt-5.4
```

Behavior notes:

- If no LLM endpoint is configured, Layer 2 falls back to the deterministic mock LLM for tests and smoke runs.
- If no embedding endpoint is configured, Layer 2 falls back to the local hashing embedder for tests and smoke runs.
- PostgreSQL plus pgvector is the intended production path. SQLite is supported for tests and local structure checks only.

```bash
layer2 init-db
layer2 embed-fragments 1
layer2 retrieve 1 --question "Is a temporary use permitted?"
layer2 answer 1 --question "What is the minimum lot area for R1?" --debug
layer2 run-eval 1 --eval-set evals/sampleton_layer2_eval.json
layer2 submit-answer-feedback 1 --rating 2 --is-correct false --is-incomplete true --notes "Missing context"
layer2 submit-claim-feedback 21 --is-correct true --notes "Verified for reuse" --reviewer-type planner
layer2 submit-retrieval-feedback 1 --missing-source-fragment-id 4 --irrelevant-source-fragment-id 8
```

## Layer 2 Operator Workflow

Typical usage is:

1. ingest a source document with Layer 1
2. generate fragment embeddings for that document
3. inspect retrieval for representative questions
4. run the answer pipeline
5. inspect persisted query, retrieval, answer, and claim logs
6. submit feedback on bad answers, missing fragments, or incorrect claims
7. rerun later questions and reuse verified claims when appropriate

For example:

```bash
layer1 ingest ./bylaws/sampleton-bylaw.pdf --municipality Sampleton --bylaw-name "Land Use Bylaw"
layer2 embed-fragments 1
layer2 retrieve 1 --question "Is a temporary use permitted?" --top-k 10
layer2 answer 1 --question "What is the minimum lot area for R1?" --debug
layer2 show-query 1
layer2 show-retrieval 1
layer2 show-answer 1
layer2 submit-answer-feedback 1 --rating 2 --is-correct false --is-incomplete true --notes "Missed Schedule B"
```

Recommended local end-to-end flow on PostgreSQL:

```bash
docker compose up -d postgres
alembic upgrade head
layer1 ingest tests/fixtures/synthetic_bylaw.txt --municipality Sampleton --bylaw-name "Synthetic Zoning Bylaw"
layer2 embed-fragments 1
layer2 answer 1 --question "Is a temporary use permitted?" --debug
layer2 run-eval 1 --eval-set evals/sampleton_layer2_eval.json
```

Fast smoke flow on SQLite:

```bash
DATABASE_URL=sqlite:///layer2_demo.db alembic upgrade head
layer1 ingest tests/fixtures/synthetic_bylaw.txt --municipality Sampleton --bylaw-name "Synthetic Zoning Bylaw" --db-url sqlite:///layer2_demo.db
layer2 embed-fragments 1 --db-url sqlite:///layer2_demo.db
layer2 run-eval 1 --db-url sqlite:///layer2_demo.db --eval-set evals/sampleton_layer2_eval.json
```

## Layer 2 Evaluation

Layer 2 ships with a lightweight local evaluation harness. Eval cases live in `evals/` and can declare:

- a question
- expected topics
- expected fragment IDs
- expected citation labels
- expected answer keywords
- expected claim shapes

Run it with:

```bash
layer2 run-eval 1 --eval-set evals/sampleton_layer2_eval.json
```

This is intended for regression checking and retrieval/prompt tuning, not for legal-grade scoring.

Parsing profiles are supported for region/document-family specific rules:

```bash
layer1 ingest tests/fixtures/synthetic_bylaw.txt --profile default
layer1 ingest "PEN 223_Effective_June 17 2017.pdf" --profile halifax
```

Available profiles:

- `default`: conservative generic municipal-bylaw heuristics
- `halifax`: enables the current Halifax-style compound numbering and definition/list heuristics
- `halifax-regional-centre-lub`: adds Regional Centre Land Use By-law cleanup and table-range handling

## Data Model

The initial migration creates:

- `document`
- `ingestion_run`
- `page_block`
- `source_fragment`
- `source_table`
- `source_table_cell`
- `cross_reference`

Enums are used for block type, fragment type, parse status, ingestion status, and cross-reference resolution status.

Layer 2 adds:

- `query_session`
- `fragment_embedding`
- `retrieval_run`
- `retrieval_result`
- `prompt_log`
- `answer_log`
- `generated_claim`
- `answer_feedback`
- `claim_feedback`
- `retrieval_feedback`

Layer 2 retrieval uses:

- PostgreSQL metadata filters
- PostgreSQL full-text search over `source_fragment` and `source_table`
- pgvector fragment embeddings
- hierarchy and cross-reference expansion
- verified claim reuse

Layer 2 generated claims currently support categories including:

- `definition`
- `use_permission`
- `dimensional_standard`
- `parking_requirement`
- `applicability_condition`
- `exception`
- `cross_reference_dependency`
- `general_regulation`
- `procedure_requirement`

## Tests

```bash
pytest
```

Tests cover citation parsing, hierarchy reconstruction, cross-reference detection, boilerplate detection, SQLite-backed integration ingest, and preservation of uncertain fragments.

Layer 2 tests add query understanding, retrieval merge and expansion, prompt assembly, claim parsing, end-to-end answer persistence, feedback persistence, and eval harness smoke coverage.

Useful verification commands:

```bash
ruff check src tests
pytest -q
layer2 run-eval 1 --eval-set evals/sampleton_layer2_eval.json
```

## Layer 1 Audit

Layer 1 now includes a review-oriented audit workflow for spot checking extraction fidelity.

Deterministic audit:

```bash
layer1 audit-pages 1 --sample 5
layer1 audit-page 1 26
```

This ranks pages by structural risk signals such as uncertain fragments, unusual roots, duplicate citation downgrades, table presence, unresolved cross-references, and unaccounted blocks.

Optional LLM-assisted audit:

```bash
export OPENAI_API_KEY=...
layer1 audit-pages 1 --sample 5 --llm
layer1 audit-page 1 26 --llm --out examples/page26_audit.json
```

The LLM audit does not replace deterministic checks or human review. It consumes source-page text plus Layer 1 blocks/fragments/tables/cross-references and returns a structured verdict to help prioritize manual inspection.

## Known Limitations

- OCR is exposed as a flag, but production PaddleOCR image extraction needs tuning against scanned PDFs.
- Docling is installed by default and used first for PDF ingest; PyMuPDF supplies the reliable geometry fallback when Docling fails or is unavailable at runtime.
- Table extraction is intentionally sparse for text-heavy bylaws. Camelot can be enabled for PDF table fallback, but complex merged-cell semantics are not inferred.
- Hierarchy reconstruction is conservative and marks uncertainty instead of making legal guesses.
- Layer 2 mock mode is deterministic for tests, but real answer quality depends on a grounded local or OpenAI-compatible model endpoint and better municipal evaluation data.
- SQLite can run Layer 2 tests and smoke loops, but PostgreSQL plus pgvector is the supported runtime for production retrieval quality.
- LLM audit mode is optional and requires an API key plus the `openai` dependency; deterministic audit remains local and available without network access.

## Architecture

See [docs/architecture.md](docs/architecture.md).

## Retrieval, MCP, And Local OpenAI Integration

The repo exposes the normalized source model through a read-only retrieval layer intended for downstream agents that answer bylaw questions using citation-grounded evidence.

All MCP-facing code lives under the top-level [`mcp/`](mcp/) directory:

- `mcp/bylaw_retrieval/retrieval`: model-agnostic retrieval service over documents, fragments, tables, and cross-references
- `mcp/bylaw_retrieval/server.py`: MCP server exposing retrieval tools and resources
- `mcp/bylaw_retrieval/api/app.py`: plain HTTP API exposing the same retrieval contract
- `mcp/bylaw_retrieval/openai_tools.py`: OpenAI-local tool schemas and dispatcher

Local service commands:

```bash
layer1-retrieval-api
layer1-mcp
python -m bylaw_retrieval.server --http
```

Optional extras:

```bash
python -m pip install -e ".[api,mcp,dev]"
```

OpenAI-specific note:

- The local OpenAI adapter is separate from the MCP server because OpenAI API and ChatGPT integrations may require product-specific tool wiring.
- The retrieval contract is shared, so upgrading from local adapter use to a hosted remote MCP server later should not require retrieval logic changes.
- See `examples/openai_local_retrieval_agent.py` for a minimal local Responses API tool-calling loop against the shared retrieval core.
