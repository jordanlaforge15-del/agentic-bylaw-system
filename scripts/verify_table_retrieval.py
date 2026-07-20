"""Verify permission-matrix table retrieval against a real corpus (ABS-409).

Read-only. Run AFTER scripts/backfill_table_citations.py against the same
database; exit 0 means the whole retrieval chain the prod Evidence-Gap miss
exposed is healed:

  (a) every permission-matrix table has a caption-linked parent fragment with
      a non-NULL citation_path, and row+column axis bindings;
  (a2) no table whose caption is a parking table is profiled
      permission_matrix (the Table 15 poisoning mode);
  (b) lookup_citation("Table 1B") ranks the canonical bracket path in its
      top suggestions;
  (c) the canonical Table 1A path resolves with table cells attached;
  (d) get_zone_profile("COR") enumerates permitted uses including the
      multi-unit dwelling row AND a continuation-page-only row (military),
      with at least one uses citation; get_zone_profile("HCD-SV") (Table 1D)
      is known and carries uses;
  (e) prints the caption->tables mapping (dry-run pass) for human review.

Usage:
    python scripts/verify_table_retrieval.py \
        --database-url postgresql+psycopg://layer1:layer1@localhost:5440/layer1 \
        [--profile halifax]
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from bylaw_retrieval.retrieval import CitationLookupRequest, RetrievalService
from layer1.db.base import Document, SourceFragment, SourceTable, TableAxisBinding, TableSemanticProfile
from layer1.db.session import session_scope
from layer1.pipeline.table_captions import link_table_captions
from layer1.semantic.permission_markers import PERMISSION_MATRIX_PROFILE

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    status = "ok " if condition else "FAIL"
    print(f"[{status}] {message}")
    if not condition:
        FAILURES.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--profile", default="halifax")
    args = parser.parse_args()

    with session_scope(args.database_url) as session:
        rc_doc = session.execute(
            select(Document)
            .where(Document.bylaw_name.ilike("%Regional Centre%"))
            .order_by(Document.id.desc())
        ).scalars().first()
        if rc_doc is None:
            print("FAIL: no Regional Centre document in this database")
            return 1
        print(f"Regional Centre document: id={rc_doc.id} ({rc_doc.bylaw_name})")

        # ---- (a) permission-matrix tables are caption-linked + bound ----
        matrix_tables = session.execute(
            select(SourceTable)
            .join(TableSemanticProfile, TableSemanticProfile.table_id == SourceTable.id)
            .where(
                SourceTable.document_id == rc_doc.id,
                TableSemanticProfile.profile_type == PERMISSION_MATRIX_PROFILE,
            )
            .order_by(SourceTable.page_start)
        ).scalars().all()
        check(bool(matrix_tables), f"(a) permission-matrix tables exist ({len(matrix_tables)} found)")
        for table in matrix_tables:
            parent = (
                session.get(SourceFragment, table.parent_fragment_id)
                if table.parent_fragment_id is not None
                else None
            )
            check(
                parent is not None and parent.citation_path is not None,
                f"(a) table {table.id} (p{table.page_start}) parent caption has citation_path "
                f"({parent.citation_path if parent else None})",
            )
            axes = set(
                session.execute(
                    select(TableAxisBinding.axis).where(TableAxisBinding.table_id == table.id).distinct()
                ).scalars()
            )
            check(
                {"row", "column"} <= axes,
                f"(a) table {table.id} has row+column axis bindings ({sorted(axes)})",
            )

        # ---- (a2) parking-captioned tables are NOT permission matrices ----
        poisoned = [
            table.id
            for table in matrix_tables
            if table.caption and "parking" in table.caption.lower()
        ]
        check(
            not poisoned,
            f"(a2) no parking-captioned table profiled permission_matrix (violations: {poisoned})",
        )

        service = RetrievalService(session)

        # ---- (b) human-style miss suggests the canonical path ----
        miss = service.lookup_citation(
            CitationLookupRequest(citation_path="Table 1B", document_id=rc_doc.id)
        )
        canonical_1b = next(
            (s for s in miss.suggestions if s.endswith("[Table 1B]")), None
        )
        rank = miss.suggestions.index(canonical_1b) + 1 if canonical_1b else None
        check(
            canonical_1b is not None and rank is not None and rank <= 3,
            f"(b) lookup('Table 1B') suggests canonical path in top 3 (rank={rank}, "
            f"suggestions={miss.suggestions[:3]})",
        )

        # ---- (c) canonical path resolves with cells ----
        canonical_1a = session.execute(
            select(SourceFragment.citation_path).where(
                SourceFragment.document_id == rc_doc.id,
                SourceFragment.citation_path.like("%[Table 1A]"),
            )
        ).scalars().first()
        check(canonical_1a is not None, f"(c) canonical Table 1A path exists ({canonical_1a})")
        if canonical_1a:
            hit = service.lookup_citation(
                CitationLookupRequest(
                    citation_path=canonical_1a, document_id=rc_doc.id, include_tables=True
                )
            )
            has_cells = bool(
                hit.match
                and any(t.cells for t in hit.match.related_tables)
            )
            check(
                has_cells,
                f"(c) lookup('{canonical_1a}') returns match with table cells "
                f"({len(hit.match.related_tables) if hit.match else 0} tables)",
            )

        # ---- (d) zone profiles enumerate ----
        cor = service.get_zone_profile("COR", include=["uses"])
        permitted_lower = [u.lower() for u in (cor.uses.permitted if cor.uses else [])]
        conditional_lower = [c.use.lower() for c in (cor.uses.conditional if cor.uses else [])]
        check(not cor.unknown_zone, "(d) COR zone is known")
        check(
            any("multi-unit dwelling" in u for u in permitted_lower + conditional_lower),
            f"(d) COR uses include multi-unit dwelling "
            f"({len(permitted_lower)} permitted / {len(conditional_lower)} conditional)",
        )
        check(
            any("military" in u for u in permitted_lower + conditional_lower
                + [u.lower() for u in (cor.uses.not_permitted if cor.uses else [])]),
            "(d) COR enumeration reaches the continuation page (military row present)",
        )
        uses_refs = [c for c in cor.citations if "uses" in c.backs]
        check(bool(uses_refs), f"(d) COR uses carry citations ({len(uses_refs)})")

        hcd = service.get_zone_profile("HCD-SV", include=["uses"])
        hcd_count = (
            len(hcd.uses.permitted) + len(hcd.uses.conditional) + len(hcd.uses.not_permitted)
            if hcd.uses
            else 0
        )
        check(
            not hcd.unknown_zone and hcd_count > 0,
            f"(d) HCD-SV (Table 1D) known with {hcd_count} enumerated uses",
        )

        # ---- (e) mapping dump for human review ----
        print("\n(e) caption -> tables mapping (dry-run re-pass):")
        stats = link_table_captions(
            session, document_id=rc_doc.id, profile=args.profile, dry_run=True
        )
        for entry in stats.mapping:
            print(
                f"  p{entry['page']:>3} {entry['label']:<10} -> {entry['table_ids']} "
                f"({entry['action']}) {entry['text'][:60]}"
            )
        print(f"  {stats.summary_line()}")

    print()
    if FAILURES:
        print(f"VERIFY FAILED — {len(FAILURES)} assertion(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("VERIFY PASSED — table retrieval chain is healed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
