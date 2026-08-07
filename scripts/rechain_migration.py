#!/usr/bin/env python3
"""
Rechain Alembic migrations on a feature branch onto dev's current head.

When two concurrent agents both create a migration parented to the same
down_revision, merging them produces a dual-head chain that breaks
`alembic upgrade head`. This script detects the mismatch and fixes it
before the merge:

1. Finds migration files ADDED by the feature branch (vs dev).
2. Identifies the "root" of the new chain — the migration whose
   down_revision points to a revision already present on dev.
3. If that down_revision != dev's current single head, rechains it.
4. Commits the rechain fix under a "[rechain]" message.

Idempotent: if the root migration already points to dev's head, exits 0
with no changes.

Usage:
    python scripts/rechain_migration.py [--worktree PATH] [--dry-run]

Run it by hand from a feature-branch worktree when `e2e-up` reports
"Multiple Alembic heads detected" (see docs/E2E_TESTING.md). The Night
Manager — which lives in its own repo, see docs/NIGHT_MANAGER.md — also
invokes it before merging a branch that touches alembic/versions/.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Matches: revision = "0021_foo" or revision = '0021_foo'
REVISION_RE = re.compile(r"^revision\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
# Matches: down_revision = "0020_foo" or down_revision = None
DOWN_RE = re.compile(
    r"^down_revision\s*=\s*(?:[\"']([^\"']+)[\"']|(None))", re.MULTILINE
)


def _parse_revision(text: str) -> str | None:
    m = REVISION_RE.search(text)
    return m.group(1) if m else None


def _parse_down_revision(text: str) -> str | None:
    """Return the down_revision value, or None for the root migration."""
    m = DOWN_RE.search(text)
    if not m:
        return None
    # group(1) = quoted value, group(2) = 'None' literal
    return m.group(1)  # None when group(1) didn't match (i.e. bare None)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def find_added_migration_files(worktree: Path) -> list[Path]:
    """Return Path list of migration files ADDED by the feature branch vs dev."""
    try:
        diff_output = _git(
            ["diff", "--diff-filter=A", "--name-only", "dev...HEAD",
             "--", "alembic/versions/"],
            worktree,
        )
    except subprocess.CalledProcessError:
        return []
    paths = []
    for line in diff_output.splitlines():
        line = line.strip()
        if line.endswith(".py"):
            p = worktree / line
            if p.exists():
                paths.append(p)
    return paths


def _load_revisions(files: list[Path]) -> dict[str, dict]:
    """Parse each file into {revision_id: {file, down, text}}."""
    revisions: dict[str, dict] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        rev = _parse_revision(text)
        down = _parse_down_revision(text)
        if rev:
            revisions[rev] = {"file": f, "down": down, "text": text}
    return revisions


def find_dev_head(worktree: Path, new_files: list[Path]) -> str | None:
    """
    Determine dev's current single head revision ID.

    Reads all migration files present in the worktree, excludes the ones
    added by the feature branch, then finds the revision that no other dev
    migration references as down_revision.

    Returns None when dev has 0 or multiple heads (caller skips rechain).
    """
    all_files = list((worktree / "alembic" / "versions").glob("*.py"))
    dev_files = [f for f in all_files if f not in new_files]
    if not dev_files:
        return None

    dev_revisions = _load_revisions(dev_files)
    if not dev_revisions:
        return None

    all_down = {data["down"] for data in dev_revisions.values()} - {None}
    heads = [rev for rev in dev_revisions if rev not in all_down]
    if len(heads) == 1:
        return heads[0]
    return None


def rechain(worktree: Path, dry_run: bool = False) -> int:
    """Main rechain logic. Returns 0 on success, 1 on unrecoverable error."""
    new_files = find_added_migration_files(worktree)
    if not new_files:
        print("rechain: no new migration files on this branch — nothing to do")
        return 0

    print(f"rechain: found {len(new_files)} new migration file(s):")
    for f in new_files:
        print(f"  {f.name}")

    dev_head = find_dev_head(worktree, new_files)
    if dev_head is None:
        print(
            "rechain: WARNING — cannot determine dev's single head "
            "(0 or multiple heads on dev). Skipping automatic rechain.\n"
            "rechain: The single-head guard in e2e-up.sh will catch this "
            "at migration time."
        )
        return 0

    print(f"rechain: dev head is '{dev_head}'")

    new_revisions = _load_revisions(new_files)
    new_rev_ids = set(new_revisions.keys())

    # Root = new migration whose down_revision is NOT another new revision
    roots = [
        rev for rev, data in new_revisions.items()
        if data["down"] not in new_rev_ids
    ]

    if len(roots) != 1:
        print(
            f"rechain: WARNING — expected 1 chain root, found {len(roots)}: {roots}. "
            "Skipping automatic rechain."
        )
        return 0

    root_rev = roots[0]
    root_data = new_revisions[root_rev]
    current_down = root_data["down"]

    if current_down == dev_head:
        print(
            f"rechain: root '{root_rev}' already points to dev head '{dev_head}' "
            "— no rechain needed"
        )
        return 0

    print(
        f"rechain: root '{root_rev}' has down_revision='{current_down}', "
        f"updating to '{dev_head}'"
    )

    new_text = DOWN_RE.sub(
        lambda m: m.group(0).replace(
            m.group(1) or "None",
            dev_head,
        ),
        root_data["text"],
        count=1,
    )
    # Safer targeted replacement: rewrite the specific down_revision line
    new_text = re.sub(
        r"^(down_revision\s*=\s*)[\"'][^\"']+[\"']",
        rf"\g<1>'{dev_head}'",
        root_data["text"],
        count=1,
        flags=re.MULTILINE,
    )

    if dry_run:
        print(f"rechain: [DRY RUN] would update {root_data['file'].name}")
        return 0

    root_data["file"].write_text(new_text, encoding="utf-8")
    print(f"rechain: updated {root_data['file'].name}")

    # Stage and commit the change
    rel_path = str(root_data["file"].relative_to(worktree))
    subprocess.run(
        ["git", "add", rel_path],
        cwd=worktree,
        check=True,
    )
    subprocess.run(
        [
            "git", "commit", "-m",
            f"[rechain] {root_rev}: update down_revision from "
            f"'{current_down}' to '{dev_head}'",
        ],
        cwd=worktree,
        check=True,
    )
    print("rechain: committed rechain fix")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worktree",
        default=".",
        help="Path to the git worktree root (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying files",
    )
    args = parser.parse_args()
    return rechain(Path(args.worktree).resolve(), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
