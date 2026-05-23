"""Synthetic title-block / drawing-page text fixtures for ABS-56.

The classifier's primary input is the Docling-extracted text of the
page (which is typically dominated by the title block in the bottom-
right corner of an architect's drawing sheet). These fixtures stand in
for that text — one per drawing type plus a couple of ambiguous cases
the vision stage exists to resolve.

Each fixture is shaped like a realistic title block: project name,
drawing title, sheet code, and a date / revision line. Real sheets
have far more text (notes, dimensions, schedules), but for
classification only the title block / sheet-code section matters.
"""
from __future__ import annotations


def site_plan_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "123 Barrington St, Halifax NS\n\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: SITE PLAN\n"
        "Scale: 1:200\n"
        "Sheet C1.01\n"
        "Issued for Permit  2024-08-15\n"
        "Lot area 1,250 m²    Setbacks indicated on plan.\n"
        "NORTH ARROW shown top-right.\n"
    )


def floor_plan_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: FIRST FLOOR PLAN\n"
        "Sheet A1.02\n"
        "Scale: 1:100\n"
        "Issued for Permit  2024-08-15\n"
        "Gross Floor Area: 412 m²\n"
    )


def elevation_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: NORTH ELEVATION\n"
        "Sheet A2.01\n"
        "Scale: 1:100\n"
        "Issued for Permit  2024-08-15\n"
    )


def section_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: BUILDING SECTION A\n"
        "Sheet A3.01\n"
        "Scale: 1:50\n"
        "Issued for Permit  2024-08-15\n"
    )


def detail_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: WALL SECTION DETAILS\n"
        "Sheet A5.03\n"
        "Scale: 1:10\n"
        "Issued for Permit  2024-08-15\n"
    )


def schedule_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: DOOR & WINDOW SCHEDULE\n"
        "Sheet A6.01\n"
        "Issued for Permit  2024-08-15\n"
    )


def cover_sheet_text() -> str:
    return (
        "ACME ARCHITECTS\n"
        "PROJECT: 1234 ROBIE STREET MIXED-USE\n"
        "DRAWING TITLE: COVER SHEET / DRAWING INDEX\n"
        "Sheet A0.01\n"
        "Issued for Permit  2024-08-15\n"
        "Drawing List:\n"
        "  A0.01 Cover Sheet\n"
        "  C1.01 Site Plan\n"
        "  A1.01 First Floor Plan\n"
    )


def unknown_text() -> str:
    """No sheet code, no title keyword — looks like a random text fragment."""
    return (
        "Some random project text without any title block content.\n"
        "Notes: see consultant drawings for further detail.\n"
    )


def ambiguous_text() -> str:
    """Sheet code says A1 (floor plan) but the title text says NORTH ELEVATION.

    Used to test the disagreement branch of the heuristic.
    """
    return (
        "ACME ARCHITECTS\n"
        "DRAWING TITLE: NORTH ELEVATION\n"
        "Sheet A1.01\n"
        "Issued for Permit  2024-08-15\n"
    )


def multi_drawing_text() -> str:
    """Page text mentions both a floor plan and an elevation.

    Used to test the multi_drawing_detected branch.
    """
    return (
        "ACME ARCHITECTS\n"
        "Drawing: TYPICAL FLOOR PLAN  +  NORTH ELEVATION\n"
        "Notes apply to all sheets.\n"
    )


def title_only_floor_plan_text() -> str:
    """Floor plan title text but no recognisable sheet code."""
    return (
        "FIRST FLOOR PLAN\n"
        "Scale: 1:100\n"
        "Project: Custom Residence\n"
    )
