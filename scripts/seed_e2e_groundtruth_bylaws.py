"""Seed clauses for the Halifax ground-truth evaluation e2e spec.

Extends the Evaluator E2E Bylaw (seeded by ``seed_e2e_evaluator_bylaws.py``)
with additional attribute-tagged clauses covering rear setback, side setback,
and lot coverage.  These clauses carry ER-1 standards from the Halifax
Regional Centre LUB ground-truth test set.

Run AFTER ``seed_e2e_evaluator_bylaws.py`` — it depends on the document
already existing.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceFragment
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

DOCUMENT_FILE_HASH = "e2e-evaluator-bylaw-1"

GROUND_TRUTH_CLAUSES = [
    dict(
        citation_label="4.5.1",
        citation_path="4.5.1",
        page_start=4,
        reading_order_start=4,
        text="The minimum rear yard shall not be less than 7.5 metres.",
        attribute_tags=["rear_setback_m"],
    ),
    dict(
        citation_label="4.6.1",
        citation_path="4.6.1",
        page_start=5,
        reading_order_start=5,
        text="The minimum side yard shall not be less than 1.2 metres.",
        attribute_tags=["side_setback_left_m"],
    ),
    dict(
        citation_label="4.7.1",
        citation_path="4.7.1",
        page_start=6,
        reading_order_start=6,
        text="The maximum lot coverage shall not exceed 35 percent.",
        attribute_tags=["lot_coverage_percent"],
    ),
]


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2604601147))

    document = session.execute(
        select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
    ).scalars().first()

    if document is None:
        print("seed_e2e_groundtruth_bylaws: parent document not found — run seed_e2e_evaluator_bylaws.py first")
        return {}

    ids: dict[str, int] = {}
    for clause in GROUND_TRUTH_CLAUSES:
        existing = session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document.id,
                SourceFragment.citation_path == clause["citation_path"],
            )
        ).scalars().first()
        if existing is not None:
            ids[clause["attribute_tags"][0]] = existing.id
            continue
        frag = SourceFragment(
            document_id=document.id,
            fragment_type=FragmentType.CLAUSE,
            citation_label=clause["citation_label"],
            citation_path=clause["citation_path"],
            parent_fragment_id=None,
            page_start=clause["page_start"],
            page_end=clause["page_start"],
            reading_order_start=clause["reading_order_start"],
            text=clause["text"],
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
            attribute_tags=list(clause["attribute_tags"]),
        )
        session.add(frag)
        session.flush()
        ids[clause["attribute_tags"][0]] = frag.id

    return ids


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    if ids:
        print(f"seed_e2e_groundtruth_bylaws: {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
