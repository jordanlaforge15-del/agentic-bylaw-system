from datetime import date

import pytest

from layer1.datasets.canonical import CoercionError, coerce_value, is_canonical_field


def test_canonical_field_membership():
    assert is_canonical_field("max_height_m")
    assert is_canonical_field("display_label")
    assert not is_canonical_field("HEIGHT")
    assert not is_canonical_field("max_height_ft")


def test_coerce_float_from_string_and_int():
    assert coerce_value("35", "float") == 35.0
    assert coerce_value(35, "float") == 35.0
    assert coerce_value(35.5, "float") == 35.5


def test_coerce_int_truncates_floats_only_when_safe():
    assert coerce_value("35", "int") == 35
    with pytest.raises(CoercionError):
        coerce_value("not an int", "int")


def test_coerce_string_passthrough():
    assert coerce_value(42, "string") == "42"
    assert coerce_value("abc", "string") == "abc"


def test_coerce_bool_handles_textual_values():
    assert coerce_value("true", "bool") is True
    assert coerce_value("FALSE", "bool") is False
    assert coerce_value(1, "bool") is True
    with pytest.raises(CoercionError):
        coerce_value("maybe", "bool")


def test_coerce_iso_date():
    assert coerce_value("2020-06-10", "date") == "2020-06-10"
    assert coerce_value(date(2020, 6, 10), "date") == "2020-06-10"
    with pytest.raises(CoercionError):
        coerce_value("not a date", "date")


def test_coerce_rfc2822_date_matches_halifax_format():
    # Halifax SDATE format from the real height precincts dataset.
    assert coerce_value("Sat, 03 Nov 2018 00:00:00 GMT", "rfc2822_date") == "2018-11-03"
    assert coerce_value("Mon, 15 Apr 2019 00:00:00 GMT", "rfc2822_date") == "2019-04-15"
    with pytest.raises(CoercionError):
        coerce_value("definitely not rfc 2822", "rfc2822_date")


def test_coerce_epoch_ms_date_matches_arcgis_rest_format():
    """ArcGIS REST endpoints return dates as Unix epoch milliseconds.
    Spot-check against known values from the live HRM Zoning Boundaries
    dataset response."""
    # 1003968000000 ms = 2001-10-25 00:00:00 UTC
    assert coerce_value(1003968000000, "epoch_ms_date") == "2001-10-25"
    # 1769990400000 ms = 2026-02-02 00:00:00 UTC (recent SDATE)
    assert coerce_value(1769990400000, "epoch_ms_date") == "2026-02-02"
    # Strings that look like integers also work — JSON sometimes preserves
    # numeric strings when the column was loaded loosely.
    assert coerce_value("1554508800000", "epoch_ms_date") == "2019-04-06"


def test_coerce_epoch_ms_date_rejects_non_integer():
    with pytest.raises(CoercionError):
        coerce_value("not a number", "epoch_ms_date")
    with pytest.raises(CoercionError):
        coerce_value(None, "epoch_ms_date")


def test_unsupported_type_rejected():
    with pytest.raises(CoercionError):
        coerce_value("anything", "polygon")
