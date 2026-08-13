"""Coverage for scripts/backup-dev-db.sh.

The script wraps `docker exec ... pg_dump`. Spinning up real Docker in
pytest is overkill, so each test puts a fake `docker` shim on PATH that
simulates either a running container with a tiny dump payload, or a
missing/stopped container. We assert the script's contracts:

1. Happy path writes a per-DOW dump file with the streamed bytes intact.
2. Re-running on the same day overwrites in place (no second file).
3. Container-not-running exits nonzero and leaves no dump artifact.
4. Weekly / monthly promotion keeps snapshots older than 7 days alive.
5. The explicit prune holds total disk under the documented ceiling.
6. `--dry-run` reports the plan without touching a single file.

The multi-month tests drive the script's `BYLAW_BACKUP_DATE` clock hook
one simulated day at a time — no real waiting, no real Postgres.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "backup-dev-db.sh"

FAKE_DUMP_BYTES = b"PGDMP-fake-payload-for-tests\n"

# Must match the defaults documented in docs/DEV_DB_BACKUP.md.
KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 6
MAX_SLOTS = KEEP_DAILY + KEEP_WEEKLY + KEEP_MONTHLY  # 17


def _write_fake_docker(bin_dir: Path, *, running: bool) -> Path:
    """Drop a `docker` shim into bin_dir that the script will find via PATH."""
    state = "true" if running else "false"
    script = f"""#!/usr/bin/env bash
# Fake docker shim used by tests/test_backup_dev_db.py.
case "$1" in
  inspect)
    # `docker inspect --format '{{{{.State.Running}}}}' <name>`
    echo "{state}"
    exit 0
    ;;
  exec)
    if [ "{state}" != "true" ]; then
      echo "Error: container not running" >&2
      exit 1
    fi
    # Stream a deterministic byte payload that stands in for pg_dump output.
    # When the test injects a clock, tag the payload with that date so a
    # restored dump can be traced back to the day it was captured.
    if [ -n "${{BYLAW_BACKUP_DATE:-}}" ]; then
      printf 'PGDMP-%s\\n' "$BYLAW_BACKUP_DATE"
    else
      printf '%s' "PGDMP-fake-payload-for-tests"
      printf '\\n'
    fi
    exit 0
    ;;
  *)
    echo "fake docker: unsupported verb $1" >&2
    exit 2
    ;;
esac
"""
    path = bin_dir / "docker"
    path.write_text(script)
    path.chmod(0o755)
    return path


def _run_backup(
    backup_dir: Path,
    bin_dir: Path,
    *,
    on: date | None = None,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BYLAW_BACKUP_DIR"] = str(backup_dir)
    env["BYLAW_PG_CONTAINER"] = "fake-container"
    if on is not None:
        env["BYLAW_BACKUP_DATE"] = on.isoformat()
    return subprocess.run(
        [str(SCRIPT), *(args or [])],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _simulate_days(backup_dir: Path, bin_dir: Path, start: date, days: int) -> None:
    """Run one backup per simulated calendar day, starting at `start`."""
    for offset in range(days):
        result = _run_backup(backup_dir, bin_dir, on=start + timedelta(days=offset))
        assert result.returncode == 0, result.stderr


def _dumps(backup_dir: Path) -> list[str]:
    return sorted(p.name for p in backup_dir.glob("layer1-*.dump"))


@pytest.fixture
def fake_env(tmp_path: Path) -> tuple[Path, Path]:
    backup_dir = tmp_path / "backups"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return backup_dir, bin_dir


def test_writes_dump_keyed_by_day_of_week(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    result = _run_backup(backup_dir, bin_dir)

    assert result.returncode == 0, result.stderr
    expected_name = f"layer1-{datetime.now().strftime('%a')}.dump"
    dump = backup_dir / expected_name
    assert dump.exists(), f"expected {dump} to exist; got {list(backup_dir.iterdir())}"
    # The script must stream pg_dump bytes through unchanged.
    assert dump.read_bytes() == FAKE_DUMP_BYTES
    # No .tmp file should be left behind on success.
    assert not (backup_dir / f"{expected_name}.tmp").exists()
    # Log line was appended.
    log = (backup_dir / "backup.log").read_text()
    assert "OK: wrote" in log


def test_rerun_overwrites_in_place(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    first = _run_backup(backup_dir, bin_dir)
    assert first.returncode == 0
    second = _run_backup(backup_dir, bin_dir)
    assert second.returncode == 0

    # Only one daily dump file — rotation slot, not append. (The weekly and
    # monthly promotions of that same dump live under their own prefixes.)
    dailies = [
        name
        for name in _dumps(backup_dir)
        if not name.startswith(("layer1-weekly-", "layer1-monthly-"))
    ]
    assert len(dailies) == 1, dailies


def test_container_not_running_fails_loudly(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=False)

    result = _run_backup(backup_dir, bin_dir)

    assert result.returncode != 0
    assert not list(backup_dir.glob("layer1-*.dump"))
    log = (backup_dir / "backup.log").read_text()
    assert "is not running" in log


# --- Retention tiers (ABS-498) ------------------------------------------


def test_snapshot_older_than_seven_days_survives_a_month(
    fake_env: tuple[Path, Path],
) -> None:
    """The defect this ticket exists for: on day 30, day 1 must be restorable.

    Under the old day-of-week-only rotation, day 8 overwrote day 1 in place,
    so nothing older than a week ever survived.
    """
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    start = date(2026, 1, 1)
    _simulate_days(backup_dir, bin_dir, start, 30)

    # Day 1 landed in the monthly tier (first run of January) and is intact.
    january = backup_dir / "layer1-monthly-2026-01.dump"
    assert january.exists(), _dumps(backup_dir)
    assert january.read_bytes() == b"PGDMP-2026-01-01\n"

    # And it is genuinely older than the 7-day daily horizon: every daily slot
    # holds one of the last seven simulated days.
    daily_contents = {
        (backup_dir / f"layer1-{(start + timedelta(days=d)).strftime('%a')}.dump")
        .read_text()
        .strip()
        for d in range(23, 30)
    }
    assert "PGDMP-2026-01-01" not in daily_contents

    # The weekly tier carries intermediate snapshots the daily tier has lost.
    weeklies = sorted(p.name for p in backup_dir.glob("layer1-weekly-*.dump"))
    # 2026-01-01 is a Thursday, so 30 days spans ISO weeks W01-W05 and the
    # 4-slot weekly tier has already pruned W01.
    assert weeklies == [
        "layer1-weekly-2026-W02.dump",
        "layer1-weekly-2026-W03.dump",
        "layer1-weekly-2026-W04.dump",
        "layer1-weekly-2026-W05.dump",
    ]


def test_disk_stays_under_documented_ceiling(fake_env: tuple[Path, Path]) -> None:
    """Bounded disk must survive the change — on purpose, via the prune."""
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    start = date(2026, 1, 1)
    # 30 simulated days first (the DoD's horizon), then on past the point
    # where every tier is saturated and the prune is doing real work.
    _simulate_days(backup_dir, bin_dir, start, 30)
    assert len(_dumps(backup_dir)) <= MAX_SLOTS, _dumps(backup_dir)

    _simulate_days(backup_dir, bin_dir, start + timedelta(days=30), 230)

    dumps = _dumps(backup_dir)
    assert len(dumps) == MAX_SLOTS, dumps
    assert len([n for n in dumps if n.startswith("layer1-weekly-")]) == KEEP_WEEKLY
    assert len([n for n in dumps if n.startswith("layer1-monthly-")]) == KEEP_MONTHLY

    # Total bytes stay under the ceiling the docs promise: MAX_SLOTS times the
    # size of the largest single dump.
    sizes = [(backup_dir / n).stat().st_size for n in dumps]
    assert sum(sizes) <= MAX_SLOTS * max(sizes)

    # Pruning is explicit and audited, not a side effect.
    assert "PRUNED:" in (backup_dir / "backup.log").read_text()


def test_prune_keeps_the_newest_slots(fake_env: tuple[Path, Path]) -> None:
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    _simulate_days(backup_dir, bin_dir, date(2026, 1, 1), 260)

    monthlies = sorted(p.name for p in backup_dir.glob("layer1-monthly-*.dump"))
    # 260 days from 2026-01-01 ends 2026-09-18; the six newest months survive.
    assert monthlies == [
        "layer1-monthly-2026-04.dump",
        "layer1-monthly-2026-05.dump",
        "layer1-monthly-2026-06.dump",
        "layer1-monthly-2026-07.dump",
        "layer1-monthly-2026-08.dump",
        "layer1-monthly-2026-09.dump",
    ]


def test_prune_ignores_hand_copied_dumps(fake_env: tuple[Path, Path]) -> None:
    """A manually preserved snapshot must never be swept up by the prune.

    Guards `layer1-pre-data-model-3.0-20260812.dump`, the only pre-DM3.0
    copy of the dev corpus.
    """
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    keepsake = backup_dir / "layer1-pre-data-model-3.0-20260812.dump"
    keepsake.write_bytes(b"irreplaceable\n")

    _simulate_days(backup_dir, bin_dir, date(2026, 1, 1), 260)

    assert keepsake.exists()
    assert keepsake.read_bytes() == b"irreplaceable\n"


def test_missed_days_still_promote(fake_env: tuple[Path, Path]) -> None:
    """Promotion keys off "first run of the week/month", not a fixed weekday.

    cron doesn't fire while the Mac is asleep, so a Sunday-only rule would
    silently skip whole weeks.
    """
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)

    # A Wednesday and a Thursday nine days later — no Sunday, no 1st.
    for day in (date(2026, 3, 4), date(2026, 3, 12)):
        assert _run_backup(backup_dir, bin_dir, on=day).returncode == 0

    weeklies = sorted(p.name for p in backup_dir.glob("layer1-weekly-*.dump"))
    assert weeklies == [
        "layer1-weekly-2026-W10.dump",
        "layer1-weekly-2026-W11.dump",
    ]
    assert (backup_dir / "layer1-monthly-2026-03.dump").exists()


def test_dry_run_changes_nothing(fake_env: tuple[Path, Path]) -> None:
    """DoD: prove the first run of the new policy destroys no existing dump."""
    backup_dir, bin_dir = fake_env
    _write_fake_docker(bin_dir, running=True)
    _simulate_days(backup_dir, bin_dir, date(2026, 1, 1), 260)

    before = {p.name: p.read_bytes() for p in backup_dir.glob("layer1-*.dump")}
    log_before = (backup_dir / "backup.log").read_text()

    result = _run_backup(
        backup_dir, bin_dir, on=date(2027, 6, 1), args=["--dry-run"]
    )

    assert result.returncode == 0, result.stderr
    after = {p.name: p.read_bytes() for p in backup_dir.glob("layer1-*.dump")}
    assert after == before
    assert (backup_dir / "backup.log").read_text() == log_before
    # It still reports what a real run would do.
    assert "would promote" in result.stderr
    assert "would prune" in result.stderr
