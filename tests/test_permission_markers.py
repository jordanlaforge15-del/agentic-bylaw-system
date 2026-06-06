"""Unit coverage for permission-marker codepoint normalization (ABS-277).

Verifies the classifier recovers the symbol-font ● (a PUA codepoint) as
``permitted``, decodes circled-number conditional markers into a footnote
ordinal, treats stripped/empty cells as ``not_permitted``, and surfaces an
unmapped PUA codepoint via a warning without crashing.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from layer1.semantic.permission_markers import (
    annotate_cell,
    annotate_permission_matrix_table,
    classify_permission_marker,
    is_permission_matrix_caption,
)

# The verified codepoints from the Phase-0 spike.
DOT = ""  # solid ● "permitted as-of-right" (symbol-font PUA)
SYMBOL_SPACE = ""  # symbol-font space / padding


def test_pua_dot_normalizes_to_permitted():
    # AC1: a cell whose text is the symbol-font dot -> permitted.
    assert classify_permission_marker(DOT) == {"permission_marker": "permitted"}


def test_circled_three_normalizes_to_conditional_with_footnote():
    # AC2: a cell whose text is "③" -> conditional, footnote 3.
    assert classify_permission_marker("③") == {
        "permission_marker": "conditional",
        "footnote": 3,
    }


def test_empty_and_symbol_space_normalize_to_not_permitted():
    # AC3: empty (or symbol-space-only) cell -> not_permitted.
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
    # AC4: an unmapped PUA codepoint is logged and treated as empty.
    unmapped = ""
    with caplog.at_level(logging.WARNING):
        result = classify_permission_marker(unmapped)
    assert result == {"permission_marker": "not_permitted"}
    assert any("U+F0AA" in rec.message for rec in caplog.records)


def test_high_circled_number_block():
    # ㉓ (U+3253) -> 23, exercising the second circled-number block.
    assert classify_permission_marker("㉓") == {
        "permission_marker": "conditional",
        "footnote": 23,
    }


def test_visible_filled_glyphs_are_permitted():
    for glyph in ("●", "•", "■"):  # ● • ■
        assert classify_permission_marker(glyph) == {"permission_marker": "permitted"}


def test_hollow_circle_is_not_permitted():
    assert classify_permission_marker("○") == {"permission_marker": "not_permitted"}


def test_is_permission_matrix_caption():
    assert is_permission_matrix_caption("Table 1A: Permitted uses by zone — Residential")
    assert is_permission_matrix_caption("table 1b permitted uses by zone")
    assert not is_permission_matrix_caption("Table 2: Parking standards")
    assert not is_permission_matrix_caption(None)
    assert not is_permission_matrix_caption("")


def test_annotate_cell_is_idempotent():
    cell = SimpleNamespace(text=DOT, metadata_json={"parser": "docling"})
    assert annotate_cell(cell) is True
    assert cell.metadata_json == {"parser": "docling", "permission_marker": "permitted"}
    # Second run: no change, raw metadata preserved.
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


def test_annotate_table_skips_headers_and_non_matrix():
    cells = [
        SimpleNamespace(row_index=0, col_index=0, text="Use", metadata_json={}),
        SimpleNamespace(row_index=0, col_index=1, text="DD", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=0, text="Dwelling", metadata_json={}),
        SimpleNamespace(row_index=1, col_index=1, text=DOT, metadata_json={}),
        SimpleNamespace(row_index=1, col_index=2, text="", metadata_json={}),
    ]
    matrix = SimpleNamespace(
        caption="Table 1A: Permitted uses by zone", cells=cells
    )
    changed = annotate_permission_matrix_table(matrix)
    assert changed == 2  # only the two value cells
    assert cells[0].metadata_json == {}  # header untouched
    assert cells[2].metadata_json == {}  # row label untouched
    assert cells[3].metadata_json == {"permission_marker": "permitted"}
    assert cells[4].metadata_json == {"permission_marker": "not_permitted"}

    non_matrix = SimpleNamespace(caption="Table 2: Parking", cells=cells)
    assert annotate_permission_matrix_table(non_matrix) == 0
