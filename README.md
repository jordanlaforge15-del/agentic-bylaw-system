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

## Tests

```bash
pytest
```

Tests cover citation parsing, hierarchy reconstruction, cross-reference detection, boilerplate detection, SQLite-backed integration ingest, and preservation of uncertain fragments.

Layer 2 tests add query understanding, retrieval merge and expansion, prompt assembly, claim parsing, end-to-end answer persistence, feedback persistence, and eval harness smoke coverage.

## Known Limitations

- OCR is exposed as a flag, but production PaddleOCR image extraction needs tuning against scanned PDFs.
- Docling output is used when installed; PyMuPDF supplies the reliable geometry fallback.
- Table extraction is intentionally sparse for text-heavy bylaws. Camelot can be enabled for PDF table fallback, but complex merged-cell semantics are not inferred.
- Hierarchy reconstruction is conservative and marks uncertainty instead of making legal guesses.
- Layer 2 mock mode is deterministic for tests, but real answer quality depends on a grounded local or OpenAI-compatible model endpoint and better municipal evaluation data.
- SQLite can run Layer 2 tests and smoke loops, but PostgreSQL plus pgvector is the supported runtime for production retrieval quality.

## Architecture

See [docs/architecture.md](docs/architecture.md).
