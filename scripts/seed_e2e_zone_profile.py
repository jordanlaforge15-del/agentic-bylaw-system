"""Seed a Regional-Centre-shaped zone corpus for the get_zone_profile e2e.

Used by ``web/e2e/functional/abs272-get-zone-profile.spec.ts`` (ABS-272).
Drops a known-shape bylaw document into ``layer1_test`` (or whatever
DATABASE_URL the caller points at) with per-zone, table-row-shaped
fragments so the thick ``get_zone_profile`` tool can compose its
internal semantic searches against real data:

* Table 5 rows — maximum height + lot coverage per zone
* Table 3/4 rows — front/side/rear setbacks per zone
* Table 1A/1B rows — use permissions per zone (P/N markers)
* Part II establishment rows — zone full names
* Part V §120 — the general off-street-parking rule + exemptions

Idempotent: every fragment is keyed by ``(document_id, citation_path)``
(the layer-1 unique constraint), so a re-run — including the four
parallel Playwright viewport projects hitting ``beforeAll`` at once —
finds the rows already present and no-ops. A transaction-scoped
advisory lock serialises the first writer on Postgres.
"""
from __future__ import annotations

# ABS-428: must precede any advisor/layer1 import so the cached settings
# resolve DATABASE_URL to the dedicated e2e Postgres instance, never dev.
import e2e_db_default  # noqa: F401  isort: skip

import sys

from sqlalchemy import select

from layer1.db.base import Document, SourceFragment, utcnow
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus


DOCUMENT_FILE_HASH = "e2e-zone-profile-bylaw-1"
DOCUMENT_MUNICIPALITY = "Halifax Regional Municipality"
DOCUMENT_BYLAW_NAME = "Regional Centre Land Use By-Law (Zone Profile E2E)"


# Per-zone rows lifted from tests/fixtures/halifax_regional_centre_lub.txt.
# COR / CEN-2 height is governed by Schedule 15 (no inline number) — the
# correct None outcome for max_height_m.
_ZONE_ROWS = {
    "HR-2": {
        "full_name": "Higher Order Residential 2",
        "use_table": "Table 1A",
        "height": "HR-2 Maximum Height 25.0 m Maximum Lot Coverage 65%",
        "setbacks": "HR-2 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions HR-2 single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
    },
    "HR-1": {
        "full_name": "Higher Order Residential 1",
        "use_table": "Table 1A",
        "height": "HR-1 Maximum Height 20.0 m Maximum Lot Coverage 60%",
        "setbacks": "HR-1 Front Setback 3.0 m Side Setback 3.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions HR-1 single-unit dwelling P secondary suite P "
            "multi-unit dwelling P home occupation P daycare P"
        ),
    },
    "COR": {
        "full_name": "Corridor",
        "use_table": "Table 1B",
        "height": "COR Maximum Height as per Schedule 15 Maximum Lot Coverage 70%",
        "setbacks": "COR Front Setback 3.0 m Side Setback 0.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions COR single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
    },
    "CEN-2": {
        "full_name": "Centre 2",
        "use_table": "Table 1B",
        "height": "CEN-2 Maximum Height as per Schedule 15 Maximum Lot Coverage 80%",
        "setbacks": "CEN-2 Front Setback 0.0 m Side Setback 0.0 m Rear Setback 3.0 m",
        "uses": (
            "Use Permissions CEN-2 single-unit dwelling N secondary suite N "
            "multi-unit dwelling P home occupation N daycare P"
        ),
    },
}

_PARKING_TEXT = (
    "Off-Street Parking Requirements. A residential development shall "
    "provide a minimum of 1 parking space per dwelling unit. Despite "
    "that, no off-street parking is required for a development in the "
    "CEN-1, CEN-2, DH, or DD zone. A non-residential development shall "
    "provide parking at the ratios in Table 8."
)


def _get_or_create_document(session) -> Document:
    document = (
        session.execute(
            select(Document).where(Document.file_hash == DOCUMENT_FILE_HASH)
        )
        .scalars()
        .first()
    )
    if document is not None:
        # Converge the publish flag on re-seed: rows created before
        # ABS-413 (or left disabled by the migration backfill) must
        # still end up retrieval-enabled in the persistent e2e DB.
        document.retrieval_enabled = True
        session.flush()
        return document
    document = Document(
        municipality=DOCUMENT_MUNICIPALITY,
        bylaw_name=DOCUMENT_BYLAW_NAME,
        source_path="e2e/zone_profile_bylaw.txt",
        file_hash=DOCUMENT_FILE_HASH,
        mime_type="text/plain",
        page_count=6,
        parser_version="e2e-seed",
        retrieval_enabled=True,
        ingestion_timestamp=utcnow(),
    )
    session.add(document)
    session.flush()
    return document


def _ensure_fragment(
    session,
    *,
    document_id: int,
    citation_path: str,
    citation_label: str,
    text: str,
    page: int,
    order: int,
) -> None:
    existing = (
        session.execute(
            select(SourceFragment).where(
                SourceFragment.document_id == document_id,
                SourceFragment.citation_path == citation_path,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    session.add(
        SourceFragment(
            document_id=document_id,
            fragment_type=FragmentType.SECTION,
            citation_label=citation_label,
            citation_path=citation_path,
            parent_fragment_id=None,
            page_start=page,
            page_end=page,
            reading_order_start=order,
            reading_order_end=order,
            text=text,
            parse_status=ParseStatus.PARSED,
            confidence=1.0,
        )
    )


def seed(session) -> dict[str, int]:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sa_text  # noqa: PLC0415

        # Stable arbitrary 64-bit key, distinct from other seed scripts.
        session.execute(sa_text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=2720272272))

    document = _get_or_create_document(session)
    order = 0
    for zone, row in _ZONE_ROWS.items():
        order += 1
        _ensure_fragment(
            session,
            document_id=document.id,
            citation_path=f"Part II > 30 > {zone}",
            citation_label=zone,
            text=f"{zone} {row['full_name']} Zone",
            page=2,
            order=order,
        )
        order += 1
        _ensure_fragment(
            session,
            document_id=document.id,
            citation_path=f"Table 5 > {zone}",
            citation_label=zone,
            text=row["height"],
            page=4,
            order=order,
        )
        order += 1
        _ensure_fragment(
            session,
            document_id=document.id,
            citation_path=f"Table 3 > {zone}",
            citation_label=zone,
            text=row["setbacks"],
            page=4,
            order=order,
        )
        order += 1
        _ensure_fragment(
            session,
            document_id=document.id,
            citation_path=f"{row['use_table']} > {zone}",
            citation_label=zone,
            text=row["uses"],
            page=3,
            order=order,
        )
    order += 1
    _ensure_fragment(
        session,
        document_id=document.id,
        citation_path="Part V > 120",
        citation_label="Section 120",
        text=_PARKING_TEXT,
        page=6,
        order=order,
    )
    session.flush()
    return {"document_id": document.id}


def main() -> int:
    with session_scope() as session:
        ids = seed(session)
    print(f"seed_e2e_zone_profile: document={ids['document_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
