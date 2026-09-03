"""Coverage for scripts/install-prod-backup-cron.sh (ABS-131).

The installer is what turns two scripts into an actual backup policy. The
things worth pinning down are the ways it could appear to work while leaving
the host unprotected:

* a cron line that runs with cron's bare environment, so the Storage Box
  target is unset and every dump quietly stays local;
* an install that stacks duplicate entries every time someone re-runs it;
* an uninstall that takes unrelated cron jobs with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prod_backup_shims import CRON_SCRIPT, base_env, run_script, write_shims

UNRELATED = "@daily /usr/local/bin/renew-certs.sh  # someone else's job"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    write_shims(tmp_path / "bin")
    call_env = base_env(
        bin_dir=tmp_path / "bin",
        state_dir=tmp_path / "state",
        backup_dir=tmp_path / "backups",
    )
    env_file = tmp_path / "backup.env"
    env_file.write_text("BYLAW_STORAGE_BOX_TARGET=u1@u1.your-storagebox.de:prod\n")
    call_env["BYLAW_BACKUP_ENV_FILE"] = str(env_file)
    call_env["BYLAW_BACKUP_CRON_LOG"] = str(tmp_path / "backups" / "cron.log")
    call_env["FAKE_CRONTAB_FILE"] = str(tmp_path / "crontab")
    return call_env


def crontab_of(env: dict[str, str]) -> str:
    path = Path(env["FAKE_CRONTAB_FILE"])
    return path.read_text() if path.exists() else ""


def install(env: dict[str, str], args: list[str] | None = None):
    return run_script(CRON_SCRIPT, env, args)


def test_installs_both_jobs(env: dict[str, str]) -> None:
    result = install(env)

    assert result.returncode == 0, result.stderr
    lines = crontab_of(env).strip().splitlines()
    assert len(lines) == 2, lines
    backup_line, verify_line = lines
    assert backup_line.startswith("30 2 * * *")
    assert "backup-prod-db.sh" in backup_line
    # The restore test runs weekly, after that morning's dump — so a week of
    # silently corrupt dumps surfaces in seven days, not during an outage.
    assert verify_line.startswith("0 4 * * 0")
    assert "verify-prod-backup.sh --restore" in verify_line


def test_jobs_load_the_env_file_before_running(env: dict[str, str]) -> None:
    """cron does not read a login shell's profile. Without this the backup
    script runs with no Storage Box target and no passphrase — it would
    succeed locally and never leave the host."""
    install(env)

    for line in crontab_of(env).strip().splitlines():
        assert f". {env['BYLAW_BACKUP_ENV_FILE']}" in line
        assert "set -a" in line
        # docker is not on cron's default PATH on every install.
        assert "PATH=" in line


def test_jobs_capture_output_somewhere_readable(env: dict[str, str]) -> None:
    install(env)

    for line in crontab_of(env).strip().splitlines():
        assert f">> {env['BYLAW_BACKUP_CRON_LOG']} 2>&1" in line


def test_reinstall_is_idempotent(env: dict[str, str]) -> None:
    install(env)
    first = crontab_of(env)

    assert install(env).returncode == 0
    assert install(env).returncode == 0

    assert crontab_of(env) == first


def test_install_preserves_unrelated_entries(env: dict[str, str]) -> None:
    Path(env["FAKE_CRONTAB_FILE"]).write_text(UNRELATED + "\n")

    install(env)

    assert UNRELATED in crontab_of(env)
    assert len(crontab_of(env).strip().splitlines()) == 3


def test_uninstall_removes_only_our_entries(env: dict[str, str]) -> None:
    Path(env["FAKE_CRONTAB_FILE"]).write_text(UNRELATED + "\n")
    install(env)

    result = install(env, ["--uninstall"])

    assert result.returncode == 0, result.stderr
    assert crontab_of(env).strip() == UNRELATED


def test_uninstall_clears_a_crontab_we_wholly_own(env: dict[str, str]) -> None:
    install(env)

    assert install(env, ["--uninstall"]).returncode == 0
    assert crontab_of(env).strip() == ""


def test_refuses_to_install_without_the_env_file(env: dict[str, str]) -> None:
    """Installing against a missing env file would schedule a job that can
    never ship offsite — the exact silent failure this ticket exists to end."""
    env["BYLAW_BACKUP_ENV_FILE"] = "/nonexistent/backup.env"

    result = install(env)

    assert result.returncode != 0
    assert "is missing or unreadable" in result.stderr
    assert crontab_of(env).strip() == ""


def test_show_lists_installed_entries(env: dict[str, str]) -> None:
    install(env)

    result = install(env, ["--show"])

    assert result.returncode == 0, result.stderr
    assert "backup-prod-db.sh" in result.stdout
    assert "verify-prod-backup.sh" in result.stdout


def test_show_on_a_clean_host_says_so(env: dict[str, str]) -> None:
    result = install(env, ["--show"])

    assert result.returncode == 0, result.stderr
    assert "no backup cron entries installed" in result.stdout
