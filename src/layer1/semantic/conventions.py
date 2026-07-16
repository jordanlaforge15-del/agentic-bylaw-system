"""Per-bylaw enrichment classification conventions (ABS-284).

Parsing (Layer 1) is already profile-driven: ``ParsingProfile`` +
``profile_for_path`` select zone/use vocabulary per bylaw. Enrichment
*classification* was not — ``_classify_table`` and ``classify_permission_marker``
hardcoded Regional-Centre conventions (the ``section_label_density >= 0.5``
permission-matrix branch, the ``U+F098`` permitted glyph) in shared modules, so
every new bylaw's quirks landed as another ABS-XXX special-case (ABS-104/105/106/
277/283).

This module captures those conventions as *data* — an
:class:`EnrichmentConventions` value carried on the active profile/overlay — so a
new bylaw becomes a manifest/profile entry rather than surgery on shared code.

The defaults reproduce today's Regional-Centre-flavoured behavior exactly, so a
caller that supplies no profile (or a profile that declares no enrichment fields)
is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Permission-encoding strategies.
# ---------------------------------------------------------------------------

# Regional Centre LUB: permitted uses live in a use×zone matrix whose cells carry
# symbol-font ● dots / circled-number conditionals (Table 1A).
SYMBOL_MATRIX = "symbol_matrix"
# Halifax Mainland LUB: permitted uses are enumerated as prose under each zone's
# section ("The following uses shall be permitted in any ICH Zone: ..."), NOT in a
# symbol-dot matrix. Tables that *look* like section×zone grids are amendment /
# section-history tables, not permission matrices — so symbol-matrix table
# detection is disabled for this encoding and permissions come from prose
# extraction (``_extract_mainland_permitted_uses``).
SECTION_INDEXED = "section_indexed"

VALID_ENCODINGS = frozenset({SYMBOL_MATRIX, SECTION_INDEXED})


# ---------------------------------------------------------------------------
# Default codepoint maps (Regional Centre).
# ---------------------------------------------------------------------------

# Codepoints meaning "permitted as-of-right". ``U+F098`` is the embedded
# symbol-font ● from the Regional Centre LUB (verified positionally against the
# rendered PDF). The visible filled glyphs are included so tables whose dots
# survived as real Unicode classify identically.
DEFAULT_PERMITTED_CODEPOINTS: frozenset[int] = frozenset(
    {
        0xF098,  # PUA — solid ● "permitted as-of-right" (symbol font)
        0x25CF,  # ● BLACK CIRCLE
        0x2022,  # • BULLET
        0x25A0,  # ■ BLACK SQUARE
    }
)

# Codepoints to strip before classifying. ``U+F020`` is the symbol font's space
# glyph (padding) — semantically empty.
DEFAULT_IGNORED_CODEPOINTS: frozenset[int] = frozenset(
    {
        0xF020,  # PUA — symbol-font space (padding)
    }
)


@dataclass(frozen=True)
class EnrichmentConventions:
    """Bylaw-specific knobs that drive enrichment classification.

    * ``permission_encoding`` — selects table-detection + marker strategy
      (:data:`SYMBOL_MATRIX` vs :data:`SECTION_INDEXED`).
    * ``permitted_codepoints`` / ``ignored_codepoints`` — per-bylaw glyph sets,
      replacing the hardcoded module constants in
      ``layer1.semantic.permission_markers``.
    * ``disqualify_amendment_tables`` — when ``True`` (default), an
      amendment/section-history table (amendment-date or council-case columns)
      can never classify as ``permission_matrix``. ABS-283.

    Frozen + hashable so it can ride on a frozen ``ParsingProfile`` /
    ``ProfileOverlay`` and live in a ``contextvars.ContextVar``.
    """

    permission_encoding: str = SYMBOL_MATRIX
    permitted_codepoints: frozenset[int] = DEFAULT_PERMITTED_CODEPOINTS
    ignored_codepoints: frozenset[int] = DEFAULT_IGNORED_CODEPOINTS
    disqualify_amendment_tables: bool = True

    @property
    def detects_symbol_matrix(self) -> bool:
        """True when this bylaw encodes permissions in a symbol-dot matrix.

        Under :data:`SECTION_INDEXED`, symbol-matrix table detection is off:
        the bylaw's permissions live in section prose, so a section×zone-shaped
        table is treated as a non-permission table rather than false-positived
        as a ``permission_matrix``.
        """
        return self.permission_encoding == SYMBOL_MATRIX


# The historical default — Regional-Centre symbol-matrix behavior. Used whenever
# no profile/overlay supplies enrichment conventions, so existing call sites are
# byte-for-byte unchanged.
DEFAULT_CONVENTIONS = EnrichmentConventions()
