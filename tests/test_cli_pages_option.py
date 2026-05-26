"""Tests for _parse_pages_option in layer1 CLI."""

import pytest
import typer

from layer1.cli import _parse_pages_option


def test_none_input():
    assert _parse_pages_option(None) is None


def test_empty_string():
    assert _parse_pages_option("") is None


def test_single_page():
    assert _parse_pages_option("5") == [5]


def test_comma_list():
    assert _parse_pages_option("5,12,26") == [5, 12, 26]


def test_single_range():
    assert _parse_pages_option("10-12") == [10, 11, 12]


def test_mixed_pages_and_ranges():
    assert _parse_pages_option("10-12,20,30-32") == [10, 11, 12, 20, 30, 31, 32]


def test_range_at_end_with_single():
    assert _parse_pages_option("5,10-12,26") == [5, 10, 11, 12, 26]


def test_deduplication_preserves_order():
    # page 11 appears via range 10-12, then explicitly — should not appear twice
    result = _parse_pages_option("10-12,11")
    assert result == [10, 11, 12]
    assert result.count(11) == 1


def test_deduplication_single_then_range():
    result = _parse_pages_option("11,10-12")
    assert result == [11, 10, 12]
    assert result.count(11) == 1


def test_whitespace_tolerance():
    assert _parse_pages_option(" 5 , 10 - 12 , 26 ") == [5, 10, 11, 12, 26]


def test_reversed_range_raises():
    with pytest.raises(typer.BadParameter, match="end before start"):
        _parse_pages_option("25-10")


def test_malformed_range_raises():
    with pytest.raises(typer.BadParameter):
        _parse_pages_option("abc-def")


def test_invalid_page_raises():
    with pytest.raises(typer.BadParameter, match="Invalid page number"):
        _parse_pages_option("foo")


def test_single_value_range():
    # A-A is a valid degenerate range of one page
    assert _parse_pages_option("5-5") == [5]
