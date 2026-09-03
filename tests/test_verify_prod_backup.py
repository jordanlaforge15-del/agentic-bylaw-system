"""Coverage for scripts/verify-prod-backup.sh (ABS-131).

A backup nobody has restored is a hypothesis. This script is the experiment:
decrypt, restore into a scratch Postgres, query the restored tables. The tests
below check that it reports honestly in both directions — that it passes only
when the data genuinely came back, and that every way the restore can go wrong
(bad passphrase, unreadable archive, half-restored schema, a scratch instance
that never starts) produces a failure rather than a reassuring PASS.

The last group matters as much as the rest: the scratch instance holds a full
copy of the user table, so it must be destroyed on every exit path.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from prod_backup_shims import (
    BACKUP_SCRIPT,
    VERIFY_SCRIPT,
    base_env,
    run_script,
    write_shims,
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    write_shims(tmp_path / "bin")
    return base_env(
        bin_dir=tmp_path / "bin",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
    )


def backup_dir_of(env: dict[str, str]) -> Path:
    return Path(env["BYLAW_PROD_BACKUP_DIR"])


def make_backup(env: dict[str, str], *, on: date | None = None) -> None:
    """Produce a real artifact with the real backup script."""
    call_env = dict(env)
    if on is not None:
        call_env["BYLAW_BACKUP_DATE"] = on.isoformat()
    result = run_script(BACKUP_SCRIPT, call_env, ["--no-offsite"])
    assert result.returncode == 0, result.stderr


def verify(env: dict[str, str], args: list[str] | None = None):
    return run_script(VERIFY_SCRIPT, env, args)


def with_passphrase(env: dict[str, str], tmp_path: Path, secret: str = "s3kr3t") -> Path:
    passfile = tmp_path / "backup.pass"
    passfile.write_text(secret)
    env["BYLAW_BACKUP_PASSPHRASE_FILE"] = str(passfile)
    return passfile


def containers_left(env: dict[str, str]) -> list[str]:
    state = Path(env["FAKE_DOCKER_STATE"])
    if not state.exists():
        return []
    return sorted(p.name for p in state.glob("*.up"))


def docker_calls(env: dict[str, str]) -> str:
    log = Path(env["FAKE_DOCKER_STATE"]) / "calls.log"
    return log.read_text() if log.exists() else ""


def assert_scratch_was_started_and_removed(env: dict[str, str]) -> None:
    """Guards against the vacuous pass: no container left because none ran."""
    calls = docker_calls(env)
    assert "--name bylaw-backup-verify-" in calls, calls
    assert "rm -f bylaw-backup-verify-" in calls, calls
    assert containers_left(env) == []


# --- level 1: archive check ----------------------------------------------


def test_archive_check_passes_on_a_good_artifact(env: dict[str, str]) -> None:
    make_backup(env)

    result = verify(env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PASS:")
    assert "archive check" in result.stdout


def test_archive_check_rejects_a_corrupt_artifact(env: dict[str, str]) -> None:
    make_backup(env)
    artifact = next(backup_dir_of(env).glob("layer1-prod-*.dump"))
    artifact.write_text("garbage that is not a postgres archive\n")

    result = verify(env)

    assert result.returncode != 0
    assert "FAIL" in result.stderr
    assert "PASS" not in result.stdout


def test_archive_check_rejects_a_table_less_archive(env: dict[str, str]) -> None:
    """A syntactically valid but empty dump is not a backup of this system."""
    env["FAKE_PGDUMP_TABLES"] = "geocode_cache"
    artifact = backup_dir_of(env) / "layer1-prod-Mon.dump"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("PGDMP-2026-06-01\ntables=geocode_cache\n")

    result = verify(env, ["--file", str(artifact)])

    assert result.returncode != 0
    assert "missing required table" in result.stderr
    assert "advisor_user" in result.stderr


def test_empty_artifact_is_rejected(env: dict[str, str]) -> None:
    artifact = backup_dir_of(env) / "layer1-prod-Mon.dump"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.touch()

    result = verify(env, ["--file", str(artifact)])

    assert result.returncode != 0
    assert "missing or empty" in result.stderr


def test_no_artifacts_at_all_is_a_failure_not_a_pass(env: dict[str, str]) -> None:
    backup_dir_of(env).mkdir(parents=True, exist_ok=True)

    result = verify(env)

    assert result.returncode != 0
    assert "no layer1-prod-* artifact found" in result.stderr


def test_verifies_the_newest_artifact_by_default(env: dict[str, str]) -> None:
    """"Is last night's backup good?" means the newest one, not an arbitrary one."""
    for offset in range(3):
        make_backup(env, on=date(2026, 6, 1) + timedelta(days=offset))
    newest = max(backup_dir_of(env).glob("layer1-prod-*.dump"), key=lambda p: p.stat().st_mtime)

    result = verify(env)

    assert result.returncode == 0, result.stderr
    assert newest.name in result.stdout


# --- encrypted artifacts -------------------------------------------------


def test_encrypted_artifact_verifies_with_the_right_passphrase(
    env: dict[str, str], tmp_path: Path
) -> None:
    with_passphrase(env, tmp_path)
    make_backup(env)

    result = verify(env)

    assert result.returncode == 0, result.stderr
    assert ".dump.gpg" in result.stdout


def test_wrong_passphrase_fails_instead_of_passing_quietly(
    env: dict[str, str], tmp_path: Path
) -> None:
    """The failure an untested backup hides for months: the artifact is fine
    and the key on the box is not the key that made it."""
    with_passphrase(env, tmp_path, secret="the-real-one")
    make_backup(env)

    (tmp_path / "backup.pass").write_text("a-stale-copy")
    result = verify(env)

    assert result.returncode != 0
    assert "could not decrypt" in result.stderr


def test_encrypted_artifact_without_a_passphrase_is_a_failure(
    env: dict[str, str], tmp_path: Path
) -> None:
    with_passphrase(env, tmp_path)
    make_backup(env)

    del env["BYLAW_BACKUP_PASSPHRASE_FILE"]
    result = verify(env)

    assert result.returncode != 0
    assert "BYLAW_BACKUP_PASSPHRASE_FILE is unset" in result.stderr


# --- level 2: full restore ------------------------------------------------


def test_restore_reports_row_counts_for_every_required_table(
    env: dict[str, str],
) -> None:
    make_backup(env)

    result = verify(env, ["--restore"])

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PASS:")
    assert "full restore" in result.stdout
    for table in ("advisor_user", "advisor_case", "advisor_usage_event", "alembic_version"):
        assert f"{table}=" in result.stdout


def test_restore_of_an_encrypted_artifact_exercises_the_whole_recovery_path(
    env: dict[str, str], tmp_path: Path
) -> None:
    """Decrypt then restore — the exact sequence an operator runs in an outage."""
    with_passphrase(env, tmp_path)
    make_backup(env)

    result = verify(env, ["--restore"])

    assert result.returncode == 0, result.stderr
    assert "advisor_user=3" in result.stdout


def test_restore_fails_when_the_schema_pointer_did_not_survive(
    env: dict[str, str],
) -> None:
    """An empty alembic_version means the restore produced a shell. The next
    `alembic upgrade head` would then replay every migration over live data."""
    make_backup(env)
    env["FAKE_ALEMBIC_ROWS"] = "0"

    result = verify(env, ["--restore"])

    assert result.returncode != 0
    assert "schema pointer did not survive" in result.stderr


def test_restore_fails_when_a_required_table_is_not_queryable(
    env: dict[str, str],
) -> None:
    """The archive can list a table that the restored database then lacks.

    Only a query against the restored data catches that; the table of contents
    is the archive describing itself, which is exactly what's in doubt.
    """
    make_backup(env)
    env["FAKE_RESTORE_DROP"] = "advisor_usage_event"

    result = verify(env, ["--restore"])

    assert result.returncode != 0
    assert "no queryable 'advisor_usage_event'" in result.stderr


def test_scratch_postgres_that_never_starts_fails_within_the_timeout(
    env: dict[str, str],
) -> None:
    make_backup(env)
    env["FAKE_SCRATCH_READY"] = "0"
    env["BYLAW_VERIFY_TIMEOUT"] = "2"

    result = verify(env, ["--restore"])

    assert result.returncode != 0
    assert "did not become ready" in result.stderr


# --- the scratch instance must never outlive the check --------------------


def test_scratch_container_is_destroyed_on_success(env: dict[str, str]) -> None:
    make_backup(env)

    assert verify(env, ["--restore"]).returncode == 0
    assert_scratch_was_started_and_removed(env)


def test_scratch_container_is_destroyed_on_failure(env: dict[str, str]) -> None:
    """It holds a full copy of advisor_user. A leaked container is a leak."""
    make_backup(env)
    env["FAKE_ALEMBIC_ROWS"] = "0"

    assert verify(env, ["--restore"]).returncode != 0
    assert_scratch_was_started_and_removed(env)


def test_decrypted_plaintext_is_not_left_on_disk(
    env: dict[str, str], tmp_path: Path
) -> None:
    """Verification decrypts to a temp dir; that copy must not survive the run.

    Otherwise every weekly restore test leaves another unencrypted snapshot of
    the user table on the production host.
    """
    with_passphrase(env, tmp_path)
    make_backup(env)

    scratch_tmp = tmp_path / "tmpdir"
    scratch_tmp.mkdir()
    env["TMPDIR"] = str(scratch_tmp)

    assert verify(env, ["--restore"]).returncode == 0
    assert list(scratch_tmp.iterdir()) == []


def test_unknown_flag_is_rejected(env: dict[str, str]) -> None:
    result = verify(env, ["--nuke"])

    assert result.returncode == 2
    assert "usage:" in result.stderr
