"""CLI entry point for the corpus-coherence audit (ABS-356).

Asserts that every overlay role declared by a dataset config under
``src/layer1/datasets/`` (zone, height_precinct, far_precinct, heritage,
bonus_zoning, shadow_impact, pedestrian_street, ...) is actually visible
through the active retrieval scope — the same scope
``RetrievalService.get_address_profile`` sees. Run this after ingesting the
corpus (and its geo datasets) to catch a silent degradation loudly instead of
downstream, in an unrelated e2e failure or a customer-visible hedged answer.

Exit code is 1 when any overlay role is missing, listing each one with the
reason (unlinked / orphaned / evicted-by-newer-document).

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \\
        .venv/bin/python scripts/corpus_coherence_audit.py

    # Point at a different config directory (e.g. a staging dataset set):
    .venv/bin/python scripts/corpus_coherence_audit.py --config-dir path/to/datasets

    # Write the JSON report to a file instead of stdout:
    .venv/bin/python scripts/corpus_coherence_audit.py --out report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bylaw_retrieval.retrieval import audit_corpus_coherence, latest_per_bylaw_resolver
from bylaw_retrieval.retrieval.coherence_audit import DEFAULT_DATASET_CONFIG_DIR
from layer1.db.session import session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help=f"Dataset config directory to audit against (default: {DEFAULT_DATASET_CONFIG_DIR})",
    )
    parser.add_argument("--db-url", default=None, help="Database URL override")
    parser.add_argument("--out", type=Path, default=None, help="Write the JSON report to a file")
    parser.add_argument(
        "--unscoped",
        action="store_true",
        help="Audit against every ingested document instead of the latest-per-bylaw scope "
        "(diagnostic: shows raw linker state, not what a real request would see).",
    )
    args = parser.parse_args(argv)

    resolver = None if args.unscoped else latest_per_bylaw_resolver
    with session_scope(args.db_url) as session:
        report = audit_corpus_coherence(
            session,
            dataset_config_dir=args.config_dir,
            default_document_id_resolver=resolver,
        )

    payload = report.model_dump(mode="json")
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote corpus-coherence report to {args.out}")
    else:
        print(text)

    if not report.coherent:
        print(
            f"corpus-coherence audit FAILED: {len(report.missing)} overlay role(s) "
            f"missing from active retrieval scope",
            file=sys.stderr,
        )
        for entry in report.missing:
            print(f"  - [{entry.reason}] {entry.role} ({entry.bylaw_name}): {entry.detail}", file=sys.stderr)
        return 1

    print(f"corpus-coherence audit passed: {report.checked_roles} role(s) across {report.bylaws_checked} bylaw(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
