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
* empty after stripping → ``not_permitted``

The codepoint map is data-driven and extensible: a future bylaw may use a
different PUA codepoint for ●. Any *unmapped* PUA codepoint encountered is
logged at WARNING level rather than silently dropped, so we notice new
symbol fonts instead of regressing to blank cells.

The result is persisted on the cell as ``metadata_json.permission_marker``
(and ``footnote`` when conditional) without clobbering the raw ``text``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-driven codepoint maps
# ---------------------------------------------------------------------------

# Codepoints that mean "permitted as-of-right". ``U+F098`` is the embedded
# symbol-font ● from the Regional Centre LUB (verified positionally against
# the rendered PDF). The visible filled glyphs are included so tables whose
# dots survived as real Unicode classify identically.
PERMITTED_CODEPOINTS: set[int] = {
    0xF098,  # PUA — solid ● "permitted as-of-right" (symbol font)
    0x25CF,  # ● BLACK CIRCLE
    0x2022,  # • BULLET
    0x25A0,  # ■ BLACK SQUARE
}

# Codepoints to strip before classifying. ``U+F020`` is the symbol font's
# space glyph (padding) — semantically empty.
IGNORED_CODEPOINTS: set[int] = {
    0xF020,  # PUA — symbol-font space (padding)
}


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


# SQL ``ILIKE`` pattern that matches a Table 1x permission-matrix caption.
# Kept in lockstep with :func:`is_permission_matrix_caption` and the retrieval
# layer's table lookup so ingest, backfill, and retrieval agree.
PERMISSION_MATRIX_CAPTION_LIKE = "Table 1%Permitted uses by zone%"


# Canonical marker values, exported so callers don't hard-code strings.
PERMITTED = "permitted"
CONDITIONAL = "conditional"
NOT_PERMITTED = "not_permitted"


def _is_private_use(codepoint: int) -> bool:
    """True for any Unicode Private Use Area codepoint (BMP + supplementary)."""
    return (
        0xE000 <= codepoint <= 0xF8FF
        or 0xF0000 <= codepoint <= 0xFFFFD
        or 0x100000 <= codepoint <= 0x10FFFD
    )


def classify_permission_marker(text: str | None) -> dict[str, Any]:
    """Classify a permission-matrix cell's raw text into a canonical marker.

    Returns one of:

    * ``{"permission_marker": "permitted"}``
    * ``{"permission_marker": "conditional", "footnote": N}``
    * ``{"permission_marker": "not_permitted"}``

    A conditional marker (circled number) takes precedence over a bare dot in
    the same cell; the footnote ordinal is the first circled number found.
    Unmapped PUA codepoints are logged and treated as empty so the pass never
    crashes on a new symbol font.
    """
    permitted = False
    footnote: int | None = None

    for char in text or "":
        codepoint = ord(char)
        if codepoint in IGNORED_CODEPOINTS or char.isspace():
            continue
        if codepoint in PERMITTED_CODEPOINTS:
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
                "cell; treating as empty. Extend PERMITTED_CODEPOINTS / "
                "IGNORED_CODEPOINTS in layer1.semantic.permission_markers if "
                "this is a real marker.",
                codepoint,
            )
            continue
        # Any other ordinary character (stray letter/punctuation) is not a
        # recognised marker — ignore it for classification purposes.

    if footnote is not None:
        return {"permission_marker": CONDITIONAL, "footnote": footnote}
    if permitted:
        return {"permission_marker": PERMITTED}
    return {"permission_marker": NOT_PERMITTED}


def is_permission_matrix_caption(caption: str | None) -> bool:
    """True when a table caption looks like a Table 1x permission matrix.

    Mirrors the SQL pattern the retrieval layer uses to find these tables
    (``Table 1%Permitted uses by zone%``) so ingest, backfill, and retrieval
    all agree on what counts as a permission matrix.
    """
    if not caption:
        return False
    lowered = caption.lower()
    return lowered.startswith("table 1") and "permitted uses by zone" in lowered


def annotate_cell(cell: Any, *, apply: bool = True) -> bool:
    """Set ``metadata_json.permission_marker`` on a SourceTableCell-like object.

    Idempotent: computes the desired metadata and only writes when it differs
    from what's already stored, so re-running yields identical values with no
    drift. Returns ``True`` when the cell would change (and did, unless
    ``apply=False`` for dry-run accounting). Leaves the raw ``text`` untouched.
    """
    result = classify_permission_marker(getattr(cell, "text", None))
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


def annotate_permission_matrix_table(table: Any, *, apply: bool = True) -> int:
    """Annotate every value cell of a permission-matrix table.

    No-op for tables whose caption isn't a permission matrix. Header cells
    (row 0 and the row-label column 0) are skipped — the marker semantics
    only apply to the value grid. Returns the number of cells changed.
    """
    if not is_permission_matrix_caption(getattr(table, "caption", None)):
        return 0
    changed = 0
    for cell in table.cells:
        if cell.row_index == 0 or cell.col_index == 0:
            continue
        if annotate_cell(cell, apply=apply):
            changed += 1
    return changed
