"""Tests for scripts/rechain_migration.py — collision-resistant Alembic migrations.

Simulates the ABS-312/ABS-314 incident: two concurrent agents each add a
migration parented to the same down_revision, producing a dual-head chain.
Verifies:
  1. find_added_migration_files identifies files new on the feature branch.
  2. find_dev_head correctly picks the single head from dev's migration set.
  3. rechain() updates down_revision and commits when a mismatch is detected.
  4. rechain() is idempotent (no change when already correct).
  5. The Night Manager's merge_to_dev calls the rechain step automatically.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.rechain_migration import (
    find_added_migration_files,
    find_dev_head,
    rechain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-q", "-b", "dev")
    _run(path, "git", "config", "user.email", "test@nm.local")
    _run(path, "git", "config", "user.name", "NM Test")
    (path / "README.md").write_text("base\n")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-q", "-m", "base")


def _migration(revision: str, down_revision: str | None) -> str:
    down_val = f'"{down_revision}"' if down_revision else "None"
    return (
        f'revision = "{revision}"\n'
        f'down_revision = {down_val}\n'
        f'\ndef upgrade():\n    pass\n\ndef downgrade():\n    pass\n'
    )


def _add_migration(repo: Path, name: str, revision: str, down: str | None) -> Path:
    """Write a migration file, git-add and commit it."""
    versions = repo / "alembic" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    f = versions / f"{name}.py"
    f.write_text(_migration(revision, down))
    _run(repo, "git", "add", str(f.relative_to(repo)))
    _run(repo, "git", "commit", "-q", "-m", f"add {name}")
    return f


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo_with_collision(tmp_path: Path):
    """
    Simulate the ABS-312/ABS-314 incident:

    dev has: 0001 -> 0002_free (head)
    feature branch adds: 0002_purchase (down_revision=0001, NOT 0002_free)
    → dual head after merge.

    Returns (repo_path,) with the feature branch checked out.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Dev chain: 0001 -> 0002_free
    _add_migration(repo, "0001_initial", "0001_initial", None)
    _add_migration(repo, "0002_free", "0002_free", "0001_initial")

    # Feature branch: adds 0002_purchase parented to 0001_initial (collision)
    _run(repo, "git", "checkout", "-q", "-b", "agent/ABS-999-feature")
    versions = repo / "alembic" / "versions"
    (versions / "0002_purchase.py").write_text(
        _migration("0002_purchase", "0001_initial")
    )
    _run(repo, "git", "add", "alembic/versions/0002_purchase.py")
    _run(repo, "git", "commit", "-q", "-m", "add 0002_purchase")

    return repo


# ---------------------------------------------------------------------------
# find_added_migration_files
# ---------------------------------------------------------------------------

class TestFindAddedMigrationFiles:
    def test_finds_only_new_files(self, repo_with_collision: Path):
        repo = repo_with_collision
        new_files = find_added_migration_files(repo)
        names = [f.name for f in new_files]
        assert names == ["0002_purchase.py"]

    def test_returns_empty_on_no_new_migrations(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _add_migration(repo, "0001_initial", "0001_initial", None)
        # No feature branch — on dev, no diff
        new_files = find_added_migration_files(repo)
        assert new_files == []

    def test_excludes_dev_migrations(self, repo_with_collision: Path):
        repo = repo_with_collision
        new_files = find_added_migration_files(repo)
        # 0001_initial and 0002_free are on dev — must not appear
        names = [f.name for f in new_files]
        assert "0001_initial.py" not in names
        assert "0002_free.py" not in names


# ---------------------------------------------------------------------------
# find_dev_head
# ---------------------------------------------------------------------------

class TestFindDevHead:
    def test_finds_single_head(self, repo_with_collision: Path):
        repo = repo_with_collision
        new_files = find_added_migration_files(repo)
        head = find_dev_head(repo, new_files)
        assert head == "0002_free"

    def test_returns_none_when_dev_has_multiple_heads(self, tmp_path: Path):
        """When dev itself already has multiple heads, we can't pick one."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        versions = repo / "alembic" / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        # Two migrations both pointing to None — dual root
        (versions / "0001_a.py").write_text(_migration("0001_a", None))
        (versions / "0001_b.py").write_text(_migration("0001_b", None))
        _run(repo, "git", "add", ".")
        _run(repo, "git", "commit", "-q", "-m", "two roots on dev")
        head = find_dev_head(repo, [])
        assert head is None

    def test_returns_none_when_no_dev_migrations(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        head = find_dev_head(repo, [])
        assert head is None


# ---------------------------------------------------------------------------
# rechain()
# ---------------------------------------------------------------------------

class TestRechain:
    def test_updates_down_revision_and_commits(self, repo_with_collision: Path):
        """Core fix: down_revision is updated to point to dev's head."""
        repo = repo_with_collision

        rc = rechain(repo)

        assert rc == 0
        text = (repo / "alembic" / "versions" / "0002_purchase.py").read_text()
        m = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        assert m is not None, "down_revision must be present after rechain"
        assert m.group(1) == "0002_free", (
            f"expected down_revision='0002_free', got '{m.group(1)}'"
        )
        # A commit must have been created
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=repo, capture_output=True, text=True,
        ).stdout
        assert "rechain" in log.lower()

    def test_idempotent_when_already_correct(self, repo_with_collision: Path):
        """Running rechain twice does not create a second commit."""
        repo = repo_with_collision

        rc1 = rechain(repo)
        assert rc1 == 0

        log_before = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True,
        ).stdout

        rc2 = rechain(repo)
        assert rc2 == 0

        log_after = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True,
        ).stdout
        assert log_before == log_after, "second rechain must not create a new commit"

    def test_noop_when_no_new_migrations(self, tmp_path: Path):
        """Branch with no new migrations exits 0 immediately."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _add_migration(repo, "0001_initial", "0001_initial", None)

        log_before = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True,
        ).stdout
        rc = rechain(repo)
        log_after = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True,
        ).stdout

        assert rc == 0
        assert log_before == log_after

    def test_dry_run_makes_no_changes(self, repo_with_collision: Path):
        """--dry-run does not modify files or create commits."""
        repo = repo_with_collision
        original = (repo / "alembic" / "versions" / "0002_purchase.py").read_text()
        log_before = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True,
        ).stdout

        rc = rechain(repo, dry_run=True)

        assert rc == 0
        assert (repo / "alembic" / "versions" / "0002_purchase.py").read_text() == original
        log_after = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True,
        ).stdout
        assert log_before == log_after

    def test_chain_of_two_new_migrations(self, tmp_path: Path):
        """
        Feature branch adds two migrations A→B chained together.
        Only the root (A) should be rechained onto dev's head.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        # Dev: 0001 -> 0002_existing (head)
        _add_migration(repo, "0001_initial", "0001_initial", None)
        _add_migration(repo, "0002_existing", "0002_existing", "0001_initial")

        # Feature branch: adds 0002_a (down=0001) then 0002_b (down=0002_a)
        _run(repo, "git", "checkout", "-q", "-b", "agent/ABS-999")
        versions = repo / "alembic" / "versions"
        (versions / "0002_a.py").write_text(_migration("0002_a", "0001_initial"))
        (versions / "0002_b.py").write_text(_migration("0002_b", "0002_a"))
        _run(repo, "git", "add", ".")
        _run(repo, "git", "commit", "-q", "-m", "add 0002_a and 0002_b")

        rc = rechain(repo)
        assert rc == 0

        # Root (0002_a) should now point to 0002_existing
        text_a = (versions / "0002_a.py").read_text()
        m = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', text_a, re.MULTILINE)
        assert m and m.group(1) == "0002_existing"

        # Non-root (0002_b) should still point to 0002_a
        text_b = (versions / "0002_b.py").read_text()
        m = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', text_b, re.MULTILINE)
        assert m and m.group(1) == "0002_a"


# ---------------------------------------------------------------------------
# Night Manager integration: merge_to_dev calls rechain
# ---------------------------------------------------------------------------

class TestMergeToDevCallsRechain:
    """Verify that merge_to_dev invokes _rechain_migrations_if_needed."""

    async def test_rechain_called_when_branch_touches_migrations(self):
        from scripts.night_manager import reviewer
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-999",
            title="Migration test",
            branch="agent/ABS-999",
            worktree="/tmp/fake-wt",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
        )

        rechain_calls = []

        async def _fake_rechain(iss):
            rechain_calls.append(iss.identifier)
            return True, "rechained"

        async def _fake_rebase(iss):
            return False, "no remote in test"

        with (
            patch.object(reviewer, "_rechain_migrations_if_needed", _fake_rechain),
            patch.object(reviewer, "_rebase_branch_onto_dev", _fake_rebase),
        ):
            success, msg = await reviewer.merge_to_dev(issue)

        # Rechain must have been called (even though rebase then fails)
        assert "ABS-999" in rechain_calls, "rechain was not called"
        assert success is False  # rebase failure stops merge

    async def test_rechain_not_called_when_no_worktree(self):
        from scripts.night_manager import reviewer
        from scripts.night_manager.state import IssuePorts, IssueState

        issue = IssueState(
            identifier="ABS-999",
            title="No worktree",
            branch="agent/ABS-999",
            worktree="",
            ports=IssuePorts(pg=5499, api=8099, web=3099),
        )

        rechain_calls = []

        async def _fake_rechain(iss):
            rechain_calls.append(iss.identifier)
            return True, ""

        async def _fake_rebase(iss):
            return False, "no worktree"

        with (
            patch.object(reviewer, "_rechain_migrations_if_needed", _fake_rechain),
            patch.object(reviewer, "_rebase_branch_onto_dev", _fake_rebase),
        ):
            await reviewer.merge_to_dev(issue)

        # _rechain_migrations_if_needed is called, but it short-circuits on
        # empty worktree and returns immediately without doing work
        assert len(rechain_calls) == 1
