#!/usr/bin/env python3
"""What changed when address resolution stopped hedging? (ABS-469)

ABS-469 asks for a before/after on answer quality, measured against the cases
ABS-468 selected rather than against generated ones. The half of that which
can be measured without a model in the loop is this: for every address the
eval and the golden subset are anchored on, what did ``get_address_profile``
report before the civic-number check existed, and what does it report now?

"Before" is not a guess — it is the same profile builder run on the geocoded
point with the verification skipped, which is exactly the code path that
shipped after ABS-466. So the two columns differ only by this issue's change.

    python scripts/measure_address_resolution.py                  # eval + golden
    python scripts/measure_address_resolution.py --address "100 Robie Street"
    python scripts/measure_address_resolution.py --json out.json

Requires ``DATABASE_URL`` pointing at an ingested corpus (the check is only
meaningful where the street-centerline layer is present). Addresses missing
from ``geocode_cache`` will hit the external geocoder if a key is configured.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (REPO_ROOT / "src", REPO_ROOT / "mcp", REPO_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bylaw_retrieval.retrieval.service import RetrievalService  # noqa: E402

from layer2.retrieval.geocode import resolve_location_with_detail  # noqa: E402
from layer2.retrieval.location import RegexLocationExtractor  # noqa: E402

EVAL_PATH = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"
GOLDEN_PATH = REPO_ROOT / "evals" / "golden" / "golden_cases.json"


@dataclass
class Measurement:
    address: str
    source: str
    case_id: str | None
    claimed_zone: str | None
    before_zone: str | None
    before_quality: str | None
    after_zone: str | None
    civic_address_status: str | None
    civic_address_evidence: str | None
    valid_ranges: list[str]
    suggestions: list[str]
    zone_boundary_distance_m: float | None
    nearest_other_zone: str | None
    parcel_zones: list[str]

    @property
    def changed(self) -> bool:
        return self.before_zone != self.after_zone


def _before_profile(service: RetrievalService, address: str) -> tuple[str | None, str | None]:
    """The zone and resolution quality the pre-ABS-469 path would report.

    Deliberately calls the private builder with no verdict: that IS the old
    behaviour, and reimplementing it here would measure the reimplementation.
    """
    refs = RegexLocationExtractor().extract(address)
    if not refs:
        return None, None
    ref = refs[0]
    resolved, _detail = resolve_location_with_detail(service.session, ref)
    if resolved is None:
        return None, None
    profile = service._build_address_profile(ref.raw_text or address, ref, resolved)
    return profile.zone, profile.resolution_quality


def measure(
    session: Session, address: str, *, source: str, case_id: str | None, claimed_zone: str | None
) -> Measurement:
    service = RetrievalService(session)
    before_zone, before_quality = _before_profile(service, address)
    after = service.get_address_profile(address)
    return Measurement(
        address=address,
        source=source,
        case_id=case_id,
        claimed_zone=claimed_zone,
        before_zone=before_zone,
        before_quality=before_quality,
        after_zone=after.zone,
        civic_address_status=after.civic_address_status,
        civic_address_evidence=after.civic_address_evidence,
        valid_ranges=after.valid_civic_number_ranges,
        suggestions=after.suggested_civic_numbers,
        zone_boundary_distance_m=after.zone_boundary_distance_m,
        nearest_other_zone=after.nearest_other_zone,
        parcel_zones=after.parcel_zones,
    )


def _eval_cases() -> list[tuple[str, str | None, str | None]]:
    if not EVAL_PATH.exists():
        return []
    payload = json.loads(EVAL_PATH.read_text())
    cases = payload["cases"] if isinstance(payload, dict) else payload
    out: list[tuple[str, str | None, str | None]] = []
    for index, case in enumerate(cases, start=1):
        address = case.get("address")
        if not address:
            continue
        out.append((address, case.get("id") or f"case-{index:03d}", case.get("zone")))
    return out


def _golden_case_ids() -> set[str]:
    if not GOLDEN_PATH.exists():
        return set()
    payload = json.loads(GOLDEN_PATH.read_text())
    return {case["case_id"] for case in payload.get("cases", [])}


def _strip_locality(address: str) -> str:
    """"1222 Robie Street, Halifax, NS" -> "1222 Robie Street"."""
    return re.split(r",", address)[0].strip()


def _render(rows: list[Measurement]) -> str:
    lines = [
        f"{'case':7} {'address':34} {'src':10} {'before':8} {'after':8} "
        f"{'civic':12} {'bdry_m':7} {'split'}"
    ]
    for row in rows:
        lines.append(
            f"{(row.case_id or '-'):7} {row.address[:33]:34} {row.source:10} "
            f"{(row.before_zone or '-'):8} {(row.after_zone or '-'):8} "
            f"{(row.civic_address_status or '-'):12} "
            f"{('-' if row.zone_boundary_distance_m is None else f'{row.zone_boundary_distance_m:.1f}'):7} "
            f"{','.join(row.parcel_zones) or '-'}"
        )
    changed = [r for r in rows if r.changed]
    not_found = [r for r in rows if r.civic_address_status == "not_found"]
    near = [r for r in rows if r.zone_boundary_distance_m is not None]
    split = [r for r in rows if r.parcel_zones]
    lines += [
        "",
        f"addresses measured        : {len(rows)}",
        f"civic number not found    : {len(not_found)}"
        + (f"  ({', '.join(r.address for r in not_found)})" if not_found else ""),
        f"zone answer changed       : {len(changed)}",
        f"within 25 m of a zone line: {len(near)}",
        f"parcel split across zones : {len(split)}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        action="append",
        default=[],
        help="Measure this address instead of the eval/golden set (repeatable).",
    )
    parser.add_argument("--json", type=Path, help="Also write the rows as JSON here.")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    golden_ids = _golden_case_ids()
    if args.address:
        targets = [(address, "manual", None, None) for address in args.address]
    else:
        targets = []
        for address, case_id, zone in _eval_cases():
            source = "golden" if case_id in golden_ids else "generated"
            targets.append((_strip_locality(address), source, case_id, zone))

    engine = create_engine(database_url)
    rows: list[Measurement] = []
    with Session(engine) as session:
        for address, source, case_id, zone in targets:
            rows.append(
                measure(session, address, source=source, case_id=case_id, claimed_zone=zone)
            )

    print(_render(rows))
    if args.json:
        payload: list[dict[str, Any]] = [asdict(row) for row in rows]
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
