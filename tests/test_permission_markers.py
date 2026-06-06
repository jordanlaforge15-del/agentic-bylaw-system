"""Unit coverage for permission-marker normalization + matrix detection.

Codepoint classification (ABS-277): recovers the symbol-font ● (a PUA
codepoint ``U+F098``) as ``permitted``, decodes circled-number conditional
markers into a footnote ordinal, treats stripped/empty cells as
``not_permitted``, and surfaces an unmapped PUA codepoint via a warning.

Detection (ABS-281): a table is a permission matrix iff it carries a
``permission_matrix`` semantic profile — NOT by caption. The real corpus stores
empty captions, so every test that exercises detection uses a **caption-absent**
table to mirror production and guard the regression that let the caption-based
gate ship green.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

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
    }


def test_empty_and_symbol_space_normalize_to_not_permitted():
    assert classify_permission_marker("") == {"permission_marker": "not_permitted"}
    assert classify_permission_marker(None) == {"permission_marker": "not_permitted"}
    assert classify_permission_marker(SYMBOL_SPACE) == {
        "permission_marker": "not_permitted"
    }
    # Symbol-space padding around a real dot is stripped, leaving permitted.
    assert classify_permission_marker(SYMBOL_SPACE + DOT + SYMBOL_SPACE) == {
        "permission_marker": "permitted"
    }


def test_unmapped_pua_codepoint_warns_and_does_not_crash(caplog):
    unmapped = chr(0xF0AA)
    with caplog.at_level(logging.WARNING):
        result = classify_permission_marker(unmapped)
    assert result == {"permission_marker": "not_permitted"}
    assert any("U+F0AA" in rec.message for rec in caplog.records)


def test_high_circled_number_block():
    assert classify_permission_marker("㉓") == {
        "permission_marker": "conditional",
        "footnote": 23,
    }


def test_visible_filled_glyphs_are_permitted():
    for glyph in ("●", "•", "■"):
        assert classify_permission_marker(glyph) == {"permission_marker": "permitted"}


def test_hollow_circle_is_not_permitted():
    assert classify_permission_marker("○") == {"permission_marker": "not_permitted"}


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
