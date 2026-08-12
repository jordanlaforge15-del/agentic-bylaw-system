#!/usr/bin/env python3
"""ABS-471: the eval corpus's three integrity guards, in one command.

An audit found defects in 17 of the 20 cases in
``evals/regional_centre_test_prompts.json`` — 7 wrong zones, 5 addresses that do
not exist, one zone with no polygons in the dataset at all, and a dozen
keyword/reference errors. None of it was caught, and none of it *could* have
been: ``scripts/build_bylaw_reference_index.py --check`` proves each
``expected_bylaw_references`` entry resolves to a real fragment, and that is all
anything checked. ``zone``, ``address`` and ``expected_answer_keywords`` had no
validation of any kind, and every one of the defects lived in those fields.

This script is the missing half.

G1 — spatial zone assertion
    Per case: the civic number is checked against the municipality's own street
    ranges (no network), the address is resolved to a point, the resolution's
    confidence is required to be better than an estimate, and the point is
    intersected against ``halifax_zoning_boundaries`` — which must return the
    zone the case declares. This alone catches every wrong zone, every
    fabricated address, and every point that lands in the road right-of-way
    instead of on a parcel.

G2 — keyword validation
    Every ``Section N`` / ``Table N`` / ``Schedule N`` token in
    ``expected_answer_keywords`` must fall in the by-law chapter that governs
    the case's zone. See ``scripts/eval_zone_chapters.py``.

G3 — the same chapter rule over ``expected_bylaw_references``
    So a case whose ``zone`` changes cannot keep references from the old zone's
    chapter. Also enforced by ``build_bylaw_reference_index.py --check``.

Usage::

    python scripts/verify_eval_corpus_integrity.py            # all three
    python scripts/verify_eval_corpus_integrity.py --offline  # G2/G3 only, no DB
    python scripts/verify_eval_corpus_integrity.py --only TC-004 TC-009

Exits non-zero if any guard fails, naming the case, the field and
expected-vs-actual on every line. The pytest form is
``tests/test_eval_address_spatial.py`` and ``tests/test_eval_keyword_chapters.py``,
which skip cleanly where the Halifax ingest is not reachable.

What is NOT checked: numeric keywords (``6.0 m``, ``80%``). Tying a bare number
to the right clause is a harder problem — see
docs/ABS-471-EVAL-CORPUS-GUARDS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (REPO_ROOT / "src", REPO_ROOT / "mcp", REPO_ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from build_zone_chapter_map import MAP_FILE, load_map  # noqa: E402
from eval_zone_chapters import all_violations  # noqa: E402

PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"

DEFAULT_DB_URL = "postgresql+psycopg://layer1:layer1@localhost:5432/layer1"

# The Regional Centre Land Use By-law's area id in the HRM zoning dataset,
# matching scripts/zone_address_picker.REGIONAL_CENTRE_BYLAW_AREA_ID. Filtering
# on it matters: HRM publishes 11,069 zoning polygons across every by-law area,
# and a point in Sackville would otherwise "confirm" a Regional Centre zone code
# that happens to be reused there.
REGIONAL_CENTRE_BYLAW_AREA_ID = "23"

# The floor a case's geocode must clear. ABS-466's vocabulary: 0.95 is a
# ROOFTOP match on the building, 0.85 an interpolation along the street from
# the surrounding civic numbering, 0.60 the centre of a block, 0.40 a locality.
#
# The bar is set at interpolated, not rooftop, on purpose. TC-002/003/004 used
# to resolve at 0.60 — a point the geocoder placed at the middle of something,
# which selects a zoning polygon more or less by luck — and TC-005's 0.60 point
# landed 32 km outside HRM entirely. Those are the resolutions this rejects.
# Rooftop-versus-interpolated is a *different* question, already owned by
# tests/test_eval_address_zones.py, which allows an interpolated point only
# where the case's notes declare it. Duplicating that here would give one
# corpus two contradictory rules.
MIN_GEOCODE_CONFIDENCE = 0.85
REJECTED_QUALITIES = frozenset({"centroid", "approximate", "unknown"})

# The zone the case claims must come back from ST_Intersects on this dataset.
ZONING_DATASET_SQL = (
    "SELECT f.canonical_attributes_json->>'zone_code' AS zone "
    "FROM external_dataset_feature f "
    "JOIN external_dataset d ON d.id = f.external_dataset_id "
    "WHERE d.name ILIKE '%zoning%' "
    "AND f.canonical_attributes_json->>'bylaw_area_id' = :area "
    "AND ST_Intersects(f.geometry, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"
)


def load_cases(path: Path = PROMPTS_FILE) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# G1 — spatial zone assertion
# ---------------------------------------------------------------------------


class CorpusUnavailable(RuntimeError):
    """The Halifax zoning ingest is not reachable, so G1 cannot run at all."""


@dataclass
class SpatialCheck:
    """What the live data says about one case's address."""

    case_id: str
    address: str
    claimed_zone: str
    civic_status: str | None = None
    civic_reason: str | None = None
    confidence: float | None = None
    location_type: str | None = None
    intersecting_zones: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        return not self.failures

    def describe(self) -> str:
        if self.skipped:
            return f"SKIP {self.case_id}: {self.skipped}"
        if self.ok:
            return (
                f"PASS {self.case_id}: {self.address} -> "
                f"{self.claimed_zone} (confidence {self.confidence}, "
                f"civic number {self.civic_status})"
            )
        return f"FAIL {self.case_id}: " + "; ".join(self.failures)


def regional_centre_zoning_polygons(session) -> int:
    """How many Regional Centre zoning polygons are in scope.

    Zero means this database cannot answer G1 — an e2e worktree database
    carries seeded overlay datasets that satisfy "some dataset called zoning"
    but contain none of these addresses, so the count is deliberately specific.
    """
    import sqlalchemy as sa

    return (
        session.execute(
            sa.text(
                "SELECT COUNT(*) FROM external_dataset_feature f "
                "JOIN external_dataset d ON d.id = f.external_dataset_id "
                "WHERE d.name ILIKE '%zoning%' "
                "AND f.canonical_attributes_json->>'bylaw_area_id' = :area"
            ),
            {"area": REGIONAL_CENTRE_BYLAW_AREA_ID},
        ).scalar()
        or 0
    )


def check_case_spatially(session, case: dict[str, Any]) -> SpatialCheck:
    """Run G1 against one case. Never raises; every finding is a failure line."""
    import sqlalchemy as sa
    from layer2.retrieval.civic_address import format_ranges, verify_civic_address
    from layer2.retrieval.geocode import resolve_location_with_detail
    from layer2.retrieval.location import RegexLocationExtractor
    from layer2.retrieval.resolution_quality import classify_resolution

    result = SpatialCheck(
        case_id=case["id"], address=case["address"], claimed_zone=case["zone"]
    )

    refs = RegexLocationExtractor().extract(case["address"])
    if not refs:
        result.failures.append(
            f"address {case['address']!r} does not parse as a location at all"
        )
        return result
    ref = refs[0]

    # 1. Does the civic number exist? Municipal data only — no network, and the
    #    cheapest high-value check here: it is what catches a fabricated
    #    address before a geocoder invents a plausible point for it.
    if ref.kind == "civic_address":
        verdict = verify_civic_address(
            session, civic_number=ref.civic_number, street=ref.street
        )
        result.civic_status, result.civic_reason = verdict.status, verdict.reason
        if verdict.status == "not_found":
            ranges = ", ".join(format_ranges(verdict.valid_ranges)) or "none published"
            result.failures.append(
                f"civic number: {case['address']!r} does not exist — no published "
                f"address or street-segment range on that street covers it. "
                f"Ranges that do exist: {ranges}"
            )
            return result

    # 2. Resolve to a point through the production resolver.
    resolved, detail = resolve_location_with_detail(session, ref)
    if resolved is None:
        if detail and "no external geocoder" in detail.lower():
            result.skipped = (
                f"{case['address']!r} is not in the geocode cache and no external "
                "geocoder is configured, so the point cannot be derived here"
            )
            return result
        result.failures.append(
            f"address {case['address']!r} does not resolve to a point "
            f"({detail or 'no detail reported'})"
        )
        return result

    result.confidence = resolved.confidence
    result.location_type = resolved.location_type
    quality = classify_resolution(
        location_type=resolved.location_type, confidence=resolved.confidence
    )

    # 3. Reject an estimated point. A zone read off a guessed coordinate is a
    #    guess, and the whole case is built on the zone.
    if quality in REJECTED_QUALITIES or (resolved.confidence or 0) < MIN_GEOCODE_CONFIDENCE:
        result.failures.append(
            f"geocode confidence: resolved at {resolved.confidence!r} "
            f"({quality}, location_type {resolved.location_type!r}); the eval "
            f"requires at least {MIN_GEOCODE_CONFIDENCE} — an estimated point "
            "selects its zoning polygon by luck"
        )
        return result

    geometry = resolved.geometry or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "Point" or not coordinates:
        result.failures.append(
            f"resolved geometry is {geometry.get('type')!r}, not a Point, so it "
            "cannot be intersected against the zoning boundaries"
        )
        return result
    lon, lat = coordinates[0], coordinates[1]

    # 4. ST_Intersects against the real zoning boundaries.
    result.intersecting_zones = sorted(
        row[0]
        for row in session.execute(
            sa.text(ZONING_DATASET_SQL),
            {"area": REGIONAL_CENTRE_BYLAW_AREA_ID, "lon": lon, "lat": lat},
        )
        if row[0]
    )

    if not result.intersecting_zones:
        result.failures.append(
            f"the point ({lon}, {lat}) intersects no Regional Centre zoning "
            f"polygon — it is in the road right-of-way or outside the mapped "
            f"area, so it cannot confirm {case['zone']}"
        )
        return result
    if len(result.intersecting_zones) > 1:
        result.failures.append(
            f"the point intersects more than one zoning polygon "
            f"({', '.join(result.intersecting_zones)}), so the case's zone is "
            "ambiguous"
        )
        return result
    if result.intersecting_zones[0] != case["zone"]:
        result.failures.append(
            f"zone: {case['address']!r} intersects the "
            f"{result.intersecting_zones[0]} polygon, but the case claims "
            f"{case['zone']}. The eval would grade a correct "
            f"{result.intersecting_zones[0]} answer against {case['zone']} "
            "expectations. Re-run scripts/verify_eval_address_zones.py --repair."
        )
    return result


def check_spatially(session, cases: list[dict[str, Any]]) -> list[SpatialCheck]:
    if not regional_centre_zoning_polygons(session):
        raise CorpusUnavailable(
            "no Regional Centre zoning polygons "
            f"(bylaw_area_id={REGIONAL_CENTRE_BYLAW_AREA_ID}) in this database"
        )
    return [check_case_spatially(session, case) for case in cases]


# ---------------------------------------------------------------------------
# G2 / G3 — the chapter rule, which needs no database
# ---------------------------------------------------------------------------

GUARDED_FIELDS = ("expected_answer_keywords", "expected_bylaw_references")


def check_chapters(cases: list[dict[str, Any]]) -> list[str]:
    return all_violations(cases, GUARDED_FIELDS, load_map())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--db-url",
        default=(
            os.environ.get("EVAL_ADDRESS_DB_URL")
            or os.environ.get("DATABASE_URL")
            or DEFAULT_DB_URL
        ),
        help=(
            "SQLAlchemy URL for the database holding the Halifax zoning ingest "
            f"(default: $EVAL_ADDRESS_DB_URL, $DATABASE_URL, then {DEFAULT_DB_URL})"
        ),
    )
    parser.add_argument("--prompts-file", type=Path, default=PROMPTS_FILE)
    parser.add_argument("--only", nargs="*", default=[], help="Limit to these case ids.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run only the guards that need no database (G2, G3).",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.prompts_file)
    selected = [c for c in cases if not args.only or c["id"] in args.only]
    failed = False

    print(f"G2/G3 — chapter rule over {', '.join(GUARDED_FIELDS)} ({MAP_FILE.name}):")
    violations = check_chapters(selected)
    for line in violations:
        print(f"  FAIL {line}")
    if violations:
        failed = True
    else:
        print(f"  PASS all {len(selected)} cases cite only their own zone's chapters.")

    if args.offline:
        return 1 if failed else 0

    print("\nG1 — spatial zone assertion:")
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    engine = sa.create_engine(args.db_url, connect_args={"connect_timeout": 3})
    try:
        with Session(engine) as session:
            results = check_spatially(session, selected)
    except (CorpusUnavailable, sa.exc.SQLAlchemyError) as exc:
        # Skipping, not failing, is the documented behaviour where the ~180k
        # parcel Halifax ingest is absent — CI and every e2e worktree. The
        # exception type is narrow on purpose: a bug in the guard itself must
        # not read as "no corpus here".
        print(f"  SKIP {type(exc).__name__}: {exc} — needs a box with the Halifax ingest.")
        return 1 if failed else 0
    for result in results:
        print(f"  {result.describe()}")
    if any(not r.ok for r in results):
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
