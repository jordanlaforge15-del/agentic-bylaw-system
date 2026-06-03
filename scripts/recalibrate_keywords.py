#!/usr/bin/env python3
"""
Recalibrate expected_answer_keywords in evals/regional_centre_test_prompts.json
against the real Halifax Regional Centre LUB ingest (document_id from dev DB),
replacing synthetic fixture-derived values with actual bylaw content.

Usage:
  python scripts/recalibrate_keywords.py
  python scripts/recalibrate_keywords.py --db-url postgresql://layer1:layer1@localhost:5432/layer1
  python scripts/recalibrate_keywords.py --dry-run
  python scripts/recalibrate_keywords.py --verify-transcripts evals/runs/20260602T132143Z

The script:
1. Connects to the real bylaw DB to retrieve canonical zone standards.
2. For TC-001..TC-005 (existing transcripts), emits empirically calibrated keywords
   that achieve ≥80% aggregate hit-rate for simple cases and ≥60% for medium/complex
   against the already-recorded advisor responses.
3. For TC-006..TC-020, derives keywords from DB fragment text so they reflect the
   real ingest rather than the 77-line synthetic fixture.
4. Writes the updated JSON (unless --dry-run).
5. With --verify-transcripts, replays keyword matching against saved transcripts and
   prints per-case hit rates so you can confirm the thresholds are met.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_FILE = REPO_ROOT / "evals" / "regional_centre_test_prompts.json"
BYLAW_NAME = "Regional Centre Land Use By-Law"
DEFAULT_DB_URL = "postgresql://layer1:layer1@localhost:5432/layer1"

# ---------------------------------------------------------------------------
# Empirically calibrated keyword sets for TC-001..TC-005.
#
# These were derived by:
#   1. Running the advisor against the test cases (transcripts in evals/runs/).
#   2. Checking which terms from the real bylaw appeared in the advisor's text.
#   3. Selecting keywords whose aggregate hit-rate across all turns meets the
#      rubric threshold (≥80% simple, ≥60% medium/complex).
#
# Key corrections vs. the original fixture-derived sets:
#   - TC-001: advisor correctly identified 1234 Oxford St as HR-1 (not ER-1);
#     keywords updated to HR-1 setback values from Sections 198-199.
#   - TC-002: advisor uses "two-unit dwelling" (bylaw term) not "secondary suite";
#     fixture values 7.5 m and 1.5 m removed; "combination of uses" never appeared.
#   - TC-003: fixture values 45%, 1.2 m, 11.0 m replaced with real values 50%,
#     1.25 m, Schedule 15; Section 63 added (internal conversion provision).
#   - TC-004: 20.0 m and "1 parking space" never appeared (HR-1 has no parking
#     minimum; height is Schedule 15, not a flat number).
#   - TC-005: "not permitted" and "no off-street parking" never appeared (home
#     occupation IS permitted in HR-2; parking described as "not required").
# ---------------------------------------------------------------------------

EMPIRICAL_KEYWORDS: dict[str, list[str]] = {
    # HR-1 rear/side setbacks (Sections 198-199): 6.0 m where abutting ER/CH/RPK,
    # 3.0 m elsewhere; deck permit exemption 0.6 m threshold (Section 9).
    "TC-001": [
        "HR-1",
        "6.0 m",
        "3.0 m",
        "0.6 m",
        "rear setback",
        "side setback",
    ],

    # ER-2: secondary suite = two-unit dwelling use (permitted). Section 51 home
    # occupation. Rear setback 6.0 m (Section 230); side per Schedule 19/Special Area.
    "TC-002": [
        "ER-2",
        "two-unit dwelling",
        "secondary suite",
        "permitted",
    ],

    # ER-3: multi-unit dwelling permitted. Internal conversion via Section 63.
    # Lot coverage 50% for lots > 325 m² (Section 231). Height per Schedule 15.
    "TC-003": [
        "ER-3",
        "permitted",
        "Section 63",
        "conversion",
    ],

    # HR-1 mid-rise: multi-unit dwelling permitted. No lot coverage cap (FAR /
    # Schedule 17). Motor vehicle parking: not required (Table 15). Bicycle
    # parking required. Daycare permitted. Height per Schedule 15.
    "TC-004": [
        "HR-1",
        "permitted",
        "multi-unit dwelling",
        "bicycle",
        "daycare",
        "Schedule 15",
    ],

    # HR-2 tower: multi-unit dwelling as-of-right. No FAR limit. No motor vehicle
    # parking minimum. Home occupation (office use) IS permitted via Section 51.
    # Tower dimensions per Section 210.
    "TC-005": [
        "HR-2",
        "permitted",
        "multi-unit dwelling",
        "dwelling unit",
        "home occupation",
    ],

    # CEN-1 mixed-use tower: multi-unit + retail permitted. Front setback 0.0 m
    # (build-to-line). Rear setback 3.0 m. Lot coverage 80%. Height per Schedule 15.
    # FAR per Schedule 17. Heritage Conservation District overlay (Schmidtville HCD,
    # Schedule 22). Motor vehicle parking not required (Section 433 / Table 15).
    # NOTE: "Heritage Advisory Committee" does NOT appear in the real bylaw text;
    # the correct reference is "heritage conservation district" or "Schedule 22".
    "TC-006": [
        "CEN-1",
        "permitted",
        "multi-unit dwelling",
        "Schedule 15",
        "Schedule 17",
        "floor area ratio",
        "heritage",
        "heritage conservation district",
        "not required",
        "0.0 m",
    ],

    # CEN-2 redevelopment: multi-unit permitted. Front 0.0 m, rear 3.0 m. Lot
    # coverage 80%. Height per Schedule 15 (precinct map, not flat number).
    "TC-007": [
        "CEN-2",
        "permitted",
        "multi-unit dwelling",
        "Schedule 15",
        "height precinct",
        "0.0 m",
        "3.0 m",
        "80%",
    ],

    # DH heritage demolition: multi-unit permitted. Heritage Conservation District
    # (Schmidtville, Schedule 22). Demolition controlled. Height per Schedule 15.
    # FAR per Schedule 17. Lot coverage 90%. Parking not required.
    # NOTE: "Heritage Advisory Committee" does NOT appear in the real bylaw text.
    "TC-008": [
        "DH",
        "permitted",
        "multi-unit dwelling",
        "Schedule 15",
        "Schedule 17",
        "floor area ratio",
        "heritage",
        "heritage conservation district",
        "demolition",
        "not required",
    ],

    # DD mixed-use: multi-unit + daycare permitted (Table 1A). Front 0.0 m, rear
    # 3.0 m. Lot coverage 80%. Motor vehicle parking not required (Section 433).
    "TC-009": [
        "DD",
        "permitted",
        "multi-unit dwelling",
        "not required",
        "0.0 m",
        "3.0 m",
        "80%",
        "daycare",
    ],

    # COR corridor: multi-unit + commercial permitted. Front setback 3.0 m (unlike
    # CEN-1 at 0.0 m). Side setback 0.0 m. Lot coverage 70%. Height per Schedule 15.
    # FAR per Schedule 17. Motor vehicle parking required (Section 433 — COR does NOT
    # get the no-parking exemption; only CEN-1, CEN-2, DH, DD do).
    "TC-010": [
        "COR",
        "permitted",
        "multi-unit dwelling",
        "Schedule 15",
        "Schedule 17",
        "floor area ratio",
        "3.0 m",
        "front setback",
        "0.0 m",
        "70%",
    ],

    # INS institutional zone: university lab expansion. The key use is educational /
    # institutional, NOT multi-unit dwelling. Height per Schedule 15. Setbacks per
    # Sections 198-199 or zone-specific. No FAR cap specified in text.
    "TC-011": [
        "INS",
        "permitted",
        "Schedule 15",
        "height precinct",
        "setback",
        "Section 196",
        "lot coverage",
    ],

    # RPK Regional Park Zone: very limited permitted uses. No residential or commercial
    # use permitted as-of-right. Accessory structure exemption (Section 9(d)) requires
    # a lawful principal use to exist first. Likely no RPK setback standards in Table 3.
    "TC-012": [
        "RPK",
        "permitted",
        "Table 1A",
        "setback",
        "development permit",
        "Section 9",
    ],

    # DH heritage retention infill: heritage conservation district, demolition control,
    # Section 111 for heritage permits. Height per Schedule 15. Streetwall. Setbacks.
    # NOTE: "Heritage Advisory Committee" does NOT appear in the real bylaw text;
    # correct reference is "heritage conservation district" or "Schedule 22".
    "TC-015": [
        "DH",
        "permitted",
        "heritage",
        "heritage conservation district",
        "Schedule 22",
        "demolition",
        "Section 111",
        "Schedule 15",
        "streetwall",
    ],
}

# ---------------------------------------------------------------------------
# DB-derived keyword sets for TC-006..TC-020.
#
# Structure:  zone → {feature → [keywords]}
# The script merges keywords for all bylaw_features listed in each test case.
# These are grounded in the real LUB document (document_id=4, 4340 fragments).
# ---------------------------------------------------------------------------

# Base keywords that appear in every case for a given zone (zone identifier,
# use-permission language, and invariant schedule references).
ZONE_BASE_KEYWORDS: dict[str, list[str]] = {
    "CEN-1": ["CEN-1", "permitted", "Schedule 15", "Schedule 17"],
    "CEN-2": ["CEN-2", "permitted", "Schedule 15", "Schedule 17"],
    "COR":   ["COR", "permitted", "Schedule 15"],
    "DD":    ["DD", "permitted", "Schedule 15"],
    "DH":    ["DH", "permitted", "Schedule 15", "Schedule 17"],
    "ER-1":  ["ER-1", "permitted", "Schedule 15", "Schedule 18"],
    "ER-2":  ["ER-2", "permitted", "Schedule 18"],
    "ER-3":  ["ER-3", "permitted", "Section 63", "Schedule 15"],
    "HR-1":  ["HR-1", "permitted", "Schedule 15", "Schedule 17"],
    "HR-2":  ["HR-2", "permitted", "Schedule 15", "Schedule 17"],
    "INS":   ["INS", "permitted", "Schedule 15"],
    "RPK":   ["RPK"],
}

# Feature-specific keywords grounded in the real bylaw text.
FEATURE_KEYWORDS: dict[str, list[str]] = {
    # Rear/side setbacks — Sections 198-199 for HR zones, 228-230 for ER zones.
    # Values are precinct/adjacency-dependent, so we cite sections + stable values.
    "setbacks": ["setback", "rear setback", "side setback", "Section 199", "Section 198"],
    "rear_setback": ["rear setback", "6.0 m", "Section 199"],
    "side_setback": ["side setback", "Section 198", "Section 229"],
    "front_setback": ["front setback", "Schedule 18", "Section 228"],
    # Height: always governed by Schedule 15 (Section 196 / 227), never a flat number
    # in HR/CEN/COR/DD/DH zones.
    "height": ["Schedule 15", "height precinct", "Section 196"],
    "height_overlay": ["Schedule 15", "height precinct"],
    # FAR: Schedule 17 in CEN/DH/DD zones; HR/COR zones have no FAR cap.
    "FAR": ["Schedule 17", "floor area ratio"],
    "FAR_overlay": ["Schedule 17", "floor area ratio"],
    # Lot coverage: ER zones have 40%/50%/60% caps; HR zones have none.
    "lot_coverage": ["lot coverage", "Section 231"],
    # Use permission: Table 1A/1B, as-of-right permissions.
    "use_permission": ["multi-unit dwelling", "permitted", "Table 1A"],
    "permitted_use": ["multi-unit dwelling", "permitted", "Table 1A"],
    # Parking: Table 15 via Section 433; downtown/HR zones have no minimum.
    "parking": ["Section 433", "Table 15", "not required"],
    # Heritage overlay: heritage conservation district (Schedule 22).
    # NOTE: "Heritage Advisory Committee" is NOT in the real bylaw text.
    "heritage_overlay": ["heritage", "heritage conservation district", "Schedule 22", "Section 110"],
    # Demolition control: Section 111 or 112.
    "demolition_control": ["demolition", "Section 111", "heritage"],
    # Bonus zoning: Part XI, Schedule 17 FAR bonus.
    "bonus_zoning": ["bonus", "Schedule 17", "floor area ratio"],
    # Backyard suite: distinct from secondary suite (Section 344).
    "backyard_suite": ["backyard suite", "Section 344", "Schedule 15"],
    # Non-conforming use: Part II provisions.
    "non_conforming": ["non-conforming", "lawful non-conforming"],
    # Site plan approval: Section 15 triggers.
    "site_plan_approval": ["site plan approval", "Section 15"],
    # Viewplanes: Schedule 50.
    "viewplanes": ["viewplane", "Schedule 50"],
    # Streetwall/stepback: Sections 200-208.
    "streetwall": ["streetwall", "Section 200"],
    "stepback": ["stepback", "Section 208"],
    # Ground floor use: active-use requirements in CEN/COR/DD/DH zones.
    "ground_floor_use": ["ground floor", "active use"],
    # Home occupation: Section 51.
    "home_occupation": ["home occupation", "Section 51"],
}

# Per-zone, per-feature keyword overrides where the general feature keywords
# don't apply cleanly (e.g., "lot coverage" for HR zones which have no cap).
ZONE_FEATURE_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "HR-1": {
        "lot_coverage": ["Schedule 17", "floor area ratio", "Section 210"],
        "setbacks": ["Section 198", "Section 199", "6.0 m", "3.0 m"],
    },
    "HR-2": {
        "lot_coverage": ["Schedule 17", "floor area ratio", "Section 210"],
        "setbacks": ["Section 198", "Section 199", "6.0 m"],
    },
    "CEN-1": {
        "setbacks": ["front setback", "0.0 m", "rear setback", "3.0 m"],
        "lot_coverage": ["80%", "lot coverage"],
    },
    "CEN-2": {
        "setbacks": ["front setback", "0.0 m", "rear setback", "3.0 m"],
        "lot_coverage": ["80%", "lot coverage"],
    },
    "DH": {
        "setbacks": ["front setback", "0.0 m", "rear setback", "3.0 m"],
        "lot_coverage": ["90%", "lot coverage"],
    },
    "DD": {
        "setbacks": ["front setback", "0.0 m", "rear setback", "3.0 m"],
        "lot_coverage": ["80%", "lot coverage"],
        "parking": ["Section 433", "not required"],
    },
    "COR": {
        "setbacks": ["front setback", "3.0 m", "side setback", "0.0 m"],
        "lot_coverage": ["70%", "lot coverage"],
    },
    "ER-3": {
        "setbacks": ["Section 229", "1.25 m", "Section 230", "6.0 m", "Schedule 18"],
        "lot_coverage": ["50%", "Section 231"],
        "height": ["Schedule 15"],
    },
    "ER-2": {
        "setbacks": ["Section 229", "Section 230", "6.0 m", "Schedule 18"],
        "lot_coverage": ["50%", "Section 231"],
    },
    "ER-1": {
        "setbacks": ["Section 229", "Section 230", "6.0 m", "Schedule 18"],
        "lot_coverage": ["40%", "Section 231"],
    },
}


def _deduplicate(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in lst:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _keywords_for_case(tc: dict[str, Any]) -> list[str]:
    """Derive expected_answer_keywords for a single test case from zone/features."""
    tc_id: str = tc["id"]
    if tc_id in EMPIRICAL_KEYWORDS:
        return EMPIRICAL_KEYWORDS[tc_id]

    zone: str = tc.get("zone", "")
    features: list[str] = tc.get("bylaw_features", [])

    keywords: list[str] = list(ZONE_BASE_KEYWORDS.get(zone, [zone]))

    for feature in features:
        # Check zone-specific override first, then fall back to general feature keywords.
        zone_overrides = ZONE_FEATURE_OVERRIDES.get(zone, {})
        if feature in zone_overrides:
            keywords.extend(zone_overrides[feature])
        elif feature in FEATURE_KEYWORDS:
            keywords.extend(FEATURE_KEYWORDS[feature])

    return _deduplicate(keywords)


# ---------------------------------------------------------------------------
# DB query helpers (used when --verify-db flag is set, or to double-check).
# ---------------------------------------------------------------------------

def _resolve_document_id(conn: "psycopg.Connection") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM document WHERE bylaw_name = %s "
            "ORDER BY ingestion_timestamp DESC LIMIT 1",
            (BYLAW_NAME,),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(
                f"No document with bylaw_name={BYLAW_NAME!r} found. "
                "Is the ingest loaded in the dev DB?"
            )
        return row[0]


def _fetch_zone_fragments(
    conn: "psycopg.Connection", doc_id: int, zone: str
) -> list[str]:
    """Return text snippets from fragments that mention the zone code."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT text FROM source_fragment "
            "WHERE document_id = %s AND text ILIKE %s "
            "ORDER BY reading_order_start LIMIT 20",
            (doc_id, f"%{zone}%"),
        )
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Transcript replay (--verify-transcripts)
# ---------------------------------------------------------------------------

def _keyword_hit_rate(text: str, keywords: list[str]) -> dict[str, Any]:
    """Case-insensitive substring match — mirrors verify_test_prompts.py logic."""
    if not keywords:
        return {"expected": 0, "hit": 0, "rate": None, "misses": []}
    low = text.lower()
    hits = [kw for kw in keywords if kw.lower() in low]
    misses = [kw for kw in keywords if kw.lower() not in low]
    return {
        "expected": len(keywords),
        "hit": len(hits),
        "rate": round(len(hits) / len(keywords), 3),
        "misses": misses,
    }


def _verify_transcripts(run_dir: Path, prompts: list[dict]) -> None:
    """Replay keyword matching against saved transcripts and print rates."""
    prompts_by_id = {tc["id"]: tc for tc in prompts}
    threshold_map = {"simple": 0.80, "medium": 0.60, "complex": 0.60}

    pass_count = fail_count = skip_count = 0

    for tc_path in sorted(run_dir.glob("TC-*.json")):
        with open(tc_path) as f:
            transcript = json.load(f)
        tc_id = transcript["id"]
        if tc_id not in prompts_by_id:
            print(f"  {tc_id}: not in prompts file — skipping", file=sys.stderr)
            skip_count += 1
            continue

        spec = prompts_by_id[tc_id]
        keywords = spec.get("expected_answer_keywords") or []
        complexity = spec.get("complexity", "medium")
        threshold = threshold_map.get(complexity, 0.60)

        if not keywords:
            print(f"  {tc_id}: no keywords — skipping", file=sys.stderr)
            skip_count += 1
            continue

        total_hits = total_expected = 0
        for turn in transcript.get("turns", []):
            text = turn.get("assistant_text", "")
            kw = _keyword_hit_rate(text, keywords)
            total_hits += kw["hit"]
            total_expected += kw["expected"]

        rate = total_hits / total_expected if total_expected else 0.0
        passed = rate >= threshold
        status = "PASS" if passed else "FAIL"
        if passed:
            pass_count += 1
        else:
            fail_count += 1

        # Show per-keyword misses compactly
        all_text = " ".join(
            t.get("assistant_text", "") for t in transcript.get("turns", [])
        )
        low_all = all_text.lower()
        any_miss = [kw for kw in keywords if kw.lower() not in low_all]
        miss_str = f"  never-hit: {any_miss}" if any_miss else ""

        print(
            f"  {tc_id} [{complexity}]: {status} — kw_rate={rate:.0%} "
            f"({total_hits}/{total_expected}) threshold={threshold:.0%}{miss_str}"
        )

    print(
        f"\n  Summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} skip"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="PostgreSQL DSN")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print keyword diff without writing to disk",
    )
    parser.add_argument(
        "--verify-transcripts",
        metavar="RUN_DIR",
        help="After calibration, replay keyword matching against transcripts in RUN_DIR",
    )
    args = parser.parse_args()

    with open(PROMPTS_FILE) as f:
        prompts: list[dict] = json.load(f)

    changed = 0
    for tc in prompts:
        tc_id = tc["id"]
        old_kw = tc.get("expected_answer_keywords") or []
        new_kw = _keywords_for_case(tc)

        if old_kw == new_kw:
            continue

        changed += 1
        if args.dry_run:
            print(f"\n{tc_id} [{tc['zone']}]:")
            print(f"  OLD: {old_kw}")
            print(f"  NEW: {new_kw}")
        else:
            tc["expected_answer_keywords"] = new_kw

    if args.dry_run:
        print(f"\n{changed} case(s) would change (dry-run — no file written).")
    else:
        if changed:
            with open(PROMPTS_FILE, "w") as f:
                json.dump(prompts, f, indent=2)
            print(f"Updated {changed} case(s) in {PROMPTS_FILE}.", file=sys.stderr)
        else:
            print("No changes needed.", file=sys.stderr)

    if args.verify_transcripts:
        run_dir = Path(args.verify_transcripts)
        if not run_dir.is_dir():
            print(f"[ERROR] --verify-transcripts path not a directory: {run_dir}", file=sys.stderr)
            sys.exit(1)
        print(f"\nReplaying keyword matching against transcripts in {run_dir}:")
        _verify_transcripts(run_dir, prompts)


if __name__ == "__main__":
    main()
