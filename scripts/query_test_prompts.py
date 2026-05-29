#!/usr/bin/env python3
"""
Query tool for the Regional Centre test prompt database.

Usage examples:
  # Filter by zone
  python scripts/query_test_prompts.py --zone ER-1

  # Filter by persona
  python scripts/query_test_prompts.py --persona homeowner

  # Filter by tag
  python scripts/query_test_prompts.py --tag renovation

  # Filter by bylaw feature
  python scripts/query_test_prompts.py --bylaw-feature setbacks

  # Filter by complexity and liability
  python scripts/query_test_prompts.py --complexity simple --liability low

  # Combine filters
  python scripts/query_test_prompts.py --zone CEN-1 --persona real_estate_developer

  # Output formats
  python scripts/query_test_prompts.py --zone HR-1 --output json
  python scripts/query_test_prompts.py --persona homeowner --output ids
  python scripts/query_test_prompts.py --output table

  # List all unique values for a field (useful for agents discovering valid filter values)
  python scripts/query_test_prompts.py --list-zones
  python scripts/query_test_prompts.py --list-personas
  python scripts/query_test_prompts.py --list-tags
  python scripts/query_test_prompts.py --list-bylaw-features

  # Retrieve a specific test case by ID
  python scripts/query_test_prompts.py --id TC-001
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROMPTS_FILE = Path(__file__).parent.parent / "evals" / "regional_centre_test_prompts.json"


def load_prompts() -> list[dict[str, Any]]:
    with open(PROMPTS_FILE) as f:
        return json.load(f)


def filter_prompts(
    prompts: list[dict],
    *,
    zone: str | None = None,
    persona: str | None = None,
    subtype: str | None = None,
    tag: str | None = None,
    bylaw_feature: str | None = None,
    complexity: str | None = None,
    liability: str | None = None,
    prompt_id: str | None = None,
) -> list[dict]:
    results = prompts

    if prompt_id:
        results = [p for p in results if p["id"] == prompt_id]

    if zone:
        results = [p for p in results if p["zone"].upper() == zone.upper()]

    if persona:
        results = [
            p for p in results
            if p["persona"]["type"] == persona
            or persona.lower() in p["persona"]["type"].lower()
        ]

    if subtype:
        results = [
            p for p in results
            if p["persona"].get("subtype") and
            subtype.lower() in (p["persona"]["subtype"] or "").lower()
        ]

    if tag:
        results = [p for p in results if tag.lower() in [t.lower() for t in p["tags"]]]

    if bylaw_feature:
        results = [
            p for p in results
            if bylaw_feature.lower() in [f.lower() for f in p["bylaw_features"]]
        ]

    if complexity:
        results = [p for p in results if p["complexity"] == complexity.lower()]

    if liability:
        results = [p for p in results if p["liability"] == liability.lower()]

    return results


def format_table(prompts: list[dict]) -> str:
    if not prompts:
        return "No test cases matched the given filters."

    header = f"{'ID':<8} {'Zone':<8} {'Persona':<30} {'Complexity':<12} {'Liability':<10} {'Turns':<6} {'Title'}"
    separator = "-" * 100
    rows = [header, separator]
    for p in prompts:
        persona_label = p["persona"]["type"]
        if p["persona"].get("subtype"):
            persona_label += f" ({p['persona']['subtype']})"
        turns = len(p["turns"])
        rows.append(
            f"{p['id']:<8} {p['zone']:<8} {persona_label:<30} {p['complexity']:<12} {p['liability']:<10} {turns:<6} {p['title']}"
        )
    rows.append(separator)
    rows.append(f"{len(prompts)} test case(s) matched.")
    return "\n".join(rows)


def format_ids(prompts: list[dict]) -> str:
    return "\n".join(p["id"] for p in prompts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query the Regional Centre test prompt database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Filters
    parser.add_argument("--zone", help="Filter by zone code (e.g. ER-1, CEN-1, DH)")
    parser.add_argument("--persona", help="Filter by persona type (e.g. homeowner, real_estate_developer)")
    parser.add_argument("--subtype", help="Filter by persona subtype (e.g. architect, planner)")
    parser.add_argument("--tag", help="Filter by tag (e.g. renovation, heritage, multi_unit)")
    parser.add_argument("--bylaw-feature", dest="bylaw_feature",
                        help="Filter by bylaw feature (e.g. setbacks, FAR, height)")
    parser.add_argument("--complexity", choices=["simple", "medium", "complex"],
                        help="Filter by complexity level")
    parser.add_argument("--liability", choices=["low", "medium", "high"],
                        help="Filter by liability level")
    parser.add_argument("--id", dest="prompt_id", help="Retrieve a specific test case by ID")

    # List modes
    parser.add_argument("--list-zones", action="store_true", help="List all unique zone codes in the database")
    parser.add_argument("--list-personas", action="store_true", help="List all unique persona types")
    parser.add_argument("--list-tags", action="store_true", help="List all unique tags")
    parser.add_argument("--list-bylaw-features", action="store_true", help="List all unique bylaw features")

    # Output format
    parser.add_argument(
        "--output",
        choices=["table", "json", "ids", "turns"],
        default="table",
        help="Output format: table (default), json (full records), ids (ID list only), turns (conversation turns only)",
    )

    args = parser.parse_args()
    prompts = load_prompts()

    # List modes (no filtering applied)
    if args.list_zones:
        zones = sorted(set(p["zone"] for p in prompts))
        print("\n".join(zones))
        return

    if args.list_personas:
        personas = sorted(set(
            p["persona"]["type"] + (f" ({p['persona']['subtype']})" if p["persona"].get("subtype") else "")
            for p in prompts
        ))
        print("\n".join(personas))
        return

    if args.list_tags:
        tags = sorted(set(tag for p in prompts for tag in p["tags"]))
        print("\n".join(tags))
        return

    if args.list_bylaw_features:
        features = sorted(set(f for p in prompts for f in p["bylaw_features"]))
        print("\n".join(features))
        return

    # Apply filters
    results = filter_prompts(
        prompts,
        zone=args.zone,
        persona=args.persona,
        subtype=args.subtype,
        tag=args.tag,
        bylaw_feature=args.bylaw_feature,
        complexity=args.complexity,
        liability=args.liability,
        prompt_id=args.prompt_id,
    )

    # Output
    if args.output == "json":
        print(json.dumps(results, indent=2))
    elif args.output == "ids":
        print(format_ids(results))
    elif args.output == "turns":
        if not results:
            print("No test cases matched.")
            sys.exit(0)
        for tc in results:
            print(f"\n{'='*60}")
            print(f"[{tc['id']}] {tc['title']}")
            print(f"Zone: {tc['zone']} | Persona: {tc['persona']['type']} | Complexity: {tc['complexity']}")
            print(f"{'='*60}")
            for turn in tc["turns"]:
                print(f"\n[Turn {turn['turn']} — {turn['role']}]")
                print(turn["message"])
    else:
        print(format_table(results))


if __name__ == "__main__":
    main()
