"""Characterise a document's NULL-``citation_path`` population (ABS-461, DoD 6).

57% of document 4's fragments have no citation path. Some of that is correct —
headings and body prose are not citable provisions — but some of it is a clause
that has a label and still cannot be reached by ``lookup_citation``. Nobody can
say how much of the corpus is genuinely uncitable without splitting the two, so
this script does the split and prints the counts.

The classes it reports:

``unlabelled``
    No ``citation_label`` at all: headings, body prose, footnotes, and list
    items the parser could not attach a number to. Legitimately unpathed, with
    the caveat below.
``collision``
    Has a label, and ``metadata_json.duplicate_citation_path`` records the path
    it would have had. ``_clear_duplicate_citation_paths`` blanked it because
    two or more fragments computed the same path. These are citable provisions
    that are currently unreachable — the citable-but-missing class.
``unresolved``
    Has a label but no recorded collision: the parser never built a path,
    usually because no addressable ancestor was in scope.

The ``unlabelled`` count carries a caveat the summary prints: a bylaw's
auto-numbered subsections often reach the parser as bare list items with the
number stripped by the PDF renderer, so some of that class is citable in the
bylaw even though nothing in the fragment says so. Sizing that needs the source
PDF, not the database, so it is called out rather than counted.

Usage:

    python scripts/audit_citation_path_coverage.py --document-id 4
    python scripts/audit_citation_path_coverage.py --document-id 4 --examples 10
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment
from layer1.db.session import session_scope

DUPLICATE_KEY = "duplicate_citation_path"


@dataclass
class CoverageReport:
    document_id: int
    total: int = 0
    pathed: int = 0
    unlabelled: Counter = field(default_factory=Counter)
    collision: Counter = field(default_factory=Counter)
    unresolved: Counter = field(default_factory=Counter)
    collided_paths: Counter = field(default_factory=Counter)
    collision_examples: list[tuple[int, str, str]] = field(default_factory=list)
    unresolved_examples: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def unpathed(self) -> int:
        return self.total - self.pathed

    @property
    def citable_but_missing(self) -> int:
        """Fragments with a label that no citation path can currently reach."""
        return sum(self.collision.values()) + sum(self.unresolved.values())


def audit(session: Session, *, document_id: int, examples: int = 5) -> CoverageReport:
    report = CoverageReport(document_id=document_id)
    stmt = (
        select(SourceFragment)
        .where(SourceFragment.document_id == document_id)
        .order_by(SourceFragment.reading_order_start, SourceFragment.id)
    )
    for fragment in session.execute(stmt).scalars():
        report.total += 1
        if fragment.citation_path:
            report.pathed += 1
            continue
        kind = str(fragment.fragment_type.value)
        if not fragment.citation_label:
            report.unlabelled[kind] += 1
            continue
        collided = (fragment.metadata_json or {}).get(DUPLICATE_KEY)
        if collided:
            report.collision[kind] += 1
            report.collided_paths[collided] += 1
            if len(report.collision_examples) < examples:
                report.collision_examples.append((fragment.id, collided, fragment.text[:70]))
            continue
        report.unresolved[kind] += 1
        if len(report.unresolved_examples) < examples:
            report.unresolved_examples.append(
                (fragment.id, fragment.citation_label, fragment.text[:70])
            )
    return report


def _print_class(title: str, counts: Counter) -> None:
    total = sum(counts.values())
    print(f"\n{title}: {total}")
    for kind, count in counts.most_common():
        print(f"    {kind:<12} {count:>6}")


def print_report(report: CoverageReport, *, examples: int) -> None:
    print(f"document {report.document_id}: {report.total} fragments")
    print(f"  citation_path set : {report.pathed:>6} ({report.pathed / report.total:.1%})")
    print(f"  citation_path NULL: {report.unpathed:>6} ({report.unpathed / report.total:.1%})")

    _print_class("  [legitimately unpathed] no citation_label", report.unlabelled)
    print(
        "      caveat: auto-numbered subsections often reach the parser as bare\n"
        "      list items with the number stripped by the renderer. Some of this\n"
        "      class is citable in the bylaw; sizing it needs the source PDF."
    )
    _print_class("  [citable, missing] path blanked as a duplicate", report.collision)
    print(f"      distinct colliding paths: {len(report.collided_paths)}")
    for path, count in report.collided_paths.most_common(examples):
        print(f"      {count:>3}x {path}")
    for fragment_id, path, text in report.collision_examples:
        print(f"      e.g. {fragment_id}: would be {path!r}")
        print(f"           {text}")
    _print_class("  [citable, missing] label but no path built", report.unresolved)
    for fragment_id, label, text in report.unresolved_examples:
        print(f"      e.g. {fragment_id} {label!r}: {text}")

    print(
        f"\n  citable-but-missing total: {report.citable_but_missing} "
        f"({report.citable_but_missing / report.total:.1%} of the document)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument("--examples", type=int, default=5, help="Examples to print per class.")
    args = parser.parse_args()

    with session_scope(args.database_url) as session:
        report = audit(session, document_id=args.document_id, examples=args.examples)
    print_report(report, examples=args.examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
