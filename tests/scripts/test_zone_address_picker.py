"""ABS-474 — a derived address must be one the municipality registers.

``zone_address_picker`` derives an address by reverse-geocoding a parcel's
interior point. A reverse geocode returns the *nearest* street address to a
point, which is not the address the municipality assigns to the parcel: on a
corner lot it takes the civic number from one street and the route from
another, and on a large multi-frontage parcel it interpolates a number between
two real ones.

Three of the twenty eval cases were fabricated that way and passed every check
the module had. The zone round-trip cannot catch it — the composed string
forward-geocodes back onto the same parcel, so it confirms. Only the civic
register can, and these tests pin that it does.

The register and the reverse geocoder are both injected, so nothing here
touches the network.
"""

from __future__ import annotations

import pytest

from scripts.zone_address_picker import (
    _address_from_register_row,
    _is_registered,
)


class FakeRegister:
    """The civic addresses a municipality registers, keyed by parcel id."""

    def __init__(self, by_pid: dict[str, list[str]]) -> None:
        self._by_pid = by_pid

    def civics_for_pid(self, pid: str) -> list[str]:
        return list(self._by_pid.get(pid, []))


# The three real failures, with the register's actual answer for each parcel.
# Kept verbatim so the test names the defect it prevents rather than a
# synthetic stand-in.
CORNER_LOT = ("40811085", ["249 Windmill Road, Dartmouth, NS",
                           "251 Windmill Road, Dartmouth, NS",
                           "257 Windmill Road, Dartmouth, NS"])
MULTI_FRONTAGE = ("00152777", ["3111 Kempt Road, Halifax, NS",
                               "3115 Kempt Road, Halifax, NS",
                               "3121 Kempt Road, Halifax, NS",
                               "3125 Kempt Road, Halifax, NS",
                               "3128 Robie Street, Halifax, NS"])
SINGLE_CIVIC = ("00078352", ["1462 Thornvale Avenue, Halifax, NS"])


@pytest.mark.parametrize(
    ("fabricated", "parcel"),
    [
        # Right civic number, wrong street: Google took "251" off Windmill Road
        # and the route off the nearer Stairs Street centreline.
        ("251 Stairs Street, Dartmouth, NS", CORNER_LOT),
        # Right street, interpolated number: 3123 sits between the registered
        # 3121 and 3125 and is assigned to nothing.
        ("3123 Kempt Road, Halifax, NS", MULTI_FRONTAGE),
        # Right civic number, adjacent street.
        ("1462 Birchdale Avenue, Halifax, NS", SINGLE_CIVIC),
    ],
)
def test_a_fabricated_address_is_not_registered_on_its_parcel(
    fabricated: str, parcel: tuple[str, list[str]]
) -> None:
    pid, registered = parcel
    assert _is_registered(fabricated, FakeRegister({pid: registered}).civics_for_pid(pid)) is False


@pytest.mark.parametrize(
    ("real", "parcel"),
    [
        ("251 Windmill Road, Dartmouth, NS", CORNER_LOT),
        ("3125 Kempt Road, Halifax, NS", MULTI_FRONTAGE),
        ("1462 Thornvale Avenue, Halifax, NS", SINGLE_CIVIC),
        # A parcel addressed on two streets registers both; either is real.
        ("3128 Robie Street, Halifax, NS", MULTI_FRONTAGE),
    ],
)
def test_a_registered_address_is_accepted(real: str, parcel: tuple[str, list[str]]) -> None:
    pid, registered = parcel
    assert _is_registered(real, FakeRegister({pid: registered}).civics_for_pid(pid)) is True


def test_matching_ignores_punctuation_and_case_only() -> None:
    """Whitespace and comma noise must not reject a genuine match...

    ...but a real difference in number, street or community must, because that
    difference is the whole signal.
    """
    registered = ["1801 Hollis Street, Halifax, NS"]
    assert _is_registered("1801  Hollis Street,Halifax, NS", registered) is True
    assert _is_registered("1801 HOLLIS STREET, HALIFAX, NS", registered) is True
    assert _is_registered("1803 Hollis Street, Halifax, NS", registered) is False
    assert _is_registered("1801 Hollis Street, Dartmouth, NS", registered) is False


def test_a_parcel_the_register_cannot_answer_for_yields_nothing() -> None:
    """No civics means no evidence — the caller must skip, not assume."""
    assert FakeRegister({}).civics_for_pid("99999999") == []


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {"CIV_NUM": 1801, "STR_NAME": "HOLLIS", "STR_TYPE": "ST",
             "GSA_NAME": "HALIFAX"},
            "1801 Hollis Street, Halifax, NS",
        ),
        # HRM splits the name from the type and abbreviates the type; the
        # corpus and Google's components both spell it out.
        (
            {"CIV_NUM": 1462, "STR_NAME": "THORNVALE", "STR_TYPE": "AVE",
             "GSA_NAME": "HALIFAX"},
            "1462 Thornvale Avenue, Halifax, NS",
        ),
        (
            {"CIV_NUM": 65, "STR_NAME": "NORA BERNARD", "STR_TYPE": "ST",
             "GSA_NAME": "HALIFAX"},
            "65 Nora Bernard Street, Halifax, NS",
        ),
        # An unmapped type composes without a suffix rather than guessing,
        # which can only make the comparison stricter.
        (
            {"CIV_NUM": 5, "STR_NAME": "GEORGES ISLAND", "STR_TYPE": "XYZ",
             "GSA_NAME": "HALIFAX"},
            "5 Georges Island, Halifax, NS",
        ),
        # Rows with nothing to compose from are dropped, not half-built.
        ({"CIV_NUM": None, "STR_NAME": "HOLLIS", "STR_TYPE": "ST"}, None),
        ({"CIV_NUM": 1801, "STR_NAME": "", "STR_TYPE": "ST"}, None),
    ],
)
def test_register_rows_compose_into_the_corpus_address_shape(
    row: dict, expected: str | None
) -> None:
    assert _address_from_register_row(row) == expected
