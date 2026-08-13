#!/usr/bin/env python
"""Undo the citation-path-collision parse-status demotion (ABS-480).

Until ABS-480, ``_clear_duplicate_citation_paths``
(``src/layer1/pipeline/hierarchy.py``) reacted to a *naming* collision — two
provisions deriving the same ``citation_path`` — by doing three things:

1. blanking ``citation_path`` and recording the lost path in
   ``metadata_json.duplicate_citation_path``  (correct: the address really is
   ambiguous, and repathing it is DM-11);
2. flipping ``parse_status`` PARSED -> UNCERTAIN;
3. capping ``confidence`` at 0.6.

(2) and (3) are wrong. The text parsed fine; a *sibling* happened to compute
the same address. Retrieval reads ``parse_status`` as a quality signal —
``_score_fragment`` awards +1.0 for parsed and -2.0 otherwise — so every
collided provision carries a 3-point ranking penalty and is labelled
"uncertain" in every response the advisor sees. The code no longer does (2) or
(3); this script repairs the rows already written that way.

Restore rule
------------
A row is restored to PARSED iff **all** of:

R1. ``metadata_json.duplicate_citation_path`` is a non-empty string.
R2. ``parse_status`` is UNCERTAIN.
R3. ``citation_label`` is not NULL.
R4. ``fragment_type`` is one of the labelled-match types
    (part / section / subsection / clause / subclause / schedule / appendix).
R5. ``metadata_json`` carries no other uncertainty marker — currently just
    ``fallback_unaccounted_block`` (``pipeline/ingest.py``'s unaccounted-block
    fallback, which writes UNCERTAIN at confidence 0.4).

Why that is sound, and not merely plausible: the *only* branch of
``reconstruct_hierarchy`` that ever assigns a non-NULL ``citation_path`` is the
labelled-citation-match branch, and it assigns one only when ``can_address`` is
true — the same expression that sets ``status = PARSED`` and
``confidence = match.confidence`` (uncapped). A recorded
``duplicate_citation_path`` proves the row held a path when the collision sweep
ran, so the row was necessarily PARSED immediately before the sweep, and the
sweep is the only thing that could have demoted it. Nothing downstream of
hierarchy reconstruction writes ``parse_status`` onto an existing fragment:
``page_break_repair`` carries the head row's status through a merge, and
``table_captions`` only rewrites ``citation_path``.

R3/R4/R5 are therefore expected to exclude nothing on a corpus written by the
pipeline. They are in the rule so that a row that *does* fail them — a
hand-edited or seeded row, a future writer of the marker — is skipped and
reported rather than silently promoted.

Confidence
----------
The 0.6 cap is lossy: the pre-cap value is not stored anywhere. It is,
however, derivable — the parser's ``match.confidence`` is a fixed constant per
label pattern, so re-running ``parse_citation_label`` over the row's stored
text reproduces it exactly. The script restores confidence only when that
re-parse agrees with the stored row on **both** ``citation_label`` and
``fragment_type`` and yields a higher value than what is stored; otherwise the
row's confidence is left alone and counted under ``confidence_unresolved``.
Use ``--status-only`` to skip the confidence pass entirely.

Idempotent. A second run reports ``restored=0``.

Usage
-----
    # Characterize first — this is the default; nothing is written.
    .venv/bin/python scripts/backfill_duplicate_citation_path_status.py --dry-run

    # Scope to one document, show more examples of every skip class:
    .venv/bin/python scripts/backfill_duplicate_citation_path_status.py \
        --dry-run --document-id 4 --examples 10

    # Apply:
    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \
        .venv/bin/python scripts/backfill_duplicate_citation_path_status.py --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer1.db.base import SourceFragment
from layer1.db.migration_fence import fence_or_abort
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus
from layer1.pipeline.citations import parse_citation_label

logger = logging.getLogger("backfill_duplicate_citation_path_status")

DUPLICATE_KEY = "duplicate_citation_path"

#: metadata keys that independently explain an UNCERTAIN parse_status. A row
#: carrying one of these is not restored, because the collision is not the only
#: thing the pipeline had to say about it.
OTHER_UNCERTAINTY_MARKERS = ("fallback_unaccounted_block",)

#: Fragment types the labelled-citation-match branch of reconstruct_hierarchy
#: can emit. Only these can legitimately hold a citation_path, so only these
#: can legitimately hold a duplicate_citation_path.
LABELLED_FRAGMENT_TYPES = frozenset(
    {
        FragmentType.PART,
        FragmentType.SECTION,
        FragmentType.SUBSECTION,
        FragmentType.CLAUSE,
        FragmentType.SUBCLAUSE,
        FragmentType.SCHEDULE,
        FragmentType.APPENDIX,
    }
)

#: The cap the old sweep applied. A restorable row sitting at exactly this
#: value is the signature of a capped confidence.
LEGACY_CONFIDENCE_CAP = 0.6


@dataclass
class BackfillStats:
    """What we found and what we did, suitable for a post-run issue comment."""

    fragments_with_marker: int = 0
    already_parsed: int = 0
    restored_status: int = 0
    #: rows whose confidence the re-parse reproduced and we raised
    confidence_restored: int = 0
    #: rows we restored to PARSED but whose confidence we could not re-derive
    confidence_unresolved: int = 0
    #: rows we restored whose confidence was never capped (already > 0.6)
    confidence_uncapped: int = 0
    skipped: Counter = field(default_factory=Counter)
    restored_by_type: Counter = field(default_factory=Counter)
    skip_examples: dict[str, list[str]] = field(default_factory=dict)

    def note_skip(self, reason: str, fragment: SourceFragment, *, examples: int) -> None:
        self.skipped[reason] += 1
        bucket = self.skip_examples.setdefault(reason, [])
        if len(bucket) < examples:
            bucket.append(
                f"#{fragment.id} {fragment.fragment_type.value} "
                f"label={fragment.citation_label!r} {fragment.text[:60]!r}"
            )

    def summary_line(self) -> str:
        skips = " ".join(f"{name}={count}" for name, count in sorted(self.skipped.items()))
        return (
            f"marker_rows={self.fragments_with_marker} "
            f"already_parsed={self.already_parsed} "
            f"restored={self.restored_status} "
            f"confidence_restored={self.confidence_restored} "
            f"confidence_unresolved={self.confidence_unresolved} "
            f"confidence_uncapped={self.confidence_uncapped}"
            + (f" skipped[{skips}]" if skips else " skipped[none]")
        )


def _rederive_confidence(fragment: SourceFragment) -> float | None:
    """The parser confidence this row was created with, or None if unknowable.

    ``parse_citation_label`` is deterministic and its confidence is a constant
    per matched pattern, so re-parsing the stored text recovers the pre-cap
    value — but only if the re-parse lands on the same label and the same
    fragment type. Hierarchy reconstruction can reclassify a match after the
    fact (roman subclause promotion, compound-section rebasing), and later
    stages can rewrite the text (page-break repair joins a split provision), so
    disagreement is a real possibility and means "don't guess".
    """
    match = parse_citation_label(fragment.text)
    if match is None:
        return None
    if match.label != fragment.citation_label:
        return None
    if match.fragment_type != fragment.fragment_type:
        return None
    return match.confidence


def _restorable(fragment: SourceFragment, stats: BackfillStats, *, examples: int) -> bool:
    """Apply R2-R5 (R1 is the query predicate) and record why not."""
    if fragment.parse_status == ParseStatus.PARSED:
        stats.already_parsed += 1
        return False
    if fragment.parse_status != ParseStatus.UNCERTAIN:
        stats.note_skip(f"parse_status_{fragment.parse_status.value}", fragment, examples=examples)
        return False
    if not fragment.citation_label:
        stats.note_skip("no_citation_label", fragment, examples=examples)
        return False
    if fragment.fragment_type not in LABELLED_FRAGMENT_TYPES:
        stats.note_skip("unlabelled_fragment_type", fragment, examples=examples)
        return False
    metadata = fragment.metadata_json or {}
    for marker in OTHER_UNCERTAINTY_MARKERS:
        if metadata.get(marker):
            stats.note_skip(f"other_marker_{marker}", fragment, examples=examples)
            return False
    return True


def backfill(
    session: Session,
    *,
    dry_run: bool = True,
    document_id: int | None = None,
    status_only: bool = False,
    examples: int = 5,
) -> BackfillStats:
    """Restore PARSED on rows demoted purely by a citation-path collision.

    Caller owns the transaction — pass ``session_scope()``'s session for real
    runs or a test-fixture session for unit tests. In dry-run mode every row is
    classified and counted but nothing is written.
    """
    stats = BackfillStats()

    stmt = select(SourceFragment).order_by(SourceFragment.id)
    if document_id is not None:
        stmt = stmt.where(SourceFragment.document_id == document_id)

    for fragment in session.execute(stmt).scalars():
        marker = (fragment.metadata_json or {}).get(DUPLICATE_KEY)
        if not isinstance(marker, str) or not marker:
            continue
        stats.fragments_with_marker += 1
        if not _restorable(fragment, stats, examples=examples):
            continue

        stats.restored_status += 1
        stats.restored_by_type[fragment.fragment_type.value] += 1
        if not dry_run:
            fragment.parse_status = ParseStatus.PARSED

        if status_only:
            continue
        stored = fragment.confidence
        if stored is not None and stored > LEGACY_CONFIDENCE_CAP:
            # Never capped (or already repaired) — nothing to re-derive.
            stats.confidence_uncapped += 1
            continue
        original = _rederive_confidence(fragment)
        if original is None or (stored is not None and original <= stored):
            stats.confidence_unresolved += 1
            continue
        stats.confidence_restored += 1
        if not dry_run:
            fragment.confidence = original

    return stats


def print_report(stats: BackfillStats, *, dry_run: bool) -> None:
    print(f"rows with {DUPLICATE_KEY}: {stats.fragments_with_marker}")
    print(f"  already parsed (nothing to do): {stats.already_parsed}")
    print(f"  restored to parsed            : {stats.restored_status}")
    for kind, count in stats.restored_by_type.most_common():
        print(f"      {kind:<12} {count:>6}")
    print(f"  confidence re-derived         : {stats.confidence_restored}")
    print(f"  confidence already > {LEGACY_CONFIDENCE_CAP}      : {stats.confidence_uncapped}")
    print(f"  confidence left alone         : {stats.confidence_unresolved}")
    if stats.skipped:
        print("  skipped (rule did not match):")
        for reason, count in stats.skipped.most_common():
            print(f"      {reason:<32} {count:>6}")
            for example in stats.skip_examples.get(reason, []):
                print(f"          e.g. {example}")
    else:
        print("  skipped (rule did not match): 0")
    print(f"\n{'DRY RUN — nothing written' if dry_run else 'APPLIED'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and report, write nothing. This is the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the restored parse_status/confidence.",
    )
    parser.add_argument(
        "--document-id",
        type=int,
        default=None,
        help="Scope to one document (default: every document).",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Restore parse_status only; leave the capped confidence alone.",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        help="Examples to print per skip class.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (otherwise read from the environment).",
    )
    args = parser.parse_args()

    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")
    dry_run = not args.apply

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not dry_run:
        # ABS-499: no unfenced write to the dev corpus.
        fence_or_abort("backfill-duplicate-citation-path-status", database_url=args.database_url)

    started = time.monotonic()
    with session_scope(args.database_url) as session:
        stats = backfill(
            session,
            dry_run=dry_run,
            document_id=args.document_id,
            status_only=args.status_only,
            examples=args.examples,
        )
        if dry_run:
            session.rollback()
    elapsed_s = time.monotonic() - started

    print_report(stats, dry_run=dry_run)
    print(
        f"backfill_duplicate_citation_path_status: {stats.summary_line()} "
        f"elapsed_s={elapsed_s:.2f} dry_run={dry_run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
