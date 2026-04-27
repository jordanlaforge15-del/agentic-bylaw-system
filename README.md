# Layer 1 + Layer 2 Bylaw Assistant

Local-only source normalization and retrieval-first answering for municipal land-use bylaws. Layer 1 ingests official source files into auditable source tables. Layer 2 retrieves from those Layer 1 artifacts, builds grounded prompts, generates answers plus reusable claims, and persists feedback and trace logs.

Repo specifics: this is a Python 3.11+ package using SQLAlchemy, Alembic, Pydantic, Typer, PyMuPDF/pdfplumber fallbacks, and optional Docling/Camelot/PaddleOCR parser integrations.

Layer 1 integration paths used by Layer 2:

- Layer 1 ORM base and source tables: `/workspace/src/layer1/db/base.py`
- Layer 1 session utilities: `/workspace/src/layer1/db/session.py`
- Layer 1 settings using `DATABASE_URL`: `/workspace/src/layer1/config.py`
- Alembic metadata entrypoint: `/workspace/alembic/env.py`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
```

`docker compose` now uses `pgvector/pgvector:pg16` so Layer 2 can use PostgreSQL full-text search and pgvector in the same database. SQLite remains useful for fast unit and smoke tests, but full Layer 2 behavior should be validated on PostgreSQL.

Install heavier parser integrations when needed:

```bash
python -m pip install -e ".[parsers]"
```

## Layer 1 CLI

```bash
layer1 init-db
layer1 ingest tests/fixtures/synthetic_bylaw.txt --municipality Sampleton --bylaw-name "Synthetic Zoning Bylaw"
layer1 ingest-dir ./bylaws --ocr --debug
layer1 validate 1
layer1 show-summary 1
layer1 export-json 1 --out examples/synthetic_bylaw_export.json
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

## Layer 2 Runtime Configuration

Layer 2 reads `DATABASE_URL` through the shared Layer 1 settings path and supports additional environment variables for models and retrieval behavior:

- `LAYER2_LLM_BASE_URL`
- `LAYER2_LLM_API_KEY`
- `LAYER2_LLM_MODEL`
- `LAYER2_EMBEDDING_BASE_URL`
- `LAYER2_EMBEDDING_API_KEY`
- `LAYER2_EMBEDDING_MODEL`
- `LAYER2_EMBEDDING_DIMENSIONS`
- `LAYER2_PROMPT_VERSION`
- `LAYER2_RETRIEVAL_VERSION`
- `LAYER2_TOKEN_BUDGET`
- `LAYER2_TOP_K`
- `LAYER2_MAX_CACHED_CLAIMS`

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

## Known Limitations

- OCR is exposed as a flag, but production PaddleOCR image extraction needs tuning against scanned PDFs.
- Docling output is used when installed; PyMuPDF supplies the reliable geometry fallback.
- Table extraction is intentionally sparse for text-heavy bylaws. Camelot can be enabled for PDF table fallback, but complex merged-cell semantics are not inferred.
- Hierarchy reconstruction is conservative and marks uncertainty instead of making legal guesses.
- Layer 2 mock mode is deterministic for tests, but real answer quality depends on a grounded local or OpenAI-compatible model endpoint and better municipal evaluation data.
- SQLite can run Layer 2 tests and smoke loops, but PostgreSQL plus pgvector is the supported runtime for production retrieval quality.

## Architecture

See [docs/architecture.md](docs/architecture.md).
