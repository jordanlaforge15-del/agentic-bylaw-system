# Layer 1 Bylaw Ingestion

Local-only source normalization for municipal land-use bylaws. The pipeline ingests official source files and writes an addressable, auditable model to PostgreSQL.

Repo specifics: this is a Python 3.11+ package using SQLAlchemy, Alembic, Pydantic, Typer, Docling as the primary PDF parser, and PyMuPDF/pdfplumber fallbacks with optional Camelot/PaddleOCR integrations.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
```

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

Parsing profiles are supported for region/document-family specific rules:

```bash
layer1 ingest tests/fixtures/synthetic_bylaw.txt --profile default
layer1 ingest "PEN 223_Effective_June 17 2017.pdf" --profile halifax
```

Available profiles:

- `default`: conservative generic municipal-bylaw heuristics
- `halifax`: enables the current Halifax-style compound numbering and definition/list heuristics

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

## Tests

```bash
pytest
```

Tests cover citation parsing, hierarchy reconstruction, cross-reference detection, boilerplate detection, SQLite-backed integration ingest, and preservation of uncertain fragments.

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
- LLM audit mode is optional and requires an API key plus the `openai` dependency; deterministic audit remains local and available without network access.

## Architecture

See [docs/architecture.md](docs/architecture.md).
