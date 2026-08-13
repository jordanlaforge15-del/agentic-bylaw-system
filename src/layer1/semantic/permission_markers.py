"""Recover permission-matrix markers from symbol-font codepoints.

Background (ABS-277)
--------------------
The authoritative permission marker in the Regional Centre LUB's Table 1A
permission matrix — the solid ● "permitted as-of-right" dot — is drawn from
the source PDF's *embedded symbol font*. Its glyph lands on a Private Use
Area (PUA) codepoint (``U+F098``) that carries no semantic Unicode meaning,
so every downstream reader (retrieval, candidate text, the model) treats the
cell as **blank**. A Phase-0 spike confirmed the dot is not lost — it is
sitting in ``source_table_cell.text`` as ``U+F098`` — so this is pure
codepoint normalization, no OCR / image work required.

What this module does
---------------------
Classifies a permission-matrix cell's raw ``text`` into a canonical marker:

* ``U+F098`` (and the visible filled glyphs ●/•/■) → ``permitted``
* circled numbers ①..⑮.. (``U+2460..U+2473`` / ``U+3251..U+325F``) →
  ``conditional`` plus the footnote ordinal ``N``
* ``U+F020`` (symbol-font space, padding) → stripped / ignored
* empty after stripping → ``not_permitted`` (a blank cell in a symbol matrix
  IS the bylaw's "not permitted" convention)
* nothing extracted at all (``text is None`` — no cell) or an *unmapped*
  private-use glyph → ``unknown``

The codepoint map is data-driven and extensible: a future bylaw may use a
different PUA codepoint for ●. Any *unmapped* PUA codepoint encountered is
logged at WARNING level rather than silently dropped, so we notice new
symbol fonts instead of regressing to blank cells.

Extraction failure is not prohibition (ABS-483)
-----------------------------------------------
Before ABS-483 the vocabulary was exactly three values, so a cell we could
*not read* — a row the table parser dropped, or a glyph from a symbol font we
have no mapping for — collapsed into ``not_permitted``: "we failed to extract
this" and "the legislature prohibited this" became the same answer. ``UNKNOWN``
splits them. The one case that deliberately stays ``not_permitted`` is a cell
that is *present and blank* in a symbol matrix: an empty cell is how these
bylaws spell "not permitted", so reading it as unknown would discard real
information.

The result is persisted on the cell as ``metadata_json.permission_marker``
(and ``footnote`` when conditional) without clobbering the raw ``text``.
"""
from __future__ import annotations

import logging
from typing import Any

from layer1.semantic.conventions import (
    DEFAULT_CONVENTIONS,
    DEFAULT_IGNORED_CODEPOINTS,
    DEFAULT_PERMITTED_CODEPOINTS,
    EnrichmentConventions,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-driven codepoint maps
# ---------------------------------------------------------------------------

# Module-level defaults, retained for backward compatibility with any caller
# that imports these names directly. The canonical, per-bylaw codepoint sets now
# live on :class:`~layer1.semantic.conventions.EnrichmentConventions`; these
# mirror the Regional-Centre default so the no-profile path is unchanged
# (ABS-284). ``U+F098`` is the embedded symbol-font ● from the Regional Centre
# LUB; the visible filled glyphs are included so tables whose dots survived as
# real Unicode classify identically.
PERMITTED_CODEPOINTS: set[int] = set(DEFAULT_PERMITTED_CODEPOINTS)

# Codepoints to strip before classifying. ``U+F020`` is the symbol font's
# space glyph (padding) — semantically empty.
IGNORED_CODEPOINTS: set[int] = set(DEFAULT_IGNORED_CODEPOINTS)


def _build_circled_numbers() -> dict[int, int]:
    """Map circled-number codepoints to their integer ordinal.

    * ``U+2460``..``U+2473`` → 1..20 (①..⑳)
    * ``U+3251``..``U+325F`` → 21..35 (㉑..㉟)

    The second block is the natural continuation of the Unicode circled-number
    run; the existing footnote matcher in the retrieval layer already scans up
    to ㉟ (``U+325F``), so we cover the same range here.
    """
    mapping: dict[int, int] = {}
    for index in range(20):  # U+2460..U+2473 -> 1..20
        mapping[0x2460 + index] = index + 1
    for index in range(15):  # U+3251..U+325F -> 21..35
        mapping[0x3251 + index] = 21 + index
    return mapping


CIRCLED_NUMBERS: dict[int, int] = _build_circled_numbers()

# Reverse map: footnote ordinal (1..35) -> circled-number codepoint. Lets a
# caller that knows the ordinal (e.g. a resolved conditional cell carrying
# ``footnote=3``) reconstruct the ① glyph to find the matching footnote
# fragment text. Built from the same source as CIRCLED_NUMBERS so the two
# never drift.
ORDINAL_TO_CODEPOINT: dict[int, int] = {
    ordinal: codepoint for codepoint, ordinal in CIRCLED_NUMBERS.items()
}


def ordinal_to_circled(ordinal: int) -> str | None:
    """Return the circled-number glyph for a footnote ordinal, or ``None``.

    Inverse of the classification in :func:`classify_permission_marker`:
    ``3 -> "③"``. Out-of-range ordinals (outside 1..35) return ``None`` so a
    caller can fall back to a plain-number search rather than crashing.
    """
    codepoint = ORDINAL_TO_CODEPOINT.get(ordinal)
    return chr(codepoint) if codepoint is not None else None


# The semantic-profile type that marks a table as the permitted-uses matrix.
# Detection keys off this (written by enrichment's ``_classify_table`` into
# ``table_semantic_profile.profile_type``), NOT the caption. Real-corpus
# captions are empty on all 1,147 tables, so the earlier caption gate matched
# zero tables and silently no-op'd the whole feature (ABS-281). The structural
# classifier already populates this for every enriched bylaw.
PERMISSION_MATRIX_PROFILE = "permission_matrix"


# Canonical marker values, exported so callers don't hard-code strings.
PERMITTED = "permitted"
CONDITIONAL = "conditional"
NOT_PERMITTED = "not_permitted"
# ABS-483: "we could not read this cell" — an extraction failure, distinct from
# the bylaw's own "not permitted". Never inferred from a blank cell.
UNKNOWN = "unknown"


def _is_private_use(codepoint: int) -> bool:
    """True for any Unicode Private Use Area codepoint (BMP + supplementary)."""
    return (
        0xE000 <= codepoint <= 0xF8FF
        or 0xF0000 <= codepoint <= 0xFFFFD
        or 0x100000 <= codepoint <= 0x10FFFD
    )


def classify_permission_marker(
    text: str | None,
    conventions: EnrichmentConventions | None = None,
) -> dict[str, Any]:
    """Classify a permission-matrix cell's raw text into a canonical marker.

    Returns one of:

    * ``{"permission_marker": "permitted"}``
    * ``{"permission_marker": "conditional", "footnote": N}``
    * ``{"permission_marker": "not_permitted"}``
    * ``{"permission_marker": "unknown"}``

    A conditional marker (circled number) takes precedence over a bare dot in
    the same cell; the footnote ordinal is the first circled number found.
    Unmapped PUA codepoints are logged and never crash the pass on a new symbol
    font.

    ABS-483 — the two extraction-failure paths yield ``unknown`` rather than
    silently reading as prohibition:

    * ``text is None`` — nothing was extracted at all. ``SourceTableCell.text``
      is NOT NULL, so ``None`` only ever reaches here from a caller that had no
      cell to read (a row the parser dropped from the grid).
    * an unmapped private-use glyph and no recognised marker beside it — the
      cell holds *something* from a symbol font we cannot decode.

    A recognised marker wins over an unmapped glyph in the same cell: the dot
    or circled number is real information, and the stray glyph is noise. Only a
    cell whose entire content is undecodable degrades to ``unknown``.
    An *empty* string is NOT unknown — a blank cell is the symbol matrix's own
    "not permitted" convention.

    ABS-284: the permitted / ignored glyph sets come from ``conventions`` (the
    active bylaw's :class:`~layer1.semantic.conventions.EnrichmentConventions`).
    Passing ``None`` selects the Regional-Centre defaults, so existing call
    sites are unchanged. A bylaw declaring a different permitted glyph (e.g. a
    plain ``"X"``) classifies that glyph as ``permitted`` without any change to
    this shared module.
    """
    conventions = conventions or DEFAULT_CONVENTIONS
    permitted_codepoints = conventions.permitted_codepoints
    ignored_codepoints = conventions.ignored_codepoints
    permitted = False
    footnote: int | None = None
    undecodable = False

    if text is None:
        # No cell / no text extracted — an extraction failure, not a verdict.
        return {"permission_marker": UNKNOWN}

    for char in text:
        codepoint = ord(char)
        if codepoint in ignored_codepoints or char.isspace():
            continue
        if codepoint in permitted_codepoints:
            permitted = True
            continue
        ordinal = CIRCLED_NUMBERS.get(codepoint)
        if ordinal is not None:
            if footnote is None:
                footnote = ordinal
            continue
        if _is_private_use(codepoint):
            logger.warning(
                "Unmapped private-use codepoint U+%04X in permission-matrix "
                "cell; classifying the cell as 'unknown'. Extend "
                "PERMITTED_CODEPOINTS / IGNORED_CODEPOINTS in "
                "layer1.semantic.permission_markers if this is a real marker.",
                codepoint,
            )
            undecodable = True
            continue
        # Any other ordinary character (stray letter/punctuation) is not a
        # recognised marker — ignore it for classification purposes.

    if footnote is not None:
        return {"permission_marker": CONDITIONAL, "footnote": footnote}
    if permitted:
        return {"permission_marker": PERMITTED}
    if undecodable:
        # Symbol-font content we can't map: don't pass it off as prohibition.
        return {"permission_marker": UNKNOWN}
    return {"permission_marker": NOT_PERMITTED}


def is_permission_matrix_table(table: Any) -> bool:
    """True when ``table`` carries a ``permission_matrix`` semantic profile.

    This is the single source of truth for "is this a permitted-uses matrix",
    shared by ingest/enrichment annotation, the backfill, and the retrieval
    layer. It reads the structural classification that enrichment's
    ``_classify_table`` writes to ``table_semantic_profile`` — captions are NOT
    consulted because the real corpus stores none (ABS-281).

    Accepts any object exposing a ``semantic_profiles`` iterable of objects with
    a ``profile_type`` attribute (the ORM ``SourceTable.semantic_profiles``
    relationship, or a lightweight stand-in in tests).
    """
    profiles = getattr(table, "semantic_profiles", None) or []
    return any(
        getattr(profile, "profile_type", None) == PERMISSION_MATRIX_PROFILE
        for profile in profiles
    )


def annotate_cell(
    cell: Any,
    *,
    apply: bool = True,
    conventions: EnrichmentConventions | None = None,
) -> bool:
    """Set ``metadata_json.permission_marker`` on a SourceTableCell-like object.

    Idempotent: computes the desired metadata and only writes when it differs
    from what's already stored, so re-running yields identical values with no
    drift. Returns ``True`` when the cell would change (and did, unless
    ``apply=False`` for dry-run accounting). Leaves the raw ``text`` untouched.
    ``conventions`` selects the per-bylaw glyph set (ABS-284); ``None`` keeps the
    Regional-Centre defaults.
    """
    result = classify_permission_marker(getattr(cell, "text", None), conventions)
    existing = dict(getattr(cell, "metadata_json", None) or {})
    desired = dict(existing)
    desired["permission_marker"] = result["permission_marker"]
    if "footnote" in result:
        desired["footnote"] = result["footnote"]
    else:
        # Drop any stale footnote so a cell re-classified away from
        # "conditional" doesn't keep an orphaned ordinal.
        desired.pop("footnote", None)

    if desired == existing:
        return False
    if apply:
        cell.metadata_json = desired
    return True


def annotate_value_cells(
    cells: Any,
    *,
    apply: bool = True,
    conventions: EnrichmentConventions | None = None,
) -> int:
    """Annotate the value grid of an already-known permission matrix.

    Header cells (row 0 and the row-label column 0) carry no marker semantics
    and are skipped. Returns the number of cells changed. Callers that have
    already established the table is a permission matrix (e.g. the enrichment
    classifier, which only invokes this in its ``permission_matrix`` branch)
    use this directly; :func:`annotate_permission_matrix_table` is the
    predicate-gated wrapper for callers that haven't. ``conventions`` selects the
    per-bylaw glyph set (ABS-284); ``None`` keeps the Regional-Centre defaults.
    """
    changed = 0
    for cell in cells:
        if cell.row_index == 0 or cell.col_index == 0:
            continue
        if annotate_cell(cell, apply=apply, conventions=conventions):
            changed += 1
    return changed


def annotate_permission_matrix_table(
    table: Any,
    *,
    apply: bool = True,
    conventions: EnrichmentConventions | None = None,
) -> int:
    """Annotate every value cell of a permission-matrix table.

    No-op for tables that do not carry a ``permission_matrix`` semantic profile
    (see :func:`is_permission_matrix_table`). Returns the number of cells
    changed. ``conventions`` selects the per-bylaw glyph set (ABS-284).
    """
    if not is_permission_matrix_table(table):
        return 0
    return annotate_value_cells(table.cells, apply=apply, conventions=conventions)
