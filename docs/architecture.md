# Layer 1 Architecture

Layer 1 is a source-normalization pipeline. It stores provenance, layout blocks, a conservative fragment tree, tables, cross-references, and validation results. It does not extract legal rules or infer zoning permissions.

## Flow

1. `document ingest`: hash the local file, detect MIME type, and create `document` plus `ingestion_run` records.
2. `parse source`: use Docling for PDFs when installed, collect PDF geometry with PyMuPDF fallback, and use a deterministic text parser for plain text and tests.
3. `page block extraction`: classify blocks as headings, paragraphs, list items, footnotes, table regions, headers, footers, or unknown.
4. `hierarchy reconstruction`: infer a fragment tree from `Part`, `Schedule`, numeric section labels, and list markers. Ambiguous content is preserved as `parse_status='uncertain'`.
5. `table handling`: table regions are stored as `source_table` and `source_table_cell` records when detected by simple text fallback or optional Camelot.
6. `cross-reference detection`: deterministic regexes capture municipal references such as `section 5.4`, `subsection 8.2.1`, and `Schedule B`.
7. `validation`: checks block accounting, tree validity, page ranges, citation uniqueness, table linkage, and cross-reference consistency.

## Parser Tradeoffs

Docling, PaddleOCR, and Camelot are intentionally optional runtime integrations because they are heavy and installation-sensitive. The code attempts Docling first for PDFs, then falls back to PyMuPDF so a local machine can still ingest text-layer PDFs. OCR is surfaced as a CLI flag and warning path, but production OCR tuning should be added against real scanned municipal PDFs.

The hierarchy builder is conservative: it favors traceability over aggressive merging. When a heading or paragraph cannot be confidently attached, it is preserved with explicit uncertainty rather than discarded.

## Future Layers

Layer 2 can consume `source_fragment`, `source_table`, and `cross_reference` records to classify legal concepts or extract zoning rules. It should treat Layer 1 citations and source block IDs as immutable lineage.
