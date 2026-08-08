"""Production compose invariants for submission storage (ABS-87).

The advisor's upload endpoints write to disk, and the prod container is
``read_only: true`` — so the feature works only because a named volume is
mounted at ``SUBMISSION_STORAGE_DIR``. That's a *deployment* fact: the
Playwright suite runs the FastAPI app from the source venv against a
writable working tree and can never observe it, and the runtime tests can
only prove the app reports/handles the failure, not that the deployment
avoids it.

So the honest coverage is to assert the compose invariants directly —
the pattern already used for ``Dockerfile.advisor`` in
``tests/test_dockerfile_advisor_setuptools_pin.py``. These fail loudly if a
future edit drops the mount, repoints the env var back at the read-only
image layer, or (the second half of the ABS-87 acceptance criteria) relaxes
the hardening that mounting the volume was required not to disturb.

The file under test is the repo's mirror of ``/srv/bylaw/docker-compose.yml``,
which is not itself in git; keeping the mirror honest is what makes it a
usable reference during a deploy.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "docker-compose.production.yml"

MOUNT_POINT = "/var/lib/bylaw/submissions"
VOLUME_KEY = "bylaw-submissions"
VOLUME_NAME = "bylaw_submissions"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def advisor(compose: dict) -> dict:
    return compose["services"]["advisor"]


def test_submissions_volume_is_declared(compose: dict) -> None:
    volumes = compose["volumes"]
    assert VOLUME_KEY in volumes, (
        f"{VOLUME_KEY} volume missing — submission uploads have nowhere to "
        "land on a read-only container (ABS-87)"
    )
    # Pinned name so the volume survives a compose project rename, matching
    # how bylaw-postgres-data is handled.
    assert volumes[VOLUME_KEY]["name"] == VOLUME_NAME


def test_advisor_mounts_the_submissions_volume(advisor: dict) -> None:
    mounts = advisor.get("volumes") or []
    assert f"{VOLUME_KEY}:{MOUNT_POINT}" in mounts, (
        f"advisor must mount {VOLUME_KEY} at {MOUNT_POINT}; got {mounts}"
    )


def test_storage_dir_points_at_the_mount(advisor: dict) -> None:
    value = advisor["environment"]["SUBMISSION_STORAGE_DIR"]
    # `${SUBMISSION_STORAGE_DIR:-/var/lib/bylaw/submissions}` — the default
    # must be the mount, so a deployment that never sets the var still works.
    assert value.endswith(f":-{MOUNT_POINT}}}"), (
        "SUBMISSION_STORAGE_DIR must default to the mounted volume, not the "
        f"read-only image layer; got {value!r}"
    )


def test_hardening_is_unchanged(advisor: dict) -> None:
    """ABS-87 acceptance: the mount must not loosen anything else."""
    assert advisor["read_only"] is True
    assert advisor["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in advisor["security_opt"]
    assert advisor["user"] == "1000:1000"
    # /tmp stays a size-capped tmpfs — the volume is for artefacts, not a
    # licence to make the rest of the filesystem writable.
    tmpfs = advisor["tmpfs"]
    assert any(entry.startswith("/tmp:") for entry in tmpfs), tmpfs
    assert any("size=" in entry for entry in tmpfs), tmpfs


def test_only_the_advisor_gains_a_writable_artefact_volume(compose: dict) -> None:
    """No other service should pick up the submissions volume by accident."""
    for name, service in compose["services"].items():
        if name == "advisor":
            continue
        mounts = service.get("volumes") or []
        assert not any(VOLUME_KEY in m for m in mounts), (
            f"service {name} unexpectedly mounts {VOLUME_KEY}"
        )


def test_healthcheck_targets_a_route_the_app_actually_serves(advisor: dict) -> None:
    """`/health` 404s (the app mounts `/healthz`), which would mark the
    container unhealthy and block `depends_on: service_healthy`."""
    test_cmd = advisor["healthcheck"]["test"]
    assert any("/healthz" in part for part in test_cmd), test_cmd
