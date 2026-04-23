# Layer 1 Bylaw Ingestion

Local-only source normalization for municipal land-use bylaws. The pipeline ingests official source files and writes an addressable, auditable model to PostgreSQL.

Repo specifics: this is a Python 3.11+ package using SQLAlchemy, Alembic, Pydantic, Typer, PyMuPDF/pdfplumber fallbacks, and optional Docling/Camelot/PaddleOCR parser integrations.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
```

Install heavier parser integrations when needed:

```bash
python -m pip install -e ".[parsers]"
```

## CLI

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

## Known Limitations

- OCR is exposed as a flag, but production PaddleOCR image extraction needs tuning against scanned PDFs.
- Docling output is used when installed; PyMuPDF supplies the reliable geometry fallback.
- Table extraction is intentionally sparse for text-heavy bylaws. Camelot can be enabled for PDF table fallback, but complex merged-cell semantics are not inferred.
- Hierarchy reconstruction is conservative and marks uncertainty instead of making legal guesses.

## Architecture

See [docs/architecture.md](docs/architecture.md).
