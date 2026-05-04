"""Seed a synthetic schedule fragment so an external geo dataset can link.

The Halifax Regional Centre Land Use By-law references its schedules by name
but several are published as separate documents (or as map figures that the
parser doesn't yet promote to FragmentType.SCHEDULE). Until the ingest
pipeline is extended to intake supporting documents, retrieval paths that
depend on these schedule fragments have nothing to traverse to. This script
writes them by hand, clearly marked in metadata so a future supporting-
document ingest can detect and replace each rather than duplicating.

Usage:
  DATABASE_URL=... .venv/bin/python scripts/seed_synthetic_fragment.py \\
      --document-id 4 --kind height
  DATABASE_URL=... .venv/bin/python scripts/seed_synthetic_fragment.py \\
      --document-id 4 --kind zoning

Idempotent: re-running prints the existing fragment id and exits 0.
Refuses to overwrite a parsed (non-manual) fragment with the same label.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from layer1.datasets.linker import relink_orphan_datasets
from layer1.db.base import Document, ExternalDataset, SourceFragment
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

SENTINEL_KEY = "manually_seeded"


@dataclass(frozen=True)
class SeedSpec:
    citation_label: str
    citation_path: str
    text: str
    fragment_type: FragmentType


SEEDS: dict[str, SeedSpec] = {
    "height": SeedSpec(
        citation_label="Schedule 15",
        citation_path="schedules.schedule_15",
        text=(
            "Schedule 15: Maximum Building Height Precincts. "
            "This schedule is published by Halifax Regional Municipality as a "
            "separate document and is not contained in the main By-law PDF. "
            "Authoritative geometry and per-precinct maximum heights are "
            "stored in the linked external geo dataset "
            "(halifax_height_precincts)."
        ),
        fragment_type=FragmentType.SCHEDULE,
    ),
    "zoning": SeedSpec(
        citation_label="Zoning Schedule",
        citation_path="schedules.zoning",
        text=(
            "Zoning Schedule. Each parcel of land within the Regional Centre "
            "is assigned a zone code that determines permitted uses, density, "
            "and built-form requirements. The authoritative zone-to-area "
            "mapping is published as the HRM Zoning Boundaries open data "
            "layer and is stored here in the linked external geo dataset "
            "(halifax_zoning_boundaries). Zone codes used by the Regional "
            "Centre Land Use By-law include the ER-, CH-, DD, CEN-, COR, "
            "HR-, INS, UC-, WA, and others; consult Part II of the bylaw for "
            "the per-zone permitted uses and standards."
        ),
        fragment_type=FragmentType.SCHEDULE,
    ),
}


def main(document_id: int, kind: str, db_url: str | None) -> int:
    if kind not in SEEDS:
        print(f"unknown kind {kind!r}; choose from: {list(SEEDS)}", file=sys.stderr)
        return 2
    spec = SEEDS[kind]

    with session_scope(db_url) as session:
        document = session.get(Document, document_id)
        if document is None:
            print(f"document {document_id} not found", file=sys.stderr)
            return 2

        existing = (
            session.execute(
                select(SourceFragment).where(
                    SourceFragment.document_id == document_id,
                    SourceFragment.citation_label == spec.citation_label,
                    SourceFragment.fragment_type == spec.fragment_type,
                )
            )
            .scalars()
            .all()
        )
        for fragment in existing:
            if (fragment.metadata_json or {}).get(SENTINEL_KEY):
                print(f"already seeded: fragment_id={fragment.id}")
                _relink_and_report(session, document_id)
                return 0
            print(
                f"refusing to overwrite parsed fragment_id={fragment.id} "
                f"(citation_label={spec.citation_label!r}); resolve manually before retrying",
                file=sys.stderr,
            )
            return 1

        fragment = SourceFragment(
            document_id=document_id,
            fragment_type=spec.fragment_type,
            citation_label=spec.citation_label,
            citation_path=spec.citation_path,
            parent_fragment_id=None,
            page_start=document.page_count or 0,
            page_end=document.page_count or 0,
            reading_order_start=None,
            reading_order_end=None,
            text=spec.text,
            parse_status=ParseStatus.UNCERTAIN,
            confidence=1.0,
            source_block_ids_json=[],
            metadata_json={
                SENTINEL_KEY: True,
                "kind": kind,
                "reason": "schedule published as separate document or map; not in main bylaw PDF",
                "seeded_at": datetime.now(timezone.utc).isoformat(),
                "expected_replacement": f"ingest of supporting document for kind={kind!r}",
            },
        )
        session.add(fragment)
        session.flush()
        print(f"inserted fragment_id={fragment.id} kind={kind} citation={spec.citation_label!r}")

        link_results = relink_orphan_datasets(session)
        for r in link_results:
            print(
                f"relink dataset_id={r.dataset_id}: status={r.status} "
                f"document_id={r.document_id} fragment_id={r.fragment_id}"
            )
    return 0


def _relink_and_report(session, document_id: int) -> None:
    datasets = (
        session.execute(
            select(ExternalDataset).where(ExternalDataset.linked_document_id == document_id)
        )
        .scalars()
        .all()
    )
    for dataset in datasets:
        print(
            f"existing dataset_id={dataset.id} ({dataset.name}) "
            f"linked_fragment_id={dataset.linked_fragment_id}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument(
        "--kind",
        choices=list(SEEDS),
        default="height",
        help="Which synthetic schedule to seed (height | zoning).",
    )
    parser.add_argument("--db-url", default=None)
    args = parser.parse_args()
    sys.exit(main(args.document_id, args.kind, args.db_url))
