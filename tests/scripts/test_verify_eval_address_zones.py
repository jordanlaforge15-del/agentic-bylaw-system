"""ABS-474 — the decision logic that decides whether an eval address is real.

``tests/test_eval_address_spatial.py`` and the ABS-474 Playwright spec assert
the registration rule against the *corpus data*. Neither touches the functions
below, so a logic regression here would read as "the corpus is clean" rather
than failing: the snapshot is what every downstream guard trusts, and these are
what write and interpret it.

``--repair``'s branch is the sharpest edge. An unregistered address is
re-derived rather than merely refreshed, because its zone is right by luck — a
string composed from a parcel geocodes back onto that parcel. A wrong ``True``
from ``address_is_registered`` silently refreshes a fabrication instead of
replacing it.

The register is injected, so nothing here touches the network.
"""

from __future__ import annotations

import pytest

from scripts.verify_eval_address_zones import (
    address_is_registered,
    backfill_registered_civics,
)

WINDMILL = [
    "249 Windmill Road, Dartmouth, NS",
    "251 Windmill Road, Dartmouth, NS",
    "257 Windmill Road, Dartmouth, NS",
]


class FakeRegister:
    """Parcel id -> registered civics, plus the address -> pid inverse."""

    def __init__(
        self,
        by_pid: dict[str, list[str]] | None = None,
        by_address: dict[str, str] | None = None,
    ) -> None:
        self._by_pid = by_pid or {}
        self._by_address = by_address or {}
        self.pid_lookups: list[str] = []

    def civics_for_pid(self, pid: str) -> list[str]:
        return list(self._by_pid.get(pid, []))

    def pid_for_address(self, address: str) -> str | None:
        self.pid_lookups.append(address)
        return self._by_address.get(address)


def _case(address: str, resolution: dict | None = None) -> dict:
    case: dict = {"id": "TC-001", "address": address}
    if resolution is not None:
        case["address_resolution"] = resolution
    return case


# ---------------------------------------------------------------------------
# address_is_registered — the predicate --check fails on
# ---------------------------------------------------------------------------


def test_an_address_in_the_snapshot_is_registered() -> None:
    case = _case(
        "251 Windmill Road, Dartmouth, NS", {"registered_civics": WINDMILL}
    )
    assert address_is_registered(case) is True


def test_an_address_absent_from_the_snapshot_is_not() -> None:
    """The real TC-004 defect: right civic number, wrong street."""
    case = _case("251 Stairs Street, Dartmouth, NS", {"registered_civics": WINDMILL})
    assert address_is_registered(case) is False


@pytest.mark.parametrize(
    "resolution",
    [
        None,                            # no block at all
        {},                              # block, no snapshot
        {"registered_civics": []},       # snapshot recorded empty
    ],
)
def test_no_snapshot_means_cannot_tell_not_a_failure(resolution: dict | None) -> None:
    """None, never False.

    A corpus predating ABS-474 has no snapshot. Reporting False there would
    fail all twenty cases and push --repair into re-deriving addresses that
    were never shown to be wrong.
    """
    assert address_is_registered(_case("1801 Hollis Street", resolution)) is None


def test_formatting_noise_is_not_a_defect_but_a_real_difference_is() -> None:
    snapshot = {"registered_civics": ["1801 Hollis Street, Halifax, NS"]}
    assert address_is_registered(_case("1801  Hollis Street,Halifax, NS", snapshot))
    assert address_is_registered(_case("1801 HOLLIS STREET, HALIFAX, NS", snapshot))
    assert not address_is_registered(_case("1803 Hollis Street, Halifax, NS", snapshot))
    # A different community is a different property, not a formatting variant.
    assert not address_is_registered(
        _case("1801 Hollis Street, Dartmouth, NS", snapshot)
    )


# ---------------------------------------------------------------------------
# backfill_registered_civics — what writes the snapshot
# ---------------------------------------------------------------------------


def test_a_recorded_pid_is_used_and_the_snapshot_is_written() -> None:
    case = _case("251 Windmill Road, Dartmouth, NS", {"parcel_pid": "40811085"})
    register = FakeRegister(by_pid={"40811085": WINDMILL})

    line, ok = backfill_registered_civics(case, civic_register=register)

    assert ok is True
    assert case["address_resolution"]["registered_civics"] == WINDMILL
    assert "is registered" in line
    assert register.pid_lookups == [], "a recorded pid must not trigger a lookup"


def test_an_unregistered_address_is_reported_and_the_snapshot_still_written() -> None:
    """The snapshot is evidence either way — it is what names the real civics."""
    case = _case("251 Stairs Street, Dartmouth, NS", {"parcel_pid": "40811085"})
    register = FakeRegister(by_pid={"40811085": WINDMILL})

    line, ok = backfill_registered_civics(case, civic_register=register)

    assert ok is False
    assert "NOT registered" in line
    assert "249 Windmill Road, Dartmouth, NS" in line
    assert case["address_resolution"]["registered_civics"] == WINDMILL


def test_a_missing_pid_is_looked_up_from_the_address() -> None:
    """The TC-008 path: a correct case authored before pids were recorded.

    Re-deriving it would throw away a working address and its narrative, so
    the parcel is found from the address instead.
    """
    case = _case("1801 Hollis Street, Halifax, NS", {})
    register = FakeRegister(
        by_pid={"40634602": ["1801 Hollis Street, Halifax, NS"]},
        by_address={"1801 Hollis Street, Halifax, NS": "40634602"},
    )

    line, ok = backfill_registered_civics(case, civic_register=register)

    assert ok is True
    assert case["address_resolution"]["parcel_pid"] == "40634602"
    assert register.pid_lookups == ["1801 Hollis Street, Halifax, NS"]


def test_a_missing_pid_the_register_cannot_place_is_reported_not_invented() -> None:
    case = _case("999 Nowhere Street, Halifax, NS", {})
    register = FakeRegister()

    line, ok = backfill_registered_civics(case, civic_register=register)

    assert ok is False
    assert "not in the register" in line
    assert "parcel_pid" not in case.get("address_resolution", {})


def test_an_unreachable_register_leaves_the_recorded_snapshot_alone() -> None:
    """No civics is no evidence, and must not be written as "nothing is here".

    ``HrmCivicRegister`` returns [] for a network failure exactly as it does
    for a parcel with no civics. Overwriting the snapshot with an empty list
    would make every downstream guard report "cannot tell" after one blip.
    """
    case = _case(
        "251 Windmill Road, Dartmouth, NS",
        {"parcel_pid": "40811085", "registered_civics": WINDMILL},
    )
    register = FakeRegister(by_pid={})  # every lookup returns []

    line, ok = backfill_registered_civics(case, civic_register=register)

    assert ok is True, "an unanswerable lookup must not be reported as a defect"
    assert case["address_resolution"]["registered_civics"] == WINDMILL
    assert "returned nothing" in line
