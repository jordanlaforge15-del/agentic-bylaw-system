#!/usr/bin/env python
"""Report when a database's ``alembic_version`` is behind this branch (ABS-499).

The Data Model 3.0 post-mortem found the dev DB stamped
``0025_signup_grant_unique`` with ``0026_drop_parcel_zone_code`` still pending:
the *data* migrations of DM3.0 had been applied while the *schema* migration
had not. Nothing said so.

This is the explicit check. The comparison itself lives in
``layer1.db.migration_drift``, which the migration fence also uses to warn when
a data migration is about to run against a database that is behind.

Usage:

    make check-migration-drift                              # uses DB_URL
    python scripts/check_migration_drift.py                 # uses DATABASE_URL
    python scripts/check_migration_drift.py --database-url ...
    python scripts/check_migration_drift.py --exit-zero     # report only

Exit codes:
    0  in sync (or --exit-zero)
    1  behind — one or more migrations pending
    2  could not determine (unreachable DB, unknown revision, ahead of branch)
"""
from __future__ import annotations

import argparse
import sys

from layer1.db.migration_drift import drift_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Always exit 0; report drift without failing the caller.",
    )
    args = parser.parse_args(argv)

    database_url = args.database_url
    if not database_url:
        from layer1.config import get_settings

        database_url = get_settings().database_url

    report = drift_report(database_url)
    print(report.render())

    if args.exit_zero:
        return 0
    if report.error:
        return 2
    return 1 if report.is_behind else 0


if __name__ == "__main__":
    sys.exit(main())
