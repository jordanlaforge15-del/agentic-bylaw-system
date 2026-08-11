#!/usr/bin/env python3
"""
Generator for Regional Centre bylaw test case prompts.

This script generates new test cases following the schema defined in
evals/regional_centre_test_prompts.json. It shells out to the `claude` CLI
(Claude Code's headless mode, `claude -p`) to produce realistic multi-turn
conversation prompts given a spec (zone, persona, complexity, etc.). This
bills generation against the operator's Claude Max plan instead of paid
Anthropic API tokens.

The address is DERIVED FROM THE ZONE, never supplied (ABS-467). The operator
used to pass `--zone` and `--address` as two independent inputs and the model
wrote a conversation around both; nothing checked that the address was in the
zone, and 17 of the first 20 cases were wrong. `--address` is gone. The zone
now picks a real parcel, the parcel yields a real civic address, and that
address is pushed back through the production `get_address_profile` path
before the case is written — so a new case cannot reintroduce the mismatch.
`--on-street` biases *which* real address is chosen when the scenario leans on
a particular street; it cannot override the verification.

Usage:
  # Generate a single test case interactively
  python scripts/generate_regional_centre_test_prompts.py \
    --zone CEN-1 \
    --persona real_estate_developer \
    --complexity complex \
    --liability high \
    --tags new_construction mixed_use \
    --bylaw-features FAR height_overlay setbacks parking \
    --title "Developer tower feasibility in CEN-1"

  # Generate a batch from a spec file and append to the prompts database
  python scripts/generate_regional_centre_test_prompts.py \
    --spec-file scripts/test_prompt_specs.json \
    --append

  # Dry-run: print the generated case without writing
  python scripts/generate_regional_centre_test_prompts.py \
    --zone HR-2 --persona homeowner --complexity simple --liability low \
    --dry-run

Requirements:
  `claude` CLI must be on PATH (Claude Code's headless mode is invoked via
  `claude -p`). If the binary is missing, the script falls back to a
  template-only stub mode that produces a skeleton with placeholder messages.

  A Postgres database carrying the HRM zoning + parcels datasets, and
  GOOGLE_MAPS_API_KEY, because address derivation resolves against both.

Output:
  By default, prints the generated JSON test case to stdout.
  With --append, merges into evals/regional_centre_test_prompts.json and
  assigns the next sequential TC-NNN ID.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zone_address_picker import (  # noqa: E402
    DEFAULT_DB_URL,
    REGIONAL_CENTRE_BYLAW_AREA_ID,
    ZoneAddress,
    _build_reverse_geocoder,
    pick_address_for_zone,
)

PROMPTS_FILE = Path(__file__).parent.parent / "evals" / "regional_centre_test_prompts.json"
BYLAW_FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "halifax_regional_centre_lub.txt"

# Every zone code the Regional Centre zoning schedule actually maps — all 25
# of them, from `zone_address_picker.zone_codes()` against bylaw_area_id 23.
# The eval suite exercises 11; the rest are listed because a case can only be
# written for a zone that exists on the map, and this is that list.
#
# ER-1 is deliberately absent. The by-law defines it (Part I s.30) but the
# schedule maps no ER-1 polygon anywhere, so an ER-1 case could never have its
# address confirmed — which is exactly how TC-017 came to be anchored on an
# address in no zone at all.
ZONES = [
    "CDD-1", "CDD-2", "CEN-1", "CEN-2", "CH-1", "CH-2", "CLI", "COR", "DD",
    "DH", "DND", "ER-2", "ER-3", "H", "HCD-SV", "HR-1", "HR-2", "HRI", "INS",
    "LI", "PCF", "RPK", "UC-1", "UC-2", "WA",
]
PERSONA_TYPES = [
    "homeowner",
    "real_estate_developer",
    "real_estate_law_professional",
    "realtor",
    "architectural_consultant",
    "city_agent",
]
PERSONA_SUBTYPES = {
    "architectural_consultant": ["architect", "planner", "drafter"],
    "city_agent": ["planner", "building_official"],
}

SYSTEM_PROMPT = """You are a test-case author for a municipal bylaw AI assistant.
Your job is to write realistic multi-turn conversation prompts that simulate how
real users with the given persona would interact with the system.

Rules:
- Write only from the USER's perspective (the user's turns in the conversation).
- Each message should be a natural, specific question referencing the address,
  zone, and scenario context.
- Do not write the assistant's replies — only the user turns.
- Start simple and escalate complexity across turns.
- Persona language: homeowners are informal/practical; developers are business-focused;
  lawyers are precise/liability-aware; architects are technical; city agents are procedural.
- Reference specific bylaw parameters (setbacks, FAR, height, permitted use, parking)
  naturally — the user knows what they're looking for but frames it conversationally.
- Every turn must advance the question or deepen the inquiry. No redundant turns.
"""


def load_bylaw_context() -> str:
    if BYLAW_FIXTURE.exists():
        return BYLAW_FIXTURE.read_text()
    return ""


def load_existing_prompts() -> list[dict]:
    if PROMPTS_FILE.exists():
        with open(PROMPTS_FILE) as f:
            return json.load(f)
    return []


def next_id(existing: list[dict]) -> str:
    if not existing:
        return "TC-001"
    nums = []
    for p in existing:
        try:
            nums.append(int(p["id"].replace("TC-", "")))
        except ValueError:
            pass
    return f"TC-{max(nums) + 1:03d}"


def derive_address(session: Session, spec: dict, **kwargs: Any) -> ZoneAddress:
    """Pick a real address in ``spec["zone"]`` and prove it resolves back to it.

    The generator's one hard rule (ABS-467): a spec names a zone, and the
    address comes out of the zoning data. Raises rather than falling back to
    an unverified string — a case with an unconfirmed address grades the
    advisor against a zone the address is not in, which is worse than no case
    at all.
    """
    picked = pick_address_for_zone(session, spec["zone"], **kwargs)
    if picked is None:
        raise RuntimeError(
            f"No address in zone {spec['zone']!r} could be verified through "
            "the production path. "
            "get_address_profile. Widen --candidates, drop --on-street, or "
            "check that the zoning and parcel datasets are ingested. Zones "
            "the schedule does not map (ER-1) can never succeed here."
        )
    return picked


def build_generation_prompt(spec: dict) -> str:
    bylaw_ctx = load_bylaw_context()
    turns_count = spec.get("turns", 3)
    return f"""
Generate {turns_count} user conversation turns for a bylaw AI assistant test case.

**Scenario spec:**
- Title: {spec.get("title", "Untitled")}
- Zone: {spec["zone"]}
- Address: {spec["address"]} (verified to resolve to the {spec["zone"]} zone —
  use it verbatim in turn 1 and do not invent a different one)
- Persona: {spec["persona"]} {("(" + spec["subtype"] + ")") if spec.get("subtype") else ""}
- Complexity: {spec.get("complexity", "medium")}
- Liability: {spec.get("liability", "medium")}
- Tags: {", ".join(spec.get("tags", []))}
- Bylaw features to cover: {", ".join(spec.get("bylaw_features", []))}

**Bylaw context (excerpt):**
{bylaw_ctx[:3000] if bylaw_ctx else "[bylaw context not available — use general knowledge of the Halifax Regional Centre Land Use By-law]"}

**Output format:**
Return ONLY a JSON array of turn objects, no prose before or after.
Each object must have:
  - "turn": integer (1-based)
  - "role": "user"
  - "message": string (the user's message text)

Example output:
[
  {{"turn": 1, "role": "user", "message": "I own a house at 123 Example St in the ER-1 zone..."}},
  {{"turn": 2, "role": "user", "message": "What about the side setback requirement?"}}
]
"""


def _extract_json_array(text: str) -> list[dict]:
    """Pull the first JSON array of turn-objects out of a free-form text response.

    `claude -p` (text mode) usually returns the bare array we asked for, but it can
    occasionally wrap it in markdown fences or prepend a sentence. Be tolerant of
    both while still raising loudly if no array is found — silent corruption of the
    corpus is worse than a hard fail.
    """
    content = text.strip()
    # Strip markdown code fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        # Drop first fence line and (if present) trailing fence line
        if lines and lines[-1].strip() == "```":
            content = "\n".join(lines[1:-1])
        else:
            content = "\n".join(lines[1:])
        content = content.strip()
    # If model added a preamble, locate the first '[' and matching final ']'
    if not content.startswith("["):
        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1 or end < start:
            raise ValueError(f"No JSON array found in claude -p output:\n{text}")
        content = content[start : end + 1]
    return json.loads(content)


def generate_turns_via_api(spec: dict) -> list[dict]:
    """Generate user-turn objects via `claude -p` (Claude Code headless mode).

    Function name is preserved for backward compatibility with callers, but the
    transport is now the local `claude` CLI billed against the operator's
    Claude Max plan rather than the paid Anthropic API.
    """
    if shutil.which("claude") is None:
        print(
            "[WARNING] `claude` CLI not found on PATH. Install Claude Code "
            "(https://docs.claude.com/claude-code) or fall back to stub mode.",
            file=sys.stderr,
        )
        return generate_turns_stub(spec)

    prompt = build_generation_prompt(spec)
    # `claude -p` accepts the prompt as a positional arg. We pass the system prompt
    # via --append-system-prompt so the test-case authoring rules are in force, and
    # request text output (which still contains the JSON array we asked for in the
    # prompt itself — keeps parsing identical to the old API path).
    cmd = [
        "claude",
        "-p",
        prompt,
        "--append-system-prompt",
        SYSTEM_PROMPT,
        "--output-format",
        "text",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"[ERROR] `claude -p` exited with code {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}",
            file=sys.stderr,
        )
        raise RuntimeError(f"claude -p failed (exit {result.returncode})")

    return _extract_json_array(result.stdout)


def generate_turns_stub(spec: dict) -> list[dict]:
    """Template stub when API is unavailable — produces placeholder messages."""
    turns_count = spec.get("turns", 3)
    zone = spec["zone"]
    address = spec["address"]
    # `or`, not a dict default: --bylaw-features defaults to an empty list, so
    # the key is present and the default never fired. Every --dry-run without
    # explicit features died on an IndexError below.
    bylaw_features = spec.get("bylaw_features") or ["setbacks"]

    turns = []
    for i in range(1, turns_count + 1):
        if i == 1:
            feature = bylaw_features[0] if bylaw_features else "permitted uses"
            msg = (
                f"[STUB — replace with real message] I'm looking at {address}. "
                f"Can you tell me about the {feature} in the {zone} zone?"
            )
        else:
            feature = bylaw_features[min(i - 1, len(bylaw_features) - 1)]
            msg = (
                f"[STUB — replace with real message] Follow-up question about "
                f"{feature} in the {zone} zone."
            )
        turns.append({"turn": i, "role": "user", "message": msg})
    return turns


def build_test_case(
    spec: dict, turns: list[dict], case_id: str, zone_address: ZoneAddress
) -> dict:
    """Assemble the case, recording the evidence its address rests on.

    ``address_resolution`` is not decoration: it is what lets a reader (and
    ``tests/test_eval_address_zones.py``) tell a case grounded on a matched
    building from one grounded on a point the geocoder estimated.
    """
    return {
        "id": case_id,
        "title": spec.get("title", f"Test case for {spec['zone']}"),
        "persona": {
            "type": spec["persona"],
            "subtype": spec.get("subtype"),
            "description": spec.get("description", ""),
        },
        "address": zone_address.address,
        "address_resolution": {
            "resolved_zone": zone_address.resolved_zone,
            "resolution_quality": zone_address.resolution_quality,
            "location_type": zone_address.location_type,
            "location_confidence": zone_address.location_confidence,
            "location_resolver": zone_address.location_resolver,
            "parcel_pid": zone_address.parcel_pid,
        },
        "zone": spec["zone"],
        "complexity": spec.get("complexity", "medium"),
        "liability": spec.get("liability", "medium"),
        "tags": spec.get("tags", []),
        "bylaw_features": spec.get("bylaw_features", []),
        "turns": turns,
        "expected_bylaw_references": spec.get("expected_bylaw_references", []),
        "expected_answer_keywords": spec.get("expected_answer_keywords", []),
        "expected_topics": spec.get("expected_topics", []),
        "notes": spec.get("notes", "Generated by generate_regional_centre_test_prompts.py"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Regional Centre bylaw test case prompts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Single-case generation args
    parser.add_argument("--zone", choices=ZONES, help="Zone code for the test case")
    parser.add_argument("--persona", choices=PERSONA_TYPES, help="Persona type")
    parser.add_argument("--subtype", help="Persona subtype (e.g. architect, planner)")
    parser.add_argument("--complexity", choices=["simple", "medium", "complex"], default="medium")
    parser.add_argument("--liability", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--tags", nargs="*", default=[])
    parser.add_argument("--bylaw-features", nargs="*", dest="bylaw_features", default=[])
    # No --address. The zone picks it; see the module docstring (ABS-467).
    parser.add_argument(
        "--on-street",
        dest="on_street",
        help="Prefer a derived address on this street (still zone-verified).",
    )
    parser.add_argument("--title", help="Title for the test case")
    parser.add_argument("--description", help="Persona description")
    parser.add_argument("--turns", type=int, default=3, help="Number of conversation turns to generate")
    parser.add_argument("--notes", help="Notes about the test case")
    parser.add_argument("--expected-references", nargs="*", dest="expected_bylaw_references", default=[])
    parser.add_argument("--expected-keywords", nargs="*", dest="expected_answer_keywords", default=[])
    parser.add_argument("--expected-topics", nargs="*", dest="expected_topics", default=[])

    # Batch mode
    parser.add_argument("--spec-file", help="Path to a JSON file containing an array of spec objects")

    # Address derivation
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB_URL))
    parser.add_argument("--candidates", type=int, default=25,
                        help="Parcels to try per zone before giving up.")
    parser.add_argument("--allow-interpolated", action="store_true",
                        help="Accept an interpolated address when no ROOFTOP one exists.")

    # Output
    parser.add_argument("--append", action="store_true",
                        help="Append generated case(s) to the prompts database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print generated JSON without writing to disk")

    args = parser.parse_args()

    existing = load_existing_prompts()

    specs: list[dict] = []

    if args.spec_file:
        with open(args.spec_file) as f:
            specs = json.load(f)
        supplied = [s.get("zone") for s in specs if s.get("address")]
        if supplied:
            parser.error(
                "Spec files must not carry an 'address' — it is derived from "
                f"'zone' (ABS-467). Offending zones: {', '.join(map(str, supplied))}. "
                "Use 'on_street' to bias which real address is chosen."
            )
    elif args.zone and args.persona:
        specs = [{
            "zone": args.zone,
            "persona": args.persona,
            "subtype": args.subtype,
            "complexity": args.complexity,
            "liability": args.liability,
            "tags": args.tags,
            "bylaw_features": args.bylaw_features,
            "on_street": args.on_street,
            "title": args.title or f"Test case for {args.zone} — {args.persona}",
            "description": args.description or "",
            "turns": args.turns,
            "notes": args.notes or "",
            "expected_bylaw_references": args.expected_bylaw_references,
            "expected_answer_keywords": args.expected_answer_keywords,
            "expected_topics": args.expected_topics,
        }]
    else:
        parser.error("Provide either --zone + --persona (single case) or --spec-file (batch).")

    generated: list[dict] = []
    counter = len(existing)

    engine = create_engine(args.db_url)
    reverse_geocoder = _build_reverse_geocoder()
    # Addresses already spoken for, so a batch cannot hand the same one to two
    # cases and so a new case never collides with an existing one.
    taken = {c.get("address", "").strip().lower() for c in existing}

    with Session(engine) as session:
        for spec in specs:
            counter += 1
            case_id = f"TC-{counter:03d}"
            print(f"[{case_id}] Deriving an address in zone={spec['zone']} ...", file=sys.stderr)
            zone_address = derive_address(
                session,
                spec,
                reverse_geocoder=reverse_geocoder,
                bylaw_area_id=REGIONAL_CENTRE_BYLAW_AREA_ID,
                candidates=args.candidates,
                allow_interpolated=args.allow_interpolated,
                exclude=taken,
                on_street=spec.get("on_street"),
            )
            session.commit()
            taken.add(zone_address.address.strip().lower())
            # The model writes the conversation around the *derived* address,
            # which is why this assignment happens before prompt building.
            spec["address"] = zone_address.address
            print(
                f"[{case_id}] {zone_address.address} "
                f"({zone_address.resolution_quality}); generating turns for "
                f"persona={spec['persona']} ...",
                file=sys.stderr,
            )

            if args.dry_run:
                turns = generate_turns_stub(spec)
            else:
                turns = generate_turns_via_api(spec)

            generated.append(build_test_case(spec, turns, case_id, zone_address))

    if args.dry_run or not args.append:
        print(json.dumps(generated if len(generated) > 1 else generated[0], indent=2))
    else:
        merged = existing + generated
        with open(PROMPTS_FILE, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")
        print(f"Appended {len(generated)} test case(s) to {PROMPTS_FILE}.", file=sys.stderr)
        print(json.dumps([tc["id"] for tc in generated]))


if __name__ == "__main__":
    main()
