#!/usr/bin/env python3
"""Check — and repair — the zone every eval address actually resolves to.

ABS-467. ``evals/regional_centre_test_prompts.json`` pairs a ``zone`` with an
``address`` on every case. Until this script existed, nothing connected the
two: the operator supplied both and the address was never resolved. 17 of the
20 disagreed with their own zone.

Two modes, both driving the *production* ``get_address_profile`` path so what
is checked is what the advisor sees:

``--check``
    Resolve every case's address and exit non-zero if any resolves to a zone
    other than the one the case claims, or if the ``address_resolution``
    block recorded on the case has drifted from the live resolution. This is
    the CLI form of ``tests/test_eval_address_zones.py``.

``--repair``
    For every failing case, derive a real address in the claimed zone (see
    ``scripts/zone_address_picker.py``) and rewrite the case's ``address``
    and ``address_resolution``. Conversation text is NOT rewritten — a turn
    that names the old street is reported so a human can re-check that
    question, zone and expected keywords still agree.

Usage:
    python scripts/verify_eval_address_zones.py --check
    python scripts/verify_eval_address_zones.py --repair --only TC-004 TC-005
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (REPO_ROOT / "src", REPO_ROOT / "mcp", REPO_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bylaw_retrieval.retrieval.service import RetrievalService
from zone_address_picker import (
    DEFAULT_DB_URL,
    REGIONAL_CENTRE_BYLAW_AREA_ID,
    _build_civic_register,
    _build_reverse_geocoder,
    _is_registered,
    pick_address_for_zone,
)

PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"

# The per-case block ABS-467 adds. ``resolution_quality`` is ABS-466's
# vocabulary, recorded so a case that depends on an *estimated* point says so
# rather than reading like a rooftop match. Every field here is re-derivable
# from ``get_address_profile``, which is what makes the block checkable.
RESOLUTION_FIELDS = (
    "resolved_zone",
    "resolution_quality",
    "location_type",
    "location_confidence",
    "location_resolver",
)

# Provenance, not a live-comparable field: the parcel the address was derived
# from. ``get_address_profile`` does not report a parcel for a civic address
# (``AddressProfile.pid`` is only populated when the question named a PID), so
# it is recorded but excluded from drift comparison.
PROVENANCE_FIELDS = ("parcel_pid",)


@dataclass(frozen=True)
class CaseCheck:
    case_id: str
    claimed_zone: str
    address: str
    resolved_zone: str | None
    resolution_quality: str | None
    unresolvable: bool
    outside_mapped_area: bool
    recorded_drift: list[str]
    # ABS-474: is ``address`` one of the civics the municipality registers on
    # the parcel the case was derived from? Read off the recorded snapshot, so
    # this stays an offline check. None means the snapshot is absent and the
    # question cannot be asked — run --backfill-civics.
    address_registered: bool | None = None

    @property
    def zone_ok(self) -> bool:
        return not self.unresolvable and self.resolved_zone == self.claimed_zone

    @property
    def ok(self) -> bool:
        return (
            self.zone_ok
            and not self.recorded_drift
            and self.address_registered is not False
        )

    def describe(self) -> str:
        if self.unresolvable:
            outcome = "UNRESOLVABLE"
        elif self.outside_mapped_area:
            outcome = "outside every mapped boundary"
        else:
            outcome = f"resolves to {self.resolved_zone}"
        if self.zone_ok:
            outcome = f"confirmed {self.resolved_zone} ({self.resolution_quality})"
        drift = f"; recorded drift: {', '.join(self.recorded_drift)}" if self.recorded_drift else ""
        if self.address_registered is False:
            drift += "; address is not registered on its parcel"
        return f"{self.case_id} claims {self.claimed_zone}: {outcome}{drift}"


def load_cases(path: Path = PROMPTS_FILE) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def live_resolution(service: RetrievalService, address: str) -> dict[str, Any]:
    """The production resolution of ``address``, as the recorded block shape."""
    profile = service.get_address_profile(address)
    return {
        "resolved_zone": profile.zone,
        "resolution_quality": profile.resolution_quality,
        "location_type": profile.location_type,
        "location_confidence": profile.location_confidence,
        "location_resolver": profile.location_resolver,
        "_unresolvable": profile.unresolvable,
        "_outside_mapped_area": profile.outside_mapped_area,
    }


def recorded_drift(case: dict[str, Any], live: dict[str, Any]) -> list[str]:
    """Fields where the case's stored ``address_resolution`` disagrees with live.

    A missing block is itself drift: the point of recording the resolution is
    that a reader can see whether the case rests on a rooftop match or an
    estimate without a database to hand.
    """
    recorded = case.get("address_resolution")
    if not isinstance(recorded, dict):
        return ["address_resolution is missing"]
    drift: list[str] = []
    for field in RESOLUTION_FIELDS:
        if recorded.get(field) != live.get(field):
            drift.append(f"{field}: recorded {recorded.get(field)!r} != live {live.get(field)!r}")
    return drift


def check_case(service: RetrievalService, case: dict[str, Any]) -> CaseCheck:
    live = live_resolution(service, case["address"])
    return CaseCheck(
        case_id=case["id"],
        claimed_zone=case["zone"],
        address=case["address"],
        resolved_zone=live["resolved_zone"],
        resolution_quality=live["resolution_quality"],
        unresolvable=bool(live["_unresolvable"]),
        outside_mapped_area=bool(live["_outside_mapped_area"]),
        recorded_drift=recorded_drift(case, live),
        address_registered=address_is_registered(case),
    )


def address_is_registered(case: dict[str, Any]) -> bool | None:
    """Whether ``address`` is a civic the register puts on the case's parcel.

    Offline: reads the ``registered_civics`` snapshot ``--backfill-civics``
    writes. None when no snapshot has been recorded, so a corpus that predates
    ABS-474 reports "cannot tell" rather than failing every case.
    """
    registered = (case.get("address_resolution") or {}).get("registered_civics")
    if not registered:
        return None
    return _is_registered(case["address"], registered)


def _street_of(address: str) -> str | None:
    """The street portion of ``"1801 Hollis Street, Halifax, NS"``."""
    head = address.split(",")[0].strip()
    match = re.match(r"^\d+\s+(.*)$", head)
    return match.group(1) if match else None


def turns_naming(case: dict[str, Any], address: str) -> list[int]:
    """Turn numbers whose text names ``address`` or its street.

    Repair changes the address but never the conversation. These turns are
    the ones a human has to re-read: a question that says "Mumford Road is a
    major transit corridor" stops making sense once the address moves.
    """
    street = _street_of(address)
    needles = [address.split(",")[0].strip().lower()]
    if street:
        needles.append(street.lower())
        needles.append(street.rsplit(" ", 1)[0].lower())
    hits = []
    for turn in case.get("turns", []):
        text = (turn.get("message") or "").lower()
        if any(needle and needle in text for needle in needles):
            hits.append(turn["turn"])
    return hits


def backfill_registered_civics(
    case: dict[str, Any], *, civic_register: Any
) -> tuple[str, bool]:
    """Record the parcel's registered civics on ``case``; say whether it matches.

    ABS-474. Every case already carries the ``parcel_pid`` its address was
    derived from, so the municipality's own answer for that parcel is one
    lookup away. Recording it turns the offline guard's question — "is this
    address real?" — from an inference about street ranges into a fact.

    Returns ``(report line, address_is_registered)``. Never edits ``address``:
    a case whose address is not registered needs re-deriving through the
    picker, which is ``--repair``'s job, not this one's.
    """
    recorded = case.get("address_resolution") or {}
    pid = str(recorded.get("parcel_pid") or "")
    if not pid:
        # A case authored before parcel provenance was recorded. Its address
        # may be perfectly good, so look the parcel up from the address rather
        # than re-deriving and losing a working case's narrative.
        pid = civic_register.pid_for_address(case["address"]) or ""
        if not pid:
            return (
                f"{case['id']}: no parcel_pid recorded and {case['address']} "
                "is not in the register — re-derive it with --repair",
                False,
            )
        recorded["parcel_pid"] = pid
    civics = civic_register.civics_for_pid(pid)
    if not civics:
        # An unreachable service and a parcel with no civics are the same
        # observation here: no evidence. Leave whatever is recorded alone
        # rather than writing an empty list that would read as "nothing is
        # registered on this parcel".
        return f"{case['id']}: register returned nothing for PID {pid}", True
    recorded["registered_civics"] = list(civics)
    case["address_resolution"] = recorded
    if _is_registered(case["address"], civics):
        return f"{case['id']}: {case['address']} is registered on PID {pid}", True
    return (
        f"{case['id']}: ** {case['address']} is NOT registered on PID {pid} ** "
        f"— HRM assigns {', '.join(civics)}",
        False,
    )


def repair_case(
    session: Session,
    service: RetrievalService,
    case: dict[str, Any],
    *,
    reverse_geocoder: Any,
    civic_register: Any,
    candidates: int,
    allow_interpolated: bool,
    exclude: set[str],
) -> str | None:
    """Give ``case`` a derived address in its claimed zone. Returns a report line."""
    on_street = _street_of(case["address"])
    picked = pick_address_for_zone(
        session,
        case["zone"],
        reverse_geocoder=reverse_geocoder,
        civic_register=civic_register,
        service=service,
        candidates=candidates,
        allow_interpolated=allow_interpolated,
        exclude=exclude,
        on_street=on_street.rsplit(" ", 1)[0] if on_street else None,
    )
    if picked is None:
        return (
            f"{case['id']}: no verified address found in zone {case['zone']} "
            f"(try --candidates higher, or --allow-interpolated)"
        )
    stale_turns = turns_naming(case, case["address"])
    case["address"] = picked.address
    case["address_resolution"] = {
        "resolved_zone": picked.resolved_zone,
        "resolution_quality": picked.resolution_quality,
        "location_type": picked.location_type,
        "location_confidence": picked.location_confidence,
        "location_resolver": picked.location_resolver,
        "parcel_pid": picked.parcel_pid,
        "registered_civics": list(picked.registered_civics),
    }
    exclude.add(picked.address.strip().lower())
    note = f"{case['id']}: -> {picked.address} [{picked.resolution_quality}]"
    if stale_turns:
        note += f"  ** turns {stale_turns} still name the old street — re-check the text **"
    return note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Verify; exit 1 on drift.")
    mode.add_argument("--repair", action="store_true", help="Re-derive failing addresses.")
    mode.add_argument(
        "--backfill-civics",
        action="store_true",
        help=(
            "Record each case's parcel's registered civic addresses and report "
            "any case whose address is not one of them. Never edits the "
            "address — re-derive those with --repair."
        ),
    )
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB_URL))
    parser.add_argument("--prompts-file", type=Path, default=PROMPTS_FILE)
    parser.add_argument("--only", nargs="*", default=[], help="Limit to these case ids.")
    parser.add_argument("--candidates", type=int, default=25)
    parser.add_argument("--allow-interpolated", action="store_true")
    parser.add_argument(
        "--upgrade-interpolated",
        action="store_true",
        help="Also re-derive cases whose zone is right but whose point is estimated.",
    )
    parser.add_argument(
        "--bylaw-area-id", default=REGIONAL_CENTRE_BYLAW_AREA_ID, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.prompts_file)
    selected = [c for c in cases if not args.only or c["id"] in args.only]

    engine = create_engine(args.db_url)
    failures: list[CaseCheck] = []
    with Session(engine) as session:
        service = RetrievalService(session)

        if args.check:
            for case in selected:
                result = check_case(service, case)
                print(("PASS " if result.ok else "FAIL ") + result.describe())
                if not result.ok:
                    failures.append(result)
            session.commit()
            if failures:
                print(
                    f"\n{len(failures)} of {len(selected)} cases do not confirm "
                    f"their claimed zone.",
                    file=sys.stderr,
                )
                return 1
            print(f"\nAll {len(selected)} cases confirm their claimed zone.")
            return 0

        if args.backfill_civics:
            unregistered = 0
            for case in selected:
                line, ok = backfill_registered_civics(
                    case, civic_register=_build_civic_register()
                )
                print(line)
                unregistered += 0 if ok else 1
            args.prompts_file.write_text(json.dumps(cases, indent=2) + "\n")
            print(f"\nWrote {args.prompts_file}")
            if unregistered:
                print(
                    f"{unregistered} of {len(selected)} addresses are not "
                    "registered on their parcel — re-derive them with "
                    "--repair --only <ids>.",
                    file=sys.stderr,
                )
                return 1
            return 0

        reverse_geocoder = _build_reverse_geocoder()
        civic_register = _build_civic_register()
        exclude = {c["address"].strip().lower() for c in cases}
        for case in selected:
            result = check_case(service, case)
            # An address whose zone is right but whose point was *estimated*
            # is still worth replacing: an interpolated point sits where the
            # geocoder guessed the civic number falls along the street, so it
            # can drift onto the neighbouring parcel on the next data refresh
            # (ABS-466). --upgrade-interpolated re-derives those too.
            upgradeable = (
                args.upgrade_interpolated
                and result.zone_ok
                and result.resolution_quality != "rooftop"
            )
            # An address the register does not put on the case's parcel has to
            # be re-derived, not refreshed: its zone is right by luck (the
            # fabricated string geocodes back onto the parcel it was composed
            # from), so no amount of re-recording the resolution fixes it.
            unregistered = result.address_registered is False
            if (
                result.zone_ok
                and not result.recorded_drift
                and not upgradeable
                and not unregistered
            ):
                print(f"{case['id']}: already confirmed, left alone")
                continue
            if result.zone_ok and not upgradeable and not unregistered:
                live = live_resolution(service, case["address"])
                recorded = case.get("address_resolution") or {}
                case["address_resolution"] = {
                    **{f: live[f] for f in RESOLUTION_FIELDS},
                    **{f: recorded.get(f) for f in PROVENANCE_FIELDS},
                }
                print(f"{case['id']}: zone already correct, refreshed address_resolution")
                continue
            print(
                repair_case(
                    session,
                    service,
                    case,
                    reverse_geocoder=reverse_geocoder,
                    civic_register=civic_register,
                    candidates=args.candidates,
                    allow_interpolated=args.allow_interpolated,
                    exclude=exclude,
                )
            )
            # Per case, not once at the end: a repair run spends most of its
            # wall time in reverse-geocode HTTP calls, and the database sets
            # idle_in_transaction_session_timeout to a minute.
            session.commit()

    # ASCII-escaped and 2-space indented to match how the generator writes the
    # file, so a repair diff shows only the lines it actually changed.
    args.prompts_file.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"\nWrote {args.prompts_file}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
