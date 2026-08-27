"""Coverage for scripts/backup-prod-db.sh (ABS-131).

Production backups protect the only copy of user, billing and audit data that
exists. The contracts asserted here are the ones that decide whether a restore
is possible at 3am:

1. A good dump becomes the day's artifact, with the bytes intact.
2. A dump that fails verification is REJECTED, and yesterday's artifact
   survives untouched. (An accepted-but-corrupt dump is the worst outcome
   available: it reads as protection and isn't.)
3. Plaintext never leaves the server unless someone said so explicitly.
4. Retention holds at 7 daily + 4 weekly, and the offsite mirror matches.

Every external command is a simulator, not a mock — see prod_backup_shims.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from prod_backup_shims import (
    BACKUP_SCRIPT,
    KEEP_WEEKLY,
    MAX_SLOTS,
    artifacts,
    base_env,
    run_script,
    write_shims,
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A configured, offsite-disabled run. Tests opt into offsite explicitly."""
    write_shims(tmp_path / "bin")
    backup_dir = tmp_path / "backups"
    return base_env(
        bin_dir=tmp_path / "bin",
        state_dir=tmp_path / "state",
        backup_dir=backup_dir,
    )


def backup_dir_of(env: dict[str, str]) -> Path:
    return Path(env["BYLAW_PROD_BACKUP_DIR"])


def run_backup(
    env: dict[str, str], *, on: date | None = None, args: list[str] | None = None
):
    call_env = dict(env)
    if on is not None:
        call_env["BYLAW_BACKUP_DATE"] = on.isoformat()
    return run_script(BACKUP_SCRIPT, call_env, args)


def simulate_days(env: dict[str, str], start: date, days: int) -> None:
    for offset in range(days):
        result = run_backup(env, on=start + timedelta(days=offset), args=["--no-offsite"])
        assert result.returncode == 0, result.stderr


def with_passphrase(env: dict[str, str], tmp_path: Path, secret: str = "s3kr3t") -> Path:
    passfile = tmp_path / "backup.pass"
    passfile.write_text(secret)
    env["BYLAW_BACKUP_PASSPHRASE_FILE"] = str(passfile)
    return passfile


def with_storage_box(env: dict[str, str], tmp_path: Path) -> Path:
    remote = tmp_path / "storagebox"
    env["BYLAW_STORAGE_BOX_TARGET"] = f"fakebox:{remote}"
    return remote


# --- the happy path ------------------------------------------------------


def test_writes_daily_artifact_keyed_by_day_of_week(env: dict[str, str]) -> None:
    result = run_backup(env, args=["--no-offsite"])

    assert result.returncode == 0, result.stderr
    backups = backup_dir_of(env)
    expected = f"layer1-prod-{datetime.now().strftime('%a')}.dump"
    dump = backups / expected
    assert dump.exists(), artifacts(backups)
    # pg_dump's bytes reach disk unchanged.
    assert dump.read_text().startswith("PGDMP-")
    assert "advisor_user" in dump.read_text()
    # Nothing half-written is left behind.
    assert not list(backups.glob("*.staging"))
    assert "OK: wrote" in (backups / "backup.log").read_text()


def test_artifact_is_owner_readable_only(env: dict[str, str]) -> None:
    """The artifact holds every user row and every purchase. 0600, not 0644."""
    assert run_backup(env, args=["--no-offsite"]).returncode == 0

    dump = next(backup_dir_of(env).glob("layer1-prod-*.dump"))
    assert dump.stat().st_mode & 0o777 == 0o600


def test_rerun_overwrites_the_days_slot(env: dict[str, str]) -> None:
    assert run_backup(env, args=["--no-offsite"]).returncode == 0
    assert run_backup(env, args=["--no-offsite"]).returncode == 0

    dailies = [
        name for name in artifacts(backup_dir_of(env)) if "weekly" not in name
    ]
    assert len(dailies) == 1, dailies


def test_container_not_running_fails_loudly(env: dict[str, str]) -> None:
    env["FAKE_CONTAINER_RUNNING"] = "false"

    result = run_backup(env, args=["--no-offsite"])

    assert result.returncode != 0
    assert not artifacts(backup_dir_of(env))
    assert "is not running" in (backup_dir_of(env) / "backup.log").read_text()


# --- verification: the reason this script exists --------------------------


@pytest.mark.parametrize(
    ("mode", "expected_log"),
    [
        ("truncated", "archive verification failed"),
        ("empty", "archive verification failed"),
        ("notables", "missing required table"),
    ],
)
def test_unverifiable_dump_is_rejected(
    env: dict[str, str], mode: str, expected_log: str
) -> None:
    """A dump that cannot be read back never becomes "the backup".

    pg_dump's exit status says nothing about the bytes that reached the far
    end of the pipe, so `if pg_dump; then mv` would happily enshrine a
    truncated file as the day's protection.
    """
    env["FAKE_PGDUMP_MODE"] = mode

    result = run_backup(env, args=["--no-offsite"])

    assert result.returncode != 0
    assert not artifacts(backup_dir_of(env)), "a rejected dump must not be promoted"
    assert not list(backup_dir_of(env).glob("*.staging"))
    assert expected_log in (backup_dir_of(env) / "backup.log").read_text()


def test_rejected_dump_leaves_yesterdays_artifact_intact(env: dict[str, str]) -> None:
    """The failure mode that costs a whole recovery: today's bad dump
    overwriting yesterday's good one."""
    monday = date(2026, 6, 1)
    assert run_backup(env, on=monday, args=["--no-offsite"]).returncode == 0
    good = backup_dir_of(env) / "layer1-prod-Mon.dump"
    before = good.read_bytes()

    env["FAKE_PGDUMP_MODE"] = "truncated"
    result = run_backup(env, on=monday, args=["--no-offsite"])

    assert result.returncode != 0
    assert good.read_bytes() == before


def test_pg_dump_failure_keeps_previous_artifact(env: dict[str, str]) -> None:
    monday = date(2026, 6, 1)
    assert run_backup(env, on=monday, args=["--no-offsite"]).returncode == 0
    before = (backup_dir_of(env) / "layer1-prod-Mon.dump").read_bytes()

    env["FAKE_PGDUMP_MODE"] = "fail"
    result = run_backup(env, on=monday, args=["--no-offsite"])

    assert result.returncode != 0
    assert (backup_dir_of(env) / "layer1-prod-Mon.dump").read_bytes() == before
    assert "pg_dump failed" in (backup_dir_of(env) / "backup.log").read_text()


# --- encryption ----------------------------------------------------------


def test_encrypted_artifact_replaces_the_plaintext(
    env: dict[str, str], tmp_path: Path
) -> None:
    with_passphrase(env, tmp_path)

    assert run_backup(env, args=["--no-offsite"]).returncode == 0

    backups = backup_dir_of(env)
    names = artifacts(backups)
    assert all(name.endswith(".dump.gpg") for name in names), names
    # No plaintext copy is left sitting next to the encrypted one.
    assert not list(backups.glob("*.dump"))
    assert not list(backups.glob("*.staging"))
    assert next(backups.glob("*.dump.gpg")).read_text().startswith("GPGWRAP[s3kr3t]")


def test_unreadable_passphrase_file_aborts_before_dumping(
    env: dict[str, str], tmp_path: Path
) -> None:
    env["BYLAW_BACKUP_PASSPHRASE_FILE"] = str(tmp_path / "nope.pass")

    result = run_backup(env, args=["--no-offsite"])

    assert result.returncode != 0
    assert "passphrase file" in (backup_dir_of(env) / "backup.log").read_text()
    assert not artifacts(backup_dir_of(env))


def test_gpg_failure_rejects_the_dump(env: dict[str, str], tmp_path: Path) -> None:
    with_passphrase(env, tmp_path)
    env["FAKE_GPG_MODE"] = "fail"

    result = run_backup(env, args=["--no-offsite"])

    assert result.returncode != 0
    assert not artifacts(backup_dir_of(env))
    assert not list(backup_dir_of(env).glob("*.staging"))


def test_toggling_encryption_does_not_double_the_rotation(
    env: dict[str, str], tmp_path: Path
) -> None:
    """Switching suffixes must retire the slot's old artifact, not shadow it."""
    monday = date(2026, 6, 1)
    assert run_backup(env, on=monday, args=["--no-offsite"]).returncode == 0
    assert (backup_dir_of(env) / "layer1-prod-Mon.dump").exists()

    with_passphrase(env, tmp_path)
    assert run_backup(env, on=monday, args=["--no-offsite"]).returncode == 0

    assert artifacts(backup_dir_of(env)) == [
        "layer1-prod-Mon.dump.gpg",
        "layer1-prod-weekly-2026-W23.dump.gpg",
    ]


# --- the plaintext gate --------------------------------------------------


def test_refuses_to_send_plaintext_offsite(env: dict[str, str], tmp_path: Path) -> None:
    """Shipping advisor_user and advisor_case_purchase to a third-party box in
    the clear is a decision, not a default."""
    remote = with_storage_box(env, tmp_path)

    result = run_backup(env)

    assert result.returncode != 0
    assert "refusing to send an unencrypted dump offsite" in result.stderr
    assert not remote.exists(), "nothing may reach the Storage Box"
    # The gate fires before the dump, so no time is spent on work we'd discard.
    calls = (tmp_path / "state" / "calls.log").read_text() if (
        tmp_path / "state" / "calls.log"
    ).exists() else ""
    assert "pg_dump" not in calls


def test_explicit_override_ships_plaintext(env: dict[str, str], tmp_path: Path) -> None:
    remote = with_storage_box(env, tmp_path)
    env["BYLAW_BACKUP_ALLOW_PLAINTEXT"] = "1"

    result = run_backup(env)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in remote.iterdir() if p.suffix == ".dump")


def test_plaintext_gate_does_not_block_a_local_only_run(env: dict[str, str]) -> None:
    """--no-offsite is the outage escape hatch; it must not trip the gate."""
    result = run_backup(env, args=["--no-offsite"])

    assert result.returncode == 0, result.stderr
    assert "offsite mirror disabled" in result.stderr


# --- offsite mirror ------------------------------------------------------


def test_offsite_mirror_matches_the_local_set(
    env: dict[str, str], tmp_path: Path
) -> None:
    with_passphrase(env, tmp_path)
    remote = with_storage_box(env, tmp_path)

    for offset in range(20):
        result = run_backup(env, on=date(2026, 6, 1) + timedelta(days=offset))
        assert result.returncode == 0, result.stderr

    local = artifacts(backup_dir_of(env))
    offsite = sorted(
        p.name for p in remote.iterdir() if p.suffix in {".dump", ".gpg"}
    )
    assert offsite == local
    assert len(local) <= MAX_SLOTS


def test_mirror_excludes_in_flight_files(env: dict[str, str], tmp_path: Path) -> None:
    with_passphrase(env, tmp_path)
    remote = with_storage_box(env, tmp_path)
    backups = backup_dir_of(env)
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "layer1-prod-Wed.staging").write_text("half a dump")

    assert run_backup(env, on=date(2026, 6, 3)).returncode == 0

    assert not [p.name for p in remote.iterdir() if p.name.endswith(".staging")]


def test_mirror_failure_is_loud_but_keeps_the_local_backup(
    env: dict[str, str], tmp_path: Path
) -> None:
    with_passphrase(env, tmp_path)
    with_storage_box(env, tmp_path)
    env["FAKE_RSYNC_MODE"] = "fail"

    result = run_backup(env, on=date(2026, 6, 1))

    assert result.returncode != 0
    assert (backup_dir_of(env) / "layer1-prod-Mon.dump.gpg").exists()
    assert "NOT offsite" in (backup_dir_of(env) / "backup.log").read_text()


def test_no_storage_box_configured_warns_but_succeeds(env: dict[str, str]) -> None:
    result = run_backup(env)

    assert result.returncode == 0, result.stderr
    assert "LOCAL ONLY" in result.stderr


# --- retention (7 daily + 4 weekly) --------------------------------------


def test_retention_holds_at_seven_daily_plus_four_weekly(env: dict[str, str]) -> None:
    simulate_days(env, date(2026, 1, 1), 60)

    names = artifacts(backup_dir_of(env))
    dailies = [n for n in names if "weekly" not in n]
    weeklies = [n for n in names if "weekly" in n]
    assert len(dailies) == 7, dailies
    assert len(weeklies) == KEEP_WEEKLY, weeklies
    assert len(names) == MAX_SLOTS, names
    assert "PRUNED:" in (backup_dir_of(env) / "backup.log").read_text()


def test_prune_keeps_the_newest_weeks(env: dict[str, str]) -> None:
    simulate_days(env, date(2026, 1, 1), 60)

    weeklies = sorted(p.name for p in backup_dir_of(env).glob("layer1-prod-weekly-*"))
    # 60 days from 2026-01-01 ends 2026-03-01; the four newest ISO weeks survive.
    assert weeklies == [
        "layer1-prod-weekly-2026-W06.dump",
        "layer1-prod-weekly-2026-W07.dump",
        "layer1-prod-weekly-2026-W08.dump",
        "layer1-prod-weekly-2026-W09.dump",
    ]


def test_weekly_tier_reaches_further_back_than_the_daily_tier(
    env: dict[str, str],
) -> None:
    """The point of the second tier: on day 30, day 21 is still restorable."""
    simulate_days(env, date(2026, 1, 1), 30)

    week4 = backup_dir_of(env) / "layer1-prod-weekly-2026-W04.dump"
    assert week4.exists(), artifacts(backup_dir_of(env))
    # W04 opens on 2026-01-19, well outside the 7-day daily horizon.
    assert "PGDMP-2026-01-19" in week4.read_text()


def test_missed_days_still_promote(env: dict[str, str]) -> None:
    """cron misses fire during an outage; promotion keys off "first run of the
    ISO week", not a fixed weekday, so a skipped Sunday costs nothing."""
    for day in (date(2026, 3, 4), date(2026, 3, 12)):
        assert run_backup(env, on=day, args=["--no-offsite"]).returncode == 0

    weeklies = sorted(p.name for p in backup_dir_of(env).glob("layer1-prod-weekly-*"))
    assert weeklies == [
        "layer1-prod-weekly-2026-W10.dump",
        "layer1-prod-weekly-2026-W11.dump",
    ]


def test_prune_ignores_hand_taken_snapshots(env: dict[str, str]) -> None:
    """A pre-migration dump an operator took by hand must survive the rotation."""
    backups = backup_dir_of(env)
    backups.mkdir(parents=True, exist_ok=True)
    keepsake = backups / "layer1-prod-pre-migration-20260601.dump.manual"
    keepsake.write_bytes(b"irreplaceable\n")

    simulate_days(env, date(2026, 1, 1), 60)

    assert keepsake.read_bytes() == b"irreplaceable\n"


# --- dry run -------------------------------------------------------------


def test_dry_run_changes_nothing(env: dict[str, str], tmp_path: Path) -> None:
    with_passphrase(env, tmp_path)
    remote = with_storage_box(env, tmp_path)
    for offset in range(20):
        assert run_backup(env, on=date(2026, 1, 1) + timedelta(days=offset)).returncode == 0

    backups = backup_dir_of(env)
    before = {p.name: p.read_bytes() for p in backups.iterdir()}
    offsite_before = sorted(p.name for p in remote.iterdir())

    result = run_backup(env, on=date(2027, 6, 1), args=["--dry-run"])

    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_bytes() for p in backups.iterdir()} == before
    assert sorted(p.name for p in remote.iterdir()) == offsite_before
    # It still reports the plan.
    assert "would promote" in result.stderr
    assert "would prune" in result.stderr
    assert "would mirror" in result.stderr
    assert "would encrypt" in result.stderr


def test_dry_run_names_the_plaintext_risk(env: dict[str, str], tmp_path: Path) -> None:
    with_storage_box(env, tmp_path)
    env["BYLAW_BACKUP_ALLOW_PLAINTEXT"] = "1"

    result = run_backup(env, args=["--dry-run"])

    assert result.returncode == 0, result.stderr
    assert "would write UNENCRYPTED" in result.stderr


def test_dry_run_doubles_as_a_config_preflight(
    env: dict[str, str], tmp_path: Path
) -> None:
    """A misconfiguration fails the dry run rather than being narrated in it.

    `--dry-run` is what an operator runs after editing backup.env, so the
    useful answer to "offsite target set, no passphrase, no override" is a
    nonzero exit now — not a plan that would have been refused at 02:30.
    """
    with_storage_box(env, tmp_path)

    result = run_backup(env, args=["--dry-run"])

    assert result.returncode != 0
    assert "refusing to send an unencrypted dump offsite" in result.stderr


def test_unknown_flag_is_rejected(env: dict[str, str]) -> None:
    result = run_backup(env, args=["--yolo"])

    assert result.returncode == 2
    assert "usage:" in result.stderr
