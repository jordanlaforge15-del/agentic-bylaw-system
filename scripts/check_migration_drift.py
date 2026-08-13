#!/usr/bin/env python
"""Report when a database's ``alembic_version`` is behind this branch (ABS-499).

The Data Model 3.0 post-mortem found the dev DB stamped
``0025_signup_grant_unique`` with ``0026_drop_parcel_zone_code`` still pending:
the *data* migrations of DM3.0 had been applied while the *schema* migration
had not. Nothing said so. The split state was only discovered because someone
went looking.

This makes it loud. Compare the revision(s) recorded in the target database
against the head(s) of ``alembic/versions`` on the current branch and print
every migration in between.

Usage:

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
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@dataclass(frozen=True)
class PendingMigration:
    revision: str
    path: str
    summary: str


@dataclass(frozen=True)
class DriftReport:
    current: tuple[str, ...]
    heads: tuple[str, ...]
    pending: tuple[PendingMigration, ...]
    error: str | None = None

    @property
    def is_behind(self) -> bool:
        return bool(self.pending)

    def render(self) -> str:
        lines: list[str] = []
        current = ", ".join(self.current) if self.current else "<none — never stamped>"
        heads = ", ".join(self.heads) if self.heads else "<none>"
        lines.append(f"alembic_version : {current}")
        lines.append(f"branch head     : {heads}")
        if self.error:
            lines.append(f"ERROR           : {self.error}")
            return "\n".join(lines)
        if not self.pending:
            lines.append("status          : in sync — no pending migrations")
            return "\n".join(lines)
        lines.append(f"status          : BEHIND — {len(self.pending)} migration(s) pending")
        for item in self.pending:
            lines.append(f"  - {item.revision}  ({item.path})")
            if item.summary:
                lines.append(f"      {item.summary}")
        lines.append("")
        lines.append("Apply with:  make migrate")
        lines.append(
            "A pending *schema* migration alongside applied *data* migrations is the "
            "split state ABS-499 exists to surface — resolve it before running more "
            "data migrations."
        )
        return "\n".join(lines)


def load_script_directory(config_path: Path = ALEMBIC_INI) -> ScriptDirectory:
    config = Config(str(config_path))
    # alembic.ini's script_location is repo-relative; resolve it so the check
    # works from any cwd.
    location = config.get_main_option("script_location") or "alembic"
    config.set_main_option("script_location", str(config_path.parent / location))
    return ScriptDirectory.from_config(config)


def compute_drift(current: tuple[str, ...], script: ScriptDirectory) -> DriftReport:
    """Pure comparison: what stands between ``current`` and the branch head(s)?"""
    heads = tuple(script.get_heads())
    try:
        # walk_revisions yields head -> base; everything after the current
        # revision(s) is already applied.
        revisions = list(script.walk_revisions("base", "heads"))
    except Exception as exc:  # pragma: no cover - corrupt version directory
        return DriftReport(current=current, heads=heads, pending=(), error=str(exc))

    known = {rev.revision for rev in revisions}
    unknown = [rev for rev in current if rev not in known]
    if unknown:
        return DriftReport(
            current=current,
            heads=heads,
            pending=(),
            error=(
                f"revision(s) {', '.join(unknown)} are recorded in the database but do "
                "not exist in alembic/versions on this branch — the DB is ahead of, or "
                "diverged from, this checkout"
            ),
        )

    # Everything reachable walking down from each recorded revision is applied.
    applied: set[str] = set()
    for rev in current:
        for ancestor in script.iterate_revisions(rev, "base"):
            applied.add(ancestor.revision)

    pending = [rev for rev in reversed(revisions) if rev.revision not in applied]
    return DriftReport(
        current=current,
        heads=heads,
        pending=tuple(
            PendingMigration(
                revision=rev.revision,
                path=str(Path(rev.path).relative_to(REPO_ROOT))
                if str(rev.path).startswith(str(REPO_ROOT))
                else str(rev.path),
                summary=(rev.doc or "").strip().splitlines()[0] if rev.doc else "",
            )
            for rev in pending
        ),
    )


def read_current_revisions(database_url: str) -> tuple[str, ...]:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as conn:
            has_table = conn.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar()
            if not has_table:
                return ()
            rows = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            return tuple(sorted(rows))
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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

    script = load_script_directory()
    try:
        current = read_current_revisions(database_url)
    except Exception as exc:
        report = DriftReport(
            current=(),
            heads=tuple(script.get_heads()),
            pending=(),
            error=f"could not read alembic_version: {exc}",
        )
        print(report.render())
        return 0 if args.exit_zero else 2

    report = compute_drift(current, script)
    print(report.render())
    if args.exit_zero:
        return 0
    if report.error:
        return 2
    return 1 if report.is_behind else 0


if __name__ == "__main__":
    sys.exit(main())
