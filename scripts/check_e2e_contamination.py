#!/usr/bin/env python
"""E2E-contamination tripwire (ABS-432).

Sweeps the target database for the three fingerprints every
``scripts/seed_e2e_*.py`` fixture stamps on the rows it creates:

* ``document.parser_version = 'e2e-seed'``
* ``document.file_hash LIKE 'e2e-%'``
* ``external_dataset.name LIKE 'e2e_%'`` (literal underscore, escaped)

Defense-in-depth behind the dev/e2e Postgres split (ABS-428) and the dev-DB
purge (ABS-429): the e2e suite now runs on its own ephemeral instance, so any
marker row in a dev or prod database is contamination that would silently
poison manual testing and real answers. This script is the shared sweep used
by the ``scripts/dev-up.sh`` boot preflight, the ABS-420 prod curation pass,
and ad-hoc audits.

Read-only: issues SELECTs only — safe to point at prod.

Exit codes::

    0  — clean (no marker rows), or the marker tables don't exist yet
         (fresh database — nothing can be contaminated)
    2  — contamination found (offending rows listed on stderr)
    1  — the sweep itself failed (bad URL, unreachable DB, ...)

Usage::

    DATABASE_URL=postgresql+psycopg://layer1:layer1@localhost:5432/layer1 \\
        .venv/bin/python scripts/check_e2e_contamination.py

    # Explicit URL override + JSON report on stdout:
    .venv/bin/python scripts/check_e2e_contamination.py --db-url ... --json
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import inspect

from bylaw_retrieval.retrieval import audit_e2e_contamination
from layer1.db.session import session_scope

RED = "\033[31;1m"
GREEN = "\033[32m"
RESET = "\033[0m"

_MARKER_TABLES = ("document", "external_dataset")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-url", default=None, help="Database URL override (default: $DATABASE_URL)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full E2eContaminationReport as JSON on stdout.",
    )
    args = parser.parse_args(argv)

    with session_scope(args.db_url) as session:
        inspector = inspect(session.get_bind())
        existing = {t for t in _MARKER_TABLES if inspector.has_table(t)}
        if not existing:
            print(
                "e2e-contamination sweep: marker tables do not exist yet "
                "(fresh database) — nothing to check."
            )
            return 0
        report = audit_e2e_contamination(session)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))

    if report.contaminated:
        counts = report.marker_counts
        print(
            f"{RED}"
            "================================================================\n"
            " E2E CONTAMINATION DETECTED — refusing to treat this database\n"
            " as a clean dev/prod corpus (ABS-432 tripwire).\n"
            "================================================================"
            f"{RESET}",
            file=sys.stderr,
        )
        print(
            f"  marker counts: parser_version='e2e-seed': {counts['document_parser_version']}, "
            f"file_hash 'e2e-%': {counts['document_file_hash']}, "
            f"external_dataset 'e2e_%': {counts['external_dataset_name']}",
            file=sys.stderr,
        )
        for marker in report.markers:
            print(f"  - [{' + '.join(marker.marker_kinds)}] {marker.detail}", file=sys.stderr)
        print(
            "\n  These rows carry e2e-suite fingerprints and do not belong in a\n"
            "  non-test database. Delete them (or re-run the ABS-429 purge)\n"
            "  before booting against this DB.",
            file=sys.stderr,
        )
        return 2

    print(f"{GREEN}e2e-contamination sweep: clean — zero e2e marker rows.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
