#!/usr/bin/env python3
"""
Generator for Regional Centre bylaw test case prompts.

This script generates new test cases following the schema defined in
evals/regional_centre_test_prompts.json. It uses the Claude API to produce
realistic multi-turn conversation prompts given a spec (zone, persona, complexity, etc.).

Usage:
  # Generate a single test case interactively
  python scripts/generate_regional_centre_test_prompts.py \
    --zone CEN-1 \
    --persona real_estate_developer \
    --complexity complex \
    --liability high \
    --tags new_construction mixed_use \
    --bylaw-features FAR height_overlay setbacks parking \
    --address "1500 Argyle Street, Halifax, NS" \
    --title "Developer tower feasibility in CEN-1"

  # Generate a batch from a spec file and append to the prompts database
  python scripts/generate_regional_centre_test_prompts.py \
    --spec-file scripts/test_prompt_specs.json \
    --append

  # Dry-run: print the generated case without writing
  python scripts/generate_regional_centre_test_prompts.py \
    --zone HR-2 --persona homeowner --complexity simple --liability low \
    --dry-run

Environment variables required:
  ANTHROPIC_API_KEY — Claude API key for agentic generation mode.
  If not set, the script falls back to a template-only stub mode that
  produces a skeleton with placeholder messages.

Output:
  By default, prints the generated JSON test case to stdout.
  With --append, merges into evals/regional_centre_test_prompts.json and
  assigns the next sequential TC-NNN ID.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROMPTS_FILE = Path(__file__).parent.parent / "evals" / "regional_centre_test_prompts.json"
BYLAW_FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "halifax_regional_centre_lub.txt"

ZONES = ["CEN-1", "CEN-2", "COR", "DD", "DH", "ER-1", "ER-2", "ER-3", "HR-1", "HR-2", "INS", "RPK"]
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


def build_generation_prompt(spec: dict) -> str:
    bylaw_ctx = load_bylaw_context()
    turns_count = spec.get("turns", 3)
    return f"""
Generate {turns_count} user conversation turns for a bylaw AI assistant test case.

**Scenario spec:**
- Title: {spec.get("title", "Untitled")}
- Zone: {spec["zone"]}
- Address: {spec.get("address", f"[address in {spec['zone']} zone, Halifax/Dartmouth Regional Centre]")}
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


def generate_turns_via_api(spec: dict) -> list[dict]:
    try:
        import anthropic
    except ImportError:
        print("[WARNING] anthropic package not installed. Falling back to stub mode.", file=sys.stderr)
        return generate_turns_stub(spec)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[WARNING] ANTHROPIC_API_KEY not set. Falling back to stub mode.", file=sys.stderr)
        return generate_turns_stub(spec)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_generation_prompt(spec)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    content = response.content[0].text.strip()
    # Strip markdown code fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(content)


def generate_turns_stub(spec: dict) -> list[dict]:
    """Template stub when API is unavailable — produces placeholder messages."""
    turns_count = spec.get("turns", 3)
    zone = spec["zone"]
    address = spec.get("address", f"[address], in the {zone} zone")
    bylaw_features = spec.get("bylaw_features", ["setbacks"])

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


def build_test_case(spec: dict, turns: list[dict], case_id: str) -> dict:
    return {
        "id": case_id,
        "title": spec.get("title", f"Test case for {spec['zone']}"),
        "persona": {
            "type": spec["persona"],
            "subtype": spec.get("subtype"),
            "description": spec.get("description", ""),
        },
        "address": spec.get("address", ""),
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
    parser.add_argument("--address", help="Address string for the scenario")
    parser.add_argument("--title", help="Title for the test case")
    parser.add_argument("--description", help="Persona description")
    parser.add_argument("--turns", type=int, default=3, help="Number of conversation turns to generate")
    parser.add_argument("--notes", help="Notes about the test case")
    parser.add_argument("--expected-references", nargs="*", dest="expected_bylaw_references", default=[])
    parser.add_argument("--expected-keywords", nargs="*", dest="expected_answer_keywords", default=[])
    parser.add_argument("--expected-topics", nargs="*", dest="expected_topics", default=[])

    # Batch mode
    parser.add_argument("--spec-file", help="Path to a JSON file containing an array of spec objects")

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
    elif args.zone and args.persona:
        specs = [{
            "zone": args.zone,
            "persona": args.persona,
            "subtype": args.subtype,
            "complexity": args.complexity,
            "liability": args.liability,
            "tags": args.tags,
            "bylaw_features": args.bylaw_features,
            "address": args.address or "",
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

    for spec in specs:
        counter += 1
        case_id = f"TC-{counter:03d}"
        print(f"[{case_id}] Generating turns for zone={spec['zone']} persona={spec['persona']} ...", file=sys.stderr)

        if args.dry_run:
            turns = generate_turns_stub(spec)
        else:
            turns = generate_turns_via_api(spec)

        tc = build_test_case(spec, turns, case_id)
        generated.append(tc)

    if args.dry_run or not args.append:
        print(json.dumps(generated if len(generated) > 1 else generated[0], indent=2))
    else:
        merged = existing + generated
        with open(PROMPTS_FILE, "w") as f:
            json.dump(merged, f, indent=2)
        print(f"Appended {len(generated)} test case(s) to {PROMPTS_FILE}.", file=sys.stderr)
        print(json.dumps([tc["id"] for tc in generated]))


if __name__ == "__main__":
    main()
