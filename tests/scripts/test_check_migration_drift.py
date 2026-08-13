"""Coverage for scripts/check_migration_drift.py (ABS-499).

The DM3.0 post-mortem found the dev DB stamped ``0025_signup_grant_unique``
while ``0026_drop_parcel_zone_code`` was still pending — data migrations
applied, schema migration not. Nothing reported it. These tests drive the
comparison against this branch's real ``alembic/versions`` directory, so the
split state the ticket describes is exactly what the first test asserts.
"""

from __future__ import annotations

import pytest

from scripts.check_migration_drift import (
    compute_drift,
    load_script_directory,
    main,
)


@pytest.fixture(scope="module")
def script_directory():
    return load_script_directory()


def test_reports_the_dm3_split_state(script_directory) -> None:
    """0025 applied, 0026 pending — the state ABS-499 was written against."""
    report = compute_drift(("0025_signup_grant_unique",), script_directory)

    assert report.is_behind
    assert [item.revision for item in report.pending] == ["0026_drop_parcel_zone_code"]
    rendered = report.render()
    assert "alembic_version : 0025_signup_grant_unique" in rendered
    assert "BEHIND — 1 migration(s) pending" in rendered
    assert "0026_drop_parcel_zone_code" in rendered
    assert "alembic/versions/0026_drop_parcel_zone_code.py" in rendered


def test_head_revision_is_in_sync(script_directory) -> None:
    (head,) = script_directory.get_heads()
    report = compute_drift((head,), script_directory)

    assert not report.is_behind
    assert report.pending == ()
    assert "in sync" in report.render()


def test_never_stamped_database_is_behind_by_everything(script_directory) -> None:
    report = compute_drift((), script_directory)

    assert report.is_behind
    revisions = [item.revision for item in report.pending]
    # Ordered base -> head, so the reader can apply top to bottom.
    assert revisions[0] == "0001_initial_layer1_schema"
    assert revisions[-1] == script_directory.get_heads()[0]
    assert "<none — never stamped>" in report.render()


def test_pending_entries_carry_the_migration_docstring(script_directory) -> None:
    report = compute_drift(("0025_signup_grant_unique",), script_directory)

    (pending,) = report.pending
    assert "parcel.zone_code" in pending.summary


def test_unknown_revision_is_reported_not_swallowed(script_directory) -> None:
    """A DB ahead of this branch is a different failure and must say so."""
    report = compute_drift(("9999_from_the_future",), script_directory)

    assert not report.is_behind
    assert report.error is not None
    assert "9999_from_the_future" in report.error
    assert "ahead of" in report.render()


def test_unreachable_database_exits_2(capsys) -> None:
    rc = main(["--database-url", "postgresql+psycopg://nobody@127.0.0.1:1/nothing"])

    assert rc == 2
    assert "could not read alembic_version" in capsys.readouterr().out


def test_exit_zero_downgrades_the_exit_code(capsys) -> None:
    rc = main(
        [
            "--database-url",
            "postgresql+psycopg://nobody@127.0.0.1:1/nothing",
            "--exit-zero",
        ]
    )

    assert rc == 0
    assert "could not read alembic_version" in capsys.readouterr().out
