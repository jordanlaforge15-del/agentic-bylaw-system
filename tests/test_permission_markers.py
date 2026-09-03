"""Unit coverage for permission-marker normalization + matrix detection.

Codepoint classification (ABS-277): recovers the symbol-font ● (a PUA
codepoint ``U+F098``) as ``permitted``, decodes circled-number conditional
markers into a footnote ordinal, treats stripped/empty cells as
``not_permitted``, and surfaces an unmapped PUA codepoint via a warning.

Extraction failure (ABS-483): "we could not read this cell" is its own marker,
``unknown``, so it is never reported as the bylaw prohibiting a use. Two
producers — no text at all (``None``, i.e. no cell) and an unmapped private-use
glyph. A present-but-blank cell deliberately stays ``not_permitted``.

Detection (ABS-281): a table is a permission matrix iff it carries a
``permission_matrix`` semantic profile — NOT by caption. The real corpus stores
empty captions, so every test that exercises detection uses a **caption-absent**
table to mirror production and guard the regression that let the caption-based
gate ship green.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from layer1.semantic.conventions import (
    DEFAULT_CONVENTIONS,
    SECTION_INDEXED,
    SYMBOL_MATRIX,
    EnrichmentConventions,
)
from layer1.semantic.permission_markers import (
    PERMISSION_MATRIX_PROFILE,
    annotate_cell,
    annotate_permission_matrix_table,
    annotate_value_cells,
    classify_permission_marker,
    is_permission_matrix_table,
)

# The verified codepoints from the Phase-0 spike, constructed explicitly so the
# test never depends on invisible literals in the source file.
DOT = chr(0xF098)  # solid ● "permitted as-of-right" (symbol-font PUA)
SYMBOL_SPACE = chr(0xF020)  # symbol-font space / padding


def _profile(profile_type: str = PERMISSION_MATRIX_PROFILE) -> SimpleNamespace:
    return SimpleNamespace(profile_type=profile_type)


# --------------------------------------------------------------------------- #
# Codepoint classification
# --------------------------------------------------------------------------- #


def test_pua_dot_normalizes_to_permitted():
    assert classify_permission_marker(DOT) == {"permission_marker": "permitted"}


def test_circled_three_normalizes_to_conditional_with_footnote():
    assert classify_permission_marker("③") == {
        "permission_marker": "conditional",
        "footnote": 3,
        "footnotes": [3],
    }


def test_empty_and_symbol_space_normalize_to_not_permitted():
    # ABS-483 AC3: a cell that is PRESENT and blank stays not_permitted — an
    # empty cell is the symbol matrix's own way of spelling "not permitted".
    assert classify_permission_marker("") == {"permission_marker": "not_permitted"}
    assert classify_permission_marker(SYMBOL_SPACE) == {
        "permission_marker": "not_permitted"
    }
    assert classify_permission_marker("   ") == {"permission_marker": "not_permitted"}
    # Symbol-space padding around a real dot is stripped, leaving permitted.
    assert classify_permission_marker(SYMBOL_SPACE + DOT + SYMBOL_SPACE) == {
        "permission_marker": "permitted"
    }


def test_abs483_missing_text_is_unknown_not_not_permitted():
    """AC1: ``None`` means no cell was extracted at all — an extraction
    failure, which must NOT read as the bylaw prohibiting the use."""
    assert classify_permission_marker(None) == {"permission_marker": "unknown"}


def test_unmapped_pua_codepoint_warns_and_does_not_crash(caplog):
    """AC2: an unmapped private-use glyph is undecodable content, so the cell
    classifies as ``unknown`` — and the warning that tells us about the new
    symbol font is still emitted."""
    unmapped = chr(0xF0AA)
    with caplog.at_level(logging.WARNING):
        result = classify_permission_marker(unmapped)
    assert result == {"permission_marker": "unknown"}
    assert any("U+F0AA" in rec.message for rec in caplog.records)


def test_abs483_recognised_marker_beats_an_unmapped_glyph_in_the_same_cell():
    """A dot or circled number is real information; a stray undecodable glyph
    beside it is noise. Only a wholly undecodable cell degrades to unknown."""
    unmapped = chr(0xF0AA)
    assert classify_permission_marker(unmapped + DOT) == {
        "permission_marker": "permitted"
    }
    assert classify_permission_marker(unmapped + "③") == {
        "permission_marker": "conditional",
        "footnote": 3,
        "footnotes": [3],
    }


def test_abs483_ordinary_stray_text_is_still_not_permitted():
    """Only PRIVATE-USE glyphs signal an undecodable symbol font. A stray
    ordinary character (a footnote dagger, a hyphen) is not a marker and leaves
    the blank-cell convention intact."""
    assert classify_permission_marker("-") == {"permission_marker": "not_permitted"}


def test_abs483_annotate_cell_persists_unknown_for_an_unmapped_glyph():
    cell = SimpleNamespace(text=chr(0xF0AA), metadata_json={})
    assert annotate_cell(cell) is True
    assert cell.metadata_json == {"permission_marker": "unknown"}


def test_high_circled_number_block():
    assert classify_permission_marker("㉓") == {
        "permission_marker": "conditional",
        "footnote": 23,
        "footnotes": [23],
    }


def test_visible_filled_glyphs_are_permitted():
    for glyph in ("●", "•", "■"):
        assert classify_permission_marker(glyph) == {"permission_marker": "permitted"}


def test_hollow_circle_is_not_permitted():
    assert classify_permission_marker("○") == {"permission_marker": "not_permitted"}


# --------------------------------------------------------------------------- #
# ABS-284: profile-driven codepoint sets
# --------------------------------------------------------------------------- #


def test_abs284_profile_codepoints_classify_custom_glyph_as_permitted():
    """AC2: a bylaw declaring a different permitted glyph (a plain ``X``)
    classifies that glyph as ``permitted`` — without any change to the shared
    module's hardcoded constants."""
    conventions = EnrichmentConventions(
        permission_encoding=SYMBOL_MATRIX,
        permitted_codepoints=frozenset({ord("X")}),
    )
    assert classify_permission_marker("X", conventions) == {
        "permission_marker": "permitted"
    }
    # And the Regional-Centre dot is NOT permitted under that bylaw's set.
    # ABS-483: under this profile U+F098 is an *unmapped* private-use glyph —
    # content we cannot interpret — so it classifies as unknown rather than
    # asserting a prohibition the bylaw never wrote.
    assert classify_permission_marker(DOT, conventions) == {
        "permission_marker": "unknown"
    }


def test_abs284_default_conventions_reproduce_module_behavior():
    """FR3: passing the default conventions matches passing nothing."""
    for text in (DOT, "③", "", SYMBOL_SPACE, "●"):
        assert classify_permission_marker(
            text, DEFAULT_CONVENTIONS
        ) == classify_permission_marker(text)


def test_abs284_profile_ignored_codepoints_are_stripped():
    """A bylaw can declare its own padding glyph to ignore."""
    pad = chr(0xF0FF)
    conventions = EnrichmentConventions(
        permitted_codepoints=frozenset({ord("X")}),
        ignored_codepoints=frozenset({0xF0FF}),
    )
    assert classify_permission_marker(pad + "X" + pad, conventions) == {
        "permission_marker": "permitted"
    }


def test_abs284_annotate_value_cells_threads_conventions():
    """The annotate path honors the bylaw's glyph set end-to-end."""
    conventions = EnrichmentConventions(permitted_codepoints=frozenset({ord("X")}))
    cells = [
        SimpleNamespace(row_index=0, col_index=0, text="Use", metadata_json={}),
        SimpleNamespace(row_index=0, col_index=1, text="DD", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=0, text="Dwelling", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=1, text="X", metadata_json={}),
    ]
    changed = annotate_value_cells(cells, conventions=conventions)
    assert changed == 1
    assert cells[3].metadata_json == {"permission_marker": "permitted"}


def test_abs284_section_indexed_conventions_disable_symbol_matrix_detection():
    """A ``section_indexed`` bylaw doesn't detect symbol matrices."""
    assert EnrichmentConventions(permission_encoding=SECTION_INDEXED).detects_symbol_matrix is False
    assert EnrichmentConventions(permission_encoding=SYMBOL_MATRIX).detects_symbol_matrix is True
    assert DEFAULT_CONVENTIONS.detects_symbol_matrix is True


# --------------------------------------------------------------------------- #
# Profile-based matrix detection (ABS-281)
# --------------------------------------------------------------------------- #


def test_is_permission_matrix_table_detects_profile_without_caption():
    # AC1: a CAPTION-ABSENT table that carries a permission_matrix profile IS
    # detected — the exact case the old caption gate missed on real data.
    table = SimpleNamespace(caption=None, semantic_profiles=[_profile()])
    assert is_permission_matrix_table(table) is True


def test_is_permission_matrix_table_rejects_other_profiles_and_none():
    assert is_permission_matrix_table(
        SimpleNamespace(caption=None, semantic_profiles=[_profile("parking_matrix")])
    ) is False
    assert is_permission_matrix_table(
        SimpleNamespace(caption=None, semantic_profiles=[])
    ) is False
    # A matching caption is NOT sufficient without a profile.
    assert is_permission_matrix_table(
        SimpleNamespace(caption="Table 1A: Permitted uses by zone", semantic_profiles=[])
    ) is False


# --------------------------------------------------------------------------- #
# Cell annotation
# --------------------------------------------------------------------------- #


def test_annotate_cell_is_idempotent():
    cell = SimpleNamespace(text=DOT, metadata_json={"parser": "docling"})
    assert annotate_cell(cell) is True
    assert cell.metadata_json == {"parser": "docling", "permission_marker": "permitted"}
    assert annotate_cell(cell) is False
    assert cell.metadata_json == {"parser": "docling", "permission_marker": "permitted"}


def test_annotate_cell_drops_stale_footnote():
    cell = SimpleNamespace(
        text=DOT, metadata_json={"permission_marker": "conditional", "footnote": 9}
    )
    assert annotate_cell(cell) is True
    assert cell.metadata_json == {"permission_marker": "permitted"}


def test_annotate_cell_dry_run_does_not_mutate():
    cell = SimpleNamespace(text=DOT, metadata_json={})
    assert annotate_cell(cell, apply=False) is True
    assert cell.metadata_json == {}


def _grid() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(row_index=0, col_index=0, text="Use", metadata_json={}),
        SimpleNamespace(row_index=0, col_index=1, text="DD", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=0, text="Dwelling", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=1, text=DOT, metadata_json={}),
        SimpleNamespace(row_index=1, col_index=2, text="", metadata_json={}),
    ]


def test_annotate_value_cells_skips_headers():
    cells = _grid()
    changed = annotate_value_cells(cells)
    assert changed == 2  # only the two value cells
    assert cells[0].metadata_json == {}  # header untouched
    assert cells[2].metadata_json == {}  # row label untouched
    assert cells[3].metadata_json == {"permission_marker": "permitted"}
    assert cells[4].metadata_json == {"permission_marker": "not_permitted"}


def test_annotate_permission_matrix_table_gates_on_profile_not_caption():
    # AC2: annotate the value grid of a CAPTION-ABSENT profiled table.
    cells = _grid()
    matrix = SimpleNamespace(
        caption=None, cells=cells, semantic_profiles=[_profile()]
    )
    assert annotate_permission_matrix_table(matrix) == 2
    assert cells[3].metadata_json == {"permission_marker": "permitted"}
    assert cells[4].metadata_json == {"permission_marker": "not_permitted"}

    # A table with NO permission_matrix profile is a no-op even if its caption
    # would have matched the old gate.
    no_profile = SimpleNamespace(
        caption="Table 1A: Permitted uses by zone", cells=_grid(), semantic_profiles=[]
    )
    assert annotate_permission_matrix_table(no_profile) == 0
