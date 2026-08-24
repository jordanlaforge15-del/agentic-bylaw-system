#!/usr/bin/env python3
"""Measure the ABS-521 blast radius: clauses whose tree parent is not their section.

ABS-521 asked a question a fix is not allowed to skip — "is this s.333, or every
``(a)`` clause?" — and the answer has to be a number somebody can re-derive, not
a sentence in a commit message. This script is that number.

What it counts
--------------
Every fragment carries two independent statements of where it sits:

``citation_path``
    How it is *cited*. ABS-488 repathed clauses onto the container that scopes
    them, so ``Part V > 333 > (a)`` is a claim that ``(a)`` is a limb of s.333.

``parent_fragment_id``
    Where the *parser* hung it. For s.333's clauses that is fragment 7874, the
    heading "Accessory Structure Footprint and Area" printed above the section —
    a sibling of s.333, not the section.

When they disagree, every lookup that walks the tree gets a different answer
from every lookup that reads the path. That is what put the 60.0 m² footprint
cap out of reach: the context channel walked the tree, inherited scope from a
heading, and never saw the sentence the clause finishes.

Three populations are reported, and the middle one is the defect::

    agrees        the path's parent and the tree's parent are the same fragment
    disagrees     they are different fragments  <- ABS-521
    unresolvable  the path names a parent no fragment carries (e.g. "Part V",
                  which the ingest holds only as "Part V, Chapter 19")

``disagrees`` is broken out by (child type, actual parent type) because not
every disagreement is the same defect. A SECTION pathed under "Part V" whose
tree parent is the PART fragment "Part V, Chapter 19" is the ingest naming one
container two ways — untidy, harmless. A CLAUSE whose tree parent is a HEADING
is the ABS-521 defect, and it is the pair to watch.

Usage::

    python scripts/audit_provision_parentage.py
    python scripts/audit_provision_parentage.py --database-url postgresql+psycopg://…
    python scripts/audit_provision_parentage.py --json      # machine-readable
    python scripts/audit_provision_parentage.py --examples 20

This is a *measurement*, not a gate. It exits 0 whatever it finds: the fix for
these rows is a retrieval-layer one (a provision arrives complete however its
tree is shaped), and failing a build on a corpus property the retriever is now
robust to would be theatre.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT, REPO_ROOT / "src", REPO_ROOT / "mcp" / "bylaw_retrieval"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from sqlalchemy import select  # noqa: E402

from bylaw_retrieval.retrieval.provision import (  # noqa: E402
    normalise_citation_path,
    parent_citation_path,
)
from layer1.db.base import Document, SourceFragment  # noqa: E402
from layer1.db.session import session_scope  # noqa: E402


def audit(session, *, example_limit: int) -> dict[str, Any]:
    scoped_ids = (
        session.execute(
            select(Document.id).where(Document.retrieval_enabled.is_(True))
        )
        .scalars()
        .all()
    )
    fragments = (
        session.execute(
            select(SourceFragment).where(SourceFragment.document_id.in_(scoped_ids))
        )
        .scalars()
        .all()
    )

    by_path: dict[tuple[int, str], int] = {}
    by_id: dict[int, SourceFragment] = {}
    for fragment in fragments:
        by_id[fragment.id] = fragment
        normalised = normalise_citation_path(fragment.citation_path)
        if normalised is not None:
            by_path[(fragment.document_id, normalised)] = fragment.id

    agrees = 0
    disagrees = 0
    unresolvable = 0
    pairs: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []

    for fragment in fragments:
        parent_path = parent_citation_path(fragment.citation_path)
        if parent_path is None:
            continue
        path_parent_id = by_path.get((fragment.document_id, parent_path))
        if path_parent_id is None:
            unresolvable += 1
            continue
        if fragment.parent_fragment_id == path_parent_id:
            agrees += 1
            continue
        disagrees += 1
        tree_parent = by_id.get(fragment.parent_fragment_id or -1)
        pair = (
            f"{fragment.fragment_type.value} under "
            f"{tree_parent.fragment_type.value if tree_parent else 'nothing'}"
        )
        pairs[pair] += 1
        if len(examples) < example_limit:
            examples.append(
                {
                    "fragment_id": fragment.id,
                    "citation_path": fragment.citation_path,
                    "path_says_parent_is": parent_path,
                    "tree_says_parent_is": (
                        f"[{tree_parent.id}] {tree_parent.fragment_type.value}: "
                        f"{(tree_parent.text or '')[:60]}"
                        if tree_parent
                        else None
                    ),
                }
            )

    # The ABS-521 population specifically: an operative clause detached from the
    # provision it completes. Counted separately from the totals because that is
    # the pair a reader of the ticket wants, and burying it in a histogram would
    # invite the number being quoted as "2,410 broken rows".
    clause_under_heading = sum(
        count
        for pair, count in pairs.items()
        if pair.startswith(("clause under", "subclause under")) and "heading" in pair
    )

    return {
        "retrieval_enabled_documents": list(scoped_ids),
        "fragments": len(fragments),
        "with_a_parent_path": agrees + disagrees + unresolvable,
        "agrees": agrees,
        "disagrees": disagrees,
        "unresolvable_parent_path": unresolvable,
        "operative_clauses_detached_from_their_provision": clause_under_heading,
        "disagreement_shapes": dict(pairs.most_common()),
        "examples": examples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--examples", type=int, default=8)
    args = parser.parse_args(argv)

    with session_scope(args.database_url) as session:
        report = audit(session, example_limit=args.examples)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"fragments in scope         : {report['fragments']}")
    print(f"  carrying a parent path   : {report['with_a_parent_path']}")
    print(f"  path and tree agree      : {report['agrees']}")
    print(f"  path and tree DISAGREE   : {report['disagrees']}")
    print(f"  parent path names nothing: {report['unresolvable_parent_path']}")
    print()
    print(
        "operative clauses detached from their provision (the ABS-521 "
        f"population): {report['operative_clauses_detached_from_their_provision']}"
    )
    print()
    print("disagreement shapes:")
    for shape, count in report["disagreement_shapes"].items():
        print(f"  {count:>6}  {shape}")
    if report["examples"]:
        print()
        print("examples:")
        for example in report["examples"]:
            print(f"  [{example['fragment_id']}] {example['citation_path']}")
            print(f"      path says parent is: {example['path_says_parent_is']}")
            print(f"      tree says parent is: {example['tree_says_parent_is']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
