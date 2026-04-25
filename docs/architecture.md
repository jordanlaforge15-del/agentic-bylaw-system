# Layer 1 Architecture

Layer 1 is a source-normalization pipeline. It stores provenance, layout blocks, a conservative fragment tree, tables, cross-references, and validation results. It does not extract legal rules or infer zoning permissions.

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

Layer 2 can consume `source_fragment`, `source_table`, and `cross_reference` records to classify legal concepts or extract zoning rules. It should treat Layer 1 citations and source block IDs as immutable lineage.
